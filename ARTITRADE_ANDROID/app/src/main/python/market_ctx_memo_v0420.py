"""0.4.2 (issue #157): stop recomputing the market-wide daily statistics under the SQLite ledger gate.

Evidence (CONFIRMED by profiling the owner's real stock database from the 2026-11-27 10:27 export on a desktop CPU, so the
absolute times are roughly 5-10x smaller than on the phone):
* `recover_frame` (thread `artitrade-fix14-higher-frame-rest`) calls `PaperEngine.discover(..., only_codes=(code,))`
  for ONE symbol and ONE frame. `discover` runs `market_state_for_session` (INTEGRATED 1.0, 6 prior daily sessions) and
  `market_context_v11` (INTEGRATED 1.1, 21 prior daily sessions) - both read every stock's daily closes of the whole market -
  while holding the ledger gate. Their result depends only on (market, signal day) and on PRIOR closed daily bars, so it is the
  same for every symbol and frame; it was recomputed for each of them. In the profile 0.25 s of the 0.27 s `discover` call
  (93 %) was these two functions; the measured gate holds were 250 ms for those calls and about 2 ms for the others.
* The same `market_context_v11` is called again for every pending entry review in the activation path (research-only audit).
* The tick pipeline metrics of the same export show the consequence: the gate is the bottleneck for the live ticks
  (priority wait up to 18 s, 31 % of ticks expired, median receive age 5 s) and the close-betting book is rejected with
  NO_FRESH_ORDERBOOK (76 of 77 evaluations).

Fix (overlay, vendor engine file unchanged, engine SHA unchanged): the two functions are memoised per (function, market,
day). The value is identical to the original (a deep copy of its result); they are research / warning values that are not an
order gate. Rules that keep it exact:
* a result is reused for 15 minutes when it is a successful assessment, for 20 seconds when it is not (data still arriving);
* the cache is bypassed for the whole `recover_frame` call of the DAILY frame (it may insert prior-day bars) and cleared after it;
* a background thread refreshes today's two markets every 2 minutes on its OWN read-only SQLite connection (no ledger gate),
  so the gate-holding callers normally only find a finished value;
* any error falls back to the original function; invalid inputs are never cached.
No order, fill, quote, candle or cash value is created or changed.
"""
from __future__ import annotations

import copy
import sqlite3
import threading
import time

VERSION = "MARKET_CTX_MEMO_V0420"
TTL_GOOD = 900.0
TTL_BAD = 20.0
WARM_SECONDS = 120.0
MAX_ENTRIES = 64
_MARKETS = ("KOSPI", "KOSDAQ")

_LOCK = threading.Lock()
_CACHE = {}                 # (name, market, day) -> (expires_monotonic, value)
_STATS = {"hit": 0, "miss": 0, "bypass": 0, "warm": 0, "warm_error": 0}
_DB_PATH = [None]
_STATE = {"installed": False, "thread": None}
_LOCAL = threading.local()
_ORIG = {}


def _key(name, market, signal_ts):
    market = str(market or "")
    day = str(signal_ts or "")[:8]
    if market not in _MARKETS or len(day) != 8 or not day.isdigit():
        return None
    return (name, market, day)


def _good(name, value):
    try:
        return isinstance(value, dict) and value.get("status") == "ASSESSED"
    except Exception:
        return False


def stats():
    with _LOCK:
        return dict(_STATS, entries=len(_CACHE))


def clear():
    with _LOCK:
        _CACHE.clear()


def _remember_path(db):
    if _DB_PATH[0] is not None:
        return
    try:
        for row in db.execute("PRAGMA database_list").fetchall():
            path = row[2] if not hasattr(row, "keys") else row["file"]
            if path:
                _DB_PATH[0] = str(path)
                return
    except Exception:
        pass


