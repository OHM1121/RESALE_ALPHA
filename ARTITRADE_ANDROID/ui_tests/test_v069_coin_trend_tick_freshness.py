"""RSI<=30 must not be lost solely because an honest Upbit ticker was quiet 5-7s.
Native broker and orderbook verification remain strictly fresh (<=3s).
"""
import datetime as dt
import threading
import time
import unittest
from types import SimpleNamespace

from market_trend_runtime_v043 import Coordinator,COIN_VERIFIED_TRADE_MAX_AGE_SEC
from market_trend_v043 import SignalLatch,preclose_due,live_rsi_check_due

KST=dt.timezone(dt.timedelta(hours=9))


class TrendTickerFreshnessV069(unittest.TestCase):
    def setUp(self):
        self.c=Coordinator.__new__(Coordinator)
        self.c.lock=threading.RLock()
        self.c._quotes={'stock':{},'coin':{}}
        self.book=SimpleNamespace(timestamp=time.time()*1000,bid=86.8,ask=86.9,
            ask_size=500.0,bid_size=350.0)
        self.runner=SimpleNamespace(_lock=threading.RLock(),
            last_book={'KRW-MPLX':self.book.__dict__})
        self.c.rt=SimpleNamespace(coin_runner=self.runner,stock_runner=None)
    def quote(self,age):
        self.c._quotes['coin']['KRW-MPLX']=(86.9,time.monotonic()-age)
        self.book.timestamp=time.time()*1000
        return self.c._quote('coin','KRW-MPLX')
    def test_public_historical_decision_age_five_and_seven_seconds(self):
        self.assertEqual(COIN_VERIFIED_TRADE_MAX_AGE_SEC,10.0)
        for age in (5.0,7.0):
            with self.subTest(age=age):
                r=self.quote(age)
                self.assertIsNotNone(r)
                self.assertEqual(r['trade'],86.9)
                self.assertEqual(r['ask'],86.9)
                self.assertEqual(r['ask_size'],500.0)
    def test_expired_trade_remains_blocked(self):
        self.assertIsNone(self.quote(11.0))
    def test_missing_trade_not_synthesized_from_book(self):
        self.assertIsNone(self.c._quote('coin','KRW-MPLX'))
    def test_orderbook_must_be_within_three_seconds(self):
        self.quote(7)
        self.book.timestamp=(time.time()-4.2)*1000
        self.assertIsNone(self.c._quote('coin','KRW-MPLX'))
    def test_invalid_depth_cannot_trade_even_if_price_verified(self):
        self.quote(7)
        self.book.ask_size=0
        self.assertIsNone(self.c._quote('coin','KRW-MPLX'))
    def test_exact_five_minute_decision_windows_unchanged(self):
        finish=dt.datetime(2026,11,16,10,55,tzinfo=KST)
        self.assertTrue(live_rsi_check_due(finish-dt.timedelta(seconds=15),finish,'5m','coin'))
        self.assertTrue(preclose_due(finish-dt.timedelta(seconds=12),finish,'5m','coin'))
        self.assertTrue(preclose_due(finish-dt.timedelta(seconds=11),finish,'5m','coin'))
        self.assertFalse(preclose_due(finish-dt.timedelta(seconds=6),finish,'5m','coin'))
    def test_rsi_30_latch_is_unchanged(self):
        l=SignalLatch()
        l.completed('2026-11-16T10:50:00+09:00',30.914)
        self.assertTrue(l.offer('2026-11-16T10:55:00+09:00',29.823))
        self.assertFalse(l.offer('2026-11-16T10:55:00+09:00',29.823))
    def test_rest_mark_updates_cache_but_never_renews_live_trade_age(self):
        import market_trend_runtime_v043 as t
        seen=[]
        box=t._PriceMemory({},lambda code,price:seen.append((code,price)))
        box['KRW-MPLX']=86.9
        self.assertEqual(seen,[('KRW-MPLX',86.9)])
        previous=getattr(t._PRICE_OBSERVATION_CONTEXT,'rest_only',False)
        t._PRICE_OBSERVATION_CONTEXT.rest_only=True
        try:
            box['KRW-MPLX']=86.7
        finally:
            t._PRICE_OBSERVATION_CONTEXT.rest_only=previous
        self.assertEqual(box['KRW-MPLX'],86.7)
        self.assertEqual(seen,[('KRW-MPLX',86.9)])
        box['KRW-MPLX']=86.6
        self.assertEqual(seen[-1],('KRW-MPLX',86.6))

    def test_block_audit_is_bounded_and_not_a_fake_order(self):
        import collections
        self.c._recent_blocked_entries={'stock':collections.deque(maxlen=160),
                                        'coin':collections.deque(maxlen=160)}
        self.c._record_entry_block('coin','KRW-MPLX','5m','2026-11-16T10:55:00+09:00',
                                   'FRESH_BOOK_MISSING_AT_SUBMIT',29.823,7.0)
        d=self.c._recent_blocked_entries['coin']
        self.assertEqual(d[0]['code'],'KRW-MPLX')
        self.assertEqual(d[0]['reason'],'FRESH_BOOK_MISSING_AT_SUBMIT')
        self.assertEqual(d[0]['rsi20_live'],29.823)
        self.assertEqual(d[0]['last_trade_age_sec'],7.0)
        self.assertNotIn('order',d[0])

if __name__=='__main__':
    unittest.main()
