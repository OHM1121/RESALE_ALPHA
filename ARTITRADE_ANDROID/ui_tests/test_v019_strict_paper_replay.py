"""Deterministic source-stamped tests for all eight original PAPER wallets."""
import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app/src/main/python"))
from paper_exit_replay_v019 import replay,GUARDS,OWNERS

BUY="2026-11-19T09:00:00+09:00"
def position(owner):
    return {
      "owner":owner,"epoch":"E1","position_id":17,
      "mode":"PAPER","status":"OPEN","venue":"KRX" if owner.startswith("stock") else "UPBIT",
      "buy_at":BUY,"original_buy_receipt":"ORIGINAL_BUY17",
      "buy_price":100,"original_qty":100,"remaining_qty":100,
      "broker_order_present":False,
      "original_full_exit_seen":False,
      "stages":[
        {"stage":1,"limit":104,"qty":30,"filled_qty":0,"original_qty":100,
         "policy_source":"ORIGINAL_NATIVE_BUY_PLAN","created_at":BUY,"broker_order_id":""},
        {"stage":2,"limit":108,"qty":35,"filled_qty":0,"original_qty":100,
         "policy_source":"ORIGINAL_NATIVE_BUY_PLAN","created_at":BUY,"broker_order_id":""}]}
def ev(p,seq,seconds,kind="CLOSED_1M",**kw):
    start=dt.datetime.fromisoformat(BUY)
    at=(start+dt.timedelta(seconds=seconds)).isoformat()
    e={"owner":p["owner"],"epoch":p["epoch"],"position_id":p["position_id"],
       "venue":p["venue"],"sequence":seq,"ts":at,
       "original_feed_event_id":"verified-"+str(seq),"kind":kind,
       "source":"AUTHENTICATED_CLOSED_ONE_MINUTE" if kind=="CLOSED_1M" else
          "AUTHENTICATED_EXCHANGE_TICK" if kind=="TRADE" else
          "ORIGINAL_POLICY_EVALUATION",
       "native_protections_checked":list(GUARDS[p["owner"]]),
       "limit_order_eligible":True,"final":True,
       "original_stop_limit":85}
    e.update(kw)
    return e

class NativeChronology(unittest.TestCase):
    def test_all_eight_original_owners_do_not_change_wallet(self):
        for owner in OWNERS:
            with self.subTest(owner=owner):
                p=position(owner)
                events=[ev(p,1,40,high=106,low=95),
                        ev(p,2,80,high=110,low=95)]
                result=replay(p,events)
                self.assertEqual(result["status"],"PAPER_ASSUMPTION_CANDIDATES_VERIFIED")
                self.assertEqual([x["stage"] for x in result["stages"]],[1,2])
                self.assertEqual([x["qty"] for x in result["stages"]],["30","35"])
                self.assertEqual(result["stages"][0]["limit"],"104")
                self.assertFalse(result["wallet_mutation"])
                self.assertFalse(result["native_execution_created"])
                self.assertFalse(result["broker_order_created"])

    def test_stop_earlier_than_later_high(self):
        p=position("coin/ma")
        a=replay(p,[ev(p,1,10,high=99,low=80),
                    ev(p,2,55,high=115,low=95)])
        self.assertEqual(a["status"],"STOP_FIRST_NO_PROFIT_CATCHUP")

    def test_tp_then_protective_stop_later_never_books_profit(self):
        p=position("coin/ma")
        a=replay(p,[ev(p,1,10,high=106,low=95),
                    ev(p,2,50,"HARD_STOP")])
        self.assertEqual(a["status"],"PRIOR_PROTECTIVE_EXIT_PREVENTS_CATCHUP")
        self.assertEqual(a["candidates"],[])

    def test_same_candle_limit_and_stop_ambiguous(self):
        p=position("stock/at")
        a=replay(p,[ev(p,1,30,high=109,low=84)])
        self.assertEqual(a["status"],"EXIT_SEQUENCE_UNVERIFIED")

    def test_touch_equal_not_eligible(self):
        p=position("stock/gmo")
        a=replay(p,[ev(p,1,30,high=104,low=90)])
        self.assertEqual(a["status"],"NO_VERIFIED_POST_BUY_STRICT_OVERSHOOT")

    def test_wrong_venue_and_wrong_owner(self):
        p=position("stock/cw")
        for key,val in [("venue","UPBIT"),("owner","coin/ma"),
                        ("position_id",19),("epoch","E2")]:
            v=ev(p,1,30,high=105,low=95)
            v[key]=val
            self.assertEqual(replay(p,[v])["status"],"SOURCE_EVENT_UNVERIFIED")

    def test_gapped_or_reordered_tape_blocks(self):
        p=position("coin/trend")
        good=ev(p,1,30,high=103,low=95)
        gap=ev(p,3,40,high=105,low=95)
        self.assertEqual(replay(p,[good,gap])["status"],"TAPE_EVENT_SEQUENCE_GAP")
        late=ev(p,2,20,high=106,low=95)
        self.assertEqual(replay(p,[good,late])["status"],"TAPE_TIMESTAMP_OUT_OF_ORDER")
        slow=ev(p,2,170,high=108,low=95)
        self.assertEqual(replay(p,[good,slow])["status"],"TAPE_TIME_GAP")

    def test_prebuy_and_unknown_policy_rejected(self):
        p=position("stock/ma")
        old=ev(p,1,-10,high=105,low=95)
        self.assertEqual(replay(p,[old])["status"],"PRE_BUY_PRICE_NOT_ELIGIBLE")
        cur=ev(p,1,20,high=105,low=95)
        cur["native_protections_checked"]=[]
        self.assertEqual(replay(p,[cur])["status"],"ORIGINAL_PROTECTIVE_RULES_UNVERIFIED")

    def test_real_fills_and_closed_position_protected(self):
        p=position("stock/at")
        real=dict(p,mode="REAL")
        self.assertEqual(replay(real,[ev(p,1,20,high=105,low=90)])["status"],
                         "UNSUPPORTED_OR_REAL_POSITION")
        closed=dict(p,status="CLOSED")
        self.assertEqual(replay(closed,[ev(p,1,20,high=105,low=90)])["status"],
                         "SOURCE_BUY_PROVENANCE_UNVERIFIED")
        past=dict(p,original_full_exit_seen=True)
        self.assertEqual(replay(past,[ev(p,1,20,high=105,low=90)])["status"],
                         "ORIGINAL_FULL_EXIT_PROTECTED")

    def test_native_partial_stages_dont_reaward_original_amount(self):
        p=position("stock/ma")
        p["remaining_qty"]=70
        p["original_stage_receipts_verified"]=True
        p["stages"][0]["filled_qty"]=30
        s=replay(p,[ev(p,1,30,high=109,low=95)])
        self.assertEqual(s["status"],"PAPER_ASSUMPTION_CANDIDATES_VERIFIED")
        self.assertEqual(len(s["stages"]),1)
        self.assertEqual(s["stages"][0]["stage"],2)

    def test_exact_timestamp_stop_and_profit_ambiguous(self):
        p=position("coin/general")
        q=ev(p,1,20,"TRADE",price=110)
        stop=ev(p,2,20,"HARD_STOP")
        self.assertEqual(replay(p,[q,stop])["status"],"EXIT_SEQUENCE_UNVERIFIED")

if __name__=="__main__":unittest.main()
