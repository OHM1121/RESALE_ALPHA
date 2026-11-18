"""V091 real original D19 account + BUY/SELL SQLite regression.

Test-only synthetic positions, no user broker connection, API call or account
modification. The original D19 cash, tax, accounting and threeleg logic are
executed, not mocked. The archived 82-module EXE core is required on PYTHONPATH.
"""
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import patch
from d_storage import KST
from decimal import Decimal
from pathlib import Path

from d_storage import DStore
from d_v19_portfolio import D19PaperEngine
from stock_at_catchup_v086 import install as install_catchup
from stock_at_policy_reconcile_v091 import (
    _ACTIVATION_BUY_TS,repair_open_v19_positions,install as install_d19)

K='005930'
WHEN='20261118132805'
BUY_AT='20261117164344'

class ActualD19StockAtPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # D19 and PaperEngine are BOTH patched. Test the native D19 subclass,
        # whose own ._sell used to evade PaperEngine's observed-price hook.
        install_catchup()
        install_d19()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='v091-D19-original-')
        self.addCleanup(self.tmp.cleanup)
        self.store=DStore(Path(self.tmp.name))
        self.engine=D19PaperEngine(self.store)

    def create_open(self,*,date=BUY_AT,first_sold=0,stage=0,
                    qty=76,entry_qty=76,with_buy=True,legacy=False,
                    name='ARCHIVED ORIGINAL PAPER',code=K):
        with self.store.lock,self.store.connect() as c:
            c.execute("""INSERT INTO positions(
              tf,code,market,name,ref_ts,ref_low,ref_high,ref_bodytop,
              entry_ts,entry_price,entry_qty,qty,hold_days,entry_fee,
              last_price,first_sell_qty,first_sold,exit_stage,exit_policy,
              exit_first_target)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              ('30m',code,'KOSDAQ',name,'20261117093000',2500,3200,2800,
               date,2600,entry_qty,qty,3,40,2600,30,first_sold,stage,'LEGACY',0))
            pid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute("""INSERT INTO signals(
                 event_key,ts,tf,code,name,market,ref_ts,
                 buy_price,target,score,features,decision)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                 ('v091-signal-'+str(pid),'20261117093000','30m',code,
                  name,'KOSDAQ','20261117093000',2500,2800,70.0,'{}','BOUGHT'))
            sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute("""INSERT INTO v19_members(
                position_id,origin_venue,signal_id,initial_won)
                VALUES(?,?,?,?)""",(pid,'KRX',sid,entry_qty*2600+40))
            c.execute("""UPDATE v19_accounts SET cash=cash-?
                WHERE tf='30m'""",(entry_qty*2600+40,))
            if with_buy:
                c.execute("""INSERT INTO executions(
                  event_key,tf,code,name,position_id,ts,side,qty,
                  price,gross,fees,tax,cash_after,reason,fill_method,seen_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  ('v091-original-buy-'+str(pid),'30m',code,name,pid,date,'BUY',
                   entry_qty,2600,entry_qty*2600,40,0,
                   2000000-entry_qty*2600-40,
                   'AT DIVERSE 1.1 순수 점수 | 50만원 상한 | 호가 기반 가상체결',
                   'LIVE_0D_DISPLAYED_ASK_PAPER_PROXY',date))
            if legacy:
                c.execute("""INSERT INTO v19_legacy_members
                    (position_id,tf,original_entry_won,adopted_at,origin_venue)
                    VALUES(?,?,?,?,?)""",(pid,'30m',entry_qty*2600,date,'KRX'))
            if qty<entry_qty:
                c.execute("""INSERT INTO executions(
                  event_key,tf,code,name,position_id,ts,side,qty,
                  price,gross,fees,tax,cash_after,reason,fill_method,seen_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  ('v091-old-sell-'+str(pid),'30m',code,name,pid,
                   '20261118090300','SELL',entry_qty-qty,2800,
                   (entry_qty-qty)*2800,0,0,2000000,
                   'ORIGINAL_LEGACY_40PCT_SALE',
                   'LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE','20261118090300'))
        return pid

    def holding(self,pid):
        with self.store.connect() as c:
            p=dict(c.execute('SELECT * FROM positions WHERE id=?',(pid,)).fetchone())
            sells=[dict(x) for x in c.execute("""SELECT * FROM executions
                WHERE position_id=? AND side='SELL' ORDER BY id""",(pid,))]
            cash=dict(c.execute("SELECT * FROM v19_accounts WHERE tf='30m'").fetchone())
            audit=[dict(x) for x in c.execute("""SELECT * FROM audit
               WHERE code=? AND category='AT_POLICY_ORIGINAL_INIT_REPAIRED'""",(p['code'],))]
        return p,sells,cash,audit

    def live_tick(self,pid,price,at=WHEN):
        # Exactly D19's executed _minute_exits path, with its original
        # live-trade fill-method context. No mocked _sell or invented order.
        snap={'code':K,'open':price,'high':price,'low':price,
              'close':price,'volume':1500}
        with self.store.lock,self.store.connect() as c:
            p=c.execute('SELECT * FROM positions WHERE id=?',(pid,)).fetchone()
            self.engine._execution_context.fill_method=(
                'LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE')
            try:self.engine._minute_exits(c,p,snap,at)
            finally:self.engine._execution_context.fill_method=(
                'LOCAL_1M_PRICE_TOUCH_ESTIMATE')
        return self.holding(pid)

    def test_actual_d19_new_buy_opens_with_original_threeleg_immediately(self):
        # End-to-end real D19 limit matcher, genuine source order row,
        # original SQLite BUY ledger and same-transaction threeleg opt-in.
        when=datetime(2026,11,18,13,28,5,tzinfo=KST)
        at=when.strftime('%Y%m%d%H%M%S')
        with self.store.lock,self.store.connect() as c:
            c.execute("""INSERT INTO bars(
                code,market,frame,ts,open,high,low,close,volume,source)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (K,'KOSDAQ','30m','20261117133000',2600,3000,
                 2500,2800,15000,'LIVE_WS_0B'))
            c.execute("""INSERT INTO signals(
                event_key,ts,tf,code,name,market,ref_ts,buy_price,
                target,score,features,decision)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ('v091-native-signal', '20261117140000','30m',K,
                 'REAL ORIGINAL MATCHER','KOSDAQ','20261117133000',
                 2500,2800,69.0,
                 '{"ranking_engine":"AT_DIVERSE_1_1_ACTIVE"}','PENDING'))
            sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute("""INSERT INTO v19_orders(
                signal_id,code,tf,venue,score,limit_won,since_ts,status)
                VALUES(?,?,?,?,?,?,?,?)""",
                (sid,K,'30m','KRX',69.0,2600,'20261117140000','PENDING'))
            row=c.execute("SELECT * FROM v19_orders WHERE signal_id=?",
                          (sid,)).fetchone()
            self.engine.book._items[(K,'KRX')]=(2600,2580,350,when)
            with patch('d_v19_portfolio.live_session',return_value=True):
                outcome=self.engine._buy_limit_depth(
                    c,row,when,'KRX',2600,1000)
            self.assertTrue(outcome[0],outcome)
        with self.store.connect() as c:
            p=dict(c.execute("SELECT * FROM positions WHERE code=?",(K,)).fetchone())
            buys=c.execute("SELECT COUNT(*) FROM executions WHERE position_id=? AND side='BUY'",
                           (p['id'],)).fetchone()[0]
            self.assertEqual(p['exit_policy'],'3LEG31')
            self.assertEqual(p['exit_first_target'],2800)
            self.assertEqual(p['first_sell_qty'],
                max(1,(p['entry_qty']*30+50)//100))
            self.assertEqual(buys,1)
            self.assertEqual(p['qty'],p['entry_qty'])

    def test_root_cause_legacy_default_repaired_without_false_trade(self):
        pid=self.create_open()
        p,s,cash,a=self.holding(pid)
        self.assertEqual(p['exit_policy'],'LEGACY')
        self.assertEqual(p['exit_first_target'],0)
        self.assertEqual([x['side'] for x in s],[])
        changed=repair_open_v19_positions(self.store)
        self.assertEqual(changed,[pid])
        fixed,sells,wallet,audit=self.holding(pid)
        self.assertEqual(fixed['exit_policy'],'3LEG31')
        self.assertEqual(fixed['exit_first_target'],2800)
        self.assertEqual(fixed['first_sell_qty'],23)
        self.assertEqual(fixed['entry_price'],2600)
        self.assertEqual(fixed['entry_qty'],76)
        self.assertEqual(fixed['qty'],76)
        self.assertEqual(sells,[])
        self.assertEqual(wallet['realized_pnl'],0)
        self.assertEqual(len(audit),1)
        self.assertEqual(repair_open_v19_positions(self.store),[])
        self.assertEqual(len(self.holding(pid)[3]),1)

    def test_post_restart_3020_executes_30_and_35_percent_at_actual_price(self):
        pid=self.create_open()
        self.assertEqual(repair_open_v19_positions(self.store),[pid])
        p,s,cash,a=self.live_tick(pid,3020)
        self.assertEqual([int(x['qty']) for x in s],[23,27])
        self.assertEqual([int(x['price']) for x in s],[3020,3020])
        self.assertTrue(all(x['fill_method']=='LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE'
                            for x in s))
        self.assertEqual(p['qty'],26)
        self.assertEqual(p['exit_stage'],2)
        self.assertGreater(cash['realized_pnl'],0)
        self.assertEqual(len(a),1)
        # Duplicate same timestamp never sells the 3rd remainder.
        after=self.live_tick(pid,3020)
        self.assertEqual(len(after[1]),2)

    def test_first_only_then_second_when_fresh_price_reaches_target(self):
        pid=self.create_open()
        repair_open_v19_positions(self.store)
        p,s,_,_=self.live_tick(pid,2830)
        self.assertEqual([r['qty'] for r in s],[23])
        self.assertEqual(p['exit_stage'],1)
        p,s,_,_=self.live_tick(pid,3000,'20261118132902')
        self.assertEqual([r['qty'] for r in s],[23,27])
        self.assertEqual(p['qty'],26)
        self.assertEqual(p['exit_stage'],2)

    def test_two_stages_then_5_percent_trailing_final(self):
        pid=self.create_open()
        repair_open_v19_positions(self.store)
        self.live_tick(pid,3020)
        p,s,_,_=self.live_tick(pid,3200,'20261118132902')
        self.assertEqual(p['peak'],3200)
        p,s,cash,_=self.live_tick(pid,3030,'20261118133000')
        self.assertEqual([x['qty'] for x in s],[23,27,26])
        self.assertEqual(p['qty'],0)
        self.assertEqual(p['status'],'CLOSED')
        self.assertGreater(cash['realized_pnl'],0)

    def test_partial_old_and_imported_legacy_are_never_reclassified(self):
        eligible=self.create_open(code='005930')
        imported=self.create_open(code='000660',legacy=True)
        old=self.create_open(code='000270',date='20261009101100')
        partial=self.create_open(code='008770',qty=46,first_sold=1)
        no_proof=self.create_open(code='032830',with_buy=False)
        self.assertEqual(repair_open_v19_positions(self.store),[eligible])
        for pid in (imported,old,partial,no_proof):
            self.assertEqual(self.holding(pid)[0]['exit_policy'],'LEGACY')
        self.assertEqual(self.holding(eligible)[0]['exit_policy'],'3LEG31')
        self.assertEqual(len(self.holding(imported)[1]),0)
        self.assertEqual(len(self.holding(partial)[1]),1)

class PortraitAndOriginalUIFields(unittest.TestCase):
    def test_no_left_crop_and_recover_real_at_exit_target(self):
        src=(Path(__file__).resolve().parents[1]/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertIn('if(screenDp<520)',src)
        self.assertIn('net.setSingleLine(false)',src)
        self.assertIn('net.setHorizontallyScrolling(false)',src)
        self.assertIn('net.setEllipsize(null)',src)
        self.assertIn('panel.addView(net,new LinearLayout.LayoutParams(-1,-2))',src)
        self.assertIn('usefulNumber(position,"exit_first_target")',src)
        self.assertIn('usefulNumber(position,"ref_bodytop")',src)

if __name__=='__main__':unittest.main()
