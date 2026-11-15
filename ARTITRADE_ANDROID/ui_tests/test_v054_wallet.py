"""v054 paper wallet capital must be segregated per market/timeframe."""
import sys
import sqlite3
import types
from unittest import mock
import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
from market_trend_v043 import KST
from market_trend_wallet_v054 import TrendWalletV054


class DummyBroker:
    def __init__(self):self.orders=[]
    def ready(self):return True
    def fok(self,*args,**kwargs):
        self.orders.append((args,kwargs))
        raise AssertionError('LIVE disabled for independent trend until explicit enable')


class RT:
    def __init__(self,root):
        self.root=Path(root)
        self.upbit=DummyBroker();self.kiwoom=DummyBroker()
        self._halted={}
        self.stock_runner=SimpleNamespace(market_map={'005930':('KOSPI','Test')})
    def live_enabled(self,market):return False  # v054 tests are explicitly PAPER; v059 separately tests market LIVE parity
    def halt_market(self,*args):raise AssertionError('Unexpected halt')


class WalletV054(TestCase):
    def setUp(self):
        self.t=TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.rt=RT(self.t.name)
        self.w=TrendWalletV054(self.rt)
        self.at=dt.datetime(2026,11,13,10,4,48,tzinfo=KST)

    def test_independent_capital_across_all_eight_frames(self):
        for m in ('stock','coin'):
            for tf in ('5m','30m','60m','day'):
                row=self.w.allocation(m,tf)
                self.assertEqual((row['seed'],row['cash'],row['max_positions']),
                                 (2_000_000,2_000_000,4))
        pos,status=self.w.buy('coin','5m','KRW-BTC','BTC',10000,20,self.at,
                    '2026-11-13T10:05:00+09:00',29.5)
        self.assertEqual(status,'BOUGHT')
        self.assertIsNotNone(pos)
        self.assertLess(self.w.allocation('coin','5m')['cash'],2_000_000)
        self.assertEqual(self.w.allocation('coin','30m')['cash'],2_000_000)
        self.assertEqual(self.w.allocation('stock','5m')['cash'],2_000_000)
        self.assertEqual(self.rt.upbit.orders,[])

    def test_position_recovery_preserves_frame_balance(self):
        pos,status=self.w.buy('coin','30m','KRW-ETH','ETH',10000,20,self.at,
                    '2026-11-13T10:30:00+09:00',28.5)
        self.assertEqual(status,'BOUGHT')
        balance=self.w.allocation('coin','30m')['cash']
        restored=TrendWalletV054(self.rt)
        positions=restored.open_positions('coin')
        self.assertEqual(len(positions),1)
        self.assertEqual(positions[0].frame,'30m')
        self.assertEqual(positions[0].symbol,'KRW-ETH')
        self.assertEqual(restored.allocation('coin','30m')['cash'],balance)

    def test_seed_adjustment_only_changes_requested_frame(self):
        self.w.apply_settings('coin',{'5m':{'seed':3_000_000,'max_positions':6},
                             '30m':{'seed':2_000_000,'max_positions':4},
                             '60m':{'seed':2_000_000,'max_positions':4},
                             'day':{'seed':2_000_000,'max_positions':4}})
        self.assertEqual(self.w.allocation('coin','5m')['cash'],3_000_000)
        self.assertEqual(self.w.allocation('coin','5m')['max_positions'],6)
        self.assertEqual(self.w.allocation('coin','day')['cash'],2_000_000)

    def test_duplicate_symbol_other_frame_is_blocked(self):
        _,first=self.w.buy('coin','5m','KRW-BTC','BTC',10000,20,self.at,
                           '2026-11-13T10:05:00+09:00',28.5)
        self.assertEqual(first,'BOUGHT')
        other,disposition=self.w.buy('coin','day','KRW-BTC','BTC',10000,20,self.at,
                                     '2026-11-14T09:00:00+09:00',28.5)
        self.assertIsNone(other)
        self.assertEqual(disposition,'ALREADY_OWNED_OR_PENDING')

    def test_stock_wallet_is_separate_and_can_sell(self):
        p,state=self.w.buy('stock','5m','005930','TEST',10000,100,self.at,
                          '2026-11-13T10:05:00+09:00',29.0)
        self.assertEqual(state,'BOUGHT')
        self.assertEqual(p.strategy,'trend')
        self.assertGreater(p.qty,0)
        # Existing -9% stop is still owned by the original TrendPosition.
        sold,state=self.w.sell(p,9000,self.at+dt.timedelta(seconds=10),'MT_STOP_9PCT',bid_size=100)
        self.assertEqual(state,'SOLD')
        self.assertEqual(p.qty,0)
        self.assertEqual(self.w.allocation('stock','5m')['positions'],0)
        self.assertEqual(self.w.allocation('stock','30m')['cash'],2_000_000)

    def test_operation_settings_migrate_to_independent_frames(self):
        with mock.patch.dict(sys.modules, {'android_live_bridge':types.ModuleType('android_live_bridge')}):
            import android_operation_bridge as op
            old={'stock':{'at':{'frames':{tf:{'seed':7_000_000,'max_positions':9}
                              for tf in ('5m','30m','60m','day')}}},
                 'coin':{'general':{'frames':{tf:{'seed':4_000_000,'max_positions':5}
                              for tf in ('5m','30m','60m','day')}}}}
            validated=op._validate(old)
            self.assertEqual(validated['stock']['at']['frames']['5m']['seed'],7_000_000)
            self.assertEqual(validated['coin']['general']['frames']['30m']['seed'],4_000_000)
            self.assertEqual(validated['coin']['trend']['frames']['day']['seed'],2_000_000)
            self.assertEqual(validated['stock']['trend']['frames']['5m']['max_positions'],4)

    def test_old_funds_and_old_research_untouched(self):
        old=self.rt.root/'coin/base/upbit_paper.sqlite3'
        old.parent.mkdir(parents=True,exist_ok=True)
        old.write_bytes(b'UNTOUCHED_ORIGINAL_LEDGER')
        self.w.buy('coin','5m','KRW-BTC','BTC',10000,20,self.at,
                   '2026-11-13T10:05:00+09:00',29.5)
        self.assertEqual(old.read_bytes(),b'UNTOUCHED_ORIGINAL_LEDGER')
        self.assertFalse((self.rt.root/'market_trend_v043.sqlite3').exists())

if __name__=='__main__':
    import unittest;unittest.main()
