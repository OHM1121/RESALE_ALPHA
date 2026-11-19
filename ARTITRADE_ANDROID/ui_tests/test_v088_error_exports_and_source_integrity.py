"""v088 error bundle: original provenance, 500MB cap, three scopes, no error UI."""
import hashlib,json,os,sqlite3,tempfile,unittest,zipfile
from pathlib import Path
import sys
SRC=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SRC/'app/src/main/python'))
from android_research_bundle import make_bundle,SCHEMA

def sha256(data):
    return hashlib.sha256(data).hexdigest()

class ScopedErrorExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='artitrade_v088_')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'source'
        self.root.mkdir()
        self.output=Path(self.temp.name)/'out'
        self.output.mkdir()
        for market in ('coin','stock'):
            path=self.root/market/'base'
            path.mkdir(parents=True)
            db=path/'error_original.sqlite3'
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE issues(id INTEGER PRIMARY KEY,code TEXT,message TEXT)')
                c.execute('INSERT INTO issues(code,message) VALUES(?,?)',
                          ('MA_BOOK_PROCESS_FAIL' if market=='coin' else 'KIWOOM_TIMEOUT',
                           "original source must be preserved"))
            (path/'runtime_errors.jsonl').write_text(
                json.dumps({'type':'test','market':market})+'\n',encoding='utf8')
        with sqlite3.connect(self.root/'execution_decisions_v074.sqlite3') as c:
            c.execute('CREATE TABLE decisions (id INTEGER PRIMARY KEY, market TEXT)')
            c.execute("INSERT INTO decisions(market) VALUES('coin')")
            c.execute("INSERT INTO decisions(market) VALUES('stock')")

    def _execute(self,scope,limit=500_000_000):
        d=self.output/scope
        d.mkdir()
        native={'schema':'ARTITRADE_ERROR_HISTORY_V088',
                'runtime_faults':[{'stage':'SYSTEM_PROCESS_EXIT_ANR','message':'test'}],
                'last_memory_sample':'{"java_heap_used_mb":55}'}
        response=json.loads(make_bundle(str(self.root),str(d),scope,limit,None,
                                        'errors',json.dumps(native)))
        return d,response,native

    def test_stock_coin_and_unified_are_scoped_and_native_crash_included(self):
        for scope in ('stock','coin','all'):
            with self.subTest(scope=scope):
                folder,resp,native=self._execute(scope)
                self.assertTrue(resp['ok'])
                self.assertEqual(resp['bundle_kind'],'errors')
                self.assertGreater(resp['parts'],0)
                name=resp['files'][0]['name']
                self.assertIn('ARTITRADE_'+scope.upper()+'_ERROR_HISTORY_',name)
                with zipfile.ZipFile(folder/name) as z:
                    self.assertIsNone(z.testzip())
                    index=json.loads(z.read('analysis/bundle_index.json'))
                    self.assertEqual(index['bundle_kind'],'errors')
                    self.assertEqual(index['schema'],SCHEMA)
                    keys=index['sources']
                    self.assertEqual(any(k.startswith('stock/') for k in keys),
                                     scope in ('stock','all'))
                    self.assertEqual(any(k.startswith('coin/') for k in keys),
                                     scope in ('coin','all'))
                    self.assertEqual(json.loads(z.read('analysis/error_history_summary.json')),native)
                    self.assertIn('execution_decisions_v074.sqlite3',keys)
                for part in resp['files']:
                    p=folder/part['name']
                    self.assertTrue(p.is_file())
                    self.assertLessEqual(p.stat().st_size,500_000_000)
                    self.assertEqual(p.stat().st_size,part['bytes'])
                    self.assertEqual(sha256(p.read_bytes()),part['sha256'])

    def test_independently_valid_zip_parts_reconstitute_full_lossless_file(self):
        # A source larger than one part forces a real fragment reassembly.
        source=self.root/'coin/base/runtime_errors.jsonl'
        rng=os.urandom(300_000)
        source.write_bytes(rng)
        folder,resp,_=self._execute('coin',max_part_bytes:=160_000)
        self.assertGreaterEqual(resp['parts'],2)
        restored={}
        first=folder/resp['files'][0]['name']
        with zipfile.ZipFile(first) as z:
            index=json.loads(z.read('analysis/bundle_index.json'))
        for rel,meta in index['sources'].items():
            segments=sorted(meta['fragments'],key=lambda x:x['offset'])
            result=bytearray()
            for piece in segments:
                with zipfile.ZipFile(folder/resp['files'][piece['part']-1]['name']) as z:
                    self.assertIsNone(z.testzip())
                    block=z.read(piece['entry'])
                    self.assertEqual(len(block),piece['size'])
                    result+=block
            self.assertEqual(len(result),meta['size'])
            self.assertEqual(sha256(result),meta['sha256'])
            restored[rel]=bytes(result)
        self.assertEqual(restored['coin/base/runtime_errors.jsonl'],rng)
        for item in resp['files']:
            self.assertLessEqual(item['bytes'],max_part_bytes)

    def test_ma_snt_first_profit_and_missing_high_fail_closed_without_relaxing_policy(self):
        from datetime import datetime,timezone,timedelta
        from decimal import Decimal
        import ast
        import ma_v067_policy as policy
        now=datetime(2026,11,17,9,6,tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(policy.up_targets(Decimal('8.18'),'5m')[0],Decimal('8.3436'))
        state=policy.initial_up_exit_state('5m')
        _,wanted,reason=policy.up_exit(state,Decimal('8.40'),Decimal('8.18'),
                                       Decimal('200'),Decimal('200'),now)
        self.assertEqual(wanted,Decimal('20.00'))
        self.assertEqual(reason,'MA_V065_UP_FIRST_10PCT')
        source=(SRC/'app/src/main/python/ma_v067_coin_runtime.py').read_text()
        self.assertIn("str(reason).startswith(('MA_V065_','MA_V067_'))",source)
        self.assertIn("MA_BOOK_KEYERROR_SOURCE_V088",source)
        self.assertIn("if elapsed-last.get(sig,0)>180",source)
        tree=ast.parse(source)
        install=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='install')
        guard=next(n for n in install.body if isinstance(n,ast.FunctionDef) and
                   n.name=='verified_or_fail_closed_range')
        def native_verified(f):
            if f.get('ma_v061_kind'):return True
            return Decimal(str(f['high']))>=Decimal(str(f['low']))
        context={'previous_range':native_verified}
        exec(compile(ast.Module(body=[guard],type_ignores=[]),'MA_SAFE_RANGE','exec'),
             context)
        checked=context['verified_or_fail_closed_range']
        self.assertFalse(checked({}))
        self.assertFalse(checked({'low':'8.1'}))
        self.assertFalse(checked({'high':'8.4'}))
        self.assertTrue(checked({'high':'8.4','low':'8.1'}))
        self.assertTrue(checked({'ma_v061_kind':'TURNPOINT'}))
        # No buy/sell policy threshold changed; invalid previous source does not
        # throw KeyError and block the entire native MA orderbook dispatch.

    def test_ui_hides_error_table_but_retains_extract_buttons(self):
        ui=(SRC/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertNotIn('TextView erRow=text("오류 이력',ui)
        self.assertIn('else if("errors".equals(page))renderHome()',ui)
        self.assertIn('코인 오류 추출',ui)
        self.assertIn('주식 오류 추출',ui)
        self.assertIn('통합 오류 추출',ui)
        self.assertIn('requestScopedErrorExport(selected)',ui)
        self.assertIn('exportScopedErrorToFolder(data.getData(),scope)',ui)
        self.assertIn('REQUEST_EXPORT_SCOPED_ERRORS',ui)
        self.assertIn('"errors",\n                        nativeReport.toString()',ui)
        self.assertIn('source.length()>500_000_000L',ui)
        self.assertIn('SHA-256',ui)
        self.assertIn('SYSTEM_PROCESS_EXIT_',(
            SRC/'app/src/main/java/com/artitrade/mobile/ProcessHealthV087.java').read_text())

if __name__=='__main__':
    unittest.main(verbosity=2)
