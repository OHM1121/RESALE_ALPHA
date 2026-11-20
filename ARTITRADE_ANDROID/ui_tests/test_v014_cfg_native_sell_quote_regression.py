"""0.0.14 CFG-class genuine native PAPER scenarios.

Every BUY/SELL executes through the repository's original MACoinLedger.
Symbols/prices are synthetic fixtures; these tests never contact Upbit,
place broker orders or edit the user's private Android wallet.
"""
import datetime as dt
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import coin_held_depth_v014 as book_guard
from coin_held_depth_v014 import (
    held_ma_symbols, prioritize_watch_codes, book_sell_evidence,
)
import ma_v067_coin_runtime as runtime
import ma_v067_policy as policy
from coin_ma_engine import MACoinLedger

runtime.install()
KST=dt.timezone(dt.timedelta(hours=9))
ENTRY=dt.datetime(2026,11,18,9,29,55,tzinfo=KST)
SYMBOL="KRW-SIMCFG"


def genuine_book(bid, ask, when, bid_size="1000000"):
    return {"market":SYMBOL,"bid":str(bid),"ask":str(ask),
            "bid_size":str(bid_size),"ask_size":"1000000",
            "timestamp":int(when.timestamp()*1000),"sr_tick_size":"0.01"}


class MAOriginalNativePaperSimulation(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="artitrade-014-cfg-")
        self.addCleanup(tmp.cleanup)
        self.wallet=MACoinLedger(Path(tmp.name)/"coin_ma")
        bars=[150.]*61+[100+i*.2 for i in range(59)]+[99.]
        detected=policy.upward_transition_on_forming(bars,114,5)
        self.assertEqual(detected["kind"],"REVERSAL_UP")
        details=dict(ma_v067_kind="REVERSAL_UP",ma_v061_kind="TURNPOINT",
            source="UPBIT_V079_LIVE_10_TO_2_TICK_AND_BOOK",
            ma_v067_bar_start="2026-11-18T09:25:00+09:00",
            ma_v067_submit_at=ENTRY.isoformat(),
            ma_v067_120_delta=detected["ma_120_delta"],
            ma_v067_15s_fill_until=(ENTRY+dt.timedelta(seconds=15)).isoformat())
        record=dict(frame="5m",market=SYMBOL,
             ref_at="2026-11-18T09:25:00+09:00",
             ref_end="2026-11-18T09:30:00+09:00",
             score=80,limit="115",target="119",features=details)
        # Original 5m range gate requires verifiable high/low as well;
        # synthetic OHLC must be complete rather than auto-accepted.
        details.update(high=120,low=100,open=115,close=115)
        accepted=self.wallet.record_candidate(record,"SIM_MA",ENTRY)
        with self.wallet.connect() as c:
            stored=[dict(r) for r in c.execute(
                "SELECT id,market,tf,ref_at FROM candidates")]
        self.assertTrue(accepted,stored)
        self.wallet.process_book(genuine_book(114,115,ENTRY+dt.timedelta(seconds=1)),
                                 ENTRY+dt.timedelta(seconds=1))
        with self.wallet.connect() as c:
            self.position=c.execute("SELECT * FROM positions WHERE market=?",
                                    (SYMBOL,)).fetchone()
            self.assertIsNotNone(self.position,"the native BUY must be confirmed")
            self.pid=int(self.position["id"])
            receipts=list(c.execute("SELECT side FROM executions WHERE position_id=?",
                                    (self.pid,)))
            self.assertEqual([x["side"] for x in receipts],["BUY"])
            targets=list(c.execute("SELECT * FROM nl1118_resting_sells WHERE position_id=?",
                                   (self.pid,)))
            self.assertEqual([x["stage"] for x in targets],[1,2])
            self.assertTrue(all(x["status"]=="PAPER_RESTING" and
                                x["broker_order_id"]=="" for x in targets))
        self.assertIn(SYMBOL,self.wallet._ma_v067_up_hold_symbols)

    def sell_receipts(self):
        with self.wallet.connect() as c:
            return list(c.execute("SELECT side,qty,reason FROM executions WHERE position_id=? ORDER BY id",
                                  (self.pid,)))

    def test_cfg_rally_without_book_never_fakes_sell_retrace_only_holds_until_lookback_close_stop(self):
        rally=ENTRY+dt.timedelta(seconds=3)
        runtime._persist_verified_up_ascent(self.wallet,SYMBOL,"118",rally)
        with self.wallet.connect() as c:
            saved=json.loads(c.execute("SELECT state_json FROM ma_v067_positions WHERE position_id=?",
                  (self.pid,)).fetchone()["state_json"])
        self.assertTrue(saved["seen_above_entry"])
        self.assertEqual(saved["verified_above_entry_source"],"UPBIT_VERIFIED_WS_TICKER")
        self.assertEqual([x["side"] for x in self.sell_receipts()],["BUY"])
        # Artificially stale book is rejected rather than converted to a SELL.
        moment=ENTRY+dt.timedelta(seconds=9)
        stale=genuine_book(112,113,moment-dt.timedelta(seconds=5))
        with patch.object(self.wallet,"write_issue") as report:
            runtime._process_v067_exits(self.wallet,stale,moment)
            self.assertTrue(any("BOOK_STALE" in str(v) for v in report.call_args_list))
        self.assertEqual([x["side"] for x in self.sell_receipts()],["BUY"])
        # NEW LEGACY F6: returning to entry is NOT an automatic exit.
        live=moment+dt.timedelta(seconds=1)
        self.wallet.process_book(genuine_book(112,113,live),live)
        receipts=self.sell_receipts()
        self.assertEqual([x["side"] for x in receipts],["BUY"])
        with self.wallet.connect() as c:
            p=c.execute("SELECT qty,status FROM positions WHERE id=?",(self.pid,)).fetchone()
            self.assertGreater(float(p["qty"]),0.)
            self.assertEqual(p["status"],"OPEN")
        # NEW LEGACY 11.20(2): the reference-candle-low rule (rev1) is RETIRED for
        # new positions (workbook blue "삭제"). A touch of the old reference low
        # plus a later close below it must NOT sell. The stop is now the lowest low
        # of the last 50 completed bars before the BUY (here 110), and only a later
        # COMPLETED bar CLOSING below it sells, on a separate genuine current BID.
        import ma_v061_coin_runtime as source
        def prior(i):
            return {"start":ENTRY.replace(minute=25,second=0)-dt.timedelta(minutes=5*(25-i)),
                    "low":"110","high":"120","close":"112"}
        history=[prior(i) for i in range(25)]
        history+=[{"start":ENTRY.replace(minute=25,second=0),
                   "low":"113","high":"116","close":"115"},
                  {"start":ENTRY.replace(minute=30,second=0),
                   "low":"112","high":"115","close":"112"}]
        closed=ENTRY.replace(minute=35,second=1)
        with source._HISTORY_LOCK:
            source._HISTORY[("5m",SYMBOL)]=(closed,tuple(history))
        try:
            self.wallet.process_book(genuine_book(112,113,closed),closed)
            self.assertEqual([x["side"] for x in self.sell_receipts()],["BUY"],
                             "rev1 reference-low close must no longer sell")
            with self.wallet.connect() as c:
                state=json.loads(c.execute(
                    "SELECT state_json FROM ma_v067_positions WHERE position_id=?",
                    (self.pid,)).fetchone()["state_json"])
            self.assertTrue(state["nl1120"])
            self.assertEqual(state["nl1120_stop_status"],"ACTIVE")
            self.assertEqual(float(state["nl1120_stop_price"]),110.0)
            # a completed bar that CLOSES below the fixed stop sells everything
            history.append({"start":ENTRY.replace(minute=35,second=0),
                            "low":"107","high":"112","close":"108"})
            later=ENTRY.replace(minute=40,second=1)
            with source._HISTORY_LOCK:
                source._HISTORY[("5m",SYMBOL)]=(later,tuple(history))
            self.wallet.process_book(genuine_book(108,109,later),later)
            receipts=self.sell_receipts()
            self.assertEqual([x["side"] for x in receipts],["BUY","SELL"])
            self.assertEqual(receipts[1]["reason"],"MA_NL1120_LOOKBACK_LOW_CLOSE_STOP")
            with self.wallet.connect() as c:
                p=c.execute("SELECT qty,status FROM positions WHERE id=?",
                            (self.pid,)).fetchone()
                self.assertEqual(float(p["qty"]),0.)
                self.assertEqual(p["status"],"CLOSED")
        finally:
            with source._HISTORY_LOCK:
                source._HISTORY.pop(("5m",SYMBOL),None)

    def test_invalid_bid_depth_does_not_fill_and_is_diagnosed(self):
        now=ENTRY+dt.timedelta(seconds=6)
        with patch.object(self.wallet,"write_issue") as report:
            runtime._process_v067_exits(self.wallet,genuine_book(130,131,now,"0"),now)
            self.assertTrue(any("BOOK_BID_SIZE_EMPTY" in str(v)
                                for v in report.call_args_list))
        self.assertEqual([x["side"] for x in self.sell_receipts()],["BUY"])

    def test_actual_native_tp1_tp2_and_final_sells(self):
        for i,bid,ask in ((3,118,119),(5,123,124),(7,130,131),(9,125,126)):
            now=ENTRY+dt.timedelta(seconds=i)
            self.wallet.process_book(genuine_book(bid,ask,now),now)
        receipts=self.sell_receipts()
        self.assertEqual([x["side"] for x in receipts],["BUY","SELL","SELL","SELL"])
        self.assertTrue(receipts[1]["reason"].startswith("MA_V1118_UP_FIRST"))
        self.assertTrue(receipts[2]["reason"].startswith("MA_V1118_UP_SECOND"))
        with self.wallet.connect() as c:
            p=c.execute("SELECT qty,status FROM positions WHERE id=?",(self.pid,)).fetchone()
            self.assertEqual(p["status"],"CLOSED")
            self.assertEqual(float(p["qty"]),0.)


