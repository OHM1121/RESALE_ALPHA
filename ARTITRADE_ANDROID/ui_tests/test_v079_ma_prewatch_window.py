"""v0.7.79 broker-independent tests: no orders during read-only prewatch."""
import datetime as dt
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'app/src/main/python'))
import ma_v067_policy as policy
import ma_v067_coin_runtime as coin
import ma_v067_stock_runtime as stock


class V079MAWatchAndEntry(unittest.TestCase):
    def test_exact_time_gates_and_no_prewatch_order(self):
        for sec in (30,29.99,20,11,10.0001):
            self.assertTrue(policy.in_ma_prewatch_window(sec),sec)
            self.assertFalse(policy.in_up_entry_window(sec),sec)
        for sec in (10,9.999,5,2):
            self.assertFalse(policy.in_ma_prewatch_window(sec),sec)
            self.assertTrue(policy.in_up_entry_window(sec),sec)
        for sec in (31,1.999,1,0,-1,None):
            self.assertFalse(policy.in_ma_prewatch_window(sec),sec)
            self.assertFalse(policy.in_up_entry_window(sec),sec)
        self.assertEqual(policy.MA_PREWATCH_BEFORE_CLOSE_SECONDS,30)
        self.assertEqual(policy.MAX_UP_FILL_DELAY_SECONDS,15)

    def test_first_flip_is_preview_only_until_verified_buy_window(self):
        prior=[Decimal('100')]*121
        # v089 requires four short SMA VALUES below MA120 in addition to
        # the previously verified slope and timing contract.
        before={n:{'delta':Decimal('-0.20'),'value':Decimal('90')}
                for n in policy.SHORT}
        before[120]={'delta':Decimal('-0.20'),'value':Decimal('100')}
        current={n:{'delta':Decimal('0.10'),'value':Decimal('90')}
                 for n in policy.SHORT}
        current[120]={'delta':Decimal('0.0050'),'value':Decimal('100')}
        def fake_slopes(series):
            return before if len(series)==121 else current
        with patch.object(policy,'slopes',side_effect=fake_slopes):
            preview=policy.prewatch_upward_alignment(prior,Decimal('101'))
            self.assertEqual(preview['kind'],'REVERSAL_UP')
            for sec in (30,11,10.0001,1.99,0):
                result=policy.upward_transition_on_forming(prior,Decimal('101'),sec)
                self.assertIsNone(result['kind'],sec)
            for sec in (10,8,5,2):
                result=policy.upward_transition_on_forming(prior,Decimal('101'),sec)
                self.assertEqual(result['kind'],'REVERSAL_UP',sec)
            # v089: +0.0099 is now inclusive, test strictly over the NEW cap.
            current[120]['delta']=Decimal('0.0100')
            result=policy.upward_transition_on_forming(prior,Decimal('101'),5)
            self.assertIsNone(result['kind'])
            self.assertEqual(result['reason'],'NO_SIMULTANEOUS_SHORT4_UP_120_DOWN')

    def test_actual_bar_close_for_stock_and_upbit(self):
        kst=coin.KST
        s=lambda h,m,sec:dt.datetime(2026,11,16,h,m,sec,tzinfo=kst)
        self.assertEqual(int(stock._close_seconds(s(9,59,50),'5m')[0]),10)
        self.assertEqual(int(stock._close_seconds(s(9,59,58),'5m')[0]),2)
        self.assertEqual(int(coin._bar_close(s(8,59,50),'day')[0]),10)
        self.assertEqual(coin._bar_close(s(8,59,50),'day')[1].hour,9)
        self.assertEqual(coin._bar_close(s(9,0,0),'day')[1].date(),
                         dt.date(2026,11,17))
        self.assertEqual(int(coin._bar_close(s(9,59,50),'5m')[0]),10)

    def test_broker_and_order_protections_are_not_preview_paths(self):
        stock_src=(BASE/'app/src/main/python/ma_v067_stock_runtime.py').read_text()
        coin_src=(BASE/'app/src/main/python/ma_v067_coin_runtime.py').read_text()
        trend_src=(BASE/'app/src/main/python/market_trend_runtime_v043.py').read_text()
        ui_src=(BASE/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertIn('MA_PREWATCH_',stock_src)
        self.assertIn('MA_PREWATCH_',coin_src)
        self.assertIn('policy.in_up_entry_window(remain)',stock_src)
        self.assertIn('policy.in_up_entry_window(remain)',coin_src)
        self.assertIn('UP_LAST_VERIFIED_TRADE_RULE_LOST',stock_src)
        self.assertIn('UP_LAST_RECHECK_ALIGNMENT_LOST',coin_src)
        self.assertIn('exchange_stamp_is_fresh',stock_src)
        self.assertIn('with runner.engine.book._lock:',stock_src)
        self.assertIn("abs(submit_now.timestamp()-book['timestamp']/1000)>3",coin_src)
        self.assertIn('not str(key[5]).startswith(\'MA_PREWATCH_\')',trend_src)
        self.assertIn('for(int phase=0;phase<2;phase++)',ui_src)
        self.assertIn('MA 매수대기',ui_src)
        self.assertIn('사전 관측 · 30~10초 전 · 미주문',ui_src)
        self.assertEqual(policy.MAX_UP_FILL_DELAY_SECONDS,15)


if __name__=='__main__':
    unittest.main()
