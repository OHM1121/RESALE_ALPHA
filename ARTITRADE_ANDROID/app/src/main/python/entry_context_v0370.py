"""0.3.7 (owner 2026-11-26): per-position BUY context written into the original ledger sidecars.

Owner: "원장에도 포지션 id별 매수진입당시 손익비 남기고(수급매매 제외) 매수당시 이동평균선 기울기
기록해놔 5, 10, 20, 60, 120 각각".

What is stored (inside the same per-position sidecar that already carries `rr_size`, keyed by the
original position id: GEN/AT plan, MA state, Trend payload):
  entry_ctx = {
    "version": "ENTRY_CTX_V0370",
    "rr": {ratio, gain_pct, loss_pct, stop_source}      # gain to the first target : loss to the stop, at the BUY
    "ma":  {"5": {"ma":..., "slope_pct":..., "entry_vs_ma":"ABOVE"|"BELOW"|"EQUAL"}, "10":..., "20", "60", "120"}
    "bars": number of completed closes used, "basis": "COMPLETED_BARS_BEFORE_BUY"
  }
slope_pct = (MA_n of the newest completed bar / MA_n one completed bar earlier - 1) * 100.
Missing evidence (too few completed bars, no history) is stored as None with a reason; it is never
filled with a guess. Pure arithmetic: no network, no order, no fill, no price is created.
GMO (수급매매) is not touched.
"""
from __future__ import annotations

import math

VERSION = "ENTRY_CTX_V0370"
PERIODS = (5, 10, 20, 60, 120)


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def clean_closes(closes):
    out = []
    for c in closes or ():
        v = _num(c)
        if v is not None:
            out.append(v)
    return out


def ma_context(closes, entry):
    """dict per period: ma, slope_pct, entry_vs_ma. `closes` = completed closes, oldest first."""
    values = clean_closes(closes)
    e = _num(entry)
    result = {}
    for n in PERIODS:
        row = {"ma": None, "slope_pct": None, "entry_vs_ma": None}
        if len(values) >= n:
            ma = sum(values[-n:]) / n
            row["ma"] = round(ma, 8)
            if e is not None:
                row["entry_vs_ma"] = "ABOVE" if e > ma else "BELOW" if e < ma else "EQUAL"
        if len(values) >= n + 1:
            prev = sum(values[-n - 1:-1]) / n
            now = sum(values[-n:]) / n
            if prev > 0:
                row["slope_pct"] = round((now / prev - 1.0) * 100.0, 6)
        result[str(n)] = row
    return {"ma": result, "bars": len(values)}


def trade_context(closes, market=None):
    """Trend (UP / DOWN / SIDEWAYS or UNVERIFIED) from the completed bars, and the market score at the BUY."""
    out = {}
    try:
        import market_regime_v0290 as regime
        label, metrics = regime.classify(closes or [])
        out["trend"] = label or "UNVERIFIED"
        out["trend_bars"] = metrics.get("bars")
        out["trend_rule"] = "close>MA20>MA60 and MA20 above 5 bars ago = UP; mirror = DOWN; else SIDEWAYS; needs 65 bars"
    except Exception as exc:
        out["trend"] = "UNVERIFIED"
        out["trend_error"] = type(exc).__name__
    if market in ("stock", "coin"):
        try:
            import buy_record_v0430 as record
            out["market_score"] = record.score_snapshot(market)
        except Exception as exc:
            out["market_score"] = {"score": None, "basis": "SCORE_RECORD_ERROR_" + type(exc).__name__}
    return out


def build(entry, closes, rr=None, reason=None, market=None):
    """The `entry_ctx` object for one position. Never raises."""
    try:
        ctx = {"version": VERSION, "basis": "COMPLETED_BARS_BEFORE_BUY"}
        ctx.update(ma_context(closes, entry))
        ctx.update(trade_context(closes, market))
        if ctx["bars"] == 0:
            ctx["reason"] = reason or "ENTRY_CTX_NO_COMPLETED_BARS"
        r = rr if isinstance(rr, dict) else {}
        ctx["rr"] = {"ratio": r.get("ratio"), "gain_pct": r.get("gain_pct"),
                     "loss_pct": r.get("loss_pct"), "stop_source": r.get("stop_source"),
                     "reason": r.get("reason")}
        return ctx
    except Exception as exc:  # pragma: no cover - diagnostics must never break a BUY
        return {"version": VERSION, "reason": "ENTRY_CTX_ERROR_" + type(exc).__name__}


def attach(info, entry, closes, reason=None, market=None):
    """Store `entry_ctx` in an rr_size evaluation dict (rr_size_v0340.record writes it to the sidecar)."""
    try:
        info["entry_ctx"] = build(entry, closes, info, reason, market)
    except Exception:
        pass
    return info
