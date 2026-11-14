"""Trade execution adapter for v043 market-trend mode.

Market-trend is an entry mode of existing AT/general and MA frame wallets,
not a new funded strategy. TREND_OPEN rows live in the original account DB,
are ignored by legacy original-strategy exits, and use v043 exit management.
Both paper and live executions require a fresh observed quote.
"""
from __future__ import annotations
import math
import sqlite3
import threading
from market_trend_v043 import _AutoClosingSqlite
import time
from decimal import Decimal,ROUND_DOWN
from pathlib import Path

from market_trend_v043 import TrendPosition, TrendJournal, hold_deadline, KST

STOCK_FEE=.00015
STOCK_SELL_TAX=.002
COIN_FEE=.0005
MIN_COIN_KRW=5000
QTY_TICK=Decimal("0.00000001")


def _coin_qty(value):
    return Decimal(str(value)).quantize(QTY_TICK,rounding=ROUND_DOWN)


def _money(value):
    return int(round(float(value)))


class UnsafeFill(RuntimeError):
    pass


class TrendWallet:
    """Single coordinator serializes order intents; original ledgers are canonical."""
    def __init__(self,rt,journal:TrendJournal):
        self.rt=rt;self.journal=journal
        self.order_lock=threading.RLock()
        self._reservations=set()

    def _path(self,market,strategy):
        root=Path(self.rt.root)
        if market=="stock" and strategy=="at":
            return root/"stock"/"ARTITRADE_D.sqlite3"
        if market=="stock" and strategy=="ma":
            return root/"stock"/"STOCK_MA_ENGINE_V1"/"ma_paper.sqlite3"
        if market=="coin" and strategy=="general":
            return root/"coin"/"base"/"upbit_paper.sqlite3"
        if market=="coin" and strategy=="ma":
            return root/"coin"/"ma"/"upbit_paper.sqlite3"
        raise ValueError("unrecognized wallet")

    def _connect(self,market,strategy):
        db=self._path(market,strategy)
        if not db.is_file():raise UnsafeFill("ORIGINAL_WALLET_MISSING")
        c=sqlite3.connect(str(db),timeout=12,factory=_AutoClosingSqlite)
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=12000")
        return c

    def _alloc(self,market,strategy,frame,c):
        if market=="stock" and strategy=="at":
            r=c.execute("SELECT cash FROM v19_accounts WHERE tf=?",(frame,)).fetchone()
            from android_operation_bridge import _load
            cfg=_load(self.rt)["stock"]["at"]["frames"][frame]
            cash=int(r["cash"]) if r is not None else 0
            seed=int(cfg["seed"]);maximum=int(cfg["max_positions"])
        elif market=="stock":
            r=c.execute("SELECT cash,seed,max_positions FROM accounts WHERE tf=?",
                        (frame,)).fetchone()
            cash=int(r["cash"]) if r is not None else 0
            seed=int(r["seed"]) if r is not None else 0
            maximum=int(r["max_positions"]) if r is not None else 1
        else:
            r=c.execute("SELECT cash,seed FROM wallets WHERE tf=?",(frame,)).fetchone()
            cash=int(r["cash"]) if r is not None else 0
            ledger=self.rt.coin_ledger if strategy=="general" else self.rt.coin_ma_ledger
            try:
                setup=ledger.settings.snapshot()["frames"][frame]
                seed=int(setup["allocated_krw"]);maximum=int(setup["max_positions"])
            except Exception:
                seed=int(r["seed"]) if r else 0;maximum=4
        if maximum<=0:raise UnsafeFill("BAD_MAX_POSITIONS")
        return cash,seed,maximum

    def _open_count(self,market,strategy,frame,c):
        codecol="code" if market=="stock" else "market"
        r=c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status IN ('OPEN','TREND_OPEN')",
                    (frame,)).fetchone()
        return int(r[0])

    def native_owned(self,market,symbol):
        for strategy in (("at","ma") if market=="stock" else ("general","ma")):
            try:
                with self._connect(market,strategy) as c:
                    codecol="code" if market=="stock" else "market"
                    row=c.execute("SELECT 1 FROM positions WHERE "+codecol+"=? AND status IN ('OPEN','TREND_OPEN') AND qty NOT IN ('0',0) LIMIT 1",
                        (symbol,)).fetchone()
                    if row:return True
                    if market=="stock" and strategy=="at":
                        row=c.execute("SELECT 1 FROM pending WHERE code=? LIMIT 1",(symbol,)).fetchone()
                    elif market=="stock":
                        row=c.execute("SELECT 1 FROM candidates WHERE code=? AND status='PENDING' LIMIT 1",(symbol,)).fetchone()
                    else:
                        row=c.execute("SELECT 1 FROM orders WHERE market=? AND status='PENDING' LIMIT 1",(symbol,)).fetchone()
                    if row:return True
            except (sqlite3.Error,OSError,UnsafeFill):
                return True   # Unknown ownership is never safe to buy.
        return False

    def trend_owned(self,market,symbol):
        if (market,symbol) in self._reservations:return True
        return any(x["market"]==market and x["symbol"]==symbol
                   for x in self.journal.open_positions())

    def _order_fill(self,market,strategy,symbol,side,qty,price,event_key,reason):
        if not self.rt.live_enabled(market):
            return {"qty":qty,"price":price,"order_id":"","mode":"PAPER"}
        broker=self.rt.kiwoom if market=="stock" else self.rt.upbit
        if not broker.ready():raise UnsafeFill("LIVE_CREDENTIALS_MISSING")
        source=("stock_"+strategy) if market=="stock" else ("coin_base" if strategy=="general" else "coin_ma")
        if market=="stock":
            fill=broker.fok(event_key,side,symbol,int(qty),int(price),reason,
                venue="KRX",source=source,strategy=strategy,strategy_event_key=event_key)
        else:
            fill=broker.fok(event_key,side,symbol,str(qty),str(price),reason,
                source=source,strategy=strategy,strategy_event_key=event_key)
        return {**fill,"mode":"LIVE"}

    def _insert_buy(self,c,market,strategy,frame,symbol,name,qty,price,fee,at,
                    bar_key,event_key):
        stamp=at.astimezone(KST).isoformat(timespec="seconds")
        compact=at.astimezone(KST).strftime("%Y%m%d%H%M%S")
        cost=_money(qty*price);spent=cost+fee
        if market=="stock" and strategy=="at":
            c.execute("UPDATE v19_accounts SET cash=cash-?,last_update=? WHERE tf=?",
                      (spent,compact,frame))
            p=c.execute("""INSERT INTO positions(tf,code,market,name,ref_ts,ref_low,
               ref_high,ref_bodytop,entry_ts,entry_price,entry_qty,qty,
               first_sell_qty,peak,hold_days,entry_fee,last_price,status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (frame,symbol,"KOSPI",name,compact,price,price,price,compact,
                int(price),int(qty),int(qty),max(1,int(qty*.30)),
                int(price),90,fee,int(price),"TREND_OPEN"))
            pid=int(p.lastrowid)
            balance=c.execute("SELECT cash FROM v19_accounts WHERE tf=?",(frame,)).fetchone()[0]
            c.execute("""INSERT INTO executions(event_key,tf,code,name,position_id,ts,side,qty,
                 price,gross,fees,tax,cash_after,reason,fill_method,seen_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (event_key,frame,symbol,name,pid,compact,"BUY",int(qty),int(price),
                  cost,fee,0,balance,"MARKET_TREND_RSI20_BUY",
                  "LIVE_BROKER_FOK" if self.rt.live_enabled(market) else "PAPER_FRESH_QUOTE",stamp))
            return pid
        if market=="stock" and strategy=="ma":
            c.execute("UPDATE accounts SET cash=cash-? WHERE tf=?",(spent,frame))
            p=c.execute("""INSERT INTO positions(candidate_id,tf,code,name,
              entry_at,entry_date,entry,qty,qty0,first_qty,
              target,low,ref_close,bodytop,recovery1,peak,midpoint,
              last_price,hold_sessions,status)
              VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (frame,symbol,name,stamp,at.date().isoformat(),int(price),int(qty),
               int(qty),max(1,int(qty*.30)),int(price),int(price),int(price),
               int(price),int(price),int(price),int(price),int(price),90,"TREND_OPEN"))
            pid=int(p.lastrowid)
            c.execute("""INSERT INTO executions(tf,position_id,code,name,ts,side,qty,
               price,fees,tax,net_profit,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
               (frame,pid,symbol,name,stamp,"BUY",int(qty),int(price),fee,0,None,
                "MARKET_TREND_RSI20_BUY"))
            return pid
        c.execute("UPDATE wallets SET cash=cash-? WHERE tf=?",(spent,frame))
        hold=hold_deadline(at,frame,"coin").isoformat()
        p=c.execute("""INSERT INTO positions(tf,market,name,ref_at,entry_at,
          entry,qty0,qty,entry_fee,target,peak,mark,first_qty,hold_until,status)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (frame,symbol,name,stamp,stamp,str(price),str(qty),str(qty),fee,
           str(price),str(price),str(price),str(_coin_qty(Decimal(str(qty))*.30)),
           hold,"TREND_OPEN"))
        pid=int(p.lastrowid)
        balance=c.execute("SELECT cash FROM wallets WHERE tf=?",(frame,)).fetchone()[0]
        c.execute("""INSERT INTO executions(event_key,position_id,tf,market,at,
          side,qty,price,fees,tax,net_pnl,reason,cash_after)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (event_key,pid,frame,symbol,stamp,"BUY",str(qty),str(price),fee,0,0,
           "MARKET_TREND_RSI20_BUY",balance))
        return pid

    def buy(self,market,strategy,frame,symbol,name,ask,ask_size,at,bar_key,
            rsi20_live):
        """Atomic parent cash allocation after explicit broker FOK confirmation."""
        event_key="MT43:BUY:"+market+":"+strategy+":"+frame+":"+symbol+":"+str(bar_key)
        with self.order_lock:
            if self.native_owned(market,symbol) or self.trend_owned(market,symbol):
                return None,"ALREADY_OWNED_OR_PENDING"
            # The signal is consumed even if an order fails; do not auto-retry.
            self._reservations.add((market,symbol))
            try:
                with self._connect(market,strategy) as c:
                    cash,seed,maximum=self._alloc(market,strategy,frame,c)
                    if cash<=0 or seed<=0:return None,"NO_WALLET_CASH"
                    if self._open_count(market,strategy,frame,c)>=maximum:
                        return None,"FRAME_MAX_POSITIONS"
                    budget=min(cash,seed//maximum)
                    if market=="stock":
                        price=int(math.ceil(float(ask)))
                        qty=min(int(budget//(price*(1+STOCK_FEE))),
                                max(0,int(float(ask_size))))
                        if qty<1:return None,"INSUFFICIENT_DEPTH_OR_CASH"
                        fee=_money(qty*price*STOCK_FEE)
                        if qty*price+fee>budget:return None,"WALLET_BUDGET"
                    else:
                        price=float(ask)
                        limit=_coin_qty(min(Decimal(str(ask_size))*.5,
                           Decimal(str(budget))/(Decimal(str(price))*(Decimal(1)+Decimal(str(COIN_FEE))))))
                        qty=limit
                        if qty<=0 or float(qty)*price<MIN_COIN_KRW:
                            return None,"INSUFFICIENT_DEPTH_OR_CASH"
                        fee=_money(float(qty)*price*COIN_FEE)
                        if _money(float(qty)*price)+fee>budget:
                            return None,"WALLET_BUDGET"
                # Never hold the original strategy DB transaction across broker IO.
                self.journal.event(event_key+":INTENT",market,strategy,frame,symbol,
                   "INTENT","BROKER_ORDER_PREPARE",price=price,qty=float(qty),
                   rsi20_live=rsi20_live,payload={"bar_key":bar_key})
                try:
                    fill=self._order_fill(market,strategy,symbol,"BUY",qty,price,
                                          event_key,"MARKET_TREND_RSI20_BUY")
                except Exception as exc:
                    self.journal.event(event_key+":FAILED",market,strategy,frame,symbol,
                        "FAILED",type(exc).__name__,price=price,qty=float(qty))
                    if getattr(self.rt,"live_enabled",lambda _:False)(market):
                        try:self.rt.halt_market(market,"MT43_BUY_UNCERTAIN",exc)
                        except Exception:pass
                    return None,type(exc).__name__
                real_qty=fill["qty"];real_price=fill["price"]
                try:
                    with self._connect(market,strategy) as c:
                        c.execute("BEGIN IMMEDIATE")
                        current,_,_=self._alloc(market,strategy,frame,c)
                        gross=_money(float(real_qty)*float(real_price))
                        fee=_money(gross*(STOCK_FEE if market=="stock" else COIN_FEE))
                        if current<gross+fee:raise UnsafeFill("CASH_MOVED_DURING_BROKER_FILL")
                        pid=self._insert_buy(c,market,strategy,frame,symbol,name,
                            real_qty,real_price,fee,at,bar_key,event_key)
                        c.commit()
                except Exception as exc:
                    self.journal.event(event_key+":LEDGER_FAILED",market,strategy,frame,
                        symbol,"UNSAFE",type(exc).__name__,price=real_price,qty=float(real_qty))
                    if self.rt.live_enabled(market):
                        self.rt.halt_market(market,"MT43_LEDGER_AFTER_FILL",exc)
                    raise UnsafeFill("FILL_RECORDED_BROKER_BUT_NOT_LEDGER") from exc
                pos=TrendPosition(market,strategy,frame,symbol,at,real_price,real_qty,bar_key)
                pos.__dict__["position_id"]=pid
                pos.__dict__["broker_order_id"]=fill.get("order_id","")
                pos.__dict__["entry_mode"]="MARKET_TREND"
                pos.__dict__["rsi20_live"]=rsi20_live
                pos.__dict__["bar_finalized"]=False
                self.journal.save_position(pos,pid)
                self.journal.event(event_key+":FILLED",market,strategy,frame,symbol,
                     "ACTUAL" if fill["mode"]=="LIVE" else "PAPER","MT_BUY_FILLED",
                     price=real_price,qty=float(real_qty),rsi20_live=rsi20_live,
                     payload={"position_id":pid,"broker_order_id":fill.get("order_id","")})
                return pos,"BOUGHT"
            finally:
                self._reservations.discard((market,symbol))

    def sell(self,pos,price,at,reason):
        """Use the existing account, prevent duplicate sell against an in-flight fill."""
        with self.order_lock:
            market,strategy,frame,symbol=pos.market,pos.strategy,pos.frame,pos.symbol
            pid=int(getattr(pos,"position_id"))
            key="MT43:SELL:"+market+":"+strategy+":"+str(pid)+":"+reason+":"+str(int(at.timestamp()))
            with self._connect(market,strategy) as c:
                codecol="code" if market=="stock" else "market"
                row=c.execute("SELECT * FROM positions WHERE id=? AND "+codecol+"=? AND status='TREND_OPEN'",
                              (pid,symbol)).fetchone()
                if row is None:return 0,"NOT_OPEN"
                qleft=float(row["qty"])
                if qleft<=0:return 0,"NO_BALANCE"
                action=pos.action(price,at,bar_finished=bool(pos.__dict__.get("bar_finalized")),
                                  bar3_preclose=bool(pos.__dict__.get("bar3_preclose",False)))
                if action is None:return 0,"NO_EXIT_SIGNAL"
                why,fraction=action
                if why!=reason:return 0,"EXIT_REASON_CHANGED"
                if market=="stock":
                    qty=int(qleft) if fraction>=.999 else max(1,int(math.floor(qleft*fraction)))
                else:
                    qty=_coin_qty(qleft if fraction>=.999 else qleft*fraction)
                if float(qty)<=0:return 0,"LOT_TOO_SMALL"
            try:
                fill=self._order_fill(market,strategy,symbol,"SELL",qty,price,key,reason)
            except Exception as exc:
                self.journal.event(key+":FAILED",market,strategy,frame,symbol,
                    "UNSAFE",type(exc).__name__,price=price,qty=float(qty))
                if self.rt.live_enabled(market):
                    try:self.rt.halt_market(market,"MT43_SELL_UNCERTAIN",exc)
                    except Exception:pass
                return 0,type(exc).__name__
            filled=fill["qty"];px=float(fill["price"])
            try:
                with self._connect(market,strategy) as c:
                    c.execute("BEGIN IMMEDIATE")
                    codecol="code" if market=="stock" else "market"
                    row=c.execute("SELECT * FROM positions WHERE id=? AND "+codecol+"=? AND status='TREND_OPEN'",
                         (pid,symbol)).fetchone()
                    if row is None or float(filled)>float(row["qty"])+1e-7:
                        raise UnsafeFill("FILL_EXCEEDS_LEDGER_POSITION")
                    orig=float(row["qty"]);remain=max(0.0,orig-float(filled))
                    gross=_money(float(filled)*px)
                    fee=_money(gross*(STOCK_FEE if market=="stock" else COIN_FEE))
                    tax=_money(gross*STOCK_SELL_TAX) if market=="stock" else 0
                    entry=float(row["entry_price"] if market=="stock" and strategy=="at" else row["entry"])
                    original_qty=float(row["entry_qty"] if market=="stock" and strategy=="at" else
                        row["qty0"])
                    # MA positions have no entry_fee column in the original schema.
                    if market=="stock" and strategy=="ma":
                        buyfee=_money(original_qty*entry*STOCK_FEE)
                    else:
                        buyfee=int(row["entry_fee"])
                    realized=gross-_money(float(filled)*entry)-fee-tax-_money(buyfee*float(filled)/original_qty)
                    stamp=at.isoformat(timespec="seconds");compact=at.strftime("%Y%m%d%H%M%S")
                    if market=="stock" and strategy=="at":
                        c.execute("UPDATE v19_accounts SET cash=cash+?,realized_pnl=realized_pnl+?,last_update=? WHERE tf=?",
                                  (gross-fee-tax,realized,compact,frame))
                        c.execute("""UPDATE positions SET qty=?,status=?,sell_proceeds=sell_proceeds+?,
                              sell_costs=sell_costs+?,last_price=?,closed_ts=? WHERE id=?""",
                                  (int(remain),"CLOSED" if remain<1 else "TREND_OPEN",
                                   gross,fee+tax,int(px),compact if remain<1 else "",pid))
                        balance=int(c.execute("SELECT cash FROM v19_accounts WHERE tf=?",(frame,)).fetchone()[0])
                        c.execute("""INSERT INTO executions(event_key,tf,code,name,position_id,ts,
                          side,qty,price,gross,fees,tax,cash_after,reason,fill_method,seen_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (key,frame,symbol,symbol,pid,compact,"SELL",int(filled),int(px),gross,
                           fee,tax,balance,"MARKET_TREND_"+reason,"LIVE_BROKER_FOK" if
                           fill["mode"]=="LIVE" else "PAPER_FRESH_QUOTE",stamp))
                    elif market=="stock":
                        c.execute("UPDATE accounts SET cash=cash+?,realized=realized+? WHERE tf=?",
                                  (gross-fee-tax,realized,frame))
                        c.execute("UPDATE positions SET qty=?,status=?,last_price=? WHERE id=?",
                                  (int(remain),"CLOSED" if remain<1 else "TREND_OPEN",int(px),pid))
                        c.execute("""INSERT INTO executions(tf,position_id,code,name,ts,side,
                          qty,price,fees,tax,net_profit,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (frame,pid,symbol,symbol,stamp,"SELL",int(filled),int(px),fee,tax,
                           realized,"MARKET_TREND_"+reason))
                    else:
                        c.execute("UPDATE wallets SET cash=cash+?,realized=realized+? WHERE tf=?",
                                  (gross-fee,realized,frame))
                        c.execute("UPDATE positions SET qty=?,status=?,mark=? WHERE id=?",
                                  (str(_coin_qty(remain)),"CLOSED" if remain<1e-8 else "TREND_OPEN",
                                   str(px),pid))
                        balance=int(c.execute("SELECT cash FROM wallets WHERE tf=?",(frame,)).fetchone()[0])
                        c.execute("""INSERT INTO executions(event_key,position_id,tf,market,at,
                          side,qty,price,fees,tax,net_pnl,reason,cash_after)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (key,pid,frame,symbol,stamp,"SELL",str(filled),str(px),fee,0,
                           realized,"MARKET_TREND_"+reason,balance))
                    c.commit()
            except Exception as exc:
                self.journal.event(key+":LEDGER_FAILED",market,strategy,frame,symbol,
                    "UNSAFE",type(exc).__name__,price=px,qty=float(filled))
                if self.rt.live_enabled(market):
                    self.rt.halt_market(market,"MT43_SELL_LEDGER",exc)
                raise UnsafeFill("SELL_FILLED_BROKER_BUT_NOT_LEDGER") from exc
            pos.apply_sell(reason,float(filled),px)
            self.journal.save_position(pos,pid)
            self.journal.event(key+":FILLED",market,strategy,frame,symbol,
               "ACTUAL" if fill["mode"]=="LIVE" else "PAPER",reason,price=px,
               qty=float(filled),payload={"position_id":pid,"net_pnl":realized})
            return float(filled),"SOLD"
