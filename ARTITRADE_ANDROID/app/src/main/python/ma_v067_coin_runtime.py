"""MA v067 coin: current verified Upbit orderbook and live ticker only.

V067 bear candidate decision replaces V061 first-next-bar restriction.
Close-minus-five positive turnpoint is created only on actual exchange
prints + fresh ask/size. Native MACoinLedger remains sole order/fill owner.
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
        stamp=str(p['entry_at'])
        c.execute("""INSERT INTO ma_v067_positions(position_id,kind,frame,code,
            state_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)""",
            (p['id'],kind,p['tf'],p['market'],json.dumps(state,ensure_ascii=False),
             stamp,stamp))
        qty=policy.number(p['qty0'])
        target=policy.number(features['ma_v067_high']) if kind=='REVERSAL_BEAR' else policy.up_targets(p['entry'],p['tf'])[0]
        first=Decimal('.30') if kind=='REVERSAL_BEAR' else Decimal('.10')
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
        finish=now.replace(hour=0,minute=0,second=0,microsecond=0)+dt.timedelta(days=1)
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


def _record_v074_ma_observation(symbol,frame,bar_end,reason):
    """No broker calls or SQLite on the 5-second decision thread."""
    try:
        import market_trend_runtime_v043 as trend
        core=getattr(trend,"_CORE",None)
        rt=getattr(core,"_RUNTIME",None) if core else None
        co=getattr(rt,"market_trend",None) if rt else None
        if co is not None:
            co.record_ma_decision('coin',symbol,frame,bar_end,reason)
    except (AttributeError,ImportError,TypeError,ValueError):
        pass


def _new_up_candidate(self,book,now):
    from d_upbit import floor_tick,SECONDS
    if not isinstance(book,dict):return
    symbol=str(book.get('market') or '')
    if not symbol.startswith('KRW-'):return
    # The original 4~5 second time gate remains exactly unchanged.
    due=[(tf,_bar_close(now,tf)[1]) for tf in ('5m','30m','60m','day')
         if 4<=_bar_close(now,tf)[0]<=5]
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
                                        'UP_RULE_'+reason)
            continue
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
            'source':'UPBIT_V067_FORMING_FRESH_TICK_AND_BOOK'}
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
                                            'UP_CANDIDATE_REGISTERED_NATIVE_FILL_PENDING')
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
    if trade is None:return
    try:
        bid=dec(book['bid']);bid_qty=dec(book['bid_size'])
        if bid<=0 or bid_qty<=0:return
        if abs(now.timestamp()-float(book['timestamp'])/1000)>3:return
    except (TypeError,KeyError,ValueError):return
    with self.lock,self.connect() as c:
        _table(c)
        rows=c.execute("""SELECT p.*,s.kind,s.state_json FROM positions p
            JOIN ma_v067_positions s ON s.position_id=p.id
            WHERE p.market=? AND p.status='OPEN' ORDER BY p.id""",
            (symbol,)).fetchall()
        if not rows:return
        depth=qty_floor(bid_qty*Decimal('.5'))
        for p in rows:
            qty=dec(p['qty'])
            if qty<=0:continue
            try:state=json.loads(p['state_json'] or '{}')
            except (TypeError,ValueError):continue
            closes=_prior_verified_closes(p['tf'],symbol,now)
            forced=policy.forced_ma_exit(closes,trade)
            if not forced and _second_followup(p['tf'],symbol,state,p['entry'],trade,now):
                forced='MA_V067_SECOND_FOLLOWUP_BELOW_ENTRY'
            if p['kind']=='REVERSAL_UP':
                updated,want,reason=policy.up_exit(
                    state,bid,p['entry'],min(qty,depth),p['qty0'],now,forced)
            else:
                updated,want,reason=policy.bear_exit(
                    state,bid,p['entry'],min(qty,depth),p['qty0'],now,forced)
            if updated!=state:
                c.execute('UPDATE ma_v067_positions SET state_json=?,updated_at=? WHERE position_id=?',
                          (json.dumps(updated,ensure_ascii=False),now.isoformat(),p['id']))
            desired=qty_floor(min(want,qty,depth))
            if desired<=0 or not reason:continue
            key='MA_V067:'+symbol+':'+str(p['id'])+':'+reason+':'+str(int(book['timestamp']))
            # Calling native _sell uses real broker/PAPER verification inside
            # the original live SQL proxy and records its filled quantity.
            if not self._sell(c,p,desired,bid,now.isoformat(),reason,event_key=key):
                continue
            executed=c.execute("""SELECT qty,price FROM executions
                WHERE event_key=? AND side='SELL' AND position_id=?""",
                (key,p['id'])).fetchone()
            if executed is None:continue
            actually=policy.number(executed['qty'])
            px=executed['price']
            if p['kind']=='REVERSAL_BEAR':
                applied=policy.apply_bear_fill(updated,reason,actually,now)
                applied=policy.mark_stage_completion(applied,p['qty0'],now,px)
            else:
                applied=policy.apply_up_fill(updated,reason,actually,now,p['qty0'],px)
            c.execute('UPDATE ma_v067_positions SET state_json=?,updated_at=? WHERE position_id=?',
                      (json.dumps(applied,ensure_ascii=False),now.isoformat(),p['id']))
            depth=max(Decimal(0),depth-actually)


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
        if row and not str(reason).startswith('MA_V067_'):
            return False # Prior SR1/V24/V061 exits cannot override new case.
        return previous_sell(self,c,p,desired,price,at,reason,event_key=event_key)
    ma.MACoinLedger._sell=scoped_sell

    older_book=ma.MACoinLedger.process_book
    def book(self,raw,at=None):
        now=at or d_upbit.kst_now()
        if now.tzinfo is None:return 0
        _new_up_candidate(self,raw,now)
        _process_v067_exits(self,raw,now)
        return older_book(self,raw,at)
    ma.MACoinLedger.process_book=book

    # v061 coin scan records verified closed candles in _HISTORY, while
    # Market Trend's existing price memory receives true Upbit WS tickers.
    # The new observer merely caches the same ticker for a 3s book match.
    prior_tick=trend.Coordinator.observe_coin_tick
    def verified_coin_tick(coord,code,price):
        try:
            runner=getattr(coord.rt,'coin_runner',None)
            ledger=getattr(runner,'ma_ledger',None) if runner else None
            if ledger is not None:
                if not hasattr(ledger,'_ma_v067_live_ticks'):
                    ledger._ma_v067_live_ticks={}
                ledger._ma_v067_live_ticks[str(code)]=(price,time.monotonic())
        except (AttributeError,TypeError,ValueError):
            pass
        return prior_tick(coord,code,price)
    trend.Coordinator.observe_coin_tick=verified_coin_tick
    _INSTALLED=True
