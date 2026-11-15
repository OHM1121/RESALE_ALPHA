"""Read-only guard against converting real broker holdings into PAPER sells.

The original broker FILLED journal is an authority only for orders it actually
recorded. An unresolved order or positive broker net must never be treated as
a paper-only position when Android has disarmed LIVE after a lease loss.
"""
from __future__ import annotations
import sqlite3
from contextlib import closing
from decimal import Decimal, InvalidOperation
from pathlib import Path

UNRESOLVED={"PREPARED","SENT","AMBIGUOUS","PARTIAL","UNKNOWN"}


class UnsafePaperExit(RuntimeError):
    pass


def _symbols(market,symbol):
    code=str(symbol or "").strip()
    if market not in ("stock","coin") or not code:
        raise UnsafePaperExit("PAPER_EXIT_SYMBOL_UNVERIFIED")
    # The Kiwoom original ledger may prefix KRX codes with A.
    if market=="stock":
        base=code[1:] if code.startswith("A") and code[1:].isdigit() else code
        return (base,"A"+base) if base.isdigit() else (code,)
    return (code,)


def refuse_paper_sell_if_live_open(root,market,symbol):
    """Raise unless a PAPER sell is safe against the locally known LIVE journal.

    Absence of a journal is not proof of no external holdings; this protects
    previously confirmed phone orders, and does not invent broker-side facts.
    """
    names=_symbols(market,symbol)
    journal=Path(root)/"broker"/"live_order_journal.sqlite3"
    if not journal.is_file():return
    try:
        uri=journal.resolve().as_uri()+"?mode=ro"
        with closing(sqlite3.connect(uri,uri=True,timeout=2)) as conn:
            placeholders=",".join("?" for _ in names)
            rows=conn.execute(
                "SELECT side,status,fill_qty FROM live_orders "
                "WHERE market=? AND symbol IN ("+placeholders+")",
                (market,*names)).fetchall()
        total=Decimal(0)
        for side,status,raw_qty in rows:
            state=str(status or "").upper()
            if state in UNRESOLVED:
                raise UnsafePaperExit("BROKER_ORDER_RECONCILIATION_REQUIRED")
            if state!="FILLED":continue
            try:
                qty=Decimal(str(raw_qty))
            except (InvalidOperation,TypeError,ValueError) as exc:
                raise UnsafePaperExit("BROKER_FILL_QUANTITY_UNVERIFIED") from exc
            if qty<=0:
                raise UnsafePaperExit("BROKER_FILL_QUANTITY_UNVERIFIED")
            if side=="BUY":total+=qty
            elif side=="SELL":total-=qty
            else:raise UnsafePaperExit("BROKER_FILL_SIDE_UNVERIFIED")
        if total!=0:
            raise UnsafePaperExit("LIVE_BROKER_POSITION_PAPER_EXIT_BLOCKED")
    except UnsafePaperExit:
        raise
    except (OSError,sqlite3.Error) as exc:
        raise UnsafePaperExit("BROKER_FILL_JOURNAL_UNREADABLE") from exc
