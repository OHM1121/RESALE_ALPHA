"""v094 GMO D+1 only, first real PAPER BUY fill +2 tick continuation.

No D+2, no absent first execution, no trading after any prior sell, no
inferred broker fill. The original engine checks and reconciles all intents.
"""
import unittest
from types import SimpleNamespace
from projectgmo.engine import Engine, Position, Quote
from gmo_partial_entry_v094 import topup_verified_d1_gmo

class TrueGMO(unittest.TestCase):
    def setUp(self):
        self.engine=Engine()
        self.engine.positions['005930']=Position('005930','Test',1,1,10000,
                           '2026-11-17','KRX',10001)
        self.engine.cash-=10001
        self.engine.buy_fills.append({'code':'005930',
            'time':'2026-11-17T09:01:00+09:00','qty':1,'price':10000,
            'fee':1,'paid':10001,'purpose':'D+1_OPEN_AT_OR_BELOW_OPEN'})
        self.engine.signals['005930']=SimpleNamespace(
            entry_date='2026-11-17',event_date='2026-11-16')
        self.q=Quote('005930','2026-11-17T09:05:00+09:00',1,
            10020,10020,10010,500,300,'KRX',0.0)
    def test_d1_underfunded_slot_allows_plus_two_ticks_and_original_fill_ledger(self):
        intent=topup_verified_d1_gmo(self.engine,self.q)
        self.assertIsNotNone(intent)
        self.assertEqual(intent.purpose,'D+1_PARTIAL_TWO_TICKS')
        self.assertEqual(intent.limit,10020)
        self.assertGreater(intent.requested,0)
        self.engine.fill(intent.id,intent.requested,10020,self.q.at)
        self.assertGreater(self.engine.positions['005930'].qty,1)
        self.assertTrue(any(x['purpose']=='D+1_PARTIAL_TWO_TICKS' for x in self.engine.buy_fills))
    def test_order_already_pending_does_not_duplicate(self):
        self.assertIsNotNone(topup_verified_d1_gmo(self.engine,self.q))
        self.assertIsNone(topup_verified_d1_gmo(self.engine,self.q))
    def test_d2_not_allowed_even_if_previous_slot_partial(self):
        bad=Quote('005930','2026-11-18T09:05:00+09:00',2,
            10020,10020,10010,500,300,'KRX',0.0)
        self.assertIsNone(topup_verified_d1_gmo(self.engine,bad))
        self.assertEqual(self.engine.orders,{})
    def test_third_tick_does_not_allow_purchase(self):
        bad=Quote('005930',self.q.at,1,10030,10030,10020,500,300,'KRX',0)
        self.assertIsNone(topup_verified_d1_gmo(self.engine,bad))
    def test_no_old_paper_buy_fills_means_no_reprice(self):
        self.engine.buy_fills=[]
        self.assertIsNone(topup_verified_d1_gmo(self.engine,self.q))
    def test_any_confirmed_prior_sell_blocks_second_purchase(self):
        self.engine.fills=[{'code':'005930','time':self.q.at,'qty':1,'price':10020}]
        self.assertIsNone(topup_verified_d1_gmo(self.engine,self.q))
    def test_stale_or_no_displayed_ask_size_is_not_a_fill(self):
        bad=Quote('005930',self.q.at,1,10020,10020,10010,0,300,'KRX',0)
        self.assertIsNone(topup_verified_d1_gmo(self.engine,bad))
        old=Quote('005930',self.q.at,1,10020,10020,10010,500,300,'KRX',4)
        self.assertIsNone(topup_verified_d1_gmo(self.engine,old))

if __name__=='__main__':unittest.main()
