"""MarketTrend: first CLOSED RSI20 below 30 in the latest 10 candles.

This module does not read live/forming prices, place orders, or change exits.
The execution owner must separately verify a fresh exchange book and fill.
"""
from __future__ import annotations
import datetime as dt
import math

def first_closed_cross(closes, rsi_fn):
    """Require ten independently completed RSI observations (>=30 then <30).

    The last 10 CLOSED bars contain exactly one under-30 observation:
    their newest bar. Earlier nine RSI readings must be >=30.
    RSI(20) needs at least 21 closes before its first valid reading,
    hence 30 completed closes are required for this ten-bar test.
    """
    if len(closes) < 30:
        return None
    values = []
    for offset in range(9, -1, -1):
        subset = closes[:-offset] if offset else closes
        value = rsi_fn(subset)
        if value is None or not math.isfinite(float(value)):
            return None
        values.append(float(value))
    if values[-1] >= 30.0 or any(value < 30.0 for value in values[:-1]):
        return None
    return {"rsi20_closed": values[-1], "last_10_rsi20": values,
            "first_below_30": True, "basis": "COMPLETED_CANDLE_CLOSE"}

def first_ten_seconds(now, open_at):
    """Allow [opening, opening+10s) only; clock must be timezone aware."""
    if not isinstance(now, dt.datetime) or not isinstance(open_at, dt.datetime):
        return False
    if now.tzinfo is None or open_at.tzinfo is None:
        return False
    age = (now - open_at).total_seconds()
    return 0.0 <= age < 10.0

def verified_closed_rows(rows, open_at, market, frame):
    """No synthetic current candle, future bar, or stale Upbit history."""
    if not first_ten_seconds(open_at, open_at):
        return []
    ordered = sorted((row for row in rows
                      if isinstance(row, dict) and
                      isinstance(row.get("end"), dt.datetime) and
                      row["end"].tzinfo is not None and row["end"] <= open_at),
                     key=lambda x: x["end"])
    if len(ordered) < 30:
        return []
    last = ordered[-1]["end"]
    if market == "coin" and last != open_at:
        return []
    if market == "stock":
        # KRX previous session is allowed at the next regular opening,
        # but a stale weekend/holiday cache is NOT sufficient proof.
        if last != open_at and (open_at-last).total_seconds() > 4*86400:
            return []
    if any(not isinstance(x.get("close"), (int,float)) or
           not math.isfinite(float(x["close"])) or x["close"] <= 0
           for x in ordered[-30:]):
        return []
    return ordered
