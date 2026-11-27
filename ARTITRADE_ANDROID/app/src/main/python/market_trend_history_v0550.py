"""0.5.5 (owner 2026-11-27, "이력 적재 단계 보완"): stock MarketTrend history is filled from the LOCAL bars before the open and the broker is asked
again only when a bar is really missing, at a bounded rate.

Evidence (real device data 2026-11-23..26, see docs/artitrade/MT_HISTORY_V0550.md): in the stock MarketTrend decision log every watch was blocked by
`OPERATIONAL_WATCH_HISTORY_MISSING` / `OPERATIONAL_HISTORY_ConnectionError`; the app never bought a stock through MarketTrend although RSI watches armed.

Root cause chain (CONFIRMED in the vendor code `market_trend_runtime_v043.Coordinator`):
1. `_raw_history` returns the local bars only when the newest local bar is at most 3 frames old. At the open the newest local bar is the PREVIOUS session's
   last bar (17+ hours old), so the test fails for EVERY symbol x frame and the code asks the broker (ka10080 / ka10081, several pages each) for all of them
   in the first minutes of the session, at the same moment the live feed, the book and the repairs are busiest.
2. `_prefetch` walks the pairs one by one with a 0.35 s sleep, so even a correct local read needed minutes to fill ~600 keys.
3. When the broker call fails (the phone showed DNS failures and 1011 keep-alive drops on 2026-11-23..26) the key stays empty and `_watch_loop` records
   `OPERATIONAL_WATCH_HISTORY_MISSING` for the rest of the window.

Fix (read-only for the ledger, no order / fill / signal is created here):
* `local_is_current`: the previous session's last bar IS current until the first bar of today can have completed (30m 09:30, 60m 10:00, day close).
* `_raw_history` (stock): local bars first; the broker only when the local series is genuinely short or stale, never more often than one call per
  `REST_MIN_GAP` seconds in total and once per `REST_KEY_COOLDOWN` seconds per symbol/frame; a throttled call returns the local bars (fail closed: the
  vendor `_closed_series` contiguity test still refuses an entry on a hole).
* `preload`: a background pass copies the local bars into `Coordinator._history` for every candidate / held pair that is empty, in one burst, as soon as
  the Coordinator has candidates (pre-open included). It never overwrites a longer / fresher series.
The entry rules (RSI20 <= 30 watch, first completed bar above the Bollinger(20, 1.5) middle, slope inside (-15, +20) degrees) are NOT changed.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

VERSION = "MT_HISTORY_V0550"
LOCAL_MIN_BARS = 25            # same threshold the vendor used for "enough local bars"
MIN_BARS = 21                  # RSI20 needs 21 closes
REST_MIN_GAP = 1.0             # seconds between two stock history broker calls (all symbols together)
REST_KEY_COOLDOWN = 120.0      # seconds before the same symbol/frame may ask the broker again
PRELOAD_PERIOD = 30.0
STATE_FILE = "mt_history_v0550.json"

_STATE = {"installed": False, "lock": threading.Lock(), "last_rest": 0.0, "key_rest": {}, "preload_threads": set(),
          "stats": {"local_served": 0, "carry_over_served": 0, "rest_allowed": 0, "rest_throttled": 0, "preloaded": 0,
                    "preload_passes": 0}}


def _mt():
    import market_trend_v043 as mt
    return mt


def _now():
    return dt.datetime.now(_mt().KST)


def _opening_closing(day):
    from krx_session_v041 import END, SPECIAL_SESSIONS, START
    return SPECIAL_SESSIONS.get(day.isoformat(), (START, END))


def previous_trading_day(day):
    mt = _mt()
    probe = day
    for _ in range(14):
        probe = probe - dt.timedelta(days=1)
        if mt._krx_day(probe):
            return probe
    return day - dt.timedelta(days=1)


def local_is_current(newest_end, now, frame):
    """True when the newest local completed bar is the newest one that can exist."""
    mt = _mt()
    secs = mt.FRAME_SECS[frame]
    if (now - newest_end).total_seconds() <= 3 * secs:          # the vendor rule
        return True
    day = now.date()
    if not mt._krx_day(day):
        return False
    opening, closing = _opening_closing(day)
    start = now.replace(hour=opening // 60, minute=opening % 60, second=0, microsecond=0)
    close = now.replace(hour=closing // 60, minute=closing % 60, second=0, microsecond=0)
    first_end = close if frame == "day" else min(start + dt.timedelta(seconds=secs), close)
    if now < first_end:                                          # today's first bar is not complete yet
        return newest_end.date() >= previous_trading_day(day)
    return False


def local_rows(co, code, frame, now):
    """Completed local KRX bars of one pair, oldest first (same conversion as the vendor `_raw_history`)."""
    import market_trend_runtime_v043 as runtime
    mt = _mt()
    runner = co.rt.stock_runner
    if runner is None:
        return []
    with runner.store.connect() as c:
        rows = c.execute("SELECT ts,close,high,low FROM bars WHERE code=? AND frame=? ORDER BY ts DESC LIMIT 90", (code, frame)).fetchall()
    converted = {}
    for row in rows:
        try:
            stamp = str(row["ts"])
            end = runtime._stock_bar_end(stamp, frame)
            if end is None or end > now:
                continue
            converted[stamp] = {"start": end - dt.timedelta(seconds=mt.FRAME_SECS[frame]), "end": end, "close": float(row["close"]),
                                "high": float(row["high"]), "low": float(row["low"]), "source": "LOCAL_KRX_VERIFIED"}
        except (ValueError, TypeError, KeyError):
            continue
    return sorted(converted.values(), key=lambda x: x["end"])


def rest_allowed(key, now_mono=None):
    """One broker history call at a time per second overall and one per key per cooldown."""
    now_mono = time.monotonic() if now_mono is None else now_mono
    with _STATE["lock"]:
        if now_mono - _STATE["last_rest"] < REST_MIN_GAP:
            _STATE["stats"]["rest_throttled"] += 1
            return False
        if now_mono - _STATE["key_rest"].get(key, -1e9) < REST_KEY_COOLDOWN:
            _STATE["stats"]["rest_throttled"] += 1
            return False
        _STATE["last_rest"] = now_mono
        _STATE["key_rest"][key] = now_mono
        if len(_STATE["key_rest"]) > 4000:
            _STATE["key_rest"].clear()
        _STATE["stats"]["rest_allowed"] += 1
        return True


def make_raw_history(original):
    def _raw_history(self, market, code, frame):
        if market != "stock":
            return original(self, market, code, frame)
        runner = self.rt.stock_runner
        if runner is None or runner.api is None:
            return []
        now = _now()
        try:
            local = local_rows(self, code, frame, now)
        except Exception:
            return original(self, market, code, frame)
        key = (market, code, frame)
        if len(local) >= LOCAL_MIN_BARS:
            from krx_session_v041 import is_regular_open
            newest = local[-1]["end"]
            if not is_regular_open() or local_is_current(newest, now, frame):
                _STATE["stats"]["local_served"] += 1
                if (now - newest).total_seconds() > 3 * _mt().FRAME_SECS[frame]:
                    _STATE["stats"]["carry_over_served"] += 1
                return local[-80:]
        # Genuinely short or stale during the session: ask the broker, but never in a burst.
        if not rest_allowed(key):
            return local[-80:]
        return original(self, market, code, frame)
    _raw_history.__wrapped_v0550__ = original
    return _raw_history


def preload(co, now=None):
    """Copy local bars into the empty history slots of every candidate / held pair. Returns the number of slots filled."""
    from new_legacy_1118_policy import TREND_FRAMES
    if getattr(co.rt, "stock_runner", None) is None:
        return 0
    now = now or _now()
    with co.lock:
        universe = list(co._candidates.get("stock", {}))
        held = [(p.symbol, p.frame) for p in co._positions.values() if p.market == "stock"]
    pairs = list(dict.fromkeys(held + [(code, frame) for code in universe for frame in TREND_FRAMES]))
    filled = 0
    for code, frame in pairs:
        if co.stop_event.is_set():
            break
        key = ("stock", code, frame)
        with co.lock:
            have = co._history.get(key, ())
            if len(have) >= MIN_BARS:
                continue
        try:
            rows = local_rows(co, code, frame, now)
        except Exception:
            continue
        if len(rows) < MIN_BARS:
            continue
        with co.lock:
            have = co._history.get(key, ())
            if len(have) >= MIN_BARS and have[-1]["end"] >= rows[-1]["end"]:
                continue
            co._history[key] = rows[-80:]
        filled += 1
    with _STATE["lock"]:
        _STATE["stats"]["preloaded"] += filled
        _STATE["stats"]["preload_passes"] += 1
    return filled


def _write_state(co):
    try:
        path = Path(co.rt.root) / "stock" / STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": VERSION, "at": dt.datetime.now(_mt().KST).isoformat(timespec="seconds"), **_STATE["stats"]}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _preload_loop(co):
    while not co.stop_event.is_set():
        try:
            preload(co)
            _write_state(co)
        except Exception:
            pass
        co.stop_event.wait(PRELOAD_PERIOD)


def install():
    if _STATE["installed"]:
        return
    import market_trend_runtime_v043 as runtime
    runtime.Coordinator._raw_history = make_raw_history(runtime.Coordinator._raw_history)
    native_start = runtime.Coordinator.start

    def start(self, *a, **k):
        result = native_start(self, *a, **k)
        if id(self) not in _STATE["preload_threads"]:
            _STATE["preload_threads"].add(id(self))
            threading.Thread(target=_preload_loop, args=(self,), name="ARTITRADE_V0550_MT_HISTORY_PRELOAD", daemon=True).start()
        return result
    runtime.Coordinator.start = start
    _STATE["installed"] = True
