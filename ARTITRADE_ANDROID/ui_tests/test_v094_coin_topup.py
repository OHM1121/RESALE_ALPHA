"""v094: native Upbit PAPER coin and MA partial buy using original SQLite wallet.

The first registered BUY execution fixes the +2 exchange tick ceiling. A
previous SELL, wrong market, missing amount, stale book, duplicate WS event
or full per-position seed prevents additional buying. This test never makes
an external trade or uses API credentials.
"""
import datetime as dt
import sqlite3
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

import d_upbit
from coin_partial_entry_v094 import topup_verified_coin_book
KST=dt.timezone(dt.timedelta(hours=9))


class NativeCoinTopup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.ledger=d_upbit.CoinLedger(self.root/'base')
        self.now=dt.datetime.now(KST)
        at=(self.now-dt.timedelta(seconds=2)).isoformat()
        self.at=at
        with self.ledger.lock,self.ledger.connect() as c:
            c.execute("""INSERT INTO positions(tf,market,name,ref_at,entry_at,entry,
              qty0,qty,entry_fee,target,peak,mark,first_qty,hold_until)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ('5m','KRW-BTC','TEST',at,at,'10000','1','1',5,'11000',
               '10000','10000','.4',(self.now+dt.timedelta(hours=4)).isoformat()))
            self.pid=int(c.execute('SELECT MAX(id) FROM positions').fetchone()[0])
            c.execute("""INSERT INTO executions(event_key,position_id,tf,market,at,
                side,qty,price,fees,tax,net_pnl,reason,cash_after)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('ORIGINAL-BUY',self.pid,'5m','KRW-BTC',at,'BUY','1','10000',5,0,0,'MA_ORIGINAL',0))
            c.execute("UPDATE wallets SET cash=cash-10005 WHERE tf='5m'")
        self.book={'market':'KRW-BTC','ask':'10020','ask_size':'1000',
                   'bid':'10010','bid_size':'900','timestamp':int(self.now.timestamp()*1000),
                   'sr_tick_size':'10'}
    def tearDown(self):self.temp.cleanup()
    def pos(self):
        with self.ledger.lock,self.ledger.connect() as c:
            return dict(c.execute('SELECT * FROM positions WHERE id=?',(self.pid,)).fetchone()),[
                dict(x) for x in c.execute('SELECT * FROM executions WHERE position_id=? ORDER BY id',
                                          (self.pid,)).fetchall()]
    def test_v067_coin_ma_book_has_explicit_partial_topup_hook(self):
        from coin_ma_engine import MACoinLedger
        from coin_partial_entry_v094 import install
        install()
        self.assertTrue(getattr(d_upbit.CoinLedger.process_book,'_v094',False))
        self.assertTrue(getattr(MACoinLedger.process_book,'_v094',False))

    def test_existing_partial_position_can_buy_within_plus_two_real_upbit_ticks(self):
        amount=topup_verified_coin_book(self.ledger,self.book,self.now)
        self.assertGreater(amount,0)
        p,execution=self.pos()
        self.assertEqual(len(execution),2)
        self.assertEqual(execution[1]['price'],'10020')
        self.assertEqual(execution[1]['reason'],'COIN_V094_PARTIAL_TWO_TICKS')
        self.assertEqual(D(p['qty']),D(p['qty0']))
    def test_replayed_exact_same_0d_timestamp_never_executes_twice(self):
        self.assertGreater(topup_verified_coin_book(self.ledger,self.book,self.now),0)
        self.assertEqual(topup_verified_coin_book(self.ledger,self.book,self.now),0)
        self.assertEqual(len(self.pos()[1]),2)
    def test_third_exchange_tick_above_first_fill_is_disallowed(self):
        self.book.update(ask='10030')
        self.assertEqual(topup_verified_coin_book(self.ledger,self.book,self.now),0)
        self.assertEqual(len(self.pos()[1]),1)
    def test_no_chase_after_first_sell(self):
        with self.ledger.lock,self.ledger.connect() as c:
            c.execute("""INSERT INTO executions(event_key,position_id,tf,market,at,
                side,qty,price,fees,tax,net_pnl,reason,cash_after)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('PRIOR-SELL',self.pid,'5m','KRW-BTC',self.now.isoformat(),
                 'SELL','.1','10100',0,0,0,'TEST',0))
        self.assertEqual(topup_verified_coin_book(self.ledger,self.book,self.now),0)
    def test_stale_orderbook_not_a_fill(self):
        self.book['timestamp']-=5000
        self.assertEqual(topup_verified_coin_book(self.ledger,self.book,self.now),0)
    def test_out_of_initial_30_second_partial_window_is_blocked(self):
        with self.ledger.lock,self.ledger.connect() as c:
            c.execute('UPDATE positions SET entry_at=? WHERE id=?',
                    ((self.now-dt.timedelta(seconds=61)).isoformat(),self.pid))
        self.assertEqual(topup_verified_coin_book(self.ledger,self.book,self.now),0)

if __name__=='__main__':unittest.main()
