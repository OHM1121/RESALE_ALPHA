"""v058 verified best-bid/ask research sidecar.

Sample ONLY the existing broker feed's timestamped books on a detached worker.
Never alter an order, signal, wallet, source DB, or the broker socket. Observed
quotes are matched to entry time and retained as immutable per-trade metrics by
ai_approved_collect_v048 / ai_trade_lifecycle_v042.
"""
from __future__ import annotations

import datetime as dt
import math
import sqlite3
import time
from pathlib import Path

import ai_research_v040 as research

KEEP_MS=90*60*1000
MAX_AGE_MS=3000
_LAST_PRUNE={}


def _value(raw):
    try:
        num=float(raw)
        return num if math.isfinite(num) else None
    except (TypeError,ValueError,OverflowError):
        return None


def _milliseconds(value):
    if isinstance(value,dt.datetime):
        if value.tzinfo is None:return None
        return int(value.timestamp()*1000)
    num=_value(value)
    if num is None:return None
    return int(num if num>=1e11 else num*1000)


def _ensure(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS best_book_observations_v058(
        symbol TEXT NOT NULL,quote_ms INTEGER NOT NULL,
        bid REAL NOT NULL,ask REAL NOT NULL,
        source TEXT NOT NULL,recorded_ms INTEGER NOT NULL,
        PRIMARY KEY(symbol,quote_ms))""")
    conn.execute("""CREATE INDEX IF NOT EXISTS v058_best_book_age
        ON best_book_observations_v058(quote_ms)""")


def persist(root,market,records,now_ms=None):
    """Store only fresh verified feed observations; bulk writes off order thread."""
    if market not in ('stock','coin'):return 0
    now_ms=int(time.time()*1000 if now_ms is None else now_ms)
    rows=[]
    for symbol,stamp,bid,ask in records:
        ts=_milliseconds(stamp)
        b,a=_value(bid),_value(ask)
        if (not symbol or ts is None or b is None or a is None or
            not 0<b<=a or not 0<=now_ms-ts<=MAX_AGE_MS):
            continue
        rows.append((str(symbol),ts,b,a,
                     'KIWOOM_FRESH_BOOK' if market=='stock' else 'UPBIT_FRESH_BOOK',
                     now_ms))
    if not rows:return 0
    path=research.db_path(root,market)
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(str(path),timeout=4) as conn:
        conn.execute('PRAGMA busy_timeout=4000')
        conn.execute('PRAGMA journal_mode=WAL')
        _ensure(conn)
        conn.executemany("""INSERT OR IGNORE INTO best_book_observations_v058
            (symbol,quote_ms,bid,ask,source,recorded_ms) VALUES(?,?,?,?,?,?)""",
            rows)
        # Rotation makes the feed sidecar storage bounded. Original finalized
        # trade evidence is held in the separate permanent trade table.
        key=(str(Path(root)),market)
        if now_ms-_LAST_PRUNE.get(key,0)>120000:
            conn.execute("DELETE FROM best_book_observations_v058 WHERE quote_ms<?",
                         (now_ms-KEEP_MS,))
            _LAST_PRUNE[key]=now_ms
    return len(rows)


def lookup(conn,symbol,entry_time,max_age_ms=MAX_AGE_MS):
    """Only a quote measured BEFORE (or exactly at) the actual entry.

    Later order books are not allowed to describe a previous transaction.
    """
    entry_ms=_milliseconds(entry_time)
    if entry_ms is None or not symbol:return None
    try:
        row=conn.execute("""SELECT quote_ms,bid,ask,source
            FROM best_book_observations_v058
            WHERE symbol=? AND quote_ms BETWEEN ? AND ?
            ORDER BY quote_ms DESC LIMIT 1""",
            (str(symbol),entry_ms-int(max_age_ms),entry_ms)).fetchone()
    except sqlite3.Error:
        return None
    if not row:return None
    ts,bid,ask,source=row
    if not 0<float(bid)<=float(ask):return None
    return {'bid':float(bid),'ask':float(ask),
            'at_epoch':int(ts)/1000.0,'book_source':str(source)}


def sample_runtime(root,rt,market,now_ms=None):
    """Copy broker book references under their existing short-lived locks."""
    now_ms=int(time.time()*1000 if now_ms is None else now_ms)
    records=[]
    if market=='stock':
        runner=getattr(rt,'stock_runner',None)
        book=getattr(getattr(runner,'engine',None),'book',None)
        if book is None:return 0
        with book._lock:
            snapshot=list(book._items.items())
        for (code,venue),item in snapshot:
            if venue!='KRX' or not item or len(item)<4:continue
            ask,bid,_,seen=item[:4]
            records.append((code,seen,bid,ask))
    elif market=='coin':
        runner=getattr(rt,'coin_runner',None)
        if runner is None:return 0
        with runner._lock:
            snapshot=list(runner.last_book.items())
        for code,book in snapshot:
            if not isinstance(book,dict):continue
            records.append((code,book.get('timestamp'),
                            book.get('bid'),book.get('ask')))
    else:return 0
    return persist(root,market,records,now_ms)
