"""v054 independent trend runner is never a market score switch of AT or MA."""
import datetime as dt
import json
import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import market_trend_v043 as mt
import market_trend_runtime_v043 as rt

class Stub:
    def __init__(self,root):
        self.root=Path(root)
        self.stock_runner=None;self.coin_runner=None
        self._halted={}
    def live_enabled(self,market):return False
    def halt_market(self,*args):pass


class IndependentRunner(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.co=rt.Coordinator(Stub(self.temp.name))
        self.co._read_score=lambda m:{'score':-90,'status':'정상','stale':False,'market_open':True}

    def test_negative_score_and_legacy_manual_no_longer_disable_at_ma(self):
        self.co.manual_trend['coin']=True
        self.co._update_score('coin')
        self.assertEqual(self.co.mode('coin'),'ORIGINAL')
        self.assertEqual(self.co.snapshot()['coin']['entry_mode'],'INDEPENDENT_TREND')

    def test_new_signal_uses_only_trend_wallet_and_mt54_event(self):
        co=self.co
        now=dt.datetime(2026,11,13,10,4,48,tzinfo=mt.KST)
        finish=dt.datetime(2026,11,13,10,5,tzinfo=mt.KST)
        co._candidates['coin']={'KRW-BTC':('CRYPTO','KRW-BTC')}
        co._quote=lambda market,code:{'trade':75,'ask':75,'bid':75,'ask_size':1000,'bid_size':1000}
        co._closed_series=lambda m,code,tf,end:[{'close':100-i,'end':end-dt.timedelta(seconds=300*(21-i))}
                                               for i in range(21)] if tf=='5m' else []
        co.wallet.native_owned=lambda m,s:False
        co.standalone_wallet.legacy_owned=lambda m,s:False
        co.journal.save_latch('coin','KRW-BTC','5m',mt.SignalLatch(True))
        bar=finish.isoformat()
        co._prechecked[('coin','KRW-BTC','5m',bar)]=25
        co._update_score('coin')
        co._preclose_candidates('coin',now)
        self.assertEqual(co.standalone_wallet.allocation('coin','5m')['positions'],1)
        self.assertEqual(co.standalone_wallet.allocation('coin','30m')['positions'],0)
        self.assertTrue(any(x['event_key'].startswith('MT54:SIG:')
                            for x in co.journal.events_recent(50)))
        self.assertFalse(any(x['event_key'].startswith('MT43:SIG:')
                             for x in co.journal.events_recent(50)))
        packet=json.loads(co.attach_history('{}'))
        self.assertEqual(packet['coin']['trend']['ledger']['positions'][0]['strategy'],'trend')

    def test_independent_stop_blocks_same_day_reentry(self):
        co=self.co
        co.standalone_wallet.legacy_owned=lambda m,s:False
        now=dt.datetime(2026,11,13,10,4,48,tzinfo=mt.KST)
        p,status=co.standalone_wallet.buy('coin','5m','KRW-ETH','ETH',10000,100,now,
                      '2026-11-13T10:05:00+09:00',29.0)
        self.assertEqual(status,'BOUGHT')
        co._positions[('coin','trend','5m','KRW-ETH')]=p
        co._quote=lambda market,code:{'bid':9000,'bid_size':1000}
        co._process_holds('coin',now+dt.timedelta(minutes=1))
        self.assertTrue(co.journal.stop_reentry_blocked('coin','KRW-ETH',now))

    def test_observation_below_35_does_not_generate_virtual(self):
        co=self.co
        co.watch.observe('coin','KRW-BTC','5m','2026-11-13T10:05:00+09:00',34,True,100)
        self.assertEqual(len(co.watch.snapshot('coin',now=100)),1)
        self.assertEqual(co._virtual,{})
        co.watch.expire('coin','5m','2026-11-13T10:05:00+09:00')
        self.assertEqual(co.watch.snapshot('coin',now=101),[])
        self.assertEqual(co._virtual,{})

if __name__=='__main__':
    import unittest;unittest.main()
