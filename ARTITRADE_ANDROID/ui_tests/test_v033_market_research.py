"""ARTITRADE v033: score cohorts must be time-ordered, never retrospective fills."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'app/src/main/python'))
import market_research_v033 as m

class MarketRegimeTests(unittest.TestCase):
    def test_stock_original_score_scale(self):
        a=m.stock_score({'KOSPI':1.5,'KOSDAQ':2.0,'NASDAQ':1.8}, {})
        self.assertEqual(a['score'],100)
        b=m.stock_score({'KOSPI':-1.5,'KOSDAQ':-2.0,'NASDAQ':-1.8}, {})
        self.assertEqual(b['score'],-100)
        self.assertLess(a['coverage'],1.0)
        self.assertIsNone(m.stock_score({}, {})['score'])

    def test_crypto_breadth_missing_candles_cannot_make_score(self):
        self.assertIsNone(m.coin_score([], [], [])['score'])

    def test_stale_market_snapshot_is_not_claimed_current(self):
        old={'score':55,'asof':1000,'status':'정상','factors':[]}
        result=m.display_snapshot('stock',old,now=1000+3700)
        self.assertTrue(result['stale'])
        self.assertEqual(result['score'],55)
        self.assertIn('오래됨',result['status'])
        failure=m.display_snapshot('coin',old,now=1005,poll_error='업비트 오류')
        self.assertFalse(failure['stale'])
        self.assertIn('업비트 오류',failure['status'])

    def test_score_band_cohort_moving_55_to_20(self):
        with tempfile.TemporaryDirectory() as d:
            db=m.ScoreLedger(Path(d)/'market.sqlite3')
            a={'stock/at/5m/1':-4.0,'stock/ma/30m/2':3.0}
            db.record_score('stock',55,100000,a,{"source":"TEST"})
            db.observe_positions('stock', {'stock/at/5m/1':2.0,'stock/ma/30m/2':5.0},100060)
            snap=db.snapshot('stock')
            self.assertEqual(snap['active_band'],'40~59')
            self.assertAlmostEqual(snap['active_avg_change_pp'],4.0)
            db.record_score('stock',20,100120,{'stock/at/5m/1':2.0}, {"source":"TEST"})
            self.assertEqual(db.snapshot('stock')['active_band'],'20~39')
            self.assertAlmostEqual(db.snapshot('stock')['active_avg_change_pp'],0)
            db.observe_positions('stock', {'stock/at/5m/1':4.0},100180)
            snap=db.snapshot('stock')
            self.assertAlmostEqual(snap['active_avg_change_pp'],2.0)
            self.assertTrue(any(row['band']=='40~59' and row['count']==1
                            for row in snap['bands']))
            db.close()

    def test_same_band_does_not_reset_and_new_purchase_not_backfilled(self):
        with tempfile.TemporaryDirectory() as d:
            db=m.ScoreLedger(Path(d)/'market.sqlite3')
            db.record_score('coin',55,100000,{'coin/general/5m/BTC':-4}, {})
            db.record_score('coin',59,100100,{'coin/general/5m/BTC':0}, {})
            db.observe_positions('coin', {'coin/general/5m/BTC':2,'coin/ma/5m/ETH':30},100120)
            s=db.snapshot('coin')
            self.assertEqual(s['active_band'],'40~59')
            self.assertAlmostEqual(s['active_avg_change_pp'],6.0)
            self.assertEqual(s['active_count'],1)
            db.close()

    def test_market_score_join_rejects_future_and_old(self):
        with tempfile.TemporaryDirectory() as d:
            db=m.ScoreLedger(Path(d)/'market.sqlite3')
            db.record_score('coin',55,200000,{}, {})
            self.assertIsNone(db.score_at('coin',199999))
            self.assertEqual(db.score_at('coin',200001,max_age_seconds=300),55)
            self.assertIsNone(db.score_at('coin',200800,max_age_seconds=300))
            db.close()

    def test_invalid_historical_observation_or_missing_entry_is_excluded(self):
        x={'stock/at/5m/7':{'entry_price':100,'last_price':96,'qty':3},
           'stock/ma/30m/8':{'entry_price':None,'last_price':100,'qty':2},
           'stock/at/5m/9':{'entry_price':100,'last_price':None,'qty':3}}
        self.assertEqual(m.holding_returns(x),{'stock/at/5m/7':-4.0})

if __name__=='__main__':unittest.main()
