"""v058 PAPER/read-only regression for RSI20, market-depth gates and AI evidence."""
import datetime as dt
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import market_trend_v043 as mt
from market_trend_runtime_v043 import Coordinator
import ai_live_book_v058 as book
import ai_approved_metrics_v048 as metrics
import ai_approved_collect_v048 as approved
import ai_pattern_diagnosis_v042 as patterns
from ai_trend_analysis_v047 import _quantified_diagnosis


class Stub:
    def __init__(self,root):
        self.root=Path(root)
        self.coin_runner=None
        self.stock_runner=None
    def live_enabled(self,*args):return False
    def halt_market(self,*args):raise AssertionError("PAPER should never send LIVE")


class V058Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=self.tmp.name

    def test_rsi_main_line_is_not_nine_period_signal(self):
        prices=[100+9*math.sin(n*.17)-.2*n for n in range(210)]
        value=mt.rsi20(prices[:200],prices[200])
        self.assertTrue(0<value<100)
        self.assertEqual(mt.RSI_PERIOD,20)
        self.assertNotIn('rsi9',mt.rsi20.__code__.co_names)

    def test_quote_requires_real_fresh_pre_entry_timestamp(self):
        entry=1794700800
        self.assertEqual(book.persist(self.root,'coin',[
            ('KRW-BTC',entry-1,99,100),('KRW-ETH',entry+5,99,100),
            ('KRW-ADA',entry-25,99,100)],entry*1000),1)
        with sqlite3.connect(book.research.db_path(self.root,'coin')) as c:
            obs=book.lookup(c,'KRW-BTC',entry)
            self.assertEqual(obs['bid'],99)
            self.assertIsNone(book.lookup(c,'KRW-ETH',entry))
            self.assertIsNone(book.lookup(c,'KRW-ADA',entry))
            self.assertAlmostEqual(metrics.entry_spread({'entry_quote':obs},
                                                          entry)['value'],
                                   10000/99.5,places=5)

    def test_no_quote_is_not_fabricated(self):
        book.persist(self.root,'coin',[('KRW-ETH',1794700799,99,100)],1794700800000)
        with sqlite3.connect(book.research.db_path(self.root,'coin')) as c:
            self.assertIsNone(book.lookup(c,'KRW-UNKNOWN',1794700800))

    def test_gap_is_true_open_over_previous_close(self):
        start=1794700800
        bars=[(start,105,95,100,1,100,99),
              (start+300,120,90,110,1,110,105)]
        value=metrics.entry_open_gap(bars,start+301,'5m')
        self.assertAlmostEqual(value['value'],5.0)
        self.assertIsNone(metrics.entry_open_gap(
            [(x[0],*x[1:6],None) for x in bars],
            start+301,'5m')['value'])

    def test_no_bid_size_does_not_stop_buy_quote(self):
        class Runner:
            def __init__(self):
                import threading
                self._lock=threading.RLock()
                self.last_book={'KRW-BTC':{'timestamp':1794700800000,
                       'bid':100,'ask':101,'ask_size':300,'bid_size':0}}
        stub=Stub(self.root);stub.coin_runner=Runner()
        co=Coordinator(stub)
        import unittest.mock as mock
        with mock.patch('market_trend_runtime_v043.time.time',
                        return_value=1794700800),mock.patch(
                        'market_trend_runtime_v043.time.monotonic',return_value=3):
            co._quotes['coin']['KRW-BTC']=(101,3)
            result=co._quote('coin','KRW-BTC')
        self.assertIsNotNone(result)
        self.assertEqual(result['bid_size'],0)

    def test_ai_rate_all_closed_denominator(self):
        sample={'code':'market_weak','title':'시장 약세',
                'n':3,'cohort_n':5}
        observed=[]
        for i in range(10):
            observed.append({'outcome':-1 if i<5 else 1,
                             'precursors':{'market_weak'} if i<3 else set(),
                             'features':({'market_score':-32} if i<3 else {},{},{} )})
        statement=patterns._finding_sentence(sample,observed,True)
        self.assertIn('30.0%',statement)
        self.assertIn('≤ -20',statement)
        self.assertNotIn('3/10',statement)

    def test_trend_analysis_does_not_invent_outcome_when_no_fills(self):
        report=_quantified_diagnosis(self.root,'coin','5m',{})
        self.assertIn('축적 중',report['cause'])
        self.assertEqual(report['sample_n'],0)

    def test_existing_original_signing_and_package_preserved_in_build_file(self):
        src=(ROOT/'app/build.gradle').read_text('utf-8')
        self.assertIn('com.artitrade.unified.s21dex',src)
        self.assertRegex(src,r'versionCode\\s+65')

if __name__=='__main__':
    unittest.main()
