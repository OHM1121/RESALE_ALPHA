"""NEW LEGACY 11.18 regression: two preplaced PAPER orders; no old routes."""
import datetime as dt
import json
import sqlite3
import threading
import unittest
from decimal import Decimal
from pathlib import Path

import new_legacy_1118_resting as resting
import new_legacy_1118_policy as nl
import ma_v067_policy
import ma_v067_coin_runtime as coinma

SRC=Path(__file__).resolve().parents[1]/'app/src/main/python'


def _db():
    c=sqlite3.connect(':memory:')
    c.row_factory=sqlite3.Row
    c.execute("""CREATE TABLE executions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,position_id INTEGER,
        side TEXT,qty TEXT,price TEXT,event_key TEXT UNIQUE)""")
    return c


class RestingBook(unittest.TestCase):
    def test_both_orders_exist_at_buy_and_no_fake_fill(self):
        with _db() as c:
            resting.place(c,17,'coin','KRW-CFG','5m',
                '2026-11-19T13:04:55+09:00',Decimal('1864.73131204'),
                Decimal('98.328'),Decimal('100.29456'),
                Decimal('559.41939361'),Decimal('652.65595921'))
            row=resting.rows(c,17)
            self.assertEqual([x['stage'] for x in row],[1,2])
            self.assertEqual([x['status'] for x in row],
                             ['PAPER_RESTING','PAPER_RESTING'])
            self.assertTrue(all(x['broker_order_id']=='' for x in row))
            self.assertEqual(c.execute('SELECT count(*) FROM executions').fetchone()[0],0)
            # App restart never duplicates orders or rewrites previous fills.
            resting.place(c,17,'coin','KRW-CFG','5m',
                '2026-11-19T13:04:55+09:00',Decimal('1864.73131204'),
                Decimal('98.328'),Decimal('100.29456'),
                Decimal('559.41939361'),Decimal('652.65595921'))
            self.assertEqual(len(resting.rows(c,17)),2)

    def test_native_receipt_required_and_idempotent(self):
        with _db() as c:
            resting.place(c,1,'coin','KRW-CFG','5m','2026-11-19T13:04:55+09:00',
                100,102,104.04,30,35)
            with self.assertRaises(ValueError):
                resting.confirmed_fill(c,1,1,1900)
            cur=c.execute("""INSERT INTO executions(
                position_id,side,qty,price,event_key) VALUES(?,?,?,?,?)""",
                (1,'SELL','30','102','BUY17:TP1'))
            self.assertTrue(resting.confirmed_fill(c,1,1,cur.lastrowid))
            self.assertFalse(resting.confirmed_fill(c,1,1,cur.lastrowid))
            rows=resting.rows(c,1)
            self.assertEqual(rows[0]['filled_qty'],'30')
            self.assertEqual(rows[0]['status'],'PAPER_FILLED')
            self.assertEqual(rows[1]['status'],'PAPER_RESTING')
            resting.cancel_remaining(c,1,'ENTRY_RETRACE')
            self.assertEqual(resting.rows(c,1)[1]['status'],
                             'PAPER_CANCELLED_ENTRY_RETRACE')

    def test_reject_notional_touches_without_native_receipt(self):
        with _db() as c:
            resting.place(c,1,'stock','005930','5m','20261119100000',
                          11,102,104.04,4,4)
            self.assertEqual([r['order_qty'] for r in resting.rows(c,1)],['4','4'])
            # A historical 104 high is not proof of a SELL ledger row.
            self.assertEqual(c.execute('SELECT count(*) FROM executions').fetchone()[0],0)
            c.execute("""INSERT INTO executions(position_id,side,qty,price,event_key)
                VALUES(?,?,?,?,?)""",(1,'BUY','11','104','T'))
            with self.assertRaises(ValueError):
                resting.confirmed_fill(c,1,1,1)
            self.assertEqual(resting.rows(c,1)[0]['status'],'PAPER_RESTING')

    def test_live_plan_never_claims_broker_order(self):
        with _db() as c:
            resting.place(c,7,'coin','KRW-XRP','5m','2026-11-19T13:00+09:00',
                          100,102,104.04,30,35,paper=False)
            self.assertTrue(all(r['status']=='LIVE_PLAN_NOT_SUBMITTED'
                                for r in resting.rows(c,7)))


class VerifiedPath(unittest.TestCase):
    def test_ws_ticker_ascent_survives_missing_contemporaneous_orderbook(self):
        with sqlite3.connect(':memory:') as c:
            c.row_factory=sqlite3.Row
            c.execute("""CREATE TABLE positions(
                id INTEGER PRIMARY KEY,market TEXT,entry TEXT,status TEXT,qty TEXT)""")
            c.execute("""INSERT INTO positions VALUES(17,'KRW-CFG','96.4','OPEN','100')""")
            coinma._table(c)
            state=ma_v067_policy.initial_up_exit_state('5m')
            state['entry_at']='2026-11-19T13:04:55+09:00'
            c.execute("""INSERT INTO ma_v067_positions
                VALUES(17,'REVERSAL_UP','5m','KRW-CFG',?,?,?)""",
                (json.dumps(state),'2026-11-19T13:04:55+09:00',
                 '2026-11-19T13:04:55+09:00'))
            class Ledger:
                lock=threading.RLock()
                def connect(self):return c
            now=dt.datetime(2026,11,19,13,6,40,tzinfo=dt.timezone(dt.timedelta(hours=9)))
            coinma._persist_verified_up_ascent(Ledger(),'KRW-CFG','98.5',now)
            row=c.execute('SELECT state_json FROM ma_v067_positions').fetchone()
            saved=json.loads(row['state_json'])
            self.assertTrue(saved['seen_above_entry'])
            self.assertEqual(saved['verified_above_entry_price'],'98.5')
            after,want,reason=ma_v067_policy.up_exit(
                saved,Decimal('95'),Decimal('96.4'),Decimal('100'),
                Decimal('100'),now+dt.timedelta(minutes=5))
            self.assertEqual(reason,'MA_V065_UP_PRE_FIRST_ENTRY_RETURN')
            self.assertEqual(want,Decimal('100'))

    def test_hold_deadlines_match_uploaded_workbook(self):
        start='2026-11-19T13:04:55+09:00'
        self.assertEqual(coinma._ma_hold_deadline('5m',start),
                         '2026-11-21T13:04:55+09:00')
        self.assertEqual(coinma._ma_hold_deadline('30m',start),
                         '2026-11-22T21:04:55+09:00')
        self.assertIsNone(coinma._ma_hold_deadline('INVALID',start))


class NoOldAutomaticPolicy(unittest.TestCase):
    def test_legacy_exits_fail_closed_outside_gmo_and_close(self):
        src=(SRC/'reference_1118_runtime.py').read_text()
        self.assertIn('if plan is None:',src)
        self.assertIn('return p',src)
        self.assertIn('not str(reason).startswith("NL1118_")',src)
        v061=(SRC/'ma_v061_coin_runtime.py').read_text()
        self.assertNotIn('        _process_exits(self,book,now,original_sell)',v061)
        self.assertIn("proof.get('ma_v067_kind')=='REVERSAL_UP'",v061)
        mt=(SRC/'market_trend_1118_runtime.py').read_text()
        self.assertIn('if getattr(self,"nl1118_policy",None)!=POLICY:',mt)
        self.assertNotIn('return _ORIGINAL_ACTION(self,price',mt)
        stock=(SRC/'ma_v067_stock_runtime.py').read_text()
        self.assertIn("status='NL1118_RETIRED_LEGACY_MA'",stock)

if __name__=='__main__':unittest.main()
