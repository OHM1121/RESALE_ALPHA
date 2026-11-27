"""0.4.3 (#158): owner workbook 2026-11-27 (pink) - coin buy blocks and the BUY-time trend / market-score record.

Owner rules (pink text in ARTITRADE_원장_전략별_거래_20261127.xlsx):
* coin reference bar: no buy when the market score is +40 or more; no buy when the score is negative while the symbol's
  trend is UP (this removes the former "UP lifts the positive-score condition" exception for coins only);
* coin MA: no buy of a symbol listed less than 3 months ago; no buy at score +40 or more;
* coin MarketTrend: no buy at score +40 or more (applied to coin; the stock sheet had no text);
* "trend unverified" must be fixed; the market score at the BUY must always be recorded (stock reference bar showed
  "score unverified").

Root cause of "trend unverified (no bar data)": the symbol's regime / MA slopes were read ONLY from bars the app happened to hold
(the MA scanner cache, the MarketTrend history). A reference-bar or MarketTrend coin buy often has none, and the regime silently
fell back to the SIDEWAYS table. Fix: when those sources hold fewer than 65 completed bars, the completed candles before the BUY
are read once per (symbol, frame, bar) from the public Upbit candle endpoint (read-only; never a quote for an order). The same
endpoint (daily candles) gives the listing age. Failures are remembered briefly so a dead network is not hammered, and an
unknown listing age blocks a NEW coin MA entry (missing evidence is not permission); the order stays pending and is re-checked.
This replaces the 0.3.0 "no extra candle request" choice for coins only, at most one request per symbol/frame/bar.

The market score at the BUY is stored with its basis; when the live score is stale (stock after the regular session) the LAST known
value is stored flagged `stale` with its timestamp instead of "unverified". Recording never changes a gate.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import threading
import time
import urllib.request

VERSION = "BUY_RECORD_V0430"
SCORE_BLOCK_COIN = 40.0
LISTING_MIN_DAYS = 90
MIN_BARS = 65
WANT_BARS = 200
TIMEOUT = 1.5
MIN_INTERVAL = 0.2
FAIL_COOLDOWN = 20.0
_UNIT = {"5m": 5, "30m": 30, "60m": 60}
_SECS = {"5m": 300, "30m": 1800, "60m": 3600, "day": 86400}
BASE = "https://api.upbit.com/v1/candles/"

_LOCK = threading.Lock()
_NET = threading.Lock()
_CLOSES = {}          # (symbol, frame, bar_index) -> (expires_monotonic, closes)
_FAIL = {}            # key -> monotonic until which a failed request is not repeated
_LISTING = {}         # symbol -> (checked_day, age_days or None)
_LAST_CALL = [0.0]


def _get(url, timeout=TIMEOUT):
    if os.environ.get("ARTITRADE_DISABLE_CANDLE_REST") == "1":      # regression runs must not depend on the network
        raise RuntimeError("candle REST disabled by environment")
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ARTITRADE"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch(url):
    """Rate-limited single request; raises on any failure."""
    with _NET:
        wait = MIN_INTERVAL - (time.monotonic() - _LAST_CALL[0])
        if wait > 0:
            time.sleep(wait)
        try:
            return _get(url)
        finally:
            _LAST_CALL[0] = time.monotonic()


def _now(now=None):
    value = now or dt.datetime.now(dt.timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def rest_closes(symbol, frame, now=None):
    """(closes oldest first, reason). Completed candles that ended at or before `now`; None when unavailable."""
    symbol = str(symbol or "")
    if not symbol.startswith("KRW-") or frame not in _SECS:
        return None, "REST_CLOSES_UNSUPPORTED"
    current = _now(now)
    secs = _SECS[frame]
    bar = int(current.timestamp() // secs)
    key = (symbol, frame, bar)
    mono = time.monotonic()
    with _LOCK:
        hit = _CLOSES.get(key)
        if hit and hit[0] > mono:
            return list(hit[1]), None
        if _FAIL.get(key, 0) > mono:
            return None, "REST_CLOSES_RECENT_FAILURE"
    url = ("%sdays?market=%s&count=%d" % (BASE, symbol, WANT_BARS) if frame == "day"
           else "%sminutes/%d?market=%s&count=%d" % (BASE, _UNIT[frame], symbol, WANT_BARS))
    try:
        rows = _fetch(url)
        out = []
        for row in sorted(rows, key=lambda r: r["candle_date_time_utc"]):
            start = dt.datetime.fromisoformat(row["candle_date_time_utc"]).replace(tzinfo=dt.timezone.utc)
            close = float(row["trade_price"])
            if start + dt.timedelta(seconds=secs) <= current and math.isfinite(close) and close > 0:
                out.append(close)
        if not out:
            raise ValueError("no completed candles")
    except Exception as exc:
        with _LOCK:
            _FAIL[key] = time.monotonic() + FAIL_COOLDOWN
        return None, "REST_CLOSES_" + type(exc).__name__
    with _LOCK:
        if len(_CLOSES) > 400:
            _CLOSES.clear()
        _CLOSES[key] = (time.monotonic() + max(30.0, secs - (current.timestamp() % secs)), list(out))
    return out, None


def listing_age_days(symbol, now=None):
    """(days since the first daily candle or None, reason). 200 candles means at least 200 days."""
    symbol = str(symbol or "")
    current = _now(now)
    today = current.strftime("%Y%m%d")
    with _LOCK:
        hit = _LISTING.get(symbol)
        if hit and (hit[1] is not None and hit[1] >= LISTING_MIN_DAYS or hit[0] == today and hit[1] is not None):
            return hit[1], None
        if hit and hit[1] is None and _FAIL.get(("listing", symbol), 0) > time.monotonic():
            return None, "LISTING_RECENT_FAILURE"
    try:
        rows = _fetch("%sdays?market=%s&count=%d" % (BASE, symbol, WANT_BARS))
        if not rows:
            raise ValueError("no daily candles")
        first = min(dt.datetime.fromisoformat(r["candle_date_time_utc"]).replace(tzinfo=dt.timezone.utc) for r in rows)
        days = max(0, (current - first).days)
        if len(rows) >= WANT_BARS:
            days = max(days, WANT_BARS)
    except Exception as exc:
        with _LOCK:
            _LISTING[symbol] = (today, None)
            _FAIL[("listing", symbol)] = time.monotonic() + FAIL_COOLDOWN
        return None, "LISTING_" + type(exc).__name__
    with _LOCK:
        _LISTING[symbol] = (today, days)
    return days, None


def coin_listing_block(symbol, now=None):
    """Reason when a NEW coin MA entry must not be opened (listed < 3 months, or age unknown), else None."""
    days, why = listing_age_days(symbol, now)
    if days is None:
        return "COIN_LISTING_AGE_UNVERIFIED_V0430:%s:%s" % (symbol, why)
    if days < LISTING_MIN_DAYS:
        return "COIN_LISTED_UNDER_3_MONTHS_V0430:%s:%dd" % (symbol, days)
    return None


def coin_score_block(symbol, frame, score):
    """Coin MA / MarketTrend: no NEW buy at market score +40 or more."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if math.isfinite(value) and value >= SCORE_BLOCK_COIN:
        return "COIN_SCORE_AT_OR_ABOVE_40_V0430:%s:%s:%+.1f" % (symbol, frame, value)
    return None


