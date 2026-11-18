"""MA v067 coin: current verified Upbit orderbook and live ticker only.

V067 bear candidate decision replaces V061 first-next-bar restriction.
Close-minus-10 to minus-2s positive turnpoint is created only on actual
exchange prints + fresh ask/size; a 30s preview never creates an order. Native MACoinLedger remains sole order/fill owner.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import queue
import threading
import time
from decimal import Decimal
import ma_v067_policy as policy

_INSTALLED=False
KST=dt.timezone(dt.timedelta(hours=9))
_LOCK=threading.RLock()

def _table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ma_v067_positions(
        position_id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        frame TEXT NOT NULL,
        code TEXT NOT NULL,
        state_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")


def _historical_bear(h,tf,market,tick,now):
    from d_upbit import SECONDS
    from coin_ma_engine import _bear_candidate
    from datetime import timezone
    empty={'status':'REJECT','reason':'MA_V067_ALIGNMENT_NOT_SUSTAINED',
           'selected_ref_at':None,'metrics':{}}
    if tf not in policy.FRAMES or len(h)<122:
        _record_v074_ma_observation(market,tf,now.isoformat(),
                                    'BEAR_122_VERIFIED_BARS_MISSING')
        return empty,None
    cur=h[-1]
    if cur['start']+dt.timedelta(seconds=SECONDS[tf])>now.astimezone(timezone.utc):
        _record_v074_ma_observation(market,tf,cur['start'].isoformat(),
                                    'BEAR_BAR_NOT_COMPLETED')
        return dict(empty,reason='VERIFIED_BAR_NOT_COMPLETED'),None
    verdict=policy.bear_reversal_completed(h,tf)
    if verdict.get('kind')!='REVERSAL_BEAR':
        _record_v074_ma_observation(market,tf,cur['start'].isoformat(),
                                    'BEAR_RULE_'+str(verdict.get('reason') or 'NO_MATCH'))
        return dict(empty,reason=verdict.get('reason')),None
    candidate=_bear_candidate(h,tf,market,tick,now)
    if not candidate:
        _record_v074_ma_observation(market,tf,cur['start'].isoformat(),
                                    'BEAR_NATIVE_PRICE_OR_TICK_INVALID')
        return dict(empty,reason='NATIVE_BEAR_PRICE_OR_TICK_INVALID'),None
    features=candidate.setdefault('features',{})
    features.update({
        'ma_v061_kind':'DOWN_BEAR',
        'ma_v067_kind':'REVERSAL_BEAR',
        'ma_v067_high':str(cur['high']),
        'ma_v067_low':str(cur['low']),
        'ma_v067_body_pct':verdict.get('body_pct'),
        'ma_v067_confirmed_bar':str(cur['start']),
        'ma_bear_override':True,'ma_special_reference':False,
        'source':'UPBIT_MA_V067_SUSTAINED_ALIGNMENT',
        'v24_first_fraction':'.30',
        'ma_v067_low_state':{}})
    candidate['features']=features
    # Exit policy uses original verified signal high, not a native trailing
    # preset; the wallet's actual fill price remains the cost basis.
    _record_v074_ma_observation(market,tf,cur['start'].isoformat(),
                                'BEAR_CANDIDATE_RULE_QUALIFIED_NATIVE_EXECUTION_PENDING')
    return {'status':'QUALIFIED','reason':'MA_V067_SUSTAINED_LARGE_BEAR',
            'selected_ref_at':candidate['ref_at'],
            'metrics':{'body_pct':verdict['body_pct'],
                       'aligned_current':True,'aligned_previous':True}},candidate


def _kind_for(c,row):
    info=c.execute('SELECT features FROM candidates WHERE tf=? AND market=? AND ref_at=?',
                   (row['tf'],row['market'],row['ref_at'])).fetchone()
    if info is None:return '',{}
    try:f=json.loads(info['features'] or '{}')
    except (TypeError,ValueError):return '',{}
    return str(f.get('ma_v067_kind') or ''),f


def _on_real_buy(self,c,p):
    kind,features=_kind_for(c,p)
    if kind not in ('REVERSAL_BEAR','REVERSAL_UP'):return
    _table(c)
    found=c.execute('SELECT 1 FROM ma_v067_positions WHERE position_id=?',(p['id'],)).fetchone()
    if found:return
    try:
        state=(policy.initial_bear_exit_state(p['tf'],features['ma_v067_high'])
               if kind=='REVERSAL_BEAR' else policy.initial_up_exit_state(p['tf']))
        state['entry_at']=str(p['entry_at'])
        state['entry_bar_start']=str(features.get('ma_v067_bar_start') or '')
        if kind=='REVERSAL_UP':
            one,two=policy.up_targets(p['entry'],p['tf'])
            state['exit_intents']=[
                {'stage':1,'price':str(one),'fraction_of_original':'.30',
                 'status':'PRESTAGED_PAPER_TARGET'},
                {'stage':2,'price':str(two),'fraction_of_original':'.35',
                 'status':'PRESTAGED_PAPER_TARGET'}]
            state['hard_stop']=str(policy.number(p['entry'])*Decimal('.85'))
            state['policy_source']='NEW LEGACY(11.18).xlsx'
        stamp=str(p['entry_at'])
        c.execute("""INSERT INTO ma_v067_positions(position_id,kind,frame,code,
            state_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
            (p['id'],kind,p['tf'],p['market'],json.dumps(state,ensure_ascii=False),
             stamp,stamp))
        qty=policy.number(p['qty0'])
        target=policy.number(features['ma_v067_high']) if kind=='REVERSAL_BEAR' else policy.up_targets(p['entry'],p['tf'])[0]
        first=Decimal('.30')
        c.execute('UPDATE positions SET first_qty=?,target=? WHERE id=?',
                  (str(qty*first),str(target),p['id']))
    except (ValueError,TypeError,KeyError):pass


