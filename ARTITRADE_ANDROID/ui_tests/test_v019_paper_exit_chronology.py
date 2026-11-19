"""Chronology audit rejects lookahead profits and cannot write native wallets."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app/src/main/python"))
from paper_exit_chronology_v019 import audit

BASE={"owner":"coin/ma","epoch":"paper-20261119","position_id":11,
      "entry_at":"2026-11-19T09:00:00+09:00","entry_qty":100,
      "entry_price":100,"mode":"PAPER","venue":"UPBIT",
      "stages":[{"stage":1,"price":104,"qty":30,
                 "source":"ORIGINAL_NATIVE_PREBUY_COMMITTED"},
                {"stage":2,"price":108,"qty":35,
                 "source":"ORIGINAL_NATIVE_PREBUY_COMMITTED"}]}

def event(t,kind,**kw):
    return {"owner":BASE["owner"],"position_id":11,"epoch":BASE["epoch"],
            "venue":"UPBIT","ts":"2026-11-19T09:"+t+"+09:00",
            "kind":kind,"source":("ORIGINAL_NATIVE_RECEIPT" if
                    kind not in ("TRADE_TICK","OHLC") else
                    "AUTHENTICATED_SAME_VENUE_TICK" if kind=="TRADE_TICK"
                    else "AUTHENTICATED_CLOSED_OHLC"),**kw}

class ChronologyProof(unittest.TestCase):
    def test_loss_first_then_rally_never_tp(self):
        r=audit(BASE,[event("02:00","HARD_STOP"),
                      event("03:00","TRADE_TICK",price=105,tradable=True)])
        self.assertEqual(r["status"],"CHRONOLOGY_AUDITED_NO_SYNTHETIC_FILL")
        self.assertEqual(r["remaining_qty"],0)
        self.assertEqual(len(r["chronology"]),1)
        self.assertFalse(r["native_execution_created"])

    def test_prior_native_tp1_then_stop_disallows_tp2(self):
        r=audit(BASE,[event("01:00","NATIVE_TP1",qty=30),
                      event("02:00","FORCED_FULL_EXIT"),
                      event("03:00","TRADE_TICK",price=110,tradable=True)])
        self.assertEqual(r["remaining_qty"],0)
        self.assertEqual([x["kind"] for x in r["chronology"]],
                         ["NATIVE_TP1","FORCED_FULL_EXIT"])

    def test_native_tp1_and_native_tp2(self):
        r=audit(BASE,[event("01:00","NATIVE_TP1",qty=30),
                      event("02:00","NATIVE_TP2",qty=35)])
        self.assertEqual(r["remaining_qty"],35)
        self.assertEqual(r["status"],"CHRONOLOGY_AUDITED_NO_SYNTHETIC_FILL")
        self.assertFalse(r["wallet_written"])

    def test_same_timestamp_stop_and_tp_unverified(self):
        r=audit(BASE,[event("01:00","TRADE_TICK",price=105,tradable=True),
                      event("01:00","HARD_STOP")])
        self.assertEqual(r["status"],"EXIT_SEQUENCE_UNVERIFIED")
        self.assertFalse(r["inferred_profit_booked"])

    def test_ohlc_cannot_infer_tp(self):
        r=audit(BASE,[event("01:00","OHLC",high=106,low=98)])
        self.assertEqual(r["status"],"EXIT_SEQUENCE_UNVERIFIED")
        self.assertFalse(r["native_execution_created"])

    def test_strict_above_shadow_only_and_no_second(self):
        r=audit(BASE,[event("01:00","TRADE_TICK",price=105,tradable=True)])
        self.assertEqual(r["status"],"SHADOW_ASSUMPTION_NOT_SETTLED")
        self.assertEqual(r["remaining_qty"],100)
        self.assertEqual(r["chronology"][0]["kind"],"TP1")
        self.assertFalse(r["wallet_written"])

    def test_equal_needs_book_proof(self):
        r=audit(BASE,[event("01:00","TRADE_TICK",price=104,tradable=True)])
        self.assertEqual(r["chronology"][0]["state"],"LIMIT_TOUCH_NEEDS_BOOK_AND_QUEUE_PROOF")
        self.assertFalse(r["native_execution_created"])

    def test_broker_mode_denied(self):
        r=audit(dict(BASE,mode="REAL"),[event("01:00","TRADE_TICK",price=110)])
        self.assertEqual(r["status"],"SOURCE_POSITION_UNVERIFIED")

    def test_missing_epoch_and_cross_venue_denied(self):
        self.assertEqual(audit(dict(BASE,epoch=""),[])["status"],
                         "SOURCE_POSITION_UNVERIFIED")
        self.assertEqual(audit(BASE,[dict(event("01:00","HARD_STOP"),venue="KRX")])["status"],
                         "SOURCE_EVENT_UNVERIFIED")

    def test_pre_buy_high_ignored(self):
        r=audit(BASE,[event("00:00","TRADE_TICK",price=110,tradable=True)])
        self.assertEqual(r["remaining_qty"],100)
        self.assertEqual(r["chronology"],[])

    def test_out_of_order_sorted_by_authenticated_time(self):
        r=audit(BASE,[event("05:00","TRADE_TICK",price=110,tradable=True),
                      event("02:00","HARD_STOP")])
        self.assertEqual(r["remaining_qty"],0)
        self.assertEqual(r["chronology"][0]["kind"],"HARD_STOP")

    def test_no_native_plan(self):
        r=audit(dict(BASE,stages=[]),[])
        self.assertEqual(r["status"],"PERSISTED_TARGET_PLAN_UNVERIFIED")


if __name__=="__main__":
    unittest.main()
