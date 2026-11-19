"""0.0.13 original D19 AT PENDING -> native PAPER BUY proof.

Regression for a genuine blocker in 0.0.12: native DStore.root is .../stock,
while signed score publisher's key is .../ARTITRADE_PHONE_MAIN_V070.
The old score worker lookup by store.root returned no score -> strict
NEW LEGACY positive-score check denied every BUY. No broker, REST or real
holdings are used by this deterministic source-proven test.
"""
from __future__ import annotations
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import market_research_v033 as mr
import at_market_score_gate_v107 as gate
import reference_1118_runtime as ref
from d_storage import DStore,KST
from d_v19_portfolio import D19PaperEngine

gate.install()
ref.install()

WHEN=dt.datetime(2026,11,18,13,28,5,tzinfo=KST)
CODE="990013"

class _FakeMarketPublisher:
    def __init__(self,score,stale=False):
        self.score=score
        self.stale=stale
    def snapshot(self):
        return {"stock":{"score":self.score,"stale":self.stale,
                         "market_open":True,"source":"TEST_SIGNED_SCORE_V033"}}

class _VerifiedDepth:
    def available(self,code,venue,at,price,qty):
        if code!=CODE or venue!="KRX":return None,"UNVERIFIED_VENUE"
        return (int(price),max(10000,int(qty))),None
    def reserve(self,*args):
        return True
    def available_sell(self,*args):return 100000
    def reserve_sell(self,*args):return True

class SignedPositiveToNativeExecution(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix="at-0-0-13-regression-")
        self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)/"ARTITRADE_PHONE_MAIN_V070"
        self.store=DStore(self.root/"stock")
        self.engine=D19PaperEngine(self.store)
        self.engine.book=_VerifiedDepth()
        with self.store.lock,self.store.connect() as c:
            c.execute("""INSERT INTO bars(code,market,frame,ts,open,high,low,close,volume,source)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
               (CODE,"KOSDAQ","30m","20261117133000",2600,3200,2500,2800,15000,
                "SYNTHETIC_KRX_0B_COMPLETED"))
            c.execute("""INSERT INTO signals(event_key,ts,tf,code,name,market,ref_ts,
               buy_price,target,score,features,decision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
               ("SIM_013","20261117140000","30m",CODE,"REGRESSION_013","KOSDAQ",
                "20261117133000",2500,2800,69.,
                '{"ranking_engine":"AT_DIVERSE_1_1_ACTIVE"}',"PENDING"))
            signal_id=c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("""INSERT INTO v19_orders(signal_id,code,tf,venue,score,limit_won,
                since_ts,status) VALUES(?,?,?,?,?,?,?,?)""",
                (signal_id,CODE,"30m","KRX",69.,2600,"20261117140000","PENDING"))
    def _buy(self,score,stale=False):
        publisher=_FakeMarketPublisher(score,stale)
        with patch.dict(mr._WORKERS,{str(self.root.resolve()):publisher},clear=True),\
            patch("d_v19_portfolio.live_session",return_value=True):
            # Fail closed if an unresolved publisher is silently promoted
            # into a false positive. The very same ORIGINAL native 0D/FOK
            # account method owns all accepted fills and cash.
            evidence=gate.score_evidence(self.store.root,"stock")
            with self.store.lock,self.store.connect() as c:
                row=c.execute("SELECT * FROM v19_orders WHERE status='PENDING'").fetchone()
                if row is None:return evidence,None
                outcome=self.engine._buy_limit_depth(c,row,WHEN,"KRX",2600,1000)
                return evidence,outcome
    def _state(self):
        with self.store.connect() as c:
            executions=list(c.execute("SELECT side,qty,price,position_id FROM executions ORDER BY id"))
            positions=list(c.execute("SELECT id,qty,exit_policy FROM positions ORDER BY id"))
            order=c.execute("SELECT status FROM v19_orders").fetchone()["status"]
            plan=c.execute("""SELECT COUNT(*) FROM sqlite_master WHERE type='table'
                AND name='nl1118_reference_positions'""").fetchone()[0]
            return executions,positions,order,plan
    def test_01_positive_signed_score_goes_from_pending_to_real_native_buy_journal(self):
        evidence,result=self._buy(24)
        self.assertEqual(evidence["basis"],"SIGNED_MARKET_SCORE_V033",evidence)
        self.assertEqual(evidence["score"],24)
        self.assertTrue(result[0],result)
        executions,positions,status,plan=self._state()
        self.assertEqual(status,"BOUGHT")
        self.assertEqual(len(positions),1)
        self.assertEqual(len(executions),1)
        self.assertEqual(executions[0]["side"],"BUY")
        self.assertEqual(positions[0]["exit_policy"],"NEW_LEGACY_1118")
        self.assertEqual(plan,1)
        with self.store.connect() as c:
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM nl1118_reference_positions").fetchone()[0],1)
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM nl1118_resting_sells").fetchone()[0],2)
    def test_02_score_zero_keeps_original_order_pending(self):
        evidence,result=self._buy(0)
        self.assertEqual(evidence["score"],0)
        self.assertFalse(result[0],result)
        self.assertEqual(self._state()[2],"PENDING")
        self.assertEqual(len(self._state()[0]),0)
    def test_03_negative_score_keeps_original_order_pending(self):
        evidence,result=self._buy(-12)
        self.assertEqual(evidence["score"],-12)
        self.assertFalse(result[0],result)
        self.assertEqual(self._state()[2],"PENDING")
        self.assertEqual(len(self._state()[0]),0)
    def test_04_stale_score_cannot_fill_from_past_positive(self):
        evidence,result=self._buy(30,stale=True)
        self.assertIsNone(evidence["score"])
        self.assertFalse(result[0],result)
        self.assertEqual(self._state()[2],"PENDING")
        self.assertEqual(len(self._state()[0]),0)
    def test_05_no_signed_score_publisher_keeps_pending(self):
        with patch.dict(mr._WORKERS,{},clear=True),\
             patch("d_v19_portfolio.live_session",return_value=True):
            evidence=gate.score_evidence(self.store.root,"stock")
            with self.store.lock,self.store.connect() as c:
                row=c.execute("SELECT * FROM v19_orders WHERE status='PENDING'").fetchone()
                result=self.engine._buy_limit_depth(c,row,WHEN,"KRX",2600,1000)
        self.assertEqual(evidence["basis"],"SCORE_PUBLISHER_NOT_READY")
        self.assertFalse(result[0],result)
        self.assertEqual(self._state()[2],"PENDING")
    def test_06_stock_worker_root_is_not_stock_folder(self):
        self.assertEqual(gate._score_worker_root(self.store.root,"stock"),self.root.resolve())
        self.assertEqual(gate._score_worker_root(self.root,"stock"),self.root.resolve())
        self.assertEqual(gate._score_worker_root(self.root/"coin"/"base","coin"),
                         (self.root/"coin"/"base").resolve())

if __name__=="__main__":
    unittest.main(verbosity=2)