def _recent_trade(self,market,now):
    cache=getattr(self,'_ma_v067_live_ticks',{})
    value=cache.get(market)
    if not value:return None
    price,seen=value
    if abs(time.monotonic()-seen)>3:return None
    return policy.number(price)


def _bar_close(now,frame):
    if frame=='day':
        # Upbit daily candles close at 00:00 UTC = 09:00 KST.
        finish=now.replace(hour=9,minute=0,second=0,microsecond=0)
        if finish<=now:finish+=dt.timedelta(days=1)
    elif frame=='60m':
        finish=now.replace(minute=0,second=0,microsecond=0)+dt.timedelta(hours=1)
    else:
        step=5 if frame=='5m' else 30
        finish=now.replace(minute=(now.minute//step)*step,
                           second=0,microsecond=0)+dt.timedelta(minutes=step)
    return (finish-now).total_seconds(),finish


def _prior_verified_closes(tf,symbol,now):
    import ma_v061_coin_runtime as legacy
    from d_upbit import SECONDS
    with legacy._HISTORY_LOCK:
        row=legacy._HISTORY.get((tf,symbol))
    if not row:return []
    stamp,history=row
    if stamp.tzinfo is None or (now.astimezone(dt.timezone.utc)-stamp).total_seconds()>SECONDS[tf]+3:
        return []
    return [bar['close'] for bar in history]


def _second_followup(tf,symbol,state,entry,trade,now):
    import ma_v061_coin_runtime as legacy
    with legacy._HISTORY_LOCK:
        data=legacy._HISTORY.get((tf,symbol))
    if not data:return False
    _,history=data
    if len(history)<3:return False
    try:
        entry_at=dt.datetime.fromisoformat(state['entry_at']).astimezone(dt.timezone.utc)
        if tf=='day':
            start=entry_at.replace(hour=0,minute=0,second=0,microsecond=0)
            current=now.astimezone(dt.timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        else:
            sec={'5m':300,'30m':1800,'60m':3600}[tf]
            start=dt.datetime.fromtimestamp(int(entry_at.timestamp()//sec)*sec,dt.timezone.utc)
            current=dt.datetime.fromtimestamp(int(now.timestamp()//sec)*sec,dt.timezone.utc)
        old=sum(1 for h in history if start<=h['start']<current)
        return old==2 and policy.number(trade)<policy.number(entry)
    except (TypeError,ValueError,KeyError):
        return False


def _v074_coin_tick_worker(runner):
    """Catch the ticker-after-book ordering without a second WS or invented quote.

    The source price is an actual Upbit WS ticker, the cached book is actual
    exchange depth <=3s old, and original MA wallet remains order authority.
    """
    tasks=runner._ma_v074_tick_queue
    stop=getattr(runner,'stop_event',None)
    while not (stop is not None and stop.is_set()):
        try:code,queued_at=tasks.get(timeout=.2)
        except queue.Empty:continue
        try:
            if time.monotonic()-queued_at>1.5:
                _record_v074_ma_observation(code,'all','LIVE',
                                            'OPERATIONAL_MA_TICK_WORKER_LATE')
                continue
            now=dt.datetime.now(KST)
            due=[(tf,end) for tf in ('5m','30m','60m','day')
                 for remain,end in [_bar_close(now,tf)] if 2<=remain<=30]
            if not due:continue
            lock=getattr(runner,'_lock',None)
            if lock is not None:
                with lock:
                    book=(getattr(runner,'last_book',{}) or {}).get(code)
            else:book=(getattr(runner,'last_book',{}) or {}).get(code)
            if not isinstance(book,dict):
                for tf,end in due:
                    _record_v074_ma_observation(code,tf,end.isoformat(),
                        ('MA_PREWATCH_BOOK_NOT_READY' if policy.in_ma_prewatch_window(_bar_close(now,tf)[0])
                         else 'UP_NO_BOOK_AFTER_FRESH_TICKER'))
                continue
            stamp=book.get('timestamp')
            if not isinstance(stamp,(int,float)) or abs(now.timestamp()-stamp/1000)>3:
                for tf,end in due:
                    _record_v074_ma_observation(code,tf,end.isoformat(),
                        ('MA_PREWATCH_BOOK_STALE' if policy.in_ma_prewatch_window(_bar_close(now,tf)[0])
                         else 'UP_CACHED_BOOK_STALE_AFTER_TICKER'))
                continue
            ledger=getattr(runner,'ma_ledger',None)
            if ledger is not None:
                # Same native book and original FOK; never assume a fill.
                ledger.process_book(dict(book),now)
        except (KeyError,ValueError,TypeError,AttributeError,OverflowError) as exc:
            _record_v074_ma_observation(code,'all','LIVE',
                                        'OPERATIONAL_MA_TICK_LANE_'+type(exc).__name__)
        finally:
            tasks.task_done()


def _record_v074_ma_observation(symbol,frame,bar_end,reason,details=None):
    """No broker calls or SQLite on the genuine WS decision thread."""
    try:
        import market_trend_runtime_v043 as trend
        core=getattr(trend,"_CORE",None)
        rt=getattr(core,"_RUNTIME",None) if core else None
        co=getattr(rt,"market_trend",None) if rt else None
        if co is not None:
            co.record_ma_decision('coin',symbol,frame,bar_end,reason,details)
    except (AttributeError,ImportError,TypeError,ValueError):
        pass


def _new_up_candidate(self,book,now):
    from d_upbit import floor_tick,SECONDS
    if not isinstance(book,dict):return
    symbol=str(book.get('market') or '')
    if not symbol.startswith('KRW-'):return
    # Read-only 30..10s observation; only a genuine 10..2s trade+book
    # with the original native account/FOK path may register a candidate.
    watch=[(tf,end,remain) for tf in ('5m','30m','60m','day')
           for remain,end in [_bar_close(now,tf)]
           if policy.in_ma_prewatch_window(remain)]
    due=[(tf,end) for tf in ('5m','30m','60m','day')
         for remain,end in [_bar_close(now,tf)]
         if policy.in_up_entry_window(remain)]
    if not watch and not due:return
    if watch:
        if not hasattr(self,'_ma_v079_prewatch_keys'):self._ma_v079_prewatch_keys=set()
        for tf,end,remain in watch:
            mark=(symbol,tf,end.isoformat(),int(remain//5))
            if mark in self._ma_v079_prewatch_keys:continue
            self._ma_v079_prewatch_keys.add(mark)
            trade=_recent_trade(self,symbol,now)
            evidence={'seconds_to_close':round(remain,3),'observation_only':True,
                      'live_source':'UPBIT_VERIFIED_TICKER'}
            if trade is None:
                reason='MA_PREWATCH_NO_VERIFIED_TICK'
            else:
                history=_prior_verified_closes(tf,symbol,now)
                preview=policy.prewatch_upward_alignment(history,trade)
                reason='MA_PREWATCH_'+str(preview.get('reason') or 'UNKNOWN')
                evidence.update({k:preview[k] for k in ('ma_120_delta','ma_60_delta','ma120_down_tolerance') if k in preview})
                evidence['trade_price']=str(trade)
            _record_v074_ma_observation(symbol,tf,end.isoformat(),reason,evidence)
        if len(self._ma_v079_prewatch_keys)>12000:self._ma_v079_prewatch_keys.clear()
    if not due:return
    price=_recent_trade(self,symbol,now)
    if price is None or price<=0:
        for tf,end in due:
            _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                        'UP_TICK_STALE_OR_MISSING')
        return
    try:
        if not isinstance(book.get('timestamp'),(int,float)) or abs(now.timestamp()-book['timestamp']/1000)>3:
            for tf,end in due:
                _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                            'UP_ORDERBOOK_STALE')
            return
        ask=policy.number(book['ask'])
        bid=policy.number(book['bid'])
        size=policy.number(book['ask_size'])
        instrument_ticks=getattr(self,'sr_instrument_ticks',{}) or {}
        tick=policy.number(instrument_ticks.get(symbol) or book.get('sr_tick_size'))
        if any(x is None or x<=0 for x in (ask,bid,size,tick)) or ask<bid:
            for tf,end in due:
                _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                            'UP_BOOK_DEPTH_OR_TICKSIZE_INVALID')
            return
    except (KeyError,TypeError,ValueError,AttributeError,OverflowError) as exc:
        for tf,end in due:
            _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                        'OPERATIONAL_MA_BOOK_'+type(exc).__name__)
        return
    if not hasattr(self,'_ma_v067_up_keys'):self._ma_v067_up_keys=set()
    for tf,end in due:
        key=(symbol,tf,end.isoformat())
        if key in self._ma_v067_up_keys:continue
        remaining,_=_bar_close(now,tf)
        history=_prior_verified_closes(tf,symbol,now)
        verdict=policy.upward_transition_on_forming(history,price,remaining)
        if verdict.get('kind')!='REVERSAL_UP':
            reason=str(verdict.get('reason') or 'NO_MATCH')
            _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                        'UP_RULE_'+reason,
                                        {k:verdict[k] for k in (
                                            'ma_120_delta','ma_60_delta',
                                            'ma120_down_tolerance') if k in verdict})
            continue
        # Latest verified ticker and book must STILL satisfy the same rule
        # at submission. A 30s preview and an earlier passing tick are never
        # treated as executable evidence or manufactured broker fills.
        submit_now=dt.datetime.now(KST)
        submit_remaining,submit_end=_bar_close(submit_now,tf)
        if submit_end!=end or not policy.in_up_entry_window(submit_remaining):
            _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                        'UP_LAST_RECHECK_OUTSIDE_10_TO_2_SECONDS')
            continue
        final_price=_recent_trade(self,symbol,submit_now)
        if final_price is None:
            _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                        'UP_LAST_RECHECK_GENUINE_TICK_STALE')
            continue
        if abs(submit_now.timestamp()-book['timestamp']/1000)>3:
            _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                        'UP_LAST_RECHECK_BOOK_STALE')
            continue
        checked=policy.upward_transition_on_forming(history,final_price,submit_remaining)
        if checked.get('kind')!='REVERSAL_UP':
            _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                        'UP_LAST_RECHECK_ALIGNMENT_LOST',
                                        {'last_trade_price':str(final_price),
                                         'reason':str(checked.get('reason') or 'NO_MATCH')})
            continue
        now=submit_now
        verdict=checked
        price=final_price
        limit=floor_tick(ask,tick)
        if limit<ask:limit+=tick
        target=limit*(Decimal(1)+policy.UP_FIRST_TARGET_PCT[tf]/100)
        start=end-dt.timedelta(seconds=SECONDS[tf])
        features={
            'ma_v061_kind':'TURNPOINT','ma_v067_kind':'REVERSAL_UP',
            'ma_v067_bar_start':start.isoformat(),
            'ma_v067_submit_at':now.isoformat(),
            'ma_v067_15s_fill_until':(now+dt.timedelta(seconds=15)).isoformat(),
            'ma_v067_120_delta':verdict['ma_120_delta'],
            'ma_v061_open_at':now.isoformat(),
            'ma_v061_deadline_at':(now+dt.timedelta(seconds=15)).isoformat(),
            'ma_v061_first_target_pct':str(policy.UP_FIRST_TARGET_PCT[tf]),
            'ma_bear_override':True,'ma_special_reference':False,
            'v24_first_fraction':'.10',
            'tick_size':str(tick),
            'source':'UPBIT_V079_LIVE_10_TO_2_TICK_AND_BOOK'}
        candidate={'frame':tf,'market':symbol,
            'ref_at':start.astimezone(dt.timezone.utc).isoformat(),
            'ref_end':end.astimezone(dt.timezone.utc).isoformat(),
            'limit':str(limit),'target':str(target),
            'score':80.,'features':features}
        try:
            accepted=self.record_candidate(candidate,symbol,now)
            if accepted:
                self._ma_v067_up_keys.add(key)
                _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                            'UP_CANDIDATE_REGISTERED_NATIVE_FILL_PENDING',
                                            {'trade_price':str(price),
                                             'ask':str(ask),
                                             'ma120_delta':verdict.get('ma_120_delta')})
            else:
                _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                            'UP_CANDIDATE_REJECTED_BY_NATIVE_WALLET')
        except (KeyError,ValueError,TypeError,AttributeError) as exc:
            _record_v074_ma_observation(symbol,tf,end.isoformat(),
                                        'OPERATIONAL_MA_CANDIDATE_'+type(exc).__name__)
    if len(self._ma_v067_up_keys)>5000:
        self._ma_v067_up_keys=set(list(self._ma_v067_up_keys)[-1000:])


