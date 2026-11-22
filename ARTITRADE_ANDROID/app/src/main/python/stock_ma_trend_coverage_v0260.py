"""0.2.6: stock MA and stock MarketTrend could not reach their BUY decision.

Root cause (code, same mechanism as #118 close-betting; device prevalence
UNVERIFIED because the 11-20 resets erased the weekday decision rows):

* Both strategies decide only on a genuine KRX 0B print that is <= 3 s old
  (MA: 10..2 s before a bar closes; MarketTrend: every step). Since 0.0.20
  the stock Trend candidates (the whole AT universe) have NO band of their
  own in the shared 200-slot Kiwoom socket and MA candidates exist only after
  a signal, so both ride the rotating whole-market slice (rotated every 180 s).
  A symbol that is not subscribed at its decision moment can never buy.
* MarketTrend starts its 24 h RSI20<=30 watch only from a fresh live print
  (forming RSI). If the dip happened while the symbol was rotated out, the
  watch never started and that cycle could never be bought. The completed
  bar RSI path (v107 _preclose_candidates) was replaced by the 11.18 rule and
  its fast just-closed-bar refresh went with it.

Fix (no rule threshold, quote freshness or wallet authority is relaxed):
1. NearSignalWorker computes, from completed bars only, the symbols that can
   trigger soon: MA (the forming price band that would make MA5/10/20/60 turn
   up below a falling MA120) and MarketTrend (an active 24 h watch, or
   RSI20<=35). Those are pinned in the Kiwoom subscription (max 40 + 40).
2. MarketTrend (stock): a COMPLETED bar with RSI20<=30 also starts the 24 h
   watch, once per closed bar, with the same evidence fields. The live-print
   path is unchanged. Near symbols get the fast closed-bar refresh again.
Every BUY still needs a fresh real 0B/0D, the unchanged rule and the wallet.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import threading
import time

KST = dt.timezone(dt.timedelta(hours=9))
MA_FRAMES = ("5m", "30m", "60m")
MA_TOLERANCE_PCT = {"5m": 1.0, "30m": 1.5, "60m": 2.0}
MA_CAP = 40
TREND_CAP = 40
TREND_NEAR_RSI = 35.0
REFRESH_SECONDS = 15
FRAME_SECS = {"5m": 300, "30m": 1800, "60m": 3600}

_LOCK = threading.Lock()
_NEAR = {"ma": [], "trend": [], "at": None, "counts": {}}
_WORKER = None
_INSTALLED = False


# ---------------------------------------------------------------- pure rules
def _qualifies(closes, i):
    """MA_V1118 REVERSAL_UP alignment at index i (same as ma_v067_policy)."""
    if i < 120:
        return False
    def sma(n, j):
        return sum(closes[j - n + 1:j + 1]) / n
    m120 = sma(120, i)
    if not (m120 - sma(120, i - 1) < 0):
        return False
    for n in (5, 10, 20, 60):
        v = sma(n, i)
        if not (v - sma(n, i - 1) > 0 and v < m120):
            return False
    return True


def ma_trigger_band(closes):
    """Forming-price interval (lo, hi) that would create a NEW REVERSAL_UP.

    closes: completed closes, oldest first (>= 121). With forming price p:
    SMA_k rises iff p > c[n-k]; SMA120 falls iff p < c[n-120];
    SMA_k(cur) < SMA120(cur) is linear in p. Returns None if impossible or
    the alignment already existed on the last completed bar.
    """
    c = [float(x) for x in closes]
    n = len(c)
    if n < 121 or any(x <= 0 for x in c):
        return None
    if _qualifies(c, n - 1):
        return None  # UPWARD_ALIGNMENT_ALREADY_EXISTED -> no new transition
    lo = max(c[n - 5], c[n - 10], c[n - 20], c[n - 60])
    hi = c[n - 120]
    a120 = sum(c[n - 119:n])
    for k in (5, 10, 20, 60):
        ak = sum(c[n - k + 1:n])
        coef = 1.0 / k - 1.0 / 120
        bound = (a120 / 120 - ak / k) / coef
        hi = min(hi, bound)
    if not lo < hi:
        return None
    return lo, hi


def ma_distance_pct(closes, price):
    """0 inside the band, % distance outside it, None if impossible."""
    band = ma_trigger_band(closes)
    if band is None or price is None or price <= 0:
        return None
    lo, hi = band
    if lo < price < hi:
        return 0.0
    if price <= lo:
        return (lo - price) / price * 100.0
    return (price - hi) / price * 100.0


# ------------------------------------------------------------------ helpers
def _runtime():
    import android_live_bridge as core
    return getattr(core, "_RUNTIME", None)


def _last_price(trend, code):
    try:
        with trend.lock:
            raw = trend._quotes.get("stock", {}).get(code)
        if raw:
            return float(raw[0])
    except Exception:
        pass
    return None


def compute_ma_near(runner, trend, now):
    from ma_v061_stock_runtime import _slope_closes
    universe = list((getattr(runner, "krx_market_map", None) or
                     getattr(runner, "market_map", None) or {}).keys())[:400]
    scored = []
    for code in universe:
        best = None
        for frame in MA_FRAMES:
            closes = _slope_closes(runner.ma, code, frame, now)
            if len(closes) < 121:
                continue
            price = _last_price(trend, code) if trend is not None else None
            if price is None:
                price = float(closes[-1])
            gap = ma_distance_pct(closes, price)
            if gap is None or gap > MA_TOLERANCE_PCT[frame]:
                continue
            best = gap if best is None else min(best, gap)
        if best is not None:
            scored.append((best, str(code)))
    scored.sort()
    return [code for _, code in scored[:MA_CAP]]


def compute_trend_near(trend, now):
    import market_trend_v043 as mt
    from new_legacy_1118_policy import TREND_FRAMES
    with trend.lock:
        candidates = list(trend._candidates.get("stock", {}))
        histories = {k: list(v) for k, v in trend._history.items()
                     if k[0] == "stock" and k[2] in TREND_FRAMES}
    watches = getattr(trend, "_nl1118_watches", {}) or {}
    watching, scored = [], []
    for code in candidates:
        rsi_best = None
        for frame in TREND_FRAMES:
            w = watches.get(("stock", code, frame))
            if w and w.get("status") == "WATCHING":
                try:
                    if now < dt.datetime.fromisoformat(w["expires_at"]):
                        watching.append(code)
                except (KeyError, TypeError, ValueError):
                    pass
            rows = histories.get(("stock", code, frame), ())
            closes = [float(r["close"]) for r in rows]
            price = _last_price(trend, code)
            rsi = mt.rsi20(closes, price) if price else mt.rsi20(closes)
            if rsi is not None:
                rsi_best = rsi if rsi_best is None else min(rsi_best, rsi)
        if rsi_best is not None and rsi_best <= TREND_NEAR_RSI:
            scored.append((rsi_best, code))
    scored.sort()
    out = list(dict.fromkeys(watching))
    for _, code in scored:
        if code not in out:
            out.append(code)
    return out[:TREND_CAP]


def near_codes():
    with _LOCK:
        return list(_NEAR["ma"]), list(_NEAR["trend"])


def snapshot():
    with _LOCK:
        return {"ma": len(_NEAR["ma"]), "trend": len(_NEAR["trend"]),
                "at": _NEAR["at"], "counts": dict(_NEAR["counts"])}


def _session_active(now):
    from krx_session_v041 import SPECIAL_SESSIONS, START, END
    if now.weekday() >= 5:
        return False
    opening, closing = SPECIAL_SESSIONS.get(now.date().isoformat(), (START, END))
    minute = now.hour * 60 + now.minute
    return opening - 10 <= minute < closing


def refresh_once(now=None):
    now = now or dt.datetime.now(KST)
    rt = _runtime()
    runner = getattr(rt, "stock_runner", None) if rt is not None else None
    trend = getattr(rt, "market_trend", None) if rt is not None else None
    ma, tr = [], []
    errors = {}
    if runner is not None:
        try:
            ma = compute_ma_near(runner, trend, now)
        except Exception as exc:
            errors["ma"] = type(exc).__name__
    if trend is not None:
        try:
            tr = compute_trend_near(trend, now)
        except Exception as exc:
            errors["trend"] = type(exc).__name__
    with _LOCK:
        _NEAR["ma"], _NEAR["trend"] = ma, tr
        _NEAR["at"] = now.isoformat()
        _NEAR["counts"] = {"ma_near": len(ma), "trend_near": len(tr), **errors}
    return ma, tr


def _worker_loop():
    last_audit = 0.0
    while True:
        try:
            now = dt.datetime.now(KST)
            if _session_active(now):
                ma, tr = refresh_once(now)
                if time.monotonic() - last_audit >= 300:
                    last_audit = time.monotonic()
                    rt = _runtime()
                    co = getattr(rt, "market_trend", None) if rt is not None else None
                    if co is not None:
                        co._record_entry_block("stock", "__COVERAGE__", "all",
                            now.isoformat(timespec="minutes"), "V0260_NEAR_SIGNAL_PINNED",
                            strategy="coverage", ma_details=snapshot())
            else:
                with _LOCK:
                    _NEAR["ma"], _NEAR["trend"] = [], []
        except Exception:
            pass
        time.sleep(REFRESH_SECONDS)


def start_worker():
    global _WORKER
    if _WORKER is not None and _WORKER.is_alive():
        return _WORKER
    _WORKER = threading.Thread(target=_worker_loop, daemon=True,
                               name="artitrade-stock-near-signal-v0260")
    _WORKER.start()
    return _WORKER


# ------------------------------------------- MarketTrend closed-bar arming
def arm_from_closed_bars(co, market, now):
    """Stock only: a COMPLETED bar RSI20<=30 starts the 24 h watch.

    Mirrors the live-print branch of market_trend_1118_runtime._entry_window
    (same _mark_rsi_watch, same evidence keys); once per closed bar.
    """
    if market != "stock" or not hasattr(co, "_nl1118_watch_db"):
        return 0
    import market_trend_v043 as mt
    from market_trend_1118_runtime import _confirmed_recent, _mark_rsi_watch
    from new_legacy_1118_policy import TREND_FRAMES
    seen = co.__dict__.setdefault("_v0260_closed_armed", set())
    with co.lock:
        candidates = list(co._candidates.get(market, {}))
        histories = {k: list(v) for k, v in co._history.items()
                     if k[0] == market and k[2] in TREND_FRAMES}
    armed = 0
    for code in candidates:
        for frame in TREND_FRAMES:
            k = (market, code, frame)
            closed = _confirmed_recent(histories.get(k, ()), now, market, frame)
            if not closed:
                continue
            marker = (code, frame, closed[-1]["end"].isoformat())
            if marker in seen:
                continue
            seen.add(marker)
            closes = [float(row["close"]) for row in closed]
            rsi = mt.rsi20(closes)
            if rsi is None:
                continue
            old = co._nl1118_watches.get(k)
            if old is None and rsi > 30:
                continue
            proof = None
            if (rsi <= 30 and (old is None or old["status"] == "READY" or
                    (old["status"] == "WATCHING" and not old.get("evidence")))):
                completed = closed[-21:]
                payload = "|".join(row["end"].isoformat() + ":" + repr(float(row["close"]))
                                   for row in completed)
                proof = {"market": market, "symbol": code, "frame": frame,
                         "observed_at": now.isoformat(), "rsi20": float(rsi),
                         "verified_trade": None, "basis": "COMPLETED_BAR_RSI20_V0260",
                         "history_end": closed[-1]["end"].isoformat(),
                         "source": closed[-1].get("source"),
                         "closed21_sha256": hashlib.sha256(payload.encode("utf8")).hexdigest()}
            if old is None or old["status"] in ("WATCHING", "EXPIRED", "READY"):
                before = (old or {}).get("observed_at")
                row = _mark_rsi_watch(co, market, k, old, rsi, now, evidence=proof)
                if row and row.get("status") == "WATCHING" and row.get("observed_at") != before:
                    armed += 1
                    co._record_entry_block(market, code, frame,
                        closed[-1]["end"].isoformat(), "RSI30_COMPLETED_BAR_WATCH_STARTED_V0260",
                        rsi, strategy="trend")
    if len(seen) > 20000:
        seen.clear()
    return armed


def refresh_near_closed_bars(co, market, now):
    """Restore the fast just-closed bar read for near / watching symbols."""
    if market != "stock":
        return
    from market_trend_runtime_v043 import _end_clock
    _, near_trend = near_codes()
    if not near_trend:
        return
    from new_legacy_1118_policy import TREND_FRAMES
    from krx_session_v041 import SPECIAL_SESSIONS, START
    opening = SPECIAL_SESSIONS.get(now.date().isoformat(), (START, 0))[0]
    first = now.replace(hour=opening // 60, minute=opening % 60, second=0, microsecond=0)
    for frame in TREND_FRAMES:
        if frame == "day":
            continue
        if _end_clock(now, market, frame) is None:
            continue
        width = FRAME_SECS[frame]
        elapsed = (now - first).total_seconds()
        if elapsed < width:
            continue
        begins = first + dt.timedelta(seconds=int(elapsed // width) * width)
        if (now - begins).total_seconds() > 120:
            continue  # only right after a bar boundary
        for code in near_trend:
            co._refresh_due_closed_local_v108(market, code, frame, begins)


def install(core=None):
    global _INSTALLED
    if _INSTALLED:
        return
    import market_trend_runtime_v043 as runtime
    native_entry = runtime.Coordinator._preclose_candidates

    def entry_with_closed_arming(self, market, now):
        result = native_entry(self, market, now)
        if market == "stock":
            clock = time.monotonic()
            marks = self.__dict__.setdefault("_v0260_clock", {"refresh": 0.0, "arm": 0.0})
            if clock - marks["refresh"] >= 2.0:
                marks["refresh"] = clock
                try:
                    refresh_near_closed_bars(self, market, now)
                except Exception as exc:
                    self._last_fetch_error[market] = "V0260_CLOSED_REFRESH:" + type(exc).__name__
            if clock - marks["arm"] >= 5.0:
                marks["arm"] = clock
                try:
                    arm_from_closed_bars(self, market, now)
                except Exception as exc:
                    self._last_fetch_error[market] = "V0260_CLOSED_ARM:" + type(exc).__name__
        return result

    runtime.Coordinator._preclose_candidates = entry_with_closed_arming
    start_worker()
    _INSTALLED = True
