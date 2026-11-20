"""v0.7.89 regression: exact RSI time-frame isolation and MA120 +0.0099 limit."""
import datetime as dt
import sys
import threading
import unittest
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PY=ROOT/"app/src/main/python"
sys.path.insert(0,str(PY))
import ma_v067_policy as policy
import market_trend_v043 as mt
from market_trend_runtime_v043 import Coordinator

KST=dt.timezone(dt.timedelta(hours=9))

class UserMA120LimitTests(unittest.TestCase):
    def test_limit_inclusive_and_above_prohibited(self):
        self.assertTrue(policy.ma120_down_for_entry({120:{"delta":Decimal("0.0099")}}))
        self.assertTrue(policy.ma120_down_for_entry({120:{"delta":Decimal("0.008333")}}))
        self.assertFalse(policy.ma120_down_for_entry({120:{"delta":Decimal("0.0100")}}))

    def test_first_below120_short4_transition_at_0008333(self):
        before={n:{"delta":Decimal("-0.10"),"value":Decimal("40")} for n in policy.SHORT}
        before[120]={"delta":Decimal("0.008333"),"value":Decimal("45")}
        now={n:{"delta":Decimal("0.10"),"value":Decimal("41")} for n in policy.SHORT}
        now[120]={"delta":Decimal("0.008333"),"value":Decimal("45")}
        with patch.object(policy,"slopes",side_effect=lambda prices: before if len(prices)==121 else now):
            signal=policy.upward_transition_on_forming([Decimal("40")]*121,Decimal("42"),5)
        self.assertEqual(signal["kind"],"REVERSAL_UP",signal)
        self.assertEqual(signal["short4_below120"],True)

    def test_same_transition_above_ma120_rejected(self):
        before={n:{"delta":Decimal("-0.1"),"value":Decimal("50")} for n in policy.SHORT}
        before[120]={"delta":Decimal("0.008333"),"value":Decimal("45")}
        now={n:{"delta":Decimal("0.1"),"value":Decimal("51")} for n in policy.SHORT}
        now[120]={"delta":Decimal("0.008333"),"value":Decimal("45")}
        with patch.object(policy,"slopes",side_effect=lambda p: before if len(p)==121 else now):
            signal=policy.upward_transition_on_forming([Decimal("50")]*121,Decimal("52"),5)
        self.assertEqual(signal["kind"],None)
        self.assertEqual(signal["reason"],"SHORT4_NOT_BELOW_120")

class RSIIsolationTests(unittest.TestCase):
    def test_four_timeframes_with_no_one_minute_candle(self):
        self.assertEqual(mt.FRAMES,("5m","30m","60m","day"))
        self.assertEqual(mt.FRAME_SECS,{"5m":300,"30m":1800,"60m":3600,"day":86400})
        self.assertEqual(mt.RSI_PERIOD,20)

    def test_independent_history_and_live_rsi_for_same_xpl_symbol(self):
        ending=dt.datetime(2026,11,17,9,0,tzinfo=KST)
        history={}
        for frame,seconds in mt.FRAME_SECS.items():
            # Different genuine-type close series: no 1-minute resampling
            values=([Decimal(100)-Decimal(i) for i in range(25)]
                     if frame in ("5m","30m")
                     else [Decimal(70)+Decimal(i) for i in range(25)])
            history[("coin","KRW-XPL",frame)]=[
                {"end":ending-dt.timedelta(seconds=seconds*(25-i)),
                 "close":float(values[i])} for i in range(25)]
        class RT:
            pass
        rt=RT()
        rt.lock=threading.RLock()
        rt._history=history
        calculated={}
        for frame in mt.FRAMES:
            rows=Coordinator._closed_series(rt,"coin","KRW-XPL",frame,ending)
            self.assertEqual(len(rows),25)
            self.assertEqual(rows[-1]["end"],ending-dt.timedelta(seconds=mt.FRAME_SECS[frame]))
            calculated[frame]=mt.rsi20([x["close"] for x in rows],83.0)
        self.assertLess(calculated["5m"],30)
        self.assertGreater(calculated["60m"],30)
        self.assertGreater(calculated["day"],30)
        self.assertNotEqual(calculated["5m"],calculated["60m"])

    def test_preclose_book_order_retains_original_verified_frame(self):
        text=(PY/"market_trend_runtime_v043.py").read_text()
        self.assertIn("closed=self._closed_series(market,code,frame,finish)",text)
        self.assertIn("rsi=mt.rsi20(completed_closes,observed_trade)",text)
        self.assertIn("frame,rsi,barid,q=next(x for x in signals if x[0]==chosen)",text)
        self.assertIn("self.standalone_wallet.buy(market,frame,code,name,",text)

if __name__=="__main__":
    unittest.main()