def _expire_or_rebound(self,c,book,now):
    symbol=str(book.get('market') or '')
    price=_recent_trade(self,symbol,now)
    if price is None:return
    rows=c.execute("""SELECT o.id AS order_id,o.created_at,o.ref_at,o.tf,
            x.id AS candidate_id,x.features
        FROM orders o JOIN candidates x ON x.tf=o.tf AND x.market=o.market
                                      AND x.ref_at=o.ref_at
        WHERE o.market=? AND o.status='PENDING' AND
              x.features LIKE '%"ma_v067_kind"%'""",(symbol,)).fetchall()
    for row in rows:
        try:features=json.loads(row['features'] or '{}')
        except (TypeError,ValueError):continue
        kind=features.get('ma_v067_kind')
        if kind=='REVERSAL_UP':
            try:
                start=dt.datetime.fromisoformat(row['created_at'])
                if start.tzinfo is None or (now-start).total_seconds()>15:
                    c.execute("UPDATE orders SET status='EXPIRED' WHERE id=? AND status='PENDING'",
                              (row['order_id'],))
                    c.execute("UPDATE candidates SET reason='MA_V067_UP_15S_EXPIRED' WHERE id=?",
                              (row['candidate_id'],))
            except (TypeError,ValueError):pass
        elif kind=='REVERSAL_BEAR':
            state=features.get('ma_v067_low_state') or {}
            updated,allow=policy.bear_low_entry(state,price,features.get('ma_v067_low'))
            if updated!=state:
                features['ma_v067_low_state']=updated
                c.execute("UPDATE candidates SET features=? WHERE id=?",
                          (json.dumps(features,ensure_ascii=False),row['candidate_id']))
            if updated.get('rebounded_to_low'):
                c.execute("UPDATE orders SET status='EXPIRED' WHERE id=?",
                          (row['order_id'],))


