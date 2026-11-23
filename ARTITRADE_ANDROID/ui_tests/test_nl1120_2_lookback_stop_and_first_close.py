"""NEW LEGACY 11.20(2) workbook (issue #120): MA / MarketTrend changes.

Red additions: lookback-low CLOSE stop (MA 50-bar min low; Trend 200/90/90-bar
average low) and "only the FIRST close above the Bollinger MIDDLE after
RSI<=30 may buy". Blue deletion: the "reference candle low, then close below
it" exit for MA and Trend. Synthetic data only; no broker, no REST, no order.
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import threading
import time
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
import new_legacy_1118_policy as pol
import new_legacy_1120_policy as p20
import ma_v067_policy as map_

try:
    import market_trend_v043 as mt
    import market_trend_1118_runtime as nl
    RUNTIME = True
except Exception:  # pragma: no cover - engine not on sys.path
    RUNTIME = False

KST = dt.timezone(dt.timedelta(hours=9))
T0 = dt.datetime(2026, 11, 20, 10, 0, tzinfo=KST)
WIDTH = {"5m": 300, "30m": 1800, "60m": 3600, "day": 86400}


def bar(end, low, close, source="UPBIT_CLOSED"):
    return {"start": end - dt.timedelta(seconds=1800), "end": end,
            "low": low, "high": max(low, close) + 1, "close": close, "source": source}


def history(n, last_end, low=100.0, close=105.0, step=1800):
    """n completed bars ending at last_end, all with the same low/close."""
    return [dict(bar(last_end - dt.timedelta(seconds=step * (n - 1 - i)), low, close),
                 start=last_end - dt.timedelta(seconds=step * (n - i)))
            for i in range(n)]


class LookbackStopPure(unittest.TestCase):
    ENTRY = T0
    NOW = T0 + dt.timedelta(hours=40)

    def test_ma_stop_is_the_lowest_low_of_the_last_50_bars_before_the_buy(self):
        bars = history(60, T0, low=100.0)
        bars[-3]["low"] = 90.0            # inside the last 50 -> the stop
        bars[2]["low"] = 50.0             # older than 50 bars -> ignored
        s = p20.reconcile({}, "ma", "30m", bars, T0, 104.0, self.NOW)
        self.assertEqual(s["nl1120_stop_status"], "ACTIVE")
        self.assertEqual(float(s["nl1120_stop_price"]), 90.0)
        self.assertEqual(s["nl1120_stop_bars"], 50)

    def test_trend_stop_is_the_lowest_low_with_200_90_90_windows(self):
        # 0.1.9 (owner decision 2026-11-21): lowest LOW of the window, not the average.
        for frame, need in (("30m", 200), ("60m", 90), ("day", 90)):
            bars = history(need + 25, T0, low=100.0)
            for b in bars[-need:-need + 10]:
                b.update(low=90.0, close=130.0, high=131.0)    # 10 bars inside the window
            for b in bars[:25]:
                b["low"] = 1.0              # outside the window: must not count
            s = p20.reconcile({}, "trend", frame, bars, T0, 120.0, self.NOW)
            self.assertEqual(s["nl1120_stop_status"], "ACTIVE", frame)
            self.assertAlmostEqual(float(s["nl1120_stop_price"]), 90.0, places=6)
            self.assertEqual(s["nl1120_stop_bars"], need, frame)
            self.assertEqual(s["nl1120_stop_rule"], p20.STOP_RULE_TREND_MIN)

    def test_near_replay_average_was_above_entry_but_lowest_low_is_a_real_stop(self):
        # NEAR 30m export: buy 3955, average of 200 lows 4347 (> buy => old rule IGNORED the stop).
        bars = history(200, T0, low=4400.0, close=4450.0)
        bars[10]["low"] = 3300.0; bars[10]["close"] = 3400.0
        average = sum(b["low"] for b in bars) / 200
        self.assertGreater(average, 3955.0)
        s = p20.reconcile({}, "trend", "30m", bars, T0, 3955.0, T0 + dt.timedelta(hours=1))
        self.assertEqual(s["nl1120_stop_status"], "ACTIVE")
        self.assertAlmostEqual(float(s["nl1120_stop_price"]), 3300.0)
        # A completed bar CLOSING below 3300 after the buy sells; a close above does not.
        after = bars + [bar(T0 + dt.timedelta(seconds=1800), 3290.0, 3310.0)]
        calm = p20.reconcile(s, "trend", "30m", after, T0, 3955.0, T0 + dt.timedelta(hours=1))
        self.assertIsNot(calm.get("nl1120_stop_confirmed"), True)
        after2 = after + [bar(T0 + dt.timedelta(seconds=3600), 3200.0, 3250.0)]
        hit = p20.reconcile(calm, "trend", "30m", after2, T0, 3955.0, T0 + dt.timedelta(hours=2))
        self.assertIs(hit["nl1120_stop_confirmed"], True)

    def test_open_position_bought_under_the_average_rule_is_refixed_once_from_the_full_window(self):
        bars = history(200, T0, low=4400.0, close=4450.0)
        bars[10]["low"] = 3300.0; bars[10]["close"] = 3400.0
        old = {"nl1120_stop_status": "IGNORED_ABOVE_ENTRY", "nl1120_stop_price": "4347.1",
               "nl1120_stop_bars": 199, "nl1120_stop_rule": p20.STOP_RULE}
        self.assertTrue(p20.stop_needs_refix("trend", old))
        s = p20.reconcile(old, "trend", "30m", bars, T0, 3955.0, T0 + dt.timedelta(hours=1))
        self.assertEqual(s["nl1120_stop_status"], "ACTIVE")
        self.assertAlmostEqual(float(s["nl1120_stop_price"]), 3300.0)
        self.assertEqual(s["nl1120_stop_rule"], p20.STOP_RULE_TREND_MIN)
        self.assertFalse(p20.stop_needs_refix("trend", s))       # done once
        # Bars already closed after the buy are judged against the NEW stop as well.
        below = bars + [bar(T0 + dt.timedelta(seconds=1800), 3100.0, 3200.0)]
        t = p20.reconcile(old, "trend", "30m", below, T0, 3955.0, T0 + dt.timedelta(hours=2))
        self.assertIs(t["nl1120_stop_confirmed"], True)

    def test_refix_waits_for_the_full_window_and_never_touches_ma_or_triggered_positions(self):
        short = history(120, T0, low=4400.0, close=4450.0)
        old = {"nl1120_stop_status": "IGNORED_ABOVE_ENTRY", "nl1120_stop_price": "4347.1",
               "nl1120_stop_bars": 120, "nl1120_stop_rule": p20.STOP_RULE}
        s = p20.reconcile(old, "trend", "30m", short, T0, 3955.0, T0 + dt.timedelta(hours=1))
        self.assertEqual(s["nl1120_stop_status"], "IGNORED_ABOVE_ENTRY")   # unchanged, nothing invented
        self.assertEqual(s["nl1120_stop_rule"], p20.STOP_RULE)
        self.assertFalse(p20.stop_needs_refix("ma", old))
        self.assertFalse(p20.stop_needs_refix("trend", dict(old, nl1120_stop_confirmed=True)))
        ma = p20.reconcile({}, "ma", "30m", history(60, T0, low=100.0), T0, 104.0, self.NOW)
        self.assertEqual(ma["nl1120_stop_rule"], p20.STOP_RULE)

    def test_stop_above_the_buy_price_is_ignored(self):
        bars = history(60, T0, low=100.0, close=101.0)
        s = p20.reconcile({}, "ma", "30m", bars, T0, 99.0, self.NOW)
        self.assertEqual(s["nl1120_stop_status"], "IGNORED_ABOVE_ENTRY")
        bars2 = bars + [bar(T0 + dt.timedelta(minutes=30), 10.0, 5.0)]
        again = p20.reconcile(s, "ma", "30m", bars2, T0, 99.0, self.NOW)
        self.assertFalse(again.get("nl1120_stop_confirmed"))

    def test_too_few_bars_invents_no_stop_and_retries_later(self):
        s = p20.reconcile({}, "ma", "30m", history(10, T0), T0, 104.0, self.NOW)
        self.assertEqual(s["nl1120_stop_status"], "HISTORY_SHORT")
        self.assertNotIn("nl1120_stop_price", s)
        s = p20.reconcile(s, "ma", "30m", history(60, T0), T0, 104.0, self.NOW)
        self.assertEqual(s["nl1120_stop_status"], "ACTIVE")

    def test_a_partial_window_uses_what_exists_and_records_the_count(self):
        s = p20.reconcile({}, "trend", "30m", history(120, T0, low=100.0), T0, 110.0, self.NOW)
        self.assertEqual((s["nl1120_stop_status"], s["nl1120_stop_bars"]), ("ACTIVE", 120))

    def test_only_a_completed_CLOSE_below_the_stop_sells_not_a_touch(self):
        bars = history(60, T0, low=100.0, close=105.0)
        touch = bar(T0 + dt.timedelta(minutes=30), low=80.0, close=101.0)   # wick below, close above
        s = p20.reconcile({}, "ma", "30m", bars + [touch], T0, 104.0, self.NOW)
        self.assertEqual(s["nl1120_stop_status"], "ACTIVE")
        self.assertFalse(s.get("nl1120_stop_confirmed"))
        close_below = bar(T0 + dt.timedelta(minutes=60), low=80.0, close=99.0)
        s = p20.reconcile(s, "ma", "30m", bars + [touch, close_below], T0, 104.0, self.NOW)
        self.assertTrue(s["nl1120_stop_confirmed"])
        self.assertEqual(s["nl1120_stop_closed_bar"], close_below["end"].astimezone(
            dt.timezone.utc).isoformat())

    def test_close_equal_to_the_stop_is_not_below(self):
        bars = history(60, T0, low=100.0)
        eq = bar(T0 + dt.timedelta(minutes=30), 90.0, 100.0)
        s = p20.reconcile({}, "ma", "30m", bars + [eq], T0, 104.0, self.NOW)
        self.assertFalse(s.get("nl1120_stop_confirmed"))

    def test_bars_before_the_buy_never_trigger_and_unverified_bars_are_ignored(self):
        bars = history(60, T0, low=100.0)
        bars[-1]["close"] = 50.0          # pre-buy bar closes low: part of the window only
        s = p20.reconcile({}, "ma", "30m", bars, T0, 104.0, self.NOW)
        self.assertFalse(s.get("nl1120_stop_confirmed"))
        fake = bar(T0 + dt.timedelta(minutes=30), 1.0, 1.0, source="REST_GUESS")
        forming = bar(self.NOW + dt.timedelta(minutes=30), 1.0, 1.0)
        s = p20.reconcile(s, "ma", "30m", bars + [fake, forming], T0, 104.0, self.NOW)
        self.assertFalse(s.get("nl1120_stop_confirmed"))

    def test_stop_is_fixed_once_and_survives_a_rolling_history(self):
        bars = history(60, T0, low=100.0)
        bars[-2]["low"] = 92.0
        s = p20.reconcile({}, "ma", "30m", bars, T0, 104.0, self.NOW)
        price = s["nl1120_stop_price"]
        rolled = bars[-5:]                 # cache rolled forward: old bars are gone
        s2 = p20.reconcile(s, "ma", "30m", rolled, T0, 104.0, self.NOW)
        self.assertEqual(s2["nl1120_stop_price"], price)

    def test_ma_frames_all_supported(self):
        for frame in ("5m", "30m", "60m", "day"):
            s = p20.reconcile({}, "ma", frame, history(60, T0, step=WIDTH[frame]),
                              T0, 104.0, self.NOW)
            self.assertEqual(s["nl1120_stop_status"], "ACTIVE", frame)
        self.assertIsNone(p20.stop_bar_count("trend", "5m"))


class ExitContracts(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=KST)

    def state(self, kind, stage, **extra):
        return {**pol.targets(100, "30m", kind), "market": "coin", "qty": 100., "qty0": 100.,
                "stage": stage, "seen_above_entry": True, **extra}

    def test_shared_exit_signal_sells_all_on_the_flag_at_any_stage(self):
        for kind in ("ma", "trend"):
            for stage in (0, 1, 2):
                s = self.state(kind, stage, nl1120=True, nl1120_stop_confirmed=True)
                self.assertEqual(pol.exit_signal(s, 100.5, self.NOW),
                                 ("NL1120_LOOKBACK_LOW_CLOSE_STOP", 1.0), (kind, stage))

    def test_rev1_low_exit_is_retired_for_new_positions_but_kept_for_old_ones(self):
        for kind in ("ma", "trend"):
            old = self.state(kind, 0, rev1_below_reference_low_confirmed=True)
            self.assertEqual(pol.exit_signal(old, 100.5, self.NOW),
                             ("NL1118_REV1_PRE_FIRST_REFERENCE_LOW_CLOSED", 1.0))
            new = self.state(kind, 0, nl1120=True, rev1_below_reference_low_confirmed=True)
            self.assertIsNone(pol.exit_signal(new, 100.5, self.NOW))

    def test_hard_stop_and_expiry_still_win_and_after_first_plus_1_percent_is_unchanged(self):
        s = self.state("trend", 0, nl1120=True, nl1120_stop_confirmed=True)
        self.assertEqual(pol.exit_signal(s, 84.0, self.NOW)[0], "NL1118_HARD_STOP_15PCT")
        s1 = self.state("trend", 1, nl1120=True)
        self.assertEqual(pol.exit_signal(s1, 101.0, self.NOW),
                         ("NL1118_AFTER_FIRST_PLUS_1_STOP", 1.0))

    def ma_state(self, **extra):
        base = map_.initial_up_exit_state("30m")
        base["entry_at"] = (self.NOW - dt.timedelta(hours=2)).isoformat()
        return {**base, **extra}

    def test_ma_up_exit_uses_the_new_stop_and_ignores_rev1_for_new_positions(self):
        args = (Decimal("100"), 100, 100, self.NOW)
        updated, want, reason = map_.up_exit(
            self.ma_state(nl1120=True, nl1120_stop_confirmed=True), Decimal("100.5"),
            Decimal("100"), 100, 100, self.NOW)
        self.assertEqual((reason, want), ("MA_NL1120_LOOKBACK_LOW_CLOSE_STOP", 100))
        _, _, reason = map_.up_exit(
            self.ma_state(nl1120=True, rev1_below_reference_low_confirmed=True),
            Decimal("100.5"), Decimal("100"), 100, 100, self.NOW)
        self.assertEqual(reason, "")
        _, _, reason = map_.up_exit(
            self.ma_state(rev1_below_reference_low_confirmed=True),
            Decimal("100.5"), Decimal("100"), 100, 100, self.NOW)
        self.assertEqual(reason, "MA_V018_PRE_FIRST_VERIFIED_LOW_CLOSE")

    def test_ma_hard_stop_minus_15_still_has_priority(self):
        _, _, reason = map_.up_exit(
            self.ma_state(nl1120=True, nl1120_stop_confirmed=True),
            Decimal("84"), Decimal("100"), 100, 100, self.NOW)
        self.assertEqual(reason, "MA_V1118_STOP_MINUS_15")

    def test_new_reasons_are_accepted_and_labelled_everywhere(self):
        coin = (Path(__file__).resolve().parents[1] /
                "app/src/main/python/ma_v067_coin_runtime.py").read_text("utf8")
        self.assertIn("'MA_NL1120_LOOKBACK_LOW_CLOSE_STOP'", coin)   # silent-reject whitelist
        basis = (Path(__file__).resolve().parents[1] /
                 "app/src/main/python/trade_decision_basis_v095.py").read_text("utf8")
        self.assertIn("MA_NL1120_LOOKBACK_LOW_CLOSE_STOP", basis)
        self.assertIn("NL1120_LOOKBACK_LOW_CLOSE_STOP", basis)

    def test_every_exit_reason_the_ma_policy_can_emit_is_accepted_by_the_coin_ledger(self):
        """Silent-failure guard: a sell reason missing from the coin accept-list is
        rejected WITHOUT any error (this once blocked every +2% first sale)."""
        import re
        root = Path(__file__).resolve().parents[1] / "app/src/main/python"
        policy = (root / "ma_v067_policy.py").read_text("utf8")
        body = policy[policy.index("def up_exit"):policy.index("def apply_up_fill")]
        emitted = {r for r in re.findall(r"'(MA_[A-Z0-9_]+)'", body)
                   if not r.endswith("REVERSAL_UP")}          # policy names, not sell reasons
        coin = (root / "ma_v067_coin_runtime.py").read_text("utf8")
        gate = coin[coin.index("if row is None or str(reason) not in ("):]
        accepted = set(re.findall(r"'(MA_[A-Z0-9_]+)'", gate[:gate.index("):")]))
        self.assertEqual(emitted - accepted, set(), "reasons silently rejected by the coin ledger")

    def test_new_positions_are_marked_and_stored_durably(self):
        root = Path(__file__).resolve().parents[1] / "app/src/main/python"
        stock = (root / "ma_v067_stock_runtime.py").read_text("utf8")
        coin = (root / "ma_v067_coin_runtime.py").read_text("utf8")
        self.assertIn("state['nl1120']=True", stock)
        self.assertIn("state['nl1120']=True", coin)
        self.assertIn("_store_state(c,p['id'],state,now)", stock)
        self.assertIn("UPDATE ma_v067_positions SET state_json=?", coin)


class FirstCloseAboveMiddle(unittest.TestCase):
    def rows(self, closes, last_end):
        n = len(closes)
        return [{"end": last_end - dt.timedelta(minutes=30 * (n - 1 - i)), "close": c}
                for i, c in enumerate(closes)]

    def test_returns_the_first_bar_after_the_watch_that_closed_above_its_middle(self):
        closes = [100.0] * 24 + [99.0, 101.0, 102.0]
        rows = self.rows(closes, T0)
        started = rows[24]["end"]
        self.assertEqual(p20.first_close_above_middle_end(rows, started), rows[25]["end"])
        # the second qualifying bar is NOT the first one
        self.assertNotEqual(p20.first_close_above_middle_end(rows, started), rows[26]["end"])

    def test_bars_that_ended_before_the_watch_do_not_count(self):
        closes = [100.0] * 20 + [110.0] + [100.0] * 2 + [101.0]
        rows = self.rows(closes, T0)
        started = rows[22]["end"]
        self.assertEqual(p20.first_close_above_middle_end(rows, started), rows[23]["end"])

    def test_none_when_nothing_closed_above_or_history_is_short(self):
        rows = self.rows([100.0] * 30, T0)
        self.assertIsNone(p20.first_close_above_middle_end(rows, rows[0]["end"]))
        self.assertIsNone(p20.first_close_above_middle_end(self.rows([100.0, 101.0], T0), None))


def padded(base_closes, pad=4):
    return [100.0] * pad + list(base_closes)


def ramp(k, last=None):
    s = [100.0] * 21
    s[3], s[7] = 95.0, 105.0
    for i in range(10, 21):
        s[i] = 100.0 + (i - 10) * k
    if last is not None:
        s[-1] = last
    return s


class _Wallet:
    def __init__(self):
        self.buys = []

    def stop_reentry_blocked(self, *a):
        return False

    def owned(self, *a):
        return False

    def buy(self, market, frame, code, name, ask, ask_size, at, barid, rsi):
        self.buys.append((market, frame, code, ask))
        return SimpleNamespace(execution_mode="PAPER", entry=ask, qty=1.0, frame=frame), "BOUGHT"


class _Journal:
    def __init__(self):
        self.events = []

    def stop_reentry_blocked(self, *a):
        return False

    def event(self, *a, **k):
        self.events.append((a, k))


def bars_ending(closes, last_end):
    return [{"close": c, "end": last_end - dt.timedelta(minutes=(len(closes) - 1 - i) * 30),
             "source": "UPBIT_CLOSED"} for i, c in enumerate(closes)]


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class RuntimeFirstCloseRule(unittest.TestCase):
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

    def step(self, co, closes, bar_end, now, rsi, price):
        co._decision_clock = lambda: now
        co._history[("coin", "KRW-TEST", "30m")] = bars_ending(closes, bar_end)
        co._quotes["coin"]["KRW-TEST"] = (price, time.monotonic() + 0.001 * (1 + len(
            co.standalone_wallet.buys)) + self._tick())
        with patch.object(nl, "_ready_market", return_value=True), \
             patch.object(mt, "rsi20", return_value=rsi):
            nl._entry_window(co, "coin", now)

    _n = 0

    def _tick(self):
        RuntimeFirstCloseRule._n += 1
        return RuntimeFirstCloseRule._n * 0.01

    def continuation(self, closes):
        """A next close that keeps an allowed slope and stays above the MIDDLE."""
        for value in [100.0 + 0.5 * i for i in range(0, 30)]:
            trial = closes + [value]
            if pol.closed_bar_entry_condition(trial) is not None:
                return value
        self.fail("fixture must find a valid continuation")

    def test_a_valid_first_close_buys(self):
        with tempfile.TemporaryDirectory() as root:
            first = padded(ramp(0.3))
            co = self.make(root, T0 + dt.timedelta(minutes=5))
            self.step(co, first[:-1], T0, T0 + dt.timedelta(minutes=5), 25.0, 100.0)
            self.step(co, first, T0 + dt.timedelta(minutes=30),
                      T0 + dt.timedelta(minutes=30, seconds=5), 25.0, 103.0)
            self.assertEqual(len(co.standalone_wallet.buys), 1)

    def test_after_a_failed_first_close_a_later_valid_close_does_not_buy(self):
        """First bar closes above MIDDLE but the slope is 21 deg (not allowed);
        the NEXT bar is perfectly valid yet must NOT buy: the cycle is consumed.
        A new cycle needs RSI>30 and then RSI<=30 again."""
        with tempfile.TemporaryDirectory() as root:
            first = padded(ramp(0.4))                       # above MIDDLE, slope 21 deg
            self.assertIsNone(pol.closed_bar_entry_condition(first))
            nxt = first + [self.continuation(first)]
            self.assertIsNotNone(pol.closed_bar_entry_condition(nxt))
            co = self.make(root, T0 + dt.timedelta(minutes=5))
            self.step(co, first[:-1], T0, T0 + dt.timedelta(minutes=5), 25.0, 100.0)
            self.step(co, first, T0 + dt.timedelta(minutes=30),
                      T0 + dt.timedelta(minutes=30, seconds=5), 25.0, 103.0)
            self.assertEqual(len(co.standalone_wallet.buys), 0)
            self.step(co, nxt, T0 + dt.timedelta(minutes=60),
                      T0 + dt.timedelta(minutes=60, seconds=5), 25.0, 104.0)
            self.assertEqual(len(co.standalone_wallet.buys), 0, "second close must not buy")
            watch = co._nl1118_watches[("coin", "KRW-TEST", "30m")]
            self.assertEqual(watch["status"], "WATCHING")

    def test_consumed_cycle_is_released_only_after_rsi_rises_above_30(self):
        with tempfile.TemporaryDirectory() as root:
            first = padded(ramp(0.4))
            nxt = first + [self.continuation(first)]
            co = self.make(root, T0 + dt.timedelta(minutes=5))
            self.step(co, first[:-1], T0, T0 + dt.timedelta(minutes=5), 25.0, 100.0)
            self.step(co, first, T0 + dt.timedelta(minutes=30),
                      T0 + dt.timedelta(minutes=30, seconds=5), 25.0, 103.0)
            self.step(co, nxt, T0 + dt.timedelta(minutes=60),
                      T0 + dt.timedelta(minutes=60, seconds=5), 40.0, 104.0)
            self.assertEqual(len(co.standalone_wallet.buys), 0)
            self.assertEqual(co._nl1118_watches[("coin", "KRW-TEST", "30m")]["status"], "READY")


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class TrendObserverAndExit(unittest.TestCase):
    """The closed-bar observer fixes the stop once, persists it and the exit
    action then sells everything; old (pre-11.20(2)) positions are untouched."""

    def position(self, frame="30m"):
        p = mt.TrendPosition("coin", "trend", frame, "KRW-TEST", T0 + dt.timedelta(seconds=5),
                             105.0, 10.0, "bar")
        p.__dict__.update({"nl1118_policy": pol.POLICY, "bar_finalized": True, "nl1120": True})
        p.target1, p.target2 = 110.0, 114.0
        return p

    def co(self, history_rows):
        saved = []
        wallet = SimpleNamespace(save_position=lambda pos: saved.append(dict(pos.__dict__)))
        return SimpleNamespace(standalone_wallet=wallet, _last_fetch_error={},
                               rt=SimpleNamespace(stock_runner=None, coin_runner=None),
                               _history={("coin", "KRW-TEST", "30m"): history_rows}), saved

    def test_observer_fixes_the_stop_then_a_close_below_sells_all(self):
        p = self.position()
        rows = history(210, T0, low=100.0, close=106.0)
        co, saved = self.co(rows)
        histories = {("coin", "KRW-TEST", "30m"): rows}
        with patch.object(nl, "_nl1120_deep_rows", side_effect=lambda self_, pos, r: r):
            nl._nl1120_after_closed(co, [p], histories, now=T0 + dt.timedelta(minutes=10))
        self.assertEqual(p.nl1120_stop_status, "ACTIVE")
        self.assertAlmostEqual(float(p.nl1120_stop_price), 100.0)
        self.assertTrue(saved, "the fixed stop must be persisted")
        later = T0 + dt.timedelta(hours=3)
        self.assertIsNone(nl._trend_exit_action(p, 106.0, later))
        rows2 = rows + [bar(T0 + dt.timedelta(minutes=30), 90.0, 99.0)]
        with patch.object(nl, "_nl1120_deep_rows", side_effect=lambda self_, pos, r: r):
            nl._nl1120_after_closed(co, [p], {("coin", "KRW-TEST", "30m"): rows2}, now=later)
        self.assertTrue(p.nl1120_stop_confirmed)
        self.assertEqual(nl._trend_exit_action(p, 106.0, later),
                         ("NL1120_LOOKBACK_LOW_CLOSE_STOP", 1.0))

    def test_open_position_with_the_old_average_stop_gets_one_deep_read_and_is_refixed(self):
        p = self.position()
        # State exactly as the 0.1.2-0.1.8 observer left it: average stop above the buy price, ignored.
        p.__dict__.update({"nl1120_stop_status": "IGNORED_ABOVE_ENTRY", "nl1120_stop_price": "140.0",
                           "nl1120_stop_bars": 199, "nl1120_stop_rule": p20.STOP_RULE,
                           "nl1120_deep_tried": True})
        shallow = history(100, T0, low=120.0, close=125.0)           # in-memory cache is short
        full = history(210, T0, low=120.0, close=125.0)
        full[-50]["low"] = 98.0                                         # real minimum below the buy price
        co, saved = self.co(shallow)
        calls = []
        def deep(self_, pos, r):
            calls.append(1)
            return full
        for minute in range(3):
            with patch.object(nl, "_nl1120_deep_rows", side_effect=deep):
                nl._nl1120_after_closed(co, [p], {("coin", "KRW-TEST", "30m"): shallow},
                                        now=T0 + dt.timedelta(minutes=10 + minute))
        self.assertEqual(p.nl1120_stop_status, "ACTIVE")
        self.assertAlmostEqual(float(p.nl1120_stop_price), 98.0)
        self.assertEqual(p.nl1120_stop_rule, p20.STOP_RULE_TREND_MIN)
        self.assertEqual(len(calls), 1, "one deep read, then the position is on the new rule")
        self.assertTrue(saved, "the re-fixed stop must be persisted")

    def test_deep_history_is_read_only_until_the_stop_is_fixed(self):
        """A fixed stop must stop the per-cycle deep KRX/Upbit history read."""
        p = self.position()
        rows = history(210, T0, low=100.0, close=106.0)
        co, _ = self.co(rows)
        calls = []
        def deep(self_, pos, r):
            calls.append(1)
            return r
        with patch.object(nl, "_nl1120_deep_rows", side_effect=deep):
            for minute in range(3):
                nl._nl1120_after_closed(co, [p], {("coin", "KRW-TEST", "30m"): rows},
                                        now=T0 + dt.timedelta(minutes=10 + minute))
        self.assertEqual(p.nl1120_stop_status, "ACTIVE")
        self.assertEqual(len(calls), 1, "deep history must be read once, then never again")

    def test_old_position_without_the_marker_is_not_touched_by_the_new_observer(self):
        p = self.position()
        p.__dict__["nl1120"] = False
        self.assertFalse(getattr(p, "nl1120"))
        p.__dict__["nl1120_stop_confirmed"] = True      # even a stray flag cannot sell it
        # 0.3.2: a fixed time inside the hold window. `now()` turned this into a hold-deadline
        # test once the real date passed the fixed T0 + hold period (failed from 2026-11-23).
        self.assertIsNone(nl._trend_exit_action(p, 106.0, T0 + dt.timedelta(hours=1)))


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class StockMaStopFromVerifiedBars(unittest.TestCase):
    def test_stock_ma_reads_bars_before_and_after_the_buy_from_the_kiwoom_store(self):
        import sqlite3
        import ma_v067_stock_runtime as stock
        with tempfile.TemporaryDirectory() as root:
            db = Path(root) / "bars.sqlite3"
            with sqlite3.connect(db) as c:
                c.execute("""CREATE TABLE bars(code TEXT,market TEXT DEFAULT '',frame TEXT,
                    ts TEXT,open INT,high INT,low INT,close INT,volume INT,source TEXT)""")
                entry = dt.datetime(2026, 11, 20, 10, 0, tzinfo=KST)
                for i in range(60):                       # 60 completed 30m bars before the buy
                    start = entry - dt.timedelta(minutes=30 * (60 - i))
                    low = 9000 if i == 40 else 9500
                    c.execute("INSERT INTO bars VALUES('005930','','30m',?,0,?,?,?,0,'KIWOOM_VERIFIED')",
                              (start.strftime("%Y%m%d%H%M%S"), 10100, low, 10000))
                # first bar after the buy CLOSES below the 9000 stop (wick alone would not)
                after = entry
                c.execute("INSERT INTO bars VALUES('005930','','30m',?,0,?,?,?,0,'KIWOOM_VERIFIED')",
                          (after.strftime("%Y%m%d%H%M%S"), 9800, 8800, 8900))
            class Store:
                def connect(self_):
                    conn = sqlite3.connect(db)
                    conn.row_factory = sqlite3.Row
                    return conn
            fake = SimpleNamespace(market_store=Store())
            position = {"code": "005930", "tf": "30m", "entry": 10000,
                        "entry_at": entry.isoformat()}
            now = entry + dt.timedelta(hours=1)
            state = stock._nl1120_stock_stop_state(fake, {"nl1120": True}, position, now)
            self.assertEqual(state["nl1120_stop_status"], "ACTIVE")
            self.assertEqual(float(state["nl1120_stop_price"]), 9000.0)
            self.assertTrue(state["nl1120_stop_confirmed"])
            # the retired rev1 helper is not used for new positions
            same = stock._rev1_stock_closed_bar_state(fake, {"nl1120": True}, position, now)
            self.assertTrue(same["nl1120_stop_confirmed"])


if __name__ == "__main__":
    unittest.main()
