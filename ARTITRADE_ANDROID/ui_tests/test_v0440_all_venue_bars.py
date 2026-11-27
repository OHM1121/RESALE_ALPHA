"""0.4.4 (#159): combined KRX+NXT candles - stage 1 read-only measurement."""
import datetime as dt
import sqlite3
import tempfile
import time
import types
import unittest
from pathlib import Path

import stock_all_venue_bars_v0440 as probe

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 27, 20, 3, tzinfo=KST)          # a Friday, inside the probe window


def row(ts, o, h, lo, c, v):
    return {"cntr_tm": ts, "open_pric": "+%d" % o, "high_pric": "+%d" % h, "low_pric": "-%d" % lo, "cur_prc": "+%d" % c,
            "trde_qty": str(v)}


def packet(rows):
    return {"stk_min_pole_chart_qry": rows, "return_code": 0}


class Api:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows or [], error, []

    def post(self, api_id, path, body, cont="N", key=""):
        self.calls.append((api_id, path, dict(body)))
        if self.error:
            raise RuntimeError(self.error)
        return packet(self.rows)


class Parse(unittest.TestCase):
    def test_all_sessions_are_kept_and_the_forming_candle_is_not(self):
        rows = [row("20261127080000", 100, 101, 99, 100, 5), row("20261127084500", 100, 102, 100, 101, 7),
                row("20261127093000", 101, 103, 101, 102, 9), row("20261127153500", 102, 102, 100, 100, 4),
                row("20261127194000", 100, 100, 98, 98, 3), row("20261127200000", 98, 98, 97, 97, 1)]
        got = probe.parse_rows(packet(rows), "5m", NOW)
        self.assertEqual(sorted(got), ["20261127080000", "20261127084500", "20261127093000", "20261127153500", "20261127194000"])
        self.assertEqual(got["20261127080000"], (100, 101, 99, 100, 5))                    # signs of the broker fields stripped
        forming = probe.parse_rows(packet([row("20261127200000", 98, 98, 97, 97, 1)]), "5m", NOW.replace(hour=20, minute=3))
        self.assertEqual(forming, {})                                                      # 20:00-20:05 is still forming at 20:03

    def test_invalid_rows_are_dropped(self):
        bad = [row("20261127093000", 0, 1, 1, 1, 5), row("20261127093500", 100, 99, 98, 100, 5),
               row("20261127094000", 100, 101, 99, 100, 0), {"cntr_tm": "x"}, row("20261127094500", 100, 101, 99, 100, 5)]
        self.assertEqual(list(probe.parse_rows(packet(bad), "5m", NOW)), ["20261127094500"])

    def test_fetch_asks_for_the_combined_code_and_reports_errors(self):
        api = Api([row("20261127093000", 100, 101, 99, 100, 5)])
        rows, error = probe.fetch(api, "080220", "30m", NOW)
        self.assertIsNone(error)
        self.assertEqual(api.calls[0][0:2], ("ka10080", "/api/dostk/chart"))
        self.assertEqual(api.calls[0][2]["stk_cd"], "080220_AL")
        self.assertEqual(api.calls[0][2]["tic_scope"], "30")
        rows, error = probe.fetch(Api(error="[2:invalid code]"), "080220", "5m", NOW)
        self.assertIsNone(rows)
        self.assertIn("invalid code", error)
        self.assertEqual(probe.fetch(Api([]), "080220", "5m", NOW), (None, "NO_USABLE_ROWS"))


