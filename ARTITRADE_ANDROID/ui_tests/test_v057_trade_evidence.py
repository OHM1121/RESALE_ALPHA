"""Real SQLite fixtures prevent per-symbol source/after-exit misattribution."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))

class V057ReadOnlyTradeEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        ai=self.root/'coin/ai/AI_COMPREHENSIVE_V052.sqlite3'
        ai.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(ai) as c:
            c.execute('''CREATE TABLE trade_cases(scope TEXT,case_key TEXT,symbol TEXT,
                frame TEXT,execution_mode TEXT,kind TEXT,entry_ts INTEGER,
                realized_roi REAL,case_json TEXT,updated_at INTEGER)''')
            for ident,entry,roi,n in [('POS-A',1730000100,5.5,4),('POS-B',1730100100,-2.0,7)]:
                payload={'source_id':ident,'features':[{'phase':'entry','key':str(k),'value':1}
                         for k in range(n)],'sources':['ORIGINAL_POSITION_STATE_AS_OBSERVED'],
                         'entry_ts':entry,'roi':roi}
                c.execute('INSERT INTO trade_cases VALUES(?,?,?,?,?,?,?,?,?,?)',
                        ('coin/trend',ident,'KRW-BTC','5m','PAPER','FILLED',entry,roi,
                         json.dumps(payload),1730110000))
        follow=self.root/'POST_EXIT_HIGH_V053.sqlite3'
        with sqlite3.connect(follow) as c:
            c.execute('''CREATE TABLE cases(market TEXT,strategy TEXT,frame TEXT,
                symbol TEXT,source_ref TEXT,exit_evidence TEXT,exit_ts INTEGER,
                peak REAL,peak_pct REAL,sample_count INTEGER,status TEXT,cohort TEXT,
                execution_mode TEXT)''')
            for ident,t,price,pct in [('POS-A',1730002000,55000.,10.),('POS-B',1730102000,45000.,-10.)]:
                c.execute('INSERT INTO cases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('coin','trend','5m','KRW-BTC','coin:position:'+ident+':start',
                     'CONFIRMED',t,price,pct,55,'COMPLETE','ACTUAL_FILLED','PAPER'))

    def tearDown(self):self.tmp.cleanup()
    def lookup(self,requests):
        from trade_history_evidence_v057 import lookup_batch
        return json.loads(lookup_batch(str(self.root),json.dumps(requests)))
    def row(self,ident,ts,side='SELL'):
        return {'key':ident+':'+side,'market':'coin','strategy':'trend',
                'frame':'5m','symbol':'KRW-BTC','position_id':ident,
                'at':ts,'side':side}

    def test_multiple_episodes_never_mix_followup(self):
        got=self.lookup([self.row('POS-A',1730002000),self.row('POS-B',1730102000)])
        self.assertEqual(got['POS-A:SELL']['post_sale_peak'],55000)
        self.assertEqual(got['POS-B:SELL']['post_sale_peak'],45000)
        self.assertEqual(got['POS-A:SELL']['ai_source_count'],1)
        self.assertEqual(got['POS-B:SELL']['ai_factor_count'],7)
        self.assertEqual(got['POS-A:SELL']['ai_realized_roi'],5.5)

    def test_never_attribute_to_unmatched_older_or_future_trade(self):
        got=self.lookup([self.row('POS-A',1730202000),
                         self.row('UNKNOWN',1730002000),
                         self.row('POS-A',1730002000,'BUY')])
        self.assertNotIn('post_sale_peak',got['POS-A:SELL'])
        self.assertNotIn('post_sale_peak',got['UNKNOWN:SELL'])
        self.assertNotIn('post_sale_peak',got['POS-A:BUY'])

    def test_lookup_is_readonly_and_missing_database_is_not_created(self):
        before={str(x):x.read_bytes() for x in self.root.rglob('*.sqlite3')}
        self.lookup([self.row('POS-A',1730002000)])
        self.assertEqual(before,{str(x):x.read_bytes() for x in self.root.rglob('*.sqlite3')})
        with tempfile.TemporaryDirectory() as empty:
            from trade_history_evidence_v057 import lookup_batch
            self.assertEqual(json.loads(lookup_batch(empty,json.dumps([self.row('X',1730002000)]))),{'X:SELL':{}})
            self.assertEqual(list(Path(empty).rglob('*.sqlite3')),[])

    def test_scope_is_strict_and_timestamp_is_never_guessed(self):
        s=self.row('POS-A',1730002000)
        bad=dict(s,strategy='general',key='other')
        future=dict(s,at=None,key='no-clock')
        r=self.lookup([bad,future])
        self.assertEqual(r['other'],{})
        self.assertNotIn('post_sale_peak',r['no-clock'])

if __name__=='__main__':unittest.main()
