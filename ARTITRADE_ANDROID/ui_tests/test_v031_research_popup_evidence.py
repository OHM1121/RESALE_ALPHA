"""Research score popup: original fill prices/times versus observed-only highs."""
import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT=Path(__file__).parents[1]/'app/src/main/python'
sys.path.insert(0,str(ROOT))
from android_sqlite_reader import db_rows
src=ROOT/'android_runtime_v078.py'
tree=ast.parse(src.read_text(encoding='utf-8'))
names={'_research_scope','_research_bands','_research_record',
       '_stock_at_research','_stock_ma_research','_coin_research','_close_research',
       '_research_first_target_fills','_gmo_fill_evidence'}
selected=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
ns={'_core':SimpleNamespace(_db_rows=db_rows),'Path':Path}
exec(compile(ast.Module(body=selected,type_ignores=[]),str(src),'exec'),ns)


class SourceEvidenceForScorePopup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
    def tearDown(self):
        self.temp.cleanup()
    def open(self,path):
        f=self.root/path
        f.parent.mkdir(parents=True,exist_ok=True)
        return sqlite3.connect(f)

    def test_at_actual_entry_and_first_target_fill_are_not_reference_price(self):
        with self.open('stock/ARTITRADE_D.sqlite3') as c:
            c.executescript("""
                CREATE TABLE candidate_snapshots(signal_id INT,tf TEXT,code TEXT,name TEXT,
                  signal_ts TEXT,ref_ts TEXT,diverse_11_score REAL,limit_price INT,first_target INT);
                CREATE TABLE positions(id INT,tf TEXT,code TEXT,ref_ts TEXT,
                  status TEXT,peak INT,entry_ts TEXT,entry_price INT,first_sold INT);
                CREATE TABLE executions(id INT,position_id INT,side TEXT,ts TEXT,price INT,reason TEXT);
            """)
            c.execute("INSERT INTO candidate_snapshots VALUES(1,'5m','000111','EXE AT',"
                      "'20261114090000','20261114085900',55,1000,1110)")
            c.execute("INSERT INTO positions VALUES(7,'5m','000111','20261114085900',"
                      "'OPEN',1120,'20261114090210',1002,1)")
            c.execute("INSERT INTO executions VALUES(8,7,'SELL','20261114101500',1111,'FIRST_TARGET_40PCT')")
        item=ns['_stock_at_research'](self.root)['items'][0]
        self.assertEqual(item['buy_price'],1002)
        self.assertEqual(item['buy_at'],'20261114090210')
        self.assertEqual(item['sell_price'],1111)
        self.assertEqual(item['sell_at'],'20261114101500')
        self.assertEqual(item['first_target'],1110)

    def test_unselected_high_is_only_observation_never_sell(self):
        with self.open('stock/ARTITRADE_D.sqlite3') as c:
            c.executescript("""
                CREATE TABLE candidate_snapshots(signal_id INT,tf TEXT,code TEXT,name TEXT,
                  signal_ts TEXT,ref_ts TEXT,diverse_11_score REAL,limit_price INT,first_target INT);
                CREATE TABLE candidate_forward(signal_id INT,minute_count INT,
                  unobserved_session_minutes INT,last_close INT,min_price INT,max_price INT,
                  first_limit_touch_ts TEXT,first_target_after_touch_ts TEXT,observation_basis TEXT,last_ts TEXT);
            """)
            c.execute("INSERT INTO candidate_snapshots VALUES(1,'5m','000222','VIRTUAL',"
                      "'20261114090000','20261114085900',51,1000,1110)")
            c.execute("INSERT INTO candidate_forward VALUES(1,10,0,1110,940,1150,"
                      "'20261114094500','20261114101000','MARKET_PRICE_ONLY','20261114110000')")
        item=ns['_stock_at_research'](self.root)['items'][0]
        self.assertFalse(item['bought'])
        self.assertFalse(bool(item.get('sell_price')))
        self.assertFalse(bool(item.get('sell_at')))
        self.assertEqual(item['first_observed_target_after_touch'],'20261114101000')

    def test_ma_first_target_price_is_filled_price(self):
        with self.open('stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3') as c:
            c.executescript("""
                CREATE TABLE candidates(id INT,tf TEXT,code TEXT,name TEXT,ref_ts TEXT,
                  created_at TEXT,score REAL,status TEXT,target INT,features TEXT);
                CREATE TABLE positions(id INT,candidate_id INT,tf TEXT,code TEXT,name TEXT,
                  status TEXT,first_sold INT,peak INT,target INT,entry_at TEXT,entry INT);
                CREATE TABLE executions(id INT,position_id INT,side TEXT,ts TEXT,price INT,reason TEXT);
            """)
            c.execute("INSERT INTO candidates VALUES(1,'5m','000555','MA','R','T',55,'BUY',1150,'{}')")
            c.execute("INSERT INTO positions VALUES(7,1,'5m','000555','MA','OPEN',1,1150,1150,'BUY_TS',1020)")
            c.execute("INSERT INTO executions VALUES(1,7,'SELL','SELL_TS',1138,'MA_FIRST_TARGET')")
        item=ns['_stock_ma_research'](self.root)['items'][0]
        self.assertEqual(item['buy_price'],1020)
        self.assertEqual(item['sell_price'],1138)
        self.assertEqual(item['sell_at'],'SELL_TS')

    def test_coin_base_and_ma_do_not_cross_position_identity(self):
        for which,market in (('base','KRW-BTC'),('ma','KRW-ETH')):
            with self.open(f'coin/{which}/upbit_paper.sqlite3') as c:
                c.executescript("""
                    CREATE TABLE candidates(id INT,tf TEXT,market TEXT,name TEXT,ref_at TEXT,
                      created_at TEXT,score REAL,limit_price TEXT,target TEXT,reason TEXT);
                    CREATE TABLE positions(id INT,tf TEXT,market TEXT,ref_at TEXT,status TEXT,
                      first_sold_qty TEXT,entry TEXT,entry_at TEXT);
                    CREATE TABLE executions(id INT,position_id INT,side TEXT,at TEXT,
                      price TEXT,reason TEXT);
                """)
                c.execute("INSERT INTO candidates VALUES(1,'5m',?,?,'R','C',55,'100','110','TEST')",
                          (market,market))
                c.execute("INSERT INTO positions VALUES(8,'5m',?,'R','OPEN','0.25','101','B')",(market,))
                c.execute("INSERT INTO executions VALUES(1,8,'SELL','S','113','FIRST_TARGET')")
            item=ns['_coin_research'](self.root,which)['items'][0]
            self.assertEqual(item['buy_price'],'101')
            self.assertEqual(item['sell_price'],'113')
            self.assertEqual(item['buy_at'],'B')
            self.assertEqual(item['sell_at'],'S')

    def test_close_requires_original_executions_for_sell_price(self):
        with self.open('stock/close/AT_CLOSE_PAPER.sqlite3') as c:
            c.executescript("""
                CREATE TABLE signals(id INT,ts TEXT,strategy TEXT,code TEXT,name TEXT,
                  decision TEXT,entry_result TEXT,feature_json TEXT,reason_json TEXT);
                CREATE TABLE positions(id INT,signal_id INT,entry_price INT,entry_ts TEXT,
                  status TEXT,first_sold INT);
                CREATE TABLE executions(id INT,position_id INT,side TEXT,ts TEXT,price INT,reason TEXT);
            """)
            c.execute("INSERT INTO signals VALUES(1,'SIGNAL','CLOSE','000999','CW','BUY','OK','{}','{}')")
            c.execute("INSERT INTO positions VALUES(9,1,970,'BUY_TS','OPEN',1)")
            c.execute("INSERT INTO executions VALUES(3,9,'SELL','TARGET_TS',1020,'FIRST_TARGET')")
        item=ns['_close_research'](self.root)['items'][0]
        self.assertEqual(item['buy_price'],970)
        self.assertEqual(item['sell_price'],1020)
        self.assertEqual(item['sell_at'],'TARGET_TS')

    def test_gmo_target_price_must_come_from_attributed_paper_fill(self):
        rows={'paper_bought':True,'paper_first_target_filled':True,'paper_buy_price':950}
        candidate={'code':'001000','entry_date':'2026-11-16'}
        buys=[{'code':'001000','time':'2026-11-16 09:01:02','price':950,'qty':5},
              {'code':'001000','time':'2026-11-18 09:01:02','price':990,'qty':5}]
        sells=[{'code':'001000','time':'2026-11-16 14:00:00','price':1005,'qty':2,'purpose':'TP_FIRST'},
               {'code':'001000','time':'2026-11-18 14:00:00','price':1060,'qty':2,'purpose':'TP_FIRST'}]
        out=ns['_gmo_fill_evidence'](candidate,rows,buys,sells)
        self.assertEqual(out['buy_at'],'2026-11-16 09:01:02')
        self.assertEqual(out['sell_price'],1005)
        self.assertEqual(out['sell_at'],'2026-11-16 14:00:00')
        out2=ns['_gmo_fill_evidence'](candidate,dict(rows,paper_first_target_filled=False),buys,sells)
        self.assertFalse(bool(out2.get('sell_price')))

if __name__=='__main__':unittest.main()
