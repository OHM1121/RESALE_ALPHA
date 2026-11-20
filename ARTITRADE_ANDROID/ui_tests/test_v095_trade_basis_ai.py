"""v095 RED: exact original BUY/SELL evidence becomes Korean reasons and AI factors.

No inferred source/indicator from a later live chart; no fabricated fill.
"""
import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from trade_history_evidence_v057 import lookup_batch
from trade_decision_basis_v095 import summary_for_ai
KST=dt.timezone(dt.timedelta(hours=9))


class VerifiedRationale(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.at=dt.datetime.now(KST).replace(microsecond=0)
        self.timestamp=self.at.timestamp()
        self.path=self.root/'stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3'
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as c:
            c.executescript("""CREATE TABLE positions(id INTEGER PRIMARY KEY,
                tf TEXT,code TEXT,candidate_id INTEGER,entry INTEGER,entry_at TEXT,
                status TEXT);
              CREATE TABLE candidates(id INTEGER PRIMARY KEY,features TEXT);
              CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,
                side TEXT,price INTEGER,qty INTEGER,reason TEXT,ts TEXT);
            """)
            feat={'ma_v067_kind':'REVERSAL_UP','ma_v067_bar_start':'20261117100000',
                  'ma_v067_120_delta':'0.005','ma_v067_no_below120_filter':True}
            c.execute("INSERT INTO candidates VALUES(7,?)",(json.dumps(feat),))
            c.execute("INSERT INTO positions VALUES(11,'5m','005930',7,10000,?,'OPEN')",
                (self.at.isoformat(),))
            c.execute("INSERT INTO executions VALUES(21,11,'BUY',10000,100,'MA_KRX_FRESH_BOOK_PAPER',?)",
                (self.at.isoformat(),))
            c.execute("INSERT INTO executions VALUES(22,11,'SELL',10200,10,'MA_V065_UP_FIRST_10PCT',?)",
                (self.at.isoformat(),))

    def tearDown(self):self.temp.cleanup()

    def request(self,market,strategy,frame,symbol,pid,side,eid):
        return {'key':'row','market':market,'strategy':strategy,'frame':frame,
                'symbol':symbol,'position_id':pid,'side':side,
                'execution_id':eid,'at':self.timestamp}
    def lookup(self,request):
        return json.loads(lookup_batch(str(self.root),json.dumps([request])))['row']

    def test_real_v067_stock_ma_buy_reason_contains_rule_and_original_delta(self):
        x=self.lookup(self.request('stock','ma','5m','005930','11','BUY','21'))
        reason=x['verified_trade_basis']
        self.assertIn('5·10·20·60',reason)
        self.assertIn('120선 아래',reason)
        self.assertIn('처음 양수',reason)
        self.assertIn('0.005',reason)
        self.assertEqual(x['verified_basis_source'],'ORIGINAL_MA_V067_CANDIDATE')
        self.assertEqual(x['verified_numeric_factors']['ma120_delta'],0.005)
        self.assertNotIn('공개호가 기반 하단 지정가',reason)

    def test_ma_first_sell_is_reasoned_by_actual_first_target_and_fraction(self):
        x=self.lookup(self.request('stock','ma','5m','005930','11','SELL','22'))
        self.assertIn('1차 매도',x['verified_trade_basis'])
        self.assertIn('2%',x['verified_trade_basis'])
        self.assertIn('10%',x['verified_trade_basis'])
        self.assertIn('10200',x['verified_trade_basis'].replace(',',''))

    def test_wrong_execution_or_wrong_position_does_not_borrow_neighbor_reason(self):
        x=self.lookup(self.request('stock','ma','5m','005930','12','BUY','21'))
        self.assertNotIn('verified_trade_basis',x)
        x=self.lookup(self.request('stock','ma','5m','005930','11','BUY','99'))
        self.assertNotIn('verified_trade_basis',x)

    def test_trend_buy_wilder_rsi20_and_sell_exit_rules_are_original(self):
        path=self.root/'coin/trend/market_trend_wallet_v054.sqlite3'
        path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as c:
            c.executescript("""CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,
                symbol TEXT,entry_at TEXT,entry REAL,payload TEXT,status TEXT);
              CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,
                side TEXT,price REAL,qty REAL,reason TEXT,at TEXT);
            """)
            payload={'rsi20_live':29.78,'bar_key':'2026-11-17T10:00:00+09:00',
                     'execution_mode':'PAPER','target1':11000,'target2':11500}
            c.execute("INSERT INTO positions VALUES(9,'5m','KRW-BTC',?,?,?,?)",
                (self.at.isoformat(),10000,json.dumps(payload),'TREND_OPEN'))
            c.execute("INSERT INTO executions VALUES(51,9,'BUY',10000,0.2,'MARKET_TREND_RSI20_BUY',?)",
                (self.at.isoformat(),))
            c.execute("INSERT INTO executions VALUES(52,9,'SELL',11000,0.06,'MT_TP1',?)",
                (self.at.isoformat(),))
        buy=self.lookup(self.request('coin','trend','5m','KRW-BTC','9','BUY','51'))
        self.assertIn('Wilder RSI(20)',buy['verified_trade_basis'])
        self.assertIn('29.78',buy['verified_trade_basis'])
        self.assertIn('30 이하',buy['verified_trade_basis'])
        self.assertEqual(buy['verified_numeric_factors']['rsi20_live'],29.78)
        sell=self.lookup(self.request('coin','trend','5m','KRW-BTC','9','SELL','52'))
        self.assertIn('1차 매도',sell['verified_trade_basis'])
        self.assertIn('30%',sell['verified_trade_basis'])

    def test_manual_ai_scopes_include_original_joined_rationale_not_orderbook_slogan(self):
        result=summary_for_ai(self.root,'stock','ma')
        self.assertGreaterEqual(result['verified_fills'],2)
        self.assertTrue(any(x['source']=='ORIGINAL_MA_V067_CANDIDATE'
                            for x in result['explanations']))
        self.assertTrue(any(x['numeric_factors'].get('ma120_delta')==0.005
                            for x in result['explanations']))

if __name__=='__main__':unittest.main()
