"""NEW LEGACY rev1 F6/F7: official entry-candle LOW + future closed bar.

Pure, forward-only state reconciliation. This module NEVER receives broker
credentials, sends orders, invents candles, records fills or mutates wallets.
The original MA/Trend owner alone sells from a separate current true BID.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

FRAME_SECONDS={"5m":300,"30m":1800,"60m":3600,"day":86400}
AUTHENTIC_SOURCES=frozenset((
    "UPBIT_CLOSED","UPBIT_CLOSED_OHLCV",
    "NATIVE_UPBIT_VERIFIED_COMPLETED",
    "KIWOOM_VERIFIED","LOCAL_KRX_VERIFIED",
    "FIX15_VERIFIED_KIWOOM_REST","ORIGINAL_UPBIT_MA_VERIFIED",
))

def _clock(value):
    if isinstance(value,datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    try:
        parsed=datetime.fromisoformat(str(value))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError,TypeError,OverflowError):
        return None

def _positive_number(value):
    try:
        d=Decimal(str(value))
        return d if d.is_finite() and d>0 else None
    except (ValueError,TypeError,InvalidOperation,OverflowError):
        return None

def reconcile(state, entry_at, frame, original_bars, now,
              entry_bar_start=None, entry_bar_end=None):
    """Observe only authenticated, completed source OHLCV bars.

    The exact entry-signal candle itself supplies the reference LOW. Touching
    this LOW arms the stop; a completed bar closing STRICTLY below that LOW
    marks a full-exit signal. Touch alone, intrabar last/ask, old pre-entry
    bars or a missing provenance NEVER qualifies.
    """
    state=dict(state or {})
    entered=_clock(entry_at)
    observed=_clock(now)
    start_needed=_clock(entry_bar_start) if entry_bar_start else None
    end_needed=_clock(entry_bar_end) if entry_bar_end else None
    if (entered is None or observed is None or entered>observed
        or frame not in FRAME_SECONDS
        or (start_needed is None and end_needed is None)
        or not isinstance(original_bars,(list,tuple))):
        return state

    valid=[]
    for raw in original_bars:
        if not isinstance(raw,dict):
            continue
        if str(raw.get("source") or "") not in AUTHENTIC_SOURCES:
            continue
        start=_clock(raw.get("start"))
        end=_clock(raw.get("end"))
        if end is None and start is not None:
            end=start+timedelta(seconds=FRAME_SECONDS[frame])
        low=_positive_number(raw.get("low"))
        high=_positive_number(raw.get("high"))
        close=_positive_number(raw.get("close"))
        if (start is None or end is None or start>=end or end>observed
            or any(x is None for x in (low,high,close))
            or not low<=close<=high):
            continue
        valid.append((start,end,low,close))
    valid.sort(key=lambda x:x[1])
    reference=next((b for b in valid if
        (start_needed is not None and b[0]==start_needed) or
        (end_needed is not None and b[1]==end_needed)),None)
    if reference is None or reference[1]<entered:
        return state
    ref_low=reference[2]
    prior=_positive_number(state.get("rev1_reference_low"))
    if prior is not None and prior!=ref_low:
        # Official history was revised. Avoid silently changing an active
        # stop, and do not sell before the original source resolves its audit.
        state["rev1_source_conflict"]=True
        return state
    if state.get("rev1_source_conflict") is True:
        return state
    state["rev1_reference_low"]=str(ref_low)
    state["rev1_reference_end"]=reference[1].isoformat()
    processed=_clock(state.get("rev1_last_bar_end"))
    for start,end,low,close in valid:
        if end<reference[1] or end<entered:
            continue
        if processed is not None and end<=processed:
            continue
        if low<=ref_low:
            if not state.get("rev1_low_touched"):
                state["rev1_low_touched"]=True
                state["rev1_low_touched_bar"]=end.isoformat()
        if (state.get("rev1_low_touched") is True and close<ref_low):
            state["rev1_below_reference_low_confirmed"]=True
            state["rev1_stop_closed_bar"]=end.isoformat()
        state["rev1_last_bar_end"]=end.isoformat()
    return state
