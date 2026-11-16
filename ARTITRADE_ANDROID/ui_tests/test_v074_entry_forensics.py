"""v074: real-world specified deadline and durable no-order forensic regression.

Offline only: no broker auth, funds, actual orders, synthetic filled trades, or
replayed missed opportunities. Historical Upbit public 1s check is separate CI.
"""
from __future__ import annotations
import datetime as dt
import json
import queue
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP=Path(__file__).resolve().parents[1]/'app/src/main/python'
if str(APP) not in sys.path:sys.path.insert(0,str(APP))
KST=dt.timezone(dt.timedelta(hours=9))


class V074ForensicTests(unittest.TestCase):
    def fake_coordinator(self,root):
        import market_trend_runtime_v043 as v
        class FakeRuntime:
            def __init__(self):
                self.root=Path(root)
                self.stock_runner=None
                self.coin_runner=None
                self._halted={}
            def live_enabled(self,market):
                return False
        return v.Coordinator(FakeRuntime())

    def test_exact_20261116_user_trend_gate_seconds(self):
        import market_trend_v043 as mt
        for stamp in ((10,9,45),(10,49,45)):
            finish=dt.datetime(2026,11,16,stamp[0],stamp[1]+1,0,tzinfo=KST)
            at=dt.datetime(2026,11,16,*stamp,tzinfo=KST)
            market='stock' if stamp[1]==9 else 'coin'
            self.assertTrue(mt.live_rsi_check_due(at,finish,'5m',market))
            self.assertTrue(mt.preclose_due(at+dt.timedelta(seconds=3),
                                             finish,'5m',market))
            self.assertTrue(mt.preclose_due(at+dt.timedelta(seconds=4),
                                             finish,'5m',market))
            self.assertFalse(mt.preclose_due(at+dt.timedelta(seconds=9),
                                              finish,'5m',market))

    def test_research_and_error_preserve_distinct_no_order_reasons_after_restart(self):
        with tempfile.TemporaryDirectory() as root:
            co=self.fake_coordinator(root)
            try:
                bar='2026-11-16T10:50:00+09:00'
                co._record_entry_block('coin','KRW-DRV','5m',bar,
                                       'RSI30_OBSERVED_NOT_AN_ORDER',27.549332)
                co._record_entry_block('coin','KRW-DRV','5m',bar,
                                       'FRESH_BOOK_MISSING_AT_SUBMIT',27.549332,3.)
                co._record_entry_block('coin','KRW-DRV','5m',bar,
                                       'FRESH_BOOK_MISSING_AT_SUBMIT',27.549332,3.)
                co.record_ma_decision('coin','KRW-SKR','60m',
                    '2026-11-16T10:00:00+09:00',
                    'UP_RULE_NO_SIMULTANEOUS_SHORT4_UP_120_DOWN')
                co._v074_audit_queue.join()
                saved=co._v074_evidence.recent(30)
                self.assertEqual(len(saved),3)
                self.assertTrue(all(x['mode']!='FILLED' for x in saved))
                out=json.loads(co.attach_history(json.dumps({
                    'stock':{},'coin':{'ma':{'ledger':{},'research':{'items':[]}}}})))
                trend_rows=out['coin']['trend']['research']['items']
                ma_rows=out['coin']['ma']['research']['items']
                self.assertEqual(len(trend_rows),2)
                self.assertEqual(len(ma_rows),1)
                self.assertTrue(all(x['bought'] is False for x in trend_rows+ma_rows))
                self.assertTrue(all(x['execution_mode']=='NO_ORDER' for x in trend_rows+ma_rows))
                self.assertEqual(out['coin']['trend']['ledger'].get('positions',[]),[])
                self.assertTrue(any('FRESH_BOOK_MISSING' in x['stage']
                    for x in out['market_trend_audit_errors']['coin']))
                # New Coordinator can read the existing independent SQLite
                # without adding a single buy, order or market quote.
                other=self.fake_coordinator(root)
                try:
                    self.assertEqual(len(other._v074_evidence.recent(30)),3)
                finally:other.stop()
            finally:co.stop()

    def test_20261116_seeker_hourly_candle_boundary_and_missing_history_reason(self):
        import ma_v067_coin_runtime as m
        at=dt.datetime(2026,11,16,9,59,55,tzinfo=KST)
        remaining,finish=m._bar_close(at,'60m')
        self.assertEqual(remaining,5)
        self.assertEqual(finish.hour,10)
        self.assertEqual(finish.minute,0)
        signals=[]
        ledger=SimpleNamespace(
            sr_instrument_ticks={'KRW-SKR':'.1'},
            _ma_v067_live_ticks={'KRW-SKR':(15.5,time.monotonic())})
        book={'market':'KRW-SKR','timestamp':int(at.timestamp()*1000),
              'ask':15.6,'bid':15.5,'ask_size':10000,'sr_tick_size':'.1'}
        with patch.object(m,'_record_v074_ma_observation',
                          side_effect=lambda *args:signals.append(args)):
            m._new_up_candidate(ledger,book,at)
        # Real book data is fresh at the specified historical decision timestamp.
        # But unverified historical SMA never creates a fake candidate.
        self.assertTrue(any(x[1]=='60m' and
                    x[3]=='UP_RULE_SMA_HISTORY_REQUIRED' for x in signals))

    def test_upbit_late_ticker_with_still_fresh_real_book_wakes_native_ma_wallet(self):
        import ma_v067_coin_runtime as m
        real=dt.datetime.now(KST)
        stop=threading.Event()
        calls=[]
        def process(book,when):
            calls.append((book,when))
            stop.set()
            return 0
        book={'market':'KRW-SKR','timestamp':int(real.timestamp()*1000),
              'ask':15.6,'bid':15.5,'ask_size':100,'sr_tick_size':'.1'}
        runner=SimpleNamespace(
            stop_event=stop,
            _lock=threading.RLock(),
            last_book={'KRW-SKR':book},
            ma_ledger=SimpleNamespace(process_book=process),
            _ma_v074_tick_queue=queue.Queue())
        runner._ma_v074_tick_queue.put_nowait(('KRW-SKR',time.monotonic()))
        with patch.object(m,'_bar_close',side_effect=lambda now,tf:
                          (5,now+dt.timedelta(seconds=5)) if tf=='60m'
                          else (50,now+dt.timedelta(seconds=50))):
            worker=threading.Thread(target=m._v074_coin_tick_worker,args=(runner,))
            worker.start()
            runner._ma_v074_tick_queue.join()
            stop.set()
            worker.join(timeout=1.5)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0]['market'],'KRW-SKR')

    def test_stale_cached_book_cannot_reenter_native_order_engine(self):
        import ma_v067_coin_runtime as m
        stop=threading.Event()
        calls=[]
        book={'market':'KRW-SKR',
              'timestamp':int((time.time()-7)*1000),
              'ask':15.6,'bid':15.5,'ask_size':100}
        runner=SimpleNamespace(
            stop_event=stop,_lock=threading.RLock(),
            last_book={'KRW-SKR':book},
            ma_ledger=SimpleNamespace(process_book=lambda *a:calls.append(a)),
            _ma_v074_tick_queue=queue.Queue())
        runner._ma_v074_tick_queue.put_nowait(('KRW-SKR',time.monotonic()))
        with patch.object(m,'_bar_close',side_effect=lambda now,tf:
                          (5,now+dt.timedelta(seconds=5)) if tf=='60m'
                          else (50,now+dt.timedelta(seconds=50))):
            worker=threading.Thread(target=m._v074_coin_tick_worker,args=(runner,))
            worker.start()
            runner._ma_v074_tick_queue.join()
            stop.set()
            worker.join(timeout=1.5)
        self.assertEqual(calls,[])


if __name__=='__main__':
    unittest.main(verbosity=2)
