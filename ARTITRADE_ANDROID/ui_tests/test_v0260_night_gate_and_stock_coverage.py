"""0.2.6 regression: night EWY gate, GMO ceiling, pre-emptive stop plan,
stock MA / MarketTrend decision coverage (owner instructions 2026-11-22)."""
import datetime as dt
import random
import unittest
from pathlib import Path
from types import SimpleNamespace

import night_futures_gate_v0260 as night
import stock_ma_trend_coverage_v0260 as cov

KST = dt.timezone(dt.timedelta(hours=9))
PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


def yahoo(sessions):
    stamps, opens, closes = [], [], []
    for start, o, c in sessions:
        stamps.append(int(start.timestamp())); opens.append(o); closes.append(c)
    return {"chart": {"result": [{"timestamp": stamps,
            "indicators": {"quote": [{"open": opens, "close": closes}]}}]}}


def us_open(day):  # 09:30 New York (EST) == 23:30 KST same calendar day
    return dt.datetime.combine(day, dt.time(23, 30), tzinfo=KST)


class NightValue(unittest.TestCase):
    def test_uses_only_complete_session_after_previous_krx_close(self):
        # Monday 2026-11-23: previous KRX close Fri 11-20 15:30; the Friday US
        # session (11-20 23:30 -> 11-21 06:00 KST) is the night to use.
        now = dt.datetime(2026, 11, 23, 7, 0, tzinfo=KST)
        obj = yahoo([(us_open(dt.date(2026, 11, 18)), 100.0, 99.0),
                     (us_open(dt.date(2026, 11, 19)), 100.0, 101.0),
                     (us_open(dt.date(2026, 11, 20)), 100.0, 96.5)])
        parsed = night.parse_us_session(obj, now, night.previous_krx_close(now.date()))
        self.assertAlmostEqual(parsed["change_pct"], -3.5)

    def test_unfinished_or_older_session_is_not_used(self):
        now = dt.datetime(2026, 11, 24, 2, 0, tzinfo=KST)   # US session still open
        obj = yahoo([(us_open(dt.date(2026, 11, 20)), 100.0, 96.5),
                     (us_open(dt.date(2026, 11, 23)), 100.0, 90.0)])
        parsed = night.parse_us_session(obj, now, night.previous_krx_close(now.date()))
        self.assertEqual(parsed.get("reason"), "NO_US_SESSION_SINCE_PREVIOUS_KRX_CLOSE")

    def test_refresh_never_downgrades_a_verified_day(self):
        now = dt.datetime(2026, 11, 23, 7, 0, tzinfo=KST)
        ok = night.refresh(now, fetch=lambda: yahoo([(us_open(dt.date(2026, 11, 20)), 100.0, 96.5)]))
        self.assertEqual(ok["status"], "VERIFIED"); self.assertTrue(ok["severe"])
        def boom():
            raise ConnectionError("x")
        again = night.refresh(now + dt.timedelta(minutes=1), fetch=boom)
        self.assertEqual(again["status"], "VERIFIED")


class OpeningBlock(unittest.TestCase):
    def setUp(self):
        night.record_block = lambda *a, **k: None

    def _state(self, day, pct):
        night.set_state_for_test({"date": day.isoformat(), "status": "VERIFIED",
                                  "change_pct": pct, "severe": pct <= night.SEVERE_PCT,
                                  "source": night.SOURCE})

    def test_minus_three_blocks_at_ma_trend_for_thirty_minutes_only(self):
        day = dt.date(2026, 11, 23); self._state(day, -3.0)
        t = lambda h, m, s: dt.datetime(2026, 11, 23, h, m, s, tzinfo=KST)
        for strategy in ("at", "ma", "trend"):
            self.assertTrue(night.blocked_now(strategy, "005930", "5m", t(9, 0, 0)))
            self.assertTrue(night.blocked_now(strategy, "005930", "5m", t(9, 29, 59)))
            self.assertFalse(night.blocked_now(strategy, "005930", "5m", t(9, 30, 0)))
            self.assertFalse(night.blocked_now(strategy, "005930", "5m", t(8, 59, 59)))

    def test_supply_demand_is_never_time_blocked(self):
        day = dt.date(2026, 11, 23); self._state(day, -6.0)
        self.assertFalse(night.blocked_now("gmo", "000680", "D+1",
                                           dt.datetime(2026, 11, 23, 9, 0, 5, tzinfo=KST)))

    def test_minus_two_point_nine_does_not_block(self):
        day = dt.date(2026, 11, 23); self._state(day, -2.9)
        self.assertFalse(night.blocked_now("ma", "005930", "5m",
                                           dt.datetime(2026, 11, 23, 9, 1, tzinfo=KST)))

    def test_unverified_night_never_blocks(self):
        night.set_state_for_test({"date": "2026-11-25", "status": "FETCH_FAILED_ConnectionError",
                                  "change_pct": None, "severe": False})
        self.assertFalse(night.blocked_now("at", "", "5m",
                                           dt.datetime(2026, 11, 25, 9, 1, tzinfo=KST)))

    def test_special_session_block_follows_its_opening(self):
        # 2026-11-19 (CSAT) opened at 10:00.
        day = dt.date(2026, 11, 19); self._state(day, -4.0)
        self.assertFalse(night.blocked_now("ma", "", "5m", dt.datetime(2026, 11, 19, 9, 10, tzinfo=KST)))
        self.assertTrue(night.blocked_now("ma", "", "5m", dt.datetime(2026, 11, 19, 10, 10, tzinfo=KST)))


