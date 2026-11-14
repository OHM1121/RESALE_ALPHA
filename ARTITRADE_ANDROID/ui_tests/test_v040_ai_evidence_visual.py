"""v040 scope isolation, open-sample immediately usable, archived indicator capture and visual UI."""
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

import sys
ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import ai_research_v040 as capture
import ai_strategy_analysis_v040 as model
import android_ledger_backup as backup
import android_research_bundle as bundle

VISUAL=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
CHART=(ROOT/"app/src/main/java/com/artitrade/mobile/AiAnalysisVisualV040.java").read_text()


def db_make(root,market,strategy,closed=False):
    relative,table=model.SCOPES[market,strategy]
    db=Path(root)/relative;db.parent.mkdir(parents=True,exist_ok=True)
    at="2026-11-12T09:00:00+09:00"
    with sqlite3.connect(db) as conn:
        if (market,strategy)==("stock","ma"):
            conn.execute("CREATE TABLE candidates(id INTEGER,tf TEXT,code TEXT,created_at TEXT,ref_ts TEXT,score REAL,features TEXT)")
            conn.execute("INSERT INTO candidates VALUES(1,'5m','000001',?,?,62,?)",
                         (at,"20261112085800",json.dumps({"atr_pct":4.0,
                          "williams_r":-12,"volume_ratio":2.8,"rsi":80})))
            conn.execute("""CREATE TABLE positions(id INTEGER,candidate_id INTEGER,tf TEXT,
                         code TEXT,status TEXT,entry_price REAL,last_price REAL,
                         initial_cost REAL,realized_net_krw REAL,entry_at TEXT,closed_at TEXT)""")
            conn.execute("INSERT INTO positions VALUES(8,1,'5m','000001',?,?,?,?,?, ?, ?)",
                         ("CLOSED" if closed else "OPEN",100,103,100000,
                          -5000 if closed else None,at,
                          "2026-11-12T10:00:00+09:00" if closed else None))
        elif (market,strategy)==("coin","general"):
            conn.execute("CREATE TABLE candidates(id INTEGER,tf TEXT,market TEXT,ref_at TEXT,created_at TEXT,score REAL,reason TEXT)")
            conn.execute("INSERT INTO candidates VALUES(1,'30m','KRW-BTC','20261112083000',?,71,'volume')",(at,))
            conn.execute("CREATE TABLE positions(id INTEGER,tf TEXT,market TEXT,ref_at TEXT,status TEXT,entry REAL,last_price REAL,entry_at TEXT)")
            conn.execute("INSERT INTO positions VALUES(4,'30m','KRW-BTC','20261112083000','OPEN',100,106,?)",(at,))
        elif (market,strategy)==("coin","ma"):
            conn.execute("CREATE TABLE candidates(id INTEGER,tf TEXT,market TEXT,ref_at TEXT,created_at TEXT,score REAL)")
            conn.execute("INSERT INTO candidates VALUES(1,'60m','KRW-ETH','20261112083000',?,66)",(at,))
            conn.execute("CREATE TABLE positions(id INTEGER,tf TEXT,market TEXT,ref_at TEXT,status TEXT,entry REAL,last_price REAL,entry_at TEXT)")
            conn.execute("INSERT INTO positions VALUES(4,'60m','KRW-ETH','20261112083000','OPEN',100,99,?)",(at,))
        elif (market,strategy)==("stock","gmo"):
            conn.execute("CREATE TABLE gmo_candidates(id INTEGER,code TEXT,event_date TEXT,gmo_score_at_signal REAL)")
            conn.execute("INSERT INTO gmo_candidates VALUES(1,'000002','2026-11-12',89)")
        else:
            conn.execute(f'CREATE TABLE "{table}"(id INTEGER,ts TEXT)')
    return db


