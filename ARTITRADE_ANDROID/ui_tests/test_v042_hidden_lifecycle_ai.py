"""v042: hidden per-stock BUY/SELL/HOLD indicator lifecycle and result-only AI."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import json
import sqlite3
import sys
import unittest

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import ai_research_v040 as j
import ai_trade_lifecycle_v042 as lifecycle
import ai_pattern_diagnosis_v042 as diagnosis
import ai_strategy_analysis_v040 as analysis

UI=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
VIS=(ROOT/"app/src/main/java/com/artitrade/mobile/AiAnalysisVisualV040.java").read_text()


def setup_rows(root,market="stock",strategy="ma"):
    start=j._timestamp("2026-11-13T09:00:00+09:00")
    entry=start+35*300
    exit_at=start+45*300
    code="005930" if market=="stock" else "KRW-BTC"
    candles=[]
    for n in range(58):
        # The final 10 bars are substantially different. Entry features
        # must not read future RSI, Williams or ATR changes.
        v=100+n*.2+(20 if n>48 else 0)
        candles.append((start+n*300,v+3,v-4,v,float(100+n*5)))
    with j._connect(root,market) as conn:
        for ts,hi,lo,cl,volume in candles:
            conn.execute(
                "INSERT OR IGNORE INTO candle_observations "
                "(strategy,frame,symbol,bar_ts,high,low,close,volume,origin) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (strategy,"5m",code,ts,hi,lo,cl,volume,"TEST_COMPLETE_BAR"))
        for n in range(35*5,46*5):
            ts=start+n*60
            score=-61 if ts<entry+10*60 else -40
            conn.execute(
                "INSERT INTO market_score_observations(score_ts,score,source)"
                " VALUES (?,?,?)",(ts,score,"TEST_KST"))
    return code,entry,exit_at,candles


class LifecycleV042(TestCase):
    def test_buy_sell_hold_means_and_market_scores_are_internal_and_saved(self):
        with TemporaryDirectory() as root:
            symbol,entry,exit_at,candles=setup_rows(root)
            position={"id":7,"tf":"5m","code":symbol,
                      "entry_at":entry,"closed_at":exit_at,
                      "entry_price":100,"last_price":109,
                      "status":"CLOSED","qty":3,"sell_reason":"TRAILING_STOP"}
            values=lifecycle.rebuild(root,"stock","ma",[position],now=exit_at+300)
            self.assertEqual(len(values),1)
            row=values[0]
            for key in ("atr","rsi","williams_r","volume","volume_ratio","mfi",
                        "vwap","bollinger_bandwidth_pct"):
                self.assertIn(key,row["entry_features"],msg=key)
                self.assertIn(key,row["exit_features"],msg=key)
            self.assertIsNotNone(row["holding_mean"]["volume"])
            self.assertGreater(row["volume_observations"],0)
            self.assertEqual(row["market_entry"],-61)
            self.assertEqual(row["market_exit"],-40)
            self.assertGreater(row["market_holding_mean"],-61)
            self.assertLess(row["market_holding_mean"],-40)
            self.assertGreater(row["market_coverage_seconds"],0)
            self.assertEqual(row["state"],"CLOSED")
            self.assertIsNotNone(row["max_favorable_pct"] if row["observed_marks"] else 0)
            with sqlite3.connect(j.db_path(root,"stock")) as c:
                line=c.execute("SELECT summary_json FROM position_lifecycle_v042").fetchone()
            self.assertIsNotNone(line)
            stored=json.loads(line[0])
            self.assertEqual(stored["market_entry"],-61)
            self.assertIn("holding_mean",stored)
            self.assertEqual(len(lifecycle.existing(root,"stock","ma")),1)

    def test_future_candles_do_not_change_historical_buy_or_sell_readings(self):
        with TemporaryDirectory() as root:
            symbol,entry,exit_at,candles=setup_rows(root)
            row={"id":4,"tf":"5m","code":symbol,"entry_at":entry,
                 "closed_at":exit_at,"status":"CLOSED","entry_price":100}
            first=lifecycle.rebuild(root,"stock","ma",[row],now=exit_at+600)[0]
            with j._connect(root,"stock") as c:
                c.execute("""INSERT INTO candle_observations
                    (strategy,frame,symbol,bar_ts,high,low,close,volume,origin)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    ("ma","5m",symbol,exit_at+3600,999999,1,2000,1e9,"FUTURE"))
            second=lifecycle.rebuild(root,"stock","ma",[row],now=exit_at+7200)[0]
            self.assertEqual(first["entry_features"],second["entry_features"])
            self.assertEqual(first["exit_features"],second["exit_features"])
            self.assertEqual(first["holding_mean"],second["holding_mean"])

    def test_no_entry_timestamp_never_fabricates_position_features(self):
        with TemporaryDirectory() as root:
            symbol,entry,exit_at,_=setup_rows(root)
            position={"id":7,"tf":"5m","code":symbol,"entry_date":"2026-11-13",
                      "status":"CLOSED","entry_price":100}
            self.assertEqual(lifecycle.rebuild(root,"stock","ma",[position]),[])
            self.assertEqual(lifecycle.existing(root,"stock","ma"),[])

    def test_original_buy_fill_can_supply_exact_missing_entry_timestamp(self):
        with TemporaryDirectory() as root:
            symbol,entry,exit_at,_=setup_rows(root)
            row={"id":33,"tf":"5m","code":symbol,
                 "status":"CLOSED","entry_price":100}
            fills=[{"position_id":33,"side":"BUY","time":entry},
                   {"position_id":33,"side":"SELL","time":exit_at}]
            result=lifecycle.rebuild(root,"stock","ma",[row],now=exit_at+3600,
                                     executions=fills)
            self.assertEqual(result[0]["entry_ts"],entry)
            self.assertEqual(result[0]["exit_ts"],exit_at)

    def test_matched_signal_prebuy_features_enrich_and_postbuy_rejected(self):
        with TemporaryDirectory() as root:
            symbol,entry,exit_at,_=setup_rows(root)
            with j._connect(root,"stock") as conn:
                conn.execute("DELETE FROM candle_observations")
            pos={"id":29,"tf":"5m","code":symbol,"entry_at":entry,"status":"OPEN"}
            results=lifecycle.rebuild(root,"stock","ma",[pos],now=entry+600)
            trade={"raw":pos,"frame":"5m","signal_ts":entry-60,
                   "features":{"atr_pct":3.4,"rsi":71,"williams_r":-14}}
            updated=lifecycle.merge_original_signal_features(
                root,"stock","ma",results,[trade])
            self.assertEqual(updated[0]["entry_features"]["rsi"],71)
            saved=lifecycle.existing(root,"stock","ma")
            self.assertEqual(saved[0]["entry_features"]["williams_r"],-14)
            # A future-dated observation is never disguised as a prebuy input.
            pos2={"id":30,"tf":"5m","code":symbol,"entry_at":entry,"status":"OPEN"}
            rows=lifecycle.rebuild(root,"stock","ma",[pos2],now=entry+600)
            future={"raw":pos2,"frame":"5m","signal_ts":entry+60,
                    "features":{"rsi":94}}
            lifecycle.merge_original_signal_features(root,"stock","ma",rows,[future])
            self.assertNotIn("rsi",rows[0]["entry_features"])

    def test_online_capture_preserves_position_specific_factor_events(self):
        with TemporaryDirectory() as root:
            code,entry,_,_=setup_rows(root)
            position={"id":55,"code":code,"tf":"5m","entry_at":entry,
                      "entry_price":100,"last_price":102,"qty":2,"status":"OPEN",
                      "spread_pct":.25}
            j.capture_scope(root,"stock","ma",{},{"positions":[position]},
                            now=entry+120,market_score=-40)
            with j._connect(root,"stock") as c:
                event=c.execute(
                    "SELECT position_key,price,market_score,features_json "
                    "FROM position_factor_events_v042").fetchone()
            self.assertEqual(event[0],"55|5m|"+code)
            self.assertEqual(event[1],102)
            self.assertEqual(event[2],-40)
            self.assertEqual(json.loads(event[3])["spread_pct"],.25)

    def test_loss_and_rise_patterns_are_results_not_raw_indicator_numbers(self):
        def link(key,rsi,vol,market_score):
            return {"position_key":key,"frame":"5m",
                    "entry_features":{"rsi":rsi,"volume_ratio":vol},
                    "holding_mean":{"atr_pct":2.2},
                    "market_entry":market_score,
                    "market_holding_mean":market_score}
        trades=[]
        links=[]
        for i,(pnl,rsi,vol,market_score) in enumerate([
            (-6,84,3,-60),(-3,78,2.8,-55),(4,55,1.4,30),(5,58,1.8,45)]):
            key=str(i)+"|5m|005930"
            trades.append({"key":str(i),"raw":{"id":i,"tf":"5m","code":"005930"},
                           "frame":"5m","closed_roi":pnl,"open_roi":None,
                           "at":1750000000+i})
            links.append(link(key,rsi,vol,market_score))
        frame={}
        result=diagnosis.diagnose(frame,trades,links,"stock","ma","5m")
        item=result["diagnosis"]
        self.assertEqual(item["sample_n"],4)
        self.assertIn("진입",item["cause"])
        self.assertIn("시장",item["decline"])
        self.assertIn("상승",item["rise"] if "상승" in item["rise"] else "상승")
        self.assertIn("진입",item["recommendation"])
        self.assertFalse(item["effect"]["validated"])
        self.assertGreater(item["effect"]["delta_pp"],0)
        for key in ("cause","decline","rise","recommendation"):
            for token in ("ATR 0.","RSI 8","Williams -","지표 축적률"):
                self.assertNotIn(token,item[key])

    def test_result_only_ui_never_renders_raw_indicator_tiles(self):
        view=UI[UI.index("    private void aiFrameCard("):
                UI.index("    private void renderResearch(){")]
        self.assertNotIn("지표 축적률",view)
        self.assertNotIn("AiEvidenceMeterV040",view+VIS)
        for title in ("손실 선행조건","반복 하락패턴","반복 상승패턴",
                      "개선 전략","정량 개선효과"):
            self.assertIn(title,view)
        for forbidden in ("윌리엄스", "Williams","시장점수 평균","ATR",
                          "RSI","volume_ratio","feature_snapshots"):
            self.assertNotIn(forbidden,view)


if __name__=="__main__":
    unittest.main()
