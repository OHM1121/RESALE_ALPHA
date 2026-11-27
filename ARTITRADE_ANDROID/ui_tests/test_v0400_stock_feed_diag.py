"""0.4.0 (#155) - measurement only: ledger-gate long-hold stacks, scheduling lag, disconnect diagnostics."""
import json
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

import d_storage
import stock_feed_diag_v0400 as diag

BASE = Path(__file__).resolve().parents[1]
PY = BASE / "app/src/main/python"


def _gate():
    gate = d_storage.TradePriorityRLock()
    return gate


class HoldReport(unittest.TestCase):
    def setUp(self):
        diag._GATES.clear()
        diag._REPORTED.clear()
        diag._LINES[0] = 0
        self.tmp = tempfile.TemporaryDirectory()
        (Path(self.tmp.name) / "stock").mkdir()
        diag._ROOT[0] = self.tmp.name

    def tearDown(self):
        diag._ROOT[0] = None
        self.tmp.cleanup()

    def _hold(self, gate, seconds_ago, name="artitrade-fix14-higher-frame-rest"):
        ready, release = threading.Event(), threading.Event()

        def work():
            gate.acquire()
            gate._started = time.perf_counter() - seconds_ago      # pretend it has been held that long
            ready.set()
            release.wait(5)
            gate.release()
        t = threading.Thread(target=work, name=name)
        t.start()
        ready.wait(5)
        return t, release

    def _lines(self):
        f = Path(self.tmp.name) / "stock" / diag.SAMPLE_FILE
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []

    def test_long_hold_is_reported_once_with_holder_and_stack(self):
        gate = _gate()
        diag.register(gate)
        t, release = self._hold(gate, 6.0)
        try:
            self.assertEqual(diag.check_gates(), 1)
            self.assertEqual(diag.check_gates(), 0)                  # same hold -> not repeated
        finally:
            release.set()
            t.join(5)
        rows = self._lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["holder"], "artitrade-fix14-higher-frame-rest")
        self.assertGreaterEqual(rows[0]["held_s"], 5.9)
        self.assertTrue(any(f.endswith(":work") for f in rows[0]["stack"]), rows[0]["stack"])

    def test_short_hold_is_not_reported_and_a_new_hold_is(self):
        gate = _gate()
        diag.register(gate)
        t, release = self._hold(gate, 1.0)
        try:
            self.assertEqual(diag.check_gates(), 0)
        finally:
            release.set()
            t.join(5)
        t, release = self._hold(gate, 7.0)
        try:
            self.assertEqual(diag.check_gates(), 1)
        finally:
            release.set()
            t.join(5)
        self.assertEqual(len(self._lines()), 1)

    def test_line_cap(self):
        gate = _gate()
        diag.register(gate)
        diag._LINES[0] = diag.MAX_LINES
        t, release = self._hold(gate, 9.0)
        try:
            self.assertEqual(diag.check_gates(), 0)
        finally:
            release.set()
            t.join(5)

    def test_idle_gate_and_missing_root_never_raise(self):
        gate = _gate()
        diag.register(gate)
        self.assertEqual(diag.check_gates(), 0)
        diag._ROOT[0] = None
        t, release = self._hold(gate, 9.0)
        try:
            diag.check_gates()                                       # no root and no bridge -> silently nothing
        finally:
            release.set()
            t.join(5)


class Suffix(unittest.TestCase):
    def test_suffix_has_gate_queue_and_scheduling_lag(self):
        gate = _gate()
        runner = types.SimpleNamespace(store=types.SimpleNamespace(lock=gate), _feed_high_water=7, _feed_overflows=2,
                                       _feed_message_queue=__import__("queue").Queue(),
                                       _tick_queue=__import__("queue").Queue())
        text = diag.disconnect_suffix(runner)
        for token in (" | DIAG ", "gate_now=", "long_holds=", "feedq=0 hi=7 overflow=2", "tickq=0", "sched_lag_max_ms="):
            self.assertIn(token, text)

    def test_suffix_never_raises(self):
        self.assertTrue(isinstance(diag.disconnect_suffix(object()), str))
        self.assertTrue(isinstance(diag.disconnect_suffix(None), str))


class Install(unittest.TestCase):
    def test_install_wraps_health_only_for_a_disconnect_and_registers_gates(self):
        diag._INSTALLED[0] = False
        diag._GATES.clear()

        class FakeGate:
            def __init__(self):
                self.native = True

        class FakeRunner:
            def __init__(self):
                self.seen = []
                self.store = types.SimpleNamespace(lock=FakeGate())

            def _health(self, state, details=""):
                self.seen.append((state, details))

        try:
            diag.install(d_storage=types.SimpleNamespace(TradePriorityRLock=FakeGate),
                         d_live=types.SimpleNamespace(LivePaperRunner=FakeRunner), threads=False)
            g = FakeGate()
            self.assertTrue(g.native)
            self.assertIn(g, diag._GATES)
            r = FakeRunner()
            r._health("실시간 연결됨", "ok")
            r._health("연결 끊김 - 재연결 대기", "ConnectionClosedError")
            self.assertEqual(r.seen[0], ("실시간 연결됨", "ok"))
            self.assertTrue(r.seen[1][1].startswith("ConnectionClosedError | DIAG "))
        finally:
            diag._INSTALLED[0] = False
            diag._GATES.clear()

    def test_runtime_installs_it_without_letting_a_failure_stop_the_app(self):
        src = (PY / "android_runtime_v078.py").read_text(encoding="utf-8")
        self.assertIn("_install_stock_feed_diag_v0400()", src)
        self.assertIn("except Exception as _diag_exc:", src)

    def test_probe_samples_lag(self):
        diag._LAG.clear()
        diag._LAG.extend([1.0, 2.0, 50.0])
        self.assertEqual(diag.lag_stats()[0], 50.0)


if __name__ == "__main__":
    unittest.main()