class SupplyDemandCeiling(unittest.TestCase):
    def test_down_and_up_nights_shift_d_close_and_floor_to_tick(self):
        now = dt.datetime(2026, 11, 26, 8, 0, tzinfo=KST)
        night.set_state_for_test({"date": "2026-11-26", "status": "VERIFIED", "change_pct": -3.5})
        ceiling, info = night.gmo_ceiling(10000, now)
        self.assertEqual(ceiling, 9650); self.assertEqual(info["reason"], "EWY_ADJUSTED_D_CLOSE")
        night.set_state_for_test({"date": "2026-11-26", "status": "VERIFIED", "change_pct": 1.23})
        self.assertEqual(night.gmo_ceiling(10000, now)[0], 10120)   # 10123 -> 10-won tick
        self.assertEqual(night.gmo_ceiling(2405, now)[0], 2430)     # 2434.58 -> 5-won tick

    def test_unverified_night_keeps_the_former_rule(self):
        now = dt.datetime(2026, 11, 27, 8, 0, tzinfo=KST)
        ceiling, info = night.gmo_ceiling(10000, now)
        self.assertIsNone(ceiling)
        self.assertEqual(info["reason"], "NIGHT_VALUE_UNVERIFIED_KEEP_ORIGINAL_RULE")

    def test_gmo_step_uses_the_single_ceiling_and_no_time_block(self):
        src = (PY / "gmo_d1_preplan_v097.py").read_text(encoding="utf-8")
        self.assertIn("night.gmo_ceiling(close_value,at)", src)
        self.assertIn('"EWY_ADJUSTED_D_CLOSE"', src)
        self.assertNotIn("night.blocked_now", src)


class PreemptiveStop(unittest.TestCase):
    def holding(self, mark, stop, strategy="ma"):
        return {"strategy": strategy, "code": "005930", "position_id": 7, "entry": 10000.0,
                "mark": mark, "stops": [{"price": stop, "label": "직전봉 최저가 손절", "pct": 0}]}

    def test_sells_when_night_decline_reaches_nearest_stop(self):
        plan = night.preemptive_plan([self.holding(10000.0, 9750.0)], -3.0)
        self.assertEqual(len(plan), 1); self.assertAlmostEqual(plan[0]["projected"], 9700.0)

    def test_keeps_when_stop_is_further_than_the_decline(self):
        self.assertEqual(night.preemptive_plan([self.holding(10000.0, 9600.0)], -3.0), [])

    def test_up_night_or_unknown_never_sells(self):
        self.assertEqual(night.preemptive_plan([self.holding(10000.0, 9999.0)], 0.5), [])
        self.assertEqual(night.preemptive_plan([self.holding(10000.0, 9999.0)], None), [])

    def test_unknown_stop_plan_is_skipped_not_guessed(self):
        h = self.holding(10000.0, 9990.0); h["stops"] = []
        self.assertEqual(night.preemptive_plan([h], -5.0), [])

    def test_automatic_exit_is_not_recorded_as_user_manual(self):
        import stock_manual_v086 as manual
        rid = "12345678-1234-5678-1234-567812345678"
        self.assertTrue(manual._reason({"request_id": rid,
            "auto_reason": "NIGHT_EWY_PREEMPTIVE_STOP"}).startswith("AUTO_NIGHT_EWY_PREEMPTIVE_STOP:"))
        self.assertTrue(manual._reason({"request_id": rid}).startswith("USER_SIM_MANUAL:"))
        self.assertTrue(manual._reason({"request_id": rid,
            "auto_reason": "ANYTHING_ELSE"}).startswith("USER_SIM_MANUAL:"))


