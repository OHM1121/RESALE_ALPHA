"""0.3.4 regression (issue #149): owner 2026-11-23 after the ledger report.

1. "진입가 복귀 청산 부분만 너말대로": coin GEN return-to-entry exit arms only after entry +1.5 %.
2. "고정매수금액은 손익비가 1:0.5 이하인 종목에만 세팅": coin GEN / MA / Trend buy is reduced only when
   gain to the first target / loss to the stop <= 0.5 (factor = ratio / 0.5, floor 0.1).
Synthetic data only."""
import datetime as dt
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import new_legacy_1118_policy as nl
import rr_size_v0340 as rr

KST = dt.timezone(dt.timedelta(hours=9))
BASE = Path(__file__).resolve().parents[1]
PY = BASE / "app/src/main/python"
NOW = dt.datetime(2026, 11, 23, 12, 0, tzinfo=KST)


def read(name):
    return (PY / name).read_text(encoding="utf-8")


class Evaluate(unittest.TestCase):
    def test_above_1_to_0_5_keeps_the_fixed_amount(self):
        out = rr.evaluate(100, 102, 97)            # gain 2 %, loss 3 % -> 0.667
        self.assertEqual(out["factor"], 1.0)
        self.assertFalse(out["applied"])
        self.assertEqual(out["reason"], "RR_ABOVE_1_TO_0_5_FIXED_AMOUNT")

    def test_exactly_1_to_0_5_is_included_and_continuous(self):
        out = rr.evaluate(100, 102, 96)            # 2 / 4 = 0.5 -> factor 1.0, applied
        self.assertTrue(out["applied"])
        self.assertAlmostEqual(out["factor"], 1.0)

    def test_wide_stop_is_sized_so_the_loss_equals_a_1_to_0_5_position(self):
        out = rr.evaluate(100, 102, 90)            # 2 / 10 = 0.2 -> 0.4
        self.assertAlmostEqual(out["factor"], 0.4)
        # loss at stop = 10 % x 0.4 = 4 % = what a full 1:0.5 position (2 x 2 %) loses
        self.assertAlmostEqual(out["loss_pct"] / 100 * out["factor"], 0.04)

    def test_floor_and_unverifiable_inputs(self):
        self.assertEqual(rr.evaluate(100, 101, 50)["factor"], rr.MIN_FACTOR)
        for bad in ((None, 102, 90), (100, None, 90), (100, 102, None), (0, 1, 1), (100, 99, 90)):
            self.assertEqual(rr.evaluate(*bad)["factor"], 1.0, bad)
        self.assertEqual(rr.evaluate(100, 102, 100)["reason"], "RR_STOP_NOT_BELOW_ENTRY")

    def test_ma_trend_stop_is_never_beyond_the_15pct_hard_stop(self):
        self.assertAlmostEqual(rr.effective_ma_trend_stop(100, 75), 85.0)   # PIEVERSE -25 % case
        self.assertAlmostEqual(rr.effective_ma_trend_stop(100, 95), 95.0)
        self.assertAlmostEqual(rr.effective_ma_trend_stop(100, 101), 85.0)  # ignored above entry
        self.assertAlmostEqual(rr.effective_ma_trend_stop(100, None), 85.0)

    def test_capped(self):
        self.assertEqual(rr.capped(200000, 0.4), 80000)
        self.assertEqual(rr.capped(200000, 1.0), 200000)


def bars(n, start, frame_s, low_at, low_price, base=100.0):
    out = []
    for i in range(n):
        s = start + dt.timedelta(seconds=frame_s * i)
        lo = low_price if i == low_at else base * 0.99
        out.append({"start": s, "open": base, "high": base * 1.01, "low": lo, "close": base})
    return out


