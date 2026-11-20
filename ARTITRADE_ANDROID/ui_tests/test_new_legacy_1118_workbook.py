"""Exact November 18 workbook regression. Synthetic quotes; NO order placed."""
from __future__ import annotations
import datetime as dt
import math
import unittest
from pathlib import Path

import new_legacy_1118_policy as p
import ma_v067_policy as ma
from market_trend_1118_runtime import RsiWatch,_mark_rsi_watch
from types import SimpleNamespace
import tempfile

KST=dt.timezone(dt.timedelta(hours=9))
NOW=dt.datetime(2026,11,18,9,0,tzinfo=KST)

class WorkbookNovember18(unittest.TestCase):
    def test_positive_current_score_only_no_old_reference_age_restriction(self):
        for score in (0,-.01,-10,-100,None,"",float("nan")):
            self.assertFalse(p.validate_stock_score(score))
        self.assertTrue(p.validate_stock_score(.01))
        self.assertTrue(p.validate_stock_score(100.0))
    def test_reference_low_minus_complete_high_low_range(self):
        self.assertEqual(p.at_hard_stop(90,100),80.)
        with self.assertRaises(ValueError):p.at_hard_stop(100,90)
    def test_four_frame_target_prices_and_allocation(self):
        first={"5m":1.02,"30m":1.03,"60m":1.035,"day":1.04}
        second={"5m":1.02,"30m":1.02,"60m":1.025,"day":1.03}
        trails={"5m":.025,"30m":.03,"60m":.03,"day":.04}
        for frame in p.FRAMES:
            with self.subTest(frame=frame):
                result=p.targets(1000,frame,"reference",ref_low=900,ref_high=950)
                self.assertAlmostEqual(result["first"],1000*first[frame])
                self.assertAlmostEqual(result["second"],1000*first[frame]*second[frame])
                self.assertAlmostEqual(result["stop"],850)
                self.assertAlmostEqual(result["trail"],trails[frame])
                self.assertEqual([x["fraction_of_original"] for x in result["sell_intents"]],[.30,.35])
                self.assertTrue(all(x["is_real_broker_order"] is False
                                    for x in result["sell_intents"]))
    def _position(self,frame="30m",kind="reference"):
        t=p.targets(100,frame,kind,ref_low=90,ref_high=95)
        t.update({"qty0":100,"qty":100,"market":"coin","stage":0,
                  "stage1_filled":0.,"stage2_filled":0.,
                  "seen_above_entry":False,"score_positive_at_buy":True})
        return t
    def test_pre_first_retrace_requires_actual_previous_up_quote(self):
        t=self._position()
        self.assertIsNone(p.exit_signal(t,100,NOW))
        self.assertIsNone(p.exit_signal(t,101,NOW))
        self.assertEqual(p.exit_signal(t,100,NOW)[0],"NL1118_PRE_FIRST_RETURN_TO_ENTRY")
    def test_reference_all_three_independent_forced_exits(self):
        t=self._position()
        self.assertEqual(p.exit_signal(t,85,NOW)[0],"NL1118_REFERENCE_HARD_STOP")
        self.assertEqual(p.exit_signal(t,100,NOW,market_score=-10)[0],
                         "NL1118_MARKET_SCORE_MINUS_10")
        self.assertIsNone(p.exit_signal(t,100,NOW,market_score=-9.99))
    def test_stage1_tp30_stage2_tp35_subsequent_trail(self):
        t=self._position("5m")
        self.assertEqual(p.exit_signal(t,102,NOW),
                         ("NL1118_TP1",.3))
        t.update(stage=1,qty=70,stage1_filled=30)
        self.assertEqual(p.exit_signal(t,102*1.02,NOW),
                         ("NL1118_TP2",.5))
        self.assertEqual(p.exit_signal(t,101,NOW)[0],
                         "NL1118_AFTER_FIRST_PLUS_1_STOP")
        t.update(stage=2,qty=35,stage2_filled=35,peak_after_second=110)
        self.assertEqual(p.exit_signal(t,107,NOW)[0],"NL1118_AFTER_SECOND_TRAIL")
        t["peak_after_second"]=105
        self.assertEqual(p.exit_signal(t,t["first"],NOW)[0],
                         "NL1118_AFTER_SECOND_FIRST_FLOOR")
    def test_stock_shares_round_but_never_exceed_initial(self):
        self.assertEqual(p.stage_quantity(13,"stock",1),4)
        self.assertEqual(p.stage_quantity(13,"stock",2),5)
        self.assertEqual(p.stage_quantity(100,"coin",1),30)
        self.assertEqual(p.stage_quantity(100,"coin",2),35)
    def test_ma_stop_hold_and_up_only_contract(self):
        self.assertIsNone(ma.bear_reversal_completed([], "5m")["kind"])
        self.assertIn("RETIRED",ma.bear_reversal_completed([], "5m")["reason"])
        prev={120:{"value":120.,"delta":-1.}}
        current={120:{"value":120.,"delta":-1.}}
        for n in (5,10,20,60):
            prev[n]={"value":n+50.,"delta":-1.}
            current[n]={"value":n+50.,"delta":1.}
        self.assertTrue(p.ma_up_alignment(prev,current))
        current[120]["delta"]=0
        self.assertFalse(p.ma_up_alignment(prev,current))
        for frame,hours in p.MA_HOLD_HOURS.items():
            self.assertEqual(p.holding_deadline(NOW,"ma",frame)-NOW,
                             dt.timedelta(hours=hours))
            targets=p.targets(100,frame,"ma")
            targets.update({"market":"stock","qty0":100,"qty":100,
                            "stage":0,"stage1_filled":0,"stage2_filled":0,
                            "deadline":p.holding_deadline(NOW,"ma",frame).isoformat()})
            self.assertEqual(p.exit_signal(targets,84,NOW)[0],"NL1118_HARD_STOP_15PCT")
            self.assertEqual(p.exit_signal(targets,100,NOW+dt.timedelta(days=20))[0],
                             "NL1118_HOLD_EXPIRY")
    def test_legacy_ma_price_targets_replaced(self):
        for frame,one in (("5m",1.02),("30m",1.03),
                          ("60m",1.035),("day",1.04)):
            self.assertAlmostEqual(float(ma.up_targets(100,frame)[0]),100*one)
        self.assertEqual(ma.UP_FIRST_FRACTION,ma.Decimal(".30"))
        self.assertEqual(ma.UP_TRAIL_FROM_SECOND["day"],ma.Decimal(".04"))
    def test_trend_three_frames_bollinger_and_15_stop(self):
        with self.assertRaises(ValueError):p.targets(100,"5m","trend")
        for frame,h in p.TREND_HOLD_HOURS.items():
            self.assertEqual(p.holding_deadline(NOW,"trend",frame)-NOW,
                             dt.timedelta(hours=h))
            t=p.targets(100,frame,"trend")
            self.assertAlmostEqual(t["stop"],85)
            self.assertAlmostEqual(t["trail"],p.TREND_TRAIL[frame])
        flat=p.bollinger([100.]*21)
        self.assertAlmostEqual(flat["middle"],100.)
        self.assertAlmostEqual(flat["angle_deg"],0.)
        self.assertTrue(flat["angle_allowed"])
        self.assertFalse(p.actual_middle_touch(100,99,101))
        self.assertTrue(p.actual_middle_touch(99,101,100))
    def test_rsi_watch_persists_for_24h_and_expires(self):
        with tempfile.TemporaryDirectory() as d:
            store=RsiWatch(d)
            holder=SimpleNamespace(_nl1118_watch_db=store,
                                   _nl1118_watches={})
            k=("coin","KRW-QUID","30m")
            row=_mark_rsi_watch(holder,"coin",k,None,29.9,NOW)
            self.assertEqual(row["status"],"WATCHING")
            self.assertEqual(len(store.load()),1)
            self.assertEqual(_mark_rsi_watch(holder,"coin",k,row,51,
                             NOW+dt.timedelta(hours=23))["status"],"WATCHING")
            self.assertEqual(_mark_rsi_watch(holder,"coin",k,row,31,
                             NOW+dt.timedelta(hours=24))["status"],"EXPIRED")
    def test_stock_and_coin_buy_sidecars_include_native_verified_sell_intents(self):
        import sqlite3
        import json
        import reference_1118_runtime as ref
        with sqlite3.connect(":memory:") as c:
            c.row_factory=sqlite3.Row
            stock=dict(id=7,tf="30m",entry_price=100,
                entry_qty=100,qty=100,ref_low=90,ref_high=95,code="005930",
                entry_ts="20261118100000")
            self.assertTrue(ref._mark(c,stock,"stock"))
            x=ref._read(c,7)
            self.assertEqual(x["sell_intents"][0]["quantity"],30.)
            self.assertEqual(x["sell_intents"][1]["quantity"],35.)
            self.assertEqual(x["stop"],85.)
            self.assertTrue(all(x["state"]=="ARMED" for x in x["sell_intents"]))
            c.execute("CREATE TABLE candidates(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,ref_at TEXT,features TEXT)")
            c.execute("INSERT INTO candidates(tf,market,ref_at,features) VALUES(?,?,?,?)",
                      ("60m","KRW-TST","2026-11-18T11:00",
                       json.dumps({"low":90,"high":95})))
            coin=dict(id=11,tf="60m",market="KRW-TST",ref_at="2026-11-18T11:00",
                      entry=100,qty0="200",qty="200",
                      hold_until="2026-12-01T11:00:00+09:00",
                      entry_at="2026-11-18T20:00:00+09:00")
            self.assertTrue(ref._mark(c,coin,"coin"))
            y=ref._read(c,11)
            self.assertEqual(y["sell_intents"][0]["quantity"],60.)
            self.assertEqual(y["sell_intents"][1]["quantity"],70.)
            self.assertEqual(y["deadline"],coin["hold_until"])
            self.assertEqual(y["stop"],85.)
    def test_stock_exit_refuses_to_advance_without_real_sqlite_sell_execution(self):
        import sqlite3
        import reference_1118_runtime as ref
        with sqlite3.connect(":memory:") as c:
            c.row_factory=sqlite3.Row
            c.execute("""CREATE TABLE executions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,position_id INTEGER,
                side TEXT,event_key TEXT,qty INTEGER,price INTEGER)""")
            row=dict(id=71,code="005930",tf="5m",
                     entry_ts="20261118090000",entry_price=100,
                     entry_qty=100,qty=100,ref_low=90,ref_high=95)
            self.assertTrue(ref._mark(c,row,"stock"))
            class FakeStore:
                root="unused" # score reader would normally have native root
            class Owner:
                store=FakeStore()
                def _sell(self,conn,p,at,qty,price,reason,event_key=None):
                    conn.execute("INSERT INTO executions(position_id,side,event_key,qty,price) VALUES(?,?,?,?,?)",
                                 (p["id"],"SELL",event_key,qty,price))
                    new=dict(p);new["qty"]-=qty
                    return new
            owner=Owner()
            origin=ref._market_score
            ref._market_score=lambda *a:10.
            try:
                executed=ref._act(owner,c,row,{"close":102}, "20261118090001")
                self.assertEqual(executed["qty"],70)
                t=ref._read(c,71)
                self.assertEqual(t["stage"],1)
                self.assertEqual(t["stage1_filled"],30)
                executed=ref._act(owner,c,executed,{"close":105}, "20261118090002")
                self.assertEqual(executed["qty"],35)
                self.assertEqual(ref._read(c,71)["stage"],2)
            finally:
                ref._market_score=origin

    def test_workbook_runtime_activation_after_old_policy(self):
        src=(Path(__file__).resolve().parents[1]/
             "app/src/main/python/android_runtime_v078.py").read_text()
        self.assertIn("_install_nl1118_trend()",src)
        self.assertIn("_install_nl1118_reference()",src)
        self.assertLess(src.index("_install_reference_score_gate_v107()"),
                        src.index("_install_nl1118_reference()"))
        self.assertTrue("PRICE" not in p.POLICY) # static exact policy name

if __name__=="__main__":unittest.main()
