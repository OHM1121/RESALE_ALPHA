"""v047 close-based MA, 30 preceding bars, entry strengths, and trend separation."""
import datetime as dt
import json
import math
import sqlite3
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import ai_entry_context_v047 as entry
import ai_research_v040 as journal
import ai_strategy_analysis_v040 as ai
import ai_trend_analysis_v047 as trend
import android_ai_evidence_export_v046 as evidence

UI=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text(encoding="utf-8")
MT=(ROOT/"app/src/main/python/market_trend_runtime_v043.py").read_text(encoding="utf-8")
GRADLE=(ROOT/"app/build.gradle").read_text(encoding="utf-8")


class TestEntryContextV047(TestCase):
    @staticmethod
    def bars(close=100,entry_vol=2000,n=151,first=1750000000):
        step=300
        start=first-(first%step)
        return [(start+i*step,200+i,20+i*.1,
                 close if i==n-1 else 100,
                 entry_vol if i==n-1 else 1000) for i in range(n)]

    def test_close_is_only_ma_and_touch_measurement(self):
        bars=self.bars()
        at=bars[-1][0]+10
        incomplete=entry.candle_context(bars,at,"5m",now=at+100)
        self.assertFalse(incomplete["entry_candle_complete"])
        self.assertIsNone(incomplete["sma"]["7"] if incomplete["sma"].get("7") else None)
        done=entry.candle_context(bars,at,"5m",now=bars[-1][0]+300)
        self.assertTrue(done["entry_candle_complete"])
        self.assertEqual(done["state"],"FINALIZED")
        for period in ("7","20","60","120"):
            self.assertEqual(done["sma"][period]["position"],"TOUCH")
            self.assertEqual(done["sma"][period]["sma"],100)
        self.assertAlmostEqual(done["high_slope_price_per_bar"],1.0)
        self.assertAlmostEqual(done["low_slope_price_per_bar"],.1)
        self.assertAlmostEqual(done["high_slope_pct_per_bar"],1.0)
        self.assertAlmostEqual(done["low_slope_pct_per_bar"],.1)
        self.assertEqual(done["prior30_volume_mean"],1000)
        self.assertEqual(done["entry_volume"],2000)
        self.assertEqual(done["volume_ratio30"],2.0)
        self.assertEqual(done["prior30_bars_available"],30)
        self.assertEqual(done["slope_method"],"OLS_PREVIOUS_30_CLOSED_BARS")

    def test_close_only_not_high_low_touch(self):
        bars=self.bars(close=110)
        at=bars[-1][0]+5
        done=entry.candle_context(bars,at,"5m",now=bars[-1][0]+300)
        self.assertEqual(done["sma"]["7"]["position"],"ABOVE")
        self.assertEqual(done["sma"]["20"]["position"],"ABOVE")
        # Every entry candle in this fixture crosses SMA via high/low, yet
        # close-based classification must stay ABOVE.
        below=self.bars(close=90)
        result=entry.candle_context(below,at,"5m",now=below[-1][0]+300)
        self.assertEqual(result["sma"]["120"]["position"],"BELOW")

    def test_missing_bars_not_zero_filled(self):
        bars=self.bars(n=18)
        result=entry.candle_context(bars,bars[-1][0]+10,"5m",
                                    now=bars[-1][0]+300)
        self.assertIsNone(result["high_slope_pct_per_bar"])
        self.assertIsNone(result["prior30_volume_mean"])
        self.assertIsNone(result["sma"]["120"]["position"])
        self.assertEqual(result["state"],"PARTIAL_PRIOR_HISTORY")

    def test_actual_and_virtual_are_different_and_require_timestamped_strength(self):
        bars=self.bars()
        at=bars[-1][0]+10
        base={"id":71,"tf":"5m","market":"KRW-BTC",
              "entry_at":at,"execution_strength":129.9}
        raw=entry._candidate(base,"general","ACTUAL")
        self.assertIsNone(raw["entry_strength"])
        base["execution_strength_at"]=at+1
        raw=entry._candidate(base,"general","ACTUAL")
        self.assertEqual(raw["entry_strength"],129.9)
        base["execution_strength_at"]=at+10
        raw=entry._candidate(base,"general","ACTUAL")
        self.assertIsNone(raw["entry_strength"])
        virtual=entry._candidate({
            "id":"MT43:SIG:coin:KRW-BTC:5m:bar",
            "entry_mode":"MARKET_TREND","tf":"5m","market":"KRW-BTC",
            "at":at},"general","VIRTUAL")
        self.assertEqual(virtual["role"],"VIRTUAL")
        self.assertEqual(virtual["entry_mode"],"MARKET_TREND")
        self.assertNotEqual(virtual["key"],raw["key"])

    def test_persistence_tracks_original_and_trend_separately(self):
        with TemporaryDirectory() as root:
            bars=self.bars()
            at=bars[-1][0]+10
            original={"id":5,"tf":"5m","market":"KRW-BTC",
                      "entry_at":at,"entry":100,"qty":1}
            virtual={"id":"MT43:SIG:coin:KRW-BTC:5m:bar",
                      "tf":"5m","market":"KRW-BTC","at":at,
                      "entry_mode":"MARKET_TREND","entry_observed":True}
            count=entry.capture_events(root,"coin","general",signals=[virtual],
                    positions=[original],now=bars[-1][0]+100)
            self.assertEqual(count,2)
            self.assertEqual(len(entry.captured(root,"coin","ORIGINAL")),1)
            self.assertEqual(len(entry.captured(root,"coin","MARKET_TREND")),1)
            result=entry.capture_events(root,"coin","general",signals=[virtual],
                    positions=[original],bars=bars,frame="5m",
                    symbol="KRW-BTC",now=bars[-1][0]+300,
                    source="UPBIT_CLOSED_OHLCV")
            self.assertEqual(result,2)
            originals=entry.captured(root,"coin","ORIGINAL")
            shadows=entry.captured(root,"coin","MARKET_TREND")
            self.assertEqual(originals[0]["state"],"FINALIZED")
            self.assertEqual(shadows[0]["state"],"FINALIZED")
            self.assertEqual(shadows[0]["context"]["volume_ratio30"],2)
            self.assertEqual(entry.summarize(root,"coin","MARKET_TREND")["entry_events"],1)
            dump=json.loads(entry.export_rows(root,"coin","MARKET_TREND"))
            self.assertEqual(dump["rows"][0]["context"]["sma"]["120"]["position"],"TOUCH")
            self.assertEqual(dump["rows"][0]["role"],"VIRTUAL")

    def test_original_ai_removes_closed_trend_id_not_same_ticker_native(self):
        with TemporaryDirectory() as root:
            with sqlite3.connect(Path(root)/"market_trend_v043.sqlite3") as c:
                c.execute("CREATE TABLE position_state(market TEXT,strategy TEXT,"
                    "position_id INT,frame TEXT,symbol TEXT)")
                c.execute("INSERT INTO position_state VALUES"
                          "('coin','general',3,'5m','KRW-BTC')")
            two=[
                {"id":3,"tf":"5m","market":"KRW-BTC","status":"CLOSED"},
                {"id":4,"tf":"5m","market":"KRW-BTC","status":"CLOSED"},
            ]
            raw={"positions":two,"executions":[
                {"position_id":3,"tf":"5m","market":"KRW-BTC","side":"SELL"},
                {"position_id":4,"tf":"5m","market":"KRW-BTC","side":"SELL"}]}
            research={"items":[{"id":"MT43:SIG:...","entry_mode":"MARKET_TREND"},
                               {"id":"normal_signal","tf":"5m"}]}
            ledger={"positions":list(two),"executions":list(raw["executions"])}
            archive={"positions":[],"features":[]}
            actual=ai._exclude_trend_cohort(root,"coin","general",raw,
                                           research,ledger,archive)
            self.assertEqual(len(actual[0]["positions"]),1)
            self.assertEqual(actual[0]["positions"][0]["id"],4)
            self.assertEqual(len(actual[0]["executions"]),1)
            self.assertEqual(len(actual[1]["items"]),1)

    def test_trend_analysis_is_independent_real_and_virtual(self):
        with TemporaryDirectory() as root:
            db=Path(root)/"market_trend_v043.sqlite3"
            with sqlite3.connect(db) as c:
                c.executescript("""
                   CREATE TABLE events(event_key TEXT,at REAL,market TEXT,
                     strategy TEXT,frame TEXT,symbol TEXT,mode TEXT,
                     rsi20_live REAL,rsi20_closed REAL,price REAL,qty REAL,
                     reason TEXT,payload TEXT);
                   CREATE TABLE position_state(market TEXT,strategy TEXT,frame TEXT,
                     symbol TEXT,position_id INT,payload TEXT,status TEXT,
                     updated_at REAL);
                   CREATE TABLE virtual_position_state(market TEXT,strategy TEXT,
                     frame TEXT,symbol TEXT,payload TEXT,status TEXT,updated_at REAL);
                """)
                c.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",[
                    ("MT43:SIG:coin:KRW-BTC:5m:A",100,"coin","general","5m",
                     "KRW-BTC","ACTUAL",28,None,100,1,"BOUGHT",'{"score":-20}'),
                    ("MT43:SIG:coin:KRW-ETH:5m:B",101,"coin","ma","5m",
                     "KRW-ETH","VIRTUAL",27,None,200,1,"NO_WALLET_CASH",'{"score":-20}'),
                    ("MT43:BUY:coin:general:5m:BTC:FILLED",101,"coin","general",
                     "5m","KRW-BTC","PAPER",28,None,100,1,"MT_BUY_FILLED",'{"position_id":7}'),
                    ("MT43:SELL:coin:general:7:MT_TP1:110:FILLED",110,"coin","general",
                     "5m","KRW-BTC","PAPER",None,None,110,1,"MT_TP1",
                     '{"position_id":7,"net_pnl":9}')
                ])
                c.execute("INSERT INTO position_state VALUES(?,?,?,?,?,?,?,?)",
                    ("coin","general","5m","KRW-BTC",7,
                     '{"entry":100,"original_qty":1}',"CLOSED",120))
                c.execute("INSERT INTO virtual_position_state VALUES(?,?,?,?,?,?,?)",
                    ("coin","ma","5m","KRW-ETH","{}","OPEN",120))
            output=trend.analyze(root,"coin")
            self.assertEqual(output["scope"],"coin/trend")
            f=next(x for x in output["frames"] if x["frame"]=="5m")
            self.assertEqual(f["signals"],2)
            self.assertEqual(f["market_trend_rsi20"]["confirmed_buy_fills"],1)
            self.assertEqual(f["market_trend_rsi20"]["virtual_signals"],1)
            self.assertEqual(f["avg_realized_pct"],9.0)
            native=ai.analyze(root,"coin","trend")
            self.assertEqual(json.loads(native)["strategy"],"trend")

    def test_quantitative_export_includes_context_source_schema(self):
        with TemporaryDirectory() as root:
            report={"ok":True,"scope":"coin/trend","frames":[],"source":"test"}
            saved=json.loads(evidence.build_reports(root,"coin/trend",json.dumps(report)))
            data=json.loads(saved["analysis/AI_QUANTITATIVE_V046.json"])
            self.assertIn("entry_candle_methodology",data)
            self.assertIn("entry_candle_summary",data)
            self.assertIn("analysis/ENTRY_CANDLE_EVIDENCE_V047.json",saved)

    def test_java_ui_and_accounts_unchanged(self):
        self.assertIn('private boolean aiTrendOnly=false,researchTrendOnly=false',UI)
        self.assertIn('renderTrendResearch()',UI)
        self.assertIn('isTrendRow(r)',UI)
        self.assertIn('마켓트렌드 RSI20',UI)
        self.assertIn('가상체결',UI)
        self.assertRegex(GRADLE,r"versionCode\s+(?:5[4-9]|[6-9][0-9])")
        self.assertIn('market_trend_research',MT)
        self.assertIn('ledger_is_virtual_only',MT)