class Record(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "stock").mkdir()
        self.main = root / "stock" / "ARTITRADE_D.sqlite3"
        with sqlite3.connect(self.main) as cx:
            cx.execute("CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open INT,high INT,low INT,close INT,volume INT,source TEXT)")
            cx.execute("INSERT INTO bars VALUES('080220','KOSPI','5m','20261127093000',101,103,101,102,4,'K')")
            cx.execute("INSERT INTO bars VALUES('080220','KOSPI','5m','20261127093500',102,104,102,103,4,'K')")
        self.root = root

    def tearDown(self):
        self.tmp.cleanup()

    def test_compare_and_log_are_written_to_the_probe_file_only(self):
        rows = {"20261127080000": (100, 101, 99, 100, 5), "20261127093000": (101, 103, 101, 102, 8),
                "20261127093500": (102, 105, 102, 104, 8), "20261127194000": (100, 100, 98, 98, 3)}
        probe.record(self.root / "stock" / probe.FILE, self.main, "080220", "5m", rows, None, NOW)
        with sqlite3.connect(self.root / "stock" / probe.FILE) as cx:
            log = cx.execute("SELECT ok,rows,hours,regular_same,regular_diff,regular_volume_ratio,ma_all,ma_krx FROM probe_log").fetchone()
            self.assertEqual(log[:6], (1, 4, "08,09,19", 1, 1, 2.0))
            self.assertTrue(log[6].startswith("SMA_HISTORY_REQUIRED") and log[7].startswith("SMA_HISTORY_REQUIRED"))
            self.assertEqual(cx.execute("SELECT COUNT(*) FROM bars_all").fetchone()[0], 4)
        with sqlite3.connect(self.main) as cx:                                         # the strategies' table is untouched
            self.assertEqual(cx.execute("SELECT COUNT(*) FROM bars").fetchone()[0], 2)
        probe.record(self.root / "stock" / probe.FILE, self.main, "080220", "5m", None, "boom", NOW)
        with sqlite3.connect(self.root / "stock" / probe.FILE) as cx:
            self.assertEqual(cx.execute("SELECT ok,error FROM probe_log ORDER BY id DESC").fetchone(), (0, "boom"))

    def test_ma_verdict_runs_the_owner_rule_on_enough_bars(self):
        closes = [100.0 + (i % 7) for i in range(130)]
        self.assertNotIn("SMA_HISTORY_REQUIRED", probe.ma_verdict(closes))


class Cycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        (Path(self.tmp.name) / "stock").mkdir()
        probe._STATE.update(failures=0, paused_until=0.0)

    def tearDown(self):
        self.tmp.cleanup()

    def runtime(self, api, backlog=0, scope=()):
        runner = types.SimpleNamespace(api=api, _tick_queue=types.SimpleNamespace(qsize=lambda: backlog))
        coordinator = types.SimpleNamespace(_candidates={"stock": {c: 1 for c in scope}})
        return types.SimpleNamespace(stock_runner=runner, root=self.tmp.name, market_trend=coordinator)

    def test_cycle_probes_the_default_and_scope_symbols_without_sleeping_long(self):
        api = Api([row("20261127093000", 100, 101, 99, 100, 5)])
        ok = probe.cycle(self.runtime(api, scope=("005930", "080220")), NOW, sleep=lambda s: None)
        self.assertEqual(ok, 6)                                                          # 3 symbols x 2 frames
        self.assertEqual({c[2]["stk_cd"] for c in api.calls}, {"080220_AL", "466100_AL", "005930_AL"})

    def test_outside_the_window_or_with_a_backed_up_feed_nothing_is_requested(self):
        api = Api([row("20261127093000", 100, 101, 99, 100, 5)])
        self.assertEqual(probe.cycle(self.runtime(api), NOW.replace(day=28), sleep=lambda s: None), 0)       # Saturday
        self.assertEqual(probe.cycle(self.runtime(api), NOW.replace(hour=21), sleep=lambda s: None), 0)
        self.assertEqual(probe.cycle(self.runtime(api, backlog=30), NOW, sleep=lambda s: None), 0)
        self.assertEqual(api.calls, [])

    def test_three_failures_pause_the_probe(self):
        api = Api(error="[2:no]")
        self.assertEqual(probe.cycle(self.runtime(api), NOW, sleep=lambda s: None), 0)
        self.assertEqual(len(api.calls), 3)
        before = len(api.calls)
        probe.cycle(self.runtime(api), NOW, sleep=lambda s: None)
        self.assertEqual(len(api.calls), before)                                          # paused
        self.assertGreater(probe._STATE["paused_until"], time.monotonic())

    def test_install_is_wired_and_idempotent(self):
        text = (PY / "android_runtime_v078.py").read_text("utf8")
        self.assertIn("from stock_all_venue_bars_v0440 import install", text)
        probe._STATE["installed"] = False
        probe.install(threads=False)
        probe.install(threads=False)
        self.assertTrue(probe._STATE["installed"])
        probe._STATE["installed"] = False


if __name__ == "__main__":
    unittest.main()
