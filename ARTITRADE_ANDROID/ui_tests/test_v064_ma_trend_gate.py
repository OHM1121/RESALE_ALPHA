"""v064 fail-closed gates using synthetic closed OHLC/RSI data only."""
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import ma_v061_rules as ma
import market_trend_v043 as mt
import market_trend_runtime_v043 as runtime

class MaSellPriority(unittest.TestCase):
 def test_120up_four_above_and_down_never_buys(self):
  # Long-term SMA still rises, but all 4 shorter SMA slopes fall above it.
  prices=[100+i for i in range(90)]+[190-i*.5 for i in range(32)]
  bars=[]
  for p in prices:
   bars.append({'open':str(p+1),'close':str(p),'high':str(p+2),
                'low':str(p-1)})
  metrics=ma.ma_values([ma.dec(x['close']) for x in bars],len(bars)-1)
  self.assertGreater(metrics[120]['delta'],0)
  self.assertTrue(all(metrics[n]['value']>metrics[120]['value'] and
                      metrics[n]['delta']<0 for n in (5,10,20,60)))
  self.assertEqual(ma.forced_exit_reason([x['close'] for x in bars[:-1]],
                 bars[-1]['close']),'MA_V061_FORCE_120UP_TOP4_DOWN')
  for frame in ma.FRAMES:
   result=ma.candidate_from_closed(bars,frame)
   self.assertIsNone(result['kind'])
   self.assertEqual(result['reason'],'MA_V061_FORCE_120UP_TOP4_DOWN_NO_BUY')
 def test_plain_three_down_bear_stays_eligible(self):
  prices=[200-i*.25 for i in range(122)]
  bars=[{'open':str(p+1),'close':str(p),'high':str(p+2),
         'low':str(p-1)} for p in prices]
  self.assertEqual(ma.candidate_from_closed(bars,'30m')['kind'],'DOWN_BEAR')
 def test_first_turnpoint_rule_unchanged(self):
  self.assertEqual(ma.FIRST_TARGET_PCT['30m'],ma.dec(2))

class TrendTimeGate(unittest.TestCase):
 def test_rsi_latch_is_not_a_fill(self):
  latch=mt.SignalLatch()
  latch.completed('prev',32)
  self.assertTrue(latch.offer('current',29))
  self.assertFalse(latch.offer('current',28))
 def test_stock_30m_and_coin_30m_boundary(self):
  kst=mt.KST
  now=dt.datetime(2026,11,16,10,29,48,tzinfo=kst)
  end=dt.datetime(2026,11,16,10,30,tzinfo=kst)
  self.assertTrue(mt.preclose_due(now,end,'30m','stock'))
  self.assertTrue(mt.preclose_due(now,end,'30m','coin'))
if __name__=='__main__':unittest.main()
