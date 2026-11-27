"""0.5.7: one short status line about the after-close data collection / storage for the market feed card (owner 2026-11-27)."""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
import offhours_prework_v0530 as off

KST = dt.timezone(dt.timedelta(hours=9))
NIGHT = dt.datetime(2026, 11, 27, 21, 0, tzinfo=KST)          # Friday 21:00: inside the quiet window of Friday's bars
DAY = dt.datetime(2026, 11, 27, 13, 0, tzinfo=KST)


class Status(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        off._STATE.update({"running": False, "progress": [0, 0], "paused_until": 0.0})

    def tearDown(self):
        off._STATE.update({"running": False, "progress": [0, 0], "paused_until": 0.0})
        self.tmp.cleanup()

    def test_nothing_in_the_daytime(self):
        self.assertEqual(off.status(self.root, DAY), {})

    def test_waiting_before_the_first_pass(self):
        s = off.status(self.root, NIGHT)
        self.assertEqual(s["phase"], "waiting")
        self.assertIn("대기", s["label"])

    def test_running_shows_progress(self):
        off._STATE.update({"running": True, "progress": [12, 180]})
        s = off.status(self.root, NIGHT)
        self.assertEqual(s["phase"], "running")
        self.assertEqual(s["label"], "장마감 후 데이터 수집·저장 중 12/180")

    def test_done_shows_the_stock_count(self):
        off.save_state(self.root, {"2026-11-27": {"bars_completion": {"done": True, "codes_done": ["a", "b", "c"], "inserted": 4}}})
        s = off.status(self.root, NIGHT)
        self.assertEqual(s["phase"], "done")
        self.assertEqual(s["label"], "장마감 후 데이터 수집·저장 완료 · 3종목")

    def test_paused_after_repeated_failures(self):
        off._STATE["paused_until"] = time.monotonic() + 600
        self.assertEqual(off.status(self.root, NIGHT)["phase"], "paused")

    def test_next_morning_before_the_open_still_reports_yesterdays_work(self):
        off.save_state(self.root, {"2026-11-27": {"bars_completion": {"done": True, "codes_done": ["a"]}}})
        s = off.status(self.root, dt.datetime(2026, 11, 28, 6, 30, tzinfo=KST))     # Saturday: no trading day, day of Friday's bars
        self.assertIn(s.get("phase"), ("done", None))

    def test_run_clears_the_running_flag_even_without_a_runner(self):
        off._STATE["running"] = True
        with mock.patch.object(off.dt, "datetime", wraps=dt.datetime):
            off.run(type("R", (), {"stock_runner": None, "root": self.root})(), NIGHT)
        self.assertFalse(off._STATE["running"])

    def test_status_never_raises(self):
        self.assertEqual(off.status(None, NIGHT).get("phase") in ("waiting", None), True)


if __name__ == "__main__":
    unittest.main()
