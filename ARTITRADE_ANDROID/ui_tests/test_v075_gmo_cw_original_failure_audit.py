"""v075: no strategy-policy edit, only read-only buyer rejection visibility.

Synthetic historical prices are reproductions of reported date/price gates,
not broker fills. Never calls Kiwoom/Upbit or creates any order.
"""
from __future__ import annotations
import datetime as dt
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
APP=ROOT/'app/src/main/python'
if str(APP) not in sys.path:sys.path.insert(0,str(APP))
import entry_failure_audit_v075 as audit


def db_at(path,script,rows=()):
    path.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(path) as c:
        c.executescript(script)
        for sql,args in rows:c.execute(sql,args)


class SourceEvidenceTests(unittest.TestCase):
    def make(self,root):
        stock=Path(root)/'stock'
        db_at(stock/'PROJECTGMO_V1.0_research.sqlite3',"""
          CREATE TABLE gmo_scan(trade_date TEXT,code TEXT,status TEXT,detail TEXT,checked_at TEXT);
          CREATE TABLE gmo_issues(at TEXT,category TEXT,code TEXT,detail TEXT);
          CREATE TABLE gmo_candidates(code TEXT,name TEXT,event_date TEXT,entry_date TEXT,status TEXT);
        """,[
          ("INSERT INTO gmo_scan VALUES(?,?,?,?,?)",('20261113','180640','COMPLETE',
                      'D-day closing price must decline','2026-11-13T20:22:00+09:00')),
          ("INSERT INTO gmo_scan VALUES(?,?,?,?,?)",('20261116','003230','COMPLETE',
                      'D-day closing price must decline','2026-11-16T20:22:00+09:00')),
          ("INSERT INTO gmo_scan VALUES(?,?,?,?,?)",('20261113','005930','COMPLETE',
                      'SIGNAL_ACCEPTED','2026-11-13T20:22:00+09:00')),
          ("INSERT INTO gmo_issues VALUES(?,?,?,?)",('2026-11-16T09:00:00+09:00',
                      'ENTRY_BLOCKED_NO_LIVE_ASK','180640','KRX ask missing')),
          ("INSERT INTO gmo_candidates VALUES(?,?,?,?,?)",('000001','sample',
                      '2026-11-12','2026-11-13','미체결/후보'))
        ])
        (stock/'PROJECTGMO_V1.0_account_state.json').write_text(json.dumps({
          'audit':[{'time':'2026-11-16T09:00:01+09:00','code':'180640',
                    'kind':'ENTRY_SKIPPED','detail':'4 positions including pending buys limit reached'},
                   {'time':'2026-11-16T09:00:02+09:00','code':'180640',
                    'kind':'ORDER_SUBMITTED','detail':'not a missing order'}],
          'cash':2000000,'positions':{'180640':{'qty':3}},'buy_fills':[]
        }),encoding='utf8')
        db_at(stock/'close'/'AT_CLOSE_PAPER.sqlite3',"""
          CREATE TABLE research_scan_skips(id INTEGER PRIMARY KEY,ts TEXT,code TEXT,name TEXT,stage TEXT,reason TEXT,detail_json TEXT);
          CREATE TABLE research_candidates(id INTEGER PRIMARY KEY,ts TEXT,strategy TEXT,code TEXT,name TEXT,decision TEXT,reason_json TEXT);
          CREATE TABLE signals(id INTEGER PRIMARY KEY,ts TEXT,strategy TEXT,code TEXT,name TEXT,decision TEXT,entry_result TEXT);
          CREATE TABLE research_events(id INTEGER PRIMARY KEY,ts TEXT,level TEXT,event TEXT,detail_json TEXT);
        """,[
          ("INSERT INTO research_scan_skips VALUES(?,?,?,?,?,?,?)",
           (1,'2026-11-16T15:15:00+09:00','180640','한진칼','LIVE','NO_LIVE_TRADE','{}')),
          ("INSERT INTO research_candidates VALUES(?,?,?,?,?,?,?)",
           (1,'2026-11-16T15:15:02+09:00','CLOSE','003230','삼양식품',
            'REJECT','["LOWER_WICK_TOO_SMALL","NO_20DMA_UNDERSHOOT"]')),
          ("INSERT INTO signals VALUES(?,?,?,?,?,?,?)",
           (1,'2026-11-16T15:18:59+09:00','CLOSE','000660','하이닉스',
            'BUY','STALE_BOOK_AT_COMMIT')),
          ("INSERT INTO research_events VALUES(?,?,?,?,?)",
           (1,'2026-11-16T15:19:00+09:00','WARN','CLOSE_ENTRY_DEFERRED',
            '{"code":"000660","reason":"STALE_BOOK_AT_COMMIT"}')),
        ])
        return stock

    def test_existing_rules_rejected_and_unfilled_causes_appear_without_new_orders(self):
        with tempfile.TemporaryDirectory() as d:
            stock=self.make(d)
            original=(stock/'PROJECTGMO_V1.0_account_state.json').read_bytes()
            result=audit._load(d)
            rows=result['stock']
            self.assertGreaterEqual(len(rows),7)
            self.assertTrue(all(x['audit_only'] for x in rows))
            self.assertTrue(all(not x['confirmed_order'] for x in rows))
            self.assertTrue(any(x['code']=='180640' and
                x['message']=='D-day closing price must decline' for x in rows))
            self.assertTrue(any(x['code']=='003230' and
                x['status']=='RULE_NOT_MET' for x in rows))
            self.assertTrue(any(x['code']=='000660' and
                x['message']=='STALE_BOOK_AT_COMMIT' for x in rows))
            self.assertTrue(any('LOWER_WICK_TOO_SMALL' in x['message'] for x in rows))
            self.assertFalse(any(x['code']=='005930' and x['stage'].startswith('GMO_D_DAY_SCAN')
                    for x in rows))
            self.assertEqual((stock/'PROJECTGMO_V1.0_account_state.json').read_bytes(),original)
            with sqlite3.connect(stock/'PROJECTGMO_V1.0_research.sqlite3') as c:
                self.assertEqual(c.execute('SELECT COUNT(*) FROM gmo_issues').fetchone()[0],1)

    def test_async_snapshot_returns_without_waiting_for_sqlite_and_keeps_cache(self):
        with tempfile.TemporaryDirectory() as d:
            self.make(d)
            original=audit._load
            def slow(root):
                time.sleep(.17)
                return original(root)
            with patch.object(audit,'_load',side_effect=slow):
                began=time.monotonic()
                first=audit.snapshot_for_root(d)
                self.assertLess(time.monotonic()-began,.12)
                self.assertTrue(first.get('initializing'))
                deadline=time.monotonic()+2
                latest=first
                while time.monotonic()<deadline and not latest.get('stock'):
                    time.sleep(.025);latest=audit.snapshot_for_root(d)
                self.assertTrue(latest['stock'])
                self.assertEqual(latest['schema'],'ARTITRADE_ENTRY_FAILURE_AUDIT_V075')

    def test_stock_ui_error_and_original_research_zip_paths_stay_connected(self):
        java=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        bridge=(APP/'android_runtime_v078.py').read_text()
        self.assertIn('entry_failure_audit',java)
        self.assertIn('entry_failure_audit',bridge)
        self.assertIn('requestErrorHistoryExport',java)
        self.assertIn('research_scan_skips',audit.__doc__ or '' if False else audit._cw.__code__.co_consts.__str__())

    def test_selection_requires_falling_close_even_with_three_party_net_flow(self):
        from projectgmo.selector import DSignal,select
        samples=[
            ('180640','2026-11-13','2026-11-16',203000,203500,-220528,207570,12965),
            ('180640','2026-11-16','2026-11-17',203500,215500,-89592,37130,54221),
            ('003230','2026-11-16','2026-11-17',1210000,1285000,-21369,12629,10986),
        ]
        for code,event,entry,prev,now,p,f,i in samples:
            signal=DSignal(code,code,event,entry,prev,now,
                prev,prev,p,f,i,[-100 for _ in range(20)],
                'SHORT','KOSPI','PUBLIC_DAILY_PRICE+USER_SCREENSHOT')
            verdict=select(signal)
            self.assertFalse(verdict.accepted)
            self.assertEqual(verdict.reason,'D-day closing price must decline')

    def test_selector_source_comment_and_active_price_growth_veto_disagree(self):
        from projectgmo.selector import (DSignal,select,
            FIVE_DAY_CHANGE_MAX_PCT,TWENTY_DAY_CHANGE_MAX_PCT)
        # Investigative regression only: preserve live rules unchanged.
        self.assertIsNone(FIVE_DAY_CHANGE_MAX_PCT)
        self.assertIsNone(TWENTY_DAY_CHANGE_MAX_PCT)
        signal=DSignal('180640','한진칼','2026-11-13','2026-11-16',
            100,99,85,95,-10000,5000,5000,
            [-1000]*20,'SHORT','KOSPI','SYNTHETIC_TEST')
        result=select(signal)
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason,'previous price growth veto')

    def test_cw_scan_time_budget_is_shorter_than_its_whole_universe(self):
        import inspect
        import at_close_runner
        body=inspect.getsource(at_close_runner)
        self.assertIn('chunk=min(12,len(ranked))',body)
        self.assertIn('now-last_scan>10',body)
        self.assertIn("if not ('1510'<=now_kst().strftime('%H%M')<'1520')",body)
        self.assertIn("fresh['age']>2",body)

    def test_cw_entry_time_and_book_gate(self):
        from at_close_engine import evaluate
        features={'ask':100,'bid':100,'book_age_sec':.1,'spread_pct':0.,
                  'heavy_ask':False,'suspicious_bid':False,'change_pct':0,
                  'market_cap_krw':2_000_000_000_000,
                  'daily_green':True,'daily_lower_wick_ratio':.5,
                  'daily_close_position':.8,'sma20':105.,
                  'daily_low':100.,'daily_close':103.}
        self.assertTrue(evaluate('CLOSE',features,'1510')[0])
        self.assertFalse(evaluate('CLOSE',features,'1520')[0])
        self.assertIn('OUTSIDE_CLOSE_BET_TIME',evaluate('CLOSE',features,'1520')[2])
        bad=dict(features,book_age_sec=3.1)
        self.assertFalse(evaluate('CLOSE',bad,'1515')[0])
        self.assertIn('NO_FRESH_ORDERBOOK',evaluate('CLOSE',bad,'1515')[2])


if __name__=='__main__':
    unittest.main(verbosity=2)