def coin_reference_allowed(score_info):
    """Coin reference bar, applied before the older positive-score / UP rule. False = no buy."""
    try:
        value = score_info.get("score")
        if value is None:
            return True                      # unknown score is decided by the older rule
        value = float(value)
    except (TypeError, ValueError, AttributeError):
        return True
    if not math.isfinite(value):
        return True
    return not (value >= SCORE_BLOCK_COIN or value < 0)


def score_snapshot(market, root=None):
    """Market score for the BUY record: {score, basis, stale, asof}. Never raises, never gates."""
    out = {"score": None, "basis": "SCORE_UNAVAILABLE", "stale": None, "asof": None}
    try:
        if root is None:
            import market_regime_v0290 as regime
            runtime = regime._runtime()
            root = getattr(runtime, "root", None) if runtime is not None else None
        if root is None:
            return out
        from at_market_score_gate_v107 import score_evidence, _score_worker_root
        evidence = score_evidence(root, market)
        if evidence.get("score") is not None:
            out.update(score=evidence["score"], basis=evidence.get("basis"), stale=False)
        else:
            out["basis"] = evidence.get("basis") or out["basis"]
        from market_research_v033 import _WORKERS, _WORKERS_LOCK
        with _WORKERS_LOCK:
            worker = _WORKERS.get(str(_score_worker_root(root, market)))
        row = ((worker.snapshot() or {}).get(market) or {}) if worker is not None else {}
        out["asof"] = row.get("asof")
        history = [r for r in (row.get("recent_scores") or []) if isinstance(r, dict) and r.get("score") is not None]
        if out["score"] is None and history:
            value = float(history[-1]["score"])
            if math.isfinite(value) and -100 <= value <= 100:
                out.update(score=value, stale=True, basis="LAST_KNOWN_SCORE_STALE_AT_BUY", asof=history[-1].get("ts"))
                try:
                    # 0.6.4: after the close the score is recorded every 3 minutes (history only); one younger than 10 minutes is the score of that moment.
                    if market == "stock" and time.time() - float(history[-1].get("ts")) <= 600:
                        out.update(stale=False, basis="AFTER_HOURS_SCORE_RECORDED_WITHIN_10_MIN")
                except (TypeError, ValueError):
                    pass
    except Exception as exc:
        out["basis"] = "SCORE_RECORD_ERROR_" + type(exc).__name__
    return out
