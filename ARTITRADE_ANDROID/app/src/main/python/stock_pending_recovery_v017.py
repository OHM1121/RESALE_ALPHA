"""0.0.17 stock FIX14 official-frame recovery retry and verified-pending ownership.

Source baseline: 0.0.16 original 82-module archive b014a00f...
Evidence: 0.0.15 phone errors showed pending FIX14 audits remaining BLOCKED,
transient Kiwoom DNS errors with no scheduled retry, and an original retry
earlier than the 55-second native throttle being silently discarded.

Only the SAME native Kiwoom client, original official REST recover_frame, source
DStore and original PAPER ledger are used. No new order/position/fill or
historical candle is created here, and no GMO/CW/MA/Trend strategy threshold is
changed. This is an additive ownership-preserving scheduling patch for FIX14.
"""
from __future__ import annotations

import queue
import time
from datetime import datetime
from threading import RLock

_INSTALLED = False
_ORIGINAL_GUARD = None
_ORIGINAL_QUEUE = None
_ORIGINAL_LOOP = None
_PRIORITY_LOCK = RLock()
_ALLOWED = frozenset(("5m", "30m", "60m", "day"))
_RETRYABLE = frozenset((
    "OFFICIAL_LAST_PARENT_MISSING", "OFFICIAL_PREVIOUS_DAY_MISSING",
    "PARENT_SEQUENCE_GAP", "HISTORICAL_VOLUME_NOT_READY",
    "OFFICIAL_CACHED_OHLCV_CONFLICT", "OFFICIAL_REVISION_REQUIRES_REVIEW",
    "OFFICIAL_REVISION_PENDING_CONFIRMATION", "FRAME_NOT_CLOSED",
))


def _retry(runner, key, seconds):
    """Never lose work because retry is due before the native 55s minimum."""
    now = time.monotonic()
    throttle = float(runner._frame_repair_last_attempt.get(key, 0) or 0)
    later = max(now + max(.15, float(seconds)), throttle + .15)
    prior = runner._frame_repair_retry.get(key)
    if prior is None or float(prior) < later:
        runner._frame_repair_retry[key] = later
    return runner._frame_repair_retry[key]


def _native_pending_priorities(runner):
    """Refresh a small, read-only set rather than query SQLite per stock."""
    now = time.monotonic()
    old = float(getattr(runner, "_v017_pinned_at", 0) or 0)
    if old and now - old < 30.0:
        return getattr(runner, "_v017_pending_pairs", set())
    # The only source of priority is the native order table. An unavailable
    # table is no evidence of an executable order: fall back to native order.
    with _PRIORITY_LOCK:
        old = float(getattr(runner, "_v017_pinned_at", 0) or 0)
        if old and now-old < 30.0:
            return getattr(runner, "_v017_pending_pairs", set())
        pairs = set()
        try:
            with runner.store.lock, runner.store.connect() as db:
                pairs = {(str(r["code"]),str(r["tf"])) for r in db.execute(
                    "SELECT DISTINCT code,tf FROM v19_orders WHERE status='PENDING'"
                    " AND venue='KRX' AND tf IN ('5m','30m','60m','day')"
                ).fetchall()}
        except (AttributeError,ValueError,TypeError,RuntimeError):
            pairs = set()
        except Exception:
            # A DB gate stall cannot authorize a new trade or interfere with
            # native quote delivery; the untouched native queue is the fallback.
            pairs = set()
        runner._v017_pending_pairs = pairs
        runner._v017_pinned_at = now
        return pairs


def _prioritized_queue(self, code, tf):
    """Allow genuine PENDING frames before generic universe scans.

    Every queued item still runs through the *same* single original REST
    worker and its original official-bar validator. No extra socket/client.
    """
    if tf not in _ALLOWED:
        return _ORIGINAL_QUEUE(self, code, tf)
    code = str(code)
    if (code, tf) not in _native_pending_priorities(self):
        return _ORIGINAL_QUEUE(self, code, tf)
    key = (code, tf)
    with self._frame_repair_lock:
        if key in self._frame_repair_queued:
            return False
        self._frame_repair_seq += 1
        try:
            self._frame_repair_queue.put_nowait(
                (-1, self._frame_repair_seq, code, tf))
        except queue.Full:
            # A full queue is not completion and never authorizes BUY.
            return False
        self._frame_repair_queued.add(key)
        return True


