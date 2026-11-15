"""v062 stock MA real-KRX tick entry lane: full-universe first 5s routing.

Root cause: d_live.LivePaperRunner._tick_job_loop calls StockMAService
on_tick only for codes already in ma.watched_codes(). A new MA turnpoint
stock is not yet watched, so the first eligible KRX tick was dropped before
v061's _try_open_first5s could create its first candidate.

This dedicated bounded worker receives ONLY true KRX 0B with fresh exchange
and receive timestamps, and delegates to the native service's existing
on_tick and shared 0D orderbook. It does not open another broker socket,
bypass the live lease or manufacture a fill. No result is backdated if 5
seconds elapsed, and old baseline/AT/GMO/CLOSE work is left untouched.
"""
from __future__ import annotations
import datetime as dt
import queue
import threading
import time

MAX_QUEUE=512
MAX_DISPATCH_PER_CODE=3
_INSTALLED=False
UTC=dt.timezone.utc

def _eligible(tick,now=None):
    from d_minute_repair_fast import exchange_stamp_is_fresh
    from d_v19_portfolio import live_session
    if not isinstance(tick,(tuple,list)) or len(tick)<9 or str(tick[7])!='KRX':
        return False
    try:
        moment=dt.datetime.fromisoformat(str(tick[6]))
        if moment.tzinfo is None:return False
        current=now or dt.datetime.now(moment.tzinfo)
        if abs((current-moment).total_seconds())>2.9:return False
        # The minute boundary also covers the KRX 09:00 open, day opening
        # or any 30m/60m transition. No 5-second late candle inference.
        if moment.minute%5!=0 or not (0<=moment.second<5):
            return False
        if not live_session(moment,'KRX'):
            return False
        if not exchange_stamp_is_fresh(tick[8],tick[6],current):
            return False
        return True
    except (ValueError,TypeError,OverflowError,AttributeError):
        return False


def _lane(runner):
    cache={}
    while not runner.stop_event.is_set():
        try:
            tick=runner._ma_v062_queue.get(timeout=.15)
        except queue.Empty:
            continue
        try:
            if not _eligible(tick):
                continue
            code=str(tick[0])
            slot=str(tick[6])[:16]
            key=(code,slot)
            n=cache.get(key,0)
            if n>=MAX_DISPATCH_PER_CODE:
                continue
            cache[key]=n+1
            if not getattr(runner,'ma',None):
                continue
            # The exact native MA service drives price/qty/position slot,
            # original-KRX 0D quote proof and broker SQL FOK interception.
            changed=runner.ma.on_tick(tick,runner.engine.book)
            if changed:
                runner._ledger_changed()
        except Exception as exc:
            try:
                runner.store.write_runtime_error(
                    exc,'MA_V062_FIRST5_KRX_DISPATCH:'+str(tick[0]))
            except Exception:
                pass
        finally:
            try:runner._ma_v062_queue.task_done()
            except Exception:pass
            if len(cache)>4000:
                cache={k:v for k,v in cache.items()
                       if k[1]==str(dt.datetime.now(dt.timezone.utc))[:16]}


def install():
    global _INSTALLED
    if _INSTALLED:return
    import d_live
    original=d_live.LivePaperRunner._new_tick
    def forward(self,tick):
        result=original(self,tick)
        if not _eligible(tick):
            return result
        try:
            if str(tick[0]) not in self.market_map:
                return result
            q=getattr(self,'_ma_v062_queue',None)
            if q is None:
                q=queue.Queue(maxsize=MAX_QUEUE)
                self._ma_v062_queue=q
            worker=getattr(self,'_ma_v062_thread',None)
            if worker is None or not worker.is_alive():
                worker=threading.Thread(target=_lane,args=(self,),daemon=True,
                    name='artitrade-ma-krx-first5-entry')
                self._ma_v062_thread=worker
                worker.start()
            try:
                q.put_nowait(tuple(tick))
            except queue.Full:
                # Explicitly dropped; never a synthetic later opportunity.
                if int(time.monotonic())%30==0:
                    self.store.write_runtime_error(
                        RuntimeError('KRX MA first5 queue full'),
                        'MA_V062_KRX_FIRST5_BACKPRESSURE')
        except (AttributeError,TypeError,ValueError):
            return result
        return result
    d_live.LivePaperRunner._new_tick=forward
    _INSTALLED=True
