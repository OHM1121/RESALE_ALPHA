"""Read-only forensic audit of observed signals, skipped entry gates and MA checks.

This is intentionally separate from broker/strategy/order SQLite connections.
Neither initialization nor writes may block an executable trading decision.
No fake/retroactive orders, fills, prices, balances or ownership flags.
"""
from __future__ import annotations
import json
import sqlite3
from contextlib import closing
from pathlib import Path


class ForensicLedger:
    def __init__(self,root):
        self.path=Path(root)/'execution_decisions_v074.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(str(self.path),timeout=3)) as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('PRAGMA busy_timeout=3000')
            c.execute("""CREATE TABLE IF NOT EXISTS decisions(
                event_key TEXT PRIMARY KEY, at REAL NOT NULL,
                market TEXT NOT NULL, strategy TEXT NOT NULL,
                frame TEXT NOT NULL, symbol TEXT NOT NULL,
                mode TEXT NOT NULL, rsi20_live REAL,
                reason TEXT NOT NULL, payload TEXT NOT NULL)""")
            c.execute('CREATE INDEX IF NOT EXISTS ix_v074_at ON decisions(at DESC)')
            c.commit()

    def write(self,row):
        strategy=row.get('strategy','trend')
        kind='MA74' if strategy=='ma' else 'MT74'
        code=row['code']
        event_key=':'.join((kind,'AUDIT',row['market'],code,
                            row['frame'],row['bar_end'],row['reason']))
        mode='OBSERVED' if row['reason'].startswith('RSI30_OBSERVED') else 'NOT_ORDERED'
        body={'bar_end':row['bar_end'],
              'last_trade_age_sec':row.get('last_trade_age_sec'),
              'verified_order_placed':False,
              'forensic_only':True}
        with closing(sqlite3.connect(str(self.path),timeout=3)) as c:
            c.execute('PRAGMA busy_timeout=3000')
            c.execute("""INSERT OR IGNORE INTO decisions
                (event_key,at,market,strategy,frame,symbol,mode,
                 rsi20_live,reason,payload) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (event_key,float(row['audit_epoch']),row['market'],strategy,
                 row['frame'],code,mode,row.get('rsi20_live'),
                 row['reason'],json.dumps(body,ensure_ascii=False)))
            c.commit()

    def recent(self,limit=750):
        if not self.path.is_file():return []
        with closing(sqlite3.connect(str(self.path),timeout=3)) as c:
            c.row_factory=sqlite3.Row
            rows=c.execute("""SELECT event_key,at,market,strategy,
                frame,symbol,mode,rsi20_live,reason,payload
                FROM decisions ORDER BY at DESC LIMIT ?""",
                (max(1,min(2000,int(limit))),)).fetchall()
        return [dict(row) for row in rows]
