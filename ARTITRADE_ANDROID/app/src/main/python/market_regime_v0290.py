"""Market regime (UP / DOWN / SIDEWAYS) for the sheet-11.22 buy rules.

0.2.9 (owner workbook NEW_LEGACY11.18_rev1.xlsx, sheet 11.22, red text):
* C11 (every strategy, stock AND coin): UP -> buy whatever the market score and the former
  "score <= -10 -> half the per-symbol limit" rule is deleted; DOWN / SIDEWAYS per strategy.
* D6 MA / D7 MarketTrend:
    DOWN:     score >= -10 -> 50 %, -20 <= score < -10 -> 70 %, < -20 -> 100 %, >= +5 -> no NEW buy.
    SIDEWAYS: score >= -10 -> 50 %, < -10 -> 100 %, >= +10 -> no NEW buy.
* D5 reference bar: a NEW buy needs a positive score; only an UP regime lifts this.

0.2.10 (owner 2026-11-22, superseding the 0.2.9 KOSPI/BTC daily benchmark):
"각 시간봉별로 ... 다르게 평가" + "비트코인에 근거해서가 아니라 각 매수한 종목을 봐야지".
The regime is judged on the BOUGHT SYMBOL ITSELF, on the timeframe of the strategy frame
that buys it (5m / 30m / 60m / day), from completed bars the app already holds:
  stock  native bars table (stock/ARTITRADE_D.sqlite3), else the MarketTrend history;
  coin   the latest closed bars the original Upbit scanner produced (cached when
         d_upbit.point_in_time_bars runs for the current time), else the MarketTrend history.
No extra quote/candle request is made (owner API-limit rule). The market score itself is
still the market-wide signed score.

Rule per symbol/timeframe (INFERRED, the owner may correct):
  UP       close > MA20 > MA60 and MA20 higher than 5 bars ago
  DOWN     close < MA20 < MA60 and MA20 lower than 5 bars ago
  SIDEWAYS anything else
Fewer than 65 bars / no bars -> regime unverified -> SIDEWAYS table (INFERRED, middle
setting); an unverified score never reduces or blocks (unknown is not -10).
No order, fill, wallet or price is created here.
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
import statistics
import threading
import time
from decimal import Decimal
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
UP, DOWN, SIDEWAYS = "UP", "DOWN", "SIDEWAYS"
MA_SHORT, MA_LONG, SLOPE_LAG = 20, 60, 5
MIN_BARS = MA_LONG + SLOPE_LAG
KEEP_BARS = 90
FRAME_SECS = {"5m": 300, "30m": 1800, "60m": 3600, "day": 86400}
FALLBACK_REGIME = SIDEWAYS

TABLES = {
    DOWN: (5.0, ((-10.0, Decimal("0.5")), (-20.0, Decimal("0.7"))), Decimal(1)),
    SIDEWAYS: (10.0, ((-10.0, Decimal("0.5")),), Decimal(1)),
}
FULL = Decimal(1)
BLOCK_REASON = "SYMBOL_REGIME_SCORE_NO_NEW_BUY_V0290"

_LOCK = threading.Lock()
_COIN_BARS = {}          # (symbol, frame) -> (monotonic seconds, closes oldest first)
_INSTALLED = False


def classify(closes):
    """closes: completed closes, oldest first. Returns (regime or None, metrics)."""
    values = []
    for v in closes or ():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f) and f > 0:
            values.append(f)
    if len(values) < MIN_BARS:
        return None, {"bars": len(values)}
    close = values[-1]
    ma20 = statistics.fmean(values[-MA_SHORT:])
    ma60 = statistics.fmean(values[-MA_LONG:])
    ma20_prev = statistics.fmean(values[-MA_SHORT - SLOPE_LAG:-SLOPE_LAG])
    metrics = {"bars": len(values), "close": round(close, 4), "ma20": round(ma20, 4),
               "ma60": round(ma60, 4), "ma20_prev": round(ma20_prev, 4)}
    if close > ma20 > ma60 and ma20 > ma20_prev:
        return UP, metrics
    if close < ma20 < ma60 and ma20 < ma20_prev:
        return DOWN, metrics
    return SIDEWAYS, metrics


# ---------------------------------------------------------------- bar sources (no network)

def _runtime():
    try:
        import android_live_bridge as bridge
        return getattr(bridge, "_RUNTIME", None)
    except Exception:
        return None


def remember_coin_bars(symbol, frame, closes):
    with _LOCK:
        _COIN_BARS[(str(symbol), str(frame))] = (time.monotonic(), list(closes)[-KEEP_BARS:])


def _coin_cached(symbol, frame):
    with _LOCK:
        hit = _COIN_BARS.get((str(symbol), str(frame)))
    if not hit:
        return None
    age = time.monotonic() - hit[0]
    return hit[1] if age <= max(2 * FRAME_SECS.get(frame, 300), 900) else None


def _stock_bars(symbol, frame, conn=None, now=None):
    sql = ("SELECT close FROM bars WHERE code=? AND frame=? AND ts<=? ORDER BY ts DESC LIMIT ?")
    stamp = (now or dt.datetime.now(KST)).strftime("%Y%m%d%H%M%S")
    args = (str(symbol), str(frame), stamp, KEEP_BARS)
    try:
        if conn is not None:
            rows = conn.execute(sql, args).fetchall()
        else:
            rt = _runtime()
            root = getattr(rt, "root", None) if rt is not None else None
            if not root:
                return None
            db = Path(root) / "stock" / "ARTITRADE_D.sqlite3"
            if not db.exists():
                return None
            with sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2) as ro:
                rows = ro.execute(sql, args).fetchall()
    except Exception:
        return None
    return [r[0] for r in reversed(rows)] if rows else None


def _trend_history(market, symbol, frame, now=None):
    rt = _runtime()
    co = getattr(rt, "market_trend", None) if rt is not None else None
    if co is None:
        return None
    now = now or dt.datetime.now(KST)
    try:
        with co.lock:
            rows = list(co._history.get((market, str(symbol), frame), ()))
        rows = sorted((r for r in rows if isinstance(r, dict) and isinstance(r.get("end"), dt.datetime)
                       and r["end"].tzinfo is not None and r["end"] <= now), key=lambda r: r["end"])
        return [float(r["close"]) for r in rows][-KEEP_BARS:] or None
    except Exception:
        return None


def closes_for(market, symbol, frame, conn=None, now=None):
    """Completed closes of `symbol` on `frame` the app already holds, or None."""
    if not symbol or frame not in FRAME_SECS:
        return None
    if market == "coin":
        return _coin_cached(symbol, frame) or _trend_history(market, symbol, frame, now)
    if market == "stock":
        found = _stock_bars(symbol, frame, conn, now)
        if found and len(found) >= MIN_BARS:
            return found
        return _trend_history(market, symbol, frame, now) or found
    return None


def symbol_regime(market, symbol, frame, conn=None, closes=None, now=None):
    if closes is None:
        closes = closes_for(market, symbol, frame, conn, now)
    regime, metrics = classify(closes or ())
    return regime, metrics


# ---------------------------------------------------------------- policies

def _score(root, market):
    try:
        from at_market_score_gate_v107 import score_evidence
        return score_evidence(root, market).get("score")
    except Exception:
        return None


def ma_trend_decision(root, market, score=None, now=None, score_known=False,
                      symbol=None, frame=None, closes=None, conn=None):
    """MA / MarketTrend sizing at a BUY of `symbol` on `frame`.

    Returns {"factor", "block", "regime", "applied", "score", "symbol", "frame", "bars"}.
    `block` applies to NEW entries only; top-ups of an already filled entry use the factor.
    """
    if not score_known:
        score = _score(root, market)
    regime, metrics = symbol_regime(market, symbol, frame, conn, closes, now)
    applied = regime or FALLBACK_REGIME
    out = {"regime": regime, "applied": applied, "score": score, "symbol": symbol,
           "frame": frame, "bars": metrics.get("bars"), "factor": FULL, "block": None}
    if applied == UP:
        return out
    try:
        value = float(score)
        if not math.isfinite(value):
            raise ValueError
    except (TypeError, ValueError):
        return out                       # unknown score: never reduce or block
    no_buy_at, bands, below = TABLES[applied]
    factor = below
    for bound, f in bands:
        if value >= bound:
            factor = f
            break
    out["factor"] = factor
    if value >= no_buy_at:
        out["block"] = "%s:%s:%s:%s_SCORE_%+.1f_AT_OR_ABOVE_%+.0f" % (
            BLOCK_REASON, symbol, frame, applied, value, no_buy_at)
    return out


def at_buy_allowed(root, market, score_positive, symbol=None, frame=None, conn=None,
                   closes=None, now=None):
    """Reference-bar NEW buy: positive score, or the bought symbol is UP on its frame (C11)."""
    if score_positive:
        return True
    return symbol_regime(market, symbol, frame, conn, closes, now)[0] == UP


# ---------------------------------------------------------------- coin bar capture

def install():
    """Remember the closed bars the original Upbit scanner builds for the CURRENT time."""
    global _INSTALLED
    if _INSTALLED:
        return
    import d_upbit
    native = d_upbit.point_in_time_bars

    def point_in_time_bars(items, frame, at=None):
        bars = native(items, frame, at)
        try:
            now = dt.datetime.now(dt.timezone.utc)
            live = at is None or abs((now - at).total_seconds()) <= FRAME_SECS.get(frame, 300)
            if bars and live and frame in FRAME_SECS and len(bars) >= 2:
                symbol = next((x.get("market") for x in items if isinstance(x, dict) and x.get("market")), None)
                if symbol:
                    remember_coin_bars(symbol, frame, [b["close"] for b in bars])
        except Exception:
            pass
        return bars

    point_in_time_bars._v0290_regime = True
    d_upbit.point_in_time_bars = point_in_time_bars
    _INSTALLED = True
