"""0.3.1 regression (issue #146): frozen-engine supervisor, thread dump, lock-safe regime reads.

Evidence: coin error export 2026-11-22 20:35 (app 0.2.9). At 19:39 the Python engine froze (scan,
book, price-path and best-book recorders all silent for 55 minutes) while the process, the UI
thread and the market-score worker lived. The six watchdog feed restarts never ran because
restart() is synchronized on the same bridge monitor the blocked snapshot() holds."""
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import engine_stall_dump_v0310 as dump
import market_regime_v0290 as regime

BASE = Path(__file__).resolve().parents[1]
PY = BASE / "app/src/main/python"
JAVA = BASE / "app/src/main/java/com/artitrade/mobile"


def read(path):
    return path.read_text(encoding="utf-8")


class ThreadDump(unittest.TestCase):
    def test_names_blocked_and_busy_threads_without_taking_locks(self):
        lock = threading.Lock()
        lock.acquire()                               # held by the test thread, never released here
        started = threading.Event()

        def waiter():
            started.set()
            lock.acquire()                           # blocks like the frozen engine threads
        t = threading.Thread(target=waiter, name="artitrade-fake-engine-waiter", daemon=True)
        t.start()
        started.wait(2)
        time.sleep(0.1)
        try:
            text = dump.dump_threads()
        finally:
            lock.release()
            t.join(2)
        self.assertIn("artitrade-fake-engine-waiter", text)
        line = [l for l in text.splitlines() if l.startswith("artitrade-fake-engine-waiter")][0]
        self.assertIn(" LOCK", line)                 # flagged as waiting on a lock
        self.assertIn(">> lock.acquire()", line)     # and the exact source line it is parked on
        self.assertIn("test_v0310_engine_hang_diagnostics.py", line)   # shows WHERE it is parked
        self.assertTrue(text.splitlines()[1].startswith("threads="))
        self.assertLessEqual(len(text), dump.MAX_CHARS + 40)

    def test_an_idle_queue_reader_is_not_reported_as_a_lock_waiter(self):
        import queue
        q = queue.Queue()
        t = threading.Thread(target=q.get, name="artitrade-fake-queue-reader", daemon=True)
        t.start()
        time.sleep(0.1)
        try:
            rows = {r["name"]: r for r in dump.snapshot_threads()}
        finally:
            q.put(None)
            t.join(2)
        self.assertFalse(rows["artitrade-fake-queue-reader"]["lock_wait"])
        self.assertTrue(rows["artitrade-fake-queue-reader"]["idle"])

    def test_busy_threads_first_then_lock_waiters_then_idle(self):
        rows = [
            {"name": "idle-a", "daemon": True, "ident": 1, "frames": [("q.py", 1, "get")],
             "lock_wait": False, "idle": True},
            {"name": "blocked-b", "daemon": True, "ident": 2, "frames": [("x.py", 9, "acquire")],
             "lock_wait": True, "idle": True},
            {"name": "busy-c", "daemon": False, "ident": 3, "frames": [("y.py", 3, "compute")],
             "lock_wait": False, "idle": False},
        ]
        text = dump.format_dump(rows, header="h")
        order = [l.split(" ")[0] for l in text.splitlines()[2:]]
        # 0.3.6: running threads (the lock holders) first, then lock waiters, idle last
        self.assertEqual(order, ["busy-c", "blocked-b", "idle-a"])

    def test_output_is_bounded_and_never_raises(self):
        rows = [{"name": "t%03d" % i, "daemon": True, "ident": i,
                 "frames": [("module_%d.py" % i, 10, "function_name_%d" % i)] * 7,
                 "lock_wait": False, "idle": False} for i in range(400)]
        self.assertLessEqual(len(dump.format_dump(rows, 3000)), 3000)
        original = dump.snapshot_threads
        try:
            dump.snapshot_threads = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            self.assertTrue(dump.dump_threads().startswith("py-thread-dump-failed"))
        finally:
            dump.snapshot_threads = original

    def test_dump_has_no_local_variables_or_network_or_files(self):
        src = read(PY / "engine_stall_dump_v0310.py")
        for word in ("f_locals", "requests", "urllib", "socket", "open(", "sqlite3", ".write(", "f_globals"):
            self.assertNotIn(word, src)

    def test_runtime_exports_the_dump_for_the_bridge(self):
        src = read(PY / "android_runtime_v078.py")
        self.assertIn("def dump_threads_v0310():", src)
        self.assertIn("from engine_stall_dump_v0310 import dump_threads", src)