class MaNearSignal(unittest.TestCase):
    def test_trigger_band_matches_the_real_ma_rule(self):
        import ma_v067_policy as policy
        random.seed(11)
        signals = 0
        for _ in range(150):
            c = [100.0]; drift = random.uniform(-0.004, 0.001)
            for _ in range(160):
                c.append(max(1.0, c[-1] * (1 + drift + random.gauss(0, 0.006))))
            band = cov.ma_trigger_band(c)
            for k in range(-40, 41):
                p = c[-1] * (1 + k / 800)
                real = policy.prewatch_upward_alignment(c, p).get("kind") == "REVERSAL_UP"
                predicted = bool(band and band[0] < p < band[1])
                self.assertEqual(real, predicted)
                signals += real
        self.assertGreater(signals, 50)

    def test_distance_is_zero_inside_and_none_when_impossible(self):
        c = [100.0 - i * 0.1 for i in range(160)]
        self.assertIsNone(cov.ma_distance_pct([5.0] * 50, 5.0))
        band = cov.ma_trigger_band(c)
        if band:
            mid = (band[0] + band[1]) / 2
            self.assertEqual(cov.ma_distance_pct(c, mid), 0.0)


class _WatchDb:
    def __init__(self): self.saved = {}
    def save(self, key, row): self.saved[key] = row


class _FakeTrend:
    def __init__(self, history, now):
        import threading
        self.lock = threading.Lock()
        self._candidates = {"stock": {"005930": ("KOSPI", "SAMSUNG")}}
        self._history = history
        self._quotes = {"stock": {}}
        self._nl1118_watch_db = _WatchDb()
        self._nl1118_watches = {}
        self.blocks = []
    def _record_entry_block(self, *a, **k): self.blocks.append((a, k))


def _bars(end, frame_secs, closes):
    rows = []
    for i, close in enumerate(closes):
        e = end - dt.timedelta(seconds=frame_secs * (len(closes) - 1 - i))
        rows.append({"start": e - dt.timedelta(seconds=frame_secs), "end": e, "close": close,
                     "high": close, "low": close, "source": "LOCAL_KRX_VERIFIED"})
    return rows


class TrendClosedBarWatch(unittest.TestCase):
    def test_completed_bar_rsi30_starts_watch_once_with_evidence(self):
        now = dt.datetime(2026, 11, 23, 11, 5, tzinfo=KST)     # Monday, session open
        last_end = dt.datetime(2026, 11, 23, 11, 0, tzinfo=KST)
        falling = [100.0 - i * 0.8 for i in range(60)]
        co = _FakeTrend({("stock", "005930", "30m"): _bars(last_end, 1800, falling)}, now)
        armed = cov.arm_from_closed_bars(co, "stock", now)
        self.assertEqual(armed, 1)
        watch = co._nl1118_watches[("stock", "005930", "30m")]
        self.assertEqual(watch["status"], "WATCHING")
        evidence = watch["evidence"]
        self.assertEqual(evidence["observed_at"], watch["observed_at"])
        self.assertLessEqual(evidence["rsi20"], 30)
        self.assertEqual(evidence["basis"], "COMPLETED_BAR_RSI20_V0260")
        self.assertEqual(cov.arm_from_closed_bars(co, "stock", now + dt.timedelta(seconds=5)), 0)

    def test_coin_is_untouched(self):
        now = dt.datetime(2026, 11, 23, 11, 5, tzinfo=KST)
        co = _FakeTrend({}, now)
        self.assertEqual(cov.arm_from_closed_bars(co, "coin", now), 0)

    def test_near_trend_includes_watching_and_low_rsi(self):
        now = dt.datetime(2026, 11, 23, 11, 5, tzinfo=KST)
        last_end = dt.datetime(2026, 11, 23, 11, 0, tzinfo=KST)
        co = _FakeTrend({("stock", "005930", "30m"): _bars(last_end, 1800,
                                                         [100.0 - i * 0.8 for i in range(60)])}, now)
        self.assertEqual(cov.compute_trend_near(co, now), ["005930"])


class SubscriptionCoverage(unittest.TestCase):
    def test_near_symbols_are_protected_with_room_left_for_rotation(self):
        src = (PY / "stock_priority_runtime_v093.py").read_text(encoding="utf-8")
        import stock_priority_runtime_v093 as prio
        self.assertEqual(prio.NEAR_PROTECTED_LIMIT, 170)
        self.assertLess(prio.NEAR_PROTECTED_LIMIT, prio.KIWOOM_MAX_SUBSCRIPTIONS)
        self.assertIn("from stock_ma_trend_coverage_v0260 import near_codes", src)
        self.assertIn('"near_signal_pinned_v0260"', src)

    def test_runtime_installs_both_modules_last(self):
        src = (PY / "android_runtime_v078.py").read_text(encoding="utf-8")
        a = src.index("_install_stock_pending_recovery_v017()")
        b = src.index("_install_night_futures_v0260(_core)")
        c = src.index("_install_stock_coverage_v0260(_core)")
        self.assertLess(a, b); self.assertLess(b, c)
        self.assertGreater(b, src.index("_install_nl1118_trend()"))


if __name__ == "__main__":
    unittest.main()
