"""Real original DStore/PaperEngine SQLite: reconnect 3LEG31 exits.

No fake _sell callback; the only SELLs here execute D engine's original
accounting in a brand-new temporary paper wallet, never a phone or broker DB.
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from d_storage import DStore
from d_engine import PaperEngine
from stock_at_catchup_v086 import install

K='005930'
STAMP='20261118100001'

class NativeAtCatchup(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='artitrade-v086-at-')
        self.addCleanup(self.tmp.cleanup)
        self.store=DStore(Path(self.tmp.name))
        install()
        self.engine=PaperEngine(self.store)
        self.engine._execution_context.fill_method='LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE'
        with self.store.connect() as c:
            c.execute("""INSERT INTO positions(
                tf,code,market,name,ref_ts,ref_low,ref_high,ref_bodytop,
                entry_ts,entry_price,entry_qty,qty,hold_days,entry_fee,
                last_price,peak,exit_policy,exit_stage,exit_first_target)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('5m',K,'KOSPI','TEST','20261117090000',90,120,110,
                 '20261117090100',100,100,100,2,10,100,100,'3LEG31',0,105))
            self.pid=c.execute('SELECT id FROM positions ORDER BY id DESC LIMIT 1').fetchone()[0]

    def event(self,price,when=STAMP):
        minute={'code':K,'open':price,'high':price,'low':price,
                'close':price,'volume':100}
        with self.store.lock,self.store.connect() as c:
            p=c.execute('SELECT * FROM positions WHERE id=?',(self.pid,)).fetchone()
            self.engine._minute_exits_threeleg(c,p,minute,when)
        with self.store.connect() as c:
            p=dict(c.execute('SELECT * FROM positions WHERE id=?',(self.pid,)).fetchone())
            sells=[dict(x) for x in c.execute("""SELECT qty,price,ts,reason,fill_method
                FROM executions WHERE position_id=? AND side='SELL' ORDER BY id""",(self.pid,))]
            account=dict(c.execute("SELECT * FROM accounts WHERE tf='5m'").fetchone())
        return p,sells,account

    def test_one_actual_post_gap_trade_fills_first_and_second_at_observed_price(self):
        p,s,account=self.event(110)
        self.assertEqual([x['qty'] for x in s],[30,35])
        self.assertEqual([x['price'] for x in s],[110,110])
        self.assertEqual(p['qty'],35)
        self.assertEqual(p['exit_stage'],2)
        self.assertTrue(all(x['fill_method']=='LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE' for x in s))
        self.assertGreater(account['realized_pnl'],0)
        _,again,_=self.event(110)
        self.assertEqual(len(again),2,'same genuine trade cannot resell the remaining stage')

    def test_below_second_target_only_first_stage_is_realized(self):
        p,s,_=self.event(106)
        self.assertEqual([x['qty'] for x in s],[30])
        self.assertEqual([x['price'] for x in s],[106])
        self.assertEqual(p['exit_stage'],1)

    def test_uninterrupted_tick_preserves_original_first_price_and_single_stage(self):
        self.engine._execution_context.v086_at_last_by_code={K:time.monotonic()}
        p,s,_=self.event(110)
        self.assertEqual([x['qty'] for x in s],[30])
        self.assertEqual([x['price'] for x in s],[105])
        self.assertEqual(p['exit_stage'],1)

    def test_downside_final_stop_priority_over_all_profit_stages(self):
        p,s,_=self.event(70)
        self.assertEqual(len(s),1)
        self.assertEqual(s[0]['qty'],100)
        self.assertIn('하락 최종',s[0]['reason'])
        self.assertEqual(p['qty'],0)
        self.assertEqual(p['status'],'CLOSED')

if __name__=='__main__':unittest.main()
