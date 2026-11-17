"""v097 D+1 entry scenario tests; PAPER-only original ProjectGMO wallet."""
import base64,datetime as dt,io,math,sys,tempfile,threading,unittest,zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]/"app/src/main"
sys.path.insert(0,str(ROOT/"python"))
from gmo_d1_preplan_v097 import Preplanner,PHASE,_valid_phase,KST
from projectgmo.prices import plus_ticks

class ServiceFixture:
    def __init__(self,root):
        from projectgmo.engine import Engine
        self.root=Path(root)
        self.engine=Engine()
        self.engine._v097_managed_codes=set()
        self._lock=threading.RLock()
        self.opens={}
        self.priority_books={}
        self._stop=threading.Event()
        self.last_error=""
        self.persist_calls=0
        self._persist=lambda *args,**kw:setattr(self,"persist_calls",self.persist_calls+1)
        self.engine.signals["005930"]=SimpleNamespace(
            code="005930",name="TEST",event_date="2026-11-16",
            entry_date="2026-11-17",event_close=9900,session_type="SHORT")

class PreplanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.svc=ServiceFixture(self.tmp.name)
        self.ready=dt.datetime(2026,11,17,8,58,0,tzinfo=KST)
        self.now=dt.datetime(2026,11,17,9,0,0,tzinfo=KST)
        self.p=Preplanner(self.svc,clock=lambda:self.now)
    def tearDown(self):self.tmp.cleanup()

    def test_exact_preflight_times_and_nxt_vs_krx(self):
        self.assertTrue(_valid_phase("NXT",dt.datetime(2026,11,17,7,58,tzinfo=KST),True))
        self.assertFalse(_valid_phase("NXT",dt.datetime(2026,11,17,8,0,tzinfo=KST),True))
        self.assertTrue(_valid_phase("KRX",self.ready,True))
        self.assertFalse(_valid_phase("KRX",self.now,True))
        self.assertEqual(self.p.prepare("KRX",self.ready),("005930",))
        item=self.p.plans[("2026-11-17","005930","KRX")]
        self.assertEqual(item["max_budget_won"],500000)
        self.assertEqual(item["scenario"],0)
        self.assertFalse(self.svc.engine.orders)

    def test_three_prices_one_order_and_partials_never_exceed_seed(self):
        from projectgmo.engine import Quote
        self.p.prepare("KRX",self.ready)
        plan=self.p.plans[("2026-11-17","005930","KRX")]
        plan["opening_price"]=10000
        def q_at(at,last,ask,size):
            return Quote("005930",at.isoformat(),1,last,ask,ask-10,size,30,"KRX",0)
        q0=q_at(self.now,10000,10000,9)
        self.p._step(plan,self.now,10000,10000,9,"KRX",q=q0)
        buys=self.svc.engine.buy_fills
        self.assertEqual(plan["scenario"],1)
        self.assertEqual(len(self.svc.engine.orders),1)
        self.assertEqual(buys[0]["qty"],9)
        t1=self.now+dt.timedelta(milliseconds=250)
        q1=q_at(t1,10010,10010,10)
        self.p._step(plan,t1,10010,10010,10,"KRX",q=q1)
        self.assertEqual(plan["scenario"],2)
        self.assertEqual(len(self.svc.engine.orders),1)
        t2=t1+dt.timedelta(milliseconds=250)
        q2=q_at(t2,10020,10020,50)
        self.p._step(plan,t2,10020,10020,50,"KRX",q=q2)
        self.assertEqual(plan["scenario"],3)
        self.assertEqual(len(self.svc.engine.orders),1)
        self.assertLessEqual(sum(float(x["paid"]) for x in buys),500000.01)
        self.assertLessEqual(max(x["price"] for x in buys),plus_ticks(10000,2))
        self.assertTrue(all(x["purpose"].startswith("D+1_V097_KRX_") for x in buys))

    def test_above_two_ticks_never_submit_and_wrong_day_blocked(self):
        from projectgmo.engine import Quote
        self.p.prepare("KRX",self.ready)
        plan=self.p.plans[("2026-11-17","005930","KRX")]
        plan["opening_price"]=10000
        q=Quote("005930",self.now.isoformat(),1,10030,10030,10020,100,50,"KRX",0)
        self.p._step(plan,self.now,10030,10030,100,"KRX",q=q)
        self.assertFalse(self.svc.engine.orders)
        tomorrow=self.now+dt.timedelta(days=1)
        self.p._step(plan,tomorrow,10000,10000,100,"KRX",q=q)
        self.assertFalse(self.svc.engine.orders)

    def test_sell_evidence_blocks_more_buy_orders(self):
        self.p.prepare("KRX",self.ready)
        plan=self.p.plans[("2026-11-17","005930","KRX")]
        plan["opening_price"]=10000
        self.svc.engine.fills.append({"code":"005930","time":self.now.isoformat(),"qty":1})
        self.p._step(plan,self.now,10000,10000,100,"KRX")
        self.assertEqual(plan["status"],"SOLD")
        self.assertEqual(len(self.svc.engine.orders),0)

    def test_nxt_preflight_needs_eligibility_and_real_depth(self):
        at=dt.datetime(2026,11,17,7,58,tzinfo=KST)
        self.assertEqual(self.p.prepare("NXT",at),())
        runner=SimpleNamespace(nxt_snapshot=SimpleNamespace(allows=lambda *a: True),
                               _subscribed_groups={"1":["005930_NX"]})
        self.p.runner=runner
        self.assertEqual(self.p.prepare("NXT",at),("005930",))
        self.assertFalse(self.svc.engine.buy_fills)

    def test_nxt_price_proved_by_first_trade_not_krx_placeholder(self):
        from projectgmo.engine import Quote
        self.p.runner=SimpleNamespace(nxt_snapshot=SimpleNamespace(allows=lambda *a:True),
                                      _subscribed_groups={"1":["005930_NX"]})
        before=dt.datetime(2026,11,17,7,58,tzinfo=KST)
        self.p.prepare("NXT",before)
        at=dt.datetime(2026,11,17,8,0,0,tzinfo=KST)
        plan=self.p.plans[("2026-11-17","005930","NXT")]
        plan["opening_price"]=10000
        self.svc.engine._d1_buy_window_ok=lambda order,when:order.time[:10]==when[:10]
        self.p._step(plan,at,10000,10000,5,"NXT")
        self.assertEqual(self.svc.engine.buy_fills[0]["qty"],5)
        self.assertEqual(self.svc.engine.buy_fills[0]["purpose"],"D+1_V097_NXT_S1")
        self.assertEqual(self.svc.engine.positions["005930"].qty,5)
        # 08:50 leaves no NXT order that could accidentally match a KRX print.
        self.p._clear_nxt(dt.datetime(2026,11,17,8,50,tzinfo=KST))
        active=[o for o in self.svc.engine._active_buys() if o.code=="005930"]
        self.assertEqual(active,[])

if __name__=="__main__":
    unittest.main()
