"""v094 user request: +2 EXCHANGE ticks only for verifiable partial fills.

Prices use EXACT first BUY execution, never mutable current average. The
cap is actual per-symbol seed, not unrestricted account cash. Orderbook
staleness/duplicate execution is checked by strategy caller, not bypassed.
"""
import unittest
from decimal import Decimal as D
from partial_entry_v094 import two_ticks_max, qualified_topup, tick_size

class TwoRealTicks(unittest.TestCase):
    def test_stock_two_ticks_across_exchange_price_bands(self):
        self.assertEqual(two_ticks_max('stock','1999'),D('2005'))
        self.assertEqual(two_ticks_max('stock','4995'),D('5010'))
        self.assertEqual(two_ticks_max('stock','19990'),D('20050'))
        self.assertEqual(two_ticks_max('stock','199900'),D('200500'))
        self.assertEqual(two_ticks_max('stock','499500'),D('501000'))
        self.assertEqual(two_ticks_max('stock','10000'),D('10020'))

    def test_upbit_two_ticks_across_price_bands(self):
        self.assertEqual(two_ticks_max('coin','9.99'),D('10.10'))
        self.assertEqual(two_ticks_max('coin','9999'),D('10010'))
        self.assertEqual(two_ticks_max('coin','999.0'),D('1001'))
        self.assertEqual(two_ticks_max('coin','0.0999'),D('0.1001'))

    def test_special_upbit_instrument_tick_overrides_generic_ladder(self):
        self.assertEqual(two_ticks_max('coin','1500',symbol='KRW-USDT',instrument_tick='0.5'),D('1501.0'))
        self.assertIsNone(tick_size('coin','1500',symbol='KRW-USDT')) # fail closed if unknown market exception
        self.assertIsNone(two_ticks_max('coin','1500',symbol='KRW-USDC'))

    def test_unfilled_or_full_seed_cannot_chase_two_ticks(self):
        kw=dict(market='stock',first_fill_price='10000',ask='10020',
                spent='30000',max_per_symbol_seed='100000',
                already_sold=False,has_verified_buy=True)
        self.assertTrue(qualified_topup(**kw))
        self.assertFalse(qualified_topup(**(kw|{'spent':'100000'})))
        self.assertFalse(qualified_topup(**(kw|{'already_sold':True})))
        self.assertFalse(qualified_topup(**(kw|{'has_verified_buy':False})))
        self.assertFalse(qualified_topup(**(kw|{'ask':'10030'})))
        self.assertFalse(qualified_topup(**(kw|{'spent':'0'})))
        self.assertFalse(qualified_topup(**(kw|{'max_per_symbol_seed':'0'})))

    def test_repricing_never_reanchors_from_new_weighted_average(self):
        self.assertTrue(qualified_topup('coin','100',D('102'),10,100,False,True))
        self.assertFalse(qualified_topup('coin','100',D('103'),10,100,False,True))
        self.assertTrue(qualified_topup('coin','100',D('102'),10,100,False,True))
        # Even if new average is 101.8, +2 ceiling remains first fill +2.
        self.assertFalse(qualified_topup('coin','100',D('104'),11,100,False,True))

if __name__=='__main__':unittest.main()