def _process_v067_exits(self,book,now):
    from d_upbit import dec,qty_floor
    if not isinstance(book,dict):return
    symbol=str(book.get('market') or '')
    trade=_recent_trade(self,symbol,now)
    # Same authentic Upbit bid/depth as before: no synthetic sale on reconnect.
    try:
        bid=dec(book['bid']);bid_qty=dec(book['bid_size'])
        if bid<=0 or bid_qty<=0:return
        if abs(now.timestamp()-float(book['timestamp'])/1000)>3:return
    except (TypeError,KeyError,ValueError):return
    began=time.monotonic()
    with self.lock,self.connect() as c:
        _table(c)
        rows=c.execute("""SELECT p.*,s.kind,s.state_json FROM positions p
            JOIN ma_v067_positions s ON s.position_id=p.id
            WHERE p.market=? AND p.status='OPEN' ORDER BY p.id""",
            (symbol,)).fetchall()
        if not rows:return
        depth=qty_floor(bid_qty*Decimal('.5'))
        for item in rows:
            for attempt in range(2):
                # A single genuine post-reconnect order book may be above
                # BOTH profit targets; consume at most two confirmed stages.
                # Protect against a slow SQLite operation aging that book.
                if depth<=0 or time.monotonic()-began>2.5:break
                # First stage uses the already-authoritative initial SQL
                # row. Only after the first confirmed SELL do we re-read
                # the now-changed position and sidecar for stage 2.
                p=item if attempt==0 else c.execute("""SELECT p.*,s.kind,s.state_json FROM positions p
                    JOIN ma_v067_positions s ON s.position_id=p.id
                    WHERE p.id=? AND p.market=? AND p.status='OPEN' AND p.qty!='0'""",
                    (item['id'],symbol)).fetchone()
                # An incomplete stage-2 reread must not raise inside the
                # first SELL's original transaction or imply another fill.
                if p is None or any(k not in p.keys() for k in (
                        'id','qty','qty0','entry','tf','kind','state_json')):break
                qty=dec(p['qty'])
                if qty<=0:break
                try:state=json.loads(p['state_json'] or '{}')
                except (TypeError,ValueError):break
                closes=_prior_verified_closes(p['tf'],symbol,now)
                forced=(policy.forced_ma_exit(closes,trade)
                        if trade is not None and p['kind']=='REVERSAL_BEAR'
                        else '')
                if p['kind']=='REVERSAL_BEAR' and trade is not None and not forced and _second_followup(
                        p['tf'],symbol,state,p['entry'],trade,now):
                    forced='MA_V067_SECOND_FOLLOWUP_BELOW_ENTRY'
                if p['kind']=='REVERSAL_UP':
                    updated,want,reason=policy.up_exit(
                        state,bid,p['entry'],min(qty,depth),p['qty0'],now,forced)
                else:
                    updated,want,reason=policy.bear_exit(
                        state,bid,p['entry'],min(qty,depth),p['qty0'],now,forced)
                if attempt==1 and reason not in (
                        'MA_V065_SECOND_HALF_REMAINING',
                        'MA_V1118_UP_SECOND_35PCT'):
                    break
                if updated!=state:
                    c.execute('UPDATE ma_v067_positions SET state_json=?,updated_at=? WHERE position_id=?',
                              (json.dumps(updated,ensure_ascii=False),now.isoformat(),p['id']))
                desired=qty_floor(min(want,qty,depth))
                if desired<=0 or not reason:break
                key=('MA_V067:'+symbol+':'+str(p['id'])+':'+
                     reason+':'+str(int(book['timestamp'])))
                if not self._sell(c,p,desired,bid,now.isoformat(),reason,event_key=key):
                    break
                executed=c.execute("""SELECT qty,price FROM executions
                    WHERE event_key=? AND side='SELL' AND position_id=?""",
                    (key,p['id'])).fetchone()
                if executed is None:break
                actually=policy.number(executed['qty'])
                if actually is None or actually<=0:break
                px=executed['price']
                if p['kind']=='REVERSAL_BEAR':
                    applied=policy.apply_bear_fill(updated,reason,actually,now)
                    applied=policy.mark_stage_completion(applied,p['qty0'],now,px)
                else:
                    applied=policy.apply_up_fill(updated,reason,actually,now,p['qty0'],px)
                c.execute('UPDATE ma_v067_positions SET state_json=?,updated_at=? WHERE position_id=?',
                          (json.dumps(applied,ensure_ascii=False),now.isoformat(),p['id']))
                # Reconcile original first-stage field strictly from the
                # matching native SELL execution (V067 IDs are non-Korean).
                first_reason=reason in ('MA_V1118_UP_FIRST_30PCT',
                    'MA_V065_FIRST_30_AT_REVERSAL_HIGH')
                if first_reason:
                    first_goal=(policy.up_stage_quantities(applied,p['qty0'])[0]
                                if p['kind']=='REVERSAL_UP'
                                else (policy.number(p['qty0']) or Decimal(0))*policy.FIRST_FRACTION)
                    confirmed=policy.number(applied.get('first_sold')) or Decimal(0)
                    existing=(policy.number(p['first_sold_qty']) or Decimal(0)
                              if 'first_sold_qty' in p.keys() else Decimal(0))
                    verified=min(first_goal,confirmed)
                    if verified>existing:
                        c.execute('UPDATE positions SET first_sold_qty=? WHERE id=?',
                                  (str(verified),p['id']))
                depth=max(Decimal(0),depth-actually)
                # Only a FULLY booked first stage may immediately arm second.
                # Otherwise await a new authentic orderbook with enough depth.
                if not first_reason or confirmed<first_goal:break


