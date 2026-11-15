"""MT54 real paper fill provenance, independent from RSI35 watch and MT43."""
import datetime as dt
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
from market_trend_wallet_v054 import TrendWalletV054
from market_trend_v043 import KST
from post_exit_sources_v053 import scan
from ai_trend_analysis_v047 import analyze, research_rows

class ResearchV054(TestCase):
    def setUp(self):
        self.t=TemporaryDirectory();self.addCleanup(self.t.cleanup)
        self.root=Path(self.t.name)
        self.runtime=SimpleNamespace(root=self.root,stock_runner=None,coin_runner=None)
        self.runtime.live_enabled=lambda market:False
        self.wallet=TrendWalletV054(self.runtime)
        self.at=dt.datetime(2026,11,15,10,4,48,tzinfo=KST)
    def make_trade(self):
        p,reason=self.wallet.buy('coin','5m','KRW-BTC','BTC',10000,200,self.at,
                       '2026-11-15T10:05:00+09:00',29)
        self.assertEqual(reason,'BOUGHT')
        sold,reason=self.wallet.sell(p,9000,self.at+dt.timedelta(seconds=5),
                       'MT_STOP_9PCT',bid_size=1000)
        self.assertEqual(reason,'SOLD')
        return p
    def test_confirmed_sale_is_one_mt54_episode(self):
        p=self.make_trade()
        cases,problems=scan(self.root,(self.at+dt.timedelta(minutes=5)).timestamp())
        matches=[c for c in cases if c['strategy']=='trend' and c['market']=='coin']
        self.assertEqual(len(matches),1)
        row=matches[0]
        self.assertEqual(row['frame'],'5m')
        self.assertEqual(row['exit_evidence'],'CONFIRMED')
        self.assertEqual(row['execution_mode'],'PAPER')
        self.assertIn('market_trend_wallet_v054.sqlite3',row['source_ref'])
    def test_watch_only_never_enters_followup_or_trade_research(self):
        from market_trend_watch_v054 import WatchBook
        watch=WatchBook()
        watch.observe('coin','KRW-ETH','5m','2026-11-15T10:05:00+09:00',34,False,100)
        watch.expire('coin','5m','2026-11-15T10:05:00+09:00')
        cases,_=scan(self.root,(self.at+dt.timedelta(minutes=5)).timestamp())
        self.assertEqual([c for c in cases if c.get('strategy')=='trend'],[])
        self.assertEqual(analyze(self.root,'coin')['signals'],0)
        self.assertEqual(research_rows(self.root,'coin'),[])
    def test_comprehensive_trend_reads_new_closed_fill_once(self):
        from ai_comprehensive_v052 import analyze_trend
        self.make_trade()
        result=analyze_trend(self.root,'coin',now=(self.at+dt.timedelta(hours=1)).timestamp())
        self.assertTrue(result['ok'])
        self.assertEqual(result['filled_cases'],1)
        self.assertEqual(result['by_execution_mode']['PAPER'],1)
        self.assertEqual(result['closed_with_verified_roi'],1)

    def test_new_filled_trade_is_in_trend_ai_and_research_once(self):
        self.make_trade()
        report=analyze(self.root,'coin')
        self.assertGreaterEqual(report['market_trend_rsi20']['filled'],1)
        self.assertGreaterEqual(report['closed'],1)
        rows=research_rows(self.root,'coin')
        mt54=[x for x in rows if str(x['id']).startswith('MT54:')]
        self.assertEqual(len(mt54),1)
        self.assertEqual(mt54[0]['execution_mode'],'PAPER')

if __name__=='__main__':
    import unittest;unittest.main()
