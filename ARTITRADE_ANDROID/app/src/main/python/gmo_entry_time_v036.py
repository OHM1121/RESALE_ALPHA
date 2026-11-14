"""Trace a GMO holding to its ORIGINAL buy_fills.time in EXE JSON.

Not the signal D-day, not D+1 date at midnight, not a later rebuy. No
trading state mutation: only the UI snapshot dictionaries are enriched.
"""
from __future__ import annotations

def _day(raw):
    value=str(raw or "").strip()
    if len(value)>=10 and value[4]=="-" and value[7]=="-":
        return value[:10]
    if len(value)>=8 and value[:8].isdigit():
        return value[:4]+"-"+value[4:6]+"-"+value[6:8]
    return ""


def entry_fill(position,buy_fills):
    code=str(position.get("code") or "").strip()
    entered=_day(position.get("entry_date"))
    if not code or not entered:return None
    matches=[]
    for fill in buy_fills or ():
        if not isinstance(fill,dict) or str(fill.get("code") or "")!=code:
            continue
        ts=str(fill.get("time") or "").strip()
        if _day(ts)!=entered:continue
        try:
            q=float(fill.get("qty") or 0)
            price=float(fill.get("price") or 0)
        except (ValueError,TypeError):
            continue
        if q>0 and price>0:
            matches.append(fill)
    if not matches:return None
    first=min(matches,key=lambda f:str(f["time"]))
    return {"entry_at":str(first["time"]),
            "buy_at":str(first["time"]),
            "buy_reason":str(first.get("purpose") or ""),
            "buy_reason_source":"EXE_GMO_CONFIRMED_BUY_FILL"}


def enrich_positions(ui_positions,state):
    originals=state.get("positions") or {}
    buys=state.get("buy_fills") or []
    if not isinstance(originals,dict):return ui_positions
    for row in ui_positions or ():
        if not isinstance(row,dict):continue
        code=str(row.get("code") or "")
        original=originals.get(code)
        if not isinstance(original,dict):continue
        check=dict(original,code=code)
        found=entry_fill(check,buys)
        if not found:continue
        row.update(found)
    return ui_positions
