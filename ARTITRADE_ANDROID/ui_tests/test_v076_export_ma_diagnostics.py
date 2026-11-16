"""v076 regression: nonempty ZIP, stock/coin error forensic & MA120 tolerance.

All original wallets/broker orders untouched. No live API order requests.
"""
from __future__ import annotations
from decimal import Decimal
import json
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'app/src/main/python'
if str(APP) not in sys.path:sys.path.insert(0,str(APP))
import android_ledger_backup as backup
import android_research_bundle as bundle
import entry_failure_audit_v075 as entry
import execution_evidence_v074 as forensic
import ma_v067_policy as ma


class Ma120ToleranceTests(unittest.TestCase):
    @staticmethod
    def movement(v120):
        out={}
        for n in (5,10,20,60,120):
            out[n]={'value':Decimal('10')+Decimal(str(n))/100,
                    'delta':(Decimal('0.68') if n==5 else
                             Decimal('0.32') if n==10 else
                             Decimal('0.16') if n==20 else
                             Decimal('0.025') if n==60 else
                             Decimal(str(v120)))}
        return out

    def test_ma120_inclusive_through_positive_0050_and_not_beyond(self):
        for val in ('-0.01','0','0.0016666666667','0.0049','0.0050'):
            self.assertTrue(ma.ma120_down_for_entry(self.movement(val)),val)
        for val in ('0.00500000000001','0.0051','0.025'):
            self.assertFalse(ma.ma120_down_for_entry(self.movement(val)),val)

    def test_skr_095955_would_form_candidate_given_verified_trade_and_full_history(self):
        prior=self.movement('-.01916666666666666667')
        prior[60]['delta']=Decimal('-.01666666666666')
        forming=self.movement('0.001666666666666666667')
        def slope_stub(p):
            return prior if len(p)==121 else forming
        with patch.object(ma,'slopes',side_effect=slope_stub):
            result=ma.upward_transition_on_forming([Decimal(12)]*121,
                                                    Decimal('15.5'),Decimal('4.5'))
        self.assertEqual(result['kind'],'REVERSAL_UP')
        self.assertIn('ma_120_delta',result)
        self.assertEqual(result['reason'],'FIRST_FORMING_SHORT4_UP_AT_MINUS_10_TO_2')

    def test_strict_10_to_2_second_deadline_and_existing_protective_exit_unchanged(self):
        with patch.object(ma,'slopes',side_effect=[
                self.movement('-.02'),self.movement('0.001')]):
            self.assertEqual(
                ma.upward_transition_on_forming([Decimal(12)]*121,15.5,1.9)['reason'],
                'NOT_CLOSE_MINUS_TEN_TO_TWO_SECONDS')
        src=(APP/'ma_v067_policy.py').read_text()
        self.assertIn("if m[120]['delta']>0 and all(",src)
        self.assertIn('MAX_UP_FILL_DELAY_SECONDS=15',src)


class BackupAndMaEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='artitrade-v076-')
        self.base=Path(self.tmp.name);self.root=self.base/'phone'
        self.root.mkdir()
        (self.root/'stock').mkdir()
        (self.root/'coin'/'ma_epoch_v062').mkdir(parents=True)
        (self.root/'stock'/'MA_EPOCH_V062'/'STOCK_MA_ENGINE_V1').mkdir(parents=True)
        self.forensic=self.root/'execution_decisions_v074.sqlite3'
        with sqlite3.connect(self.forensic) as c:
            c.execute('CREATE TABLE decisions(code TEXT, reason TEXT)')
            c.execute("INSERT INTO decisions VALUES('KRW-SKR','MA120_THRESHOLD')")
        with sqlite3.connect(self.root/'stock'/'MA_EPOCH_V062'/
                            'STOCK_MA_ENGINE_V1'/'ma_paper.sqlite3') as c:
            c.executescript("""CREATE TABLE research(ts TEXT,tf TEXT,code TEXT,
                              verdict TEXT,detail TEXT);
                              CREATE TABLE candidates(created_at TEXT,tf TEXT,code TEXT,
                              status TEXT,reason TEXT);
                              CREATE TABLE issues(at TEXT,severity TEXT,code TEXT,
                              message TEXT);""")
            c.execute("INSERT INTO research VALUES(?,?,?,?,?)",
                ('2026-11-16T10:09:55+09:00','5m','000660','REJECT','MA120 > .0050'))
            c.execute("INSERT INTO candidates VALUES(?,?,?,?,?)",
                ('2026-11-16T10:09:55+09:00','5m','000660','REJECT','NO_FRESH_KRX_0D'))
        with sqlite3.connect(self.root/'coin'/'ma_epoch_v062'/'upbit_paper.sqlite3') as c:
            c.executescript("""CREATE TABLE issues(at TEXT,severity TEXT,code TEXT,
                              message TEXT);
                              CREATE TABLE candidates(created_at TEXT,tf TEXT,market TEXT,
                              status TEXT,reason TEXT);""")
            c.execute("INSERT INTO issues VALUES(?,?,?,?)",
                ('2026-11-16T09:59:55+09:00','WARN','KRW-SKR','MA ticker did not arrive'))
            c.execute("INSERT INTO candidates VALUES(?,?,?,?,?)",
                ('2026-11-16T09:59:55+09:00','60m','KRW-SKR','EXPIRED','15 second fill window'))
        (self.root/'stock'/'research.json').write_text('{"ok":true}')
    def tearDown(self):self.tmp.cleanup()

    def test_snapshot_contains_complete_nonempty_forsensic_db_and_stage_file(self):
        target=self.base/'export.zip'
        progress=self.base/'progress.json'
        report={'analysis/error_history_visible.json':'{"market":"stock","items":[]}'}
        result=json.loads(backup.make_backup(str(self.root),str(target),
                                              json.dumps(report),
                                              str(progress)))
        self.assertTrue(result['ok'])
        self.assertGreater(result['bytes'],128)
        self.assertTrue(backup.validate_backup(str(target))['ok'])
        safe_reply=json.loads(backup.validate_backup_json(str(target)))
        self.assertTrue(safe_reply['ok'])
        self.assertGreater(safe_reply['files'],0)
        with zipfile.ZipFile(target) as z:
            self.assertIsNone(z.testzip())
            self.assertIn('execution_decisions_v074.sqlite3',z.namelist())
            self.assertIn('analysis/error_history_visible.json',z.namelist())
            self.assertEqual(json.loads(z.read('analysis/error_history_visible.json')),
                             {'market':'stock','items':[]})
            self.assertIn('PK',str(target.read_bytes()[:2]))
        stage=json.loads(progress.read_text())
        self.assertEqual(stage['percent'],92)
        self.assertGreater(stage['updated_ms'],0)

    def test_native_ma_stock_coin_failure_facts_are_visible_even_if_no_pending(self):
        result=entry._load(self.root)
        stock=result['stock'];coin=result['coin']
        self.assertTrue(any('MA120 > .0050' in r['message'] for r in stock))
        self.assertTrue(any('NO_FRESH_KRX_0D' in r['message'] for r in stock))
        self.assertTrue(any('MA ticker did not arrive' in r['message'] for r in coin))
        self.assertTrue(any('15 second fill window' in r['message'] for r in coin))
        self.assertTrue(all(not r['confirmed_order'] for r in stock+coin))
        self.assertTrue(all(r['audit_only'] for r in stock+coin))
        self.assertTrue(all(r['market']=='stock' for r in stock))
        self.assertTrue(all(r['market']=='coin' for r in coin))

    def test_root_forensic_history_in_both_stock_and_coin_research_exports(self):
        for market in ('stock','coin'):
            folder=self.base/('parts-'+market)
            folder.mkdir()
            progress=self.base/('parts-'+market+'.json')
            result=json.loads(bundle.make_bundle(str(self.root),str(folder),
                                                  market,40_000,str(progress)))
            self.assertTrue(result['ok'])
            self.assertGreaterEqual(result['parts'],1)
            manifest=None
            sources=[]
            for f in result['files']:
                file=folder/f['name']
                self.assertGreater(file.stat().st_size,0)
                with zipfile.ZipFile(file) as z:
                    self.assertIsNone(z.testzip())
                    manifest=json.loads(z.read('analysis/bundle_index.json'))
                    sources=list(manifest['sources'])
            self.assertIn('execution_decisions_v074.sqlite3',sources)
            self.assertEqual(json.loads(progress.read_text())['percent'],92)

    def test_real_forensic_ma_and_trend_rows_query_independently(self):
        with tempfile.TemporaryDirectory() as t:
            led=forensic.ForensicLedger(t)
            for idx in range(100):
                led.write({'market':'coin','code':'KRW-SKR','frame':'60m',
                    'bar_end':f'2026-11-16T{idx:03d}:00:00+09:00',
                    'reason':'NO_MA_CONDITION','strategy':'ma','rsi20_live':None,
                    'audit_epoch':float(idx),'ma_details':{'ma120_delta':'0.006'}})
            for idx in range(100):
                led.write({'market':'stock','code':'000660','frame':'5m',
                    'bar_end':f'2026-11-16T{idx:03d}:00:00+09:00',
                    'reason':'RSI30_OBSERVED_NOT_AN_ORDER','strategy':'trend',
                    'rsi20_live':29.5,'audit_epoch':float(idx)})
            self.assertEqual(len(led.recent_for_strategy('ma',200)),100)
            self.assertEqual(len(led.recent_for_strategy('trend',200)),100)
            self.assertIn('ma120_delta',
                led.recent_for_strategy('ma',1)[0]['payload'])

    def test_android_prevalidated_zip_before_file_picker_and_progress_bars(self):
        src=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertIn('beginPreparedLedgerExport(REQUEST_EXPORT_ERRORS',src)
        self.assertIn('beginPreparedLedgerExport(REQUEST_EXPORT_RESEARCH',src)
        self.assertIn('make_backup",originalRoot.getAbsolutePath()',src)
        self.assertIn('validate_backup',src)
        self.assertIn('finishPreparedLedgerExport(requestCode,data.getData())',src)
        self.assertIn('removeFailedSafDocument(uri)',src)
        self.assertIn('openOutputStream(uri,"wt")',src)
        self.assertIn('drawerErrorExportProgress=addProgressBlock',src)
        self.assertIn('drawerResearchExportProgress=addProgressBlock',src)
        self.assertLess(src.index('TextView exportTitle=text("내보내기 진행 상황"'),
                        src.index('TextView apiTitle=text('))
        self.assertIn('.callAttr("validate_backup_json",archive.getAbsolutePath())',src)
        self.assertIn('private void renderMaEntryObservations()',src)
        self.assertIn('ma_watch_v076',src)

if __name__=='__main__':unittest.main(verbosity=2)
