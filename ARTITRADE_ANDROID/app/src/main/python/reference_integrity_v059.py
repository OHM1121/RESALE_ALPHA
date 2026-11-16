"""v060 narrow fix for superseded ORIGINAL coin reference orders.

The original EXE source owns the reference selection, market-volume unit,
MA special branch, moving-average slopes, limit-price calculation and all
broker/order execution. None of those rules is rewritten here.

The original recovery scanner already declares that a newer strict volume
record supersedes an older reference even when the newer candle fails its
wick/move/bear gate (d_upbit.recoverable_ref_features). The normal scanner
omitted the corresponding pending cancellation. Restore that single invariant
using only fully closed, sourced candles. MA orders are never changed here.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from decimal import Decimal

_INSTALLED=False
UTC=dt.timezone.utc


def _utc(value):
    result=(value if isinstance(value,dt.datetime) else
            dt.datetime.fromisoformat(str(value)))
    if result.tzinfo is None:
        raise ValueError("reference timestamp must be timezone aware")
    return result.astimezone(UTC)


def newest_superseding_record(history,frame,old_reference):
    """Return a newer true quantity-record candle, not a traded-value guess.

    This can report the 22:30 candle only when the required ORIGINAL
    lookback covers it and its verified base-asset quantity exceeds all
    earlier quantities in that wall-clock window. Candles not yet closed
    must never be passed by the upstream point_in_time_bars.
    """
    from coin_v24_policy import prior_window
    old=_utc(old_reference)
    newest=None
    for end in range(1,len(history)+1):
        candle=history[end-1]
        ref=_utc(candle["start"])
        if ref<=old:
            continue
        state=prior_window(history[:end],frame)
        if state is None:
            continue  # missing whole lookback: do not invent a record
        current=Decimal(str(state["ref"]["volume"]))
        earlier=Decimal(str(state["prior_max"]))
        if current>earlier:
            newest={"ref_start":ref.isoformat(),"current":str(current),
                    "prior_max":str(earlier),"metric":"BASE_ASSET_QUANTITY"}
    return newest


def _cancel_superseded_original_pending(runner,history,frame,market,at):
    """Cancel ONLY unfilled base orders older than a verified subsequent record.

    Reconciliation and original research remain append-only. A MA pending
    order is governed exclusively by the native coin_ma_engine state machine.
    """
    ledger=getattr(runner,"ledger",None)
    if ledger is None or not history:
        return 0
    changed=0
    with ledger.lock,ledger.connect() as conn:
        pending=conn.execute("""SELECT id,ref_at FROM orders
            WHERE market=? AND tf=? AND status='PENDING'""",
            (market,frame)).fetchall()
        for order in pending:
            proof=newest_superseding_record(history,frame,order["ref_at"])
            if proof is None:
                continue
            updated=conn.execute("""UPDATE orders SET status='REPLACED'
                WHERE id=? AND status='PENDING'""",(order["id"],))
            if updated.rowcount!=1:
                continue
            candidate=conn.execute("""SELECT id FROM candidates
                WHERE market=? AND tf=? AND ref_at=?""",
                (market,frame,order["ref_at"])).fetchone()
            if candidate is not None:
                ledger._decision(conn,candidate["id"],frame,market,at,
                    "REPLACED",
                    "새로운 완료봉 거래수량 최고기록 이후 과거 기준봉 대기 무효")
                try:
                    ledger._expire_waiting_shadow(
                        conn,candidate["id"],at,"REPLACED")
                except AttributeError:
                    pass
            conn.execute("""INSERT INTO issues(at,severity,code,message)
                VALUES(?,?,?,?)""",(at.isoformat(),"INFO",
                "COIN_ORIGINAL_NEWER_VOLUME_RECORD_SUPERSEDES_OLD",
                (frame+" "+market+" old="+str(order["ref_at"])+
                 " new="+proof["ref_start"]+
                 " qty="+proof["current"]+
                 " earlier_max="+proof["prior_max"])[:400]))
            changed+=1
    return changed


def install():
    """Attach one observation to the existing Upbit scan; never wrap MA policy."""
    global _INSTALLED
    if _INSTALLED:
        return
    import d_upbit
    original=d_upbit.UpbitPaperRunner._ma_scan_decision
    def scan_with_original_reference_cleanup(self,history,frame,code,tick,at,name):
        if history:
            # Scanning is strictly after an exchange-provided candle closes.
            # A fault here should be visible in MA_SCAN_EXCEPTION diagnostics
            # instead of being mislabeled a successful cleanup.
            _cancel_superseded_original_pending(
                self,history,frame,code,at)
        return original(self,history,frame,code,tick,at,name)
    d_upbit.UpbitPaperRunner._ma_scan_decision=scan_with_original_reference_cleanup
    _INSTALLED=True
