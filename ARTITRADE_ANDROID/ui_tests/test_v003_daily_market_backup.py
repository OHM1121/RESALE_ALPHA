"""KST market-specific immutable source backup, no network or APK."""
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from android_daily_paper_backup_v003 import due_date,prepare
from android_ledger_backup import validate_backup
from krx_session_v041 import KST

class DailyMarketBackup(unittest.TestCase):
    def setUp(self):
        self.dir=tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.parent=Path(self.dir.name)
        self.root=self.parent/"files"/"ARTITRADE_PHONE_MAIN_V070"
        self.root.mkdir(parents=True)
        self.cache=self.parent/"cache"
        self.cache.mkdir()
        (self.root/"ARTITRADE_FRESH_EPOCH_V106.json").write_text(json.dumps({
            "schema":"ARTITRADE_FRESH_PAPER_EPOCH_V106",
            "state":"COMMITTED","epoch_id":"daily-epoch-001",
            "completed_at_utc":(dt.datetime.now(dt.timezone.utc)-
               dt.timedelta(days=2)).isoformat()}))
        (self.root/"ANDROID_OPERATION_V074.json").write_text(
            '{"stock_seed":2000000,"coin_seed":2000000}')
        (self.root/"execution_entry_plans_v107.sqlite3").touch()
        # Common candidate DB must be valid SQLite for the online backup.
        with sqlite3.connect(self.root/"execution_entry_plans_v107.sqlite3") as c:
            c.execute("CREATE TABLE plans(id INTEGER PRIMARY KEY)")
        for market in ("stock","coin"):
            path=self.root/market/"paper.sqlite3"
            path.parent.mkdir()
            with sqlite3.connect(path) as c:
                c.execute("""CREATE TABLE executions(
                   id INTEGER PRIMARY KEY,ts TEXT,side TEXT,qty REAL)""")
                c.execute("INSERT INTO executions VALUES(1,?,?,1)",
                    (dt.datetime.now(dt.timezone.utc).isoformat(),"BUY"))

    def test_stock_trading_day_2010_and_korean_holiday(self):
        def now(day,h,m):
            return dt.datetime.fromisoformat(day).replace(hour=h,minute=m,
                tzinfo=KST)
        self.assertEqual(due_date("stock","",now("2026-11-18",20,9)),
                         "2026-11-17")
        self.assertEqual(due_date("stock","",now("2026-11-18",20,10)),
                         "2026-11-18")
        self.assertEqual(due_date("stock","",now("2026-11-21",12,0)),
                         "2026-11-20")
        self.assertEqual(due_date("stock","",now("2026-12-31",20,11)),
                         "2026-12-30")
        self.assertEqual(due_date("stock","2026-11-18",
                              now("2026-11-18",21,0)),"")

    def test_coin_1130_every_day_and_late_restart(self):
        def t(hour,minute):
            return dt.datetime(2026,11,21,hour,minute,tzinfo=KST)
        self.assertEqual(due_date("coin","",t(11,29)),"2026-11-20")
        self.assertEqual(due_date("coin","",t(11,30)),"2026-11-21")
        self.assertEqual(due_date("coin","2026-11-20",t(11,31)),
                         "2026-11-21")

    def test_two_market_snapshots_preserve_immutable_originals(self):
        orig={market:(self.root/market/"paper.sqlite3").read_bytes()
              for market in ("stock","coin")}
        stock=json.loads(prepare(str(self.root),str(self.cache),"stock",
                                   "2026-11-18"))
        self.assertTrue(stock["ok"],stock)
        x=validate_backup(stock["path"])
        self.assertEqual(x["market_scope"],"stock")
        self.assertEqual(x["epoch_id"],"daily-epoch-001")
        with ZipFile(stock["path"]) as z:
            self.assertIn("stock/paper.sqlite3",z.namelist())
            self.assertNotIn("coin/paper.sqlite3",z.namelist())
            self.assertIn("ARTITRADE_FRESH_EPOCH_V106.json",z.namelist())
            self.assertIn("execution_entry_plans_v107.sqlite3",z.namelist())
        coin=json.loads(prepare(str(self.root),str(self.cache),"coin",
                                  "2026-11-18"))
        self.assertTrue(coin["ok"],coin)
        with ZipFile(coin["path"]) as z:
            self.assertIn("coin/paper.sqlite3",z.namelist())
            self.assertNotIn("stock/paper.sqlite3",z.namelist())
        for market in ("stock","coin"):
            self.assertEqual((self.root/market/"paper.sqlite3").read_bytes(),
                             orig[market])
        self.assertTrue(json.loads(prepare(str(self.root),str(self.cache),
            "stock","2026-11-18"))["reused"])

    def test_corrupted_staged_zip_is_never_silently_replaced(self):
        r=json.loads(prepare(str(self.root),str(self.cache),"stock",
                             "2026-11-18"))
        self.assertTrue(r["ok"],r)
        Path(r["path"]).write_bytes(b"CORRUPTED_BACKUP")
        second=json.loads(prepare(str(self.root),str(self.cache),"stock",
                                  "2026-11-18"))
        self.assertFalse(second["ok"])
        self.assertIn("NO_REPLACEMENT",second["error"])

if __name__=="__main__":unittest.main()
