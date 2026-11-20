"""v097 D+1 entry scenario tests; PAPER-only original ProjectGMO wallet."""
import base64,datetime as dt,io,math,sys,tempfile,threading,unittest,zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]/"app/src/main"
sys.path.insert(0,str(ROOT/"python"))
from gmo_d1_preplan_v097 import Preplanner,PHASE,_valid_phase,_venue_phase,KST
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

    def test_csat_2026_nxt_closed_and_delayed_krx_d1(self):
        k=lambda hour,minute:dt.datetime(2026,11,19,hour,minute,tzinfo=KST)
        self.assertIsNone(_venue_phase("NXT",k(8,0)))
        self.assertFalse(_valid_phase("NXT",k(7,58),True))
        self.assertFalse(_valid_phase("NXT",k(8,0)))
        self.assertFalse(_valid_phase("KRX",k(8,50),True))
        self.assertTrue(_valid_phase("KRX",k(9,50),True))
        self.assertTrue(_valid_phase("KRX",k(9,59),True))
        self.assertFalse(_valid_phase("KRX",k(9,59)))
        self.assertTrue(_valid_phase("KRX",k(10,0)))
        self.assertTrue(_valid_phase("KRX",k(16,29)))
        self.assertFalse(_valid_phase("KRX",k(16,30)))
        self.assertEqual(_venue_phase("KRX",k(10,0)),("095000","100000","163000"))
        self.svc.engine.signals["005930"].event_date="2026-11-18"
        self.svc.engine.signals["005930"].entry_date="2026-11-19"
        # Original D+1 proof remains authoritative; this does not issue BUY.
        from unittest.mock import patch
        with patch("d_gmo_runtime._next_day",return_value="2026-11-19"):
            self.assertEqual(self.p.prepare("NXT",k(7,58)),())
            self.assertEqual(self.p.prepare("KRX",k(8,50)),())
            self.assertEqual(self.p.prepare("KRX",k(9,50)),("005930",))
        p=self.p.plans[("2026-11-19","005930","KRX")]
        self.assertIn("10:00:00",p["opening_at"])
        self.assertEqual(p["status"],"PREPARED")
        self.assertEqual(p["scenario"],0)
        self.assertIsNone(p["opening_price"])
        self.assertEqual(self.svc.engine.buy_fills,[])

    def test_late_recovery_can_rebuild_real_d1_with_no_official_open(self):
        from projectgmo.engine import Quote
        at=dt.datetime(2026,11,17,11,7,0,tzinfo=KST)
        # No plan exists after a simulated app restart. This must NOT be
        # mistaken for an absence of the completed D-day signal.
        self.assertEqual(self.p.plans,{})
        self.assertEqual(self.p.prepare("KRX",at),())
        self.assertEqual(self.p.prepare("KRX",at,recovery=True),("005930",))
        plan=self.p.plans[("2026-11-17","005930","KRX")]
        self.assertTrue(plan["late_recovery"])
        self.assertIsNone(plan["opening_price"])
        # A genuinely received current KRX trade+ask below VERIFIED D close
        # may trigger the native owner even though today's open is unknown.
        q=Quote("005930",at.isoformat(),1,9870,9880,9860,100,100,"KRX",0)
        self.p._step(plan,at,9870,9880,100,"KRX",q=q)
        self.assertEqual(plan["entry_reference_source"],"VERIFIED_D_CLOSE_ONLY")
        self.assertTrue(self.svc.engine.orders)
        self.assertLessEqual(max(o.limit for o in self.svc.engine.orders.values()),9900)

    def test_late_recovery_must_not_invent_open_or_buy_above_d_close(self):
        at=dt.datetime(2026,11,17,11,7,0,tzinfo=KST)
        self.p.prepare("KRX",at,recovery=True)
        plan=self.p.plans[("2026-11-17","005930","KRX")]
        self.p._step(plan,at,10100,10100,100,"KRX")
        self.assertEqual(plan["status"],"WAIT_OFFICIAL_OPEN_OR_D_CLOSE_TOUCH")
        self.assertIsNone(plan["opening_price"])
        self.assertEqual(self.svc.engine.orders,{})

    def test_no_new_entry_after_d_plus_one(self):
        at=dt.datetime(2026,11,18,11,7,0,tzinfo=KST)
        self.assertEqual(self.p.prepare("KRX",at,recovery=True),())
        self.assertFalse(self.svc.engine.buy_fills)

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

    def test_nxt_partial_then_krx_at_9_preserves_one_budget(self):
        from projectgmo.engine import Quote
        self.p.runner=SimpleNamespace(nxt_snapshot=SimpleNamespace(allows=lambda *a:True),
                                      _subscribed_groups={"1":["005930_NX"]})
        nstart=dt.datetime(2026,11,17,7,58,tzinfo=KST)
        self.p.prepare("NXT",nstart)
        nxt=dt.datetime(2026,11,17,8,0,tzinfo=KST)
        plan_nxt=self.p.plans[("2026-11-17","005930","NXT")]
        plan_nxt["opening_price"]=10000
        native_window=self.svc.engine._d1_buy_window_ok
        self.svc.engine._d1_buy_window_ok=lambda order,when:(
            order.time[:10]==when[:10] if "V097_NXT" in order.purpose
            else native_window(order,when))
        self.p._step(plan_nxt,nxt,10000,10000,5,"NXT")
        self.assertEqual(self.svc.engine.positions["005930"].qty,5)
        self.p._clear_nxt(dt.datetime(2026,11,17,8,50,tzinfo=KST))
        self.assertEqual(self.p.prepare("KRX",self.ready),("005930",))
        krx_plan=self.p.plans[("2026-11-17","005930","KRX")]
        krx_plan["opening_price"]=10020
        q=Quote("005930",self.now.isoformat(),1,10020,10020,10010,40,30,"KRX",0)
        self.p._step(krx_plan,self.now,10020,10020,40,"KRX",q=q)
        self.assertGreater(self.svc.engine.positions["005930"].qty,5)
        self.assertLessEqual(sum(f["paid"] for f in self.svc.engine.buy_fills),500000.01)

    def test_krx_recovers_canceled_nxt_intent_after_restart(self):
        from projectgmo.engine import Quote
        eng=self.svc.engine
        nxt=dt.datetime(2026,11,17,8,0,tzinfo=KST)
        o=eng._order("005930","BUY","D+1_V097_NXT_S1",10000,10000,45,nxt.isoformat())
        eng.used.add("005930|2026-11-17")
        # The service restarted; there are NO transient NXT preplan objects.
        self.p._clear_nxt(dt.datetime(2026,11,17,8,50,tzinfo=KST))
        self.assertEqual(o.state,"CANCELED")
        self.assertEqual(self.p.prepare("KRX",self.ready),("005930",))
        plan=self.p.plans[("2026-11-17","005930","KRX")]
        plan["opening_price"]=10000
        q=Quote("005930",self.now.isoformat(),1,10000,10000,9990,15,20,"KRX",0)
        self.p._step(plan,self.now,10000,10000,15,"KRX",q=q)
        self.assertGreater(self.svc.engine.positions["005930"].qty,0)
        self.assertLessEqual(sum(f["paid"] for f in eng.buy_fills),500000.01)

    def test_unverified_zero_trade_volume_cannot_anchor_open(self):
        from gmo_d1_preplan_v097 import _verified
        tick=("005930","KOSPI","TEST","202611170800",10000,0,
              self.now.isoformat(),"NXT",self.now.isoformat(),10000,9990)
        self.assertIsNone(_verified(tick,self.now))

if __name__=="__main__":
    unittest.main()
