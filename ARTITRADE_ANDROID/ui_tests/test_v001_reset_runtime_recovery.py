"""ARTITRADE 0.0.1 post-reset regression (source-only, no broker/orders).

Keep v0.9.5 native strategy logic and revive market research on an EMPTY
new PAPER epoch, without importing/restoring old PC fills or changing data.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]/"app/src/main/python"

def source(name):
    return (ROOT/name).read_text(encoding="utf8")


class FakeStop:
    stopped=False
    def is_set(self):return self.stopped


class FakeThread:
    alive=True
    def is_alive(self):return self.alive


class FakeWorker:
    instances=[]
    def __init__(self,root):
        self.root=Path(root)
        self._stop=FakeStop()
        self._thread=FakeThread()
        FakeWorker.instances.append(self)


class ResetRuntimeRecovery(unittest.TestCase):
    def test_native_pre_reset_trend_entry_and_independent_preplans_restored(self):
        body=source("market_trend_runtime_v043.py")
        preplan=source("market_trend_preplan_v099.py")
        self.assertIn("def _preclose_candidates(self,market,now):",body)
        self.assertIn("self._prune_evaluation_cache(now)",body)
        self.assertIn("self._prechecked[entry]=rsi",body)
        self.assertIn("self.standalone_wallet.buy(",body)
        self.assertIn("def _refresh_rank(self,market):",body)
        self.assertIn("def _process_holds(self,market,now):",body)
        self.assertIn('self._mt99_preplans=PreparedCoinTrend(self)',preplan)
        self.assertIn('self._mt081_stock_preplans=PreparedStockTrend(self)',preplan)
        self.assertIn("native_start(self)",preplan)
        self.assertIn("worker.start()",preplan)
        self.assertNotIn("from market_trend_closed_entry_v107 import (",body)

    def test_at_coin_ma_gmo_cw_trend_native_gate_survives_paper_reset(self):
        body=source("android_runtime_v078.py")
        for label in (
            "_install_at_policy_v091()",
            "_install_unified_recovery_v092(_core)",
            "_install_stock_quote_truth_v066(_core)",
            "_install_trend_preplan_v099()",
        ):
            self.assertIn(label,body)
        self.assertNotIn("_install_reference_score_gate_v107()",body)
        self.assertNotIn("_install_entry_plan_hooks_v107(_core)",body)
        self.assertNotIn("_install_nl1118_trend()",body)
        self.assertNotIn("_install_nl1118_reference()",body)

    def test_dead_score_worker_restarts_without_invented_market_score(self):
        import market_research_v033 as score
        FakeWorker.instances.clear()
        with tempfile.TemporaryDirectory() as location:
            key=str(Path(location).resolve())
            with score._WORKERS_LOCK:
                previous=score._WORKERS.pop(key,None)
            try:
                with patch.object(score,"_MarketWorker",FakeWorker):
                    worker=score.ensure_worker(location)
                    self.assertIs(worker,score.ensure_worker(location))
                    self.assertEqual(len(FakeWorker.instances),1)
                    worker._stop.stopped=True
                    revived=score.ensure_worker(location)
                    self.assertIsNot(worker,revived)
                    revived._thread.alive=False
                    again=score.ensure_worker(location)
                    self.assertIsNot(revived,again)
                    self.assertEqual(len(FakeWorker.instances),3)
            finally:
                with score._WORKERS_LOCK:
                    score._WORKERS.pop(key,None)
                    if previous is not None:score._WORKERS[key]=previous

    def test_empty_score_database_creates_new_verified_events(self):
        import market_research_v033 as score
        with tempfile.TemporaryDirectory() as location:
            path=Path(location)/"market_research_v033.sqlite3"
            self.assertFalse(path.exists())
            db=score.ScoreLedger(path)
            try:
                self.assertIsNone(db.snapshot("stock")["score"])
                self.assertIsNone(db.snapshot("coin")["score"])
                self.assertTrue(db.record_score("coin",12.5,1795000000,{},{
                    "source":"SYNTHETIC_TEST_FIXTURE",
                    "status":"SYNTHETIC_TEST",
                    "coverage":1.0
                }))
                self.assertEqual(db.snapshot("coin")["score"],12.5)
                self.assertIsNone(db.snapshot("stock")["score"])
                with sqlite3.connect(path) as c:
                    self.assertEqual(c.execute(
                        "SELECT COUNT(*) FROM score_events").fetchone()[0],1)
            finally:
                db.close()

    def test_market_score_is_bootstrapped_before_heavy_empty_history_read(self):
        body=source("android_runtime_v078.py")
        lo=body.index("def _hub_records_v078(self,for_ui=False):")
        hi=body.index("def _stock_at_ledger_v19(self):",lo)
        segment=body[lo:hi]
        self.assertIn("from market_research_v033 import ensure_worker",segment)
        self.assertLess(segment.index("ensure_worker(self.root)"),
                        segment.index("raw=_ORIG_HUB_RECORDS(self)"))
        self.assertIn("out['market_research']=offer_records(self.root,out)",segment)
        self.assertIn("market_score_worker_bootstrap_001",segment)

    def test_no_second_reset_triggered_by_score_or_old_pc_migration(self):
        fresh=source("fresh_epoch_reset_v106.py")
        self.assertIn('if existing and existing["state"] == "COMMITTED":',fresh)
        self.assertIn('"status": "ALREADY_COMMITTED"',fresh)
        self.assertNotIn("reset_once_json(",source("android_runtime_v078.py"))
        # Old PC migration is forbidden, but no additional trade-entry veto
        # is allowed to be mistaken for successful market analysis.
        self.assertIn("def reset_once_json(root):",fresh)


if __name__=="__main__":
    unittest.main()
