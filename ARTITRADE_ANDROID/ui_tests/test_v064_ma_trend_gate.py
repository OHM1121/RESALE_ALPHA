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
  prices=[100]*50+[220]*40+[220-i for i in range(32)]
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

 def test_qualified_rsi_can_reach_independent_paper_wallet_and_real_ledger(self):
  from tempfile import TemporaryDirectory
  from pathlib import Path
  class FakeRt:
   def __init__(self,path):
    self.root=Path(path);self.stock_runner=None;self.coin_runner=None;self._halted={}
   def live_enabled(self,market):return False
   def halt_market(self,*args):raise AssertionError('PAPER must not send actual orders')
  with TemporaryDirectory() as path:
   co=runtime.Coordinator(FakeRt(path))
   co._candidates['coin']={'KRW-BTC':('CRYPTO','KRW-BTC')}
   co._closed_series=lambda market,code,frame,bar_end:(
       [{'close':100.0} for _ in range(21)] if frame=='5m' else [])
   co._quote=lambda market,code:{'trade':70.,'ask':70.,'bid':69.,
                                  'ask_size':10000.,'bid_size':10000.}
   co.journal.save_latch('coin','KRW-BTC','5m',mt.SignalLatch(True))
   kst=mt.KST
   pre=dt.datetime(2026,11,16,10,4,45,tzinfo=kst)
   decision=dt.datetime(2026,11,16,10,4,48,tzinfo=kst)
   co._decision_clock=lambda:decision
   co._preclose_candidates('coin',pre)
   co._preclose_candidates('coin',decision)
   with co.standalone_wallet._connect('coin') as db:
    buys=db.execute("SELECT COUNT(*) FROM executions WHERE side='BUY' AND execution_mode='PAPER'").fetchone()[0]
   self.assertEqual(buys,1,'Qualified current RSI 30 must reach independent PAPER ledger; blocks='+repr(co._entry_block_counts['coin'])+'; prechecked='+repr(co._prechecked)+'; last='+repr(co._last_fetch_error))
   self.assertFalse(co._virtual,'PAPER fill must not be recorded as virtual')


 def test_epoch_ma_checked_without_optional_old_db_and_live_missing_base_blocks(self):
  from tempfile import TemporaryDirectory
  from pathlib import Path
  import json,sqlite3
  from market_trend_wallet_v043 import TrendWallet
  class FakeRt:
   def __init__(self,path,live):
    self.root=Path(path);self.live=live
    self.stock_runner=None;self.coin_runner=None;self._halted={}
   def live_enabled(self,market):return self.live
  with TemporaryDirectory() as path:
   root=Path(path)
   active=root/'coin/ma_epoch_v062/upbit_paper.sqlite3'
   active.parent.mkdir(parents=True,exist_ok=True)
   marker=active.parent/'ma_epoch_v062.json'
   marker.write_text(json.dumps({'strategy_version':'MA_V062_NEW_STRATEGY',
                                 'market':'coin','started_at':'2026-11-16T09:00:00+09:00'}))
   with sqlite3.connect(active) as db:
    db.execute("CREATE TABLE positions(market TEXT,status TEXT,qty REAL)")
    db.execute("CREATE TABLE orders(market TEXT,status TEXT)")
   co=runtime.Coordinator(FakeRt(root,False))
   self.assertFalse(co.wallet.native_owned('coin','KRW-BTC'))
   with sqlite3.connect(active) as db:
    db.execute("INSERT INTO positions VALUES('KRW-BTC','OPEN',1)")
   self.assertTrue(co.wallet.native_owned('coin','KRW-BTC'))
   with sqlite3.connect(active) as db:
    db.execute("DELETE FROM positions")
   co_live=runtime.Coordinator(FakeRt(root,True))
   self.assertTrue(co_live.wallet.native_owned('coin','KRW-BTC'),
                   'Missing other original LIVE ledger must remain fail closed')

if __name__=='__main__':unittest.main()
