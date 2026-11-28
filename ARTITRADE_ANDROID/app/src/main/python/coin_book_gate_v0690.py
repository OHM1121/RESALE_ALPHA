"""0.6.9 (owner 2026-11-28 "코인 다른 전략 및 프로그램 오류 수정"): the coin order-book path did a SQLite write transaction per book message for symbols nobody holds.

Evidence (device export 2026-11-28, `UPBIT_WS_BOOK_BACKLOG` 4,088 rows, `UPBIT_WS_BOOK_PROFILE_V0370`): about 2.3 million book messages received, 132,000 processed, a book waits 3-4 seconds before it is processed (`proc_avg_ms` 140, `proc_max_ms` 10,994).
The profile of 25 books: `coin_partial_entry_v094.book` 6.7 s, `reference_1118_runtime.book_coin` 3.1 s, vendor `process_book` 2.7 s, `ma_v067_coin_runtime.book` 0.9 s. Three overlay layers open a connection and `BEGIN IMMEDIATE` (a WRITE lock on a 500 MB database) for EVERY book
of EVERY market, although they only act on a market that has an open position (top-up of a just-bought position, the NL1118 take-profit / stop of a held position, the MA exits).
Fix: those three layers first ask a tiny cache - the markets that have an OPEN position of this ledger, re-read at most every 0.5 s - and skip the write transaction when the book is of a market nobody holds. A position opened a moment ago is seen within 0.5 s
(its first exit decision and the 15 s / 30 s top-up window are far longer; and every book that filled something clears the cache, so a position opened by a fill is seen at the very next book); when the cache cannot be read the layers work exactly as before. No order, fill, price or rule changes.
"""
from __future__ import annotations

import threading
import time

VERSION = "COIN_BOOK_GATE_V0690"
TTL_SECONDS = 0.5
_LOCK = threading.Lock()


def open_symbols(ledger, now=None):
    """frozenset of markets with an OPEN position in this ledger, or None when it cannot be told (callers then do the full work)."""
    clock = time.monotonic if now is None else now
    stamp = clock()
    cache = getattr(ledger, "_v0690_open_symbols", None)
    if cache is not None and 0.0 <= stamp - cache[0] < TTL_SECONDS:
        return cache[1]
    try:
        with ledger.connect() as c:
            rows = c.execute("SELECT DISTINCT market FROM positions WHERE status='OPEN' AND qty!='0'").fetchall()
        symbols = frozenset(str(r[0]) for r in rows)
    except Exception:
        return None
    try:
        with _LOCK:
            ledger._v0690_open_symbols = (stamp, symbols)
    except Exception:
        pass
    return symbols


def invalidate(ledger):
    """A book was processed and something was filled (a BUY may have opened a position): read the list again at the next book."""
    try:
        ledger._v0690_open_symbols = None
    except Exception:
        pass


def nobody_holds(ledger, symbol):
    """True only when it is certain that this ledger has no open position in `symbol`."""
    symbols = open_symbols(ledger)
    return symbols is not None and str(symbol) not in symbols