def _guard_after_verified_native_parent(engine, code, tf, at=None):
    """Prevent an unrelated delayed *global* minute checkpoint from erasing a
    fully VERIFIED native per-symbol history audit.

    A signal stays blocked unless its latest eligible closed parent actually
    exists with official broker provenance and every OPEN PENDING row has an
    original VERIFIED audit made no earlier than that parent.
    """
    from d_multiframe_recovery_fix14 import expected_last_parent, stamp_for
    from d_engine import bar_end
    if tf not in _ALLOWED:
        return _ORIGINAL_GUARD(engine, code, tf, at)
    stamp = stamp_for(at)
    expected = expected_last_parent(stamp,tf)
    if expected is None:
        return _ORIGINAL_GUARD(engine,code,tf,at)
    verified_native_parent=False
    try:
        with engine.store.lock, engine.store.connect() as db:
            parent = db.execute(
                "SELECT source FROM bars WHERE code=? AND frame=? AND ts=?",
                (str(code),tf,expected)).fetchone()
            verified_native_parent=(parent is not None and
                str(parent["source"])=="FIX15_VERIFIED_KIWOOM_REST")
            active = db.execute(
                """SELECT o.signal_id,a.state,a.checked_at
                   FROM v19_orders o LEFT JOIN v19_higher_frame_audit14 a
                   ON a.signal_id=o.signal_id
                   WHERE o.code=? AND o.tf=? AND o.status='PENDING'
                   AND o.venue='KRX'""",(str(code),tf)).fetchall()
            # Never turn an unverified or not-yet-rechecked BUY candidate into
            # VERIFIED. That state is only written by original recover_frame.
            if verified_native_parent and active and all(
                    r["state"]=="VERIFIED" and str(r["checked_at"])>=expected
                    for r in active):
                return 0
    except Exception:
        # The original guard is deliberately conservative if audit DB fails.
        pass
    # The original guard is called only AFTER the read-only DB context exits;
    # it owns its own write transaction and must never nest a SQLite writer.
    return _ORIGINAL_GUARD(engine,code,tf,at)


def _requeue_due(runner):
    """Due work cannot starve behind continuously populated scan queues."""
    now = time.monotonic()
    due = sorted(
        ((float(when), key) for key, when in
         tuple(runner._frame_repair_retry.items()) if float(when) <= now),
        key=lambda x: x[0])[:24]
    for _, key in due:
        if runner._queue_frame_repair(*key):
            runner._frame_repair_retry.pop(key, None)


