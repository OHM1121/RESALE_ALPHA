"""0.4.5 (#160): the stock tick feed is dead for most of every session - why earlier fixes did not hold, and what changes now.

Evidence (stock error export 2026-11-27 10:27, app 0.3.6; `stream_health`, `live_minute_quality`, `TICK_PIPELINE_METRICS`,
`execution_decisions_v074`):
* 210 Kiwoom WebSocket disconnect episodes in 8 days (40-65 per day): 100 "no close frame", 89 "1011 keepalive ping timeout",
  25 "1000 Bye", 16 queue back-pressure, 69 DNS / REST connection errors. Inside the regular session 51-144 minutes per day were
  spent disconnected; on 11-26 the socket dropped every 2-5 minutes for an hour (13:50-14:50), each time after 70-150 s.
* Market-wide only about 400 ticks per minute reached the minute-bar builder (median), 45-82 % of the regular-session minutes had
  fewer than 30 ticks, and 99 % of the stock MA 5m evaluations were skipped for "no fresh tick in the 10-2 s window".
* The reconnect loop doubles its pause (2, 4, ... 60 s) and only resets it after a connection that lived 120 s; the sockets that
  die of a ping timeout live 70-150 s, so most waits are 60 s (median recovery 56-82 s). `minute_bars.clear()` drops the open minute.
* The ledger gate and the GIL are shared by the real-time reader and ~10 background analytics threads (candidate research,
  history indicators, higher-frame REST repair, 1m / 5m gap repair). TICK_PIPELINE_METRICS: gate waits of ticks up to 18 s,
  31 % of ticks expired, gate holders `fix14-higher-frame-rest`, `HISTORY_INDICATORS` (up to 96 s), `CANDIDATE_RESEARCH_ONLY`.

Why the earlier fixes (close-feed coalescing 0.2.7, subscription pins, book coalescer, queue sizes, ping tuning, 0.4.2 memo) did not
end it: each one shortened ONE consumer or ONE queue. None separated the real-time path from the background workload, none shortened
the reconnect pause, and nothing was MEASURED per day (the feed counters `receive_frames_discarded` / `receive_max_lag_ms` are in the
snapshot only, never in an export). Every stall longer than 3 s is then converted into permanent loss by the freshness rules
(3 s feed lag, 3 s exchange stamp), which are safety rules and stay.

This overlay (failure-tolerant, vendor engine untouched, no data semantics changed):
1. FAST RECONNECT: after a connection that was healthy for >= 15 s dropped with ConnectionClosedError (1011 / no close frame) the
   next pause is 3 s instead of up to 60 s (at most 5 times per 10 minutes; never for 1000 Bye, registration rejects, DNS / REST errors).
2. BACKGROUND YIELD: while the real-time path is under pressure (feed queue >= 120, tick queue >= 24, or Python scheduling lag
   >= 350 ms) the analytics entry points wait before they start: candidate research (20 s max), history indicators, higher-frame
   repair, 1m / 5m repair (10 s / 8 s max). They always run after the maximum wait, hold nothing while waiting, and only act
   08:55-15:40 on weekdays. Strategy evaluation (MA, MarketTrend, AT, GMO) is never delayed.
3. MEASUREMENT: one line per minute in `stock/feed_health_v0450.jsonl` (queues, scheduling lag, frames discarded / processed /
   overflowed since the last line, reconnect and yield counters) so the next export shows the feed health per minute.
No order, fill, quote, candle or ledger value is created or changed.
"""
from __future__ import annotations

import asyncio as _real_asyncio
import datetime as dt
import json
import threading
import time
from pathlib import Path

VERSION = "STOCK_FEED_RESILIENCE_V0450"
FEED_Q_HIGH = 120
TICK_Q_HIGH = 24
LAG_HIGH_MS = 350.0
LAG_SAMPLES = 8
POLL_SECONDS = 0.25
MAX_WAIT = {"research": 20.0, "history": 10.0, "fix14": 10.0, "fix12": 10.0, "repair1m": 8.0}
FAST_DELAY = 3.0
FAST_MIN_UPTIME = 15.0
FAST_MAX_PER_WINDOW = 5
FAST_WINDOW = 600.0
FAST_ARM_SECONDS = 5.0
LOG_FILE = "feed_health_v0450.jsonl"
LOG_SECONDS = 60.0
LOG_MAX_LINES = 4000
KST = dt.timezone(dt.timedelta(hours=9))