def _store(name, key, value):
    ttl = TTL_GOOD if _good(name, value) else TTL_BAD
    with _LOCK:
        if len(_CACHE) >= MAX_ENTRIES:
            now = time.monotonic()
            for old in [k for k, (until, _v) in _CACHE.items() if until <= now]:
                _CACHE.pop(old, None)
            while len(_CACHE) >= MAX_ENTRIES:
                _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[key] = (time.monotonic() + ttl, copy.deepcopy(value))


def memoised(name, original):
    """Wrap `original(db, market, signal_ts)`; same signature, same result."""

    def wrapper(db, market, signal_ts, *args, **kwargs):
        key = _key(name, market, signal_ts)
        if key is None or args or kwargs or getattr(_LOCAL, "bypass", 0):
            with _LOCK:
                _STATS["bypass"] += 1
            return original(db, market, signal_ts, *args, **kwargs)
        _remember_path(db)
        now = time.monotonic()
        with _LOCK:
            hit = _CACHE.get(key)
            if hit is not None and hit[0] > now:
                _STATS["hit"] += 1
                return copy.deepcopy(hit[1])
            _STATS["miss"] += 1
        value = original(db, market, signal_ts)
        try:
            _store(name, key, value)
        except Exception:
            pass
        return value

    wrapper.__name__ = getattr(original, "__name__", name)
    wrapper.__wrapped__ = original
    return wrapper


def _wrap_recover(original):
    def recover_frame(store, engine, client, code, tf, *a, **kw):
        if tf != "day":
            return original(store, engine, client, code, tf, *a, **kw)
        # The daily frame may insert prior-day bars that these statistics read: stay exact for this call.
        _LOCAL.bypass = getattr(_LOCAL, "bypass", 0) + 1
        try:
            return original(store, engine, client, code, tf, *a, **kw)
        finally:
            _LOCAL.bypass -= 1
            clear()

    recover_frame.__wrapped__ = original
    return recover_frame


def warm_once(now_day=None):
    """Refresh today's two markets on a private read-only connection (no ledger gate)."""
    path = _DB_PATH[0]
    if not path or not _ORIG:
        return 0
    if now_day is None:
        try:
            from d_storage import now_kst
            now_day = now_kst().strftime("%Y%m%d")
        except Exception:
            now_day = time.strftime("%Y%m%d")
    done = 0
    cx = None
    try:
        cx = sqlite3.connect("file:" + path + "?mode=ro", uri=True, timeout=5)
        cx.row_factory = sqlite3.Row
        for name, original in _ORIG.items():
            for market in _MARKETS:
                key = _key(name, market, now_day)
                try:
                    value = original(cx, market, now_day + "000000")
                    _store(name, key, value)
                    done += 1
                except Exception:
                    with _LOCK:
                        _STATS["warm_error"] += 1
        with _LOCK:
            _STATS["warm"] += done
    except Exception:
        with _LOCK:
            _STATS["warm_error"] += 1
    finally:
        try:
            if cx is not None:
                cx.close()
        except Exception:
            pass
    return done


def _warm_loop():
    time.sleep(30.0)
    while True:
        try:
            warm_once()
        except Exception:
            pass
        time.sleep(WARM_SECONDS)


def install(d_engine=None, fix14=None, threads=True):
    """Wrap the two functions in the engine module namespace and the daily-frame recovery. Idempotent."""
    if _STATE["installed"]:
        return
    if d_engine is None:
        import d_engine
    if fix14 is None:
        try:
            import d_multiframe_recovery_fix14 as fix14
        except Exception:
            fix14 = None
    for name in ("market_state_for_session", "market_context_v11"):
        original = getattr(d_engine, name)
        _ORIG[name] = original
        setattr(d_engine, name, memoised(name, original))
    if fix14 is not None and hasattr(fix14, "recover_frame"):
        fix14.recover_frame = _wrap_recover(fix14.recover_frame)
    if threads and _STATE["thread"] is None:
        thread = threading.Thread(target=_warm_loop, name="ARTITRADE_V0420_MARKET_CTX_WARM", daemon=True)
        _STATE["thread"] = thread
        thread.start()
    _STATE["installed"] = True