class StopsBeforeBuy(unittest.TestCase):
    def test_ma_coin_from_the_scanner_cache(self):
        import ma_v061_coin_runtime as native
        b = bars(60, NOW - dt.timedelta(minutes=5 * 60), 300, 10, 90.0)
        key = ("5m", "KRW-TEST")
        with native._HISTORY_LOCK:
            old = native._HISTORY.get(key)
            native._HISTORY[key] = (NOW, tuple(b))
        try:
            out = rr.ma_coin("5m", "KRW-TEST", 100, NOW)
        finally:
            with native._HISTORY_LOCK:
                if old is None:
                    native._HISTORY.pop(key, None)
                else:
                    native._HISTORY[key] = old
        self.assertAlmostEqual(out["loss_pct"], 10.0, places=3)      # 50-bar lowest low 90
        self.assertAlmostEqual(out["gain_pct"], 2.0, places=3)       # MA 5m first target +2 %
        self.assertAlmostEqual(out["factor"], 0.4, places=3)
        self.assertTrue(out["stop_source"].startswith("MA_LOOKBACK_CANDIDATE"))

    def test_ma_coin_without_history_keeps_the_amount(self):
        out = rr.ma_coin("5m", "KRW-NONE", 100, NOW)
        self.assertEqual((out["factor"], out["reason"]), (1.0, "RR_NO_MA_HISTORY"))

    def test_ma_coin_busy_history_lock_never_waits_long(self):
        import ma_v061_coin_runtime as native
        held, release = threading.Event(), threading.Event()

        def hold():
            with native._HISTORY_LOCK:
                held.set()
                release.wait(3)
        t = threading.Thread(target=hold, daemon=True)
        t.start()
        held.wait(2)
        try:
            out = rr.ma_coin("5m", "KRW-TEST", 100, NOW)
        finally:
            release.set()
            t.join(2)
        self.assertEqual((out["factor"], out["reason"]), (1.0, "RR_HISTORY_LOCK_BUSY"))

    def test_gen_coin_uses_the_reference_candle_hard_stop(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE candidates(id INTEGER PRIMARY KEY,tf TEXT,market TEXT,ref_at TEXT,features TEXT)")
        c.execute("INSERT INTO candidates(tf,market,ref_at,features) VALUES(?,?,?,?)",
                  ("30m", "KRW-G", "R", json.dumps({"low": 95.0, "high": 100.0})))
        out = rr.gen_coin(c, {"tf": "30m", "market": "KRW-G", "ref_at": "R"}, 100)
        # stop = 95 - (100 - 95) = 90 -> loss 10 %, GEN 30m first +3 % -> 0.3 -> factor 0.6
        self.assertAlmostEqual(out["factor"], 0.6, places=3)
        self.assertEqual(out["stop_source"], "REFERENCE_HARD_STOP")

    def test_trend_from_the_coordinator_history_with_a_tried_lock(self):
        rows = [dict(r, end=r["start"] + dt.timedelta(minutes=30), source="UPBIT_CLOSED")
                for r in bars(100, NOW - dt.timedelta(minutes=30 * 101), 1800, 5, 92.0)]
        co = SimpleNamespace(lock=threading.RLock(), _history={("coin", "KRW-T", "30m"): rows})
        out = rr.trend(SimpleNamespace(market_trend=co), "coin", "KRW-T", "30m", 100, NOW)
        # stop 92 (-8 %), Trend 30m first +3 % -> 0.375 -> factor 0.75
        self.assertAlmostEqual(out["factor"], 0.75, places=3)
        held, release = threading.Event(), threading.Event()

        def hold():
            with co.lock:
                held.set()
                release.wait(3)
        t = threading.Thread(target=hold, daemon=True)
        t.start()
        held.wait(2)
        try:
            busy = rr.trend(SimpleNamespace(market_trend=co), "coin", "KRW-T", "30m", 100, NOW)
        finally:
            release.set()
            t.join(2)
        # no bars visible -> only the -15 % hard stop: 3 / 15 = 0.2 -> 0.4 (never larger than verified)
        self.assertEqual(busy["stop_source"], "TREND_LOOKBACK_NO_TREND_HISTORY_0")

    def test_stored_factor_for_top_ups(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE ma_v067_positions(position_id INTEGER,state_json TEXT)")
        c.execute("CREATE TABLE nl1118_reference_positions(position_id INTEGER,intent_json TEXT)")
        c.execute("INSERT INTO ma_v067_positions VALUES(1,?)", (json.dumps({"rr_size_factor": "0.4"}),))
        c.execute("INSERT INTO nl1118_reference_positions VALUES(2,?)", (json.dumps({"rr_size_factor": "0.6"}),))
        self.assertAlmostEqual(rr.stored_coin_factor(c, 1, "ma"), 0.4)
        self.assertAlmostEqual(rr.stored_coin_factor(c, 2, "base"), 0.6)
        self.assertEqual(rr.stored_coin_factor(c, 3, "ma"), 1.0)


def coin_plan(entry=100.0):
    plan = nl.targets(entry, "30m", "reference", ref_low=95, ref_high=100)
    plan.update({"market": "coin", "qty0": 1.0, "qty": 1.0, "stage": 0, "seen_above_entry": False})
    return plan


class CoinReturnToEntry(unittest.TestCase):
    def test_small_rise_then_entry_no_longer_exits(self):
        plan = coin_plan()
        self.assertIsNone(nl.exit_signal(plan, 101.0, NOW))           # +1 %
        self.assertIsNone(nl.exit_signal(plan, 100.0, NOW))           # back to entry: held
        self.assertTrue(plan["seen_above_entry"])
        self.assertNotIn("rte_armed_v0340", plan)

    def test_rise_of_1_5pct_then_entry_exits(self):
        plan = coin_plan()
        self.assertIsNone(nl.exit_signal(plan, 101.5, NOW))
        self.assertTrue(plan["rte_armed_v0340"])
        self.assertEqual(nl.exit_signal(plan, 100.0, NOW), ("NL1118_PRE_FIRST_RETURN_TO_ENTRY", 1.0))

    def test_old_armed_coin_plan_follows_the_new_rule(self):
        plan = coin_plan()
        plan["seen_above_entry"] = True                                  # armed by 0.3.3 at +0.1 %
        self.assertIsNone(nl.exit_signal(plan, 100.0, NOW))

    def test_stock_reference_rule_is_unchanged(self):
        plan = nl.targets(10000, "30m", "reference", ref_low=9500, ref_high=10000)
        plan.update({"market": "stock", "qty0": 10, "qty": 10, "stage": 0, "seen_above_entry": False})
        self.assertIsNone(nl.exit_signal(plan, 10010, NOW))
        self.assertEqual(nl.exit_signal(plan, 10000, NOW), ("NL1118_PRE_FIRST_RETURN_TO_ENTRY", 1.0))

    def test_stops_and_targets_unchanged(self):
        plan = coin_plan()
        self.assertEqual(nl.exit_signal(plan, 90.0, NOW), ("NL1118_REFERENCE_HARD_STOP", 1.0))
        self.assertEqual(nl.exit_signal(coin_plan(), 103.0, NOW)[0], "NL1118_TP1")

    def test_position_view_shows_the_exit_only_when_armed(self):
        import position_view_v0200 as view
        plan = coin_plan()
        plan["seen_above_entry"] = True
        labels = [x for x in view.stops_reference(plan, 100.0) if x]
        self.assertFalse(any("매수가 복귀" in json.dumps(x, ensure_ascii=False) for x in labels))
        plan["rte_armed_v0340"] = True
        labels = [x for x in view.stops_reference(plan, 100.0) if x]
        self.assertTrue(any("매수가 복귀" in json.dumps(x, ensure_ascii=False) for x in labels))


class Wiring(unittest.TestCase):
    def test_gen_buy_caps_available_and_records(self):
        src = read("reference_1118_runtime.py")
        self.assertIn("rr=rrs.gen_coin(c,order,book[\"ask\"])", src)
        self.assertIn("available=min(Decimal(str(available)),Decimal(rrs.capped(per,rr[\"factor\"])))", src)
        self.assertIn("_save(c,rrs.record(plan,rr))", src)

    def test_ma_buy_only_reversal_up_and_records(self):
        src = read("ma_v067_coin_runtime.py")
        self.assertIn("if _kind_for(c,order)[0]=='REVERSAL_UP':", src)
        self.assertIn("rr=rrs.ma_coin(order['tf'],order['market'],book['ask'],now)", src)
        self.assertIn("_record_buy_size(c,pos['id'],score_at_buy,size_factor,rr)", src)

    def test_trend_buy_coin_only_and_records(self):
        src = read("market_trend_wallet_v054.py")
        self.assertIn("if market=='coin':", src)
        self.assertIn("rr=rrs.trend(self.rt,market,symbol,frame,float(ask),at)", src)
        self.assertIn("rrs.record(pos.__dict__,rr)", src)

    def test_top_ups_never_refill_above_the_sized_amount(self):
        self.assertIn("rr_f=rrs.stored_coin_factor(c,p['id'],self.account_variant)",
                      read("coin_partial_entry_v094.py"))
        self.assertIn("rr_f=float(pos.__dict__.get('rr_size_factor') or 1)", read("trend_partial_entry_v094.py"))

    def test_module_has_no_network_or_order(self):
        src = read("rr_size_v0340.py")
        for word in ("requests", "urllib", "socket", "_order_fill", "INSERT INTO executions", "place("):
            self.assertNotIn(word, src)


if __name__ == "__main__":
    unittest.main()
