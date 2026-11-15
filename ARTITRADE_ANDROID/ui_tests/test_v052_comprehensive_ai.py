"""v052 trade-centric AI: every source family, source clocks and mode isolation."""
import datetime as dt
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import ai_comprehensive_v052 as comprehensive


def seed_ai(db,entry):
    with sqlite3.connect(db) as c:
        c.executescript("""
        CREATE TABLE entry_candle_context_v047(
            strategy TEXT,evidence_key TEXT,role TEXT,entry_mode TEXT,
            frame TEXT,symbol TEXT,entry_ts INTEGER,state TEXT,context_json TEXT);
        CREATE TABLE trade_evidence_v048(
            strategy TEXT,evidence_key TEXT,role TEXT,entry_mode TEXT,
            frame TEXT,symbol TEXT,entry_ts INTEGER,source_position_id TEXT,
            execution_kind TEXT,market_id TEXT,exit_ts INTEGER,updated_at INTEGER,
            entry_json TEXT,post_json TEXT);
        CREATE TABLE position_lifecycle_v042(
            strategy TEXT,position_key TEXT,frame TEXT,symbol TEXT,
            entry_ts INTEGER,exit_ts INTEGER,summary_json TEXT);
        CREATE TABLE position_factor_events_v042(
            strategy TEXT,position_key TEXT,frame TEXT,symbol TEXT,
            entry_ts INTEGER,observed_minute INTEGER,features_json TEXT);
        CREATE TABLE candle_observations(
            strategy TEXT,frame TEXT,symbol TEXT,bar_ts INTEGER,
            high REAL,low REAL,close REAL,volume REAL);
        CREATE TABLE indicator_observations(
            strategy TEXT,frame TEXT,symbol TEXT,observed_minute INTEGER,
            features_json TEXT,origin TEXT);
        CREATE TABLE market_score_observations(score_ts INTEGER,score REAL);
        CREATE TABLE feature_snapshots(
            strategy TEXT,frame TEXT,signal_key TEXT,event_ts INTEGER,
            first_captured INTEGER,origin TEXT,features_json TEXT);
        CREATE TABLE benchmark_candles_v048(
            benchmark TEXT,frame TEXT,bar_ts INTEGER,close REAL);
        CREATE TABLE trade_exit_events_v048(
            strategy TEXT,evidence_key TEXT,execution_key TEXT,at_ms INTEGER,
            payload TEXT);
        CREATE TABLE position_observations(
            strategy TEXT,frame TEXT,position_key TEXT,observed_minute INTEGER,
            mark REAL,quantity REAL,pnl REAL,market_score REAL,extra_json TEXT);
        """)
        c.execute("""INSERT INTO entry_candle_context_v047 VALUES(
            'at','ACTUAL|ORIGINAL|at|5m|005930|entry|42','ACTUAL','ORIGINAL',
            '5m','005930',?,'FINALIZED',?)""",
            (entry,json.dumps({"high_slope_pct_per_bar":0.34,
                               "entry_candle_complete":True,
                               "volume_ratio30":1.8})))
        c.execute("""INSERT INTO trade_evidence_v048 VALUES(
            'at','evidence_42','ACTUAL','ORIGINAL','5m','005930',?,
            '42','PAPER','^KS11',?,?,
            ?,?)""",
            (entry,entry+2100,entry+2400,
             json.dumps({"02":{"value":{"spread_pct":0.12}},
                         "10":{"value":{"atr_pct":1.5}},
                         "11":{"value":{"vwap":51100}}}),
             json.dumps({"13":{"value":{"mfe_pct":3.2}},
                         "14":{"value":{"sold_pct":100,"steps":[{"filled_qty":1}]}}})))
        for n in range(35):
            bar=entry-300*(35-n)
            c.execute("INSERT INTO candle_observations VALUES(?,?,?,?,?,?,?,?)",
                      ("at","5m","005930",bar,51000+n,50000+n,50500+n,500+n))
        for n in range(4):
            bar=entry+300*n
            c.execute("INSERT INTO candle_observations VALUES(?,?,?,?,?,?,?,?)",
                      ("at","5m","005930",bar,51500+n,50000+n,51200+n,900+n))
        c.execute("INSERT INTO indicator_observations VALUES(?,?,?,?,?,?)",
                  ("at","5m","005930",entry-300,
                   json.dumps({"rsi":28,"atr":800}),"CLOSED_PRE_ENTRY"))
        c.execute("INSERT INTO indicator_observations VALUES(?,?,?,?,?,?)",
                  ("at","5m","005930",entry+120,
                   json.dumps({"rsi":35,"volume_ratio":1.4}),"SOURCE_LIVE"))
        c.execute("INSERT INTO indicator_observations VALUES(?,?,?,?,?,?)",
                  ("at","5m","005930",entry+420,
                   json.dumps({"rsi":39,"volume_ratio":1.8}),"SOURCE_LIVE"))
        c.execute("INSERT INTO market_score_observations VALUES(?,?)",(entry-90,-27))
        c.execute("INSERT INTO market_score_observations VALUES(?,?)",(entry+30,100))
        c.execute("INSERT INTO feature_snapshots VALUES(?,?,?,?,?,?,?)",
                  ("at","5m","signal_42|5m|005930",entry-50,entry-55,
                   "SOURCE_SIGNAL",json.dumps({"williams_r":-93})))
        c.execute("INSERT INTO feature_snapshots VALUES(?,?,?,?,?,?,?)",
                  ("at","5m","late|5m|005930",entry-20,entry+500,
                   "SOURCE_SIGNAL",json.dumps({"future_alpha":999})))
        for n in range(10):
            c.execute("INSERT INTO benchmark_candles_v048 VALUES(?,?,?,?)",
                      ("^KS11","5m",entry-300*(10-n),2500+n))
        c.execute("INSERT INTO position_factor_events_v042 VALUES(?,?,?,?,?,?,?)",
                  ("at","pkey_42","5m","005930",entry,entry+300,
                   json.dumps({"bid_imbalance":0.61,"trade_strength":153})))
        c.execute("INSERT INTO position_observations VALUES(?,?,?,?,?,?,?,?,?)",
                  ("at","5m","pkey_42",entry+600,51900,1,3500,-23,
                   json.dumps({"spread_pct":0.18})))
        c.execute("INSERT INTO trade_exit_events_v048 VALUES(?,?,?,?,?)",
                  ("at","evidence_42","fill_sell_42",(entry+2100)*1000,
                   json.dumps({"filled_qty":1,"fee":23})))
        c.commit()


