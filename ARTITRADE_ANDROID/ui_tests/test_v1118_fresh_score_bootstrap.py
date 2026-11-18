"""Source-only regression for empty new PAPER epoch and score worker lifecycle.

No network, real broker/order or Android files. Only temporary SQLite files.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import market_research_v033 as score
from new_legacy_1118_policy import validate_stock_score


class _FakeEvent:
    def __init__(self, stopped=False):
        self.stopped = stopped

    def is_set(self):
        return self.stopped


class _FakeThread:
    def __init__(self, alive=True):
        self.alive = alive

    def is_alive(self):
        return self.alive


class _FakeWorker:
    instances = []

    def __init__(self, root):
        self.root = Path(root)
        self._stop = _FakeEvent()
        self._thread = _FakeThread()
        self.records = None
        self.instances.append(self)

    def offer(self, records):
        self.records = records

    def snapshot(self):
        return {
            "stock": {"score": None, "status": "검증된 시세 대기", "stale": True},
            "coin": {"score": None, "status": "검증된 시세 대기", "stale": True},
        }


class FreshScoreBootstrap(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        with score._WORKERS_LOCK:
            saved = dict(score._WORKERS)
            score._WORKERS.clear()
        self.addCleanup(self._restore, saved)

    @staticmethod
    def _restore(saved):
        with score._WORKERS_LOCK:
            score._WORKERS.clear()
            score._WORKERS.update(saved)

    def test_recreate_dead_worker_without_rewriting_ledger(self):
        _FakeWorker.instances.clear()
        with mock.patch.object(score, "_MarketWorker", _FakeWorker):
            one = score.ensure_worker(self.root)
            self.assertIs(one, score.ensure_worker(self.root))
            self.assertEqual(len(_FakeWorker.instances), 1)
            one._stop.stopped = True
            two = score.ensure_worker(self.root)
            self.assertIsNot(one, two)
            two._thread.alive = False
            three = score.ensure_worker(self.root)
            self.assertIsNot(two, three)
            self.assertEqual(len(_FakeWorker.instances), 3)
            result = score.offer_records(self.root, {"coin": {"general": {}}})
            self.assertIsNone(result["stock"]["score"])
            self.assertIsNone(result["coin"]["score"])
            self.assertEqual(three.records, {"coin": {"general": {}}})

    def test_fresh_score_ledger_rebuilds_from_zero_without_old_pc(self):
        db = self.root / "market_research_v033.sqlite3"
        self.assertFalse(db.exists())
        ledger = score.ScoreLedger(db)
        try:
            self.assertIsNone(ledger.snapshot("stock")["score"])
            self.assertIsNone(ledger.snapshot("coin")["score"])
            ledger.record_score(
                "coin", 17.0, 1_795_000_000, {},
                {"source": "SYNTHETIC_TEST", "status": "정상", "coverage": 1.0},
            )
            self.assertEqual(ledger.snapshot("coin")["score"], 17.0)
            self.assertIsNone(ledger.snapshot("stock")["score"])
        finally:
            ledger.close()

    def test_missing_score_is_not_positive_order_permission(self):
        for absent in (None, "", float("nan"), -1, 0):
            self.assertFalse(validate_stock_score(absent))
        self.assertTrue(validate_stock_score(1))
        self.assertTrue(validate_stock_score(100))

    def test_hub_bootstraps_score_before_heavy_ledger_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        src = (root / "app/src/main/python/android_runtime_v078.py").read_text()
        start = src.index("def _hub_records_v078(self,for_ui=False):")
        end = src.index("def _stock_at_ledger_v19(self):", start)
        segment = src[start:end]
        self.assertIn("from market_research_v033 import ensure_worker", segment)
        self.assertLess(segment.index("ensure_worker(self.root)"),
                        segment.index("raw=_ORIG_HUB_RECORDS(self)"))
        self.assertIn("market_score_worker_start_v1118", segment)


if __name__ == "__main__":
    unittest.main()