def _reliable_original_frame_loop(self):
    """Faithful original FIX14 worker with repaired retry lifecycle.

    The original implementation silently lost a DNS-error job, discarded a
    retry if it woke before the 55s throttle, and only scanned due retries
    when the full-universe priority queue was EMPTY. This worker resolves
    all three without lowering source/quote/history/ledger safety gates.
    """
    from d_storage import now_kst
    from d_engine import bar_end
    from d_multiframe_recovery_fix14 import recover_frame
    while not self.stop_event.is_set():
        try:
            _requeue_due(self)
        except (RuntimeError,TypeError,ValueError,AttributeError):
            # Native queues continue even when read-only priority data races.
            pass
        try:
            _, _, code, tf = self._frame_repair_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        key = (code, tf)
        try:
            if self.api is None or code not in self.market_map:
                _retry(self,key,30.0)
                continue
            if not self._minute_repair_queue.empty():
                _retry(self,key,3.0)
                continue
            if self._tick_queue.qsize() >= 24:
                _retry(self,key,1.5)
                continue
            moment=now_kst()
            hm=moment.strftime("%H%M")
            # Prior original cutoff 15:40 prevented repairing 16:xx PENDING
            # even though the same confirmed official history could be safely
            # read before NXT extended close. This never opens broker trading.
            if moment.weekday()>=5 or not ("0855" <= hm <= "2000"):
                _retry(self,key,180.0)
                continue
            now = time.monotonic()
            last = float(self._frame_repair_last_attempt.get(key, 0) or 0)
            if now < last:
                _retry(self,key,max(.15,last-now))
                continue
            self._frame_repair_last_attempt[key] = now + 55.0
            result=recover_frame(self.store,self.engine,self.api,code,tf)
            state=str(result.get("state") or "")
            if state=="VERIFIED_RESTORED":
                self._frame_repair_retry.pop(key,None)
                if result.get("new_pending") or result.get("expired_pending"):
                    self._ledger_changed()
                if result.get("selected"):
                    self._queue_pipeline_event("fix14_reference_recovered",{
                        "code":code,"frame":tf,"new_pending":result.get("new_pending",0),
                        "signal_time_not_execution_time":True})
                # Only native VERIFIED audit can release the order safety gate.
                getattr(self,"_v017_repair_failures",{}).pop(key,None)
            else:
                delay={
                    "FRAME_NOT_CLOSED":60.0,
                    "OFFICIAL_LAST_PARENT_MISSING":35.0,
                    "OFFICIAL_PREVIOUS_DAY_MISSING":35.0,
                    "HISTORICAL_VOLUME_NOT_READY":180.0,
                    "OFFICIAL_REVISION_PENDING_CONFIRMATION":12.0,
                    "OFFICIAL_REVISION_REQUIRES_REVIEW":
                        (12.0 if tf=="day" and
                         int(result.get("daily_audit_observations",0))<2
                         else 3600.0),
                }.get(state,90.0)
                if state in _RETRYABLE:
                    _retry(self,key,delay)
                if state not in ("FRAME_NOT_CLOSED",
                                 "OFFICIAL_REVISION_PENDING_CONFIRMATION"):
                    expected=str(result.get("expected") or "")
                    just_closed=False
                    if state=="OFFICIAL_LAST_PARENT_MISSING" and expected:
                        try:
                            closed_at=datetime.strptime(bar_end(expected,tf),
                                                        "%Y%m%d%H%M%S")
                            elapsed=(now_kst().replace(tzinfo=None)-closed_at).total_seconds()
                            just_closed=0<=elapsed<90
                        except (TypeError,ValueError,OverflowError):
                            pass
                    if not just_closed:
                        label={"5m":"5분봉","30m":"30분봉",
                               "60m":"1시간봉","day":"일봉"}.get(tf,tf)
                        self.store.issue("FIX5_FRAME_UNVERIFIED",
                          label+" 원본 공식 완료봉 검증 미완료 · 재시도 예약",
                          key="v017-retry:"+str(code)+":"+tf+":"+
                              now_kst().strftime("%Y%m%d%H"),
                          details={"frame":tf,"state":state,
                                   "retry_scheduled":state in _RETRYABLE,
                                   "historical_fills_created":False})
        except Exception as exc:
            try:self.store.write_runtime_error(exc,"v017_fix14_frame_repair:"+str(code)+":"+tf)
            except Exception:pass
            failures=getattr(self,"_v017_repair_failures",None)
            if failures is None:
                failures={}
                self._v017_repair_failures=failures
            failures[key]=min(8,int(failures.get(key,0))+1)
            # Includes DNS failures. Preserve pending and retry using the same
            # authenticated Kiwoom REST client, never a fabricated fallback.
            _retry(self,key,min(300.0,15.0*(2**min(4,failures[key]))))
        finally:
            with self._frame_repair_lock:
                self._frame_repair_queued.discard(key)
            self._frame_repair_queue.task_done()


def install():
    global _INSTALLED, _ORIGINAL_GUARD, _ORIGINAL_QUEUE, _ORIGINAL_LOOP
    if _INSTALLED:
        return
    import d_live
    import d_multiframe_recovery_fix14 as native
    runner = d_live.LivePaperRunner
    _ORIGINAL_GUARD = native.guard_missing_live_parent
    _ORIGINAL_QUEUE = runner._queue_frame_repair
    _ORIGINAL_LOOP = runner._frame_repair_loop
    native.guard_missing_live_parent = _guard_after_verified_native_parent
    runner._queue_frame_repair = _prioritized_queue
    runner._frame_repair_loop = _reliable_original_frame_loop
    _INSTALLED = True
