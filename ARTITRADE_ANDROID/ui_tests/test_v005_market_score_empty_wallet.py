"""ARTITRADE v005 market-score collection, isolated display, empty-paper wallet.

All inputs synthetic. Original score_events is the only historical trade-score
attribution authority. No test invokes a broker, LIVE orders or real cash.
"""
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import market_research_v033 as score


class MarketScoreEmptyWallet(unittest.TestCase):
    def test_zero_holdings_still_records_two_scores_with_no_fake_return(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"market_research_v033.sqlite3"
            ledger=score.ScoreLedger(path)
            try:
                t=int(time.time())
                for market,value in (("stock",23.4),("coin",-11.7)):
                    self.assertTrue(ledger.record_score(
                        market,value,t-65,{},{"source":"SYNTHETIC_005","coverage":1}))
                    self.assertTrue(ledger.record_score(
                        market,value,t-30,{},{"source":"SYNTHETIC_005","coverage":1}))
                    ledger.observe_positions(market,{},t)
                    snap=ledger.snapshot(market)
                    self.assertEqual(snap["score"],value)
                    self.assertEqual(snap["active_count"],0)
                    self.assertTrue(all(row["position_segments"]==0 for row in snap["bands"]))
                    self.assertTrue(all(row["avg_change_pp"] is None for row in snap["bands"]))
                    self.assertGreater(sum(row["observed_seconds"] for row in snap["bands"]),0)
                    self.assertEqual(ledger.score_at(market,t),value)
                    self.assertEqual(ledger.conn.execute(
                        "SELECT COUNT(*) FROM cohort_positions").fetchone()[0],0)
            finally:
                ledger.close()

    def test_compact_display_does_not_need_position_or_execution_cache(self):
        band={"band":"20~39","position_segments":0,"avg_change_pp":None,
              "observed_seconds":60.0}
        current={"stock":{"score":30.,"asof":int(time.time()),"stale":False,
                          "bands":[band],"factors":[],"status":"정상",
                          "recent_scores":[{"ts":0}]*720},
                 "coin":{"score":-5.,"asof":int(time.time()),"stale":False,
                         "bands":[band],"factors":[],"status":"정상"}}
        fake=SimpleNamespace(snapshot=lambda:current)
        with patch.object(score,"ensure_worker",return_value=fake):
            current_score=score.compact_snapshot("/tmp/v005_synthetic")
        self.assertEqual(current_score["stock"]["score"],30.)
        self.assertEqual(current_score["coin"]["score"],-5.)
        self.assertNotIn("recent_scores",current_score["stock"])
        self.assertEqual(current_score["stock"]["bands"][0]["avg_change_pp"],None)
        self.assertEqual(current_score["stock"]["bands"][0]["position_segments"],0)

    def test_failed_feed_displays_unavailable_not_old_positive_score(self):
        fake=SimpleNamespace(snapshot=lambda:{"stock":{"score":50.,"stale":True},
                                              "coin":{"score":-50.,"stale":True}})
        with patch.object(score,"ensure_worker",return_value=fake):
            snapshot=score.compact_snapshot("/tmp/v005_unavailable")
        for market in ("stock","coin"):
            self.assertIsNone(snapshot[market]["score"])
            self.assertIsNone(snapshot[market]["active_band"])

    def test_domestic_and_global_public_factor_reads_are_isolated(self):
        names=set()
        lock=threading.Lock()
        def public(url,params):
            with lock:names.add(threading.current_thread().name)
            time.sleep(.012)
            return {"chart":{"result":[{"indicators":{"quote":[{"close":[100,101,102]}]}}]}}
        def domestic(name):return 0.5 if name=="KOSPI" else -0.2
        with patch.object(score,"_public_json",side_effect=public),             patch.object(score,"_domestic_index_fallback",side_effect=domestic):
            result=score._fetch_stock()
        self.assertIsInstance(result["score"],float)
        self.assertGreaterEqual(len(names),2)
        self.assertIn("KOSPI",result["received"])
        self.assertIn("KOSDAQ",result["received"])

    def test_history_uses_only_sqlite_event_time_not_new_view_preference(self):
        src=Path(__file__).resolve().parents[1]/"app/src/main"
        lookup=(src/"java/com/artitrade/mobile/MarketResearchScoreLookup.java").read_text()
        activity=(src/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
        bridge=(src/"java/com/artitrade/mobile/StrategyRuntimeBridge.java").read_text()
        service=(src/"java/com/artitrade/mobile/UnifiedTradingService.java").read_text()
        runtime=(src/"python/android_runtime_v078.py").read_text()
        self.assertIn("FROM score_events",lookup)
        self.assertIn("scores.floorEntry(at)",lookup)
        self.assertIn("market_score_plane_v005",activity)
        self.assertIn('scoreSnapshotV005()',bridge)
        self.assertIn('artitrade_market_score_v005',service)
        self.assertIn("def score_snapshot_v005():",runtime)


if __name__=="__main__":
    unittest.main()
