"""Independent low-latency, OFFICIAL completed-candle MA turnpoint verifier.

The regular all-market REST scanner often runs longer than 5 seconds. This
worker uses ONLY genuine previously verified 121-bar history for a shortlist,
then requests the just-closed official candle at the next bar boundary.
A temporary forming-price candidate is used only for prefiltering and NEVER
placed as a trade or written as a verified historical candle.
No "fill at opening" is invented: only a real fresh Upbit best book within
five elapsed seconds can create an order / trigger existing broker FOK.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from decimal import Decimal
import ma_v061_rules as rule

_INSTALLED=False

def _shortlist(history,mid):
    if len(history)<121:return False
    closes=[rule.dec(row['close']) for row in history[-121:]]
    if any(v is None or v<=0 for v in closes):return False
    # ±1.5% broad observation band is only a shortlist; never a signal.
    prior=rule.ma_values(closes,len(closes)-1)
    if rule.turnpoint_condition(prior):
        return False
    mid=rule.dec(mid)
    if mid is None or mid<=0:return False
    for ratio in (Decimal('.985'),Decimal('.995'),Decimal('1'),
                  Decimal('1.005'),Decimal('1.015')):
        possible=rule.ma_values(closes+[mid*ratio],len(closes))
        if rule.turnpoint_condition(possible):return True
    return False

def _verified_book(runner,code,now):
    try:
        with runner._lock:
            book=dict(runner.last_book.get(code) or {})
            tick=runner.tick_sizes.get(code)
            approved=(runner.ledger._allowed_new_markets is None or
                      code in runner.ledger._allowed_new_markets)
            warning=code in runner.market_warnings
        timestamp=int(book.get('timestamp') or 0)/1000.0
        if (not approved or warning or tick is None or not book
            or abs(now.timestamp()-timestamp)>3
            or rule.dec(book.get('bid')) is None
            or rule.dec(book.get('ask')) is None
            or rule.dec(book.get('bid'))<=0
            or rule.dec(book.get('ask'))<=0):
            return None,None
        return book,tick
    except (TypeError,ValueError,AttributeError):
        return None,None

def _boundary_opportunities(runner,now):
    import d_upbit
    import ma_v061_coin_runtime as coin
    with coin._HISTORY_LOCK:
        snapshot=list(coin._HISTORY.items())
    candidates=[]
    for (frame,code),(end,bars) in snapshot:
        period=d_upbit.SECONDS[frame]
        bucket=int(now.timestamp())//period*period
        elapsed=now.timestamp()-bucket
        if not 0<=elapsed<5:continue
        signal_start=dt.datetime.fromtimestamp(bucket-period,dt.timezone.utc)
        if end!=signal_start or len(bars)<121:continue
        book,tick=_verified_book(runner,code,now)
        if not book:continue
        # Only prefilter a broad range using the observed current market.
        if _shortlist(bars,book['bid']):
            candidates.append((frame,code,signal_start,tuple(bars),tick))
    # To respect Upbit public request pacing, do not fan out 140 calls at
    # once. Unexamined opportunities are explicitly MISSED, never late buys.
    return candidates[:8]

def _verify_and_order(runner,client,case,now):
    import d_upbit
    import coin_ma_engine as ma
    frame,code,start,history,tick=case
    # Request 11 newest official candles; at the exact boundary this usually
    # includes the last completed bar and at least one forming next candle.
    # REST network absence is NOT permission to infer candle high/low.
    raw=client.candles(code,frame,count=11)
    observed=d_upbit.point_in_time_bars(raw,frame,now)
    exact=[bar for bar in observed if bar['start']==start]
    if not exact:return False
    closed=history[-121:]+(exact[-1],)
    if len(closed)<122:return False
    facts=rule.candidate_from_closed(closed,frame)
    if facts.get('kind')!='TURNPOINT':return True
    quote,_=_verified_book(runner,code,dt.datetime.now(dt.timezone.utc))
    if quote is None:return False
    stamp=dt.datetime.now(d_upbit.KST)
    if (stamp.astimezone(dt.timezone.utc)-start).total_seconds()>d_upbit.SECONDS[frame]+5:
        return False
    _,candidate=ma.ma_reference_decision(closed,frame,code,tick,stamp)
    if not candidate or candidate['features'].get('ma_v061_kind')!='TURNPOINT':
        return True
    name=runner.markets.get(code,code)
    ledger=runner.ma_ledger
    # Original independent MA cash, position cap, broker FOK and stop-reentry
    # safeguards are still enforced inside record_candidate/process_book.
    queued=ledger.record_candidate(candidate,name,stamp)
    if queued:
        ledger.write_issue('MA_V061_PIVOT_VERIFIED_OPEN_WINDOW',
            frame+' '+code+' signal='+start.isoformat()+
            ' queued_at='+stamp.isoformat(),severity='INFO',now=stamp)
    latest,_=_verified_book(runner,code,dt.datetime.now(dt.timezone.utc))
    if latest is not None:
        # A real, fresh quote gives an actionable near-opening attempt now,
        # independently from the slower 5s REST/orderbook supervisor cycle.
        ledger.process_book(latest,at=dt.datetime.now(d_upbit.KST))
    return True

def _work(runner):
    import d_upbit
    client=d_upbit.UpbitPublicClient(min_interval=.12)
    completed=set()
    fail_counts={}
    while not runner.stop_event.is_set():
        now=dt.datetime.now(dt.timezone.utc)
        cases=_boundary_opportunities(runner,now)
        active={(f,c,start.isoformat()) for f,c,start,_,_ in cases}
        for case in cases:
            frame,code,start,*_=case
            key=(frame,code,start.isoformat())
            if key in completed:continue
            try:
                finished=_verify_and_order(runner,client,case,now)
            except Exception as exc:
                n=fail_counts.get(key,0)+1
                fail_counts[key]=n
                if n==1 or n==5:
                    try:runner.ma_ledger.write_issue(
                        'MA_V061_OFFICIAL_CANDLE_VERIFY_RETRY',
                        frame+' '+code+' '+type(exc).__name__,
                        severity='WARN',now=d_upbit.kst_now())
                    except Exception:pass
                finished=False
            if finished:completed.add(key)
        # Keep bounded memory and clean obsolete signal keys.
        if len(completed)>1600:
            completed=set(list(completed)[-500:])
        if len(fail_counts)>1600:fail_counts.clear()
        runner.stop_event.wait(.20)

def install():
    global _INSTALLED
    if _INSTALLED:return
    import d_upbit
    original=d_upbit.UpbitPaperRunner.start
    def start(self):
        result=original(self)
        if not getattr(self,'_ma_v061_fast_thread',None) or not self._ma_v061_fast_thread.is_alive():
            worker=threading.Thread(target=_work,args=(self,),
                name='artitrade-ma-turnpoint-5second-verifier',daemon=True)
            self._ma_v061_fast_thread=worker
            worker.start()
        return result
    d_upbit.UpbitPaperRunner.start=start
    _INSTALLED=True
