"""V053: sale-anchored 5/10/15-day high tracking without trade-engine mutations."""
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import post_exit_high_v053 as high
import post_exit_sources_v053 as sources
import android_ledger_backup as backup
KST=dt.timezone(dt.timedelta(hours=9))


def at(day,hour=10,minute=0,second=0):
    return dt.datetime.fromisoformat(day).replace(hour=hour,minute=minute,
        second=second,tzinfo=KST)


class PostSalePolicyV053(unittest.TestCase):
    def test_explicit_windows_by_market_and_strategy(self):
        sale=at("2026-11-12")
        self.assertEqual(high.WINDOWS,{"5m":5,"30m":5,"60m":10,
                                      "day":15,"gmo":5,"close":5})
        for frame,days in high.WINDOWS.items():
            stock=high.deadline(sale,"stock",frame)
            self.assertEqual(stock.hour,16 if stock.date().isoformat()=="2026-11-19" else 15)
            self.assertGreaterEqual((stock.date()-sale.date()).days,days)
            coin=high.deadline(sale,"coin",frame)
            self.assertEqual(coin,sale+dt.timedelta(days=days))

    def test_stock_next_five_sessions_exclude_sale_day_and_special_close(self):
        # 11/13, 11/16, 11/17, 11/18, 11/19 admission exam special session.
        point=high.deadline(at("2026-11-12",15,29),"stock","30m")
        self.assertEqual(point,at("2026-11-19",16,30))
        after=high.deadline(at("2026-12-24"),"stock","5m")
        self.assertNotEqual(after.date().isoformat(),"2026-12-31")
        self.assertEqual(after.date().isoformat(),"2027-01-05")
        self.assertEqual(after.hour,15)
        with self.assertRaisesRegex(ValueError,"KRX_CALENDAR_NOT_VERIFIED"):
            high.deadline(at("2027-12-30"),"stock","60m")

    def test_price_history_only_after_exit_and_all_high_updates(self):
        with tempfile.TemporaryDirectory() as t:
            manager=high.Tracker(t)
            sale=at("2026-11-12",10,0)
            key=manager.register("stock","at","5m","005930","ACTUAL_FILLED",
               100,sale.isoformat(),"at:position:35",exit_evidence="CONFIRMED",
               execution_mode="ORIGINAL_PAPER_FILL",target_price=106)
            points=[("stock","005930",150,at("2026-11-12",9,59,59),"KRX_TRADE"),
                    ("stock","005930",101,at("2026-11-12",10,0),"KRX_TRADE"),
                    ("stock","005930",98,at("2026-11-12",10,0,1),"KRX_TRADE"),
                    ("stock","005930",105,at("2026-11-12",10,1),"KRX_TRADE"),
                    ("stock","005930",108,at("2026-11-12",10,1,9),"KRX_TRADE"),
                    ("stock","005930",103,at("2026-11-12",10,2),"KRX_TRADE"),
                    ("stock","005930",180,at("2026-11-20",9,0),"KRX_TRADE")]
            self.assertEqual(manager.record_batch(points),4)
            record=manager.summary("stock","at")["cases"][0]
            self.assertEqual(record["case_id"],key)
            self.assertEqual(record["peak"],108)
            self.assertAlmostEqual(record["peak_pct"],8)
            self.assertEqual(record["trough"],98)
            self.assertAlmostEqual(record["trough_pct"],-2)
            self.assertEqual(record["sample_count"],4)
            self.assertEqual(record["target_hit_at"],int(at("2026-11-12",10,1,9).timestamp()))
            with sqlite3.connect(manager.path) as con:
                rows=con.execute("SELECT high_at,price,pct_from_buy FROM high_history WHERE case_id=? ORDER BY high_at",(key,)).fetchall()
                self.assertEqual([x[1] for x in rows],[98,105,108])
                source=con.execute("SELECT high,high_at,samples FROM minute_path WHERE case_id=? AND bucket_epoch=?",(key,int(at("2026-11-12",10,1).timestamp())//60)).fetchone()
                self.assertEqual(source[0],108)
                self.assertEqual(source[1],int(at("2026-11-12",10,1,9).timestamp()))
                self.assertEqual(source[2],2)

    def test_coin_continuous_five_days_and_paper_expected_is_not_actual(self):
        with tempfile.TemporaryDirectory() as t:
            manager=high.Tracker(t)
            sold=at("2026-11-12",12,33,11)
            case=manager.register("coin","general","30m","KRW-BTC","PAPER",
                500,sold.isoformat(),"coin/shadow/7",exit_evidence="EXPECTED")
            same=manager.register("coin","general","30m","KRW-BTC","PAPER",
                500,sold.isoformat(),"coin/shadow/7",exit_evidence="EXPECTED")
            self.assertEqual(case,same)
            manager.record_batch([("coin","KRW-BTC",490,sold+dt.timedelta(seconds=1),"UPBIT_PUBLIC_LAST"),
                                  ("coin","KRW-BTC",600,sold+dt.timedelta(days=5),"UPBIT_PUBLIC_LAST"),
                                  ("coin","KRW-BTC",700,sold+dt.timedelta(days=5,seconds=1),"UPBIT_PUBLIC_LAST")])
            values=manager.summary("coin","general")
            self.assertEqual(values["total_cases"],1)
            row=values["cases"][0]
            self.assertEqual(row["cohort"],"PAPER")
            self.assertEqual(row["exit_evidence"],"EXPECTED")
            self.assertEqual(row["peak"],600)
            self.assertEqual(row["peak_pct"],20)
            self.assertEqual(row["sample_count"],2)
            confirm=manager.register("coin","general","30m","KRW-BTC","PAPER",
                500,(sold+dt.timedelta(seconds=5)).isoformat(),
                "coin/shadow/7",exit_evidence="CONFIRMED")
            self.assertNotEqual(confirm,case)
            old=next(x for x in manager.summary("coin","general")["cases"]
                     if x["case_id"]==case)
            self.assertEqual(old["status"],"SUPERSEDED")
            self.assertEqual(manager.summary("coin","general")["total_cases"],2)

    def test_restore_reuses_immutable_source_case_and_original_settings(self):
        with tempfile.TemporaryDirectory() as t:
            tr=high.Tracker(t)
            sale=at("2026-11-12")
            token=tr.register("stock","ma","60m","000660","ACTUAL_FILLED",
                              1000,sale.isoformat(),"stock/ma:position:23")
            tr.record_batch([("stock","000660",1070,sale+dt.timedelta(seconds=3),"KRX_TRADE")])
            again=high.Tracker(t)
            self.assertEqual(token,again.register("stock","ma","60m","000660",
                    "ACTUAL_FILLED",1000,sale.isoformat(),"stock/ma:position:23"))
            self.assertEqual(again.summary("stock","ma")["cases"][0]["peak"],1070)

    def test_shadow_source_and_confirmed_real_sell_from_original_sqlite(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            path=root/"stock"/"ARTITRADE_D.sqlite3"
            path.parent.mkdir()
            sold=at("2026-11-12",11,2)
            with sqlite3.connect(path) as c:
                c.execute("""CREATE TABLE positions(
                   id INTEGER PRIMARY KEY,tf TEXT,code TEXT,status TEXT,
                   entry_price REAL,ref_ts TEXT)""")
                c.execute("""CREATE TABLE executions(
                   id INTEGER,position_id INTEGER,ts TEXT,side TEXT,reason TEXT,
                   fill_method TEXT)""")
                c.execute("""CREATE TABLE stock_shadow_trials(
                   signal_id INTEGER,tf TEXT,code TEXT,entry_ts TEXT,
                   entry_price REAL,state TEXT,sold_at TEXT,hold_days INTEGER)""")
                c.execute("INSERT INTO positions VALUES(1,'5m','005930','CLOSED',51000,'20261111100000')")
                c.execute("INSERT INTO executions VALUES(5,1,?,'SELL','TAKE_PROFIT','PAPER_FRESH_QUOTE')",
                          (sold.strftime("%Y%m%d%H%M%S"),))
                c.execute("INSERT INTO stock_shadow_trials VALUES(2,'5m','000660','20261111100000',900,'CLOSED',?,2)",
                          (sold.strftime("%Y%m%d%H%M%S"),))
            cases,errors=sources.scan(root,int(sold.timestamp())+90)
            self.assertEqual(len(cases),2)
            self.assertEqual({x["cohort"] for x in cases},{"ACTUAL_FILLED","PAPER"})
            self.assertTrue(all(x["exit_evidence"]=="CONFIRMED" for x in cases))
            self.assertTrue(all(x["exit_at"]==sold.isoformat() for x in cases))
            manager=high.Tracker(root)
            manager.scan_sources(int(sold.timestamp())+90)
            self.assertEqual(manager.summary("stock")["total_cases"],2)
            # Read-only scan does not mutate either original file.
            with sqlite3.connect(path) as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM positions").fetchone()[0],1)

    def test_gmo_json_completed_round_trip_and_paper_d15_are_not_mixed(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            stock=root/"stock";stock.mkdir()
            (stock/"PROJECTGMO_V1.0_account_state.json").write_text(json.dumps({
               "buy_fills":[{"code":"005930","time":at("2026-11-11",10).isoformat(),
                             "qty":10,"price":1000}],
               "fills":[{"code":"005930","time":at("2026-11-12",14).isoformat(),
                         "qty":4,"price":1100},
                        {"code":"005930","time":at("2026-11-12",14,15).isoformat(),
                         "qty":6,"price":1120}],
               "positions":{}
            }))
            cases,issues=sources.scan(root,int(at("2026-11-13").timestamp()))
            gmo=[x for x in cases if x["strategy"]=="gmo"]
            self.assertEqual(len(gmo),1)
            self.assertEqual(gmo[0]["cohort"],"ACTUAL_FILLED")
            self.assertEqual(gmo[0]["entry_price"],1000)
            self.assertEqual(gmo[0]["exit_at"],at("2026-11-12",14,15).isoformat())
            self.assertEqual(gmo[0]["frame"],"gmo")

    def test_full_backup_contains_and_verifies_new_research_journal(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)/"app";root.mkdir()
            manager=high.Tracker(root)
            manager.register("coin","ma","day","KRW-ETH","ACTUAL_FILLED",
                1100,at("2026-11-12").isoformat(),"coin:MA:5")
            archive=Path(t)/"backup.zip"
            made=json.loads(backup.make_backup(str(root),str(archive)))
            self.assertTrue(made["ok"])
            self.assertTrue(backup.validate_backup(str(archive))["ok"])
            import zipfile
            with zipfile.ZipFile(archive) as z:
                self.assertIn("POST_EXIT_HIGH_V053.sqlite3",z.namelist())

    def test_integration_does_not_replace_order_execution(self):
        text=(ROOT/"app/src/main/python/post_exit_runtime_v053.py").read_text(encoding="utf-8")
        self.assertIn("native_stock_tick(self,tick)",text)
        self.assertIn("native_coin_mark(self,code,price,seen)",text)
        self.assertIn("_pinned_stocks=tracking_pins",text)
        self.assertNotIn("broker.fok(",text)
        self.assertNotIn("manual_sell(",text)


if __name__=="__main__":unittest.main()
