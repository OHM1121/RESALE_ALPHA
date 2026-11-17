"""v0.8.0 GMO D+1 07:50 / 08:50 API-free preflight regressions.

Use the unchanged archived projectgmo/Engine and no broker API. Prices and
confirmed fills remain unknown until an actual venue opens.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from gmo_d1_preplan_v097 import Preplanner, PHASE, _valid_phase, KST


class OriginalService:
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
        self.persisted=0
        self._persist=lambda *a,**kw:setattr(self,"persisted",self.persisted+1)
        self.engine.signals["005930"]=SimpleNamespace(
            code="005930",name="TEST",event_date="2026-11-16",
            entry_date="2026-11-17",event_close=9900,session_type="SHORT")


class EarlyPreflight(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.service=OriginalService(self.tmp.name)
        self.p=Preplanner(self.service)
        self.nxt=self.at(7,50)
        self.krx=self.at(8,50)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def at(h,m=0,s=0):
        return dt.datetime(2026,11,17,h,m,s,tzinfo=KST)

    def test_exact_preparation_boundaries_keep_original_open_times(self):
        self.assertEqual(PHASE["NXT"],("075000","080000","085000"))
        self.assertEqual(PHASE["KRX"],("085000","090000","153000"))
        for venue,early in (("NXT",self.nxt),("KRX",self.krx)):
            self.assertFalse(_valid_phase(venue,early-dt.timedelta(seconds=1),True))
            self.assertTrue(_valid_phase(venue,early,True))
            self.assertTrue(_valid_phase(venue,early+dt.timedelta(minutes=9,seconds=59),True))
            self.assertFalse(_valid_phase(venue,early+dt.timedelta(minutes=10),True))
            self.assertFalse(_valid_phase(venue,early,False))
            self.assertTrue(_valid_phase(venue,early+dt.timedelta(minutes=10),False))

    def test_0750_nxt_provisional_plan_does_not_require_api_or_buy(self):
        self.assertEqual(self.p.prepare("NXT",self.nxt),())
        p=self.p.plans[("2026-11-17","005930","NXT")]
        self.assertEqual(p["status"],"WAIT_NXT_ELIGIBILITY")
        self.assertEqual(p["prepared_at"],self.nxt.isoformat(timespec="seconds"))
        self.assertEqual(p["opening_at"],self.at(8).isoformat())
        self.assertFalse(p["requires_live_price_at_prepare"])
        self.assertTrue(p["opening_quote_required_for_final_price_and_qty"])
        self.assertIsNone(p["opening_price"])
        self.assertEqual(p["max_budget_won"],500000)
        self.assertEqual(p["indicative_remaining_budget_won"],500000)
        self.assertEqual(len(p["scenario_templates"]),3)
        self.assertEqual([x["scenario"] for x in p["scenario_templates"]],[1,2,3])
        self.assertTrue(all(x["limit_price"] is None and x["quantity"] is None
                            for x in p["scenario_templates"]))
        self.assertFalse(self.service.engine.orders)
        self.assertFalse(self.service.engine.buy_fills)
        self.assertEqual(self.p.prepare("NXT",self.at(7,51)),())
        self.assertEqual(len(self.p.plans),1)

    def test_nxt_eligibility_cache_can_upgrade_without_losing_0750_plan(self):
        self.p.prepare("NXT",self.nxt)
        self.p.runner=SimpleNamespace(nxt_snapshot=SimpleNamespace(
            allows=lambda code,day:code=="005930" and day=="20261117"),
            _subscribed_groups={})
        self.assertEqual(self.p.prepare("NXT",self.at(7,52)),("005930",))
        p=self.p.plans[("2026-11-17","005930","NXT")]
        self.assertTrue(p["nxt_eligibility_verified"])
        self.assertEqual(p["status"],"PREPARED")
        self.assertEqual(p["prepared_at"],self.nxt.isoformat(timespec="seconds"))
        self.assertEqual(len(self.service.engine.orders),0)
        self.assertEqual(self.p.prepare("NXT",self.at(7,54)),())

    def test_0850_krx_plan_ready_even_without_any_live_quote(self):
        self.assertEqual(self.p.prepare("KRX",self.krx),("005930",))
        p=self.p.plans[("2026-11-17","005930","KRX")]
        self.assertEqual(p["opening_at"],self.at(9).isoformat())
        self.assertEqual(p["status"],"PREPARED")
        self.assertEqual(p["prepared_at"],self.krx.isoformat(timespec="seconds"))
        self.assertFalse(self.service.opens)
        self.assertFalse(self.service.priority_books)
        self.assertFalse(self.service.engine.orders)
        self.assertEqual(self.p.prepare("KRX",self.at(8,59,59)),())
        self.assertEqual(len(self.p.plans),1)

    def test_krx_still_only_orders_after_genuine_open_plus_2_cap(self):
        from projectgmo.engine import Quote
        self.p.prepare("KRX",self.krx)
        now=self.at(9)
        plan=self.p.plans[("2026-11-17","005930","KRX")]
        plan["opening_price"]=10000
        q=Quote("005930",now.isoformat(),1,10000,10000,9990,10,10,"KRX",0)
        self.p._step(plan,now,10000,10000,10,"KRX",q=q)
        self.assertEqual(plan["scenario"],1)
        self.assertEqual([x["limit_price"] for x in plan["scenario_templates"]],
                         [10000,10010,10020])
        self.assertTrue(all(x["quantity"]==plan["finalized_max_quantity_at_plus2_ticks"]
                            for x in plan["scenario_templates"]))
        self.assertLessEqual(sum(x["paid"] for x in self.service.engine.buy_fills),500000)
        self.assertEqual(len(self.service.engine.orders),1)

    def test_no_wrong_day_signals_at_0750_or_0850(self):
        self.service.engine.signals["005930"].entry_date="2026-11-18"
        self.assertEqual(self.p.prepare("NXT",self.nxt),())
        self.assertEqual(self.p.prepare("KRX",self.krx),())
        self.assertEqual(self.p.plans,{})


if __name__=="__main__":
    unittest.main()
