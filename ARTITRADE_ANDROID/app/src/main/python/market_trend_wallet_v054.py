"""Independent, per-timeframe MT54 ledger: never touches parent AT/MA cash.

PAPER is the default, even when existing market LIVE is enabled. A separate
trend approval AND broker-live access are required for external orders. Every
confirmed fill is journaled with source and broker evidence before reporting.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import sqlite3
import threading
from pathlib import Path
from decimal import Decimal

from market_trend_v043 import FRAMES, TrendPosition, KST, _as_kst, _AutoClosingSqlite
from market_trend_wallet_v043 import (STOCK_FEE, STOCK_SELL_TAX, COIN_FEE,
    MIN_COIN_KRW, _money, _coin_qty, UnsafeFill)

DEFAULT_SEED=2_000_000
DEFAULT_MAX=4


class TrendWalletV054:
    """Four independently financed wallets per market, under one order lock."""
    def __init__(self,rt,legacy_owned=None,order_lock=None):
        self.rt=rt
        self.root=Path(rt.root)
        self.order_lock=order_lock or threading.RLock()
        self._reservations=set()
        self.legacy_owned=legacy_owned
        for market in ('stock','coin'):
            self._initialize(market)

    def _path(self,market):
        if market not in ('stock','coin'):raise ValueError('invalid market')
        return self.root/market/'trend'/'market_trend_wallet_v054.sqlite3'

    def _connect(self,market):
        c=sqlite3.connect(str(self._path(market)),timeout=12,factory=_AutoClosingSqlite)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA busy_timeout=12000')
        return c

    def _initialize(self,market):
        path=self._path(market)
        path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect(market) as c:
            c.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS accounts(
                tf TEXT PRIMARY KEY,seed INTEGER NOT NULL,cash INTEGER NOT NULL,
                max_positions INTEGER NOT NULL,realized INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS positions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,tf TEXT NOT NULL,
                symbol TEXT NOT NULL,name TEXT NOT NULL,entry_at TEXT NOT NULL,
                entry REAL NOT NULL,qty0 REAL NOT NULL,qty REAL NOT NULL,
                entry_fee INTEGER NOT NULL DEFAULT 0,mark REAL NOT NULL,
                status TEXT NOT NULL,execution_mode TEXT NOT NULL,
                broker_order_id TEXT NOT NULL DEFAULT '',bar_key TEXT NOT NULL,
                payload TEXT NOT NULL,updated_at TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS mt54_positions_by_symbol
                ON positions(symbol,status,tf);
              CREATE TABLE IF NOT EXISTS executions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT NOT NULL UNIQUE,
                position_id INTEGER,tf TEXT NOT NULL,symbol TEXT NOT NULL,
                at TEXT NOT NULL,side TEXT NOT NULL,qty REAL NOT NULL,
                price REAL NOT NULL,fees INTEGER NOT NULL,tax INTEGER NOT NULL,
                net_pnl INTEGER NOT NULL DEFAULT 0,cash_after INTEGER NOT NULL,
                execution_mode TEXT NOT NULL,broker_order_id TEXT NOT NULL,
                reason TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS seed_audit(
                id INTEGER PRIMARY KEY AUTOINCREMENT,tf TEXT NOT NULL,
                before_seed INTEGER NOT NULL,after_seed INTEGER NOT NULL,
                before_cash INTEGER NOT NULL,after_cash INTEGER NOT NULL,
                changed_at TEXT NOT NULL);
            ''')
            for tf in FRAMES:
                c.execute('INSERT OR IGNORE INTO accounts(tf,seed,cash,max_positions,realized) VALUES(?,?,?,?,0)',
                          (tf,DEFAULT_SEED,DEFAULT_SEED,DEFAULT_MAX))

    def allocation(self,market,frame):
        if frame not in FRAMES:raise ValueError('bad frame')
        with self._connect(market) as c:
            r=c.execute('SELECT * FROM accounts WHERE tf=?',(frame,)).fetchone()
            held=c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status='TREND_OPEN' AND qty>0",(frame,)).fetchone()[0]
            if r is None:raise UnsafeFill('MISSING_TREND_FRAME')
            return {**dict(r),'positions':int(held)}

    def apply_settings(self,market,frames):
        """Change only target-seed free cash delta; spent principal stays invested."""
        if set(frames)!=set(FRAMES):raise ValueError('all four frames required')
        validated={}
        for tf,cfg in frames.items():
            seed=int(cfg['seed']);maximum=int(cfg['max_positions'])
            if not 10_000<=seed<=2_000_000_000 or not 1<=maximum<=100:
                raise ValueError('invalid trend capital or holdings')
            validated[tf]=(seed,maximum)
        with self.order_lock,self._connect(market) as c:
            c.execute('BEGIN IMMEDIATE')
            for tf,(seed,maximum) in validated.items():
                r=c.execute('SELECT seed,cash FROM accounts WHERE tf=?',(tf,)).fetchone()
                old,cash=int(r['seed']),int(r['cash'])
                target=cash+seed-old
                if target<0:raise UnsafeFill('TREND_SEED_BELOW_INVESTED_PRINCIPAL')
                c.execute('UPDATE accounts SET seed=?,cash=?,max_positions=? WHERE tf=?',
                          (seed,target,maximum,tf))
                if seed!=old:
                    c.execute('INSERT INTO seed_audit(tf,before_seed,after_seed,before_cash,after_cash,changed_at) VALUES(?,?,?,?,?,?)',
                              (tf,old,seed,cash,target,dt.datetime.now(KST).isoformat()))
            c.commit()
        return {tf:self.allocation(market,tf) for tf in FRAMES}

    def owned(self,market,symbol):
        with self._connect(market) as c:
            row=c.execute("SELECT 1 FROM positions WHERE symbol=? AND status='TREND_OPEN' AND qty>0 LIMIT 1",
                          (symbol,)).fetchone()
            return bool(row)

    def _is_live(self,market):
        try:
            approved=bool(getattr(self.rt,'trend_live_enabled',lambda _:False)(market))
            return approved and bool(self.rt.live_enabled(market))
        except Exception:return False

    def _order_fill(self,market,symbol,side,qty,price,event_key,reason):
        if not self._is_live(market):
            return {'qty':qty,'price':price,'order_id':'','mode':'PAPER'}
        if getattr(self.rt,'_halted',{}).get(market):
            raise UnsafeFill('BROKER_MARKET_HALTED')
        broker=self.rt.kiwoom if market=='stock' else self.rt.upbit
        if broker is None or not broker.ready():raise UnsafeFill('BROKER_NOT_READY')
        if market=='stock':
            result=broker.fok(event_key,side,symbol,int(qty),int(price),reason,
                              venue='KRX',source='stock_trend',strategy='trend',strategy_event_key=event_key)
        else:
            result=broker.fok(event_key,side,symbol,str(qty),str(price),reason,
                              source='coin_trend',strategy='trend',strategy_event_key=event_key)
        return {**result,'mode':'LIVE'}

    def _price_qty(self,market,ask,ask_size,budget):
        if not all(math.isfinite(float(v)) and float(v)>0 for v in (ask,ask_size,budget)):
            return None
        if market=='stock':
            price=int(math.ceil(float(ask)))
            qty=min(int(budget//(price*(1+STOCK_FEE))),int(float(ask_size)))
            if qty<=0:return None
            fee=_money(qty*price*STOCK_FEE)
        else:
            price=float(ask)
            qty=_coin_qty(min(Decimal(str(ask_size))*Decimal('0.5'),
                              Decimal(str(budget))/(Decimal(str(price))*(1+Decimal(str(COIN_FEE))))))
            if qty<=0 or float(qty)*price<MIN_COIN_KRW:return None
            fee=_money(float(qty)*price*COIN_FEE)
        if _money(float(qty)*price)+fee>budget:return None
        return price,qty,fee

    def buy(self,market,frame,symbol,name,ask,ask_size,at,bar_key,rsi20_live):
        if frame not in FRAMES:raise ValueError('bad frame')
        at=_as_kst(at)
        key='MT54:BUY:'+':'.join((market,'trend',frame,str(symbol),str(bar_key)))
        with self.order_lock:
            if (market,symbol) in self._reservations or self.owned(market,symbol):
                return None,'ALREADY_OWNED_OR_PENDING'
            if self.legacy_owned is not None and self.legacy_owned(market,symbol):
                return None,'ALREADY_OWNED_OR_PENDING'
            self._reservations.add((market,symbol))
            try:
                with self._connect(market) as c:
                    r=c.execute('SELECT * FROM accounts WHERE tf=?',(frame,)).fetchone()
                    if r is None:return None,'MISSING_TREND_FRAME'
                    held=c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status='TREND_OPEN'",
                                   (frame,)).fetchone()[0]
                    if int(held)>=int(r['max_positions']):return None,'FRAME_MAX_POSITIONS'
                    if int(r['cash'])<=0:return None,'NO_WALLET_CASH'
                    budget=min(int(r['cash']),int(r['seed'])//int(r['max_positions']))
                    amount=self._price_qty(market,ask,ask_size,budget)
                    if amount is None:return None,'INSUFFICIENT_DEPTH_OR_CASH'
                    price,qty,fee=amount
                if market=='stock':
                    runner=getattr(self.rt,'stock_runner',None)
                    identity=(getattr(runner,'market_map',{}) or {}).get(symbol) if runner else None
                    if not identity or identity[0] not in ('KOSPI','KOSDAQ'):
                        return None,'UNVERIFIED_STOCK_MARKET'
                    book=getattr(getattr(runner,'engine',None),'book',None)
                    if book is not None:
                        proof,_=book.available(symbol,'KRX',at,price,int(qty))
                        if proof is None:return None,'INSUFFICIENT_FRESH_BEST_ASK'
                        qty=min(int(qty),int(proof[1]))
                        if qty<1 or not book.reserve(symbol,'KRX',at,price,int(qty)):
                            return None,'ORDERBOOK_CAPACITY_RESERVED'
                mode='LIVE' if self._is_live(market) else 'PAPER'
                try:
                    filled=self._order_fill(market,symbol,'BUY',qty,price,key,'MARKET_TREND_RSI20_BUY')
                except Exception as exc:
                    if mode=='LIVE':
                        try:self.rt.halt_market(market,'MT54_BUY_UNCERTAIN',exc)
                        except Exception:pass
                    return None,'BROKER:'+type(exc).__name__
                real_qty=float(filled['qty']);real_price=float(filled['price'])
                total=_money(real_qty*real_price)
                real_fee=_money(total*(STOCK_FEE if market=='stock' else COIN_FEE))
                try:
                    pos=TrendPosition(market,'trend',frame,symbol,at,real_price,real_qty,bar_key)
                    pos.__dict__.update({'entry_mode':'MARKET_TREND','execution_mode':filled['mode'],
                                         'broker_order_id':str(filled.get('order_id') or ''),
                                         'rsi20_live':rsi20_live,'bar_finalized':False})
                    with self._connect(market) as c:
                        c.execute('BEGIN IMMEDIATE')
                        current=int(c.execute('SELECT cash FROM accounts WHERE tf=?',(frame,)).fetchone()[0])
                        if current<total+real_fee:raise UnsafeFill('CASH_MOVED_AFTER_BROKER_FILL')
                        c.execute('UPDATE accounts SET cash=cash-? WHERE tf=?',(total+real_fee,frame))
                        stamp=at.isoformat()
                        cur=c.execute('''INSERT INTO positions(tf,symbol,name,entry_at,entry,qty0,qty,
                          entry_fee,mark,status,execution_mode,broker_order_id,bar_key,payload,updated_at)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (frame,symbol,name,stamp,real_price,real_qty,real_qty,real_fee,real_price,
                           'TREND_OPEN',filled['mode'],pos.broker_order_id,str(bar_key),
                           json.dumps(pos.to_dict(),ensure_ascii=False),stamp))
                        pos.__dict__['position_id']=int(cur.lastrowid)
                        c.execute('UPDATE positions SET payload=? WHERE id=?',
                                  (json.dumps(pos.to_dict(),ensure_ascii=False),pos.position_id))
                        c.execute('''INSERT INTO executions(event_key,position_id,tf,symbol,at,side,qty,
                          price,fees,tax,net_pnl,cash_after,execution_mode,broker_order_id,reason)
                          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                          (key,pos.position_id,frame,symbol,stamp,'BUY',real_qty,real_price,
                           real_fee,0,0,current-total-real_fee,filled['mode'],pos.broker_order_id,
                           'MARKET_TREND_RSI20_BUY'))
                        c.commit()
                except Exception as exc:
                    if mode=='LIVE':
                        try:self.rt.halt_market(market,'MT54_LEDGER_AFTER_FILL',exc)
                        except Exception:pass
                    raise UnsafeFill('MT54_FILL_LEDGER_MISMATCH') from exc
                return pos,'BOUGHT'
            finally:
                self._reservations.discard((market,symbol))

    def save_position(self,pos):
        if pos.strategy!='trend':raise UnsafeFill('WRONG_POSITION_OWNER')
        with self.order_lock,self._connect(pos.market) as c:
            c.execute('''UPDATE positions SET qty=?,mark=?,status=?,payload=?,updated_at=?
                         WHERE id=? AND tf=? AND symbol=?''',
                      (pos.qty,pos.last_price,'TREND_OPEN' if pos.qty>0 else 'CLOSED',
                       json.dumps(pos.to_dict(),ensure_ascii=False),dt.datetime.now(KST).isoformat(),
                       int(pos.position_id),pos.frame,pos.symbol))

    def open_positions(self,market):
        from market_trend_v043 import TrendPosition
        positions=[]
        with self._connect(market) as c:
            rows=c.execute("SELECT * FROM positions WHERE status='TREND_OPEN' AND qty>0 ORDER BY id").fetchall()
        for row in rows:
            data=json.loads(row['payload'])
            p=TrendPosition(market,'trend',row['tf'],row['symbol'],
                            data['entry_at'],row['entry'],row['qty0'],row['bar_key'])
            p.__dict__.update(data)
            p.entry_at=_as_kst(data['entry_at'])
            p.deadline=_as_kst(data['deadline'])
            p.__dict__.update({'position_id':int(row['id']),
                               'execution_mode':str(row['execution_mode']),
                               'broker_order_id':str(row['broker_order_id'])})
            p.qty=float(row['qty'])
            positions.append(p)
        return positions

    def sell(self,pos,price,at,reason,bid_size=None):
        if pos.strategy!='trend':return 0,'WRONG_POSITION_OWNER'
        market,frame,symbol=pos.market,pos.frame,pos.symbol
        at=_as_kst(at)
        with self.order_lock:
            with self._connect(market) as c:
                r=c.execute('SELECT * FROM positions WHERE id=? AND tf=? AND symbol=? AND status=?',
                           (int(pos.position_id),frame,symbol,'TREND_OPEN')).fetchone()
                if r is None:return 0,'NOT_OPEN'
                if pos.execution_mode!='PAPER' and not self._is_live(market):
                    return 0,'LIVE_POSITION_BROKER_DISABLED'
                if pos.execution_mode=='PAPER' and self._is_live(market):
                    return 0,'PAPER_POSITION_LIVE_MODE'
                action=pos.action(price,at,bar_finished=bool(pos.__dict__.get('bar_finalized')),
                                  bar3_preclose=bool(pos.__dict__.get('bar3_preclose')))
                if action is None or action[0]!=reason:return 0,'NO_EXIT_SIGNAL'
                fraction=action[1];balance=float(r['qty'])
                if market=='stock':
                    qty=int(balance) if fraction>=.999 else max(1,int(round(balance*fraction)))
                    runner=getattr(self.rt,'stock_runner',None)
                    book=getattr(getattr(runner,'engine',None),'book',None)
                    if book is not None:
                        amount=book.available_sell(symbol,'KRX',at,int(price),qty)
                        qty=min(qty,int(amount))
                        if qty<=0 or not book.reserve_sell(symbol,'KRX',at,int(price),qty):
                            return 0,'INSUFFICIENT_FRESH_BID_DEPTH'
                    elif bid_size is not None:
                        qty=min(qty,int(bid_size))
                else:
                    if bid_size is None:return 0,'NO_FRESH_BID_DEPTH'
                    limit=_coin_qty(Decimal(str(bid_size))*Decimal('.5'))
                    qty=min(_coin_qty(balance if fraction>=.999 else balance*fraction),limit)
                if qty<=0:return 0,'INSUFFICIENT_FRESH_BID_DEPTH'
            key='MT54:SELL:'+':'.join((market,str(pos.position_id),reason,str(int(at.timestamp()))))
            mode=str(pos.execution_mode)
            try:
                fill=self._order_fill(market,symbol,'SELL',qty,price,key,reason)
            except Exception as exc:
                if mode=='LIVE':
                    try:self.rt.halt_market(market,'MT54_SELL_UNCERTAIN',exc)
                    except Exception:pass
                return 0,'BROKER:'+type(exc).__name__
            sold=float(fill['qty']);px=float(fill['price'])
            gross=_money(sold*px)
            fee=_money(gross*(STOCK_FEE if market=='stock' else COIN_FEE))
            tax=_money(gross*STOCK_SELL_TAX) if market=='stock' else 0
            buyfee=_money(int(r['entry_fee'])*sold/float(r['qty0']))
            pnl=gross-_money(sold*float(r['entry']))-fee-tax-buyfee
            try:
                with self._connect(market) as c:
                    c.execute('BEGIN IMMEDIATE')
                    row=c.execute('SELECT qty FROM positions WHERE id=? AND status=?',
                                  (int(pos.position_id),'TREND_OPEN')).fetchone()
                    if row is None or sold>float(row['qty'])+1e-7:
                        raise UnsafeFill('UNKNOWN_OR_OVERSOLD_POSITION')
                    rest=max(0.0,float(row['qty'])-sold)
                    c.execute('UPDATE accounts SET cash=cash+?,realized=realized+? WHERE tf=?',
                              (gross-fee-tax,pnl,frame))
                    new_cash=c.execute('SELECT cash FROM accounts WHERE tf=?',(frame,)).fetchone()[0]
                    pos.apply_sell(reason,sold,px)
                    c.execute('''UPDATE positions SET qty=?,mark=?,status=?,payload=?,updated_at=? WHERE id=?''',
                              (rest,px,'CLOSED' if rest<=1e-8 else 'TREND_OPEN',
                               json.dumps(pos.to_dict(),ensure_ascii=False),at.isoformat(),pos.position_id))
                    c.execute('''INSERT INTO executions(event_key,position_id,tf,symbol,at,side,qty,
                       price,fees,tax,net_pnl,cash_after,execution_mode,broker_order_id,reason)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (key,pos.position_id,frame,symbol,at.isoformat(),'SELL',sold,px,fee,tax,
                       pnl,new_cash,mode,str(fill.get('order_id') or ''),reason))
                    c.commit()
            except Exception as exc:
                if mode=='LIVE':
                    try:self.rt.halt_market(market,'MT54_LEDGER_AFTER_SELL',exc)
                    except Exception:pass
                raise UnsafeFill('MT54_SELL_LEDGER_MISMATCH') from exc
            return sold,'SOLD'

    def snapshot(self,market):
        with self._connect(market) as c:
            accounts=[dict(r) for r in c.execute('SELECT * FROM accounts ORDER BY tf')]
            positions=[dict(r) for r in c.execute('SELECT * FROM positions ORDER BY id DESC LIMIT 500')]
            executions=[dict(r) for r in c.execute('SELECT * FROM executions ORDER BY id DESC LIMIT 1500')]
        for p in positions:
            p.update({'code':p['symbol'],'market':p['symbol'] if market=='coin' else 'KRX',
                      'tf':p['tf'],'entry_price':p['entry'],'entry_at':p['entry_at'],
                      'qty0':p['qty0'],'paper_pnl':(p['mark']-p['entry'])*p['qty'],
                      'entry_mode':'MARKET_TREND','strategy':'trend'})
            p.pop('payload',None)
        for x in executions:
            x.update({'code':x['symbol'],'market':x['symbol'] if market=='coin' else 'KRX',
                      'strategy':'trend','entry_mode':'MARKET_TREND'})
        return {'accounts':accounts,'positions':positions,'executions':executions,
                'realized_pnl':sum(x['realized'] for x in accounts),
                'unrealized_pnl':sum(p['paper_pnl'] for p in positions if p['status']=='TREND_OPEN'),
                'pending':[],'strategy':'trend'}