class ComprehensiveV052(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.db=self.root/"stock"/"ai"/"AI_RESEARCH_V040.sqlite3"
        self.db.parent.mkdir(parents=True)
        self.entry=int(dt.datetime(2026,11,12,10,30,
                       tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp())
        seed_ai(self.db,self.entry)
        self.life=[{
            "position_key":"pkey_42","frame":"5m","symbol":"005930",
            "entry_ts":self.entry,"exit_ts":self.entry+2100,
            "entry_features":{"rsi":27},"entry_feature_origin":"PRE_ENTRY_CLOSED_OHLCV",
            "exit_features":{"rsi":56},"holding_mean":{"spread":0.13},
            "market_entry":-25,"market_exit":17,
            "duration_seconds":2100,"bar_observations":7,
            "max_favorable_pct":3.2,"max_adverse_pct":-1.1}]
        self.raw={"id":42,"strategy":"at","tf":"5m","code":"005930",
                  "entry_at":dt.datetime.fromtimestamp(
                      self.entry,dt.timezone.utc).isoformat(),
                  "status":"CLOSED","execution_mode":"PAPER",
                  "entry_price":51000,"qty0":1}
        self.trades=[{"raw":self.raw,"frame":"5m","at":self.entry,
            "signal_ts":self.entry-30,"features":{"rsi":28,"score":45},
            "current_features":{"rsi":48},"closed_roi":1.2,
            "state":"CLOSED"}]

    def tearDown(self):
        self.tmp.cleanup()

    def test_broad_source_join_and_future_leakage_barrier(self):
        result=comprehensive.analyze(self.root,"stock","at",[],self.trades,
                                     self.life,now=self.entry+2500)
        self.assertTrue(result["ok"])
        self.assertEqual(result["filled_cases"],1)
        self.assertGreaterEqual(result["distinct_numeric_factors"],20)
        self.assertGreaterEqual(result["sidecar_rows_loaded"]["ohlcv_history"],39)
        self.assertGreaterEqual(result["trade_joined_source_rows"]["ohlcv_history"],34)
        self.assertGreaterEqual(result["trade_joined_source_rows"]["holding_events"],1)
        self.assertGreaterEqual(result["trade_joined_source_rows"]["exit_events"],1)
        self.assertGreaterEqual(result["trade_joined_source_rows"]["position_snapshots"],1)
        self.assertEqual(result["by_execution_mode"]["PAPER"],1)
        journal=self.root/"stock"/"ai"/"AI_COMPREHENSIVE_V052.sqlite3"
        with sqlite3.connect(journal) as c:
            payload=json.loads(c.execute(
               "SELECT case_json FROM trade_cases WHERE scope='stock/at'").fetchone()[0])
        self.assertEqual(payload["mode"],"PAPER")
        values={(x["phase"],x["key"],x["source"]):x["value"]
                for x in payload["features"]}
        self.assertTrue(any(x[0]=="entry" and "rsi" in x[1] for x in values))
        self.assertTrue(any(x[0]=="entry" and "high_slope" in x[1] for x in values))
        self.assertTrue(any(x[0]=="entry" and "williams_r" in x[1] for x in values))
        self.assertTrue(any(x[0]=="holding" and "rsi_mean" in x[1] for x in values))
        self.assertTrue(any(x[0]=="exit" and "fee" in x[1] for x in values))
        self.assertTrue(any(x[0]=="entry" and "market_score" in x[1]
                            and x[2]=="V052_POINT_IN_TIME_MARKET_SCORE"
                            and x[3]==-27 for x in
               ((k[0],k[1],k[2],v) for k,v in values.items())))
        self.assertFalse(any(x[0]=="entry" and "future_alpha" in x[1] for x in values))
        self.assertTrue(any(x[0]=="unverified" and "future_alpha" in x[1] for x in values))
        self.assertTrue(any(x[0]=="holding" and "mfe_pct" in x[1] for x in values))
        self.assertFalse(any(x[0]=="post" and "mfe_pct" in x[1] for x in values))

    def test_same_ticker_reentry_with_different_position_id_not_joined(self):
        other=dict(self.raw,id=99,entry_at=dt.datetime.fromtimestamp(
            self.entry+120,dt.timezone.utc).isoformat(),
            execution_mode="LIVE")
        trades=self.trades+[{"raw":other,"frame":"5m","at":self.entry+120,
            "signal_ts":None,"features":{},"current_features":{},
            "closed_roi":-2,"state":"CLOSED"}]
        report=comprehensive.analyze(self.root,"stock","at",[],trades,
                                     self.life,now=self.entry+2500)
        self.assertEqual(report["filled_cases"],2)
        with sqlite3.connect(self.root/"stock"/"ai"/"AI_COMPREHENSIVE_V052.sqlite3") as c:
            rows=[json.loads(x[0]) for x in c.execute("SELECT case_json FROM trade_cases")]
        first=next(x for x in rows if x["source_id"]=="42")
        next_entry=next(x for x in rows if x["source_id"]=="99")
        self.assertIn("V048_APPROVED_10",first["sources"])
        self.assertNotIn("V048_APPROVED_10",next_entry["sources"])
        self.assertEqual(next_entry["mode"],"LIVE")
        self.assertGreaterEqual(sum(next_entry["missing"].values()),1)

    def test_virtual_candidate_is_not_closed_or_realized(self):
        shadow={"raw":{"id":"shadow4","code":"000660","tf":"5m",
                       "target_hit":True},"frame":"5m",
                "signal_ts":self.entry,"features":{"rsi":19}}
        report=comprehensive.analyze(self.root,"stock","at",[shadow],[],
                                     [],now=self.entry+2500)
        self.assertEqual(report["closed_with_verified_roi"],0)
        self.assertEqual(report["unfilled_virtual_candidates"],1)
        self.assertEqual(report["virtual_observed_hit_n"],1)
        self.assertEqual(report["by_execution_mode"]["SHADOW_UNFILLED"],1)
        with sqlite3.connect(self.root/"stock"/"ai"/"AI_COMPREHENSIVE_V052.sqlite3") as c:
            data=json.loads(c.execute("SELECT case_json FROM trade_cases").fetchone()[0])
        self.assertIsNone(data["roi"])
        self.assertFalse(any(x["key"]=="observed_target_hit" and x["phase"]=="entry"
                         for x in data["features"]))
        self.assertTrue(any(x["key"]=="observed_target_hit" and x["phase"]=="virtual"
                         for x in data["features"]))

    def test_unknown_timestamp_cannot_be_promoted_to_preentry(self):
        raw=dict(self.raw,id=101,entry_at=None,tf="5m")
        trade=dict(self.trades[0],raw=raw,at=None,signal_ts=None,
                   features={"rsi":1000},closed_roi=None)
        report=comprehensive.analyze(self.root,"stock","at",[],[trade],
                                     [],now=self.entry+2500)
        self.assertEqual(report["closed_with_verified_roi"],0)
        with sqlite3.connect(self.root/"stock"/"ai"/"AI_COMPREHENSIVE_V052.sqlite3") as c:
            data=json.loads(c.execute("SELECT case_json FROM trade_cases").fetchone()[0])
        self.assertFalse(any(x["phase"]=="entry" and x["key"]=="rsi"
                             for x in data["features"]))

    def test_temporal_out_of_sample_not_claimed_on_small_evidence(self):
        base=self.entry
        cases=[]
        for j in range(20):
            c=comprehensive._case(dict(code=f"000{j:03d}",id=j),
                                  "5m",base+j*3600,"PAPER","FILLED","CLOSED",
                                  roi=1 if j%2 else -1)
            comprehensive._add(c,"entry",{"rsi":20+j},"PRE_ENTRY_SOURCE",base+j*3600-1)
            cases.append(c)
        factors=comprehensive._rank_cases(cases)
        self.assertTrue(factors)
        self.assertEqual(factors[0]["status"],"EXPLORATORY_ONLY")
        self.assertIsNone(factors[0]["holdout"])
        self.assertFalse(factors[0]["auto_apply_to_orders"])


if __name__=="__main__":
    unittest.main()