_LOCK = threading.Lock()
_STATS = {"yield_calls": 0, "yield_waited_s": 0.0, "yield_by_label": {}, "fast_reconnects": 0, "reconnect_seen": 0}
_FAST = {"armed_until": 0.0, "history": [], "ok_at": None}
_STATE = {"installed": False, "thread": None, "lines": 0, "prev": {}}
_AGE_EDGES = (1000.0, 3000.0, 6000.0, 12000.0)          # tick age at the consumer: <1 s, 1-3, 3-6, 6-12, >=12 s
_AGE = {"ok": [0, 0, 0, 0, 0], "expired": [0, 0, 0, 0, 0]}


# ----------------------------------------------------------------------------------------------- pressure

def _runner():
    try:
        import android_live_bridge as bridge
        return getattr(getattr(bridge, "_RUNTIME", None), "stock_runner", None)
    except Exception:
        return None


def _lag_samples(count=LAG_SAMPLES):
    try:
        import stock_feed_diag_v0400 as diag
        with diag._LOCK:
            return list(diag._LAG)[-count:]
    except Exception:
        return []


def in_hours(now=None):
    now = now or dt.datetime.now(KST)
    return now.weekday() < 5 and "0855" <= now.strftime("%H%M") <= "1540"


def pressure(runner=None):
    """Reason text when the real-time path is lagging, else None. Never raises."""
    runner = runner if runner is not None else _runner()
    if runner is None:
        return None
    try:
        q = getattr(runner, "_feed_message_queue", None)
        if q is not None and q.qsize() >= FEED_Q_HIGH:
            return "FEED_QUEUE_%d" % q.qsize()
        tq = getattr(runner, "_tick_queue", None)
        if tq is not None and tq.qsize() >= TICK_Q_HIGH:
            return "TICK_QUEUE_%d" % tq.qsize()
        lag = _lag_samples()
        if len(lag) >= LAG_SAMPLES:
            ordered = sorted(lag)
            median = ordered[len(ordered) // 2]
            if median >= LAG_HIGH_MS:
                return "SCHEDULING_LAG_%dms" % median
    except Exception:
        return None
    return None


def wait_calm(label, now=None, sleep=time.sleep, runner=None, clock=time.monotonic):
    """Wait (bounded) until the real-time path is calm. Returns the seconds waited. Holds nothing."""
    if not in_hours(now):
        return 0.0
    limit = MAX_WAIT.get(label, 10.0)
    started = clock()
    waited = 0.0
    while waited < limit and pressure(runner) is not None:
        sleep(POLL_SECONDS)
        waited = clock() - started
    with _LOCK:
        _STATS["yield_calls"] += 1
        if waited > 0:
            _STATS["yield_waited_s"] = round(_STATS["yield_waited_s"] + waited, 2)
            by = _STATS["yield_by_label"]
            by[label] = by.get(label, 0) + 1
    return waited


def shed(label, function):
    """Wrap an analytics entry point: wait for calm first, then call it unchanged."""

    def wrapper(*args, **kwargs):
        try:
            wait_calm(label)
        except Exception:
            pass
        return function(*args, **kwargs)

    wrapper.__wrapped__ = function
    wrapper.__name__ = getattr(function, "__name__", label)
    wrapper._v0450_shed = label
    return wrapper


# ------------------------------------------------------------------------------------------ fast reconnect

def arm_fast_reconnect(error_text, now_monotonic=None):
    """Decide whether the pause after this disconnect is shortened. True when armed."""
    now = time.monotonic() if now_monotonic is None else now_monotonic
    text = str(error_text or "")
    with _LOCK:
        _STATS["reconnect_seen"] += 1
        ok_at = _FAST["ok_at"]
        if ok_at is None or now - ok_at < FAST_MIN_UPTIME:
            return False
        if "ConnectionClosedError" not in text or "1000 (OK)" in text or "등록 거부" in text:
            return False
        if "HTTPSConnectionPool" in text or "gaierror" in text or "ConnectionError" in text.replace("ConnectionClosedError", ""):
            return False
        recent = [t for t in _FAST["history"] if now - t < FAST_WINDOW]
        if len(recent) >= FAST_MAX_PER_WINDOW:
            _FAST["history"] = recent
            return False
        recent.append(now)
        _FAST["history"] = recent
        _FAST["armed_until"] = now + FAST_ARM_SECONDS
        return True


def consume_fast_delay(delay, now_monotonic=None):
    """Called with the vendor's reconnect pause; returns the (possibly shortened) pause."""
    now = time.monotonic() if now_monotonic is None else now_monotonic
    with _LOCK:
        if _FAST["armed_until"] > now and delay > FAST_DELAY:
            _FAST["armed_until"] = 0.0
            _STATS["fast_reconnects"] += 1
            return FAST_DELAY
    return delay


class _AsyncioProxy:
    """The `asyncio` module as seen by d_live: identical except that the reconnect pause may be shortened."""

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)

    async def sleep(self, delay, *args, **kwargs):
        try:
            delay = consume_fast_delay(delay)
        except Exception:
            pass
        return await object.__getattribute__(self, "_real").sleep(delay, *args, **kwargs)


