"""NEW LEGACY 11.20(2) workbook rules for stock+coin MA and MarketTrend.

Pure functions only: no orders, no fills, no wallet writes, no network. The
original MA / Trend owner still performs every SELL from a separate fresh bid.

1. Lookback-low CLOSE stop (workbook sheet 11.20(2), column "손절매도", red):
   right after the BUY, take the COMPLETED bars that ended before the purchase
   (the bar just before the buy is the newest one) and fix a stop price once:
     - MA (5m/30m/60m/day): lowest LOW of the last 50 completed bars;
     - MarketTrend 30m: lowest LOW of the last 200 completed bars;
       MarketTrend 60m and day: lowest LOW of the last 90 bars.
       (0.1.9, owner decision 2026-11-21: this was the AVERAGE of those LOWs in
       0.1.2-0.1.8. The average sat above the buy price for almost every
       MarketTrend entry - they buy a rebound after RSI<=30 - so the stop was
       ignored and never protected the position. The lowest low is used instead.)
   If that price is HIGHER than the buy price the stop is ignored. Afterwards a
   completed bar whose CLOSE is below the stop marks a full exit. Touching,
   intrabar prints and unverified bars never qualify.
   This replaces (blue "삭제") the 11.18 rev1 "reference-candle low touch, then
   close below it" exit for NEW positions (see new_legacy_rev1_low_close).
2. MarketTrend entry (red addition): after RSI20<=30, only the FIRST completed
   bar that closes above its Bollinger MIDDLE may buy; if that bar is missed or
   fails the slope condition the cycle is consumed until RSI rises above 30 and
   falls to <=30 again (see ``first_close_above_middle_end``).
"""
from __future__ import annotations

import datetime as dt
from statistics import fmean

from new_legacy_rev1_low_close import (
    AUTHENTIC_SOURCES, FRAME_SECONDS, _clock, _positive_number)

STOP_RULE="NL1120_2_LOOKBACK_LOW_CLOSE_STOP"
STOP_RULE_TREND_MIN="NL1120_3_LOOKBACK_MIN_LOW_CLOSE_STOP"
MA_STOP_BARS=50
TREND_STOP_BARS={"30m":200,"60m":90,"day":90}
# Fewer completed bars than this is not a lookback window at all (RSI20 and
# Bollinger already need 21). Between this and the full window the stop uses
# every bar that exists and records how many that were.
MIN_STOP_BARS=20
EXIT_REASON="NL1120_LOOKBACK_LOW_CLOSE_STOP"


def stop_bar_count(kind,frame):
    if kind=="ma":
        return MA_STOP_BARS if frame in FRAME_SECONDS else None
    if kind=="trend":
        return TREND_STOP_BARS.get(frame)
    return None


def _completed(original_bars,frame,now):
    """Authenticated, completed, internally consistent bars sorted by end."""
    seen={}
    for raw in original_bars if isinstance(original_bars,(list,tuple)) else ():
        if not isinstance(raw,dict):continue
        if str(raw.get("source") or "") not in AUTHENTIC_SOURCES:continue
        start=_clock(raw.get("start"))
        end=_clock(raw.get("end"))
        if end is None and start is not None:
            end=start+dt.timedelta(seconds=FRAME_SECONDS[frame])
        low=_positive_number(raw.get("low"))
        close=_positive_number(raw.get("close"))
        high=_positive_number(raw.get("high")) if raw.get("high") is not None else close
        if (start is None or end is None or start>=end or end>now
                or low is None or close is None or high is None
                or not low<=close<=high):
            continue
        seen[end]=(end,low,close)
    return [seen[k] for k in sorted(seen)]


def lookback_stop(kind,frame,bars,entered_at):
    """Return (status, price|None, bars_used). Pure; bars already completed."""
    need=stop_bar_count(kind,frame)
    if need is None:return "UNSUPPORTED_FRAME",None,0
    before=[b for b in bars if b[0]<=entered_at][-need:]
    if len(before)<MIN_STOP_BARS:return "HISTORY_SHORT",None,len(before)
    lows=[b[1] for b in before]
    price=min(lows)
    return "CANDIDATE",price,len(before)


