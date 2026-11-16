"""v062 strict MA-only reset, historical archive, research isolation and LIVE path."""
from __future__ import annotations
import datetime as dt
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import ma_epoch_v062 as epoch
import ma_v062_stock_live_lane as lane
import ma_v062_ai_scope as ai
import ma_v062_packet as packet
import android_live_bridge as bridge


class EpochSQLiteTest(unittest.TestCase):
    def setUp(self):
        self._tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root=Path(self._tmp.name)

    def test_original_stock_coin_ma_databases_preserved_but_active_money_zero(self):
        import stock_ma_engine as stock
        from d_storage import DStore
        from coin_ma_engine import MACoinLedger

        coinroot=self.root/'coin'/'ma'
        old_coin=MACoinLedger(coinroot)
        old_stock=stock.StockMAService(self.root/'stock',DStore(self.root/'stock'))
        expected_coin=old_coin.path
        expected_stock=old_stock.db
        self.assertTrue(expected_coin.is_file())
        self.assertTrue(expected_stock.is_file())
        with stock._connect(expected_stock) as c:
            c.execute("""UPDATE accounts SET cash=cash-120000,realized=33750
                WHERE tf='30m'""")
        prior_stock=expected_stock.stat().st_size
        prior_coin=expected_coin.stat().st_size
        epoch.install()
        new_coin=MACoinLedger(coinroot)
        new_stock=stock.StockMAService(
            self.root/'stock',DStore(self.root/'stock'))
        self.assertEqual(new_coin.path,epoch.coin_active(self.root))
        self.assertEqual(new_stock.db,epoch.stock_active(self.root))
        self.assertNotEqual(new_coin.path,old_coin.path)
        self.assertNotEqual(new_stock.db,old_stock.db)
        self.assertTrue(expected_coin.is_file())
        self.assertTrue(expected_stock.is_file())
        self.assertGreater(prior_coin,0)
        self.assertGreater(prior_stock,0)
        with stock._connect(new_stock.db) as c:
            rows=c.execute("SELECT tf,seed,cash,realized FROM accounts").fetchall()
            self.assertEqual(len(rows),4)
            self.assertTrue(all(int(x['seed'])==int(x['cash']) for x in rows))
            self.assertTrue(all(int(x['realized'])==0 for x in rows))
            for table in ('positions','executions','candidates'):
                self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        with stock._connect(expected_stock) as c:
            original=c.execute("SELECT realized,cash FROM accounts WHERE tf='30m'").fetchone()
            self.assertEqual(int(original['realized']),33750)
        with new_coin.connect() as c:
            for table in ('positions','executions','orders'):
                self.assertEqual(c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        first=(epoch.epoch_start(self.root,'stock'),
               epoch.epoch_start(self.root,'coin'))
        self.assertTrue(all(first))
        new_coin_again=MACoinLedger(coinroot)
        self.assertEqual(first,(epoch.epoch_start(self.root,'stock'),
                                epoch.epoch_start(self.root,'coin')))
        self.assertEqual(new_coin_again.path,epoch.coin_active(self.root))
        archive=epoch.read_archive(self.root,'stock')
        self.assertTrue(archive['read_only'])
        self.assertTrue(archive['path'].endswith('STOCK_MA_ENGINE_V1/ma_paper.sqlite3'))

    def test_legacy_live_holding_prevents_new_live_same_symbol_not_original_history(self):
        import stock_ma_engine as stock
        from d_storage import DStore
        old=stock.StockMAService(self.root/'stock',DStore(self.root/'stock'))
        with stock._connect(old.db) as c:
            c.execute("""INSERT INTO positions(tf,code,name,entry_at,entry_date,entry,
                  qty,qty0,first_qty,target,low,ref_close,bodytop,recovery1,peak,
                  midpoint,last_price,hold_sessions,status)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  ('5m','005930','삼성전자',
                   '2026-11-15T09:10:00+09:00','2026-11-15',10000,
                   15,15,3,11000,9500,10000,10300,9700,10000,
                   10000,10000,3,'OPEN'))
        self.assertEqual(epoch._prior_live_conflict(
            self.root,'stock','005930'),'OLD_MA_POSITION_UNPROVEN_BROKER_STATE')
        class StubProxy:
            _source='stock_ma'
            class _rt:
                root=self.root
                @staticmethod
                def live_enabled(m):return m=='stock'
        sql='INSERT INTO executions(tf,position_id,code,name,ts,side,qty,price,reason) VALUES(?,?,?,?,?,?,?,?,?)'
        try:
            with self.assertRaises(bridge.LiveOrderError):
                epoch._guard_before(lambda *a:None,StubProxy(),sql,
                    ('5m',1,'005930','삼성전자','now','BUY',1,10000,'MA_TEST'))
        except NameError:
            self.fail('No broker safeguard available')

    def test_ai_scopes_new_ma_only_and_no_trend_mixing_in_ui(self):
        import ai_strategy_analysis_v037 as original
        prior=original.SOURCE.copy()
        ai.install()
        self.assertEqual(original.SOURCE[('stock','ma')][0],
                         'stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3')
        self.assertEqual(original.SOURCE[('coin','ma')][0],
                         'coin/ma_epoch_v062/upbit_paper.sqlite3')
        self.assertEqual(original.SOURCE[('stock','at')],prior[('stock','at')])
        self.assertEqual(original.SOURCE[('coin','general')],prior[('coin','general')])
        from ai_research_v040 import capture_scope,db_path
        capture_scope(self.root,'stock','ma',{'items':[]},{'positions':[]},now=1794500000)
        self.assertTrue(db_path(ai.ai_root(self.root),'stock').is_file())
        self.assertFalse(db_path(self.root,'stock').is_file())
        java=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text('utf8')
        self.assertNotIn('renderResearchCohortToggle',java)
        self.assertNotIn('renderTrendResearch()',java)
        self.assertNotIn('researchTrendOnly',java)
        self.assertIn('showLegacyMaArchive',java)
        self.assertIn('"trend".equals(s)',java)

    def test_stock_first5_eligible_independently_of_watchlist(self):
        from d_storage import KST
        now=dt.datetime(2026,11,16,9,0,2,tzinfo=KST)
        record=('005930','KOSPI','삼성전자','20261116090002',9999,400,
                now.isoformat(),'KRX',now.isoformat())
        with mock.patch('d_minute_repair_fast.exchange_stamp_is_fresh',
                        return_value=True):
            self.assertTrue(lane._eligible(record,now))
            late=now.replace(second=6)
            self.assertFalse(lane._eligible(record[:6]+(late.isoformat(),)+record[7:],late))
            self.assertFalse(lane._eligible(record[:7]+('NXT',)+record[8:],now))
            self.assertFalse(lane._eligible(record[:7]+('KRX',now.isoformat()),
                                              now+dt.timedelta(seconds=9)))

    def test_confirmed_stock_ma_execution_calls_existing_native_fok(self):
        class Broker:
            def __init__(self):self.calls=[]
            def fok(self,*args):
                self.calls.append(args)
                return {'qty':args[3],'price':args[4],'order_id':'FAKE_STUB_ONLY'}
        class Runtime:
            def __init__(self):
                self.kiwoom=Broker();self.calls=[]
            def live_enabled(self,market):return market=='stock'
            def on_live_fill(self,*args):self.calls.append(args)
        rt=Runtime()
        with sqlite3.connect(':memory:') as db:
            proxy=bridge.LiveConnectionProxy(db,rt,'stock_ma')
            sql="""INSERT INTO executions(tf,position_id,code,name,ts,side,
                qty,price,fees,tax,net_profit,reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"""
            params=('30m',1,'005930','삼성전자',
                    '2026-11-16T09:30:01+09:00','BUY',1,50000,0,0,None,
                    'MA_KRX_FRESH_BOOK_PAPER')
            proxy._before(sql,params)
        self.assertEqual(len(rt.kiwoom.calls),1)
        self.assertEqual(rt.kiwoom.calls[0][1:5],
                         ('BUY','005930',1,50000))
        self.assertEqual(len(rt.calls),1)


if __name__=='__main__':unittest.main()
