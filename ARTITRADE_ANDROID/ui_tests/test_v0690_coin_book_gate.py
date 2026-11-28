"""0.6.9: the coin book path must not take a SQLite write lock for a market nobody holds (device export: a book waited 3-4 s, proc_avg 140 ms, proc_max 11 s)."""
from __future__ import annotations

import datetime as dt
import inspect
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

import coin_book_gate_v0690 as gate

KST = dt.timezone(dt.timedelta(hours=9))


class FakeLedger:
    def __init__(self, path, held=()):
        self.path = path
        self.lock = threading.RLock()
        self.connects = 0
        self.begins = 0
        self.account_variant = "base"
        with sqlite3.connect(path) as c:
            c.execute("CREATE TABLE positions(id INTEGER PRIMARY KEY,market TEXT,status TEXT,qty TEXT,qty0 TEXT,first_sold_qty TEXT,entry_at TEXT)")
            for i, market in enumerate(held, 1):
                c.execute("INSERT INTO positions(market,status,qty,qty0,first_sold_qty,entry_at) VALUES(?,?,?,?,?,?)", (market, "OPEN", "1", "1", "0", "2026-11-28T10:00:00+09:00"))
            c.execute("INSERT INTO positions(market,status,qty,qty0,first_sold_qty,entry_at) VALUES('KRW-OLD','CLOSED','0','1','0','2026-11-01T10:00:00+09:00')")

    def connect(self):
        self.connects += 1
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


class Cache(unittest.TestCase):
    def test_open_markets_only_and_cached_for_a_short_time(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = FakeLedger(str(Path(root) / "a.db"), held=("KRW-BTC", "KRW-ETH"))
            clock = [100.0]
            self.assertEqual(gate.open_symbols(ledger, lambda: clock[0]), frozenset({"KRW-BTC", "KRW-ETH"}))
            clock[0] = 100.3
            gate.open_symbols(ledger, lambda: clock[0])
            self.assertEqual(ledger.connects, 1, "inside the cache time the database is not asked again")
            clock[0] = 100.6
            gate.open_symbols(ledger, lambda: clock[0])
            self.assertEqual(ledger.connects, 2)

    def test_nobody_holds_is_certain_only_when_the_list_could_be_read(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = FakeLedger(str(Path(root) / "a.db"), held=("KRW-BTC",))
            self.assertFalse(gate.nobody_holds(ledger, "KRW-BTC"))
            self.assertTrue(gate.nobody_holds(ledger, "KRW-XRP"))
            self.assertTrue(gate.nobody_holds(ledger, "KRW-OLD"), "a closed position does not count")
        broken = types.SimpleNamespace(connect=lambda: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
        self.assertIsNone(gate.open_symbols(broken))
        self.assertFalse(gate.nobody_holds(broken, "KRW-XRP"), "unknown: the full work is done as before")


class Invalidation(unittest.TestCase):
    def test_a_book_that_filled_something_makes_the_next_book_read_the_list_again(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = FakeLedger(str(Path(root) / "a.db"), held=())
            clock = [10.0]
            self.assertEqual(gate.open_symbols(ledger, lambda: clock[0]), frozenset())
            with sqlite3.connect(ledger.path) as c:
                c.execute("INSERT INTO positions(market,status,qty,qty0,first_sold_qty,entry_at) VALUES('KRW-NEW','OPEN','1','1','0','2026-11-28T10:00:00+09:00')")
            clock[0] = 10.1                                                       # inside the cache time: still the old list
            self.assertEqual(gate.open_symbols(ledger, lambda: clock[0]), frozenset())
            gate.invalidate(ledger)                                               # what the book wrappers do after a fill
            self.assertEqual(gate.open_symbols(ledger, lambda: clock[0]), frozenset({"KRW-NEW"}))

    def test_the_book_wrappers_invalidate_after_a_fill(self):
        root = Path(__file__).resolve().parents[1] / "app/src/main/python"
        for name in ("ma_v067_coin_runtime.py", "reference_1118_runtime.py", "coin_partial_entry_v094.py"):
            self.assertIn("invalidate", (root / name).read_text("utf8"), name)


class Sites(unittest.TestCase):
    def test_the_three_layers_ask_the_gate_before_their_write_transaction(self):
        for name in ("coin_partial_entry_v094.py", "reference_1118_runtime.py", "ma_v067_coin_runtime.py"):
            text = (Path(__file__).resolve().parents[1] / "app/src/main/python" / name).read_text("utf8")
            self.assertIn("nobody_holds", text, name)
        text = (Path(__file__).resolve().parents[1] / "app/src/main/python/coin_partial_entry_v094.py").read_text("utf8")
        self.assertLess(text.index("nobody_holds(self,symbol)"), text.index("c.execute('BEGIN IMMEDIATE')"))

    def test_the_top_up_takes_no_write_lock_for_an_unheld_market_and_still_works_for_a_held_one(self):
        try:
            import coin_partial_entry_v094 as partial
            import d_upbit  # noqa: F401
        except Exception:
            self.skipTest("vendor engine not on the path")
        fake_bridge = types.SimpleNamespace(_RUNTIME=None)
        sys.modules["android_live_bridge"] = fake_bridge
        try:
            now = dt.datetime.now(KST)
            book = {"market": "KRW-XRP", "ask": "1000", "bid": "999", "ask_size": "5", "bid_size": "5", "timestamp": now.timestamp() * 1000}
            with tempfile.TemporaryDirectory() as root:
                ledger = FakeLedger(str(Path(root) / "a.db"), held=("KRW-BTC",))
                real_connect = ledger.connect
                statements = []

                def tracing_connect():
                    connection = real_connect()
                    connection.set_trace_callback(statements.append)
                    return connection
                ledger.connect = tracing_connect
                self.assertEqual(partial.topup_verified_coin_book(ledger, book, now), 0)
                self.assertFalse(any("BEGIN IMMEDIATE" in s for s in statements), "unheld market: no write transaction")
                held = dict(book, market="KRW-BTC")
                statements.clear()
                ledger._v0690_open_symbols = None
                partial.topup_verified_coin_book(ledger, held, now)
                self.assertTrue(any("BEGIN IMMEDIATE" in s for s in statements), "held market: the work is done as before")
        finally:
            sys.modules.pop("android_live_bridge", None)


if __name__ == "__main__":
    unittest.main()