class EvidenceV040(unittest.TestCase):
    def test_current_stock_ma_5m_open_is_analyzed_today(self):
        with tempfile.TemporaryDirectory() as root:
            db_make(root,"stock","ma")
            score=Path(root)/"market_research_v033.sqlite3"
            with sqlite3.connect(score) as c:
                c.execute("CREATE TABLE score_events(market TEXT,ts INTEGER,score REAL)")
                t=capture._timestamp("2026-11-12T09:00:00+09:00")
                c.executemany("INSERT INTO score_events VALUES('stock',?,?)",[
                    (t+300,20.),(t+600,-10.),(t-900,70.)])
            r=json.loads(model.analyze(root,"stock","ma"))
            self.assertTrue(r["ok"])
            self.assertEqual(r["scope"],"stock/ma")
            self.assertEqual(len(r["frames"]),4)
            frame=r["frames"][0]
            self.assertEqual(frame["signals"],1)
            self.assertEqual(frame["entries"],1)
            self.assertEqual(frame["open"],1)
            self.assertEqual(frame["closed"],0)
            self.assertEqual(frame["avg_open_pct"],3)
            self.assertEqual(frame["avg_hold_market_score"],5.)
            self.assertEqual(frame["hold_market_samples"],2)
            self.assertEqual(frame["status"],"LIVE_OBSERVATION")
            self.assertIn("ATR",str(frame["patterns"]))
            self.assertEqual(r["frames"][1]["signals"],0)
            self.assertTrue(capture.db_path(root,"stock").exists())
            journal=capture.stored(root,"stock","ma")
            self.assertGreaterEqual(len(journal["features"]),1)
            self.assertEqual(len(journal["positions"]),1)
            with sqlite3.connect(capture.db_path(root,"stock")) as c:
                self.assertEqual(c.execute("SELECT count(*) FROM analysis_runs").fetchone()[0],1)

    def test_two_coin_strategies_do_not_share_analysis(self):
        with tempfile.TemporaryDirectory() as root:
            db_make(root,"coin","general")
            db_make(root,"coin","ma")
            a=json.loads(model.analyze(root,"coin","general"))
            b=json.loads(model.analyze(root,"coin","ma"))
            self.assertEqual(a["scope"],"coin/general")
            self.assertEqual(b["scope"],"coin/ma")
            self.assertEqual(a["frames"][1]["signals"],1)
            self.assertEqual(a["frames"][2]["signals"],0)
            self.assertEqual(b["frames"][1]["signals"],0)
            self.assertEqual(b["frames"][2]["signals"],1)
            self.assertEqual(a["frames"][1]["avg_open_pct"],6.0)
            self.assertEqual(b["frames"][2]["avg_open_pct"],-1.0)

    def test_gmo_has_one_strategy_card_not_fake_5min_frames(self):
        with tempfile.TemporaryDirectory() as root:
            db_make(root,"stock","gmo")
            r=json.loads(model.analyze(root,"stock","gmo"))
            self.assertEqual(len(r["frames"]),1)
            self.assertEqual(r["frames"][0]["label"],"수급매매")
            self.assertEqual(r["frames"][0]["signals"],1)
            self.assertEqual(r["frames"][0]["status"],"LIVE_OBSERVATION")

    def test_closed_one_sample_quantified_as_exploratory_not_verified(self):
        with tempfile.TemporaryDirectory() as root:
            db_make(root,"stock","ma",closed=True)
            r=json.loads(model.analyze(root,"stock","ma"))
            f=r["frames"][0]
            self.assertEqual(f["closed"],1)
            self.assertEqual(f["avg_realized_pct"],-5)
            self.assertEqual(f["status"],"EXPLORATORY")
            effect=f["improvement"]
            self.assertEqual(effect["sample_n"],1)
            self.assertEqual(effect["delta_pp"],5.)
            self.assertFalse(effect["validated"])
            self.assertEqual(effect["method"],"SCENARIO")

    def test_indicators_include_14_day_atr_williams_volume_rsi(self):
        bars=[]
        epoch=capture._timestamp("2026-11-12T09:30:00+09:00")
        for i in range(23):
            bars.append({"ts":epoch-(22-i)*60,"high":101+i,"low":90+i,
                         "close":96+i,"volume":100+i*5})
        features=capture.extract_features({"bars":bars,"entry_price":115},epoch)
        for name in ("atr","atr_pct","williams_r","volume_ratio","rsi","vwap"):
            self.assertIn(name,features)
        self.assertGreater(features["atr_pct"],0)
        self.assertGreaterEqual(features["williams_r"],-100)
        self.assertLessEqual(features["williams_r"],0)
        bars.append({"ts":epoch+3600,"high":100000,"low":1,
                     "close":80000,"volume":999999})
        self.assertEqual(features,
            capture.extract_features({"bars":bars,"entry_price":115},epoch))

    def test_phone_capture_accumulates_moving_journal_without_duplicate_signal(self):
        with tempfile.TemporaryDirectory() as root:
            at="2026-11-12T09:00:00+09:00"
            c={"id":7,"code":"005930","tf":"5m","ref_ts":"20261112085900",
               "at":at,"score":67,"features":json.dumps({"atr_pct":2.4,"williams_r":-58})}
            p={"id":9,"code":"005930","tf":"5m","ref_ts":"20261112085900",
               "status":"OPEN","entry_price":100,"last_price":101,"qty":2,"entry_at":at}
            first=capture.capture_scope(root,"stock","at",{"items":[c]},
                                        {"positions":[p]},now=1794440000)
            self.assertEqual(first,{"signals":1,"positions":1})
            p["last_price"]=104
            capture.capture_scope(root,"stock","at",{"items":[c]},
                                  {"positions":[p]},now=1794440065)
            out=capture.stored(root,"stock","at")
            self.assertEqual(len(out["features"]),1)
            self.assertEqual(len(out["positions"]),2)
            self.assertEqual(out["positions"][-1]["mark"],104)
            self.assertGreater(out["positions"][-1]["observed_minute"],
                               out["positions"][0]["observed_minute"])

    def test_market_score_and_bars_exported_with_stock_research(self):
        with tempfile.TemporaryDirectory() as root:
            rootpath=Path(root)
            t=capture._timestamp("2026-11-12T10:00:00+09:00")
            bars=[{"ts":t-(23-i)*60,"high":105+i,"low":95+i,
                   "close":100+i,"volume":100+i*2} for i in range(24)]
            signal={"id":15,"code":"005930","tf":"5m",
                    "ref_ts":"20261112095000","at":"2026-11-12T10:00:00+09:00",
                    "bars":bars}
            capture.capture_scope(root,"stock","at",{"items":[signal]}, {},now=t+60)
            with sqlite3.connect(capture.db_path(root,"stock")) as c:
                self.assertEqual(c.execute("SELECT count(*) FROM candle_observations").fetchone()[0],24)
            packet={"market_research":{"stock":{"score":27,"asof":t,
                     "source":"TEST_TIMELINE"}},
                    "stock":{"at":{"ledger":{},"research":{"items":[]}}}}
            capture.capture_packet(root,packet,now=t+60)
            with sqlite3.connect(capture.db_path(root,"stock")) as c:
                self.assertEqual(c.execute("SELECT score FROM market_score_observations").fetchone()[0],27.)
            self.assertTrue(backup._candidate(rootpath,capture.db_path(root,"stock")))
            export_folder=rootpath/"results";export_folder.mkdir()
            result=json.loads(bundle.make_bundle(root,str(export_folder),"stock"))
            self.assertTrue(result["ok"])
            with zipfile.ZipFile(export_folder/result["files"][0]["name"]) as z:
                names=z.namelist()
                self.assertIn("stock/ai/AI_RESEARCH_V040.sqlite3",names)

    def test_numeric_kst_yyyymmddhhmmss_not_misread_as_unix_ms(self):
        self.assertEqual(capture._timestamp(20261112090000),
                         capture._timestamp("20261112090000"))

    def test_native_visual_contains_numeric_geometry_not_old_text_report(self):
        self.assertIn('getModule("ai_strategy_analysis_v040")',VISUAL)
        self.assertIn('final String chosenMarket=market,chosenStrategy=strategy;',VISUAL)
        self.assertIn('scope.equals(report.optString("scope",""))',VISUAL)
        self.assertIn('private void renderAiAnalysis()',VISUAL)
        for label in ("개선효과 비교","지표 축적률","원인 후보 / 공통패턴","개선 전략",
                      "연구 신호","현재 보유"):
            self.assertIn(label,VISUAL)
        for cls in ("AiEvidenceMeterV040","AiReturnCompareV040"):
            self.assertIn(cls,VISUAL)
            self.assertIn("final class "+cls,CHART)
        self.assertNotIn('aiLine(note,"분석 기준"',VISUAL)
        self.assertNotIn('"1. 현황"',VISUAL)


if __name__=="__main__":
    unittest.main()