def install():
    global _INSTALLED
    if _INSTALLED:return
    import coin_ma_engine as ma
    import d_upbit
    import ma_v061_coin_runtime as previous
    import market_trend_runtime_v043 as trend
    ma.ma_reference_decision=_historical_bear
    # V061 _process_exits captures its own original_sell pointer. If a
    # V067 candidate also got a V061 sidecar, legacy staged exits could
    # bypass the V067 scoped _sell. Suppress the V061 sidecar at birth
    # for V067 candidates, while retaining all old V061 positions.
    old_stage=previous._entry_stage
    def distinct_v067_stage(self,c,p,kind):
        actual,facts=_kind_for(c,p)
        if actual in ('REVERSAL_BEAR','REVERSAL_UP'):
            return None
        return old_stage(self,c,p,kind)
    previous._entry_stage=distinct_v067_stage

    # The original MA 5m range filter indexes f['high'] and f['low'].
    # Historical/imported old candidates may lack one of those values.
    # That used to escape as KeyError('high') from process_book, aborting
    # evaluation of OTHER valid pending orders and sell-side work.
    # Reject only the unverifiable/incomplete source metadata. Keep the
    # original threshold logic untouched for every complete native record,
    # and keep the explicit V061/V067 MA rules exactly as before.
    previous_range=ma.MACoinLedger._enough_5m_range
    def verified_or_fail_closed_range(f):
        if not isinstance(f,dict):
            return False
        if not f.get('ma_v061_kind') and ('high' not in f or 'low' not in f):
            return False
        return previous_range(f)
    ma.MACoinLedger._enough_5m_range=staticmethod(verified_or_fail_closed_range)

    old_init=ma.MACoinLedger.__init__
    def init(self,*a,**kw):
        old_init(self,*a,**kw)
        with self.lock,self.connect() as c:
            _table(c)
            joined=c.execute("""SELECT p.* FROM positions p JOIN candidates x
                ON x.tf=p.tf AND x.market=p.market AND x.ref_at=p.ref_at
                LEFT JOIN ma_v067_positions s ON s.position_id=p.id
                WHERE p.status='OPEN' AND s.position_id IS NULL
                AND x.features LIKE '%"ma_v067_kind"%'""").fetchall()
            for row in joined:_on_real_buy(self,c,row)
        self._ma_v067_live_ticks={}
    ma.MACoinLedger.__init__=init

    old_buy=ma.MACoinLedger._buy
    def buy(self,c,order,book,seen,available):
        # Expire old UP entries BEFORE native paper or LIVE book matcher.
        now=seen if isinstance(seen,dt.datetime) else dt.datetime.fromisoformat(str(seen))
        if now.tzinfo is None:return 0
        _expire_or_rebound(self,c,book,now)
        refreshed=c.execute('SELECT status FROM orders WHERE id=?',(order['id'],)).fetchone()
        if refreshed is None or refreshed['status']!='PENDING':return 0
        res=old_buy(self,c,order,book,seen,available)
        if res:
            pos=c.execute("""SELECT * FROM positions
                WHERE tf=? AND market=? AND ref_at=? ORDER BY id DESC LIMIT 1""",
                (order['tf'],order['market'],order['ref_at'])).fetchone()
            if pos:_on_real_buy(self,c,pos)
        return res
    ma.MACoinLedger._buy=buy

    previous_sell=ma.MACoinLedger._sell
    def scoped_sell(self,c,p,desired,price,at,reason,event_key=None):
        _table(c)
        row=c.execute('SELECT 1 FROM ma_v067_positions WHERE position_id=?',(p['id'],)).fetchone()
        # V067 positions use the verified v065 policy's original reason
        # names for target/stop orders. The former V067-only prefix gate
        # silently rejected ALL legitimate +2% first-profit sells.
        # Retain the legacy-policy isolation: only reasons from the
        # exact v065 policy or the v067 second-bar guard may pass.
        manual=(str(event_key or '').startswith('USER:') and
                str(reason).startswith('사용자 가상 강제매도 '))
        if row and not (str(reason).startswith(('MA_V065_','MA_V067_','MA_V1118_')) or manual):
            return False
        return previous_sell(self,c,p,desired,price,at,reason,event_key=event_key)
    ma.MACoinLedger._sell=scoped_sell

    older_book=ma.MACoinLedger.process_book
    def book(self,raw,at=None):
        now=at or d_upbit.kst_now()
        if now.tzinfo is None:return 0
        _new_up_candidate(self,raw,now)
        _process_v067_exits(self,raw,now)
        try:
            return older_book(self,raw,at)
        except KeyError as error:
            # Preserve strict non-fabricated execution and the original
            # caller's MA_BOOK_PROCESS_FAIL handling. Store a rate-limited
            # exact source line so recurring legacy failures are diagnosable.
            symbol=str(raw.get('market') or '') if isinstance(raw,dict) else ''
            last=getattr(self,'_v088_keyerror_trace_seen',{})
            elapsed=time.monotonic()
            sig=(symbol,str(error))
            if elapsed-last.get(sig,0)>180:
                last[sig]=elapsed
                if len(last)>150:last.clear()
                self._v088_keyerror_trace_seen=last
                try:
                    import traceback
                    stack=traceback.extract_tb(error.__traceback__)
                    frame=stack[-1] if stack else None
                    location=(str(frame.name)+':'+str(frame.lineno)
                              if frame is not None else 'unknown')
                    self.write_issue('MA_BOOK_KEYERROR_SOURCE_V088',
                        symbol+' '+str(error)[:80]+' origin='+location,
                        severity='ERROR')
                except Exception:
                    pass
            raise
    ma.MACoinLedger.process_book=book

    # Observe the same genuine Upbit WS ticker on an independent, short,
    # bounded worker. No broker/network/SQLite work on the WS thread.
    prior_tick=trend.Coordinator.observe_coin_tick
    def verified_coin_tick(coord,code,price):
        runner=getattr(coord.rt,'coin_runner',None)
        ledger=getattr(runner,'ma_ledger',None) if runner else None
        if ledger is not None:
            if not hasattr(ledger,'_ma_v067_live_ticks'):
                ledger._ma_v067_live_ticks={}
            ledger._ma_v067_live_ticks[str(code)]=(price,time.monotonic())
            try:
                now=dt.datetime.now(KST)
                if any(2<=_bar_close(now,tf)[0]<=30
                       for tf in ('5m','30m','60m','day')):
                    pending=getattr(runner,'_ma_v074_tick_queue',None)
                    if pending is None:
                        pending=queue.Queue(maxsize=512)
                        runner._ma_v074_tick_queue=pending
                    worker=getattr(runner,'_ma_v074_tick_thread',None)
                    if worker is None or not worker.is_alive():
                        worker=threading.Thread(target=_v074_coin_tick_worker,
                            args=(runner,),daemon=True,
                            name='ARTITRADE_V074_UPBIT_MA_FIRST_FLIP')
                        runner._ma_v074_tick_thread=worker
                        worker.start()
                    # Prewatch samples every 5s; during the 10..2s execution
                    # window allow a genuine-ticker retry every 250ms.
                    with _LOCK:
                        gate=getattr(runner,'_ma_v079_ws_queue_gate',None)
                        if gate is None:
                            gate={};runner._ma_v079_ws_queue_gate=gate
                        remaining=min(_bar_close(now,tf)[0] for tf in ('5m','30m','60m','day')
                                      if 2<=_bar_close(now,tf)[0]<=30)
                        cooldown=.25 if policy.in_up_entry_window(remaining) else 5.0
                        stamped=time.monotonic()
                        permitted=stamped-gate.get(str(code),0)>cooldown
                        if permitted:gate[str(code)]=stamped
                        if len(gate)>200:gate.clear()
                    if permitted:
                        try:pending.put_nowait((str(code),time.monotonic()))
                        except queue.Full:
                            _record_v074_ma_observation(str(code),'all',now.isoformat(),
                                'OPERATIONAL_MA_TICK_QUEUE_FULL')
            except (ValueError,TypeError,AttributeError,OverflowError) as exc:
                _record_v074_ma_observation(str(code),'all','LIVE',
                    'OPERATIONAL_MA_TICK_DISPATCH_'+type(exc).__name__)
        return prior_tick(coord,code,price)
    trend.Coordinator.observe_coin_tick=verified_coin_tick
    _INSTALLED=True
