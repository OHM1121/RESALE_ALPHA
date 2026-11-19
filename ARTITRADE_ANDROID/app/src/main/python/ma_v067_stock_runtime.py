"""MA v067 stock KRX live 0B / native 0D/FOK integration.

No retrospective candle trades; only maintained alignment completed bears
and genuine LIVE close-10s through close-2s rising transitions. Existing StockMAService remains
sole wallet, official shared depth and broker execution owner.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import queue
import sqlite3
import threading
from decimal import Decimal,ROUND_CEILING
import ma_v067_policy as policy

_INSTALLED=False
_LOCK=threading.RLock()
KST=dt.timezone(dt.timedelta(hours=9))
MAX_QUEUE=512
MAX_ACTIVE_SECONDS=3


class _LatestVerifiedMAQueue:
    """Bound queue by unique stock, not raw KRX 0B messages.

    v005 logged MA_V067_UP_5S_QUEUE_FULL because dozens of same-symbol
    ticks piled up behind SQLite/MA work. Keep the latest *received genuine*
    event per symbol. Workers still independently check its exchange time,
    session and 0D depth before asking the unmodified native wallet.
    """
    def __init__(self,maxsize=MAX_QUEUE):
        self._queue=queue.Queue(maxsize=maxsize)
        self._latest={}
        self._pending=set()
        self._lock=threading.Lock()

    def offer(self,tick):
        if not isinstance(tick,(tuple,list)) or len(tick)<9:
            return False
        code=str(tick[0])
        with self._lock:
            if code not in self._pending:
                try:self._queue.put_nowait(code)
                except queue.Full:return False
                self._pending.add(code)
            prior=self._latest.get(code)
            # Never roll a verified symbol backward to a delayed older print.
            if prior is None or str(tick[6])>=str(prior[6]):
                self._latest[code]=tuple(tick)
        return True

    def get(self,timeout=None):
        code=self._queue.get(timeout=timeout)
        with self._lock:
            tick=self._latest.pop(code,None)
            self._pending.discard(code)
        return tick

    def task_done(self):
        self._queue.task_done()

    def qsize(self):
        return self._queue.qsize()

    def empty(self):
        return self._queue.empty()


def _table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS ma_v067_positions(
        position_id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        frame TEXT NOT NULL,
        code TEXT NOT NULL,
        state_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")


def _candidate_from_closed(rows,tf,when):
    from d_engine import _krx_tick
    rows=[dict(r) for r in rows]
    if not rows:return {'decision':'REJECT','reason':'NO_CONFIRMED_BARS'},None
    cur=rows[-1]
    verdict=policy.bear_reversal_completed(rows,tf)
    audit={'decision':'REJECT','reason':verdict.get('reason',''),
           'ref_ts':str(cur.get('ts') or ''),
           'ma_v067_kind':verdict.get('kind') or '',
           'body_pct':verdict.get('body_pct'),
           'policy':policy.__name__}
    if verdict.get('kind')!='REVERSAL_BEAR':return audit,None
    if not all(str(cur.get(x) or '') for x in ('code','market','ts','low','high','open','close')):
        audit['reason']='INVALID_SOURCE_BAR';return audit,None
    lo,hi,close,op=(policy.number(cur[x]) for x in ('low','high','close','open'))
    if any(x is None for x in (lo,hi,close,op)) or not (0<lo<hi and lo<=min(op,close)<=max(op,close)<=hi):
        audit['reason']='INVALID_SOURCE_OHLC';return audit,None
    tick=Decimal(str(_krx_tick(int(close))))
    if tick<=0:return audit,None
    # Actual 0D ask/depth and broker FOK determine future fills. Never set
    # a simulated "current market price" or use an old candle close as ASK.
    limit=int((lo/tick).to_integral_value(rounding=ROUND_CEILING)*tick)
    first=int((hi/tick).to_integral_value(rounding=ROUND_CEILING)*tick)
    if first<=limit:
        audit['reason']='HIGH_NOT_ABOVE_ENTRY_LIMIT';return audit,None
    audit.update(decision='QUALIFIED',reason='MA_V067_SUSTAINED_LARGE_BEAR',
                 ma_v061_kind='DOWN_BEAR',ma_v067_kind='REVERSAL_BEAR',
                 ma_v067_low=str(lo),ma_v067_high=str(hi),
                 ma_v067_body_pct=str(verdict['body_pct']),
                 ma_v067_max_rebound_buy=False)
    candidate={'tf':tf,'code':str(cur['code']),'market':str(cur['market']),
        'ref_ts':str(cur['ts']),'name':'','limit':limit,
        'target':first,'first_fraction':.30,'score':60.,
        'special':False,'low':int(lo),'close':int(close),
        'high':int(hi),'bodytop':max(int(op),int(close)),
        'recovery1':int(lo),'audit':audit}
    return audit,candidate


def _read_state(c,pid):
    _table(c)
    row=c.execute('SELECT kind,state_json FROM ma_v067_positions WHERE position_id=?',(pid,)).fetchone()
    if row is None:return None,None
    try:return str(row['kind']),json.loads(row['state_json'])
    except (TypeError,ValueError,KeyError):return None,None


def _store_state(c,pid,state,at):
    c.execute('UPDATE ma_v067_positions SET state_json=?,updated_at=? WHERE position_id=?',
              (json.dumps(state,ensure_ascii=False),at.isoformat(),pid))


def _join_new_positions(self,c,code):
    _table(c)
    rows=c.execute("""SELECT p.*,a.features FROM positions p
        JOIN candidates a ON a.id=p.candidate_id
        LEFT JOIN ma_v067_positions v ON v.position_id=p.id
        WHERE p.status='OPEN' AND p.code=? AND v.position_id IS NULL
        ORDER BY p.id DESC""",(code,)).fetchall()
    for p in rows:
        try:f=json.loads(p['features'] or '{}')
        except (TypeError,ValueError):continue
        kind=f.get('ma_v067_kind')
        if kind not in ('REVERSAL_BEAR','REVERSAL_UP'):continue
        try:
            state=(policy.initial_bear_exit_state(str(p['tf']),f.get('ma_v067_high') or p['high'])
                   if kind=='REVERSAL_BEAR' else policy.initial_up_exit_state(str(p['tf'])))
            state['entry_at']=str(p['entry_at'])
            state['entry_bar_start']=str(f.get('ma_v067_bar_start') or '')
            if kind=='REVERSAL_UP':
                one,two=policy.up_targets(p['entry'],p['tf'])
                state['exit_intents']=[
                    {'stage':1,'price':str(one),'fraction_of_original':'.30',
                     'status':'PRESTAGED_PAPER_TARGET'},
                    {'stage':2,'price':str(two),'fraction_of_original':'.35',
                     'status':'PRESTAGED_PAPER_TARGET'}]
                state['hard_stop']=str(policy.number(p['entry'])*Decimal('.85'))
                state['policy_source']='NEW LEGACY(11.18).xlsx'
            if kind=='REVERSAL_UP':
                # Set physical stock-share stage targets before the first
                # sell. 13 shares -> first ceil(1.3)=2, post-first 11 shares
                # -> second ceil(5.5)=6. Never round a confirmed fill to
                # fractional stock shares or reject its actual 2/6 prints.
                initial=int(p['qty0'])
                stock_first=max(1,min(initial,int(math.ceil(initial*.30))))
                stock_second=(max(1,int(math.ceil(initial*.35)))
                              if initial>stock_first else 0)
                state['rounded_first_goal']=str(stock_first)
                state['rounded_second_goal']=str(stock_second)
        except (TypeError,ValueError,KeyError):continue
        at=str(p['entry_at'])
        c.execute("""INSERT OR IGNORE INTO ma_v067_positions
            (position_id,kind,frame,code,state_json,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?)""",
            (p['id'],kind,p['tf'],code,json.dumps(state,ensure_ascii=False),at,at))
        first_target=(float(state['ref_high']) if kind=='REVERSAL_BEAR'
                      else float(policy.up_targets(p['entry'],p['tf'])[0]))
        fraction=.30
        first=(int(state['rounded_first_goal']) if kind=='REVERSAL_UP'
               else max(1,int(math.ceil(int(p['qty0'])*fraction))))
        c.execute('UPDATE positions SET target=?,first_qty=? WHERE id=?',
                  (int(math.ceil(first_target)),first,p['id']))


def _expire_old_or_rebounded(self,tick,now):
    import stock_ma_engine as native
    code=str(tick[0]);value=policy.number(tick[4])
    if value is None or value<=0:return
    with self.lock,native._connect(self.db) as c:
        found=c.execute("""SELECT id,created_at,low,features FROM candidates
           WHERE code=? AND status='PENDING' AND
           features LIKE '%"ma_v067_kind"%'""",(code,)).fetchall()
        for row in found:
            try:features=json.loads(row['features'])
            except (TypeError,ValueError):continue
            kind=features.get('ma_v067_kind')
            if kind=='REVERSAL_UP':
                try:
                    submitted=dt.datetime.fromisoformat(row['created_at'])
                    if submitted.tzinfo is None or (now-submitted).total_seconds()>15:
                        c.execute("UPDATE candidates SET status='MA_V067_UP_15S_TIMEOUT' WHERE id=?",
                                  (row['id'],))
                except (TypeError,ValueError):c.execute(
                    "UPDATE candidates SET status='MA_V067_UP_BAD_TIMESTAMP' WHERE id=?",
                    (row['id'],))
            elif kind=='REVERSAL_BEAR':
                state=features.get('ma_v067_low_state') or {}
                updated,allow=policy.bear_low_entry(state,value,row['low'])
                features['ma_v067_low_state']=updated
                if updated.get('rebounded_to_low'):
                    c.execute("UPDATE candidates SET status='MA_V067_BEAR_REBOUND_DISQUALIFIED' WHERE id=?",
                              (row['id'],))
                if updated!=state:
                    c.execute("UPDATE candidates SET features=? WHERE id=?",
                              (json.dumps(features,ensure_ascii=False),row['id']))


def _latest_0b(self,code,received):
    cache=getattr(self,'_ma_v067_last_trade',{})
    data=cache.get(str(code))
    if not data:return None
    price,stamp=data
    return price if abs((received-stamp).total_seconds())<=3 else None


def _second_followup_bar(self,p,when,forming_trade):
    frame=str(p['tf'])
    ticksecs={'5m':300,'30m':1800,'60m':3600,'day':86400}.get(frame)
    if not ticksecs:return False
    entrystr=str(p['entry_at'])
    try:entry=dt.datetime.fromisoformat(entrystr)
    except (ValueError,TypeError):return False
    if entry.tzinfo is None:return False
    # Exact broker-verified close bar count, not wall-clock duration.
    with self.market_store.connect() as src:
        rows=src.execute("""SELECT DISTINCT ts FROM bars
            WHERE frame=? AND code=? AND ts<=?
            ORDER BY ts DESC LIMIT 12""",
            (frame,p['code'],when.strftime('%Y%m%d%H%M%S'))).fetchall()
    starts=[]
    for r in rows:
        try:starts.append(dt.datetime.strptime(str(r['ts'])[:14],'%Y%m%d%H%M%S').replace(tzinfo=KST))
        except (TypeError,ValueError):pass
    if frame=='day':
        after=sum(1 for x in starts if entry.date()<=x.date()<when.date())
    else:
        entry_start=entry.replace(second=0,microsecond=0)
        step={'5m':5,'30m':30,'60m':60}[frame]
        entry_start=entry_start.replace(minute=(entry_start.minute//step)*step)
        current_start=when.replace(second=0,microsecond=0)
        current_start=current_start.replace(minute=(current_start.minute//step)*step)
        after=sum(1 for x in starts if entry_start<=x<current_start)
    return after==2 and policy.number(forming_trade)<policy.number(p['entry'])


def _exit_policy(self,c,p,bid,now,book,previous):
    kind,state=_read_state(c,p['id'])
    if state is None or kind!='REVERSAL_UP':
        # Old MA V061/V24 automatic exits must NEVER run after 11.18.
        # Missing sidecars remain in the original wallet, not auto-sold.
        return None
    from ma_v061_stock_runtime import _slope_closes
    trade=_latest_0b(self,p['code'],now)
    # Verified KRX 0D bid/depth can execute targets without a coincident 0B
    # print. The protective forming-MA signals must still have a fresh 0B.
    old=_slope_closes(self,p['code'],p['tf'],now) if trade is not None else []
    forced=(policy.forced_ma_exit(old,trade) if trade is not None
            and kind=='REVERSAL_BEAR' else '')
    if kind=='REVERSAL_BEAR' and trade is not None and not forced and _second_followup_bar(self,p,now,trade):
        forced='MA_V067_SECOND_FOLLOWUP_BELOW_ENTRY'
    available=int(p['qty'])
    if available<=0:return
    if kind=='REVERSAL_BEAR':
        changed,wanted,reason=policy.bear_exit(
            state,bid,p['entry'],available,p['qty0'],now,forced)
    else:
        changed,wanted,reason=policy.up_exit(
            state,bid,p['entry'],available,p['qty0'],now,forced)
    if changed!=state:_store_state(c,p['id'],changed,now)
    size=min(available,max(0,int(math.ceil(float(wanted)))))
    if not reason or size<=0:return
    # Native sell performs genuine KRX 0D capacity reservation, broker FOK
    # interception and exact actual/PAPER execution recording.
    actually=self._sell(c,p,size,int(bid),now,reason,book)
    if not actually:return
    # A returned native sell amount is not enough to advance MA stages:
    # the corresponding exact SELL execution must exist in the same ledger.
    filled=c.execute("""SELECT price,ts FROM executions WHERE position_id=?
        AND side='SELL' AND reason=? ORDER BY id DESC LIMIT 1""",
        (p['id'],reason)).fetchone()
    if filled is None:return
    px=filled['price']
    if kind=='REVERSAL_BEAR':
        state=policy.apply_bear_fill(changed,reason,actually,now)
        state=policy.mark_stage_completion(state,p['qty0'],now,px)
    else:
        state=policy.apply_up_fill(changed,reason,actually,now,p['qty0'],px)
    _store_state(c,p['id'],state,now)
    # The v067 staged state is the execution authority, but the original
    # position flags are still read by native snapshots and legacy consumers.
    # Keep those flags in sync ONLY after an exact confirmed SELL ledger row.
    if kind=='REVERSAL_UP':
        first_goal,second_goal=policy.up_stage_quantities(state,p['qty0'])
    else:
        initial=policy.number(p['qty0']) or Decimal(0)
        first_goal=initial*policy.FIRST_FRACTION
        second_goal=(initial-first_goal)*policy.SECOND_FRACTION_OF_REMAINING
    first_confirmed=bool(first_goal>0 and
        (policy.number(state.get('first_sold')) or Decimal(0))>=first_goal)
    second_confirmed=bool(second_goal>0 and
        (policy.number(state.get('second_sold')) or Decimal(0))>=second_goal)
    if first_confirmed or second_confirmed:
        c.execute("""UPDATE positions SET
            first_sold=CASE WHEN ? THEN 1 ELSE first_sold END,
            second_sold=CASE WHEN ? THEN 1 ELSE second_sold END
            WHERE id=?""",
            (int(first_confirmed),int(second_confirmed),p['id']))
    # A phone/network reconnect can deliver its first valid KRX 0B+0D when
    # price is ALREADY above both configured profit steps. Native on_tick()
    # previously evaluated only one step and then waited for another 0B,
    # which may never arrive on a quiet symbol. Advance exactly ONE more
    # step using this SAME still-fresh exchange bid, but only after a
    # verified SELL row and with the original 0D reservation budget.
    # All other exits (hard stop, trail, forced) keep their native priority.
    first_reasons=('MA_V1118_UP_FIRST_30PCT',
                   'MA_V065_FIRST_30_AT_REVERSAL_HIGH')
    if reason in first_reasons and first_confirmed:
        current=c.execute("""SELECT * FROM positions
            WHERE id=? AND status='OPEN' AND qty>0""",(p['id'],)).fetchone()
        if current is not None and all(k in current.keys() for k in (
                'id','entry','qty','qty0')):
            # The native SELL journal is already durable in this SQL
            # transaction. A malformed/incomplete reopened row must not
            # throw away that valid first execution through a retry.
            # Permit one target-2 evaluation, no recursive stage-3 or
            # second unrelated new entry on the same exchange event.
            state_now=_read_state(c,p['id'])[1]
            if state_now is not None:
                if kind=='REVERSAL_BEAR':
                    _,next_qty,next_reason=policy.bear_exit(
                        state_now,bid,current['entry'],current['qty'],
                        current['qty0'],now)
                else:
                    _,next_qty,next_reason=policy.up_exit(
                        state_now,bid,current['entry'],current['qty'],
                        current['qty0'],now)
                if next_reason in ('MA_V065_SECOND_HALF_REMAINING',
                                   'MA_V1118_UP_SECOND_35PCT') and next_qty>0:
                    # The nested call re-reads native 0D capacity and
                    # authoritative position+execution state. It cannot
                    # enter a third stage because only FIRST reasons recurse.
                    _exit_policy(self,c,current,bid,now,book,previous)


def _topup_partial_stock(self,tick,shared_book):
    """Complete an ACTUALLY underfilled MA BUY, not create a new entry.

    The exact first confirmed BUY execution sets an immutable +2 KRX-tick
    ceiling. No top-up after 15s, after ANY sale, or beyond the original
    account's per-position seed. One original SQLite transaction persists
    both added shares and their actual fresh 0D ask execution.
    """
    from partial_entry_v094 import qualified_topup,two_ticks_max
    from stock_ma_engine import _connect,FEE
    if len(tick)<8 or tick[7]!='KRX':return 0
    try:
        now=dt.datetime.fromisoformat(str(tick[6]))
        if now.tzinfo is None or abs((dt.datetime.now(KST)-now).total_seconds())>3:
            return 0
        code=str(tick[0])
        with shared_book._lock:
            quote=shared_book._items.get((code,'KRX'))
        if quote is None:return 0
        ask,bid,asksize,observed_at=quote
        if (ask<=0 or ask<bid or asksize<=0 or
            not -.5<=(now-observed_at).total_seconds()<=3):
            return 0
        from android_live_bridge import _RUNTIME
        if _RUNTIME is not None and _RUNTIME.live_enabled('stock'):
            # LIVE broker FOK/partial reconciliation is separate; never
            # credit a new PAPER BUY while the physical broker is armed.
            return 0
    except (TypeError,ValueError,AttributeError,OverflowError,ImportError):
        return 0
    with self.lock,_connect(self.db) as c:
        c.execute('BEGIN IMMEDIATE')
        _table(c)
        positions=c.execute("""SELECT p.*,s.kind,s.state_json
            FROM positions p JOIN ma_v067_positions s ON s.position_id=p.id
            WHERE p.code=? AND p.status='OPEN' AND p.qty>0
            ORDER BY p.id""",(code,)).fetchall()
        for p in positions:
            try:
                if int(p['qty'])!=int(p['qty0']) or int(p['first_sold']) or int(p['second_sold']):
                    continue
                entered=dt.datetime.fromisoformat(str(p['entry_at']))
                if entered.tzinfo is None or not 0<(now-entered).total_seconds()<=15:
                    continue
                if c.execute("SELECT 1 FROM executions WHERE position_id=? AND side='SELL' LIMIT 1",
                             (p['id'],)).fetchone():
                    continue
                fills=c.execute("""SELECT price,qty,fees,ts FROM executions
                    WHERE position_id=? AND side='BUY' ORDER BY id""",(p['id'],)).fetchall()
                if not fills:continue
                # Only a persisted real 0D original BUY can establish the
                # reference. NEVER rebase this ceiling on a later average.
                first=int(fills[0]['price'])
                spent=sum(int(x['price'])*int(x['qty'])+int(x['fees']) for x in fills)
                wallet=c.execute('SELECT seed,cash,max_positions FROM accounts WHERE tf=?',
                                 (p['tf'],)).fetchone()
                if wallet is None or int(wallet['max_positions'])<=0:continue
                per_symbol=int(wallet['seed'])//int(wallet['max_positions'])
                if not qualified_topup('stock',first,ask,spent,per_symbol,False,True):
                    continue
                cash=int(wallet['cash'])
                budget=min(cash,per_symbol-spent)
                desired=int(math.floor(budget/(ask*(1+FEE))))
                if desired<1:continue
                ceiling=two_ticks_max('stock',first)
                # Scenario 2: only AFTER a genuine first BUY remains open,
                # re-evaluate actual residual seed on a NEW fresh KRX 0D.
                # Scenario 3: a missing/stale book returns above, leaving the
                # original position and order untouched for the next 0B/0D.
                # Use no virtual fill, no independent account and no chase.
                from buy_execution_recovery_v082 import Intent
                guard=Intent('ma','stock',code,str(p['id'])+':TOPUP',
                             str(first),str(budget),str(FEE))
                guard.attempts=1  # Original BUY evidence exists in fills.
                decision=guard.quote({
                    'symbol':code,'exchange_ms':int(observed_at.timestamp()*1000),
                    'ask':int(ask),'bid':int(bid),'ask_size':int(asksize)
                },int(now.timestamp()*1000))
                if decision.get('action')!='PLACE':continue
                desired=min(desired,int(Decimal(decision['qty'])))
                if desired<1:continue
                proof,_=shared_book.available(code,'KRX',now,int(ceiling),desired)
                if proof is None or int(proof[0])!=int(ask):continue
                qty=min(desired,int(proof[1]))
                while qty>0 and (qty*ask+round(qty*ask*FEE)>budget):
                    qty-=1
                if qty<1:continue
                # One same-second execution per position, not a fabricated
                # replay of a cached top quote or an extra position.
                if c.execute("""SELECT 1 FROM executions WHERE position_id=?
                    AND side='BUY' AND ts=? LIMIT 1""",
                    (p['id'],now.isoformat())).fetchone():
                    continue
                if not shared_book.reserve(code,'KRX',now,ask,qty):continue
                fee=round(qty*ask*FEE)
                updated_qty=int(p['qty0'])+qty
                weighted=int(round((int(p['entry'])*int(p['qty0'])+ask*qty)/updated_qty))
                state=json.loads(p['state_json'])
                if p['kind']=='REVERSAL_UP':
                    first_target=max(1,min(updated_qty,int(math.ceil(updated_qty*.10))))
                    second_target=(max(1,int(math.ceil((updated_qty-first_target)*.5)))
                                   if updated_qty>first_target else 0)
                    state['rounded_first_goal']=str(first_target)
                    state['rounded_second_goal']=str(second_target)
                    target=math.ceil(float(policy.up_targets(weighted,p['tf'])[0]))
                else:
                    first_target=max(1,int(math.ceil(updated_qty*.30)))
                    target=int(p['target'])
                c.execute('UPDATE accounts SET cash=cash-? WHERE tf=?',
                          (qty*ask+fee,p['tf']))
                c.execute("""UPDATE positions SET entry=?,qty0=?,qty=?,first_qty=?,
                    target=?,last_price=? WHERE id=? AND status='OPEN'""",
                          (weighted,updated_qty,int(p['qty'])+qty,first_target,
                           target,ask,p['id']))
                c.execute("""INSERT INTO executions(tf,position_id,code,name,ts,side,qty,
                    price,fees,tax,net_profit,reason)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (p['tf'],p['id'],code,p['name'],now.isoformat(),'BUY',
                     qty,ask,fee,0,None,'MA_V094_PARTIAL_TWO_TICKS'))
                _store_state(c,p['id'],state,now)
                c.commit()
                return qty
            except (TypeError,ValueError,KeyError,OverflowError,sqlite3.Error):
                # Do not commit any half-updated wallet on audit failure.
                c.rollback()
                raise
    return 0


