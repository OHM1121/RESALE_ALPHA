"""v061 coin MA entry/exit overlay using existing Upbit MA wallet and feed.

No new websocket, no separate broker balance, and no changes to ORIGINAL/base
reference strategy. New MA candidates: down-slope bearish candle (limit=low),
or first verified MA120-below turnpoint (next-bar-open, max 5s). Existing MA
positions preserve their legacy exit policy; new bearish positions additionally
receive both critical MA-slope stops. New turnpoint positions use durable 20%,
half-remaining, 4% peak trail, entry guard AFTER 1st target and priority stops.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import sqlite3
import time
import threading
from decimal import Decimal
import ma_v061_rules as rule

_INSTALLED=False
_HISTORY={}
_HISTORY_LOCK=threading.RLock()

def _candidate_decision(legacy,h,tf,market,tick,now):
    import coin_ma_engine as ma
    from d_upbit import floor_tick,dec,SECONDS,coin_diverse_priority,HOLD_DAYS
    from datetime import timedelta,timezone
    facts=rule.candidate_from_closed(h,tf)
    kind=facts.get('kind')
    audit={'status':'REJECT','reason':facts.get('reason'),
           'metrics':facts,'selected_ref_at':None,
           'newest_ref_at':h[-1]['start'].isoformat() if h else None}
    if kind is None:return audit,None
    cur=h[-1]
    at=now.astimezone(timezone.utc)
    if cur['start']+timedelta(seconds=SECONDS[tf])>at:
        audit.update(status='INSUFFICIENT',reason='MA_SIGNAL_BAR_UNCLOSED')
        return audit,None
    if kind=='DOWN_BEAR':
        candidate=ma._bear_candidate(h,tf,market,tick,now)
        if candidate is None:
            audit.update(reason='MA_DOWN_BEAR_NATIVE_PRICE_POLICY_REJECT')
            return audit,None
        # A normal bearish body is sufficient. The original long-body
        # 3x/9x/20x test determines BONUS only, never eligibility.
        avg_range=sum((dec(x['high'])-dec(x['low']) for x in h[-16:-1]),Decimal(0))/15
        avg_volume=sum((dec(x['volume']) for x in h[-16:-1]),Decimal(0))/15
        width=dec(cur['high'])-dec(cur['low'])
        big=(avg_range>0 and width>=3*avg_range and
             avg_volume>0 and dec(cur['volume'])>=ma.VOLUME_MULTIPLIER[tf]*avg_volume)
        if not big:
            candidate['score']=candidate['features']['coin_diverse_base_score']
            candidate['features']['ma_bonus_points']=0
            candidate['features']['coin_diverse_score']=candidate['score']
        candidate['features']['ma_special_reference']=bool(big)
        candidate['features']['ma_bear_override']=True
        candidate['features']['ma_v061_kind']='DOWN_BEAR'
        candidate['features']['ma_v061_signal']=facts
        audit.update(status='QUALIFIED',reason='MA_3PLUS_DOWN_BEAR'+('_BIG' if big else ''),
                     selected_ref_at=candidate['ref_at'])
        return audit,candidate
    close=dec(cur['close'])
    if close<=0 or dec(tick)<=0:return audit,None
    # A marketable LIMIT around the signal close is bounded to +1% to avoid
    # a blind price chase. The exact FILL, not estimated opening quote,
    # determines the profit targets and position cost.
    limit=floor_tick(close*Decimal('1.01'),tick)
    target=floor_tick(close*Decimal('1.10'),tick)
    if limit<=0 or target<=limit:return audit,None
    end=cur['start']+timedelta(seconds=SECONDS[tf])
    features={
        'ma_bear_override':True,'ma_special_reference':False,
        'ma_v061_kind':'TURNPOINT','ma_v061_signal':facts,
        'ma_v061_open_at':end.isoformat(),
        'ma_v061_deadline_at':(end+timedelta(seconds=5)).isoformat(),
        'ma_v061_max_entry_slippage_pct':'1',
        'ma_v061_first_target_pct':str(rule.FIRST_TARGET_PCT[tf]),
        'tick_size':str(tick),
        'source':'UPBIT_COMPLETED_MA_TURNPOINT_V061',
        'coin_diverse_base_score':50.0,'coin_diverse_score':50.0,
        'expected_hold_days_proxy':HOLD_DAYS[tf],
        'v061_pivot_range_max_pct':'2'}
    candidate={'frame':tf,'market':market,'ref_at':cur['start'].isoformat(),
        'ref_end':end.isoformat(),'limit':str(limit),'target':str(target),
        'score':50.,'features':features}
    audit.update(status='QUALIFIED',reason='MA120_SHORT4_FIRST_UP_TURNPOINT',
                 selected_ref_at=candidate['ref_at'])
    return audit,candidate


def _make_table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ma_v061_positions(
        position_id INTEGER PRIMARY KEY,kind TEXT NOT NULL,tf TEXT NOT NULL,
        market TEXT NOT NULL,ref_at TEXT NOT NULL,state_json TEXT NOT NULL,
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")


def _clear_legacy_pending(self):
    # Previous MA reference candidates are no longer valid new-entry signals.
    # Preserve every existing BOUGHT position and all historical executions.
    with self.lock,self.connect() as c:
        _make_table(c)
        old=c.execute("""SELECT o.id,o.tf,o.market,o.ref_at
            FROM orders o LEFT JOIN candidates x
              ON x.tf=o.tf AND x.market=o.market AND x.ref_at=o.ref_at
            WHERE o.status='PENDING'
              AND COALESCE(x.features,'') NOT LIKE '%"ma_v061_kind"%'""").fetchall()
        for row in old:
            c.execute("UPDATE orders SET status='EXPIRED' WHERE id=? AND status='PENDING'",
                      (row['id'],))
            c.execute("""UPDATE candidates SET reason='MA_V061_LEGACY_REFERENCE_DISABLED'
                WHERE tf=? AND market=? AND ref_at=?""",
                (row['tf'],row['market'],row['ref_at']))
        if old:
            c.execute("INSERT INTO issues(at,severity,code,message) VALUES(?,?,?,?)",
                      (dt.datetime.now(dt.timezone.utc).isoformat(),'INFO',
                      'MA_V061_LEGACY_PENDING_DISABLED',str(len(old))+' old MA orders disabled'))


def _entry_stage(self,c,p,kind):
    initial=rule.dec(p['qty0'])
    if initial is None:return
    state={'frame':p['tf'],'stage':0,'first_anchor':'0',
           'first_sold':'0','second_sold':'0','peak2':'0',
           'entry_at':p['entry_at'],'last_observed_at':''}
    stamp=dt.datetime.now(dt.timezone.utc).isoformat()
    _make_table(c)
    c.execute("""INSERT OR IGNORE INTO ma_v061_positions
        (position_id,kind,tf,market,ref_at,state_json,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?)""",
        (p['id'],kind,p['tf'],p['market'],p['ref_at'],
         json.dumps(state,ensure_ascii=False),stamp,stamp))
    if kind=='TURNPOINT':
        target=rule.dec(p['entry'])*(1+rule.FIRST_TARGET_PCT[p['tf']]/100)
        tick=self.sr_instrument_ticks.get(p['market']) or '0.00000001'
        # Stored target is informative only; the pivot's staged selling
        # policy is the independent ma_v061_positions ledger.
        c.execute("""UPDATE positions SET target=?,first_qty=? WHERE id=?""",
                  (str(target),str(rule.FIRST_SELL_FRACTION*initial),p['id']))


def _verified_forced(frame,symbol,bid,now):
    with _HISTORY_LOCK:
        old=_HISTORY.get((frame,symbol))
    if not old:return ''
    stamp,history=old
    closes=[bar['close'] for bar in history]
    # No stale MA inference after a missing whole candle.
    from d_upbit import SECONDS
    if (now.astimezone(dt.timezone.utc)-stamp).total_seconds()>SECONDS[frame]+3:
        return ''
    return rule.forced_exit_reason(closes,bid)


def _process_exits(self,book,now,original_sell):
    from d_upbit import dec,qty_floor
    if not book or not str(book.get('market','')).startswith('KRW-'):return
    try:
        stamp=int(book['timestamp'])/1000
        bid=dec(book['bid']);size=dec(book['bid_size'])
        if abs(now.timestamp()-stamp)>3 or bid<=0 or size<0:return
    except (ValueError,TypeError,KeyError,ArithmeticError):return
    symbol=str(book['market'])
    with self.lock,self.connect() as c:
        _make_table(c)
        joined=c.execute("""SELECT p.*,m.kind,m.state_json
          FROM positions p JOIN ma_v061_positions m ON m.position_id=p.id
          WHERE p.market=? AND p.status='OPEN' ORDER BY p.id""",
          (symbol,)).fetchall()
        if not joined:return
        c.execute('BEGIN IMMEDIATE')
        depth=qty_floor(size*Decimal('.5'))
        for p in joined:
            current=qty_floor(dec(p['qty']))
            if current<=0:continue
            forced=_verified_forced(p['tf'],symbol,bid,now)
            state=json.loads(p['state_json'])
            state['last_observed_at']=now.isoformat()
            if p['kind']=='TURNPOINT':
                updated,wanted,reason=rule.evaluate_pivot_exit(
                    state,bid,p['entry'],min(current,depth),p['qty0'],forced)
            elif forced:
                updated,wanted,reason=state,min(current,depth),forced
            else:
                continue  # legacy bearish take-profit is unchanged
            if updated!=state:
                c.execute("""UPDATE ma_v061_positions SET state_json=?,updated_at=?
                    WHERE position_id=?""",
                    (json.dumps(updated,ensure_ascii=False),now.isoformat(),p['id']))
            desired=qty_floor(min(wanted,current,depth))
            if desired<=0 or not reason:continue
            key='MA_V061:'+symbol+':'+str(p['id'])+':'+reason+':'+str(int(stamp*1000))
            # This is the existing original broker/ledger code path. In LIVE
            # mode SQL execution is intercepted by the original FOK proxy.
            sold=original_sell(self,c,p,desired,bid,now.isoformat(),
                               reason+' 1차 목표' if reason=='MA_V061_FIRST_20PCT' else reason,
                               event_key=key)
            if not sold:continue
            depth-=desired
            applied=rule.quantity_after_fill(updated,reason,desired)
            if reason=='MA_V061_FIRST_20PCT' and (
                    rule.dec(state.get('first_sold')) or Decimal(0))==0:
                # The 2nd +3% target is measured from the CONFIRMED first
                # execution price, not the earlier first-target touch.
                fill=c.execute("""SELECT price FROM executions
                    WHERE position_id=? AND side='SELL'
                    ORDER BY id DESC LIMIT 1""",(p['id'],)).fetchone()
                if fill and rule.dec(fill['price']) is not None:
                    applied['first_anchor']=str(fill['price'])
            c.execute("""UPDATE ma_v061_positions SET state_json=?,updated_at=?
                WHERE position_id=?""",
                (json.dumps(applied,ensure_ascii=False),now.isoformat(),p['id']))
            remaining=current-desired
            if remaining<=0 and reason.startswith('MA_V061_'):
                c.execute("""UPDATE orders SET status='EXPIRED'
                    WHERE market=? AND status='PENDING'""",(symbol,))


def install():
    global _INSTALLED
    if _INSTALLED:return
    import d_upbit,coin_ma_engine as ma
    original_decision=ma.ma_reference_decision
    ma.ma_reference_decision=lambda h,tf,market,tick,now:(
        _candidate_decision(original_decision,h,tf,market,tick,now))
    original_gate=ma.MACoinLedger._original_reference_gate
    original_range=ma.MACoinLedger._enough_5m_range
    ma.MACoinLedger._original_reference_gate=staticmethod(
        lambda f,tf:({'status':'ELIGIBLE','reason':'MA_V061_SPECIFIC_SIGNAL'}
            if isinstance(f,dict) and f.get('ma_v061_kind') else original_gate(f,tf)))
    ma.MACoinLedger._enough_5m_range=staticmethod(
        lambda f:(True if isinstance(f,dict) and f.get('ma_v061_kind')
            else original_range(f)))
    original_init=ma.MACoinLedger.__init__
    def initiated(self,*a,**k):
        original_init(self,*a,**k)
        _clear_legacy_pending(self)
    ma.MACoinLedger.__init__=initiated
    original_record=ma.MACoinLedger.record_candidate
    def record(self,candidate,name,now=None):
        kind=(candidate.get('features') or {}).get('ma_v061_kind')
        if kind not in ('TURNPOINT','DOWN_BEAR'):return False
        if kind=='TURNPOINT':
            stamp=now or d_upbit.kst_now()
            open_at=dt.datetime.fromisoformat(candidate['features']['ma_v061_open_at'])
            deadline=dt.datetime.fromisoformat(candidate['features']['ma_v061_deadline_at'])
            if not open_at<=stamp<=deadline:
                return False  # Never re-open a stale pivot on later scan
        success=original_record(self,candidate,name,now)
        if success and kind=='TURNPOINT':
            with self.lock,self.connect() as c:
                c.execute("""UPDATE orders SET expires_at=?
                    WHERE market=? AND tf=? AND ref_at=? AND status='PENDING'""",
                    (candidate['features']['ma_v061_deadline_at'],
                     candidate['market'],candidate['frame'],candidate['ref_at']))
        return success
    ma.MACoinLedger.record_candidate=record
    original_buy=ma.MACoinLedger._buy
    def buy(self,c,order,book,seen,available):
        # Pending DOWN_BEAR limit buys may be filled several minutes after
        # their source candle. A newer live quote can already match the
        # existing forced SELL pattern. Never turn that into a fresh BUY.
        try:
            frame=str(order['tf']);symbol=str(order['market'])
            stamp=seen if isinstance(seen,dt.datetime) else dt.datetime.fromisoformat(str(seen))
            if stamp.tzinfo is not None and isinstance(book,dict):
                asks=rule.dec(book.get('ask'))
                bids=rule.dec(book.get('bid'))
                if asks is not None and bids is not None and asks>=bids>0:
                    forced=_verified_forced(frame,symbol,asks,stamp) or _verified_forced(frame,symbol,bids,stamp)
                    if forced:
                        c.execute('INSERT INTO issues(at,severity,code,message) VALUES(?,?,?,?)',
                                  (stamp.isoformat(),'WARN','MA_V064_ENTRY_SELL_CONFLICT',
                                   frame+' '+symbol+' '+forced+'; new buy suppressed'))
                        return 0
        except (KeyError,ValueError,TypeError,sqlite3.Error):
            # Never manufacture a bypass order on a failed guard. The original
            # order path remains authoritative and independently fail-closed.
            return 0
        result=original_buy(self,c,order,book,seen,available)
        if result:
            row=c.execute("""SELECT p.* FROM positions p
                WHERE p.market=? AND p.tf=? AND p.ref_at=?
                ORDER BY p.id DESC LIMIT 1""",
                (order['market'],order['tf'],order['ref_at'])).fetchone()
            if row:
                features=c.execute("""SELECT features FROM candidates
                    WHERE market=? AND tf=? AND ref_at=?""",
                    (order['market'],order['tf'],order['ref_at'])).fetchone()
                try:kind=json.loads(features['features']).get('ma_v061_kind') if features else ''
                except (TypeError,ValueError):kind=''
                if kind in ('TURNPOINT','DOWN_BEAR'):
                    _entry_stage(self,c,row,kind)
        return result
    ma.MACoinLedger._buy=buy
    original_sell=ma.MACoinLedger._sell
    def protected_sell(self,c,p,desired,price,at,reason,event_key=None):
        row=c.execute("""SELECT kind FROM ma_v061_positions
            WHERE position_id=?""",(p['id'],)).fetchone()
        if row and row['kind']=='TURNPOINT' and not str(reason).startswith('MA_V061_'):
            return False # Legacy SR1/V24/V26 exits cannot usurp pivot state
        return original_sell(self,c,p,desired,price,at,reason,event_key=event_key)
    ma.MACoinLedger._sell=protected_sell
    original_book=ma.MACoinLedger.process_book
    def scoped_book(self,book,at=None):
        now=at or d_upbit.kst_now()
        _process_exits(self,book,now,original_sell)
        return original_book(self,book,at)
    ma.MACoinLedger.process_book=scoped_book
    original_scan=d_upbit.UpbitPaperRunner._ma_scan_decision
    def scan(self,history,frame,code,tick,now,name):
        if len(history)>=122:
            with _HISTORY_LOCK:
                _HISTORY[(frame,code)]=(
                    history[-1]['start']+dt.timedelta(seconds=d_upbit.SECONDS[frame]),
                    tuple(dict(x) for x in history[-125:]))
        return original_scan(self,history,frame,code,tick,now,name)
    d_upbit.UpbitPaperRunner._ma_scan_decision=scan
    _INSTALLED=True
