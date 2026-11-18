"""Workbook reference-bar PAPER execution policy installed over original wallets.

Only NEW original fills receive NL1118 stage intents. Exact native SQL,
real 0B/0D/Upbit books, genuine BUY/SELL, live guards, quotas, fees, manual
order execution and WAL remain with their original owners. No old PC ledger
is migrated or restarted.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import sqlite3
import time
from pathlib import Path

import new_legacy_1118_policy as nl

_INSTALLED=False
_TBL="""CREATE TABLE IF NOT EXISTS nl1118_reference_positions(
    position_id INTEGER PRIMARY KEY, policy TEXT NOT NULL,
    market TEXT NOT NULL,frame TEXT NOT NULL,symbol TEXT NOT NULL,
    intent_json TEXT NOT NULL,updated_at TEXT NOT NULL)"""

def _is_base_coin(self):
    try:
        p=Path(self.path).resolve()
        return (p.name=="upbit_paper.sqlite3" and p.parent.name=="base"
                and p.parent.parent.name=="coin")
    except (AttributeError,TypeError,OSError):return False

def _market_score(root,market):
    from at_market_score_gate_v107 import score_evidence
    proof=score_evidence(root,market)
    score=proof.get("score")
    return score if proof.get("basis")=="SIGNED_MARKET_SCORE_V033" else None

def _mark(c,position,market):
    try:
        p=dict(position)
        entry=p["entry_price"] if market=="stock" else p["entry"]
        ref_low=p["ref_low"] if market=="stock" else None
        ref_high=p["ref_high"] if market=="stock" else None
        if market=="coin":
            r=c.execute("""SELECT features FROM candidates WHERE tf=?
              AND market=? AND ref_at=? ORDER BY id DESC LIMIT 1""",
              (p["tf"],p["market"],p["ref_at"])).fetchone()
            facts=json.loads(r[0]) if r and r[0] else {}
            ref_low=facts.get("low")
            ref_high=facts.get("high")
        plan=nl.targets(entry,p["tf"],"reference",
                        ref_low=ref_low,ref_high=ref_high)
        # Stock positions store an integer, coin positions a decimal string.
        plan.update({"market":market,"symbol":p["code"] if market=="stock" else p["market"],
            "position_id":int(p["id"]),"qty0":float(p["entry_qty"] if market=="stock" else p["qty0"]),
            "qty":float(p["qty"]),"stage":0,"stage1_filled":0.0,
            "stage2_filled":0.0,"peak_after_second":float(entry),
            "seen_above_entry":False,"score_positive_at_buy":True})
        if market=="coin":
            # Original Upbit wallet's immutable actual hold_until remains
            # authoritative. Its default auto SELL is suppressed only for
            # THIS new policy, so re-apply same deadline here.
            plan["deadline"]=str(p["hold_until"])
        elif "hold_until" in p.keys() and p["hold_until"]:
            plan["deadline"]=str(p["hold_until"])
        plan["sell_intents"][0]["quantity"]=nl.stage_quantity(plan["qty0"],market,1)
        plan["sell_intents"][1]["quantity"]=nl.stage_quantity(plan["qty0"],market,2)
        c.execute(_TBL)
        c.execute("""INSERT OR IGNORE INTO nl1118_reference_positions(
          position_id,policy,market,frame,symbol,intent_json,updated_at)
          VALUES(?,?,?,?,?,?,?)""",
          (int(p["id"]),nl.POLICY,market,p["tf"],plan["symbol"],
           json.dumps(plan,ensure_ascii=False),
           dt.datetime.now(dt.timezone.utc).isoformat()))
        return True
    except (KeyError,TypeError,ValueError,sqlite3.Error,ArithmeticError):
        # Fail closed for this new policy. Do not silently turn an unplanned
        # buy into a holding with obsolete legacy exits.
        return False

def _read(c,pid):
    c.execute(_TBL)
    row=c.execute("""SELECT intent_json FROM nl1118_reference_positions
        WHERE position_id=? AND policy=?""",(pid,nl.POLICY)).fetchone()
    if row is None:return None
    try:return json.loads(row[0])
    except (TypeError,ValueError):return None

def _save(c,plan):
    c.execute("""UPDATE nl1118_reference_positions
        SET intent_json=?,updated_at=? WHERE position_id=?""",
        (json.dumps(plan,ensure_ascii=False,separators=(",",":")),
         dt.datetime.now(dt.timezone.utc).isoformat(),plan["position_id"]))

def _act(self,c,p,minute,at):
    plan=_read(c,p["id"])
    if plan is None:
        from d_v19_portfolio import D19PaperEngine
        owner=(_ORIGINAL_D19_EXIT if isinstance(self,D19PaperEngine)
               else _ORIGINAL_STOCK_EXIT)
        return owner(self,c,p,minute,at)
    if str(at)<=str(p["entry_ts"]):return p
    try:
        at_dt=dt.datetime.strptime(str(at),"%Y%m%d%H%M%S").replace(
            tzinfo=dt.timezone(dt.timedelta(hours=9)))
        verified_price=float(minute["close"])
        if not math.isfinite(verified_price) or verified_price<=0:return p
        plan["qty"]=float(p["qty"])
        if plan["qty"]<=0:return p
        score=_market_score(self.store.root,"stock")
        due=nl.exit_signal(plan,verified_price,at_dt,score)
        _save(c,plan)
        if due is None:return p
        reason,fraction=due
        qty=min(int(p["qty"]),max(1,int(math.ceil(
            float(p["qty"])*fraction-1e-10))))
        # Native single 0B verified SELL path uses ONE immutable event key,
        # original cash accounting and execution ledger. No actual broker
        # order is inferred by a target merely being staged at purchase.
        event_key=("NL1118:"+str(p["id"])+":"+reason+":"+str(at))
        done=self._sell(c,p,at,qty,int(verified_price),reason,
                        event_key=event_key)
        if done is None:return p
        if reason=="NL1118_TP1":
            plan["stage1_filled"]+=qty
            if plan["stage1_filled"]+1e-8>=nl.stage_quantity(
                    plan["qty0"],"stock",1):
                plan["stage"]=max(1,plan["stage"])
                plan["sell_intents"][0]["state"]="FILLED"
        elif reason=="NL1118_TP2":
            plan["stage2_filled"]+=qty
            if plan["stage2_filled"]+1e-8>=nl.stage_quantity(
                    plan["qty0"],"stock",2):
                plan["stage"]=max(2,plan["stage"])
                plan["peak_after_second"]=verified_price
                plan["sell_intents"][1]["state"]="FILLED"
        plan["qty"]=float(done["qty"])
        _save(c,plan)
        return done
    except (ValueError,TypeError,KeyError,ArithmeticError,sqlite3.Error):
        # Reject unsafe fallback; do not manufacture a SELL.
        return p

_ORIGINAL_STOCK_EXIT=None
_ORIGINAL_D19_EXIT=None

def install():
    global _INSTALLED,_ORIGINAL_STOCK_EXIT,_ORIGINAL_D19_EXIT
    if _INSTALLED:return
    import d_engine,d_v19_portfolio,d_upbit

    _ORIGINAL_STOCK_EXIT=d_engine.PaperEngine._minute_exits
    _ORIGINAL_D19_EXIT=d_v19_portfolio.D19PaperEngine._minute_exits
    d_engine.PaperEngine._minute_exits=_act
    # D19 overrides PaperEngine._minute_exits; patch both independently.
    # Otherwise almost all modern stock AT BUY positions silently retain
    # obsolete 3LEG31 and ignore the user's 11.18 profit/stop policy.
    d_v19_portfolio.D19PaperEngine._minute_exits=_act

    native_d19=d_v19_portfolio.D19PaperEngine._buy_limit_depth
    def buy_d19(self,c,row,when,venue,observed_trade_price,
                observed_trade_qty=0):
        if not nl.validate_stock_score(_market_score(self.store.root,"stock")):
            return False,"NL1118_MARKET_SCORE_NOT_POSITIVE"
        result=native_d19(self,c,row,when,venue,observed_trade_price,
                          observed_trade_qty)
        if isinstance(result,tuple) and result[0]:
            p=c.execute("""SELECT * FROM positions WHERE tf=? AND code=?
                AND status='OPEN' ORDER BY id DESC LIMIT 1""",
                (row["tf"],row["code"])).fetchone()
            if p is None or str(p["entry_ts"])!=when.strftime("%Y%m%d%H%M%S"):
                raise RuntimeError("NL1118_D19_BUY_POSITION_MISSING_ROLLBACK")
            if not _mark(c,p,"stock"):
                raise RuntimeError("NL1118_D19_BUY_INTENT_MISSING_ROLLBACK")
            c.execute("UPDATE positions SET exit_policy=? WHERE id=?",
                      (nl.POLICY,p["id"]))
        return result
    d_v19_portfolio.D19PaperEngine._buy_limit_depth=buy_d19

    legacy_buy=d_engine.PaperEngine._buy_if_touch
    def buy_legacy(self,c,tf,minute,at):
        if not nl.validate_stock_score(_market_score(self.store.root,"stock")):
            return None
        p=legacy_buy(self,c,tf,minute,at)
        if p is not None:
            if not _mark(c,p,"stock"):
                raise RuntimeError("NL1118_STOCK_BUY_INTENT_MISSING_ROLLBACK")
            c.execute("UPDATE positions SET exit_policy=? WHERE id=?",
                      (nl.POLICY,p["id"]))
            p=c.execute("SELECT * FROM positions WHERE id=?",(p["id"],)).fetchone()
        return p
    d_engine.PaperEngine._buy_if_touch=buy_legacy

    native_coin=d_upbit.CoinLedger._buy
    def buy_coin(self,c,order,book,seen,available):
        if _is_base_coin(self):
            root=Path(self.path).resolve().parent.parent.parent
            if not nl.validate_stock_score(_market_score(root,"coin")):
                return 0
        bought=native_coin(self,c,order,book,seen,available)
        if _is_base_coin(self) and bought:
            p=c.execute("""SELECT * FROM positions WHERE tf=? AND market=?
               AND ref_at=? AND status='OPEN' ORDER BY id DESC LIMIT 1""",
               (order["tf"],order["market"],order["ref_at"])).fetchone()
            if p is None or not _mark(c,p,"coin"):
                raise RuntimeError("NL1118_COIN_BUY_INTENT_MISSING_ROLLBACK")
        return bought
    d_upbit.CoinLedger._buy=buy_coin

    original_coin_sell=d_upbit.CoinLedger._sell
    def sell_coin(self,c,p,desired,price,at,reason,event_key=None):
        if _is_base_coin(self) and _read(c,p["id"]) is not None:
            manual=str(reason).startswith(("USER:","사용자")) or str(
                event_key or "").startswith("USER:")
            if not str(reason).startswith("NL1118_") and not manual:
                return False
        return original_coin_sell(self,c,p,desired,price,at,reason,
                                  event_key=event_key)
    d_upbit.CoinLedger._sell=sell_coin

    original_book=d_upbit.CoinLedger.process_book
    def book_coin(self,book,at=None):
        result=original_book(self,book,at)
        if not _is_base_coin(self) or not isinstance(book,dict):return result
        now=at or dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        if not isinstance(now,dt.datetime) or now.tzinfo is None:return result
        try:
            if abs(now.timestamp()-float(book["timestamp"])/1000)>3:
                return result
            bid=float(book["bid"]);size=float(book["bid_size"])
            if bid<=0 or size<=0 or not math.isfinite(bid):return result
            symbol=str(book["market"])
            with self.lock,self.connect() as c:
                c.execute(_TBL)
                positions=c.execute("""SELECT * FROM positions WHERE
                     market=? AND status='OPEN' AND qty!='0' ORDER BY id""",
                     (symbol,)).fetchall()
                for pos in positions:
                    plan=_read(c,pos["id"])
                    if plan is None:continue
                    plan["qty"]=float(pos["qty"])
                    score=_market_score(Path(self.path).resolve().parent.parent.parent,"coin")
                    exit_due=nl.exit_signal(plan,bid,now,score)
                    _save(c,plan)
                    if not exit_due:continue
                    reason,fraction=exit_due
                    from d_upbit import dec,qty_floor
                    wanted=qty_floor(min(dec(pos["qty"])*dec(fraction),
                        dec(size)*dec('.5')))
                    if wanted<=0:continue
                    key=("NL1118:"+str(pos["id"])+":"+reason+":"+
                         str(int(float(book["timestamp"]))))
                    if not self._sell(c,pos,wanted,dec(bid),now.isoformat(),
                                      reason,event_key=key):continue
                    if reason=="NL1118_TP1":
                        plan["stage1_filled"]+=float(wanted)
                        if plan["stage1_filled"]+1e-7>=nl.stage_quantity(
                                plan["qty0"],"coin",1):
                            plan["stage"]=max(1,plan["stage"])
                            plan["sell_intents"][0]["state"]="FILLED"
                    elif reason=="NL1118_TP2":
                        plan["stage2_filled"]+=float(wanted)
                        if plan["stage2_filled"]+1e-7>=nl.stage_quantity(
                                plan["qty0"],"coin",2):
                            plan["stage"]=max(2,plan["stage"])
                            plan["sell_intents"][1]["state"]="FILLED"
                            plan["peak_after_second"]=bid
                    updated=c.execute("SELECT qty FROM positions WHERE id=?",
                                      (pos["id"],)).fetchone()
                    if updated:plan["qty"]=float(updated[0])
                    _save(c,plan)
        except (sqlite3.Error,KeyError,TypeError,ValueError,ArithmeticError,
                AttributeError):
            return result
        return result
    d_upbit.CoinLedger.process_book=book_coin
    _INSTALLED=True
