"""Synthetic 0.0.18 NEW LEGACY rev1 F6/F7 and existing-book held fast exit.

No user device positions or SQLite files. No fabricated stock/crypto fill.
"""
import datetime as dt
import threading
import time
import types
import unittest
from decimal import Decimal
from unittest.mock import patch

import ma_v067_policy as ma
from new_legacy_1118_policy import exit_signal,targets
from new_legacy_rev1_low_close import reconcile
from ma_fast_held_exit_v018 import dispatch_once

KST=dt.timezone(dt.timedelta(hours=9))
def clock(hour,minute,second=0):
    return dt.datetime(2026,11,19,hour,minute,second,tzinfo=KST)
def bar(start,low,close,high=None,source="UPBIT_CLOSED"):
    return {"start":start,"end":start+dt.timedelta(minutes=5),
            "low":low,"high":high if high is not None else max(low,close),
            "close":close,"source":source}

class MARevisionPolicy(unittest.TestCase):
    def setUp(self):
        self.entry=clock(20,9,51)
        self.s=ma.initial_up_exit_state("5m")
        self.s["entry_at"]=self.entry.isoformat()

    def test_pod_ask_595_bid_593_is_not_an_executable_first_sale(self):
        state,qty,reason=ma.up_exit(self.s,Decimal(593),Decimal(583),
                                   Decimal("342.88173237"),Decimal("342.88173237"),
                                   clock(20,10,26))
        self.assertFalse(reason)
        self.assertEqual(qty,Decimal(0))
        self.assertEqual(ma.up_targets(Decimal(583),"5m")[0],Decimal("594.66"))
        _,qty,reason=ma.up_exit(self.s,Decimal(595),Decimal(583),
                               Decimal("342.88173237"),Decimal("342.88173237"),
                               clock(20,10,26))
        self.assertEqual(reason,"MA_V1118_UP_FIRST_30PCT")
        self.assertGreater(qty,0)

    def test_revisiting_entry_after_above_without_confirmed_low_close_holds(self):
        self.s["seen_above_entry"]=True
        _,qty,reason=ma.up_exit(self.s,Decimal(583),Decimal(583),Decimal(100),
                               Decimal(100),clock(20,11))
        self.assertEqual((qty,reason),(Decimal(0),""))

    def test_official_entry_low_touch_following_close_below_triggers_full_stop(self):
        first=clock(20,5)
        rows=[bar(first,580,590,595),bar(clock(20,10),579,579,594)]
        s=reconcile(self.s,self.entry,"5m",rows,clock(20,15),
                    entry_bar_start=first.isoformat())
        self.assertEqual(s["rev1_reference_low"],"580")
        self.assertTrue(s["rev1_low_touched"])
        self.assertTrue(s["rev1_below_reference_low_confirmed"])
        self.assertEqual(s["rev1_stop_closed_bar"],clock(20,15).astimezone(dt.timezone.utc).isoformat())
        _,qty,reason=ma.up_exit(s,Decimal(580),Decimal(583),Decimal(100),
                               Decimal(100),clock(20,15,1))
        self.assertEqual((qty,reason),(Decimal(100),"MA_V018_PRE_FIRST_VERIFIED_LOW_CLOSE"))

    def test_touch_alone_no_stop_and_unverified_lookahead_candle_rejected(self):
        first=clock(20,5)
        rows=[bar(first,580,590,595),bar(clock(20,10),580,581,585)]
        s=reconcile(self.s,self.entry,"5m",rows,clock(20,15),
                    entry_bar_start=first.isoformat())
        self.assertFalse(s.get("rev1_below_reference_low_confirmed",False))
        _,qty,reason=ma.up_exit(s,Decimal(581),Decimal(583),Decimal(100),
                               Decimal(100),clock(20,15))
        self.assertEqual((qty,reason),(Decimal(0),""))
        bad=[bar(first,580,590,595,source="UNVERIFIED_CACHED"),
             bar(clock(20,10),579,579,590,source="UNVERIFIED_CACHED")]
        unchanged=reconcile(self.s,self.entry,"5m",bad,clock(20,15),
                            entry_bar_start=first.isoformat())
        self.assertFalse(unchanged.get("rev1_below_reference_low_confirmed",False))

    def test_future_unclosed_bars_and_revised_reference_low_are_not_traded(self):
        first=clock(20,5)
        data=[bar(first,580,590,595),bar(clock(20,10),579,579,595)]
        x=reconcile(self.s,self.entry,"5m",data,clock(20,11),
                    entry_bar_start=first.isoformat())
        self.assertFalse(x.get("rev1_below_reference_low_confirmed",False))
        y=reconcile(x,self.entry,"5m",
                    [bar(first,579,590,595),data[1]],clock(20,16),
                    entry_bar_start=first.isoformat())
        self.assertTrue(y.get("rev1_source_conflict"))
        self.assertFalse(y.get("rev1_below_reference_low_confirmed",False))

class TrendRevisionPolicy(unittest.TestCase):
    def test_trend_pre_first_retrace_only_holds_then_closed_below_sells(self):
        now=clock(20,15,1)
        s={**targets(583,"30m","trend"),"market":"coin",
           "qty":100.,"qty0":100.,"stage":0,
           "seen_above_entry":True}
        self.assertIsNone(exit_signal(s,583,now))
        s["rev1_below_reference_low_confirmed"]=True
        self.assertEqual(exit_signal(s,583,now),
                         ("NL1118_REV1_PRE_FIRST_REFERENCE_LOW_CLOSED",1.0))

    def test_reference_original_retrace_rule_unaffected(self):
        now=clock(20,15,1)
        s={**targets(583,"5m","reference",ref_low=580,ref_high=590),
           "market":"coin","qty":100.,"qty0":100.,"stage":0,
           "seen_above_entry":True,
           # 0.3.4 (#149): a coin GEN plan is armed only after entry +1.5 % (rte_armed_v0340).
           "rte_armed_v0340":True}
        self.assertEqual(exit_signal(s,583,now),
                         ("NL1118_PRE_FIRST_RETURN_TO_ENTRY",1.0))

class HeldFirstBookDispatch(unittest.TestCase):
    def test_fast_dispatch_only_new_fresh_original_bid_once(self):
        stamp=int(time.time()*1000)
        ledger=types.SimpleNamespace(_ma_v067_up_hold_symbols={"KRW-AAA"})
        book={"market":"KRW-AAA","timestamp":stamp,"bid":595,
              "ask":596,"bid_size":"100","ask_size":"100"}
        runner=types.SimpleNamespace(ma_ledger=ledger,
             last_book={"KRW-AAA":book},_lock=threading.RLock())
        seen={}
        hits=[]
        with patch("ma_v067_coin_runtime._process_v067_exits",
                   side_effect=lambda *args:hits.append(args)):
            self.assertEqual(dispatch_once(runner,seen),1)
            self.assertEqual(dispatch_once(runner,seen),0)
            self.assertEqual(len(hits),1)
            runner.last_book["KRW-AAA"]={**book,"timestamp":stamp-10000}
            self.assertEqual(dispatch_once(runner,seen),0)
            self.assertEqual(len(hits),1)

if __name__=="__main__":
    unittest.main()
