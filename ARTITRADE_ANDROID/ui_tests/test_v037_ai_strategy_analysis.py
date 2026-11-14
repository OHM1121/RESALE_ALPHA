"""v037 evidence-based strategy report regressions; no broker/order actions."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parents[1]/"app/src/main/python"))
import ai_strategy_analysis_v037 as ai


class ReportContract(unittest.TestCase):
    def test_main_navigation_and_safe_readonly_contract(self):
        src = (Path(__file__).parents[1] /
               "app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
        self.assertIn('bottomNavItem("analysis","◇","AI분석")', src)
        self.assertIn('else if("analysis".equals(page))renderAiAnalysis();', src)
        self.assertIn('private void runAiAnalysis()', src)
        self.assertIn('private void renderAiAnalysis()', src)
        self.assertIn('getModule("ai_strategy_analysis_v040")', src)
        self.assertIn('final String chosenMarket=market,chosenStrategy=strategy;', src)
        self.assertIn('if(scope.equals(aiAnalysisScope))', src)
        for item in ("손실 선행조건", "반복 하락패턴", "반복 상승패턴",
                     "개선 전략", "정량 개선효과"):
            self.assertIn(item, src)
        self.assertNotIn("지표 축적률", src)
        self.assertNotIn("callAttr(\"apply_settings\"", src[src.index("private void runAiAnalysis()"):
                                                     src.index("private void renderResearch()")])

    def test_no_future_data_leakage(self):
        before = {"score": 45, "atr_pct": 3.2, "market_score": -29,
                  "features": '{"volume_ratio":4.1,"rsi":83,"future_max_price":99999,'
                              '"exit_profit":-900,"post_sell_volume":400}',
                  "observed_max_price":900, "peak":1000, "target_hit":True}
        f = ai._entry_features(before, 100)
        self.assertEqual(f["atr_pct"], 3.2)
        self.assertEqual(f["market_score"], -29)
        self.assertEqual(f["volume_ratio"], 4.1)
        self.assertNotIn("future_max_price", f)
        self.assertNotIn("exit_profit", f)
        self.assertNotIn("observed_max_price", f)

    def test_open_position_never_counted_as_loss(self):
        a, state = ai._pnl_return({"status":"OPEN", "net_pnl":-8000,
                                   "initial_cost":100000}, [])
        self.assertIsNone(a)
        self.assertEqual(state,"OPEN")

    def test_closed_net_pnl_requires_cost_basis(self):
        a, state = ai._pnl_return({"status":"CLOSED", "net_pnl":-6000}, [])
        self.assertIsNone(a)
        self.assertEqual(state,"COST_BASIS_MISSING")
        b, state2 = ai._pnl_return({"status":"CLOSED","net_pnl":-6000,
                                    "initial_cost":200000}, [])
        self.assertEqual(b, -3.)
        self.assertEqual(state2,"VERIFIED_NET_PNL_COST")

    def test_no_synthetic_sell_from_price_high(self):
        raw={"candidates":[{"signal_id":1,"tf":"5m","code":"0001",
                             "ref_ts":"20261114090000","signal_ts":"20261114090100",
                             "score":45, "observed_max_price":300}],
             "positions":[], "executions":[]}
        rs=json.dumps({"items":[{"signal_id":1,"tf":"5m","code":"0001",
                                "ref_ts":"20261114090000","bought":False,
                                "entry_observed":True, "target_hit":True}]})
        candidates, trades=ai._build_samples("stock","at",raw,rs,"{}",([],[]))
        self.assertEqual(len(candidates),1)
        self.assertEqual(trades,[])

    def test_strategy_cohorts_never_mix(self):
        self.assertNotEqual(ai.SOURCE["stock","at"], ai.SOURCE["stock","ma"])
        self.assertNotEqual(ai.SOURCE["coin","ma"], ai.SOURCE["coin","general"])

    def test_market_score_no_future_or_stale(self):
        stamps=[1000000000,1000000050]
        scores=[-31, 40]
        self.assertIsNone(ai._point_in_time_market(stamps,scores,999999999,"coin"))
        self.assertEqual(ai._point_in_time_market(stamps,scores,1000000030,"coin"), -31)
        self.assertIsNone(ai._point_in_time_market(stamps,scores,1000001100,"coin"))

    def synthetic(self, reverse=False):
        rows=[]
        for i in range(100):
            # Intraday groups across at least 20 distinct dates.
            at = f"2026-10-{(i//5)+1:02d}T09:{i%5:02d}:00+09:00"
            risk = i%2==0
            pnl = (-5. if risk else 2.)
            if reverse and i>=70: pnl=(3. if risk else -4.)
            rows.append({"key":f"k{i}","frame":"5m","at":at,
                         "timestamp":ai._time(at),"features":{"atr_pct":4.0 if risk else 1.0},
                         "return_pct":pnl,"pnl_state":"VERIFIED_NET_PNL_COST"})
        return rows

    def test_holdout_improvement_is_quantified_only_when_supported(self):
        trades=self.synthetic()
        result=ai._summarize_frame("5m",[],trades,"EXE_SQLITE_READONLY",False,[])
        self.assertEqual(result["status"],"VALIDATED_CANDIDATE")
        q=result["quantitative"]
        self.assertEqual(q["train_n"],70)
        self.assertEqual(q["test_n"],30)
        self.assertGreater(q["delta_pp"],0)
        self.assertGreater(q["delta_ci95_pp"][0],0)
        self.assertTrue(q["validated"])

    def test_overfit_is_not_promoted_when_holdout_reverses(self):
        result=ai._summarize_frame("5m",[],self.synthetic(reverse=True),
                                   "EXE_SQLITE_READONLY",False,[])
        self.assertNotEqual(result["status"],"VALIDATED_CANDIDATE")
        self.assertFalse(result["quantitative"]["validated"])

    def test_sparse_rows_do_not_claim_effect(self):
        result=ai._summarize_frame("5m",[],self.synthetic()[:7],
                                   "SYNC_SNAPSHOT",False,[])
        self.assertEqual(result["status"],"INSUFFICIENT")
        self.assertIsNone(result["quantitative"])

    def test_read_only_original_sqlite(self):
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/"coin/ma/upbit_paper.sqlite3"
            db.parent.mkdir(parents=True)
            with sqlite3.connect(db) as c:
                c.execute("CREATE TABLE candidates(id INTEGER,tf TEXT,market TEXT,ref_at TEXT,score REAL)")
                c.execute("INSERT INTO candidates VALUES(1,'5m','KRW-BTC','2026-11-14T09:00:00+09:00',45)")
                c.execute("CREATE TABLE positions(id INTEGER,tf TEXT,market TEXT,ref_at TEXT,status TEXT)")
                c.execute("CREATE TABLE executions(id INTEGER,position_id INTEGER,side TEXT)")
            start=db.read_bytes()
            report=json.loads(ai.analyze(folder,"coin","ma"))
            self.assertTrue(report["ok"])
            self.assertEqual(report["frames"][0]["signals"],1)
            self.assertEqual(report["frames"][0]["status"],"INSUFFICIENT")
            self.assertEqual(db.read_bytes(),start)


if __name__=="__main__":
    unittest.main()