class RegimeReadsNeverBlock(unittest.TestCase):
    """The regime is read inside BUY paths; it must never wait on the Trend coordinator lock."""

    def test_busy_coordinator_lock_means_unverified_not_a_wait(self):
        co = SimpleNamespace(lock=threading.RLock(), _history={("coin", "KRW-X", "30m"): []})
        held, release = threading.Event(), threading.Event()

        def hold():
            with co.lock:
                held.set()
                release.wait(5)
        t = threading.Thread(target=hold, daemon=True)
        t.start()
        held.wait(2)
        original = regime._runtime
        regime._runtime = lambda: SimpleNamespace(market_trend=co, root=None)
        try:
            started = time.monotonic()
            self.assertIsNone(regime._trend_history("coin", "KRW-X", "30m"))
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            regime._runtime = original
            release.set()
            t.join(2)

    def test_free_lock_is_released_after_the_read(self):
        co = SimpleNamespace(lock=threading.Lock(), _history={})
        original = regime._runtime
        regime._runtime = lambda: SimpleNamespace(market_trend=co, root=None)
        try:
            regime._trend_history("coin", "KRW-X", "30m")
            self.assertTrue(co.lock.acquire(blocking=False))     # not left locked
            co.lock.release()
        finally:
            regime._runtime = original

    def test_sqlite_reads_really_close_their_connection(self):
        for name in ("market_regime_v0290.py", "ma_v067_stock_runtime.py"):
            src = read(PY / name)
            self.assertIn("closing(sqlite3.connect(", src)
            self.assertIn("from contextlib import closing", src)
        self.assertNotIn("ro:\n                rows = ro.execute(sql", read(PY / "market_regime_v0290.py").replace(
            "closing(sqlite3.connect(\"file:%s?mode=ro\" % db, uri=True, timeout=2)) as ro:", ""))

    def test_stock_bars_connection_is_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "stock").mkdir()
            db = root / "stock" / "ARTITRADE_D.sqlite3"
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE bars(code TEXT,frame TEXT,ts TEXT,close INTEGER)")
            c.execute("INSERT INTO bars VALUES('005930','30m','20261120100000',70000)")
            c.commit(); c.close()
            opened = []
            real = sqlite3.connect

            def spy(*a, **k):
                conn = real(*a, **k)
                opened.append(conn)
                return conn
            original = regime._runtime
            regime._runtime = lambda: SimpleNamespace(root=str(root))
            regime.sqlite3 = SimpleNamespace(connect=spy)
            try:
                self.assertEqual(regime._stock_bars("005930", "30m"), [70000])
            finally:
                regime.sqlite3 = sqlite3
                regime._runtime = original
            self.assertEqual(len(opened), 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                opened[0].execute("select 1")                    # closed


class SupervisorWiring(unittest.TestCase):
    def test_bridge_dump_is_not_synchronized(self):
        src = read(JAVA / "StrategyRuntimeBridge.java")
        self.assertIn("String dumpThreadsV0310(){", src)
        self.assertNotIn("synchronized String dumpThreadsV0310", src)
        self.assertIn('callAttr("dump_threads_v0310")', src)

    def test_supervisor_is_independent_of_the_bridge_monitor(self):
        src = read(JAVA / "UnifiedTradingService.java")
        i = src.index("private void engineSupervisorLoop()")
        loop = src[i:src.index("private void checkBrokerSellReceipts()", i)]
        for forbidden in ("strategy.snapshot(", "strategy.restart(", "strategy.stop(", "safeRestartStrategy"):
            self.assertNotIn(forbidden, loop)
        self.assertIn("EngineHangPolicy.step(", loop)
        self.assertIn("restartProcessAfterHang()", loop)
        j = src.index("private void restartProcessAfterHang()")
        restart = src[j:src.index("private void engineSupervisorLoop()", j)]
        self.assertIn("android.os.Process.killProcess(android.os.Process.myPid())", restart)
        self.assertIn("putLong(\"hang_flush_at\",wall).commit()", restart)       # fault log flushed first
        self.assertNotIn("strategy.", restart)

    def test_supervisor_started_with_the_service_and_is_a_daemon(self):
        src = read(JAVA / "UnifiedTradingService.java")
        self.assertIn('new Thread(this::engineSupervisorLoop,"artitrade-v0310-engine-supervisor")', src)
        self.assertIn("engineSupervisor.setDaemon(true);", src)

    def test_hang_dump_is_stored_with_a_larger_fault_limit_only_for_that_stage(self):
        src = read(JAVA / "RuntimeCrashStore.java")
        self.assertIn('stage!=null&&stage.startsWith("engine_hang")?9500:2800', src)

    def test_policy_thresholds(self):
        src = read(JAVA / "EngineHangPolicy.java")
        for needle in ("DUMP_AFTER_STALE_MS=90_000L", "RESTART_AFTER_STALE_MS=240_000L",
                       "ENGINE_MIN_UP_MS=300_000L", "MIN_RESTART_GAP_MS=600_000L",
                       "MAX_RESTARTS_PER_HOUR=3", "CONTINUITY_MS=30_000L"):
            self.assertIn(needle, src)

    def test_policy_has_no_android_dependency(self):
        src = read(JAVA / "EngineHangPolicy.java")
        self.assertNotIn("import android", src)

    def test_jvm_test_is_in_the_signer_workflow(self):
        flow = BASE.parent / ".github/workflows/artitrade-0310-trusted-branch-signer.yml"
        if flow.exists():
            text = flow.read_text(encoding="utf-8")
            self.assertIn("EngineHangPolicy.java", text)
            self.assertIn("EngineHangPolicyTest", text)
            self.assertIn("test_v0310_engine_hang_diagnostics.py", text)


if __name__ == "__main__":
    unittest.main()
