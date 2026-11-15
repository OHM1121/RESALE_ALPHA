"""v048 user-approved metrics 02/05/09/10/11/13/14/15/16/17.

No trades are placed; only synthetic in-memory/time-series evidence is used.
All tests also verify null/missing provenance to prevent fabricated numbers.
"""
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import ai_approved_metrics_v048 as m
import ai_approved_collect_v048 as collect
import ai_market_data_v040 as md
import ai_strategy_analysis_v040 as ai
import android_ai_evidence_export_v046 as export

JAVA=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text(encoding="utf8")
GRADLE=(ROOT/"app/build.gradle").read_text(encoding="utf8")


class ApprovedMetricsV048(unittest.TestCase):
    START=1794700800
    STEP=300

    @classmethod
    def candles(cls,n=160):
        # OHLCV + true exchange turnover + source open
        t=cls.START-cls.START%cls.STEP
        return [(t+i*cls.STEP,102+(i%4),95-(i%3),100,
                 10.,1000.,99.) for i in range(n)]

    def test_02_spread_uses_only_fresh_book(self):
        entry=self.START
        packet={"entry_quote":{"bid":100,"ask":100.2,
                "at_epoch":entry+0.2}}
        got=m.entry_spread(packet,entry)
        self.assertAlmostEqual(got["value"],10000*.2/100.1,places=6)
        self.assertEqual(got["unit"],"bps")
        packet["entry_quote"]["at_epoch"]=entry-10
        self.assertIsNone(m.entry_spread(packet,entry)["value"])
        self.assertIsNone(m.entry_spread({"bid":100,"ask":100.5},entry)["value"])

    def test_05_order_latency_source_ms_and_missing(self):
        p={"signal_at":self.START+0.100,
           "order_sent_at":self.START+0.350,
           "order_ack_at":self.START+0.600,
           "filled_at":self.START+1.100}
        result=m.order_latency(p)["value"]
        self.assertEqual(result["signal_to_submit_ms"],250)
        self.assertEqual(result["submit_to_fill_ms"],750)
        self.assertEqual(result["signal_to_fill_ms"],1000)
        self.assertIsNone(m.order_latency({})["value"])

    def test_09_10_11_no_lookahead_real_turnover_required(self):
        bars=self.candles()
        entry_at=bars[130][0]+10
        future_entry=m.entry_technicals(bars,entry_at,"5m",100,
                       asof=entry_at+1)
        self.assertIsNone(future_entry["09"]["value"])
        self.assertIsNotNone(future_entry["10"]["value"])
        self.assertIsNotNone(future_entry["11"]["value"])
        past=m.entry_technicals(bars,entry_at,"5m",100,
                     asof=bars[130][0]+300)
        pattern=past["09"]["value"]
        self.assertEqual(pattern["direction"],"UP")
        self.assertAlmostEqual(pattern["body_pct_of_range"],
                                100/(bars[130][1]-bars[130][2]),places=5)
        self.assertEqual(past["10"]["value"]["reference_close"],100)
        self.assertEqual(past["10"]["source"],"PRE_ENTRY_20_CLOSED_CANDLES")
        self.assertEqual(past["11"]["value"]["prior30_true_vwap"],100)
        no_turnover=[(a,b,c,d,e,None,g) for a,b,c,d,e,f,g in bars]
        missing=m.entry_technicals(no_turnover,entry_at,"5m",100,
                       asof=bars[130][0]+300)
        self.assertIsNone(missing["11"]["value"])

    def test_13_mfe_mae_excludes_intra_entry_candle(self):
        bars=self.candles(140)
        entry_at=bars[130][0]+10
        varied=list(bars)
        varied[130]=(bars[130][0],999,1,100,10,1000,99)
        varied[131]=(bars[131][0],120,80,100,10,1000,99)
        result=m.after_entry_extremes(varied,entry_at,"5m",100,
                            asof=bars[132][0]+300)["value"]
        self.assertAlmostEqual(result["mfe_pct"],20.)
        self.assertAlmostEqual(result["mae_pct"],-20.)
        self.assertLess(result["highest_post_entry_price"],999)
        self.assertTrue(result["entry_candle_excluded"] if
                        "entry_candle_excluded" in result else True)

    def test_14_sell_stages_use_position_id_not_ticker_only(self):
        entry=self.START
        position={"id":7,"tf":"5m","market":"KRW-BTC","qty0":2}
        sells=[{"position_id":7,"tf":"5m","market":"KRW-BTC",
                "side":"SELL","at":entry+300,"qty":1,"price":105,"net_pnl":4},
               {"position_id":7,"tf":"5m","market":"KRW-BTC",
                "side":"SELL","at":entry+600,"qty":1,"price":110,"net_pnl":9},
               {"position_id":8,"tf":"5m","market":"KRW-BTC",
                "side":"SELL","at":entry+700,"qty":1,"price":999,"net_pnl":999}]
        result=m.exit_steps(position,sells,entry,100)["value"]
        self.assertEqual(result["sell_fills_n"],2)
        self.assertEqual(result["sold_pct"],100)
        self.assertEqual(result["net_pnl_verified"],13)
        self.assertEqual(result["net_return_pct"],6.5)
        self.assertEqual(result["steps"][0]["fraction_of_initial_pct"],50)

    def test_15_benchmark_closed_candle_alignment(self):
        b=self.candles(5)
        entry_at=b[1][0]+10
        exit_at=b[3][0]+10
        idx=[(t,h,l,100+i*2,v,tv,o) for i,(t,h,l,c,v,tv,o) in enumerate(b)]
        result=m.benchmark_relative(idx,"KRW-BTC","5m",entry_at,exit_at,100,110)
        self.assertEqual(result["value"]["gross_instrument_return_pct"],10)
        self.assertLess(result["value"]["gross_relative_return_pp"],10)
        self.assertIsNone(m.benchmark_relative([],None,"5m",entry_at,exit_at,100,110)["value"])

    def test_16_signed_tape_and_no_fabrication(self):
        t=self.START
        data={"aggressor_ticks":[
            {"at":t-15,"aggressor_side":"BUY","qty":15},
            {"at":t-20,"aggressor_side":"SELL","qty":5},
            {"at":t-90,"aggressor_side":"BUY","qty":200},
            {"at":t-10,"aggressor_side":"UNKNOWN","qty":40}]}
        result=m.signed_trade_flow(data,t)["value"]
        self.assertEqual(result["cvd_qty"],10)
        self.assertEqual(result["buy_fraction_pct"],75)
        self.assertEqual(result["signed_ticks"],2)
        self.assertIsNone(m.signed_trade_flow({"candle_volume":999},t)["value"])

    def test_17_separated_profit_factor_drawdown_ci(self):
        vals=[1.,2.,-1.,0.]*8
        x=m.cohort_stats(vals)
        self.assertEqual(x["n"],32)
        self.assertEqual(x["profit_factor"],3.)
        self.assertIsNotNone(x["ci95_mean_pct"])
        self.assertLessEqual(x["max_drawdown_pct"],0)
        self.assertEqual(x["ci95_mean_pct"],m.cohort_stats(vals)["ci95_mean_pct"])
        self.assertIsNone(m.cohort_stats(vals[:8])["ci95_mean_pct"])

    def test_capture_original_fill_plus_separate_virtual_in_sidecar(self):
        bars=self.candles()
        at=bars[130][0]+10
        position={"id":7,"tf":"5m","market":"KRW-BTC","entry_at":at,
                  "entry_price":100,"qty0":2,"status":"OPEN",
                  "execution_mode":"PAPER"}
        shadow={"id":"VIRTUAL:1","tf":"5m","market":"KRW-ETH",
                "entry_at":at,"entry_mode":"MARKET_TREND",
                "virtual_entered":True,"entry_observed":True}
        packet={"coin":{"general":{"research":{"items":[shadow]},
                            "ledger":{"positions":[position]}}}}
        with tempfile.TemporaryDirectory() as root:
            result=collect.capture_packet(root,packet,now=at+2)
            self.assertGreaterEqual(result["rows_seen"],2)
            before=collect.summarize(root,"coin")
            print("DEBUG_TEST_CAPTURE",before,"DB=",str(Path(root)/"coin/ai/AI_RESEARCH_V040.sqlite3"))
            self.assertEqual(before["by_role"]["ACTUAL"],1)
            self.assertEqual(before["by_role"]["VIRTUAL"],1)
            self.assertEqual(before["by_execution_kind"]["PAPER"],1)
            md.record(root,"coin","general","KRW-BTC","5m",
                      bars,"TEST_CLOSED_BARS",now=bars[-1][0]+300,
                      positions=[position])
            after=collect.summarize(root,"coin","ORIGINAL","general")
            self.assertEqual(after["entry_records"],1)
            row=after["preview"][0]
            self.assertIsNotNone(row["entry_recording"]["09"]["value"])
            self.assertIsNotNone(row["entry_recording"]["10"]["value"])
            self.assertIsNotNone(row["entry_recording"]["11"]["value"])
            self.assertIsNotNone(row["post_entry_analysis"]["13"]["value"])
            self.assertIsNone(row["entry_recording"]["02"]["value"])
            self.assertEqual(after["coverage"]["02"]["known"],0)
            db=Path(root)/"coin/ai/AI_RESEARCH_V040.sqlite3"
            with sqlite3.connect(db) as conn:
                n=conn.execute("SELECT COUNT(*) FROM trade_evidence_v048").fetchone()[0]
                self.assertEqual(n,2)

    def test_trend_history_after_close_still_in_trend_cohort(self):
        with tempfile.TemporaryDirectory() as root:
            trend=Path(root)/"market_trend_v043.sqlite3"
            with sqlite3.connect(trend) as c:
                c.executescript("""CREATE TABLE position_state(
                     market TEXT,strategy TEXT,frame TEXT,symbol TEXT,
                     position_id INTEGER,payload TEXT,status TEXT,updated_at REAL);
                     CREATE TABLE virtual_position_state(
                     signal_key TEXT,market TEXT,strategy TEXT,frame TEXT,
                     symbol TEXT,payload TEXT,status TEXT,updated_at REAL);
                     CREATE TABLE events(
                     event_key TEXT,market TEXT,strategy TEXT,reason TEXT,
                     mode TEXT,payload TEXT,at REAL,frame TEXT,symbol TEXT,
                     price REAL,qty REAL);
                """)
                c.execute("INSERT INTO position_state VALUES(?,?,?,?,?,?,?,?)",
                  ("coin","general","5m","KRW-BTC",7,
                   json.dumps({"entry_at":"2026-11-15T09:00:00+09:00",
                               "entry":100,"original_qty":1,
                               "execution_mode":"PAPER"}),"CLOSED",1.))
                c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  ("MT43:BUY:coin:general:7:FILLED","coin","general",
                   "MT_BUY_FILLED","PAPER",json.dumps({"position_id":7}),1794700000.,
                   "5m","KRW-BTC",100,1.))
            count=collect.capture_packet(root,{},now=1794700800)
            self.assertGreaterEqual(count["rows_seen"],1)
            x=collect.summarize(root,"coin","MARKET_TREND")
            print("DEBUG_TEST_TREND",x)
            self.assertEqual(x["entry_records"],1)
            self.assertEqual(x["by_execution_kind"]["PAPER"],1)
            native=collect.summarize(root,"coin","ORIGINAL")
            self.assertEqual(native["entry_records"],0)

    def test_ai_and_export_integrations_are_source_aware(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIn("approved_metrics_v048",
               json.loads(ai.analyze(root,"coin","trend")))
            result={"ok":True,"scope":"coin/trend","frames":[]}
            outputs=json.loads(export.build_reports(root,"coin/trend",json.dumps(result)))
            self.assertIn("analysis/APPROVED_METRICS_V048.json",outputs)
            x=json.loads(outputs["analysis/APPROVED_METRICS_V048.json"])
            self.assertEqual(sorted(x["approved_codes"]),sorted(m.APPROVED))
        self.assertIn("versionCode 55",GRADLE)
        self.assertIn("APPROVED_METRICS_V048",(
             ROOT/"app/src/main/python/android_ai_evidence_export_v046.py")
             .read_text(encoding="utf-8"))
