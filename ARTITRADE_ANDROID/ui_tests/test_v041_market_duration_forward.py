"""v041 market regime return attribution, session gating and API-backed HIGH."""
import datetime as dt
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import sys
BASE=Path(__file__).parents[1]
sys.path.insert(0,str(BASE/"app/src/main/python"))
import market_research_v033 as market
import krx_session_v041 as krx
import stock_forward_high_v041 as high
import ai_strategy_analysis_v040 as ai
from android_ledger_backup import _candidate

def when(s):
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=krx.KST).timestamp())

class MarketScoreV041(unittest.TestCase):
    def test_krx_holiday_weekend_and_special_session(self):
        self.assertFalse(krx.is_regular_open(when("2026-11-14T13:00:00")))
        self.assertFalse(krx.is_regular_open(when("2026-12-25T13:00:00")))
        self.assertFalse(krx.is_regular_open(when("2026-11-19T09:30:00")))
        self.assertTrue(krx.is_regular_open(when("2026-11-19T10:30:00")))
        self.assertTrue(krx.is_regular_open(when("2026-11-19T16:29:00")))
        self.assertFalse(krx.is_regular_open(when("2026-11-19T16:30:00")))
        self.assertTrue(krx.is_regular_open(when("2026-11-11T13:00:00")))
        self.assertFalse(krx.is_regular_open(when("2026-11-11T15:31:00")))

    def test_weekend_stock_score_hidden_but_history_kept(self):
        saturday=when("2026-11-14T13:00:00")
        stock=market.display_snapshot("stock",{"score":-61,"asof":saturday-60,
                           "active_band":"-80~-61","bands":[
                               {"band":"-80~-61","avg_change_pp":-2.0}]},now=saturday)
        self.assertIsNone(stock["score"])
        self.assertIsNone(stock["active_band"])
        self.assertIn("휴장",stock["status"])
        self.assertEqual(stock["bands"][0]["avg_change_pp"],-2.0)
        coin=market.display_snapshot("coin",{"score":-61,"asof":saturday-60},now=saturday)
        self.assertEqual(coin["score"],-61)

    def test_user_example_band_61_then_40_with_exact_delta_and_duration(self):
        with tempfile.TemporaryDirectory() as root:
            ledger=market.ScoreLedger(Path(root)/"market_research_v033.sqlite3")
            t=when("2026-11-11T13:00:00")
            key="stock/at/5m/A"
            ledger.record_score("stock",-61,t,{key:0.0},{})
            for minutes in range(1,31):
                r=-2*minutes/30
                ledger.observe_positions("stock",{key:r},t+minutes*60)
            ledger.record_score("stock",-40,t+31*60,{key:-2.0},{})
            for minutes in range(32,51):
                # 13:31 is the start of the -40 score regime, -2% baseline.
                r=-2+4*(minutes-31)/19
                ledger.observe_positions("stock",{key:r},t+minutes*60)
            rows={r["band"]:r for r in ledger.snapshot("stock")["bands"]}
            self.assertAlmostEqual(rows["-80~-61"]["avg_change_pp"],-2,places=4)
            self.assertAlmostEqual(rows["-40~-21"]["avg_change_pp"],4,places=4)
            self.assertAlmostEqual(rows["-80~-61"]["observed_minutes"],31,places=1)
            self.assertAlmostEqual(rows["-40~-21"]["observed_minutes"],19,places=1)
            self.assertAlmostEqual(rows["-40~-21"]["observed_hours"],19/60,places=2)
            segmented=ai.band_scope_statistics(root,"stock","at","5m")
            s={r["band"]:r for r in segmented}
            self.assertAlmostEqual(s["-80~-61"]["avg_change_pp"],-2)
            self.assertAlmostEqual(s["-40~-21"]["avg_change_pp"],4)
            hypothesis=ai.attach_market_band_research(
                {"status":"LIVE_OBSERVATION","improvement":None,"proposal":"old"},
                segmented)
            self.assertEqual(hypothesis["market_band_worst"]["band"],"-80~-61")
            self.assertEqual(hypothesis["market_band_hypothesis"]["hypothetical_avoidance_pp"],2.0)
            self.assertFalse(hypothesis["market_band_hypothesis"]["validated"])
            ledger.close()

    def test_new_purchase_joins_existing_regime_not_backdated(self):
        with tempfile.TemporaryDirectory() as root:
            db=market.ScoreLedger(Path(root)/"score.sqlite3")
            start=when("2026-11-11T13:00:00")
            db.record_score("stock",-61,start,{}, {})
            for minute in range(1,61):
                db.observe_positions("stock",{},start+minute*60)
            db.observe_positions("stock",{"stock/at/5m/NEW":-2},start+61*60)
            db.observe_positions("stock",{"stock/at/5m/NEW":+2},start+62*60)
            band=next(row for row in db.snapshot("stock")["bands"]
                      if row["band"]=="-80~-61")
            self.assertEqual(band["position_segments"],1)
            self.assertAlmostEqual(band["avg_change_pp"],4.)
            db.close()

    def test_data_gap_not_fake_two_hours_of_observation(self):
        with tempfile.TemporaryDirectory() as root:
            db=market.ScoreLedger(Path(root)/"score.sqlite3")
            t=when("2026-11-11T13:00:00")
            db.record_score("stock",-61,t,{}, {})
            db.observe_positions("stock",{},t+7200)
            band=next(x for x in db.snapshot("stock")["bands"]
                      if x["band"]=="-80~-61")
            self.assertEqual(band["observed_seconds"],0)
            db.close()

    def test_market_ui_no_factor_cards_or_poll_counts(self):
        java=(BASE/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
        self.assertIn("점수 구간별 평균 등락률",java)
        self.assertIn("관측시간",java)
        self.assertIn('"observed_seconds"',java)
        self.assertNotIn('TextView method=text("평가 항목"',java)
        self.assertNotIn('TextView n=text(count+"회"',java)
        self.assertNotIn("후속 고가 검증 대기",java)

class ForwardHighV041(unittest.TestCase):
    def test_post_entry_high_and_real_minute_of_peak(self):
        t=when("2026-11-11T13:00:41")
        bars=[
            (when("2026-11-11T13:00:00"),5450), # pre-entry minute excluded
            (when("2026-11-11T13:01:00"),5100),
            (when("2026-11-11T14:00:00"),5180),
            (when("2026-11-12T13:00:00"),5120),
            (when("2026-11-13T13:00:00"),5110),
        ]
        daily=[
            (when("2026-11-11T00:00:00"),5180),
            (when("2026-11-12T00:00:00"),5120),
            (when("2026-11-13T00:00:00"),5110),
        ]
        def result(symbol,interval,span):
            self.assertEqual(symbol,"150900.KS")
            return daily if interval=="1d" else bars
        with patch.object(high,"_json_chart",side_effect=result):
            info=high.compute_forward_high("150900",t,when("2026-11-14T13:00:00"))
        self.assertEqual(info["state"],"OK")
        self.assertEqual(info["high_price"],5180)
        self.assertEqual(info["high_at"],when("2026-11-11T14:00:00"))
        self.assertEqual(info["precision_seconds"],60)

    def test_no_confirmed_high_when_missing_session_or_contradiction(self):
        t=when("2026-11-11T13:00:00")
        bars=[(when("2026-11-11T14:00:00"),5150),
              (when("2026-11-13T14:00:00"),5100)]
        daily=[(when("2026-11-11T00:00:00"),5180),
               (when("2026-11-12T00:00:00"),5200),
               (when("2026-11-13T00:00:00"),5100)]
        with patch.object(high,"_json_chart",side_effect=
                          lambda sym,interval,span:daily if interval=="1d" else bars):
            result=high.compute_forward_high("150900",t,
                                               when("2026-11-14T12:00:00"))
        self.assertNotEqual(result["state"],"OK")
        self.assertIsNone(result.get("high_at"))

    def test_high_cache_requires_exact_signal_code_and_buy_timestamp(self):
        with tempfile.TemporaryDirectory() as root:
            entry=when("2026-11-11T13:00:00")
            with high._db(root) as conn:
                conn.execute("""INSERT INTO price_highs
                    (signal_id,code,entry_ts,high_price,high_at,precision_seconds,
                     source,coverage_start,coverage_end,observed_bars,checked_at,state)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("123","150900",entry,5180,entry+300,60,
                     "YAHOO_1M",entry,entry+3600,40,entry+3600,"OK"))
                conn.commit()
            row={"signal_id":123,"code":"150900","buy_at":entry,
                 "observed_max_unverified":5450}
            wrong={"signal_id":123,"code":"150900","buy_at":entry+3600}
            high.enrich(root,[row,wrong])
            self.assertEqual(row["observed_max_price"],5180)
            self.assertEqual(row["observed_max_at"],entry+300)
            self.assertEqual(row["observed_max_evidence"],"VERIFIED_MINUTE_BAR")
            self.assertNotIn("observed_max_price",wrong)
            self.assertTrue(_candidate(Path(root),Path(root)/"stock/forward_high_v041.sqlite3"))

if __name__=="__main__":
    unittest.main()
