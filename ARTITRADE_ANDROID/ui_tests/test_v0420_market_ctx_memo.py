"""0.4.2 (#157): market-wide daily statistics are memoised, exact, bounded and kept off the ledger gate."""
import copy
import sqlite3
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import at_integrated_market_v10 as v10
import at_integrated_market_v11 as v11
import market_ctx_memo_v0420 as memo

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


def make_db(path, days=24, codes=40, source="KRX"):
    cx = sqlite3.connect(path)
    cx.row_factory = sqlite3.Row
    cx.execute("""CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open INT,high INT,low INT,close INT,
                  volume INT,source TEXT,PRIMARY KEY(code,frame,ts))""")
    day = datetime(2026, 10, 1)
    rows = []
    n = 0
    while n < days:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        n += 1
        for i in range(codes):
            price = 1000 + i * 10 + n * (1 + (i % 3)) + (7 if n % 2 else 0) * (i % 5)
            rows.append(("%06d" % (i + 1), "KOSPI", "day", day.strftime("%Y%m%d") + "000000",
                         price, price, price, price, 1000, source))
    cx.executemany("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    cx.commit()
    return cx, day.strftime("%Y%m%d")


class Counting:
    def __init__(self, fn):
        self.fn = fn
        self.calls = 0

    def __call__(self, db, market, ts):
        self.calls += 1
        return self.fn(db, market, ts)


class Memo(unittest.TestCase):
    def setUp(self):
        memo.clear()
        memo._DB_PATH[0] = None
        memo._ORIG.clear()
        for k in memo._STATS:
            memo._STATS[k] = 0
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "d.sqlite3")
        self.cx, self.last = make_db(self.path)
        self.later = (datetime.strptime(self.last, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")

    def tearDown(self):
        self.cx.close()
        self.tmp.cleanup()

    def test_result_is_identical_to_the_original_and_computed_once_per_market_day(self):
        for name, fn in (("market_state_for_session", v10.market_state_for_session),
                         ("market_context_v11", v11.market_context_v11)):
            counting = Counting(fn)
            wrapped = memo.memoised(name, counting)
            expected = fn(self.cx, "KOSPI", self.later + "093000")
            self.assertEqual(expected["status"], "ASSESSED", name)
            first = wrapped(self.cx, "KOSPI", self.later + "093000")
            second = wrapped(self.cx, "KOSPI", self.later + "151500")      # other time, same day
            self.assertEqual(first, expected)
            self.assertEqual(second, expected)
            self.assertEqual(counting.calls, 1, name)
            second["mutated"] = True                                      # callers cannot corrupt the cache
            self.assertNotIn("mutated", wrapped(self.cx, "KOSPI", self.later + "100000"))
            wrapped(self.cx, "KOSDAQ", self.later + "093000")             # other market -> own entry
            self.assertEqual(counting.calls, 2, name)
            wrapped(self.cx, "KOSPI", self.last + "093000")               # other day -> own entry
            self.assertEqual(counting.calls, 3, name)

    def test_invalid_input_is_never_cached(self):
        counting = Counting(v10.market_state_for_session)
        wrapped = memo.memoised("market_state_for_session", counting)
        for market, ts in (("", self.later + "0930"), ("KOSPI", ""), ("NYSE", self.later + "0930"), ("KOSPI", "abc")):
            wrapped(self.cx, market, ts)
            wrapped(self.cx, market, ts)
        self.assertEqual(counting.calls, 8)
        self.assertEqual(memo.stats()["entries"], 0)

    def test_unassessed_result_is_kept_only_briefly(self):
        counting = Counting(v10.market_state_for_session)
        wrapped = memo.memoised("market_state_for_session", counting)
        old = memo.TTL_BAD
        memo.TTL_BAD = 0.05
        try:
            few = datetime.strptime(self.last, "%Y%m%d") - timedelta(days=60)
            ts = few.strftime("%Y%m%d") + "093000"                        # too little history -> not ASSESSED
            first = wrapped(self.cx, "KOSPI", ts)
            self.assertNotEqual(first["status"], "ASSESSED")
            wrapped(self.cx, "KOSPI", ts)
            self.assertEqual(counting.calls, 1)
            time.sleep(0.08)
            wrapped(self.cx, "KOSPI", ts)
            self.assertEqual(counting.calls, 2)
        finally:
            memo.TTL_BAD = old

    def test_good_result_expires(self):
        counting = Counting(v11.market_context_v11)
        wrapped = memo.memoised("market_context_v11", counting)
        old = memo.TTL_GOOD
        memo.TTL_GOOD = 0.05
        try:
            wrapped(self.cx, "KOSPI", self.later + "093000")
            time.sleep(0.08)
            wrapped(self.cx, "KOSPI", self.later + "093000")
            self.assertEqual(counting.calls, 2)
        finally:
            memo.TTL_GOOD = old

    def test_daily_frame_recovery_is_exact_and_clears_the_cache(self):
        counting = Counting(v10.market_state_for_session)
        wrapped = memo.memoised("market_state_for_session", counting)
        wrapped(self.cx, "KOSPI", self.later + "093000")
        self.assertEqual(memo.stats()["entries"], 1)
        seen = []

        def native(store, engine, client, code, tf, *a, **kw):
            seen.append((tf, wrapped(self.cx, "KOSPI", self.later + "093000")["status"]))
            seen.append((tf, wrapped(self.cx, "KOSPI", self.later + "093000")["status"]))
            return {"state": "VERIFIED_RESTORED"}

        recover = memo._wrap_recover(native)
        self.assertEqual(recover(None, None, None, "000001", "day"), {"state": "VERIFIED_RESTORED"})
        self.assertEqual(counting.calls, 3)                    # 1 before + both calls inside the daily call recompute
        self.assertEqual(memo.stats()["entries"], 0)           # and the cache is cleared afterwards
        wrapped(self.cx, "KOSPI", self.later + "093000")
        wrapped(self.cx, "KOSPI", self.later + "093000")
        self.assertEqual(counting.calls, 4)
        recover(None, None, None, "000001", "30m")             # other frames keep using the cache
        self.assertEqual(counting.calls, 4)

    def test_original_error_is_not_swallowed_or_cached(self):
        def boom(db, market, ts):
            raise RuntimeError("source")
        wrapped = memo.memoised("market_state_for_session", boom)
        with self.assertRaises(RuntimeError):
            wrapped(self.cx, "KOSPI", self.later + "093000")
        self.assertEqual(memo.stats()["entries"], 0)

    def test_warm_uses_a_private_read_only_connection_and_never_the_ledger_gate(self):
        import d_storage
        gate = d_storage.TradePriorityRLock()
        memo._ORIG["market_state_for_session"] = v10.market_state_for_session
        memo._ORIG["market_context_v11"] = v11.market_context_v11
        memo._DB_PATH[0] = self.path
        held = threading.Event()
        release = threading.Event()

        def holder():
            gate.acquire()
            held.set()
            release.wait(5)
            gate.release()

        thread = threading.Thread(target=holder)
        thread.start()
        held.wait(2)
        try:
            started = time.perf_counter()
            done = memo.warm_once(self.later)                  # the gate is held by another thread the whole time
            self.assertEqual(done, 4)                          # 2 functions x 2 markets
            self.assertLess(time.perf_counter() - started, 4.0)
        finally:
            release.set()
            thread.join()
        counting = Counting(v10.market_state_for_session)
        wrapped = memo.memoised("market_state_for_session", counting)
        got = wrapped(self.cx, "KOSPI", self.later + "093000")
        self.assertEqual(counting.calls, 0)                    # served from the warmed entry
        self.assertEqual(got, v10.market_state_for_session(self.cx, "KOSPI", self.later + "093000"))

    def test_install_wraps_the_names_the_engine_uses_and_the_runtime_calls_it(self):
        engine = types.SimpleNamespace(market_state_for_session=v10.market_state_for_session,
                                       market_context_v11=v11.market_context_v11)
        fix14 = types.SimpleNamespace(recover_frame=lambda *a, **k: {"state": "x"})
        memo._STATE["installed"] = False
        try:
            memo.install(engine, fix14, threads=False)
            self.assertTrue(hasattr(engine.market_state_for_session, "__wrapped__"))
            self.assertTrue(hasattr(engine.market_context_v11, "__wrapped__"))
            self.assertTrue(hasattr(fix14.recover_frame, "__wrapped__"))
            memo.install(engine, fix14, threads=False)         # idempotent
            self.assertIs(engine.market_state_for_session.__wrapped__, v10.market_state_for_session)
        finally:
            memo._STATE["installed"] = False
        text = (PY / "android_runtime_v078.py").read_text("utf8")
        self.assertIn("from market_ctx_memo_v0420 import install", text)
        self.assertLess(text.index("market_ctx_memo_v0420"), text.index("start = _core.start"))


if __name__ == "__main__":
    unittest.main()
