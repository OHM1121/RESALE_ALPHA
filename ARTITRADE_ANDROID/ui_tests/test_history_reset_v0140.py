"""0.1.4 phase 2: clear INDEPENDENT research / error / AI history only.

Synthetic data only. Engine-owned state (signals, candidates, pending, bars, market
score, accounts) and every GMO file must be left byte-identical.
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import history_reset_v0140 as hist
import ledger_rollback_guard_v002 as guard


def db(path, sql):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as c:
        c.executescript(sql)


def count(path, table):
    with sqlite3.connect(str(path)) as c:
        return c.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Fixture(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(prefix="artitrade-v0140-")
        self.addCleanup(d.cleanup)
        self.files = Path(d.name) / "files"
        self.root = self.files / "ARTITRADE_PHONE_MAIN_V070"
        self.root.mkdir(parents=True)
        (self.root / guard.MARKER).write_text(json.dumps(dict(
            schema=guard.EPOCH_SCHEMA, epoch_id="SIM_EPOCH", state="COMMITTED",
            completed_at_utc="2026-11-18T00:00:00+00:00")))
        r = self.root
        db(r / "AI_RESEARCH_V040.sqlite3", """CREATE TABLE analysis_runs(id INTEGER PRIMARY KEY,x TEXT);
            INSERT INTO analysis_runs VALUES(1,'old');
            CREATE TABLE feature_snapshots(id INTEGER PRIMARY KEY,x TEXT);
            INSERT INTO feature_snapshots VALUES(1,'old'),(2,'old');""")
        db(r / "execution_decisions_v074.sqlite3", """CREATE TABLE decisions(id INTEGER PRIMARY KEY,x TEXT);
            INSERT INTO decisions VALUES(1,'old');""")
        db(r / "execution_entry_plans_v107.sqlite3", """CREATE TABLE entry_plans(id INTEGER PRIMARY KEY);
            INSERT INTO entry_plans VALUES(1);""")
        db(r / "phone_buy_evidence_v035.sqlite3", """CREATE TABLE buy_evidence(id INTEGER PRIMARY KEY);
            INSERT INTO buy_evidence VALUES(1);""")
        db(r / "market_trend_rsi_watch_v1118.sqlite3", """CREATE TABLE rsi_watch(symbol TEXT PRIMARY KEY);
            INSERT INTO rsi_watch VALUES('OLD');""")
        # engine-owned state in the same file as logs
        db(r / "market_trend_v043.sqlite3", """CREATE TABLE events(id INTEGER PRIMARY KEY);
            INSERT INTO events VALUES(1);
            CREATE TABLE gate_events(id INTEGER PRIMARY KEY);INSERT INTO gate_events VALUES(1);
            CREATE TABLE watch_observations(id INTEGER PRIMARY KEY);INSERT INTO watch_observations VALUES(1);
            CREATE TABLE latch(k TEXT PRIMARY KEY);INSERT INTO latch VALUES('state');
            CREATE TABLE stop_reentry_blocks(k TEXT PRIMARY KEY);INSERT INTO stop_reentry_blocks VALUES('b');
            CREATE TABLE market_score_observations(id INTEGER PRIMARY KEY);
            INSERT INTO market_score_observations VALUES(1);""")
        db(r / "stock" / "ARTITRADE_D.sqlite3", """CREATE TABLE issues(id INTEGER PRIMARY KEY);INSERT INTO issues VALUES(1);
            CREATE TABLE runtime_error_summary(signature TEXT PRIMARY KEY);INSERT INTO runtime_error_summary VALUES('s');
            CREATE TABLE issue_lifecycle(k TEXT PRIMARY KEY);INSERT INTO issue_lifecycle VALUES('k');
            CREATE TABLE signals(id INTEGER PRIMARY KEY);INSERT INTO signals VALUES(1);
            CREATE TABLE pending(id INTEGER PRIMARY KEY);INSERT INTO pending VALUES(1);
            CREATE TABLE bars(code TEXT,ts TEXT);INSERT INTO bars VALUES('005930','20261120090000');
            CREATE TABLE candidate_snapshots(id INTEGER PRIMARY KEY,signal_id INTEGER);
            INSERT INTO candidate_snapshots VALUES(1,1);
            CREATE TABLE accounts(tf TEXT PRIMARY KEY,cash INTEGER);INSERT INTO accounts VALUES('5m',2000000);""")
        db(r / "POST_EXIT_HIGH_V053.sqlite3", """CREATE TABLE cases(case_id TEXT PRIMARY KEY,strategy TEXT);
            INSERT INTO cases VALUES('c1','at'),('c2','gmo'),('c3','ma');
            CREATE TABLE minute_path(case_id TEXT);INSERT INTO minute_path VALUES('c1'),('c2'),('c3');
            CREATE TABLE high_history(case_id TEXT);INSERT INTO high_history VALUES('c1'),('c2');
            CREATE TABLE errors(id INTEGER PRIMARY KEY);INSERT INTO errors VALUES(1);""")
        db(r / "stock" / "PROJECTGMO_V1.0_research.sqlite3", """CREATE TABLE gmo_issues(id INTEGER PRIMARY KEY);
            INSERT INTO gmo_issues VALUES(1);CREATE TABLE issues(id INTEGER PRIMARY KEY);INSERT INTO issues VALUES(1);""")
        (r / "stock" / "PROJECTGMO_V1.0_account_state.json").write_text('{"positions":{"A":1}}')
        (r / "coin").mkdir(exist_ok=True)
        (r / "coin" / "runtime_errors.jsonl").write_text('{"e":1}\n{"e":2}\n')
        (r / "stock" / "ledger_notes.jsonl").write_text('{"keep":1}\n')
        guard.verify_and_checkpoint(str(r), require_existing=False)


class PhaseTwoClearing(Fixture):
    def test_independent_history_is_cleared_and_schema_kept(self):
        out = hist.run_once(str(self.root))
        self.assertEqual((out["status"], out["applied_now"]), ("DONE", True))
        r = self.root
        for f, t in (("AI_RESEARCH_V040.sqlite3", "analysis_runs"),
                     ("AI_RESEARCH_V040.sqlite3", "feature_snapshots"),
                     ("execution_decisions_v074.sqlite3", "decisions"),
                     ("execution_entry_plans_v107.sqlite3", "entry_plans"),
                     ("phone_buy_evidence_v035.sqlite3", "buy_evidence"),
                     ("market_trend_rsi_watch_v1118.sqlite3", "rsi_watch"),
                     ("market_trend_v043.sqlite3", "events"),
                     ("market_trend_v043.sqlite3", "gate_events"),
                     ("market_trend_v043.sqlite3", "watch_observations")):
            self.assertEqual(count(r / f, t), 0, f + ":" + t)
        for t in ("issues", "runtime_error_summary", "issue_lifecycle"):
            self.assertEqual(count(r / "stock" / "ARTITRADE_D.sqlite3", t), 0, t)
        self.assertEqual((r / "coin" / "runtime_errors.jsonl").read_bytes(), b"")

    def test_engine_owned_state_and_unrelated_logs_survive(self):
        hist.run_once(str(self.root))
        r = self.root
        d = r / "stock" / "ARTITRADE_D.sqlite3"
        for t in ("signals", "pending", "bars", "candidate_snapshots", "accounts"):
            self.assertEqual(count(d, t), 1, t)
        t = r / "market_trend_v043.sqlite3"
        for name in ("latch", "stop_reentry_blocks", "market_score_observations"):
            self.assertEqual(count(t, name), 1, name)
        self.assertIn("keep", (r / "stock" / "ledger_notes.jsonl").read_text())

    def test_gmo_files_are_byte_identical_and_post_exit_keeps_gmo_rows(self):
        gmo = [self.root / "stock" / "PROJECTGMO_V1.0_research.sqlite3",
               self.root / "stock" / "PROJECTGMO_V1.0_account_state.json"]
        before = [sha(p) for p in gmo]
        hist.run_once(str(self.root))
        self.assertEqual(before, [sha(p) for p in gmo])
        pe = self.root / "POST_EXIT_HIGH_V053.sqlite3"
        with sqlite3.connect(pe) as c:
            self.assertEqual(c.execute("SELECT case_id FROM cases").fetchall(), [("c2",)])
            self.assertEqual(c.execute("SELECT case_id FROM minute_path").fetchall(), [("c2",)])
            self.assertEqual(c.execute("SELECT case_id FROM high_history").fetchall(), [("c2",)])
        self.assertEqual(count(pe, "errors"), 0)

    def test_runs_once_and_guard_stays_satisfied(self):
        self.assertTrue(hist.run_once(str(self.root))["applied_now"])
        second = hist.run_once(str(self.root))
        self.assertEqual((second["status"], second["applied_now"]), ("ALREADY_RESET", False))
        with sqlite3.connect(self.root / "AI_RESEARCH_V040.sqlite3") as c:
            c.execute("INSERT INTO analysis_runs VALUES(9,'new')")      # new data accumulates
        self.assertEqual(count(self.root / "AI_RESEARCH_V040.sqlite3", "analysis_runs"), 1)
        self.assertTrue(guard.verify_and_checkpoint(str(self.root), require_existing=True)["ok"])

    def test_failure_on_one_database_is_reported_and_retried_not_marked_done(self):
        real = hist._clear_tables
        def flaky(path, names):
            if Path(path).name == "execution_decisions_v074.sqlite3":
                raise sqlite3.OperationalError("locked")
            return real(path, names)
        with patch.object(hist, "_clear_tables", side_effect=flaky):
            out = hist.run_once(str(self.root))
        self.assertEqual(out["status"], "PARTIAL")
        self.assertEqual(out["failed"][0]["error"], "OperationalError")
        self.assertFalse((self.files / "ARTITRADE_NL1120_HISTORY_RESET_V0140_DONE.json").exists())
        self.assertEqual(hist.run_once(str(self.root))["status"], "DONE")     # retry succeeds

    def test_real_mode_and_wrong_root_never_clear(self):
        with patch.dict(os.environ, {"ARTITRADE_REAL_MODE": "1"}):
            self.assertIn("skipped", hist.run_once(str(self.root)))
        self.assertEqual(count(self.root / "AI_RESEARCH_V040.sqlite3", "analysis_runs"), 1)
        with self.assertRaises(hist.UnsafeHistoryReset):
            hist.run_once(str(self.root.parent))

    def test_gmo_marker_is_never_a_target_name(self):
        self.assertTrue(all("gmo" not in n.lower() for n in hist.FULL_CLEAR_FILES))


class JavaHook(unittest.TestCase):
    def test_hook_is_caught_runs_before_the_guard_and_clears_the_fault_log_once(self):
        src = (Path(__file__).resolve().parents[1] /
               "app/src/main/java/com/artitrade/mobile/LedgerAntiRollbackV002.java").read_text("utf8")
        hook = src.index('getModule("history_reset_v0140")')
        self.assertLess(hook, src.index('getModule("ledger_rollback_guard_v002")'))
        region = src[src.rindex("try{", 0, hook):src.index('getModule("ledger_rollback_guard_v002")')]
        self.assertIn("catch(Throwable", region)
        self.assertIn('optBoolean("applied_now"', region)
        self.assertIn("new RuntimeCrashStore(c).clear()", region)


if __name__ == "__main__":
    unittest.main()
