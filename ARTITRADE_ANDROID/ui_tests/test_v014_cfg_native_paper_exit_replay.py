"""Synthetic CFG-style exchange-event regression against ORIGINAL coin MA wallet.

This is a deterministic native PAPER test, NOT an Android/device/broker test.
Never interpret an OHLC high or a plan row as a recorded SELL.
"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import ma_v067_coin_runtime as ma_runtime
import ma_v067_policy as ma_policy
from coin_ma_engine import MACoinLedger

ma_runtime.install()
KST=dt.timezone(dt.timedelta(hours=9))
BUY_AT=dt.datetime(2026,11,19,9,34,55,tzinfo=KST)
SYMBOL="KRW-SIMCFG"  # Not a real exchange order or a user's trade.

class CFGNativePaperExitReplay(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix="artitrade-v014-cfg-")
        self.addCleanup(t.cleanup)
        self.wallet=MACoinLedger(Path(t.name)/"coin"/"ma_epoch_v067")
        self.candidate=dict(
            frame="5m",market=SYMBOL,
            ref_at="2026-11-19T09:30:00+09:00",
            ref_end="2026-11-19T09:35:00+09:00",
            score=80,limit="115",target="117.3",
            features=dict(
                ma_v067_kind="REVERSAL_UP",ma_v061_kind="TURNPOINT",
                ma_v067_bar_start="2026-11-19T09:30:00+09:00",
                ma_v067_submit_at=BUY_AT.isoformat(),
                ma_v067_120_delta="-0.01",
                ma_v067_15s_fill_until=(BUY_AT+dt.timedelta(seconds=15)).isoformat(),
                ma_v067_quote_source="EXACT_UPBIT_WS_TICK_AND_BOOK",
                source="UPBIT_V079_LIVE_10_TO_2_TICK_AND_BOOK",
                high=120,low=100,open=115,close=115))
        self.assertTrue(self.wallet.record_candidate(self.candidate,"SIMULATED_MA",BUY_AT))
        with self.wallet.connect() as c:
            self.assertEqual(c.execute("SELECT status FROM orders").fetchone()["status"],"PENDING")

    def book(self,when,bid,ask=None,bid_size="10000000",stamp=None):
        return dict(market=SYMBOL,bid=str(bid),
                    ask=str(ask if ask is not None else float(bid)+1),
                    bid_size=str(bid_size),ask_size="10000000",
                    timestamp=int((stamp or when).timestamp()*1000),sr_tick_size="0.01")

    def buy(self):
        self.wallet.process_book(self.book(BUY_AT+dt.timedelta(seconds=1),114,115),
                                 BUY_AT+dt.timedelta(seconds=1))
        with self.wallet.connect() as c:
            p=c.execute("SELECT * FROM positions WHERE market=?",(SYMBOL,)).fetchone()
            self.assertIsNotNone(p,"original MA BUY native receipt missing")
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions WHERE side='BUY'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM nl1118_resting_sells").fetchone()[0],2)
            return p["id"],float(p["entry"])

    def state(self,pid):
        with self.wallet.connect() as c:
            p=c.execute("SELECT status,qty FROM positions WHERE id=?",(pid,)).fetchone()
            fills=list(c.execute("SELECT side,qty,reason FROM executions WHERE position_id=? ORDER BY id",(pid,)))
            details=json.loads(c.execute("SELECT state_json FROM ma_v067_positions WHERE position_id=?",(pid,)).fetchone()[0])
            return p,fills,details

    def test_verified_ascent_without_book_then_fresh_retrace_native_sell(self):
        pid,entry=self.buy()
        up=BUY_AT+dt.timedelta(seconds=3)
        # Authentic-looking input to the ORIGINAL source-sidecar, no synthetic execution.
        ma_runtime._persist_verified_up_ascent(self.wallet,SYMBOL,str(entry*1.03),up)
        p,ex,details=self.state(pid)
        self.assertTrue(details["seen_above_entry"])
        self.assertEqual([x["side"] for x in ex],["BUY"])
        # A five-second-old price book cannot execute a SELL.
        at=BUY_AT+dt.timedelta(seconds=10)
        stale=self.book(at,entry*.99,stamp=at-dt.timedelta(seconds=7))
        self.wallet.process_book(stale,at)
        self.assertEqual([x["side"] for x in self.state(pid)[1]],["BUY"])
        # A fresh original Upbit bid below entry must close once, with native evidence.
        at=BUY_AT+dt.timedelta(seconds=12)
        self.wallet.process_book(self.book(at,entry*.99),at)
        p,ex,details=self.state(pid)
        self.assertEqual(p["status"],"CLOSED")
        self.assertAlmostEqual(float(p["qty"]),0,places=7)
        self.assertEqual([x["side"] for x in ex],["BUY","SELL"])
        self.assertEqual(ex[1]["reason"],"MA_V065_UP_PRE_FIRST_ENTRY_RETURN")
        self.assertAlmostEqual(float(ex[1]["qty"]),float(ex[0]["qty"]),places=7)
        # Out-of-order/stale replay cannot add an extra SELL or change the wallet.
        self.wallet.process_book(stale,BUY_AT+dt.timedelta(seconds=15))
        self.assertEqual(len(self.state(pid)[1]),2)

    def test_target_price_alone_does_not_generate_broker_fill(self):
        pid,entry=self.buy()
        with self.wallet.connect() as c:
            pending=list(c.execute("SELECT status,broker_order_id FROM nl1118_resting_sells ORDER BY stage"))
            self.assertEqual([x["status"] for x in pending],["PAPER_RESTING"]*2)
            self.assertEqual([x["broker_order_id"] for x in pending],["",""])
        # Historical/cached price has no native executable book.
        self.assertEqual([x["side"] for x in self.state(pid)[1]],["BUY"])

    def test_valid_bids_above_both_targets_consume_two_original_stage_receipts(self):
        pid,entry=self.buy()
        now=BUY_AT+dt.timedelta(seconds=3)
        target_1,target_2=ma_policy.up_targets(entry,"5m")
        self.wallet.process_book(self.book(now,float(target_2)*1.002),now)
        with self.wallet.connect() as c:
            fills=list(c.execute("SELECT side,reason FROM executions WHERE position_id=? ORDER BY id",(pid,)))
            self.assertEqual([x["side"] for x in fills],["BUY","SELL","SELL"])
            self.assertEqual(fills[1]["reason"],"MA_V1118_UP_FIRST_30PCT")
            self.assertEqual(fills[2]["reason"],"MA_V1118_UP_SECOND_35PCT")
            rows=list(c.execute("SELECT status FROM nl1118_resting_sells ORDER BY stage"))
            self.assertEqual([x["status"] for x in rows],["PAPER_FILLED","PAPER_FILLED"])

if __name__=="__main__":
    unittest.main(verbosity=2)