def _bucket(age_ms):
    for i, edge in enumerate(_AGE_EDGES):
        if age_ms < edge:
            return i
    return len(_AGE_EDGES)


def _wrap_observation(native):
    """Count the age of every tick the consumer looks at (processed or expired): the 0.3.6 export could not tell a
    clock / server latency (every tick old) from a consumer stall (a few very old ticks)."""

    def observe(self, recv_age_ms, metrics=None, expired=False):
        try:
            _AGE["expired" if expired else "ok"][_bucket(float(recv_age_ms))] += 1
        except Exception:
            pass
        return native(self, recv_age_ms, metrics=metrics, expired=expired)

    observe._v0450 = True
    return observe


def _wrap_health(native):
    def health(self, state, details=""):
        try:
            text = str(state)
            if text == "실시간 연결됨":
                with _LOCK:
                    _FAST["ok_at"] = time.monotonic()
            elif text.startswith("연결 끊김"):
                arm_fast_reconnect(details)
        except Exception:
            pass
        return native(self, state, details)

    health._v0450 = True
    return health


# ------------------------------------------------------------------------------------------- measurement

def _counters(runner):
    out = {}
    for key, attr in (("processed", "_feed_processed"), ("discarded", "_feed_discarded"), ("overflows", "_feed_overflows"),
                      ("slow_ticks", "_slow_ticks")):
        try:
            out[key] = int(getattr(runner, attr, 0) or 0)
        except Exception:
            out[key] = 0
    return out


def sample(runner, now=None):
    """One health line (dict). Counters are deltas since the previous sample."""
    now = now or dt.datetime.now(KST)
    current = _counters(runner) if runner is not None else {}
    with _LOCK:
        previous = _STATE["prev"]
        delta = {k: max(0, v - previous.get(k, 0)) if k in previous else 0 for k, v in current.items()}
        _STATE["prev"] = current
        stats = {"yield_calls": _STATS["yield_calls"], "yield_waited_s": _STATS["yield_waited_s"],
                 "yield_by_label": dict(_STATS["yield_by_label"]), "fast_reconnects": _STATS["fast_reconnects"],
                 "disconnects_seen": _STATS["reconnect_seen"]}
    lag = _lag_samples(480)
    row = {"at": now.isoformat(timespec="seconds"), "delta": delta, "pressure": pressure(runner) if runner is not None else None,
           "lag_ms": {"max": round(max(lag), 1) if lag else None, "p95": round(sorted(lag)[int(len(lag) * .95)], 1) if lag else None},
           "confirmed": bool(getattr(runner, "_confirmed_ws_session", False)) if runner is not None else None}
    try:
        row["feed_q"] = runner._feed_message_queue.qsize()
        row["tick_q"] = runner._tick_queue.qsize()
        row["high_water"] = int(getattr(runner, "_feed_high_water", 0) or 0)
    except Exception:
        pass
    row.update(stats)
    with _LOCK:
        row["tick_age_hist"] = {"edges_ms": list(_AGE_EDGES), "ok": list(_AGE["ok"]), "expired": list(_AGE["expired"])}
        _AGE["ok"] = [0] * 5
        _AGE["expired"] = [0] * 5
    row["open_buys"] = open_buys(runner, now)
    return row


