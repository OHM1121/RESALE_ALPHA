"""0.4.1 (owner 2026-11-27): the supply-demand (GMO) fills of one position are shown as ONE trade.

Owner: "주식 부분 체결 이력은 왜 포지션 id 별로 매수매도를 코인처럼 하나로 안 묶어 놨어".

Root cause (CONFIRMED in code + export): the vendor GMOService.snapshot() gives every GMO position the placeholder id -1
and every fill a negative id without a position id or entry date. position_uid_v015._identity_key then has no stable
identity for a fill ("unlinked_fill:<id>"), so each partial BUY (e.g. 3 fills of 1 share) and each SELL became its own
uid and its own card, while coin trades share one position id.

Fix: before the uid sidecar runs, every GMO fill gets the `entry_date` of the D+1 entry it belongs to (the newest
entry date of that symbol that is not after the fill day, taken from the engine's own `used`, `positions` and `signals`).
position_uid_v015 then derives the same `entry:<code>|<entry_date>` identity it already uses for the GMO position, so the
position and all its fills share one uid and the history groups them. Display linkage only: no fill, price, quantity,
order or ledger value is changed; a fill that cannot be linked unambiguously stays unlinked.
"""
from __future__ import annotations

import collections


def _entry_dates(engine):
    dates = collections.defaultdict(set)
    try:
        for key in list(getattr(engine, "used", ()) or ()):
            code, _, day = str(key).partition("|")
            if code and len(day) == 10:
                dates[code].add(day)
    except Exception:
        pass
    for attr in ("positions", "signals"):
        try:
            for code, item in dict(getattr(engine, attr, {}) or {}).items():
                day = str(getattr(item, "entry_date", "") or "")
                if len(day) == 10:
                    dates[str(code)].add(day)
        except Exception:
            pass
    return dates


def link(executions, engine):
    """Set `entry_date` on GMO fills that lack both position_id and entry_date. Returns the number linked."""
    if not isinstance(executions, list):
        return 0
    dates = _entry_dates(engine)
    linked = 0
    for row in executions:
        if not isinstance(row, dict) or row.get("entry_date") or row.get("position_id"):
            continue
        code = str(row.get("code") or "")
        day = str(row.get("ts") or row.get("time") or row.get("at") or "")[:10]
        options = sorted(d for d in dates.get(code, ()) if d <= day)
        if options and len(day) == 10:
            row["entry_date"] = options[-1]
            linked += 1
    return linked
