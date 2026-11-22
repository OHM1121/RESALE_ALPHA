"""0.2.9 (owner workbook NEW_LEGACY11.18_rev1.xlsx, sheet 11.22, red text = added).

Common to every strategy, stock AND coin (C11):
* The app tells an UP / DOWN / SIDEWAYS market apart.
* UP market: buy whether the market score is below 0 or not, and the former
  "score <= -10 -> half the per-symbol limit" rule is deleted.
* DOWN / SIDEWAYS settings are per strategy:
  - MA and MarketTrend (D6, D7):
      DOWN:     score >= -10 -> 50 % of the per-symbol limit,
                -20 <= score < -10 -> 70 %, score < -20 -> 100 %,
                score >= +5 at the BUY -> no NEW buy.
      SIDEWAYS: score >= -10 -> 50 %, score < -10 -> 100 %,
                score >= +10 at the BUY -> no NEW buy.
  - Reference bar (AT, D5, unchanged black text): a NEW buy needs a positive
    score; only the UP market lifts this (C11).
  - Close-betting ("기존 방식 유지") and supply-demand do not use the market
    score at all, so nothing changes there.

Regime definition (INFERRED, the sheet does not define it; owner may correct):
completed DAILY bars of the market's benchmark - stock: KOSPI (Yahoo ^KS11),
coin: Upbit KRW-BTC.
  UP       close > MA20 > MA60 and MA20 higher than 5 bars ago
  DOWN     close < MA20 < MA60 and MA20 lower than 5 bars ago
  SIDEWAYS anything else
2-year check (2024-11..2026-11): KOSPI UP 52 % / SIDEWAYS 36 % / DOWN 12 %,
next-5-day mean +1.84 % / +0.19 % / -0.48 %; BTC 28 % / 44 % / 27 %.
An unverified regime falls back to the SIDEWAYS table (INFERRED, the middle
setting); an unverified score never reduces or blocks (unknown is not -10).

Read-only public data. No order, fill, wallet or price is created here.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics
import threading
import time
from decimal import Decimal

KST = dt.timezone(dt.timedelta(hours=9))
UP, DOWN, SIDEWAYS = "UP", "DOWN", "SIDEWAYS"
MA_SHORT, MA_LONG, SLOPE_LAG = 20, 60, 5
MIN_BARS = MA_LONG + SLOPE_LAG
VALID_SECONDS = 36 * 3600
REFRESH_SECONDS = 1800
RETRY_SECONDS = 120
SOURCES = {"stock": "YAHOO_KOSPI_^KS11_DAILY", "coin": "UPBIT_KRW-BTC_DAILY"}
FALLBACK_REGIME = SIDEWAYS

# (regime) -> (no-new-buy at score >=, [(score >= bound, factor), ...], factor below all bounds)
TABLES = {
    DOWN: (5.0, ((-10.0, Decimal("0.5")), (-20.0, Decimal("0.7"))), Decimal(1)),
    SIDEWAYS: (10.0, ((-10.0, Decimal("0.5")),), Decimal(1)),
}
FULL = Decimal(1)
BLOCK_REASON = "MARKET_REGIME_SCORE_NO_NEW_BUY_V0290"

_LOCK = threading.Lock()
_STATE = {}
_WORKER = None
_ROOT = None
_INSTALLED = False


def _now():
    return dt.datetime.now(KST)


def classify(closes):
    """closes: completed daily closes, oldest first. Returns (regime or None, metrics)."""
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


# ---------------------------------------------------------------- data

def _get_json(url, params):
    import requests
    r = requests.get(url, params=params, timeout=8,
                     headers={"User-Agent": "Mozilla/5.0 ARTITRADE market regime read-only"})
    r.raise_for_status()
    return r.json()


def parse_yahoo_daily(raw, now):
    """Completed KOSPI daily closes (today's bar dropped until 15:40 KST)."""
    rec = raw["chart"]["result"][0]
    stamps = rec.get("timestamp") or []
    closes = (rec.get("indicators") or {}).get("quote", [{}])[0].get("close") or []
    out = []
    for ts, c in zip(stamps, closes):
        if c is None:
            continue
        day = dt.datetime.fromtimestamp(int(ts), KST).date()
        if day > now.date() or (day == now.date() and now.time() < dt.time(15, 40)):
            continue
        out.append((day.isoformat(), float(c)))
    return out


def parse_upbit_daily(rows, now):
    """Completed KRW-BTC daily closes; the day candle starting today 09:00 KST is forming."""
    out = []
    session = (now - dt.timedelta(hours=9)).date()
    for r in rows or ():
        try:
            day = dt.date.fromisoformat(str(r["candle_date_time_kst"])[:10])
            c = float(r["trade_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if day >= session:
            continue
        out.append((day.isoformat(), c))
    out.sort()
    return out


def fetch(market, now=None):
    now = now or _now()
    if market == "stock":
        last = None
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            try:
                raw = _get_json("https://%s/v8/finance/chart/%%5EKS11" % host,
                                {"interval": "1d", "range": "6mo"})
                return parse_yahoo_daily(raw, now)
            except Exception as exc:
                last = exc
        raise RuntimeError("KOSPI_DAILY_UNAVAILABLE:%s" % type(last).__name__)
    rows = _get_json("https://api.upbit.com/v1/candles/days", {"market": "KRW-BTC", "count": 120})
    return parse_upbit_daily(rows, now)


# ---------------------------------------------------------------- state

def _path(root):
    return os.path.join(str(root), "market_regime_v0290.json") if root else None


def _root_dir():
    if _ROOT:
        return _ROOT
    try:
        import android_live_bridge as bridge
        rt = getattr(bridge, "_RUNTIME", None)
        return getattr(rt, "root", None) if rt is not None else None
    except Exception:
        return None


def _save():
    path = _path(_root_dir())
    if not path:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(_STATE, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def _load(root):
    path = _path(root)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            with _LOCK:
                for k in ("stock", "coin"):
                    if isinstance(data.get(k), dict):
                        _STATE[k] = data[k]
    except (OSError, ValueError, TypeError):
        pass


def refresh(market, now=None, fetcher=None):
    now = now or _now()
    try:
        bars = (fetcher or fetch)(market, now)
        regime, metrics = classify([c for _, c in bars])
        if regime is None:
            raise ValueError("INSUFFICIENT_DAILY_BARS")
        row = {"status": "VERIFIED", "regime": regime, "as_of": bars[-1][0],
               "fetched_at": now.isoformat(), "source": SOURCES[market], **metrics}
    except Exception as exc:
        with _LOCK:
            prev = dict(_STATE.get(market) or {})
        prev.update({"last_error": type(exc).__name__ + ":" + str(exc)[:80],
                     "last_error_at": now.isoformat()})
        with _LOCK:
            _STATE[market] = prev
        return prev
    with _LOCK:
        _STATE[market] = row
    _save()
    return row


def state(market, now=None):
    """Verified regime row, or {'status': 'UNVERIFIED', ...}."""
    now = now or _now()
    with _LOCK:
        row = dict(_STATE.get(market) or {})
    try:
        age = (now - dt.datetime.fromisoformat(row["fetched_at"])).total_seconds()
    except (KeyError, TypeError, ValueError):
        age = None
    if row.get("status") == "VERIFIED" and row.get("regime") in (UP, DOWN, SIDEWAYS) \
            and age is not None and -300 <= age <= VALID_SECONDS:
        return row
    return {"status": "UNVERIFIED", "regime": None, "source": SOURCES.get(market),
            "last_error": row.get("last_error")}


def set_state_for_test(market, row):
    with _LOCK:
        if row is None:
            _STATE.pop(market, None)
        else:
            _STATE[market] = dict(row)


# ---------------------------------------------------------------- policies

def _score(root, market):
    try:
        from at_market_score_gate_v107 import score_evidence
        return score_evidence(root, market).get("score")
    except Exception:
        return None


def ma_trend_decision(root, market, score=None, now=None, score_known=False):
    """MA / MarketTrend sizing at a BUY.

    Returns {"factor": Decimal, "block": reason|None, "regime", "applied", "score"}.
    `block` applies to NEW entries only; top-ups of an already filled entry use the factor.
    """
    if not score_known:
        score = _score(root, market)
    info = state(market, now)
    regime = info.get("regime")
    applied = regime or FALLBACK_REGIME
    out = {"regime": regime, "applied": applied, "score": score,
           "regime_status": info.get("status"), "factor": FULL, "block": None}
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
        out["block"] = "%s:%s_SCORE_%+.1f_AT_OR_ABOVE_%+.0f" % (BLOCK_REASON, applied, value, no_buy_at)
    return out


def at_buy_allowed(root, market, score_positive, now=None):
    """Reference-bar NEW buy: positive score, or an UP market whatever the score (C11)."""
    if score_positive:
        return True
    return state(market, now).get("regime") == UP


# ---------------------------------------------------------------- worker

def _worker_loop():
    next_at = {"stock": 0.0, "coin": 0.0}
    while True:
        try:
            if _root_dir() is None:
                time.sleep(30)
                continue
            for market in ("stock", "coin"):
                if time.monotonic() < next_at[market]:
                    continue
                row = refresh(market)
                ok = row.get("status") == "VERIFIED" and not row.get("last_error_at")
                next_at[market] = time.monotonic() + (REFRESH_SECONDS if ok else RETRY_SECONDS)
        except Exception:
            pass
        time.sleep(30)


def start_worker(root=None):
    global _WORKER, _ROOT
    if root:
        _ROOT = root
        _load(root)
    elif _root_dir():
        _load(_root_dir())
    if _WORKER is not None and _WORKER.is_alive():
        return _WORKER
    _WORKER = threading.Thread(target=_worker_loop, daemon=True, name="artitrade-market-regime-v0290")
    _WORKER.start()
    return _WORKER