def open_buys(runner, now, limit=5):
    """Supply-demand (GMO) BUY orders that are still not completely filled: why a symbol did not reach its limit.

    {code, requested, filled, age_min, book_age_s}. `book_age_s` is the age of the last valid 0D book the strategy holds
    for the code (None = never had one): a book older than 3 s forces the 10 % print proxy, so the fill stays tiny.
    """
    out = []
    try:
        gmo = getattr(runner, "gmo", None)
        orders = list(getattr(getattr(gmo, "engine", None), "orders", {}).values())
        books = dict(getattr(gmo, "last_book", {}) or {})
        for order in orders:
            if getattr(order, "side", "") != "BUY" or getattr(order, "state", "") in ("FILLED", "CANCELED"):
                continue
            requested, filled = int(getattr(order, "requested", 0) or 0), int(getattr(order, "filled", 0) or 0)
            if filled >= requested:
                continue
            created = dt.datetime.fromisoformat(str(getattr(order, "time", "")))
            book = books.get(getattr(order, "code", ""))
            out.append({"code": order.code, "requested": requested, "filled": filled,
                        "age_min": round((now - created).total_seconds() / 60.0, 1),
                        "book_age_s": round((now - book[4]).total_seconds(), 1) if book else None})
    except Exception:
        return out[:limit]
    return out[:limit]


def _write_line(row, root=None):
    try:
        if root is None:
            import android_live_bridge as bridge
            root = getattr(getattr(bridge, "_RUNTIME", None), "root", None)
        if root is None or _STATE["lines"] >= LOG_MAX_LINES:
            return False
        with (Path(root) / "stock" / LOG_FILE).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        _STATE["lines"] += 1
        return True
    except Exception:
        return False


def _log_loop():
    time.sleep(60.0)
    while True:
        try:
            runner = _runner()
            if runner is not None and in_hours_wide():
                _write_line(sample(runner))
        except Exception:
            pass
        time.sleep(LOG_SECONDS)


def in_hours_wide(now=None):
    now = now or dt.datetime.now(KST)
    return now.weekday() < 5 and "0750" <= now.strftime("%H%M") <= "2010"


# --------------------------------------------------------------------------------------------------- install

def install(d_live=None, threads=True):
    """Wrap the analytics entry points, the health callback and the reconnect pause. Idempotent."""
    if _STATE["installed"]:
        return
    if d_live is None:
        import d_live
    runner_type = d_live.LivePaperRunner
    runner_type._health = _wrap_health(runner_type._health)
    if hasattr(runner_type, "_report_pipeline_observation") and not getattr(runner_type._report_pipeline_observation, "_v0450", False):
        runner_type._report_pipeline_observation = _wrap_observation(runner_type._report_pipeline_observation)
    if not isinstance(getattr(d_live, "asyncio", None), _AsyncioProxy):
        d_live.asyncio = _AsyncioProxy(getattr(d_live, "asyncio", _real_asyncio))
    if hasattr(runner_type, "_process_completed_records"):
        runner_type._process_completed_records = shed("history", runner_type._process_completed_records)
    for module_name, attribute, label in (("d_candidate_history", "observe_live_batch", "research"),
                                          ("d_minute_repair_fast", "store_official_one_minute", "repair1m"),
                                          ("d_multiframe_recovery_fix14", "recover_frame", "fix14"),
                                          ("d_gap_recovery_fix12", "repair_missing_five_minute", "fix12")):
        try:
            module = __import__(module_name)
            function = getattr(module, attribute, None)
            if function is not None and not getattr(function, "_v0450_shed", None):
                setattr(module, attribute, shed(label, function))
        except Exception:
            continue
    if threads and _STATE["thread"] is None:
        thread = threading.Thread(target=_log_loop, name="ARTITRADE_V0450_FEED_HEALTH", daemon=True)
        _STATE["thread"] = thread
        thread.start()
    _STATE["installed"] = True
