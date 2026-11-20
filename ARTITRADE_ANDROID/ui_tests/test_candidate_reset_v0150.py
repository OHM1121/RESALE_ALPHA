"""0.1.5 phase 3: clear candidate / signal-linked research and buy-pending rows.

Synthetic data and the REAL native stores (DStore, MACoinLedger, AtCloseStore) so
the engines are proven to keep working on the emptied tables. GMO stays byte-identical.
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import candidate_reset_v0150 as reset
import ledger_rollback_guard_v002 as guard

try:
    from d_storage import DStore
    from d_engine import PaperEngine
    from coin_ma_engine import MACoinLedger
    from at_close_store import AtCloseStore
    NATIVE = True
except Exception:  # pragma: no cover - engine not on sys.path
    NATIVE = False

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


def count(path, table):
    with sqlite3.connect(str(path)) as c:
        return c.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def insert_min(c, table, **given):
    """Insert one row filling every NOT NULL column of the REAL table with a dummy."""
    cols = list(c.execute('PRAGMA table_info("%s")' % table))
    names, values = [], []
    for _, name, ctype, notnull, default, pk in cols:
        if name in given:
            names.append(name); values.append(given[name])
        elif pk and "INT" in (ctype or "").upper():
            continue
        elif notnull and default is None:
            names.append(name); values.append(0 if any(k in (ctype or "").upper() for k in ("INT", "REAL")) else "x")
    c.execute('INSERT INTO "%s"(%s) VALUES(%s)' % (table, ",".join('"%s"' % n for n in names),
                                                   ",".join("?" for _ in names)), values)


def make_root(tmp):
    files = Path(tmp) / "files"
    root = files / "ARTITRADE_PHONE_MAIN_V070"
    root.mkdir(parents=True)
    (root / guard.MARKER).write_text(json.dumps(dict(
        schema=guard.EPOCH_SCHEMA, epoch_id="SIM", state="COMMITTED",
        completed_at_utc="2026-11-18T00:00:00+00:00")))
    return files, root


class TableNamesAreReal(unittest.TestCase):
    def test_every_listed_table_exists_in_some_engine_or_overlay_schema(self):
        known = set()
        sources = list(PY.glob("*.py"))
        native = Path("/tmp/claude-0/native")
        if native.is_dir():
            sources += list(native.glob("*.py"))
        for src in sources:
            known |= set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+([A-Za-z0-9_]+)",
                                    src.read_text("utf8", "ignore")))
        if len(known) < 100:
            self.skipTest("engine sources not on this machine")
        for group in (reset.STOCK_AT, reset.STOCK_MA, reset.COIN, reset.CLOSE, reset.TREND_JOURNAL):
            for name in group:
                self.assertIn(name, known, name)

    def test_never_lists_ledger_state_or_engine_data(self):
        banned = {"positions", "executions", "accounts", "wallets", "bars", "stocks", "meta",
                  "metadata", "latch", "stop_reentry_blocks", "market_score_observations",
                  "v19_accounts", "v19_account", "v19_members", "v19_gap_holds", "audit"}
        for group in (reset.STOCK_AT, reset.STOCK_MA, reset.COIN, reset.CLOSE, reset.TREND_JOURNAL):
            self.assertEqual(banned & set(group), set())


@unittest.skipUnless(NATIVE, "needs the original engine on sys.path")
class RealStores(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(prefix="artitrade-v0150-")
        self.addCleanup(d.cleanup)
        self.files, self.root = make_root(d.name)

    def stock(self):
        store = DStore(self.root / "stock")
        with store.connect() as c:
            c.execute("INSERT OR IGNORE INTO accounts(tf,cash,realized_pnl) VALUES('30m',2000000,0)")
            c.execute("""INSERT INTO bars(code,market,frame,ts,open,high,low,close,volume,source)
                VALUES('005930','KOSPI','30m','20261120090000',1,2,1,2,10,'KIWOOM_VERIFIED')""")
        return store, self.root / "stock" / "ARTITRADE_D.sqlite3"

    def test_stock_at_candidates_signals_and_pending_are_cleared_engine_data_kept(self):
        store, path = self.stock()
        with sqlite3.connect(path) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS v19_orders(signal_id INTEGER,status TEXT)""")
            c.execute("INSERT INTO v19_orders VALUES(1,'PENDING')")
            insert_min(c, "candidate_snapshots", signal_id=1)       # real native schema
            c.execute("""INSERT INTO signals(event_key,ts,tf,code,name,market,ref_ts,buy_price,target,score,features,decision)
                VALUES('signal:30m:005930:x','20261120090000','30m','005930','T','KOSPI','20261120090000',1,2,50,'{}','NEW')""")
            insert_min(c, "pending", tf="30m", signal_id=1)          # real native schema
        accounts = count(path, "accounts")
        out = reset.run_once(str(self.root))
        self.assertEqual((out["status"], out["applied_now"]), ("DONE", True), out)
        for t in ("signals", "pending", "v19_orders", "candidate_snapshots"):
            self.assertEqual(count(path, t), 0, t)
        self.assertEqual(count(path, "bars"), 1)
        self.assertEqual(count(path, "accounts"), accounts)
        # the real engine keeps working on the emptied tables and can create new signals
        engine = PaperEngine(DStore(self.root / "stock"))
        with engine.store.connect() as c:
            self.assertIsNone(engine._pending(c, "30m"))
            c.execute("""INSERT INTO signals(event_key,ts,tf,code,name,market,ref_ts,buy_price,target,score,features,decision)
                VALUES('signal:30m:005930:y','20261120100000','30m','005930','T','KOSPI','20261120100000',1,2,50,'{}','NEW')""")
            self.assertEqual(c.execute("SELECT id FROM signals").fetchone()[0] > 1, True,
                             "autoincrement continues, old ids are not reused")

    def test_coin_ma_pending_candidates_cleared_wallet_and_engine_keep_working(self):
        path = self.root / "coin" / "ma_epoch_v067" / "upbit_paper.sqlite3"
        path.parent.mkdir(parents=True)
        ledger = MACoinLedger(path.parent)
        with ledger.connect() as c:
            c.execute("""INSERT INTO candidates(tf,market,ref_at,ref_end,score,limit_price,target,features)
                VALUES('5m','KRW-OLD','r1','r2',80,'115','119','{}')""") if False else None
        record = dict(frame="5m", market="KRW-OLD", ref_at="2026-11-20T09:25:00+09:00",
                      ref_end="2026-11-20T09:30:00+09:00", score=80, limit="115", target="119",
                      features=dict(high=120, low=100, open=115, close=115))
        import datetime as dt
        ledger.record_candidate(record, "SIM_MA", dt.datetime(2026, 11, 20, 9, 29, 55,
                                tzinfo=dt.timezone(dt.timedelta(hours=9))))
        self.assertGreaterEqual(count(ledger.path if hasattr(ledger, "path") else path, "candidates"), 1)
        seed = sqlite3.connect(path).execute("SELECT cash FROM wallets").fetchall()
        out = reset.run_once(str(self.root))
        self.assertEqual(out["status"], "DONE", out)
        self.assertEqual(count(path, "candidates"), 0)
        self.assertEqual(sqlite3.connect(path).execute("SELECT cash FROM wallets").fetchall(), seed)
        # a NEW candidate is accepted again by the real ledger
        again = MACoinLedger(path.parent)
        again.record_candidate(record, "SIM_MA", dt.datetime(2026, 11, 20, 9, 29, 56,
                               tzinfo=dt.timezone(dt.timedelta(hours=9))))
        self.assertGreaterEqual(count(path, "candidates"), 1)

    def test_close_betting_signals_and_research_cleared_wallet_kept(self):
        path = self.root / "stock" / "close" / "AT_CLOSE_PAPER.sqlite3"
        store = AtCloseStore(path.parent)
        with store.connect() as c:
            c.execute("""INSERT INTO signals(ts,strategy,code,name,decision,rule_version,feature_json,reason_json)
                VALUES('2026-11-20T15:21:00+09:00','close','005930','T','NEW','v','{}','{}')""")
            c.execute("""INSERT INTO research_candidates(ts,strategy,code,name,decision,near_signal,feature_json,reason_json,market_json)
                VALUES('2026-11-20T15:21:00+09:00','close','005930','T','NEW',0,'{}','{}','{}')""")
            wallets = c.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
        out = reset.run_once(str(self.root))
        self.assertEqual(out["status"], "DONE", out)
        self.assertEqual((count(path, "signals"), count(path, "research_candidates")), (0, 0))
        self.assertEqual(count(path, "wallets"), wallets)
        again = AtCloseStore(path.parent)               # the real store reopens and still works
        with again.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM signals").fetchone()[0], 0)


