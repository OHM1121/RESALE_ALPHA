"""v082: no eager evidence reads; real BUY signal and partial SELL peak linkage."""
import datetime as dt
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import post_exit_sources_v053 as sources
import post_exit_high_v053 as high
import trade_history_evidence_v057 as evidence

KST=dt.timezone(dt.timedelta(hours=9))


class V082LazyExecutionEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.sold=dt.datetime.now(KST).replace(microsecond=0)-dt.timedelta(minutes=3)
        self.bought=self.sold-dt.timedelta(minutes=30)
        self.db=self.root/'coin/ma/upbit_paper.sqlite3'
        self.db.parent.mkdir(parents=True,exist_ok=True)
        self.symbol='KRW-TEST'
        self.ref=self.bought.isoformat()
        with sqlite3.connect(self.db) as c:
            c.executescript("""CREATE TABLE positions (
                id INTEGER PRIMARY KEY,tf TEXT,market TEXT,ref_at TEXT,
                entry_at TEXT,entry TEXT,qty0 TEXT,qty TEXT,status TEXT,target TEXT);
                CREATE TABLE candidates(
                id INTEGER PRIMARY KEY,tf TEXT,market TEXT,ref_at TEXT,
                reason TEXT,features TEXT);
                CREATE TABLE executions(
                id INTEGER PRIMARY KEY,position_id INTEGER,tf TEXT,market TEXT,
                at TEXT,side TEXT,qty TEXT,price TEXT,reason TEXT);""")
            c.execute("INSERT INTO positions VALUES(?,?,?,?,?,?,?,?,?,?)",
                (7,'5m',self.symbol,self.ref,self.bought.isoformat(),
                 '100','100','70','OPEN','115'))
            payload={'ma_v067_kind':'REVERSAL_UP',
                     'ma_v061_kind':'TURNPOINT',
                     'ma_v067_120_delta':'0.0048',
                     'source':'UPBIT_V079_LIVE_10_TO_2_TICK_AND_BOOK',
                     'ma_v067_bar_start':self.ref,
                     'ma_v061_first_target_pct':'3.0'}
            c.execute("INSERT INTO candidates VALUES(?,?,?,?,?,?)",
                (5,'5m',self.symbol,self.ref,'원본 4개 단기 MA 상승전환',
                 json.dumps(payload)))
            c.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?)",
                (21,7,'5m',self.symbol,self.bought.isoformat(),
                 'BUY','100','100','Upbit 공개 호가 기반 하단 지정가 가상체결'))
            c.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?)",
                (22,7,'5m',self.symbol,self.sold.isoformat(),
                 'SELL','30','113','1차 목표가 도달 30%'))
        self.req={'market':'coin','strategy':'ma','frame':'5m',
                  'symbol':self.symbol,'position_id':'7'}
    def tearDown(self):
        self.tmp.cleanup()
    def lookup(self,key,side,at,execution_id):
        row=dict(self.req,key=key,side=side,at=int(at.timestamp()),
                 execution_id=str(execution_id))
        result=json.loads(evidence.lookup_batch(str(self.root),json.dumps([row])))
        return result[key]

    def test_no_synthetic_post_sale_high_on_buy(self):
        buy=self.lookup('buy','BUY',self.bought,21)
        self.assertEqual(buy['entry_signal_state'],'ORIGINAL_CANDIDATE_MATCHED')
        self.assertEqual(buy['entry_signal_kind'],'REVERSAL_UP')
        self.assertEqual(buy['entry_signal_ma120_delta'],'0.0048')
        self.assertEqual(buy['candidate_reason'],'원본 4개 단기 MA 상승전환')
        self.assertNotIn('post_sale_peak',buy)
        self.assertNotIn('post_sale_state',buy)

    def test_open_position_real_partial_sell_starts_independent_peak_case(self):
        manager=high.Tracker(self.root)
        imported=manager.scan_sources(int(self.sold.timestamp())+10)
        self.assertEqual(imported['problems'],[])
        cases=manager.summary('coin','ma')['cases']
        self.assertEqual(len(cases),1,cases)
        self.assertEqual(cases[0]['source_ref'],
            'coin/ma/upbit_paper.sqlite3:partialfill:7:22')
        self.assertEqual(cases[0]['exit_ts'],int(self.sold.timestamp()))
        self.assertIsNone(cases[0]['peak'])
        loading=self.lookup('sell','SELL',self.sold,22)
        self.assertEqual(loading['post_sale_anchor_kind'],'PARTIAL_SELL')
        self.assertEqual(loading['post_sale_samples'],0)
        self.assertNotIn('post_sale_peak',loading)
        real_price_time=self.sold+dt.timedelta(seconds=19)
        manager.record_batch([('coin',self.symbol,117,real_price_time,'UPBIT_PUBLIC_RECEIPT'),
                              ('coin',self.symbol,121,real_price_time+dt.timedelta(seconds=3),'UPBIT_PUBLIC_RECEIPT')])
        filled=self.lookup('sell','SELL',self.sold,22)
        self.assertEqual(filled['post_sale_peak'],121)
        self.assertAlmostEqual(filled['post_sale_peak_pct_from_buy'],21.0)
        self.assertEqual(filled['post_sale_samples'],2)
        self.assertNotIn('post_sale_peak',self.lookup('buy','BUY',self.bought,21))

    def test_final_exit_does_not_steal_earlier_partial_peak(self):
        manager=high.Tracker(self.root)
        manager.scan_sources(int(self.sold.timestamp())+10)
        manager.record_batch([('coin',self.symbol,128,self.sold+dt.timedelta(seconds=12),'UPBIT_PUBLIC_RECEIPT')])
        final=self.sold+dt.timedelta(seconds=50)
        with sqlite3.connect(self.db) as con:
            con.execute("UPDATE positions SET status='CLOSED',qty='0' WHERE id=7")
            con.execute("INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?)",
                (23,7,'5m',self.symbol,final.isoformat(),'SELL','70','122','최종 정리'))
        manager.scan_sources(int(final.timestamp())+5)
        cases=manager.summary('coin','ma')['cases']
        self.assertEqual(len(cases),2,cases)
        self.assertEqual(self.lookup('partial','SELL',self.sold,22)['post_sale_peak'],128)
        last=self.lookup('final','SELL',final,23)
        self.assertEqual(last['post_sale_anchor_kind'],'FINAL_SELL')
        self.assertNotIn('post_sale_peak',last)
        manager.record_batch([('coin',self.symbol,125,final+dt.timedelta(seconds=5),'UPBIT_PUBLIC_RECEIPT')])
        last=self.lookup('final','SELL',final,23)
        self.assertEqual(last['post_sale_peak'],125)

    def test_first_buy_alone_cannot_produce_any_sale_case(self):
        with sqlite3.connect(self.db) as con:
            con.execute("DELETE FROM executions WHERE side='SELL'")
            con.execute("UPDATE positions SET qty='100' WHERE id=7")
        cases,problems=sources.scan(self.root,int(self.sold.timestamp())+50)
        self.assertFalse(problems)
        self.assertEqual([c for c in cases if c['market']=='coin' and c['strategy']=='ma'],[])

    def test_ui_does_not_eager_load_hidden_fill_details(self):
        ui=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertNotIn('queryTradeHistoryEvidence(history.subList(first,last))',ui)
        self.assertIn('expandedTradeEvidenceKeys.contains(detailKey)',ui)
        self.assertIn('queryTradeHistoryEvidence(\n                            java.util.Collections.singletonList(fill))',ui)
        self.assertIn('if(opened)renderExecutionEvidenceStrip(panel,item);',ui)
        self.assertIn('if(!"executions".equals(kind)){',ui)
        self.assertIn('부분매도 후 · ',ui)
        self.assertIn('매도 전 · 해당없음',ui)
        self.assertIn('MA 실제 진입신호',ui)
        self.assertIn('.put("execution_id",row.optString("id",""))',ui)

if __name__=='__main__':
    unittest.main()