def _close_seconds(now,frame):
    today=now.date()
    if frame=='day':
        finish=dt.datetime.combine(today,dt.time(15,30),tzinfo=now.tzinfo)
    elif frame=='60m':
        finish=now.replace(minute=0,second=0,microsecond=0)+dt.timedelta(hours=1)
    else:
        step=5 if frame=='5m' else 30
        finish=now.replace(minute=(now.minute//step)*step,
                           second=0,microsecond=0)+dt.timedelta(minutes=step)
    return (finish-now).total_seconds(),finish


def _record_v076_stock_ma(symbol,frame,bar_end,reason,details=None):
    """Queue MA evidence only; never touch broker or wallet in this path."""
    try:
        import market_trend_runtime_v043 as trend
        rt=getattr(getattr(trend,'_CORE',None),'_RUNTIME',None)
        co=getattr(rt,'market_trend',None) if rt is not None else None
        if co is not None:
            co.record_ma_decision('stock',str(symbol),str(frame),
                                  str(bar_end),str(reason),details)
    except (ImportError,AttributeError,ValueError,TypeError):
        pass


def _up_worker(runner):
    import stock_ma_engine as native
    from d_minute_repair_fast import exchange_stamp_is_fresh
    from d_v19_portfolio import live_session
    from ma_v061_stock_runtime import _slope_closes
    seen=set()
    prewatched=set()
    while not runner.stop_event.is_set():
        try:tick=runner._ma_v067_up_queue.get(timeout=.15)
        except queue.Empty:continue
        try:
            if tick is None or len(tick)<9 or tick[7]!='KRX' or not exchange_stamp_is_fresh(tick[8],tick[6]):continue
            now=dt.datetime.fromisoformat(str(tick[6]))
            if not live_session(now,'KRX'):continue
            service=runner.ma
            code=str(tick[0])
            if code not in runner.market_map:
                for frame in ('5m','30m','60m','day'):
                    left,end=_close_seconds(now,frame)
                    if 2<=left<=30:
                        _record_v076_stock_ma(code,frame,end.isoformat(),
                            'UP_CODE_NOT_IN_KRX_VERIFIED_MARKET_MAP')
                continue
            for frame in ('5m','30m','60m','day'):
                remain,finish=_close_seconds(now,frame)
                if not (policy.in_ma_prewatch_window(remain) or policy.in_up_entry_window(remain)):
                    continue
                key=(code,frame,finish.isoformat())
                if policy.in_ma_prewatch_window(remain):
                    # Read-only 30..10s preview, sampled per five-second bucket.
                    # No candidate, broker call, pending order or simulated fill.
                    sample=(code,frame,finish.isoformat(),int(remain//5))
                    if sample not in prewatched:
                        prewatched.add(sample)
                        preview=_slope_closes(service,code,frame,now)
                        verdict=policy.prewatch_upward_alignment(preview,tick[4])
                        facts={k:verdict[k] for k in ('ma_120_delta','ma_60_delta','ma120_down_tolerance') if k in verdict}
                        facts.update({'remaining_sec':round(remain,3),'trade_price':str(tick[4]),
                                      'observation_only':True,'live_source':'KRX_VERIFIED_0B'})
                        _record_v076_stock_ma(code,frame,finish.isoformat(),
                            'MA_PREWATCH_'+str(verdict.get('reason') or 'UNKNOWN'),facts)
                    continue
                if key in seen:continue
                prices=_slope_closes(service,code,frame,now)
                result=policy.upward_transition_on_forming(prices,tick[4],remain)
                if result.get('kind')!='REVERSAL_UP':
                    _record_v076_stock_ma(code,frame,finish.isoformat(),
                        'UP_RULE_'+str(result.get('reason') or 'NO_MATCH'),
                        {k:result[k] for k in
                            ('ma_120_delta','ma_60_delta','ma120_down_tolerance')
                            if k in result})
                    continue
                # Recheck at actual submission time, not merely when the tick
                # entered the worker queue. Prefer a newer verified same-code
                # KRX 0B print if it arrived during computation.
                decision_now=dt.datetime.now(now.tzinfo)
                fresh_remain,active_finish=_close_seconds(decision_now,frame)
                if (active_finish!=finish or
                        not policy.in_up_entry_window(fresh_remain) or
                        not exchange_stamp_is_fresh(tick[8],tick[6],decision_now)):
                    _record_v076_stock_ma(code,frame,finish.isoformat(),
                        'UP_ENTRY_WINDOW_OR_VERIFIED_TRADE_EXPIRED')
                    continue
                decision_tick=tick
                with runner._quote_lock:
                    newer=getattr(runner,'_latest_krx_trades',{}).get(code)
                if (newer is not None and len(newer)>=9 and
                        str(newer[0])==code and newer[7]=='KRX' and
                        str(newer[6])>=str(tick[6]) and
                        exchange_stamp_is_fresh(newer[8],newer[6],decision_now)):
                    decision_tick=newer
                checked=policy.upward_transition_on_forming(prices,decision_tick[4],fresh_remain)
                if checked.get('kind')!='REVERSAL_UP':
                    _record_v076_stock_ma(code,frame,finish.isoformat(),
                        'UP_LAST_VERIFIED_TRADE_RULE_LOST',
                        {'last_trade_price':str(decision_tick[4]),
                         'reason':str(checked.get('reason') or 'NO_MATCH')})
                    continue
                # Never invent an exchange quote: only the same-symbol genuine
                # KRX 0D book at this actual recheck moment may authorize entry.
                with runner.engine.book._lock:
                    latest=runner.engine.book._items.get((code,'KRX'))
                if latest is None:
                    _record_v076_stock_ma(code,frame,finish.isoformat(),
                        'UP_0D_KRX_BOOK_NOT_RECEIVED',{'trade_price':str(tick[4])})
                    continue
                ask,bid,aq,at=latest
                if ask<=0 or bid<=0 or aq<1 or not (0<=(decision_now-at).total_seconds()<=3):
                    _record_v076_stock_ma(code,frame,finish.isoformat(),
                        'UP_0D_DEPTH_OR_FRESHNESS_BLOCKED',
                        {'ask':ask,'bid':bid,'ask_qty':aq,
                         'book_age_sec':round((decision_now-at).total_seconds(),4)})
                    continue
                with service.lock,native._connect(service.db) as c:
                    _table(c)
                    if c.execute("""SELECT 1 FROM positions WHERE code=? AND status='OPEN'""",
                                 (code,)).fetchone():continue
                    # An official KRX daily bar starts at the regular
                    # 09:00 session, NOT 15:30 of the previous date.
                    barstart=(now.replace(hour=9,minute=0,second=0,microsecond=0)
                              if frame=='day' else
                              finish-dt.timedelta(seconds={
                                  '5m':300,'30m':1800,'60m':3600}[frame]))
                    signal=barstart.strftime('%Y%m%d%H%M%S')
                    features={'decision':'QUALIFIED','reason':'MA_V079_UP_10_TO_2_SECONDS',
                        'ma_v061_kind':'TURNPOINT','ma_v067_kind':'REVERSAL_UP',
                        'ma_v067_bar_start':signal,'ma_v067_first_observed_at':decision_now.isoformat(),
                        'ma_v067_submit_deadline':(decision_now+dt.timedelta(seconds=15)).isoformat(),
                        'ma_v067_120_delta':checked['ma_120_delta'],
                        'ma_v067_quote_source':'EXACT_KRX_0B_AND_0D','ma_v067_no_below120_filter':True}
                    c.execute("""INSERT OR IGNORE INTO candidates(tf,code,name,market,
                        ref_ts,created_at,limit_price,target,score,special,low,
                        close_price,high,bodytop,recovery1,first_fraction,features)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (frame,code,str(decision_tick[2]),str(decision_tick[1]),signal,decision_now.isoformat(),
                         int(ask),int(math.ceil(float(ask)*(
                            1+float(policy.UP_FIRST_TARGET_PCT[frame])/100))),
                         80,0,int(bid),int(decision_tick[4]),int(ask),int(ask),int(bid),
                         .10,json.dumps(features,ensure_ascii=False)))
                    c.execute("""INSERT INTO research(ts,tf,code,ref_ts,verdict,detail)
                                 VALUES(?,?,?,?,?,?)""",
                              (now.isoformat(),frame,code,signal,'QUALIFIED',
                               json.dumps(features,ensure_ascii=False)))
                service._refresh_watched()
                _record_v076_stock_ma(code,frame,finish.isoformat(),
                    'UP_CANDIDATE_REGISTERED_NATIVE_FILL_UNVERIFIED',
                    {'trade_price':str(decision_tick[4]),'ask':int(ask),
                     'ma120_delta':checked.get('ma_120_delta'),
                     'seconds_to_close':round(fresh_remain,3)})
                # The original v067 marked this candle DONE before asking the
                # verified native wallet to buy. If the first 0B/0D attempt
                # returned 0 (e.g. trade>limit, missing executable depth),
                # the candidate appeared forever as PENDING and NO further
                # valid tick could retry before the entry deadline.
                # Mark done ONLY on a confirmed original SQLite BUY. No
                # altered trade/ask, invented fill, or strategy rule change.
                executed=service.on_tick(decision_tick,runner.engine.book)
                if executed and int(executed)>0:
                    seen.add(key)
                    _record_v076_stock_ma(code,frame,finish.isoformat(),
                        'UP_NATIVE_REAL_PAPER_BUY_CONFIRMED',
                        {'fill_count':int(executed),'ask':int(ask)})
                else:
                    _record_v076_stock_ma(code,frame,finish.isoformat(),
                        'UP_NATIVE_BUY_NOT_FILLED_RETRY_UNTIL_WINDOW_END',
                        {'trade_price':str(decision_tick[4]),'ask':int(ask),
                         'limit':int(ask),'no_synthetic_fill':True})
        except Exception as exc:
            try:
                _record_v076_stock_ma(
                    str(tick[0]) if tick else '__ENGINE__','all',
                    str(tick[6]) if len(tick)>6 else 'LIVE',
                    'OPERATIONAL_STOCK_MA_WORKER_'+type(exc).__name__)
            except Exception:pass
            try:runner.store.write_runtime_error(exc,'MA_V079_UP_TEN_TO_TWO_SECONDS')
            except Exception:pass
        finally:
            runner._ma_v067_up_queue.task_done()
            if len(prewatched)>12000:prewatched.clear()
            if len(seen)>12000:seen.clear()
            if len(seen)>5000:seen=set(list(seen)[-1000:])


def install():
    global _INSTALLED
    if _INSTALLED:return
    import stock_ma_engine as native
    import d_live
    import ma_v061_stock_runtime as legacy
    # Do not reuse V061 next-bar 5s TURNPOINT. The user now requires
    # an in-progress candle within the verified close-minus-10..2 seconds.
    legacy._try_open_first5s=lambda *a,**kw:None
    native.ma_reference_decision=lambda bars,tf,when:_candidate_from_closed(bars,tf,when)

    original_init=native.StockMAService.__init__
    def init(self,*a,**kw):
        original_init(self,*a,**kw)
        with self.lock,native._connect(self.db) as c:
            _table(c)
            # Retire V061/non-11.18 candidates before native on_tick sees
            # the next 0B; preserve all historical executions/positions.
            pending=c.execute("SELECT id,features FROM candidates WHERE status='PENDING'").fetchall()
            for candidate in pending:
                try:evidence=json.loads(candidate['features'] or '{}')
                except (TypeError,ValueError):evidence={}
                if not (evidence.get('ma_v067_kind')=='REVERSAL_UP' and
                        evidence.get('ma_v067_quote_source')=='EXACT_KRX_0B_AND_0D'):
                    c.execute("""UPDATE candidates
                        SET status='NL1118_RETIRED_LEGACY_MA'
                        WHERE id=? AND status='PENDING'""",(candidate['id'],))
            open_rows=c.execute("""SELECT p.code FROM positions p JOIN candidates a
                ON a.id=p.candidate_id LEFT JOIN ma_v067_positions v ON v.position_id=p.id
                WHERE p.status='OPEN' AND v.position_id IS NULL
                AND a.features LIKE '%"ma_v067_kind"%'""").fetchall()
            for row in open_rows:_join_new_positions(self,c,row['code'])
        self._ma_v067_last_trade={}
    native.StockMAService.__init__=init

    previous_exit=native.StockMAService._exit
    def exit(self,c,p,bid,now,book):
        return _exit_policy(self,c,p,bid,now,book,previous_exit)
    native.StockMAService._exit=exit

    original_tick=native.StockMAService.on_tick
    def tick(self,data,book):
        if len(data)<9 or data[7]!='KRX':return original_tick(self,data,book)
        from d_minute_repair_fast import exchange_stamp_is_fresh
        if not exchange_stamp_is_fresh(data[8],data[6]):return 0
        now=dt.datetime.fromisoformat(str(data[6]))
        self._ma_v067_last_trade[str(data[0])]=(data[4],now)
        _expire_old_or_rebounded(self,data,now)
        result=original_tick(self,data,book)
        if result:
            with self.lock,native._connect(self.db) as c:
                _join_new_positions(self,c,str(data[0]))
        # Original native frame/entry decision is still the ONLY way to
        # open a position. A later fresh 0D can merely complete its
        # verified partial size within the original 15-second window.
        _topup_partial_stock(self,data,book)
        return result
    native.StockMAService.on_tick=tick

    orig_feed=d_live.LivePaperRunner._new_tick
    def observe_live(self,tick):
        # Capture the actual near-close 0B for the independent MA worker
        # BEFORE normal AT/GMO/Trend dispatch can add Python/SQLite latency.
        # The original feed ALWAYS still receives exactly this tick once.
        try:
            from d_minute_repair_fast import exchange_stamp_is_fresh
            if (len(tick)>=9 and tick[7]=='KRX' and
                exchange_stamp_is_fresh(tick[8],tick[6])):
                now=dt.datetime.fromisoformat(str(tick[6]))
                if any(2<=_close_seconds(now,frame)[0]<=30
                       for frame in ('5m','30m','60m','day')):
                    q=getattr(self,'_ma_v067_up_queue',None)
                    if q is None:
                        q=_LatestVerifiedMAQueue(maxsize=MAX_QUEUE)
                        self._ma_v067_up_queue=q
                    worker=getattr(self,'_ma_v067_up_worker',None)
                    if worker is None or not worker.is_alive():
                        worker=threading.Thread(target=_up_worker,args=(self,),
                            daemon=True,name='artitrade-ma-v067-stock-preclose')
                        self._ma_v067_up_worker=worker
                        worker.start()
                    # A symbol already pending is UPDATED, not enqueued
                    # hundreds of times. Genuine latest timestamps remain
                    # mandatory; the consumer performs another freshness check.
                    if not q.offer(tuple(tick)):
                        self.store.write_runtime_error(
                            RuntimeError('MA_V067_UP_UNIQUE_SYMBOL_QUEUE_FULL'),
                            'MA_V067_STOCK_ENTRY_DROP')
        except (TypeError,ValueError,AttributeError):
            pass
        return orig_feed(self,tick)
    d_live.LivePaperRunner._new_tick=observe_live
    _INSTALLED=True
