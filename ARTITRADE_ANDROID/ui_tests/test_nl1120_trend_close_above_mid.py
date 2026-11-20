"""NEW LEGACY 11.20 MarketTrend condition 2 (supersedes the 11.19 sheet).

11.19: Bollinger(20,1.5) MIDDLE slope in (-20,+30) and a live price REACHES the
       MIDDLE line -> buy.
11.20: slope in (-15,+20) and the COMPLETED bar's close is ABOVE the MIDDLE.

Synthetic data only. No broker, no REST, no real order.
"""
from __future__ import annotations

import datetime as dt
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
import new_legacy_1118_policy as pol

try:  # runtime layer needs the original engine on sys.path (CI provides it)
    import market_trend_v043 as mt
    import market_trend_1118_runtime as nl
    RUNTIME = True
except Exception:  # pragma: no cover - environment without the engine
    RUNTIME = False

KST = dt.timezone(dt.timedelta(hours=9))


def ramp(k, last=None):
    """21 closes: flat 100 with two wide wicks, then a +k/bar ramp. Angle is
    pixel-projected in the auto viewport, so k=0.3 -> 16.2 deg, k=0.4 -> 21.2."""
    s = [100.0] * 21
    s[3], s[7] = 95.0, 105.0
    for i in range(10, 21):
        s[i] = 100.0 + (i - 10) * k
    if last is not None:
        s[-1] = last
    return s


def down_ramp(k):
    s = [100.0] * 21
    s[3], s[7] = 105.0, 95.0
    for i in range(10, 21):
        s[i] = 100.0 - (i - 10) * k
    return s


T0 = dt.datetime(2026, 11, 20, 10, 0, tzinfo=KST)


class PolicyConstantsAndPureRule(unittest.TestCase):
    def test_slope_window_is_the_11_20_one(self):
        self.assertEqual((pol.BOLL_ANGLE_MIN, pol.BOLL_ANGLE_MAX), (-15.0, 20.0))

    def test_close_above_middle_with_allowed_slope_passes(self):
        c = pol.closed_bar_entry_condition(ramp(0.3), T0, T0 - dt.timedelta(minutes=5))
        self.assertIsNotNone(c)
        self.assertTrue(c["close_above_middle"])
        self.assertEqual(c["entry_rule"], pol.TREND_ENTRY_RULE_1120)
        self.assertGreater(c["signal_close"], c["middle"])
        self.assertTrue(-15 < c["angle_deg"] < 20)

    def test_close_not_above_middle_is_rejected(self):
        self.assertIsNone(pol.closed_bar_entry_condition(ramp(0.3, last=99.0)))

    def test_close_equal_to_middle_is_not_above(self):
        closes = ramp(0.3)
        closes[-1] = 100.0
        fixed = {"middle": 100.0, "angle_allowed": True, "angle_deg": 5.0}
        with patch.object(pol, "bollinger", return_value=fixed):
            self.assertIsNone(pol.closed_bar_entry_condition(closes))
        closes[-1] = 100.0001
        with patch.object(pol, "bollinger", return_value=fixed):
            self.assertIsNotNone(pol.closed_bar_entry_condition(closes))

    def test_slope_between_old_and_new_upper_limit_is_now_rejected(self):
        b = pol.bollinger(ramp(0.4))
        self.assertTrue(20 <= b["angle_deg"] < 30, b["angle_deg"])   # 11.19 allowed it
        self.assertFalse(b["angle_allowed"])                          # 11.20 does not
        self.assertIsNone(pol.closed_bar_entry_condition(ramp(0.4)))

    def test_slope_between_old_and_new_lower_limit_is_now_rejected(self):
        found = None
        for k in [x / 100 for x in range(25, 45)]:
            b = pol.bollinger(down_ramp(k))
            if -20 < b["angle_deg"] <= -15:
                found = (k, b)
                break
        self.assertIsNotNone(found, "fixture search must find a -20..-15 deg line")
        self.assertFalse(found[1]["angle_allowed"])                   # 11.19 allowed it

    def test_bar_that_ended_before_the_rsi_watch_began_is_not_the_signal(self):
        started = T0 + dt.timedelta(minutes=5)
        self.assertIsNone(pol.closed_bar_entry_condition(ramp(0.3), T0, started))
        self.assertIsNotNone(pol.closed_bar_entry_condition(ramp(0.3), T0, T0))

    def test_too_few_or_invalid_closes_never_authorize(self):
        self.assertIsNone(pol.closed_bar_entry_condition(ramp(0.3)[-20:]))
        self.assertIsNone(pol.closed_bar_entry_condition(None))
        bad = ramp(0.3)
        bad[5] = float("nan")
        self.assertIsNone(pol.closed_bar_entry_condition(bad))

    def test_old_touch_helper_is_kept_but_no_longer_the_entry_rule(self):
        self.assertTrue(pol.actual_middle_touch(99, 101, 100))


class _Wallet:
    def __init__(self):
        self.buys = []

    def stop_reentry_blocked(self, *a):
        return False

    def owned(self, *a):
        return False

    def buy(self, market, frame, code, name, ask, ask_size, at, barid, rsi):
        self.buys.append((market, frame, code, ask, barid, rsi))
        return SimpleNamespace(execution_mode="PAPER", entry=ask, qty=1.0, frame=frame), "BOUGHT"