class HeldSubscriptionAndSafety(unittest.TestCase):
    def test_held_coin_precedes_long_ranked_cohort_without_second_socket(self):
        runner=SimpleNamespace(ma_ledger=SimpleNamespace(
             _ma_v067_up_hold_symbols={SYMBOL}))
        self.assertEqual(held_ma_symbols(runner),[SYMBOL])
        wanted=prioritize_watch_codes(["KRW-AA","KRW-BB",SYMBOL,"KRW-CC"],
                                      held_ma_symbols(runner))
        self.assertEqual(wanted,[SYMBOL,"KRW-AA","KRW-BB","KRW-CC"])
        self.assertEqual(len(wanted),len(set(wanted)))
        self.assertEqual(prioritize_watch_codes(["KRW-AA"],[]),["KRW-AA"])

    def test_real_class_depth_wrapper_installed_and_fake_runner_has_held_priority(self):
        import d_upbit
        self.assertEqual(d_upbit.UpbitPaperRunner._both_watched_codes.__name__,
                         'ma_held_book_watch')
        class IsolatedRunner:
            def __init__(self):
                self.ma_ledger=SimpleNamespace(
                    _ma_v067_up_hold_symbols={SYMBOL})
                self._tracking=['KRW-OTHER','KRW-CANDIDATE']
            def _both_watched_codes(self):
                return ['KRW-OTHER','KRW-CANDIDATE']
            @property
            def tracked(self):
                return self._tracking
            @tracked.setter
            def tracked(self,value):
                self._tracking=list(value)
        vendor=SimpleNamespace(UpbitPaperRunner=IsolatedRunner)
        with patch.object(book_guard,'_INSTALLED',False):
            book_guard.install(vendor)
        runner=IsolatedRunner()
        self.assertEqual(runner._both_watched_codes()[0],SYMBOL)
        self.assertEqual(runner.tracked[0],SYMBOL)
        runner.tracked=['KRW-NEW']
        self.assertEqual(runner.tracked,[SYMBOL,'KRW-NEW'])

    def test_true_source_freshness_is_not_extended(self):
        now=ENTRY+dt.timedelta(seconds=5)
        self.assertEqual(book_sell_evidence(None,now)[0],"BOOK_MISSING")
        self.assertEqual(book_sell_evidence(genuine_book(118,119,
             now-dt.timedelta(seconds=4)),now)[0],"BOOK_STALE")
        self.assertEqual(book_sell_evidence(genuine_book(118,119,now,"0"),
             now)[0],"BOOK_BID_SIZE_EMPTY")
        self.assertEqual(book_sell_evidence(genuine_book(118,119,now),
             now)[0],"OK")

if __name__=="__main__":
    unittest.main(verbosity=2)