class Behaviour(unittest.TestCase):
    def build(self, tmp):
        files, root = make_root(tmp)
        stock = root / "stock" / "ARTITRADE_D.sqlite3"
        stock.parent.mkdir(parents=True)
        with sqlite3.connect(stock) as c:
            c.executescript("""CREATE TABLE signals(id INTEGER PRIMARY KEY);INSERT INTO signals VALUES(1);
                CREATE TABLE pending(id INTEGER PRIMARY KEY);INSERT INTO pending VALUES(1);
                CREATE TABLE positions(id INTEGER PRIMARY KEY);INSERT INTO positions VALUES(1);
                CREATE TABLE bars(code TEXT);INSERT INTO bars VALUES('A');""")
        gmo = root / "stock" / "PROJECTGMO_V1.0_research.sqlite3"
        with sqlite3.connect(gmo) as c:
            c.executescript("CREATE TABLE signals(id INTEGER);INSERT INTO signals VALUES(1);"
                            "CREATE TABLE candidates(id INTEGER);INSERT INTO candidates VALUES(1);")
        (root / "stock" / "PROJECTGMO_V1.0_account_state.json").write_text('{"p":1}')
        coin = root / "coin" / "base" / "upbit_paper.sqlite3"
        coin.parent.mkdir(parents=True)
        with sqlite3.connect(coin) as c:
            c.executescript("""CREATE TABLE candidates(id INTEGER);INSERT INTO candidates VALUES(1);
                CREATE TABLE orders(id INTEGER);INSERT INTO orders VALUES(1);
                CREATE TABLE wallets(tf TEXT,cash INTEGER);INSERT INTO wallets VALUES('5m',5);
                CREATE TABLE executions(id INTEGER);INSERT INTO executions VALUES(1);""")
        guard.verify_and_checkpoint(str(root), require_existing=False)
        return files, root, stock, gmo, coin

    def test_gmo_is_byte_identical_and_ledger_tables_are_not_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            files, root, stock, gmo, coin = self.build(tmp)
            before = (sha(gmo), sha(root / "stock" / "PROJECTGMO_V1.0_account_state.json"))
            reset.run_once(str(root))
            self.assertEqual(before, (sha(gmo), sha(root / "stock" / "PROJECTGMO_V1.0_account_state.json")))
            self.assertEqual((count(stock, "positions"), count(stock, "bars")), (1, 1))
            self.assertEqual((count(coin, "wallets"), count(coin, "executions")), (1, 1))
            self.assertEqual((count(stock, "signals"), count(coin, "orders"), count(coin, "candidates")), (0, 0, 0))
            self.assertTrue(guard.verify_and_checkpoint(str(root), require_existing=True)["ok"])

    def test_once_only_and_new_rows_accumulate(self):
        with tempfile.TemporaryDirectory() as tmp:
            files, root, stock, gmo, coin = self.build(tmp)
            self.assertTrue(reset.run_once(str(root))["applied_now"])
            with sqlite3.connect(stock) as c:
                c.execute("INSERT INTO signals VALUES(9)")
            second = reset.run_once(str(root))
            self.assertEqual((second["status"], second["applied_now"]), ("ALREADY_RESET", False))
            self.assertEqual(count(stock, "signals"), 1)

    def test_one_failing_database_is_reported_and_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            files, root, stock, gmo, coin = self.build(tmp)
            real = reset._clear
            def flaky(path, names):
                if Path(path).name == "upbit_paper.sqlite3":
                    raise sqlite3.OperationalError("locked")
                return real(path, names)
            with patch.object(reset, "_clear", side_effect=flaky):
                out = reset.run_once(str(root))
            self.assertEqual(out["status"], "PARTIAL")
            self.assertFalse((files / "ARTITRADE_NL1120_CANDIDATE_RESET_V0150_DONE.json").exists())
            self.assertEqual(reset.run_once(str(root))["status"], "DONE")

    def test_real_mode_and_wrong_root_never_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            files, root, stock, gmo, coin = self.build(tmp)
            with patch.dict(os.environ, {"ARTITRADE_REAL_MODE": "1"}):
                self.assertIn("skipped", reset.run_once(str(root)))
            self.assertEqual(count(stock, "signals"), 1)
            with self.assertRaises(reset.UnsafeCandidateReset):
                reset.run_once(str(root.parent))

    def test_gmo_paths_have_no_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            files, root, stock, gmo, coin = self.build(tmp)
            self.assertIsNone(reset._plan(root, gmo))


class JavaHook(unittest.TestCase):
    def test_hook_runs_before_the_guard_and_is_caught(self):
        src = (PY.parent / "java/com/artitrade/mobile/LedgerAntiRollbackV002.java").read_text("utf8")
        hook = src.index('getModule("candidate_reset_v0150")')
        self.assertLess(hook, src.index('getModule("ledger_rollback_guard_v002")'))
        region = src[src.rindex("try{", 0, hook):src.index('getModule("ledger_rollback_guard_v002")')]
        self.assertIn("catch(Throwable", region)
        self.assertIn("candidate_reset_v0150_status", region)


if __name__ == "__main__":
    unittest.main()
