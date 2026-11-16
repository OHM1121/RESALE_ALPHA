"""v049 broker-vs-paper mode safety, without network or trading DB edits."""
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import TestCase

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
from android_live_position_guard_v049 import (
    UnsafePaperExit,refuse_paper_sell_if_live_open)


class PaperExitProtection(TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="v049_live_paper_guard_")
        self.root=Path(self.temporary.name)
        p=self.root/"broker"
        p.mkdir()
        self.db=p/"live_order_journal.sqlite3"
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TABLE live_orders(
                 market TEXT,symbol TEXT,side TEXT,status TEXT,fill_qty TEXT)""")

    def tearDown(self):
        self.temporary.cleanup()

    def fill(self,market,symbol,side,status,qty):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO live_orders VALUES(?,?,?,?,?)",
                      (market,symbol,side,status,str(qty)))

    def test_open_live_coin_cannot_be_paper_closed(self):
        self.fill("coin","KRW-BTC","BUY","FILLED","0.65")
        with self.assertRaisesRegex(UnsafePaperExit,
                "LIVE_BROKER_POSITION_PAPER_EXIT_BLOCKED"):
            refuse_paper_sell_if_live_open(self.root,"coin","KRW-BTC")
        self.fill("coin","KRW-BTC","SELL","FILLED","0.65")
        refuse_paper_sell_if_live_open(self.root,"coin","KRW-BTC")

    def test_unconfirmed_order_also_blocks_paper_exit(self):
        self.fill("stock","005930","BUY","SENT","")
        with self.assertRaisesRegex(UnsafePaperExit,
                "BROKER_ORDER_RECONCILIATION_REQUIRED"):
            refuse_paper_sell_if_live_open(self.root,"stock","005930")

    def test_stock_A_prefix_and_market_are_scoped(self):
        self.fill("stock","005930","BUY","FILLED","2")
        with self.assertRaisesRegex(UnsafePaperExit,"LIVE_BROKER_POSITION"):
            refuse_paper_sell_if_live_open(self.root,"stock","A005930")
        refuse_paper_sell_if_live_open(self.root,"coin","KRW-BTC")
        refuse_paper_sell_if_live_open(self.root,"stock","000660")

    def test_no_fill_is_not_mistaken_for_real_holding(self):
        self.fill("coin","KRW-ETH","BUY","NO_FILL","0")
        refuse_paper_sell_if_live_open(self.root,"coin","KRW-ETH")

    def test_corrupt_broker_journal_fails_closed(self):
        self.db.write_bytes(b"not a sqlite database")
        with self.assertRaisesRegex(UnsafePaperExit,"JOURNAL_UNREADABLE"):
            refuse_paper_sell_if_live_open(self.root,"coin","KRW-BTC")

    def test_android_source_guards_original_execution_proxy(self):
        source=(ROOT/"app/src/main/python/android_runtime_v078.py").read_text(
            encoding="utf-8")
        self.assertIn("_core.LiveConnectionProxy._before",source)
        self.assertIn("refuse_paper_sell_if_live_open(self._rt.root,market,symbol)",source)
        self.assertIn("paper_exit_of_live_holding_blocked",source)
