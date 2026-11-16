"""v077: KRX-only passive MA no-entry observer, no overnight false misses."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'app/src/main/python'
if str(APP) not in sys.path:sys.path.insert(0,str(APP))


class NoFalseAfterhoursMaAudit(unittest.TestCase):
    def test_stock_monitor_does_not_record_missed_buys_outside_krx(self):
        import market_trend_runtime_v043 as trend
        class Stop:
            def __init__(self):self.checks=0;self.slept=[]
            def is_set(self):
                self.checks+=1
                return self.checks>1
            def wait(self,sec):self.slept.append(sec)
        instance=trend.Coordinator.__new__(trend.Coordinator)
        instance.stop_event=Stop()
        instance._last_fetch_error={}
        def forbid(*args,**kwargs):
            raise AssertionError('No diagnostic entry outside KRX session')
        instance.record_ma_decision=forbid
        def no_krx(now,market):
            self.assertEqual(market,'KRX')
            return False
        with patch.dict(sys.modules,{'d_v19_portfolio':
                                      types.SimpleNamespace(live_session=no_krx)}):
            instance._ma_no_result_watchdog('stock')
        self.assertEqual(instance.stop_event.checks,2)
        self.assertTrue(instance.stop_event.slept)
        self.assertFalse(instance._last_fetch_error)

    def test_24h_coin_watch_does_not_gain_stock_market_session_guard(self):
        code=(APP/'market_trend_runtime_v043.py').read_text()
        watchdog=code.split('    def _ma_no_result_watchdog(self,market):',1)[1].split(
            '    def _book_evidence_loop(self,market):',1)[0]
        stock=watchdog.split("                if market=='stock':",1)[1].split(
            '                else:',1)[0]
        coin=watchdog.split('                else:',1)[1]
        self.assertIn("if not is_regular_open(now) or not live_session(now,'KRX'):",stock)
        self.assertIn("self.stop_event.wait(.8)",stock)
        self.assertIn('ma_v067_coin_runtime',coin)
        self.assertNotIn("live_session(now,'KRX')",coin)

if __name__=='__main__': unittest.main()
