"""v086: verified quote, original-wallet staged exits and read-only ZIP.

Only temporary SQLite and synthetic deterministic market fixtures. No broker
orders, user database changes, invented account fill or real-time replay.
"""
from __future__ import annotations
import datetime as dt
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_v091_ma_three_stage_real_ledger import NativeFillThreeStage
import stock_quote_truth_v066 as quotes
from android_ledger_backup import _db_snapshot
from android_research_bundle import make_bundle

BASE=Path(__file__).resolve().parents[1]/'app/src/main'
JAVA=(BASE/'java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text(encoding='utf8')
PY=(BASE/'python/android_runtime_v078.py').read_text(encoding='utf8')


class CurrentPriceEvidence(unittest.TestCase):
    def test_live_0b_quote_propagates_without_sqlite_dashboard(self):
        class Runner:pass
        r=Runner();r._v066_quote_lock=threading.RLock()
        r._v073_current_pins={'005930'};r._v072_rest_quotes={}
        r._confirmed_ws_session=True
        now=dt.datetime.now(quotes.KST)
        stamp=now.isoformat(timespec='seconds')
        r._v066_quote_by_venue={('005930','KRX'):{
            'price':6960,'received_at':stamp,'exchange_at':stamp,
            'received_ms':int(now.timestamp()*1000),
            'exchange_ms':int(now.timestamp()*1000),
            'expires_ms':int((now.timestamp()+10)*1000),
            'venue':'KRX'}}
        got=quotes.live_quote_snapshot(r)
        self.assertEqual(got['quotes']['005930']['price'],6960)
        self.assertEqual(got['quotes']['005930']['status'],'FRESH')
        self.assertEqual(got['quotes']['005930']['source'],'KIWOOM_REAL_0B')
    def test_rest_never_misrepresented_as_live_0b(self):
        class Runner:pass
        r=Runner();r._v066_quote_lock=threading.RLock()
        r._v073_current_pins={'005930'};r._v066_quote_by_venue={}
        r._confirmed_ws_session=False
        r._v072_rest_quotes={'005930':{'price':6960,
            'received_at':dt.datetime.now(quotes.KST).isoformat(timespec='seconds')}}
        got=quotes.live_quote_snapshot(r)
        self.assertEqual(got['quotes']['005930']['status'],'REST_FRESH')
    def test_ui_uses_lightweight_heartbeat_and_price_revision(self):
        self.assertIn('snapshot[\'stock_quote_live_v086\']',PY)
        self.assertIn('overlayLiveStockQuotes(snapshot,st.positions)',JAVA)
        self.assertIn('boolean quoteChanged=!liveQuoteRevision(packet)',JAVA)
        self.assertIn('?"저장 평가가":"현재가";',JAVA)


class OriginalWalletStageCatchup(unittest.TestCase):
    def fixture(self):
        o=NativeFillThreeStage('test_no_first_profit_without_actual_bid_at_target_or_orderbook_depth')
        o.setUp()
        self.addCleanup(o.tearDown)
        return o
    def test_stock_first_and_second_on_one_verified_bid(self):
        t=self.fixture()
        owner=t.seed_stock('REVERSAL_UP')
        t.stock_tick(owner,105,1)
        p,executions,state=t.stock_rows()
        self.assertEqual([x['qty'] for x in executions],[10,45])
        self.assertEqual(state['first_sold'],'10')
        self.assertEqual(state['second_sold'],'45')
        self.assertEqual(p[0]['qty'],45)
        t.stock_tick(owner,105,1)
        self.assertEqual(len(t.stock_rows()[1]),2,'same fresh book cannot duplicate confirmed stage')
    def test_coin_first_and_second_on_one_verified_book(self):
        t=self.fixture()
        owner=t.seed_coin('REVERSAL_UP')
        t.coin_tick(owner,105,1)
        p,executions,state=t.coin_rows()
        self.assertEqual([float(x['qty']) for x in executions],[10.0,45.0])
        self.assertEqual(float(state['first_sold']),10)
        self.assertEqual(float(state['second_sold']),45)
        self.assertEqual(float(p[0]['qty']),45)
        t.coin_tick(owner,105,1)
        self.assertEqual(len(t.coin_rows()[1]),2)
    def test_recovery_only_first_when_second_target_not_reached(self):
        t=self.fixture()
        owner=t.seed_stock('REVERSAL_UP')
        t.stock_tick(owner,102,1)
        self.assertEqual([x['qty'] for x in t.stock_rows()[1]],[10])
    def test_stale_coin_book_does_not_make_profits(self):
        import ma_v067_coin_runtime as coin
        t=self.fixture();owner=t.seed_coin('REVERSAL_UP')
        now=dt.datetime.now(coin.KST)
        bad={'market':'KRW-TEST','bid':'105','ask':'106',
             'bid_size':'1000','ask_size':'1000',
             'timestamp':int((now-dt.timedelta(seconds=80)).timestamp()*1000)}
        coin._process_v067_exits(owner,bad,now)
        self.assertEqual(t.coin_rows()[1],[])


class BoundSnapshot(unittest.TestCase):
    def test_read_only_wal_copy_reports_real_page_count(self):
        with tempfile.TemporaryDirectory() as folder:
            f=Path(folder);orig=f/'upbit_paper.sqlite3';out=f/'stage'/'copy.sqlite3'
            with sqlite3.connect(orig) as c:
                c.execute('PRAGMA journal_mode=WAL')
                c.execute('CREATE TABLE executions(id INTEGER PRIMARY KEY,side TEXT)')
                c.executemany('INSERT INTO executions(side) VALUES(?)',[('SELL',)]*600)
            reports=[]
            counts=_db_snapshot(orig,out,on_progress=lambda d,t,status:reports.append((d,t,status)))
            self.assertEqual(counts['executions'],600)
            self.assertTrue(reports)
            with sqlite3.connect(orig) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM executions').fetchone()[0],600)
            with sqlite3.connect(out) as c:
                self.assertEqual(c.execute('SELECT count(*) FROM executions').fetchone()[0],600)
    def test_cancellation_does_not_delete_source(self):
        with tempfile.TemporaryDirectory() as folder:
            src=Path(folder)/'x.sqlite3';dst=Path(folder)/'copy.sqlite3'
            with sqlite3.connect(src) as c:c.execute('CREATE TABLE x(v)')
            with self.assertRaisesRegex(RuntimeError,'CANCEL_NOW'):
                _db_snapshot(src,dst,check_cancel=lambda:(_ for _ in ()).throw(RuntimeError('CANCEL_NOW')))
            self.assertTrue(src.exists())
    def test_research_and_errors_use_same_cancellable_page_progress(self):
        text=(BASE/'python/android_research_bundle.py').read_text(encoding='utf8')
        self.assertIn('on_progress=report_page',text)
        self.assertIn('SQLite 상태',text)
        self.assertIn('on_progress=None', (BASE/'python/android_ledger_backup.py').read_text(encoding='utf8'))

if __name__=='__main__':unittest.main()
