"""v100: test the actual D19 AT wallet's confirmed manual PAPER SELL.

Older v086 coverage used PaperEngine, while Android's D19 owner overrides
its own SELL/ledger.  Never modify the user's persisted wallet in this test.
"""
from __future__ import annotations
import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from test_v091_d19_original_at_reconnect_and_portrait import ActualD19StockAtPolicy
from stock_at_policy_reconcile_v091 import repair_open_v19_positions
from stock_manual_v086 import VerifiedDepth,manual_stock_at

KST=dt.timezone(dt.timedelta(hours=9))

class NativeD19ManualExit(unittest.TestCase):
    def setUp(self):
        self.fixture=ActualD19StockAtPolicy(
            "test_root_cause_legacy_default_repaired_without_false_trade")
        self.fixture.setUpClass()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_d19_original_sell_uses_v19_account_and_once_only_uuid(self):
        pid=self.fixture.create_open()
        original_cash=self.fixture.holding(pid)[2]['cash']
        fixed=repair_open_v19_positions(self.fixture.store)
        self.assertEqual(fixed,[pid])
        now=dt.datetime.now(KST)
        book={'bid':2900,'bid_qty':100,'at':now,'exchange_at':now,
              'source':'KIWOOM_REAL_0D_KRX_PAPER',
              'depth':VerifiedDepth('005930',2900,100,now)}
        rt=SimpleNamespace(stock_runner=SimpleNamespace(engine=self.fixture.engine))
        req={'market':'stock','strategy':'at','code':'005930',
             'position_id':pid,'expected_qty':'76','percent':100,
             'request_id':str(uuid4()),'user_confirmed':True}
        with patch('stock_manual_v086.verified_stock_bid',return_value=book):
            first=manual_stock_at(rt,req)
            repeated=manual_stock_at(rt,req)
        self.assertEqual(first['status'],'FILLED')
        self.assertEqual(first['qty'],76)
        self.assertEqual(first['remaining_qty'],0)
        self.assertTrue(repeated['already_executed'])
        pos,sells,account,_=self.fixture.holding(pid)
        self.assertEqual(pos['status'],'CLOSED')
        self.assertEqual(len(sells),1)
        self.assertEqual(sells[0]['qty'],76)
        self.assertEqual(sells[0]['price'],2900)
        self.assertGreater(account['cash'],original_cash)
        self.assertGreater(account['realized_pnl'],0)

if __name__=="__main__":unittest.main()
