"""v040 independent public indicator research polling never changes broker/trade state."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import ai_research_v040 as journal
import ai_market_data_v040 as market
import ai_strategy_analysis_v040 as analysis


def minute_bars(n=40):
    initial=journal._timestamp("2026-11-12T09:00:00+09:00")
    return [(initial+i*300,102+i,90+i,97+i,float(100+i*5)) for i in range(n)]


class PublicIndicatorCollection(unittest.TestCase):
    def test_upbit_5minute_uses_only_completed_candles(self):
        now=journal._timestamp("2026-11-12T11:00:00+09:00")
        payload=[]
        for j in range(25):
            start=now-j*300
            payload.append({"candle_date_time_kst":
                           __import__("datetime").datetime.fromtimestamp(
                           start, __import__("datetime").timezone(
                           __import__("datetime").timedelta(hours=9))
                           ).strftime("%Y-%m-%dT%H:%M:%S"),
                           "high_price":120+j,"low_price":90+j,
                           "trade_price":105+j,"candle_acc_trade_volume":200+j})
        with patch.object(market,"_http",return_value=payload) as api:
            bars,source=market.fetch_candles("coin","KRW-BTC","5m",now)
        self.assertEqual(source,"UPBIT_CLOSED_OHLCV")
        self.assertEqual(len(bars),24)  # latest (still forming) excluded
        self.assertEqual(bars[-1][0],now-300)
        self.assertTrue(api.call_args.args[0].endswith("/v1/candles/minutes/5"))

    def test_stock_yahoo_chart_is_not_fabricated(self):
        now=journal._timestamp("2026-11-12T16:00:00+09:00")
        first=now-50*300
        mock={"chart":{"result":[{"timestamp":[first+i*300 for i in range(45)],
                         "indicators":{"quote":[{"high":[105+i for i in range(45)],
                          "low":[92+i for i in range(45)],
                          "close":[98+i for i in range(45)],
                          "volume":[1000+i for i in range(45)]}]}}]}}
        with patch.object(market,"_http",return_value=mock) as req:
            bars,source=market.fetch_candles("stock","005930","5m",now)
        self.assertEqual(len(bars),45)
        self.assertEqual(source,"YAHOO_KRX_PUBLIC_CHART")
        self.assertIn("005930.KS",req.call_args.args[0])

    def test_historical_indicator_backfill_never_uses_after_entry_bars(self):
        with tempfile.TemporaryDirectory() as root:
            bars=minute_bars(40)
            entry=bars[25][0]
            signal={"id":1,"code":"KRW-BTC","tf":"5m",
                    "ref_at":"20261112085500","at":entry}
            expected=journal.compute_candle_indicators(
                [x for x in bars if x[0]+300<=entry])
            result=market.record(root,"coin","general","KRW-BTC","5m",bars,
                                 "UPBIT_CLOSED_OHLCV",[signal],now=bars[-1][0]+600)
            self.assertEqual(result["entries"],1)
            stored=journal.stored(root,"coin","general")
            historic=[x for x in stored["features"]
                      if x["origin"]=="RECONSTRUCTED_PRE_ENTRY_OHLCV"]
            self.assertEqual(len(historic),1)
            self.assertEqual(json.loads(historic[0]["features_json"]),expected)
            self.assertEqual(len(stored["candles"]),40)
            self.assertGreaterEqual(len(stored["indicators"]),1)
            # A later observation is acceptable only because the underlying
            # historical OHLCV bars were closed before the signal.
            reconstructed=analysis._signal_rows({"candidates":[signal]},
                {"items":[]}, "coin","general",stored)
            self.assertEqual(reconstructed[0]["features"]["williams_r"],
                             expected["williams_r"])

    def test_missing_public_bars_never_generate_synthetic_indicators(self):
        with patch.object(market,"_http",side_effect=ConnectionError("offline")):
            # Stock sources swallow failed providers and remain empty.
            bars,source=market.fetch_candles("stock","005930","5m",
                   journal._timestamp("2026-11-12T16:00:00+09:00"))
        self.assertEqual(bars,[])
        self.assertEqual(source,"")

    def test_gmo_daily_scope_is_not_written_as_fake_intraday_strategy(self):
        with tempfile.TemporaryDirectory() as root:
            now=journal._timestamp("2026-11-12T16:00:00+09:00")
            with patch.object(market,"fetch_candles",return_value=([],"")) as fetch:
                packet={"stock":{"at":{"ledger":{"gmo":{"positions":[
                    {"code":"005930","entry_at":"2026-11-12T09:00:00+09:00"}]}},
                    "research":{"gmo":{"items":[]}}}}}
                market.refresh_packet(root,packet,now=now,max_calls=1)
                self.assertEqual(fetch.call_args.args[:4],("stock","005930","day",now))
            self.assertFalse((Path(root)/"stock/ai/AI_RESEARCH_V040.sqlite3").is_file())


if __name__=="__main__":
    unittest.main()
