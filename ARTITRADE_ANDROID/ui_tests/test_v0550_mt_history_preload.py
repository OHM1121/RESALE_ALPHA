"""0.5.5: stock MarketTrend history is filled from LOCAL bars before the open; the broker is not asked in a burst; a virtual candidate runs to a BUY.

Synthetic data only (no broker, no network, no ledger). The vendor Coordinator class and its `_raw_history` are the REAL ones.
The 휴림로봇 case of 2026-11-18 (first bar after the open closed above the Bollinger middle while the middle line was still falling) is reproduced with
the real policy code: it must NOT buy, and the cause must be the slope window, not missing history.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
import new_legacy_1118_policy as pol

try:
    import market_trend_v043 as mt
    import market_trend_1118_runtime as nl
    import market_trend_runtime_v043 as runtime
    import market_trend_history_v0550 as h
    RUNTIME = True
except Exception:  # pragma: no cover - needs the original engine on sys.path (CI provides it)
    RUNTIME = False

KST = dt.timezone(dt.timedelta(hours=9))
CODE = "090710"
DAYS = (dt.date(2026, 11, 24), dt.date(2026, 11, 25), dt.date(2026, 11, 26))


def flat_with_wicks(n, last):
    s = [100.0] * n
    s[3], s[7] = 95.0, 105.0
    s[-1] = last
    return s


def falling(last):
    return [110.0] * 6 + [110 - i * 0.8 for i in range(1, 20)] + [last]      # 26 closes; the middle line is still falling


def stamps(upto_day, upto_start_hhmm):
    """30m bar start stamps (YYYYMMDDHHMMSS) from the first day up to and including the given day's bar."""
    out = []
    for day in DAYS:
        for k in range(13):
            minutes = 9 * 60 + k * 30
            stamp = "%s%02d%02d00" % (day.strftime("%Y%m%d"), minutes // 60, minutes % 60)
            out.append(stamp)
            if day == upto_day and stamp[8:12] == upto_start_hhmm:
                return out
    return out


def make_db(closes, last_stamp_day, last_stamp_hhmm):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE bars(code TEXT,market TEXT,frame TEXT,ts TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT)")
    ts = stamps(last_stamp_day, last_stamp_hhmm)[-len(closes):]
    assert len(ts) == len(closes), (len(ts), len(closes))
    for stamp, close in zip(ts, closes):
        conn.execute("INSERT INTO bars VALUES(?,?,?,?,?,?,?,?,?,?)", (CODE, "KOSDAQ", "30m", stamp, close, close + 1, close - 1, close, 1000, "FIX15_VERIFIED_KIWOOM_REST"))
    conn.commit()
    return conn


class _Conn:
    """A shared in-memory connection whose `with` block does not close it."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *a):
        self.conn.commit()
        return False


import contextlib
import types


@contextlib.contextmanager
def clock(now, regular=True):
    """Fix `now` for the overlay and for the vendor Coordinator, and the exchange-open flag."""
    class FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    shim = types.SimpleNamespace(**{k: getattr(dt, k) for k in dir(dt) if not k.startswith("__")})
    shim.datetime = FakeDT
    with patch.object(runtime, "dt", shim), patch.object(h, "_now", lambda: now), patch("krx_session_v041.is_regular_open", return_value=regular):
        yield


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


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class Base(unittest.TestCase):
    def setUp(self):
        h._STATE["last_rest"] = 0.0
        h._STATE["key_rest"].clear()
        for key in h._STATE["stats"]:
            h._STATE["stats"][key] = 0

    def coordinator(self, root, conn, api=None):
        co = SimpleNamespace()
        store = SimpleNamespace(connect=lambda: _Conn(conn))
        co.rt = SimpleNamespace(root=root, stock_runner=SimpleNamespace(store=store, api=api or MagicMock()), coin_runner=None)
        co.lock = threading.RLock()
        co.stop_event = threading.Event()
        co._candidates = {"stock": {CODE: ("KOSDAQ", "휴림로봇")}, "coin": {}}
        co._positions = {}
        co._history = {}
        co._history_requests = {}
        co._quotes = {"stock": {}, "coin": {}}
        co.journal = _Journal()
        co.standalone_wallet = _Wallet()
        co._last_fetch_error = {}
        co._decision_clock = lambda: dt.datetime.now(KST)
        co.mode = lambda market: "ORIGINAL"
        co._quote = lambda market, code: {"trade": co._quotes[market][code][0], "bid": 99.0, "ask": 101.0, "ask_size": 1000.0, "bid_size": 1000.0}
        co._raw_history = lambda market, code, frame: runtime.Coordinator._raw_history(co, market, code, frame)
        co._record_entry_block = lambda *a, **k: None
        co._entry_block_counts = {"stock": __import__("collections").Counter(), "coin": __import__("collections").Counter()}
        co._recent_blocked_entries = {"stock": __import__("collections").deque(maxlen=50), "coin": __import__("collections").deque(maxlen=50)}
        return co


class CarryOverAtTheOpen(Base):
    def test_previous_session_last_bar_is_current_until_the_first_bar_completes(self):
        prev = dt.datetime(2026, 11, 25, 15, 30, tzinfo=KST)
        for hhmm, frame, expected in (((9, 0, 5), "30m", True), ((9, 29, 59), "30m", True), ((9, 30, 5), "30m", False),
                                      ((9, 59, 0), "60m", True), ((10, 0, 5), "60m", False), ((9, 40, 0), "day", True),
                                      ((15, 0, 0), "day", True)):
            now = dt.datetime(2026, 11, 26, *hhmm, tzinfo=KST)
            self.assertEqual(h.local_is_current(prev, now, frame), expected, (hhmm, frame))

    def test_monday_morning_accepts_friday_bars(self):
        friday_close = dt.datetime(2026, 11, 20, 15, 30, tzinfo=KST)
        monday = dt.datetime(2026, 11, 23, 9, 10, tzinfo=KST)
        self.assertTrue(h.local_is_current(friday_close, monday, "30m"))
        self.assertFalse(h.local_is_current(dt.datetime(2026, 11, 19, 15, 30, tzinfo=KST), monday, "30m"))

    def test_vendor_rule_is_kept_inside_the_session(self):
        newest = dt.datetime(2026, 11, 26, 11, 0, tzinfo=KST)
        self.assertTrue(h.local_is_current(newest, dt.datetime(2026, 11, 26, 12, 20, tzinfo=KST), "30m"))
        self.assertFalse(h.local_is_current(newest, dt.datetime(2026, 11, 26, 13, 0, tzinfo=KST), "30m"))


class NoBrokerBurstAtTheOpen(Base):
    def test_open_reads_local_bars_and_never_calls_the_broker(self):
        conn = make_db(flat_with_wicks(26, 100.0), DAYS[1], "1500")
        with tempfile.TemporaryDirectory() as root:
            api = MagicMock()
            api.history_bars.side_effect = ConnectionError("No address associated with hostname")
            co = self.coordinator(root, conn, api)
            now = dt.datetime(2026, 11, 26, 9, 0, 20, tzinfo=KST)
            with clock(now, True):
                co._raw_history = h.make_raw_history(runtime.Coordinator._raw_history).__get__(co)
                bars = co._raw_history("stock", CODE, "30m")
            self.assertGreaterEqual(len(bars), 21)
            self.assertEqual(api.history_bars.call_count, 0)
            self.assertEqual(h._STATE["stats"]["carry_over_served"], 1)

    def test_the_old_rule_asked_the_broker_for_the_same_pair(self):
        """Regression evidence: the vendor function, untouched, goes to the broker at 09:00:20 although the local bars are complete."""
        conn = make_db(flat_with_wicks(26, 100.0), DAYS[1], "1500")
        with tempfile.TemporaryDirectory() as root:
            api = MagicMock()
            api.history_bars.return_value = []
            co = self.coordinator(root, conn, api)
            now = dt.datetime(2026, 11, 26, 9, 0, 20, tzinfo=KST)
            original = runtime.Coordinator._raw_history
            while hasattr(original, "__wrapped_v0550__"):
                original = original.__wrapped_v0550__
            with clock(now, True):
                original(co, "stock", CODE, "30m")
            self.assertEqual(api.history_bars.call_count, 1)

    def test_a_genuinely_stale_series_asks_the_broker_but_not_in_a_burst(self):
        conn = make_db(flat_with_wicks(26, 100.0), DAYS[1], "1500")
        with tempfile.TemporaryDirectory() as root:
            api = MagicMock()
            api.history_bars.return_value = []
            co = self.coordinator(root, conn, api)
            now = dt.datetime(2026, 11, 26, 11, 40, 0, tzinfo=KST)         # the first bars of today are missing locally
            with clock(now, True):
                co._raw_history = h.make_raw_history(runtime.Coordinator._raw_history).__get__(co)
                for code in ("090710", "000001", "000002", "000003"):
                    co._raw_history("stock", code, "30m")
            self.assertEqual(api.history_bars.call_count, 1, "one broker call per second in total")
            self.assertEqual(h._STATE["stats"]["rest_allowed"], 1)
            self.assertGreaterEqual(h._STATE["stats"]["rest_throttled"], 3)
            h._STATE["last_rest"] = time.monotonic() - 5
            with clock(now, True):
                co._raw_history("stock", "090710", "30m")
            self.assertEqual(api.history_bars.call_count, 1, "the same pair keeps its cooldown")

    def test_outside_the_session_local_bars_are_served_as_before(self):
        conn = make_db(flat_with_wicks(26, 100.0), DAYS[1], "1500")
        with tempfile.TemporaryDirectory() as root:
            api = MagicMock()
            co = self.coordinator(root, conn, api)
            now = dt.datetime(2026, 11, 26, 7, 30, 0, tzinfo=KST)
            with clock(now, False):
                co._raw_history = h.make_raw_history(runtime.Coordinator._raw_history).__get__(co)
                self.assertGreaterEqual(len(co._raw_history("stock", CODE, "30m")), 21)
            self.assertEqual(api.history_bars.call_count, 0)


class Preload(Base):
    def test_preload_fills_the_empty_slot_and_never_overwrites_a_fresher_one(self):
        conn = make_db(flat_with_wicks(26, 100.0), DAYS[1], "1500")
        with tempfile.TemporaryDirectory() as root:
            co = self.coordinator(root, conn)
            now = dt.datetime(2026, 11, 26, 8, 40, tzinfo=KST)
            self.assertEqual(h.preload(co, now), 1)
            key = ("stock", CODE, "30m")
            self.assertGreaterEqual(len(co._history[key]), 21)
            fresher = [dict(r) for r in co._history[key]]
            fresher.append(dict(fresher[-1], end=fresher[-1]["end"] + dt.timedelta(minutes=30)))
            co._history[key] = fresher
            self.assertEqual(h.preload(co, now + dt.timedelta(minutes=1)), 0)
            self.assertIs(co._history[key], fresher)
            self.assertEqual(h._STATE["stats"]["preloaded"], 1)

    def test_pairs_without_enough_local_bars_are_left_empty(self):
        conn = make_db(flat_with_wicks(26, 100.0)[-10:], DAYS[1], "1500")
        with tempfile.TemporaryDirectory() as root:
            co = self.coordinator(root, conn)
            self.assertEqual(h.preload(co, dt.datetime(2026, 11, 26, 8, 40, tzinfo=KST)), 0)
            self.assertNotIn(("stock", CODE, "30m"), co._history)


class VirtualCandidateEndToEnd(Base):
    """A virtual candidate runs through the REAL entry logic with history that came from the local bars (no broker)."""

    def run_timeline(self, closes_at_t1, closes_at_t2, t2_day=DAYS[2], t2_hhmm="0900"):
        t1 = dt.datetime(2026, 11, 25, 15, 10, 5, tzinfo=KST)
        t2 = dt.datetime(2026, 11, 26, 9, 31, 5, tzinfo=KST)
        with tempfile.TemporaryDirectory() as root:
            conn1 = make_db(closes_at_t1, DAYS[1], "1500")             # bars up to the 15:00 start (its end 15:30 is in the future at t1)
            co = self.coordinator(root, conn1)
            api = co.rt.stock_runner.api
            api.history_bars.side_effect = ConnectionError("broker unreachable")      # the broker is down the whole time
            with patch.object(nl, "_ready_market", return_value=True), patch.object(mt, "rsi20", return_value=25.0), \
                 patch("krx_session_v041.is_regular_open", return_value=True):
                co._decision_clock = lambda: t1
                co._quotes["stock"][CODE] = (100.0, time.monotonic())
                h.preload(co, t1)
                nl._entry_window(co, "stock", t1)
                armed = len(co._nl1118_watches)
                after_arm = len(co.standalone_wallet.buys)
                # next morning
                co.rt.stock_runner.store = SimpleNamespace(connect=lambda: _Conn(make_db(closes_at_t2, t2_day, t2_hhmm)))
                co._history.clear()
                co._decision_clock = lambda: t2
                h.preload(co, t2)
                co._quotes["stock"][CODE] = (closes_at_t2[-1], time.monotonic() + 0.001)
                nl._entry_window(co, "stock", t2)
            return co, armed, after_arm, api

    def test_virtual_candidate_buys_once_after_the_signal_bar_completes_with_the_broker_down(self):
        base = flat_with_wicks(27, 100.0)               # the last close is the 09:00 bar of the next day; every earlier bar is <= its middle
        closes_t1 = base[:-1]
        closes_t2 = base[:-1] + [101.0]                 # +11 degrees, above the middle
        co, armed, after_arm, api = self.run_timeline(closes_t1, closes_t2)
        self.assertEqual(armed, 1)
        self.assertEqual(after_arm, 0, "the bar that ended before the watch began must not buy")
        self.assertEqual(len(co.standalone_wallet.buys), 1)
        market, frame, code, ask, _bar, rsi = co.standalone_wallet.buys[0]
        self.assertEqual((market, frame, code, ask, rsi), ("stock", "30m", CODE, 101.0, 25.0))
        self.assertEqual(api.history_bars.call_count, 0, "the whole decision used local bars; the broker was never needed")
        payload = co.journal.events[0][1]["payload"]
        self.assertEqual(payload["entry_rule"], pol.TREND_ENTRY_RULE_1120)

    def test_steep_slope_does_not_buy_and_is_not_a_history_problem(self):
        base = flat_with_wicks(27, 100.0)
        co, armed, _, api = self.run_timeline(base[:-1], base[:-1] + [103.0])      # 30 degrees > the +20 limit
        self.assertEqual(armed, 1)
        self.assertEqual(len(co.standalone_wallet.buys), 0)
        self.assertEqual(api.history_bars.call_count, 0)

    def test_hurimrobot_case_first_bar_above_the_middle_with_a_falling_middle_does_not_buy(self):
        """2026-11-18: the first bar after the open closed above the Bollinger middle, but the middle line was still falling (-19 degrees on the real
        bars; -21 here). The rule allows ONLY that first above-middle bar; it failed the slope window, so there is no buy, by design."""
        closes = falling(104.0)
        closes_t1 = closes[:-1]
        b = pol.bollinger(closes)
        self.assertTrue(closes[-1] > b["middle"])
        self.assertLess(b["angle_deg"], pol.BOLL_ANGLE_MIN)
        co, armed, _, api = self.run_timeline(closes_t1, closes)
        self.assertEqual(armed, 1)
        self.assertEqual(len(co.standalone_wallet.buys), 0)
        self.assertEqual(api.history_bars.call_count, 0)

    def test_a_later_bar_never_buys_after_the_first_above_middle_bar_failed(self):
        closes = falling(104.0)
        later = closes + [closes[-1] + 0.2]               # next bar: still above the middle, slope now allowed or not, it is no longer the first
        first = pol.first_close_above_middle_end if hasattr(pol, "first_close_above_middle_end") else None
        from new_legacy_1120_policy import first_close_above_middle_end
        rows = [{"end": dt.datetime(2026, 11, 25, 9, 30, tzinfo=KST) + dt.timedelta(minutes=30 * i), "close": c} for i, c in enumerate(later)]
        started = rows[-3]["end"] - dt.timedelta(minutes=1)
        self.assertEqual(first_close_above_middle_end(rows, started), rows[-2]["end"])
        self.assertNotEqual(first_close_above_middle_end(rows, started), rows[-1]["end"])


class InstallWiring(Base):
    def test_install_wraps_the_vendor_methods_once(self):
        with patch.object(runtime.Coordinator, "_raw_history", runtime.Coordinator._raw_history), patch.object(runtime.Coordinator, "start", runtime.Coordinator.start), \
             patch.dict(h._STATE, {"installed": False}):
            before = runtime.Coordinator._raw_history
            h.install()
            self.assertTrue(hasattr(runtime.Coordinator._raw_history, "__wrapped_v0550__"))
            self.assertIsNot(runtime.Coordinator._raw_history, before)
            wrapped = runtime.Coordinator._raw_history
            h.install()
            self.assertIs(runtime.Coordinator._raw_history, wrapped)


if __name__ == "__main__":
    unittest.main()
