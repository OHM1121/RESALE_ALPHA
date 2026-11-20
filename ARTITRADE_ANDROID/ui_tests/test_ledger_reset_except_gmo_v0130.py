"""0.1.3 one-time PAPER ledger reset for everything except the GMO ledger.

Synthetic data only. The reset must (a) empty AT/MA/Trend/CW ledgers with exact
cash and realized-P&L reversal, (b) never open GMO files, (c) never delete REAL or
broker-linked rows, (d) restore every wallet when it fails before the witness
rebase, (e) keep the independent anti-rollback guard satisfied, (f) run once.
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
import ledger_reset_v0130 as reset
import ledger_rollback_guard_v002 as guard
from test_v012_legacy_purge_and_holding_frame import coin_fixture, db, read

try:
    from at_close_store import AtCloseStore
    NATIVE = True
except Exception:  # pragma: no cover - engine not on sys.path
    NATIVE = False

EPOCH_START = "2026-11-18T00:00:00+00:00"


def make_root(tmp):
    files = Path(tmp) / "files"
    root = files / "ARTITRADE_PHONE_MAIN_V070"
    root.mkdir(parents=True)
    (root / guard.MARKER).write_text(json.dumps(dict(
        schema=guard.EPOCH_SCHEMA, epoch_id="SIM_EPOCH_0130", state="COMMITTED",
        completed_at_utc=EPOCH_START)))
    return root


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def add_open_and_closed_coin(path):
    """A NEW-policy OPEN holding and a CLOSED position with a SELL, both PAPER."""
    with sqlite3.connect(path) as c:
        c.execute("""INSERT INTO positions VALUES(2,'5m','KRW-NEW','new_ref',
            '2026-11-19T15:03:00+09:00','100','10','10','OPEN')""")
        c.execute("""INSERT INTO executions VALUES(2,2,'5m','KRW-NEW',
            '2026-11-19T15:03:00+09:00','BUY','10','100',1,0,0,'NL1118_BUY',1988994)""")
        c.execute("""INSERT INTO positions VALUES(3,'5m','KRW-WIN','win_ref',
            '2026-11-19T15:04:00+09:00','100','10','0','CLOSED')""")
        c.execute("""INSERT INTO executions VALUES(3,3,'5m','KRW-WIN',
            '2026-11-19T15:04:00+09:00','BUY','10','100',1,0,0,'NL1118_BUY',1987993)""")
        c.execute("""INSERT INTO executions VALUES(4,3,'5m','KRW-WIN',
            '2026-11-19T15:09:00+09:00','SELL','10','110',1,0,98,'NL1118_TP1',1989092)""")
        # cash now: seed -5(old buy) -1001 -1001 +1099-... computed below
        c.execute("UPDATE wallets SET cash=1989995-1001-1001+1099, realized=98 WHERE tf='5m'")


class CoinWalletReset(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(prefix="artitrade-v0130-")
        self.addCleanup(d.cleanup)
        self.root = Path(d.name)

    def test_every_position_open_closed_old_and_new_is_removed_and_cash_returns_to_seed(self):
        path = self.root / "coin" / "base" / "upbit_paper.sqlite3"
        coin_fixture(path)
        add_open_and_closed_coin(path)
        result = reset._apply_wallet(path, "coin_at", self.root)
        self.assertEqual((result["removed"], result["fills"]), (3, 4))
        self.assertTrue(result["fully_done"])
        for table in ("positions", "executions", "candidates", "orders"):
            self.assertEqual(read(path, "SELECT COUNT(*) FROM %s" % table), [(0,)], table)
        self.assertEqual(read(path, "SELECT cash,realized FROM wallets"), [(2000000, 0)])
        self.assertTrue(reset._apply_wallet(path, "coin_at", self.root)["already"])

    def test_schema_and_seed_survive_so_the_engine_needs_no_change(self):
        path = self.root / "coin" / "base" / "upbit_paper.sqlite3"
        coin_fixture(path)
        before = read(path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        reset._apply_wallet(path, "coin_at", self.root)
        after = read(path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        self.assertEqual([x for x in after if x not in before],
                         [("nl1120_ledger_reset_v0130",)])
        self.assertEqual(read(path, "SELECT seed FROM wallets"), [(2000000,)])

    def test_live_or_broker_linked_wallet_is_left_completely_untouched(self):
        path = self.root / "stock" / "trend" / "market_trend_wallet_v054.sqlite3"
        db(path, """CREATE TABLE accounts(tf TEXT PRIMARY KEY,cash INTEGER,realized INTEGER,seed INTEGER);
            INSERT INTO accounts VALUES('30m',1000000,0,2000000);
            CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,symbol TEXT,entry TEXT,
                qty0 REAL,qty REAL,status TEXT,execution_mode TEXT,broker_order_id TEXT,payload TEXT);
            INSERT INTO positions VALUES(1,'30m','005930','100',10,10,'TREND_OPEN','LIVE','123456','{}');
            CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,tf TEXT,side TEXT,
                qty REAL,price REAL,fees INTEGER,tax INTEGER,net_pnl INTEGER);
            INSERT INTO executions VALUES(1,1,'30m','BUY',10,100,1,0,0);""")
        out = reset._apply_wallet(path, "stock_trend", self.root)
        self.assertEqual(out["reason"], "LIVE_OR_BROKER_EVIDENCE_FAIL_CLOSED")
        self.assertEqual(read(path, "SELECT COUNT(*) FROM positions"), [(1,)])
        self.assertEqual(read(path, "SELECT cash FROM accounts"), [(1000000,)])

    def test_unprovable_position_is_kept_and_reported_not_guessed(self):
        path = self.root / "coin" / "base" / "upbit_paper.sqlite3"
        coin_fixture(path)
        with sqlite3.connect(path) as c:      # a position with no BUY receipt
            c.execute("""INSERT INTO positions VALUES(9,'5m','KRW-X','r','2026-11-19T15:00:00+09:00',
                '1','1','1','OPEN')""")
        out = reset._apply_wallet(path, "coin_at", self.root)
        self.assertEqual(out["removed"], 1)
        self.assertEqual(out["skip_reasons"], {"MISSING_ORIGINAL_BUY_RECEIPT": 1})
        self.assertFalse(out["fully_done"])
        self.assertEqual(read(path, "SELECT id FROM positions"), [(9,)])

    def test_gmo_can_never_be_a_reset_target(self):
        self.assertTrue(all("gmo" not in rel.lower() for _, rel in reset.SPECS))
        with patch.object(reset, "SPECS", (("stock_at", "stock/PROJECTGMO_V1.0_x.sqlite3"),)):
            with self.assertRaises(reset.UnsafeLedgerReset):
                reset._active(self.root)


@unittest.skipUnless(NATIVE, "needs the original engine on sys.path")
class CloseBettingWallet(unittest.TestCase):
    def make(self, tmp):
        root = Path(tmp) / "close"
        store = AtCloseStore(root)
        path = root / "AT_CLOSE_PAPER.sqlite3"
        with store.connect() as c:
            seed = c.execute("SELECT strategy,seed FROM wallets").fetchone()
            strategy, seed = seed["strategy"], int(seed["seed"])
            c.execute("""INSERT INTO positions(id,strategy,code,name,status,entry_ts,entry_date,
                deadline_date,entry_price,entry_qty,qty,entry_fee,signal_id,peak_price,trough_price,
                last_price) VALUES(1,?,'005930','T','OPEN','2026-11-19T15:21:00+09:00','2026-11-19',
                '2026-11-20',1000,10,10,5,NULL,1000,1000,1000)""", (strategy,))
            c.execute("""INSERT INTO executions(ts,strategy,position_id,code,name,side,qty,price,fee,tax,
                cash_after,reason,net_realized) VALUES('2026-11-19T15:21:00+09:00',?,1,'005930','T',
                'BUY',10,1000,5,0,?, 'CLOSE_BUY',0)""", (strategy, seed - 10005))
            c.execute("UPDATE wallets SET cash=? WHERE strategy=?", (seed - 10005, strategy))
        return root, path, strategy, seed

    def test_close_wallet_reset_to_seed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, path, strategy, seed = self.make(tmp)
            out = reset._apply_close(path, root)
            self.assertEqual((out["removed"], out["fills"]), (1, 1))
            self.assertEqual(read(path, "SELECT COUNT(*) FROM positions"), [(0,)])
            self.assertEqual(read(path, "SELECT COUNT(*) FROM executions"), [(0,)])
            self.assertEqual(read(path, "SELECT cash,realized FROM wallets WHERE strategy='%s'" % strategy),
                             [(seed, 0)])

    def test_close_wallet_that_does_not_reconcile_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, path, strategy, seed = self.make(tmp)
            with sqlite3.connect(path) as c:
                c.execute("UPDATE wallets SET cash=cash+7")      # drift nobody can explain
            out = reset._apply_close(path, root)
            self.assertEqual(out["reason"], "CLOSE_CASH_NOT_RECONCILABLE_TO_SEED")
            self.assertEqual(read(path, "SELECT COUNT(*) FROM executions"), [(1,)])


class EndToEnd(unittest.TestCase):
    def build(self, tmp):
        root = make_root(tmp)
        coin = root / "coin" / "base" / "upbit_paper.sqlite3"
        coin_fixture(coin)
        add_open_and_closed_coin(coin)
        # GMO ledger: account-state JSON + a research DB WITH an executions table.
        gmo_json = root / "stock" / "PROJECTGMO_V1.0_account_state.json"
        gmo_json.parent.mkdir(parents=True, exist_ok=True)
        gmo_json.write_text(json.dumps({"positions": {"005930": {"qty": 2}},
                                        "fills": [{"code": "005930"}]}))
        gmo_db = root / "stock" / "PROJECTGMO_V1.0_research.sqlite3"
        db(gmo_db, """CREATE TABLE executions(id INTEGER PRIMARY KEY,side TEXT,qty REAL,price REAL,ts TEXT);
            INSERT INTO executions VALUES(1,'BUY',2,70000,'2026-11-19T09:05:00+09:00');""")
        guard.verify_and_checkpoint(str(root), require_existing=False)
        return root, coin, gmo_json, gmo_db

    def test_full_run_clears_non_gmo_keeps_gmo_bytes_and_satisfies_the_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, coin, gmo_json, gmo_db = self.build(tmp)
            before = (sha(gmo_json), sha(gmo_db))
            result = reset.run_once(str(root))
            self.assertEqual(result["status"], "DONE", result)
            self.assertEqual(read(coin, "SELECT COUNT(*) FROM positions"), [(0,)])
            self.assertEqual(read(coin, "SELECT cash,realized FROM wallets"), [(2000000, 0)])
            self.assertEqual(before, (sha(gmo_json), sha(gmo_db)), "GMO files must be byte-identical")
            self.assertEqual(reset.run_once(str(root))["status"], "ALREADY_RESET")
            self.assertTrue(guard.verify_and_checkpoint(str(root), require_existing=True)["ok"])
            # new trades after the reset are accepted by the guard (monotonic from zero)
            with sqlite3.connect(coin) as c:
                c.execute("""INSERT INTO positions VALUES(11,'5m','KRW-AFTER','a','2026-11-20T10:00:00+09:00',
                    '100','1','1','OPEN')""")
                c.execute("""INSERT INTO executions VALUES(11,11,'5m','KRW-AFTER','2026-11-20T10:00:00+09:00',
                    'BUY','1','100',0,0,0,'NL1120',1999900)""")
            self.assertTrue(guard.verify_and_checkpoint(str(root), require_existing=True)["ok"])

    def test_failure_before_the_witness_swap_restores_every_wallet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, coin, gmo_json, gmo_db = self.build(tmp)
            cp = root.parent / guard.CHECKPOINT
            snapshot = (sha(coin), sha(cp))
            original = read(coin, "SELECT COUNT(*) FROM executions")
            with patch.object(reset, "_apply_wallet", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    reset.run_once(str(root))
            self.assertEqual(read(coin, "SELECT COUNT(*) FROM executions"), original)
            self.assertEqual(sha(cp), snapshot[1], "witness must be unchanged")
            self.assertFalse((root.parent / "ARTITRADE_NL1120_LEDGER_RESET_V0130_DONE.json").exists())
            self.assertTrue(guard.verify_and_checkpoint(str(root), require_existing=True)["ok"])
            # a later clean start can still finish the reset
            self.assertEqual(reset.run_once(str(root))["status"], "DONE")

    def test_real_trading_mode_never_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, coin, *_ = self.build(tmp)
            with patch.dict(os.environ, {"ARTITRADE_REAL_MODE": "1"}):
                self.assertIn("skipped", reset.run_once(str(root)))
            self.assertEqual(read(coin, "SELECT COUNT(*) FROM positions"), [(3,)])

    def test_unreconciled_leftovers_are_reported_as_partial_not_hidden(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, coin, *_ = self.build(tmp)
            with sqlite3.connect(coin) as c:
                c.execute("""INSERT INTO positions VALUES(9,'5m','KRW-X','r','2026-11-19T15:00:00+09:00',
                    '1','1','1','OPEN')""")
            result = reset.run_once(str(root))
            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(read(coin, "SELECT id FROM positions"), [(9,)])


class JavaHook(unittest.TestCase):
    def test_hook_runs_before_the_guard_catches_failures_and_never_blocks_trading(self):
        src = (Path(__file__).resolve().parents[1] /
               "app/src/main/java/com/artitrade/mobile/LedgerAntiRollbackV002.java").read_text("utf8")
        self.assertIn('getModule("ledger_reset_v0130")', src)
        hook = src.index('getModule("ledger_reset_v0130")')
        self.assertLess(hook, src.index('getModule("ledger_rollback_guard_v002")'))
        region = src[src.rindex("try{", 0, hook):src.index('getModule("ledger_rollback_guard_v002")')]
        self.assertIn("catch(Throwable", region)       # a failed reset must not block the engine
        self.assertIn("ledger_reset_v0130_status", region)


if __name__ == "__main__":
    unittest.main()
