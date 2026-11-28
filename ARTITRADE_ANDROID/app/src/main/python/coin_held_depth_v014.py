"""0.0.14 source-only: protect genuine Upbit MA holdings' quote coverage.

The original UpbitPaperRunner owns its single websocket and native PAPER
executions. This wrapper adds only independently verified MA OPEN symbols
to its *existing* ticker and orderbook watch lists, ahead of expendable
candidate symbols when the vendor caps a subscription. It neither creates
another socket nor treats an orderbook subscription as a fill.
"""
from __future__ import annotations
import datetime as dt
from decimal import Decimal, InvalidOperation
import math

_INSTALLED = False


def held_ma_symbols(runner):
    ledger = getattr(runner, "ma_ledger", None)
    found = getattr(ledger, "_ma_v067_up_hold_symbols", ()) if ledger is not None else ()
    return sorted({str(code) for code in (found or ())
                   if str(code).startswith("KRW-")})


def prioritize_watch_codes(regular, held):
    """Stable, de-duplicated held-first list (vendor list API preserved)."""
    used = set()
    out = []
    for code in list(held or ()) + list(regular or ()):
        code = str(code)
        if not code or code in used:
            continue
        used.add(code)
        out.append(code)
    return out


def book_sell_evidence(book, now):
    """Return (status, non-executable facts) from genuine book only.

    This helper NEVER converts last trade, OHLC or REST candle into depth.
    """
    if not isinstance(book, dict):
        return "BOOK_MISSING", {}
    if not isinstance(now, dt.datetime) or now.tzinfo is None:
        return "CLOCK_UNVERIFIED", {}
    try:
        stamp = float(book["timestamp"])
        if not math.isfinite(stamp) or stamp <= 0:
            return "BOOK_STAMP_INVALID", {}
        age = abs(now.timestamp() - stamp / 1000)
        if not __import__('fresh_rule_v0680').book_ok(age, 'coin'):
            return "BOOK_STALE", {"age_seconds": round(age, 3)}
        price = Decimal(str(book["bid"]))
        size = Decimal(str(book["bid_size"]))
        if not price.is_finite() or price <= 0:
            return "BOOK_BID_UNAVAILABLE", {"age_seconds": round(age, 3)}
        if not size.is_finite() or size <= 0:
            return "BOOK_BID_SIZE_EMPTY", {"age_seconds": round(age, 3)}
        return "OK", {"age_seconds": round(age, 3)}
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        return "BOOK_FIELDS_INVALID", {}


def install(d_upbit):
    """Preserve original watched-code function and Trend ticker property."""
    global _INSTALLED
    if _INSTALLED:
        return
    runner_type = d_upbit.UpbitPaperRunner
    original_book_watch = runner_type._both_watched_codes

    def ma_held_book_watch(self):
        return prioritize_watch_codes(original_book_watch(self),
                                      held_ma_symbols(self))

    runner_type._both_watched_codes = ma_held_book_watch
    previous_tracking = getattr(runner_type, "tracked", None)
    if isinstance(previous_tracking, property) and previous_tracking.fget:
        def ma_held_tracked(self):
            return prioritize_watch_codes(previous_tracking.fget(self),
                                          held_ma_symbols(self))
        runner_type.tracked = property(
            ma_held_tracked,
            previous_tracking.fset,
            previous_tracking.fdel,
            previous_tracking.__doc__,
        )
    _INSTALLED = True
