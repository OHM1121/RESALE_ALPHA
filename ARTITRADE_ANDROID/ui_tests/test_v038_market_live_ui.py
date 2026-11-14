"""v038: live market score source paths, independent crypto polling, exact bands."""
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

BASE=Path(__file__).parents[1]
sys.path.insert(0,str(BASE/"app/src/main/python"))
import market_research_v033 as m


def fake_tickers(n=40):
    return [{"market":f"KRW-TEST{i}","signed_change_rate":0.01 if i%3 else -0.01,
             "acc_trade_price_24h":float(10000000+i)} for i in range(n)]


def daily(count):
    # Upbit returns newest-first candles.
    return [{"trade_price":float(100+count-i)*1000} for i in range(count)]


class MarketV038(unittest.TestCase):
    def test_signed_twenty_point_boundaries_are_gapless(self):
        self.assertEqual(len(m.BANDS),10)
        vals=[-100,-80,-60,-40,-20,0,20,40,60,80,100]
        expected=["-100~-81","-80~-61","-60~-41","-40~-21","-20~-1",
                  "0~19","20~39","40~59","60~79","80~100","80~100"]
        self.assertEqual([m.market_band(v) for v in vals],expected)
        self.assertEqual(m.market_band(-80.1),"-100~-81")
        self.assertEqual(m.market_band(79.9),"60~79")
        self.assertIsNone(m.market_band(100.1))
        self.assertIsNone(m.market_band(float('nan')))

    def test_existing_v034_band_migration_keeps_positions_and_returns(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/"score.sqlite3"
            first=m.ScoreLedger(path)
            first.record_score("stock",-85,100000,{"S":-12}, {})
            first.observe_positions("stock",{"S":-5},100080)
            first.record_score("stock",58,100120,{"S":-5}, {})
            first.conn.execute("UPDATE score_events SET band='-100~-51'")
            first.conn.execute("UPDATE cohort_segments SET band='-100~-51'")
            first.conn.execute("PRAGMA user_version=34")
            first.close()
            second=m.ScoreLedger(path)
            self.assertEqual(second.conn.execute("PRAGMA user_version").fetchone()[0],35)
            self.assertEqual(second.score_at("stock",100160),58)
            self.assertEqual(second.snapshot("stock")["active_band"],"40~59")
            records=second.conn.execute("SELECT identity,start_return,latest_return FROM cohort_positions "
                                        "ORDER BY segment_id").fetchall()
            self.assertEqual([(x[0],x[1],x[2]) for x in records],[("S",-12,-5),("S",-5,-5)])
            self.assertEqual({x["band"]:x["position_segments"] for x in second.snapshot("stock")["bands"] if x["position_segments"]},
                             {"-100~-81":1,"40~59":1})
            second.close()

    def test_domestic_stock_fallback_supplies_both_required_indices(self):
        def source(url,params):
            if "m.stock.naver.com/api/index/KOSPI/basic" in url:
                return {"fluctuationsRatio":"1.35"}
            if "m.stock.naver.com/api/index/KOSDAQ/basic" in url:
                return {"fluctuationsRatio":"-0.75"}
            raise ConnectionError("Yahoo blocked")
        with patch.object(m,"_public_json",side_effect=source):
            score=m._fetch_stock()
        self.assertIsNotNone(score["score"])
        self.assertIn("KOSPI",score["naver_fallback"])
        self.assertIn("KOSDAQ",score["naver_fallback"])
        self.assertAlmostEqual(score["coverage"],(1.30+.90)/8.9,places=2)

    def test_coin_quote_24h_contract_produces_score(self):
        def source(url,params):
            if url.endswith("/ticker/all"):
                self.assertEqual(params["quote_currencies"],"KRW")
                return fake_tickers()
            if url.endswith("/candles/days"):
                return daily(35 if params["market"]=="KRW-BTC" else 12)
            raise AssertionError("unexpected endpoint "+url)
        with patch.object(m,"_public_json",side_effect=source):
            score=m._fetch_coin()
        self.assertIsInstance(score["score"],float)
        self.assertTrue(-100<=score["score"]<=100)
        self.assertEqual(len(score["factors"]),6)

    def test_coin_quote_fallback_if_all_ticker_fails(self):
        def source(url,params):
            if url.endswith("/ticker/all"): raise RuntimeError("ticker blocked")
            if url.endswith("/market/all"):
                return [{"market":f"KRW-TEST{i}"} for i in range(40)]
            if url.endswith("/ticker"):return fake_tickers()
            if url.endswith("/candles/days"):return daily(35 if params["market"]=="KRW-BTC" else 12)
            raise AssertionError("unexpected endpoint")
        with patch.object(m,"_public_json",side_effect=source):
            score=m._fetch_coin()
        self.assertIsNotNone(score["score"])

    def test_coin_fails_honestly_when_ticker_and_fallback_fail(self):
        with patch.object(m,"_public_json",side_effect=ConnectionError("offline")):
            score=m._fetch_coin()
        self.assertIsNone(score["score"])
        self.assertIn("실패",score["status"])

    def test_crypto_polls_while_stock_api_is_stalled(self):
        held=threading.Event()
        release=threading.Event()
        def stock():
            held.set()
            release.wait(3)
            return {"score":None,"status":"STOCK_UNREACHABLE"}
        def coin():
            return {"score":22.3,"status":"정상","coverage":1,
                    "factors":[],"source":"UPBIT_TEST"}
        with tempfile.TemporaryDirectory() as root, \
             patch.object(m,"_fetch_stock",side_effect=stock),\
             patch.object(m,"_fetch_coin",side_effect=coin),\
             patch("krx_session_v041.status",return_value=(True,"장중")):
            worker=m._MarketWorker(root)
            try:
                self.assertTrue(held.wait(2),"stock request did not begin")
                stop=time.monotonic()+3
                current=worker.snapshot()["coin"]
                while current.get("score")!=22.3 and time.monotonic()<stop:
                    time.sleep(.04)
                    current=worker.snapshot()["coin"]
                self.assertEqual(current.get("score"),22.3)
                # Even during a stock timeout, 24h crypto data must publish.
                self.assertFalse(release.is_set())
            finally:
                release.set()
                worker.stop()

    def test_market_screen_no_chatter_and_all_intervals(self):
        visual=(BASE/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
        gauge=(BASE/"app/src/main/java/com/artitrade/mobile/MarketRegimeGraphicView.java").read_text()
        for forbidden in ("주식 EXE 시황 평가 가중치 이관","기준점 대비 %p",
                          "같은 구간에서 기존 보유종목만","수수료 반영 손익 아님",
                          "원본 시황점수와 공개 시세로 평가한"):
            self.assertNotIn(forbidden,visual)
        self.assertNotIn("기준점 보유종목",gauge)
        self.assertNotIn("현 구간 보유수익률 변화",gauge)
        self.assertIn('TextView title=text("시장 점수",21);',visual)
        for band in m.BANDS:self.assertIn('"'+band+'"',visual)
        self.assertIn('TextView hoursView=text(hourText,14);',visual)
        self.assertNotIn('TextView method=text("평가 항목"',visual)

    def test_normal_hub_result_publishes_market_score(self):
        source=(BASE/"app/src/main/python/android_runtime_v078.py").read_text()
        block=source[source.index("def _hub_records_v078(self):"):
                     source.index("def _stock_at_ledger_v19(self):")]
        tail=block.rfind("    return json.dumps(out,ensure_ascii=False,default=str)")
        pub=block.rfind("out['market_research']=offer_records(self.root,out)")
        self.assertGreater(tail,pub)
        self.assertLess(tail-pub,600,"market scores must publish on normal path")


if __name__=="__main__":
    unittest.main()
