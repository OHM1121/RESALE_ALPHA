"""v046 all-source read-only evidence export, regression and perf checks."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import datetime as dt
import hashlib
import json
import sqlite3
import sys
import threading
import time
import zipfile

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import ai_pattern_diagnosis_v042 as patterns
import ai_strategy_analysis_v040 as analyzer
import ai_trade_lifecycle_v042 as lifecycle
import android_ai_evidence_export_v046 as export
import android_ledger_backup as backup
import market_trend_runtime_v043 as runtime
import market_trend_v043 as mt

JAVA=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text(encoding="utf-8")
GRADLE=(ROOT/"app/build.gradle").read_text(encoding="utf-8")
VIEW=(ROOT/"app/src/main/java/com/artitrade/mobile/AiEvidenceVisualV046.java").read_text(encoding="utf-8")


class V046PerformanceEvidence(TestCase):
    def test_signal_identity_index_preserves_first_exact_match(self):
        signals=[
            {"frame":"5m","symbol":"KRW-BTC","raw":{"id":3,"ref_ts":"20261115T09"},"features":{"score":1}},
            {"frame":"5m","symbol":"KRW-BTC","raw":{"id":3,"ref_ts":"20261115T09"},"features":{"score":2}},
            {"frame":"30m","symbol":"KRW-BTC","raw":{"id":3,"ref_ts":"20261115T09"},"features":{"score":3}},
            {"frame":"5m","symbol":"KRW-ETH","raw":{"id":3,"ref_ts":"20261115T09"},"features":{"score":4}},
        ]
        index=analyzer._signal_index(signals)
        target={"tf":"5m","market":"KRW-BTC","candidate_id":3}
        self.assertIs(analyzer._link(signals,target,"coin","ma",index=index),signals[0])
        self.assertIs(analyzer._link(signals,dict(target,candidate_id=44,
                      ref_ts="20261115T09"),"coin","ma",index=index),signals[0])
        self.assertIsNone(analyzer._link(signals,dict(target,market="KRW-XRP"),
                           "coin","ma",index=index))
        self.assertIsNone(analyzer._link(signals,dict(target,ref_ts="20261116T09",
                           candidate_id=44),"coin","ma",index=index))

    def test_index_speedup_quantified_against_linear_reference(self):
        signals=[]
        for i in range(6000):
            signals.append({"frame":"5m","symbol":"KRW-"+str(i%300),
                "raw":{"id":i,"ref_ts":str(i)},"features":{}})
        positions=[{"tf":"5m","market":"KRW-"+str(i%300),"candidate_id":i}
                   for i in range(5500,6000)]
        start=time.perf_counter()
        old=[]
        for item in positions:
            for signal in signals:
                if (signal["frame"]==item["tf"] and
                    signal["symbol"]==item["market"] and
                    signal["raw"]["id"]==item["candidate_id"]):
                    old.append(signal);break
        legacy=time.perf_counter()-start
        start=time.perf_counter()
        index=analyzer._signal_index(signals)
        found=[analyzer._link(signals,p,"coin","ma",index=index) for p in positions]
        new=time.perf_counter()-start
        self.assertEqual([id(x) for x in old],[id(x) for x in found])
        factor=legacy/max(1e-9,new)
        print("V046 SIGNAL_LINK: linear_s=%.6f indexed_s=%.6f speedup=%.1fx"
              % (legacy,new,factor))
        # Very generous to avoid timing noise on shared CI runners.
        self.assertGreater(factor,2.0)

    def test_time_weighted_score_matches_full_scan(self):
        events=[(1_780_000_000+i*180,float((i%21)-10)) for i in range(1800)]
        start=events[1500][0]+40
        end=events[1750][0]+100
        reference_total=reference_seconds=0.0
        reference_n=0
        for i,(ts,score) in enumerate(events):
            if ts>=end:break
            lower=max(start,ts)
            upper=min(end,ts+180,events[i+1][0] if i+1<len(events) else end)
            seconds=max(0,upper-lower)
            if seconds:
                reference_total+=score*seconds
                reference_seconds+=seconds
                reference_n+=1
        want=(round(reference_total/reference_seconds,4),round(reference_seconds,2),reference_n)
        self.assertEqual(lifecycle._market_average(events,start,end,"coin"),want)
        at=events[1700][0]+40
        self.assertEqual(lifecycle._market_at(events,at,"coin"),events[1700][1])
        self.assertIsNone(lifecycle._market_at(events,events[-1][0]+200,"coin"))
        self.assertEqual(lifecycle._market_average(events,start,start,"coin"),
                         (None,0,0))

    def test_pattern_basis_uses_selected_pattern_not_last_iteration(self):
        observed=[
            {"outcome":-5.0,"closed":True,
             "precursors":{"market_weak","trend_hold"}},
            {"outcome":-2.0,"closed":True,
             "precursors":{"market_weak"}},
            {"outcome":1.0,"closed":True,
             "precursors":{"trend_hold"}},
            {"outcome":-1.0,"closed":False,
             "precursors":{"volatility_rise"}},
        ]
        result=patterns._best_pattern(observed,observed,True)
        self.assertEqual(result["code"],"market_weak")
        self.assertEqual(result["basis"],"종료거래")

    def test_preclose_avoids_quotes_when_no_timeframe_is_due(self):
        c=object.__new__(runtime.Coordinator)
        c.modes={"coin":"MARKET_TREND","stock":"PAUSED"}
        c.lock=threading.RLock()
        c._candidates={"coin":{"KRW-BTC":("CRYPTO","KRW-BTC")},"stock":{}}
        c._performance={"coin":{"preclose_cycles":0,"preclose_no_due_cycles":0,
                                "preclose_due_cycles":0,"preclose_symbols_examined":0}}
        def forbidden(*args):
            raise AssertionError("No quote requests outside the due window")
        c._quote=forbidden
        c._preclose_candidates("coin",dt.datetime(2026,11,15,9,13,0,tzinfo=mt.KST))
        self.assertEqual(c._performance["coin"]["preclose_no_due_cycles"],1)
        self.assertEqual(c._performance["coin"]["preclose_symbols_examined"],0)

    def test_profiler_is_excluded_from_ledger_packet_and_visible_in_runtime(self):
        c=object.__new__(runtime.Coordinator)
        c.lock=threading.RLock()
        c.modes={"stock":"ORIGINAL","coin":"MARKET_TREND"}
        c.scores={"stock":20,"coin":-20}
        c.manual_trend={"stock":False,"coin":True}
        c.mode_details={"stock":"정상","coin":"정상"}
        c._candidates={"stock":{},"coin":{}}
        c._last_fetch_error={}
        c._performance={name:{
            "preclose_cycles":10,"preclose_no_due_cycles":9,
            "preclose_due_cycles":1,"preclose_symbols_examined":40,
            "stages":{"preclose":{"calls":10,"total_ms":80.0,"max_ms":20.0}}
        } for name in ("stock","coin")}
        lean=c.snapshot()
        detailed=c.snapshot(include_performance=True)
        self.assertNotIn("performance",lean["coin"])
        self.assertEqual(detailed["coin"]["performance"]["stages"]["preclose"]["mean_ms"],8.0)
        self.assertEqual(detailed["coin"]["performance"]["preclose_no_due_cycles"],9)
        self.assertTrue(lean["coin"]["manual_trend"])

    def test_full_export_includes_trend_journal_and_exact_numeric_evidence(self):
        with TemporaryDirectory() as work:
            root=Path(work)/"phone"
            root.mkdir()
            db=root/"market_research_v033.sqlite3"
            with sqlite3.connect(db) as c:
                c.executescript("CREATE TABLE score_events(market TEXT,ts INT,score REAL);")
                c.executemany("INSERT INTO score_events VALUES (?,?,?)",
                    [("coin",100,-20),("coin",200,20),("coin",300,0)])
            trend=root/"market_trend_v043.sqlite3"
            with sqlite3.connect(trend) as c:
                c.executescript("""
                    CREATE TABLE events(event_key TEXT,market TEXT,strategy TEXT,
                        frame TEXT,mode TEXT,reason TEXT);
                    CREATE TABLE gate_events(at REAL,market TEXT);
                    CREATE TABLE manual_trend_mode(market TEXT,enabled INT,changed_at REAL);
                    CREATE TABLE stop_reentry_blocks(market TEXT);
                    CREATE TABLE watch_observations(market TEXT);
                """)
                c.executemany("INSERT INTO events VALUES (?,?,?,?,?,?)",[
                    ("MT43:SIG:coin:KRW-BTC:5m:1","coin","general","5m","ACTUAL","BOUGHT"),
                    ("MT43:BUY:coin:general:5m:KRW-BTC:1:FILLED","coin","general","5m","PAPER","MT_BUY_FILLED"),
                    ("MT43:SIG:coin:KRW-ETH:5m:1","coin","general","5m","VIRTUAL","INSUFFICIENT_DEPTH_OR_CASH")
                ])
                c.execute("INSERT INTO manual_trend_mode VALUES ('coin',1,100)")
            account=root/"coin/base/upbit_paper.sqlite3"
            account.parent.mkdir(parents=True)
            with sqlite3.connect(account) as c:
                c.executescript("""
                  CREATE TABLE positions(status TEXT);
                  CREATE TABLE executions(side TEXT);
                  INSERT INTO positions VALUES ('OPEN');
                  INSERT INTO executions VALUES ('BUY');
                """)
            report={"ok":True,"scope":"coin/general","source":"TEST_SQLITE",
                "frames":[{"frame":"5m","label":"5분봉","signals":3,
                  "entries":1,"evaluated":1,"open":0,"closed_missing":0,
                  "avg_realized_pct":-2.5,
                  "diagnosis":{"negative":{"code":"market_weak","n":1,
                     "comparison_n":2},"effect":{"method":"IN_SAMPLE_AVOID_ENTRY",
                      "sample_n":1,"excluded_n":1,"delta_pp":2.5,
                      "validated":False}}}],
                "market_trend_rsi20":{"signals":2,"filled":1,"virtual":1},"error":[]}
            before={x.relative_to(root).as_posix():hashlib.sha256(x.read_bytes()).hexdigest()
                    for x in root.rglob("*.sqlite3")}
            reports=json.loads(export.build_reports(root,"coin/general",json.dumps(report)))
            detail=json.loads(reports["analysis/AI_QUANTITATIVE_V046.json"])
            self.assertEqual(detail["score_history"]["negative_samples"],1)
            self.assertEqual(detail["score_history"]["zero_or_positive_samples"],2)
            self.assertEqual(detail["trend_signal_decisions"]["confirmed_fills"][0]["execution_mode"],"PAPER")
            self.assertEqual(detail["frame_statistics"][0]["historical_filter_scenario"]["n_comparison"],1)
            self.assertFalse(detail["frame_statistics"][0]["historical_filter_scenario"]["validated"])
            zipped=Path(work)/"out.zip"
            result=json.loads(backup.make_backup(str(root),str(zipped),json.dumps(reports)))
            self.assertTrue(result["ok"])
            with zipfile.ZipFile(zipped) as z:
                manifest=json.loads(z.read("manifest.json"))
                self.assertIn("market_trend_v043.sqlite3",manifest["strategy_files"])
                self.assertIn("analysis/AI_FULL_REPORT_V046.json",z.namelist())
                self.assertIsNone(z.testzip())
            after={x.relative_to(root).as_posix():hashlib.sha256(x.read_bytes()).hexdigest()
                   for x in root.rglob("*.sqlite3")}
            self.assertEqual(before,after)

    def test_java_export_and_diagrams_present_without_order_changes(self):
        self.assertIn("REQUEST_EXPORT_AI_EVIDENCE",JAVA)
        self.assertIn('getModule("android_ai_evidence_export_v046")',JAVA)
        self.assertIn('exportFullLedgerToDocument(data.getData(),report,scope)',JAVA)
        self.assertIn('AiEvidenceFlowV046 flow=new AiEvidenceFlowV046(this)',JAVA)
        # v057: original quantitative evidence is still generated and exported,
        # but user-facing AI result shows findings rather than raw coverage.
        self.assertNotIn("AiFeatureCoverageV046 coverageView",JAVA)
        self.assertIn("AiEvidenceFlowV046 flow",JAVA)
        self.assertRegex(GRADLE,r"versionCode\s+(?:5[3-9]|[6-9][0-9])")
        self.assertIn('package com.artitrade.mobile',VIEW)
