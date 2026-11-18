"""Offline eight-strategy prebuy journal, target honesty and fresh epoch."""
from __future__ import annotations
import sqlite3
import tempfile
import unittest
from pathlib import Path

from execution_entry_plans_v107 import record_plan,NAME,SCHEMA
from fresh_epoch_reset_v106 import preview,reset,CONFIRM

WALLETS={"stock":("at","ma","close","gmo","trend"),
         "coin":("general","ma","trend")}

class EntryPlanEpochTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/"files"/"ARTITRADE_PHONE_MAIN_V070"
        self.root.mkdir(parents=True)

    def test_all_eight_record_prebuy_reason_and_known_targets(self):
        for market,strategies in WALLETS.items():
            for number,strategy in enumerate(strategies):
                known=number%2==0
                self.assertTrue(record_plan(
                    self.root,market,strategy,"5m",market+"_"+strategy,
                    "20261118T1400",reason="ORIGINAL_SIGNAL_"+strategy,
                    candidate_target="1020" if known else None,
                    phase="PREBUY_CONFIRMED_BAR",
                    extra={"proof_source":"ACTUAL_COMPLETED_BAR",
                           "fill_inferred":False}))
        with sqlite3.connect(self.root/NAME) as conn:
            rows=conn.execute("SELECT market,strategy,buy_reason,candidate_target,"
                              "planned_exit_target FROM entry_plans").fetchall()
        self.assertEqual(len(rows),8)
        self.assertEqual({(m,s) for m,s,*_ in rows},
                         {(m,s) for m,ss in WALLETS.items() for s in ss})
        self.assertTrue(all(str(reason).startswith("ORIGINAL_SIGNAL_")
                            for m,s,reason,*_ in rows))
        self.assertTrue(all(exit_target is None
                            for _,_,_,_,exit_target in rows))

    def test_known_exit_target_cannot_be_erased_by_unknown(self):
        payload=dict(root=self.root,market="stock",strategy="at",frame="30m",
                     symbol="005930",reference="20261118T0900",
                     reason="SIGNAL",phase="PREBUY_CONFIRMED_BAR")
        self.assertTrue(record_plan(**payload,exit_target="2800",
                                    target_source="ORIGINAL_TARGET"))
        self.assertTrue(record_plan(**payload,exit_target=None))
        with sqlite3.connect(self.root/NAME) as conn:
            row=conn.execute("SELECT planned_exit_target,exit_target_source,"
                             "COUNT(*) FROM entry_plans").fetchone()
        self.assertEqual(row[:2],("2800","ORIGINAL_TARGET"))
        self.assertEqual(row[2],1)

    def test_one_shot_reset_includes_entry_preplans_and_new_epoch_is_safe(self):
        record_plan(self.root,"coin","general","5m","KRW-QUID","old",
                    reason="ORIGINAL_OLD_SIGNAL")
        self.assertIn(NAME,preview(self.root)["files_to_delete"])
        outcome=reset(self.root,confirmation=CONFIRM,engine_stopped=True,
                      live_orders_disarmed=True)
        self.assertEqual(outcome["status"],"COMMITTED")
        self.assertFalse((self.root/NAME).exists())
        record_plan(self.root,"coin","general","5m","KRW-QUID","new",
                    reason="ORIGINAL_NEW_SIGNAL")
        with sqlite3.connect(self.root/NAME) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM entry_plans").fetchone()[0],1)
        again=reset(self.root,confirmation=CONFIRM,engine_stopped=True,
                    live_orders_disarmed=True)
        self.assertEqual(again["status"],"ALREADY_COMMITTED")
        self.assertTrue((self.root/NAME).is_file())

if __name__=="__main__":
    unittest.main()
