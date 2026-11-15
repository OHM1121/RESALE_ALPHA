"""v060 original-code MA parity and narrow stale ORIGINAL reference retirement.

Synthetic KST fixtures; do not present these examples as the customer's
actual PolySwarm broker fills or the exchange's true historical OHLCV.
"""
import datetime as dt
import sqlite3
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import reference_integrity_v059 as guard
from market_trend_v043 import KST
from coin_hold_policy import ORDER_LIFE_SECONDS
from d_upbit import CoinLedger,ref_features
from coin_ma_engine import MACoinLedger,ma_reference_decision


def fixture_history(last_volume=240):
    last=dt.datetime(2026,11,15,22,30,tzinfo=KST).astimezone(dt.timezone.utc)
    first=last-dt.timedelta(minutes=30*145)
    result=[]
    for i in range(146):
        at=first+dt.timedelta(minutes=30*i)
        kst=at.astimezone(KST)
        quantity=Decimal('100')
        if (kst.day,kst.hour,kst.minute)==(15,16,0):
            quantity=Decimal('230')
        if (kst.day,kst.hour,kst.minute)==(15,22,30):
            quantity=Decimal(str(last_volume))
        result.append({'start':at,'volume':quantity,'open':Decimal(10),
                       'high':Decimal(10.5),'low':Decimal(10),
                       'close':Decimal(10.1)})
    return result


def old_16_record():
    at=dt.datetime(2026,11,15,16,0,tzinfo=KST).astimezone(dt.timezone.utc)
    return {'frame':'30m','market':'KRW-NCT','ref_at':at.isoformat(),
            'ref_end':(at+dt.timedelta(minutes=30)).isoformat(),
            'limit':'10.0','target':'11.0','score':85.,
            'features':{'open':11.,'high':11.5,'low':9.4,'close':11.,
                        'volume':230,'range_pct':22.0,
                        'upper_wick_fraction':0.1,'lower_wick_fraction':0.2}}


class OriginalReferenceParity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.original=CoinLedger(root/'base')
        self.ma=MACoinLedger(root/'ma')
        self.ref_at=dt.datetime(2026,11,15,16,31,tzinfo=KST)
        self.buy_at=dt.datetime(2026,11,16,0,53,11,tzinfo=KST)
        self.assertTrue(self.original.record_candidate(old_16_record(),'폴리스웜',self.ref_at))
        self.assertTrue(self.ma.record_candidate(old_16_record(),'폴리스웜',self.ref_at))

    def _pending(self,w):
        with w.connect() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM orders WHERE status='PENDING' ORDER BY id")]

    def test_older_16_order_is_active_12_hours_without_newer_record_cleanup(self):
        self.assertEqual(ORDER_LIFE_SECONDS['30m'],43200)
        self.assertGreater(self._pending(self.original)[0]['expires_at'],
                           self.buy_at.isoformat())
        self.assertEqual(len(self._pending(self.original)),1)

    def test_new_verified_quantity_record_cancels_only_original_unfilled_order(self):
        runner=SimpleNamespace(ledger=self.original,ma_ledger=self.ma)
        history=fixture_history(240)
        next_candle=history[-1]
        self.assertIsNone(ref_features(history,'30m','KRW-NCT',Decimal('0.01'),
                                      dt.datetime(2026,11,16,0,50,35,tzinfo=KST)))
        # 22:30 made a true quantity high, but failed the unrelated 3%
        # close-vs-low movement gate: legacy scanner left 16:00 pending.
        self.assertEqual(guard._cancel_superseded_original_pending(
            runner,history,'30m','KRW-NCT',
            dt.datetime(2026,11,16,0,50,35,tzinfo=KST)),1)
        self.assertFalse(self._pending(self.original))
        self.assertEqual(len(self._pending(self.ma)),1)
        with self.original.connect() as c:
            self.assertEqual(c.execute(
                "SELECT status FROM orders").fetchone()['status'],'REPLACED')
            reasons=[r[0] for r in c.execute(
                "SELECT decision FROM candidate_decisions ORDER BY id")]
            self.assertIn('REPLACED',reasons)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM positions").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions").fetchone()[0],0)

    def test_turnover_high_without_base_quantity_high_never_cancels_original(self):
        runner=SimpleNamespace(ledger=self.original,ma_ledger=self.ma)
        self.assertEqual(guard._cancel_superseded_original_pending(
            runner,fixture_history(220),'30m','KRW-NCT',self.buy_at),0)
        self.assertEqual(len(self._pending(self.original)),1)

    def test_insufficient_history_does_not_guess_or_cancel(self):
        runner=SimpleNamespace(ledger=self.original,ma_ledger=self.ma)
        self.assertEqual(guard._cancel_superseded_original_pending(
            runner,fixture_history(240)[-25:],'30m','KRW-NCT',self.buy_at),0)
        self.assertEqual(len(self._pending(self.original)),1)

    def test_preserve_native_ma_special_and_low_buy(self):
        # Native decision source is used unchanged; no v059 strict-record
        # guard or turnover-as-quantity rewrite remains in the integration.
        guard_source=(ROOT/'app/src/main/python/reference_integrity_v059.py').read_text()
        self.assertNotIn('coin_ma_engine.ma_reference_decision=',guard_source)
        self.assertNotIn('stock_ma_engine.ma_reference_decision=',guard_source)
        self.assertNotIn('d_upbit.ref_features=',guard_source)
        self.assertNotIn('d_upbit.point_in_time_bars=',guard_source)
        ref=dt.datetime(2026,11,15,22,30,tzinfo=KST).astimezone(dt.timezone.utc)
        bars=[]
        for i in range(122):
            when=ref-dt.timedelta(minutes=30*(121-i))
            close=Decimal('124.2')-Decimal(i)*Decimal('.20')
            op=close+(Decimal(3) if i==121 else Decimal('.1'))
            volume=Decimal(100)
            if i==60:volume=Decimal(2000)
            if i==121:volume=Decimal(1000)
            bars.append({'start':when,'open':op,'close':close,
                         'high':op+Decimal('.5'),'low':close-Decimal('.5'),
                         'volume':volume})
        audit,candidate=ma_reference_decision(
            bars,'30m','KRW-NCT',Decimal('.01'),self.buy_at)
        self.assertEqual(audit['reason'],'MA_BEAR_SPECIAL_PLUS10')
        self.assertEqual(candidate['features']['ma_down'],5)
        self.assertEqual(candidate['limit'],'99.50')


if __name__=='__main__':
    unittest.main()
