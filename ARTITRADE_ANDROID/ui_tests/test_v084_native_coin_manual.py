"""v0.8.4 regression against the ACTUAL bundled Upbit MA SQLite and sell stack.

No fake _sell callback, fake fill, market API, or broker order is used. Live
quote objects below are deterministic PAPER test fixtures only. This tests
the previously untested complete CoinLedger.manual_sell -> MA overlay ->
executions -> Android response path, including recovery after lost response.
"""
import datetime as dt
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
import requests

import android_manual_paper_sell_v085 as android
import ma_v061_coin_runtime as v061
import ma_v067_coin_runtime as v067
from coin_ma_engine import MACoinLedger

KST=dt.timezone(dt.timedelta(hours=9))


class NativePaperMA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        v061.install()
        v067.install()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.owner=MACoinLedger(self.root/'test_ma')
        self.code='KRW-ERA'
        self.now=dt.datetime.now(KST)
        self.at=self.now.isoformat()
        with self.owner.lock,self.owner.connect() as c:
            c.execute("""INSERT INTO positions(
                tf,market,name,ref_at,entry_at,entry,qty0,qty,entry_fee,
                target,peak,mark,first_qty,hold_until)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('5m',self.code,'ERA',self.at,self.at,'94.90','40','40',
                 0,'96.80','94.90','94.90','12',
                 (self.now+dt.timedelta(days=2)).isoformat()))
            self.pid=int(c.execute("SELECT max(id) FROM positions").fetchone()[0])
            c.execute("""INSERT OR REPLACE INTO ma_v067_positions(
                position_id,kind,frame,code,state_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (self.pid,'REVERSAL_UP','5m',self.code,'{}',self.at,self.at))
        book={'market':self.code,'bid':'94.90','bid_size':'1000',
              'ask':'95.00','ask_size':'1000','timestamp':int(time.time()*1000)}
        self.runner=type('Runner',(),{})()
        self.runner._lock=threading.RLock()
        self.runner.last_book={self.code:book}
        self.runner.ma_ledger=self.owner
        self.runner.ledger=self.owner
        self.rt=type('Runtime',(),{'coin_runner':self.runner})()
        self.req={'code':self.code,'position_id':self.pid,'expected_qty':'40',
                  'percent':100,'request_id':str(uuid4())}

    def tearDown(self):self.temp.cleanup()

    def rows(self):
        with self.owner.lock,self.owner.connect() as c:
            p=c.execute('SELECT qty,status FROM positions WHERE id=?',(self.pid,)).fetchone()
            xs=c.execute('SELECT event_key,side,qty,price FROM executions WHERE position_id=?',(self.pid,)).fetchall()
            return (str(p['qty']),str(p['status'])),[dict(x) for x in xs]

    def test_full_manual_ma_sell_writes_exact_native_execution_and_closes(self):
        got=android._coin(self.rt,self.req,'ma')
        self.assertEqual(str(got['qty']),'40')
        pos,executions=self.rows()
        self.assertEqual(pos[1],'CLOSED')
        self.assertEqual(len(executions),1)
        self.assertEqual(executions[0]['event_key'],'USER:'+self.req['request_id'])
        self.assertEqual(executions[0]['side'],'SELL')

    def test_replay_same_uuid_after_full_sell_returns_original_fill_not_error(self):
        first=android._coin(self.rt,self.req,'ma')
        again=android._coin(self.rt,self.req,'ma')
        self.assertEqual(str(again['qty']),str(first['qty']))
        self.assertTrue(again['already_executed'])
        self.assertEqual(len(self.rows()[1]),1)

    def test_replay_same_uuid_after_partial_sell_returns_original_fill_not_error(self):
        self.req['percent']=50
        first=android._coin(self.rt,self.req,'ma')
        again=android._coin(self.rt,self.req,'ma')
        self.assertEqual(str(again['qty']),str(first['qty']))
        self.assertTrue(again['already_executed'])
        self.assertEqual(len(self.rows()[1]),1)
        self.assertEqual(self.rows()[0][1],'OPEN')

    def test_stale_ws_book_can_use_independent_fresh_official_rest_bid(self):
        self.runner.last_book.clear()
        class Response:
            def raise_for_status(self):pass
            def json(self):
                return [{'market':self_market,'timestamp':int(time.time()*1000),
                    'orderbook_units':[{'ask_price':95.00,'ask_size':1000,
                                        'bid_price':94.90,'bid_size':1000}]}]
        self_market=self.code
        with patch('requests.get',return_value=Response()) as fetch:
            got=android._coin(self.rt,self.req,'ma')
        self.assertEqual(str(got['qty']),'40')
        self.assertEqual(self.rows()[0][1],'CLOSED')
        self.assertEqual(len(self.rows()[1]),1)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(fetch.call_args.args[0],'https://api.upbit.com/v1/orderbook')

    def test_stale_official_rest_book_must_not_fake_a_sell(self):
        self.runner.last_book.clear()
        class Response:
            def raise_for_status(self):pass
            def json(self):
                return [{'market':self_market,'timestamp':int((time.time()-50)*1000),
                    'orderbook_units':[{'ask_price':95,'ask_size':1000,
                                        'bid_price':94.90,'bid_size':1000}]}]
        self_market=self.code
        with patch('requests.get',return_value=Response()):
            with self.assertRaisesRegex(ValueError,'FRESH_COIN_ORDERBOOK_MISSING'):
                android._coin(self.rt,self.req,'ma')
        self.assertEqual(self.rows()[1],[])

    def test_unverified_book_cannot_sell(self):
        self.runner.last_book[self.code]['timestamp']=int((time.time()-60)*1000)
        with patch('requests.get',side_effect=requests.exceptions.Timeout('offline')):
            with self.assertRaisesRegex(ValueError,'FRESH_COIN_ORDERBOOK_MISSING'):
                android._coin(self.rt,self.req,'ma')
        self.assertEqual(self.rows()[1],[])

    def test_wrong_expected_quantity_cannot_sell(self):
        self.req['expected_qty']='41'
        with self.assertRaisesRegex(ValueError,'COIN_QTY_CHANGED_RECONFIRM'):
            android._coin(self.rt,self.req,'ma')
        self.assertEqual(self.rows()[1],[])


if __name__=='__main__':unittest.main()
