"""0.4.6 (#161): UI ledger snapshots read without the trading gate and give the same output; fast timestamp parse is exact."""
import datetime as dt
import json
import random
import sqlite3
import tempfile
import threading
import types
import unittest
from pathlib import Path

import d_storage
import reference_policy
import ui_ledger_ro_v0460 as ro

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


def fill(db, table, rows, **fixed):
    """Insert rows into a vendor table giving every NOT NULL column without a default a neutral value."""
    cols = list(db.execute("PRAGMA table_info(%s)" % table))
    for i in range(rows):
        values = {}
        for c in cols:
            name, kind, notnull, default, pk = c[1], str(c[2]).upper(), c[3], c[4], c[5]
            if pk and "INT" in kind:
                continue
            if name in fixed:
                values[name] = fixed[name]
            elif notnull and default is None and name in ("features", "payload", "details", "meta", "state_json"):
                values[name] = "{}"
            elif notnull and default is None:
                values[name] = i if ("INT" in kind or "REAL" in kind) and name.endswith("_id") else (
                    0 if ("INT" in kind or "REAL" in kind) else "x%d" % i)
        names = ",".join(values)
        db.execute("INSERT INTO %s(%s) VALUES(%s)" % (table, names, ",".join("?" * len(values))) if values
                   else "INSERT INTO %s DEFAULT VALUES" % table, tuple(values.values()))


class UiLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = d_storage.DStore(self.tmp.name)
        with self.store.connect() as db:
            fill(db, "positions", 2, status="OPEN", tf="5m", qty=3, entry_price=100, last_price=110)
            fill(db, "executions", 4, side="SELL", qty=1, gross=120, fees=1, tax=1)
            fill(db, "signals", 3, tf="5m", code="005930")
            fill(db, "pending", 1)
        self.stub = types.SimpleNamespace(stock_runner=types.SimpleNamespace(store=self.store))
        import android_live_bridge as bridge
        self.vendor = bridge.Runtime._stock_at_ledger

    def tearDown(self):
        self.tmp.cleanup()

    def test_same_output_as_the_vendor_function(self):
        a = json.dumps(self.vendor(self.stub), sort_keys=True, default=str)
        b = json.dumps(ro.stock_at_ledger_ro(self.stub), sort_keys=True, default=str)
        self.assertEqual(a, b)
        self.assertGreater(len(json.loads(a)["positions"]), 0)

    def test_no_runner_is_an_empty_dict(self):
        self.assertEqual(ro.stock_at_ledger_ro(types.SimpleNamespace(stock_runner=None)), {})

    def test_runs_while_another_thread_holds_the_gate_and_never_writes(self):
        held, release, result = threading.Event(), threading.Event(), {}

        def holder():
            self.store.lock.acquire()
            held.set()
            release.wait(10)
            self.store.lock.release()

        t = threading.Thread(target=holder)
        t.start()
        held.wait(2)
        try:
            worker = threading.Thread(target=lambda: result.setdefault("out", ro.stock_at_ledger_ro(self.stub)))
            worker.start()
            worker.join(5)
            self.assertFalse(worker.is_alive())                                   # not blocked by the gate
            self.assertIn("out", result)
        finally:
            release.set()
            t.join()
        with ro.ui_read(self.store) as c:
            with self.assertRaises(sqlite3.OperationalError):
                c.execute("CREATE TABLE should_not_exist(x)")

    def test_overlay_uses_the_read_only_reader_and_no_gate_in_the_ui_functions(self):
        text = (PY / "android_runtime_v078.py").read_text("utf8")
        self.assertIn("_ORIG_STOCK_AT_LEDGER = _ui_ro.stock_at_ledger_ro", text)
        self.assertEqual(text.count("with _ui_read_ctx(store) as c:"), 2)
        self.assertEqual(text.count("with store.lock,store.connect() as c:"), 1)   # only the fallback inside _ui_read_ctx


class FastParse(unittest.TestCase):
    def test_exactly_the_original_for_every_kind_of_string(self):
        original = reference_policy.parse_timestamp
        fast = ro.make_fast_parse(original)
        t = dt.datetime(2025, 12, 1)
        while t < dt.datetime(2026, 12, 31):
            stamp = t.strftime("%Y%m%d%H%M%S")
            for length in (14, 13, 12, 11, 10, 9, 8):
                self.assertEqual(fast(stamp[:length]), original(stamp[:length]), stamp[:length])
            self.assertEqual(fast(stamp + "000"), original(stamp + "000"))
            t += dt.timedelta(minutes=97)
        random.seed(7)
        for _ in range(4000):
            s = "".join(random.choice("0123456789") for _ in range(random.choice((8, 10, 12, 13, 14, 15))))
            self.assertEqual(fast(s), original(s), s)
        for s in ("", "abc", "20261301093000", "20261127096000", "20261127093061", "2026112709300a", "20260230000000", "  "):
            self.assertEqual(fast(s), original(s), s)

    def test_install_rebinds_every_module_that_holds_the_original(self):
        import sys
        original = reference_policy.parse_timestamp
        holder = types.ModuleType("zz_holder_v0460")
        holder.parse_timestamp = original
        sys.modules["zz_holder_v0460"] = holder
        try:
            changed = ro.install_fast_parse()
            self.assertGreaterEqual(changed, 2)
            self.assertTrue(getattr(holder.parse_timestamp, "_v0460_fast", False))
            self.assertTrue(getattr(reference_policy.parse_timestamp, "_v0460_fast", False))
            self.assertEqual(ro.install_fast_parse(), 0)                          # idempotent
            self.assertEqual(holder.parse_timestamp("20261127093000"), dt.datetime(2026, 11, 27, 9, 30))
        finally:
            sys.modules.pop("zz_holder_v0460", None)
            reference_policy.parse_timestamp = original                           # leave the module as other tests expect


if __name__ == "__main__":
    unittest.main()
