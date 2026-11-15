"""Standalone trend watch is not an order or shadow trade."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app/src/main/python'))
from market_trend_watch_v054 import WatchBook


class WatchV054(unittest.TestCase):
    def test_35_is_visible_and_40_is_internal_only(self):
        w=WatchBook()
        self.assertEqual(w.observe('coin','KRW-BTC','5m','barA',40.0,False,100,price=100), 'INTERNAL')
        self.assertEqual(w.snapshot('coin',now=100), [])
        self.assertEqual(w.observe('coin','KRW-BTC','5m','barA',35.0,False,101,price=99), 'WATCH_PENDING')
        rows=w.snapshot('coin',now=101)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['rsi20_live'],35.0)
        self.assertFalse(rows[0]['armed'])

    def test_rebound_above_35_removes_without_virtual(self):
        w=WatchBook()
        w.observe('coin','KRW-BTC','5m','barA',34.9,True,100)
        self.assertEqual(w.observe('coin','KRW-BTC','5m','barA',35.01,True,101),'REMOVED')
        self.assertEqual(w.snapshot('coin',now=101), [])
        self.assertEqual(w.virtual_signals,0)

    def test_at_30_still_only_observation(self):
        w=WatchBook()
        w.observe('coin','KRW-BTC','30m','barB',30.0,True,100)
        x=w.snapshot('coin',now=101)[0]
        self.assertTrue(x['touched_30'])
        self.assertEqual(x['status'],'WATCH_PENDING')
        self.assertEqual(w.virtual_signals,0)

    def test_different_frames_and_expiry(self):
        w=WatchBook()
        w.observe('coin','KRW-BTC','5m','barA',35,True,100)
        w.observe('coin','KRW-BTC','30m','barB',34,True,100)
        w.expire('coin','5m','barA')
        self.assertEqual([x['frame'] for x in w.snapshot('coin',now=101)],['30m'])
        self.assertEqual(w.virtual_signals,0)

    def test_invalid_or_stale_observations_not_shown(self):
        w=WatchBook()
        w.observe('coin','KRW-BTC','5m','barA',34,True,100)
        self.assertEqual(w.snapshot('coin',now=150), [])
        w.observe('coin','KRW-BTC','5m','barA',33,True,160,fresh=False)
        self.assertEqual(w.snapshot('coin',now=161), [])

if __name__ == '__main__':unittest.main()