class _Journal:
    def __init__(self):
        self.events = []

    def stop_reentry_blocked(self, *a):
        return False

    def event(self, *a, **k):
        self.events.append((a, k))


def bars_for(closes, last_end):
    return [{"close": c, "end": last_end - dt.timedelta(minutes=(20 - i) * 30),
             "source": "UPBIT_CLOSED"} for i, c in enumerate(closes)]


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class RuntimeEntry(unittest.TestCase):
    def make(self, root, now):
        co = SimpleNamespace()
        co.rt = SimpleNamespace(root=root, coin_runner=SimpleNamespace(preload_gate=None))
        co.lock = threading.RLock()
        co.stop_event = threading.Event()
        co._quotes = {"coin": {"KRW-TEST": (100.0, time.monotonic())}, "stock": {}}
        co._candidates = {"coin": {"KRW-TEST": ("CRYPTO", "TEST")}, "stock": {}}
        co._history = {}
        co._decision_clock = lambda: now
        co.mode = lambda market: "ORIGINAL"
        co._positions = {}
        co.journal = _Journal()
        co.standalone_wallet = _Wallet()
        co._quote = lambda market, code: {"trade": co._quotes[market][code][0], "bid": 99.0,
                                          "ask": 101.0, "ask_size": 1000.0, "bid_size": 1000.0}
        co._last_fetch_error = {}
        return co

    def run_timeline(self, closes_at_signal, price_after=None):
        """10:05 arms the RSI watch (bar of 10:00 is OLDER than the watch);
        10:30:05 the 10:30 bar has COMPLETED with `closes_at_signal`."""
        with tempfile.TemporaryDirectory() as root:
            t1 = T0 + dt.timedelta(minutes=5)
            t2 = T0 + dt.timedelta(minutes=30, seconds=5)
            co = self.make(root, t1)
            co._history[("coin", "KRW-TEST", "30m")] = bars_for(closes_at_signal, T0)
            with patch.object(nl, "_ready_market", return_value=True), \
                 patch.object(mt, "rsi20", return_value=25.0):
                nl._entry_window(co, "coin", t1)
                armed = len(co._nl1118_watches)
                buys_after_arm = len(co.standalone_wallet.buys)
                co._decision_clock = lambda: t2
                co._history[("coin", "KRW-TEST", "30m")] = bars_for(
                    closes_at_signal, T0 + dt.timedelta(minutes=30))
                co._quotes["coin"]["KRW-TEST"] = (
                    price_after if price_after is not None else 103.0, time.monotonic() + 0.001)
                nl._entry_window(co, "coin", t2)
            return co, armed, buys_after_arm

    def test_buy_happens_only_after_the_signal_bar_completes_above_the_middle(self):
        co, armed, early = self.run_timeline(ramp(0.3))
        self.assertEqual(armed, 1)
        self.assertEqual(early, 0, "a bar that ended before the watch began must not buy")
        self.assertEqual(len(co.standalone_wallet.buys), 1)
        market, frame, code, ask, _bar, rsi = co.standalone_wallet.buys[0]
        self.assertEqual((market, frame, code, ask, rsi), ("coin", "30m", "KRW-TEST", 101.0, 25.0))
        self.assertEqual(co._nl1118_watches[("coin", "KRW-TEST", "30m")]["status"], "BOUGHT")
        payload = co.journal.events[0][1]["payload"]
        self.assertEqual(payload["entry_rule"], pol.TREND_ENTRY_RULE_1120)
        self.assertTrue(payload["bollinger"]["close_above_middle"])

    def test_close_below_middle_does_not_buy(self):
        co, armed, _ = self.run_timeline(ramp(0.3, last=99.0))
        self.assertEqual(armed, 1)
        self.assertEqual(len(co.standalone_wallet.buys), 0)

    def test_slope_over_the_new_limit_does_not_buy(self):
        co, _, _ = self.run_timeline(ramp(0.4))        # 21 deg: allowed by 11.19
        self.assertEqual(len(co.standalone_wallet.buys), 0)

    def test_a_live_price_crossing_the_middle_no_longer_buys(self):
        """11.19 trigger: two prints straddling MIDDLE. Here the completed bar
        closed BELOW the MIDDLE, so even a crossing print must not buy."""
        with tempfile.TemporaryDirectory() as root:
            t1 = T0 + dt.timedelta(minutes=5)
            t2 = T0 + dt.timedelta(minutes=30, seconds=5)
            closes = ramp(0.3, last=99.0)
            co = self.make(root, t1)
            co._history[("coin", "KRW-TEST", "30m")] = bars_for(closes, T0)
            with patch.object(nl, "_ready_market", return_value=True), \
                 patch.object(mt, "rsi20", return_value=25.0):
                nl._entry_window(co, "coin", t1)
                co._decision_clock = lambda: t2
                co._history[("coin", "KRW-TEST", "30m")] = bars_for(
                    closes, T0 + dt.timedelta(minutes=30))
                mid = pol.bollinger(closes)["middle"]
                for price in (mid - 1.0, mid + 1.0):       # a genuine cross
                    co._quotes["coin"]["KRW-TEST"] = (price, time.monotonic() + 0.001)
                    nl._entry_window(co, "coin", t2)
            self.assertEqual(len(co.standalone_wallet.buys), 0)


if __name__ == "__main__":
    unittest.main()