def stop_rule_for(kind):
    return STOP_RULE_TREND_MIN if kind=="trend" else STOP_RULE


def stop_needs_refix(kind,state):
    """An OPEN, not yet triggered MarketTrend position still carrying the old average-of-lows stop."""
    return bool(kind=="trend" and isinstance(state,dict)
                and state.get("nl1120_stop_confirmed") is not True
                and state.get("nl1120_stop_status") in ("ACTIVE","IGNORED_ABOVE_ENTRY")
                and state.get("nl1120_stop_rule")!=STOP_RULE_TREND_MIN)


def reconcile(state,kind,frame,original_bars,entry_at,entry_price,now):
    """Fix the stop once, then observe completed closes after the purchase."""
    state=dict(state or {})
    entered=_clock(entry_at)
    observed=_clock(now)
    entry=_positive_number(entry_price)
    if (entered is None or observed is None or entered>observed or entry is None
            or frame not in FRAME_SECONDS or state.get("nl1120_stop_confirmed") is True):
        return state
    bars=_completed(original_bars,frame,observed)
    status=state.get("nl1120_stop_status")
    refix=stop_needs_refix(kind,state)
    if status not in ("ACTIVE","IGNORED_ABOVE_ENTRY") or refix:
        found,price,used=lookback_stop(kind,frame,bars,entered)
        if refix:
            # Re-fix once under the new rule, but only from the FULL window: a shorter window would
            # give a HIGHER minimum than the real one. Until then the old stop stays untouched.
            if found!="CANDIDATE" or used<(stop_bar_count(kind,frame) or 0):
                return state
            # Bars already judged against the old stop are judged again against the new one.
            state.pop("nl1120_last_bar_end",None)
        if found=="CANDIDATE":
            if price>float(entry):
                state.update({"nl1120_stop_status":"IGNORED_ABOVE_ENTRY",
                              "nl1120_stop_price":repr(float(price)),
                              "nl1120_stop_bars":used})
            else:
                state.update({"nl1120_stop_status":"ACTIVE",
                              "nl1120_stop_price":repr(float(price)),
                              "nl1120_stop_bars":used})
            state["nl1120_stop_rule"]=stop_rule_for(kind)
        else:
            # Evidence only: nothing is invented, the next cycle tries again.
            state["nl1120_stop_status"]=found
            state["nl1120_stop_bars"]=used
            return state
    if state.get("nl1120_stop_status")!="ACTIVE":
        return state
    stop=_positive_number(state.get("nl1120_stop_price"))
    if stop is None:return state
    processed=_clock(state.get("nl1120_last_bar_end"))
    for end,low,close in bars:
        if end<=entered:continue
        if processed is not None and end<=processed:continue
        if float(close)<float(stop):
            state["nl1120_stop_confirmed"]=True
            state["nl1120_stop_closed_bar"]=end.isoformat()
        state["nl1120_last_bar_end"]=end.isoformat()
        if state.get("nl1120_stop_confirmed") is True:break
    return state


def first_close_above_middle_end(rows,watch_started_at):
    """End of the FIRST completed bar after the RSI watch that closed above its
    own Bollinger(20) MIDDLE, or None. ``rows``: dicts with aware ``end`` and
    ``close``, sorted by ``end``. A bar that ended before the watch started is
    not "after RSI<=30" and is skipped. MIDDLE = mean of the last 20 closes
    including that bar, the same value ``bollinger`` uses.
    """
    ordered=[r for r in rows if isinstance(r,dict) and isinstance(r.get("end"),dt.datetime)]
    closes=[]
    for row in ordered:
        value=_positive_number(row.get("close"))
        closes.append(float(value) if value is not None else None)
    for i,row in enumerate(ordered):
        if watch_started_at is not None and row["end"]<watch_started_at:continue
        window=closes[max(0,i-19):i+1]
        if i<20 or len(window)<20 or any(x is None for x in window):continue
        if closes[i]>fmean(window):return row["end"]
    return None
