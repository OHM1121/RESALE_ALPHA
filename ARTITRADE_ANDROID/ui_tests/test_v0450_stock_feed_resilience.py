"""0.4.5 (#160): fast reconnect after a healthy socket drops, background yield under pressure, per-minute feed health line."""
import asyncio
import datetime as dt
import json
import tempfile
import types
import unittest
from pathlib import Path

import stock_feed_resilience_v0450 as res

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"
KST = dt.timezone(dt.timedelta(hours=9))
WEEKDAY_OPEN = dt.datetime(2026, 11, 27, 9, 30, tzinfo=KST)          # a Friday
SATURDAY = dt.datetime(2026, 11, 28, 9, 30, tzinfo=KST)
NIGHT = dt.datetime(2026, 11, 27, 21, 0, tzinfo=KST)


def runner(feed=0, tick=0):
    return types.SimpleNamespace(_feed_message_queue=types.SimpleNamespace(qsize=lambda: feed),
                                 _tick_queue=types.SimpleNamespace(qsize=lambda: tick),
                                 _feed_processed=10, _feed_discarded=3, _feed_overflows=0, _slow_ticks=4,
                                 _confirmed_ws_session=True, _feed_high_water=7)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def reset():
    res._STATS.update(yield_calls=0, yield_waited_s=0.0, yield_by_label={}, fast_reconnects=0, reconnect_seen=0)
    res._FAST.update(armed_until=0.0, history=[], ok_at=None)
    res._STATE.update(lines=0, prev={})


class Pressure(unittest.TestCase):
    def setUp(self):
        reset()
        self._lag = res._lag_samples
        res._lag_samples = lambda count=res.LAG_SAMPLES: []

    def tearDown(self):
        res._lag_samples = self._lag

    def test_queue_and_lag_signals(self):
        self.assertIsNone(res.pressure(runner()))
        self.assertIn("FEED_QUEUE", res.pressure(runner(feed=res.FEED_Q_HIGH)))
        self.assertIn("TICK_QUEUE", res.pressure(runner(tick=res.TICK_Q_HIGH)))
        res._lag_samples = lambda count=res.LAG_SAMPLES: [500.0] * res.LAG_SAMPLES
        self.assertIn("SCHEDULING_LAG", res.pressure(runner()))
        res._lag_samples = lambda count=res.LAG_SAMPLES: [500.0, 10, 10, 10, 10, 10, 10, 10]   # one spike is not pressure
        self.assertIsNone(res.pressure(runner()))
        self.assertIsNone(res.pressure(None))

    def test_wait_is_bounded_and_only_in_market_hours(self):
        import time as real_time
        clock = Clock()
        busy = runner(feed=999)
        waited = res.wait_calm("history", WEEKDAY_OPEN, sleep=clock.sleep, runner=busy, clock=clock)
        self.assertGreaterEqual(waited, res.MAX_WAIT["history"])
        self.assertLess(waited, res.MAX_WAIT["history"] + 1)                  # always gives up: the task then runs
        for moment in (SATURDAY, NIGHT):
            clock2 = Clock()
            self.assertEqual(res.wait_calm("history", moment, sleep=clock2.sleep, runner=busy, clock=clock2), 0.0)
        self.assertEqual(res.wait_calm("history", WEEKDAY_OPEN, sleep=clock.sleep, runner=runner(), clock=clock), 0.0)
        self.assertGreaterEqual(res._STATS["yield_by_label"]["history"], 1)

    def test_shed_calls_the_function_unchanged_and_propagates_errors(self):
        seen = []
        wrapped = res.shed("research", lambda a, b=2: seen.append((a, b)) or "ok")
        res._runner = lambda: None
        self.assertEqual(wrapped(1, b=3), "ok")
        self.assertEqual(seen, [(1, 3)])
        self.assertEqual(wrapped._v0450_shed, "research")

        def boom():
            raise ValueError("x")
        with self.assertRaises(ValueError):
            res.shed("fix14", boom)()


class Reconnect(unittest.TestCase):
    def setUp(self):
        reset()

    def test_healthy_socket_that_dropped_gets_a_short_pause(self):
        res._FAST["ok_at"] = 100.0
        self.assertTrue(res.arm_fast_reconnect("ConnectionClosedError: sent 1011 (internal error) keepalive ping timeout", 130.0))
        self.assertEqual(res.consume_fast_delay(60, 131.0), res.FAST_DELAY)
        self.assertEqual(res.consume_fast_delay(60, 131.5), 60)               # one shortened pause per drop
        self.assertEqual(res._STATS["fast_reconnects"], 1)

    def test_not_for_other_errors_short_uptime_or_a_storm(self):
        res._FAST["ok_at"] = 100.0
        for text in ("ConnectionClosedOK: received 1000 (OK) Bye", "WebSocket 실시간 등록 거부: 8005",
                     "ConnectionError: HTTPSConnectionPool(host='api.kiwoom.com')", "gaierror: [Errno 7] No address",
                     "RuntimeError: V071_STOCK_WS_QUEUE_BACKPRESSURE"):
            self.assertFalse(res.arm_fast_reconnect(text, 200.0), text)
        self.assertEqual(res.consume_fast_delay(60, 200.5), 60)
        self.assertFalse(res.arm_fast_reconnect("ConnectionClosedError: no close frame received or sent", 105.0))   # 5 s uptime
        for i in range(res.FAST_MAX_PER_WINDOW):
            res._FAST["ok_at"] = 100.0
            self.assertTrue(res.arm_fast_reconnect("ConnectionClosedError: no close frame received or sent", 200.0 + i))
        self.assertFalse(res.arm_fast_reconnect("ConnectionClosedError: no close frame received or sent", 210.0))   # storm guard
        self.assertEqual(res._STATS["reconnect_seen"], 12)

    def test_armed_pause_expires_and_short_pauses_are_untouched(self):
        res._FAST["ok_at"] = 0.0
        self.assertTrue(res.arm_fast_reconnect("ConnectionClosedError: no close frame received or sent", 100.0))
        self.assertEqual(res.consume_fast_delay(60, 100.0 + res.FAST_ARM_SECONDS + 1), 60)   # too late: not ours
        res._FAST["armed_until"] = 1e12
        self.assertEqual(res.consume_fast_delay(2, 1.0), 2)                                  # already short
        self.assertEqual(res.consume_fast_delay(0.2, 1.0), 0.2)

    def test_asyncio_proxy_forwards_everything_and_shortens_only_the_armed_sleep(self):
        proxy = res._AsyncioProxy(asyncio)
        self.assertIs(proxy.CancelledError, asyncio.CancelledError)
        self.assertIs(proxy.create_task, asyncio.create_task)
        res._FAST["armed_until"] = 1e12
        started = dt.datetime.now()
        asyncio.run(proxy.sleep(60 if False else 4))                                          # armed: 4 s -> 3 s
        took = (dt.datetime.now() - started).total_seconds()
        self.assertLess(took, 3.8)
        self.assertEqual(res._STATS["fast_reconnects"], 1)
        asyncio.run(proxy.sleep(0))                                                           # normal sleeps pass through

    def test_health_wrapper_tracks_connection_and_arms(self):
        calls = []
        native = lambda self, state, details="": calls.append((state, details)) or "r"
        health = res._wrap_health(native)
        runner_obj = object()
        health(runner_obj, "실시간 연결됨", "x")
        self.assertIsNotNone(res._FAST["ok_at"])
        res._FAST["ok_at"] -= 40
        self.assertEqual(health(runner_obj, "연결 끊김 - 재연결 대기", "ConnectionClosedError: no close frame received or sent"), "r")
        self.assertGreater(res._FAST["armed_until"], 0)
        self.assertEqual([c[0] for c in calls], ["실시간 연결됨", "연결 끊김 - 재연결 대기"])


class Measurement(unittest.TestCase):
    def setUp(self):
        reset()

    def test_sample_holds_deltas_and_is_written_as_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "stock").mkdir()
            first = res.sample(runner(), WEEKDAY_OPEN)
            self.assertEqual(first["delta"], {"processed": 0, "discarded": 0, "overflows": 0, "slow_ticks": 0})
            r = runner()
            r._feed_processed, r._feed_discarded = 25, 9
            second = res.sample(r, WEEKDAY_OPEN)
            self.assertEqual(second["delta"]["processed"], 15)
            self.assertEqual(second["delta"]["discarded"], 6)
            self.assertEqual((second["feed_q"], second["tick_q"], second["high_water"]), (0, 0, 7))
            self.assertTrue(res._write_line(second, tmp))
            line = (Path(tmp) / "stock" / res.LOG_FILE).read_text("utf8").splitlines()
            self.assertEqual(len(line), 1)
            self.assertEqual(json.loads(line[0])["delta"]["discarded"], 6)

    def test_open_buys_show_why_an_order_is_not_complete(self):
        now = WEEKDAY_OPEN
        order = lambda **k: types.SimpleNamespace(**dict(dict(side="BUY", state="OPEN", code="003380", requested=39, filled=3,
                                                              time=(now - dt.timedelta(minutes=90)).isoformat()), **k))
        r = runner()
        r.gmo = types.SimpleNamespace(engine=types.SimpleNamespace(orders={
            "1": order(), "2": order(code="000680", filled=39), "3": order(code="005930", state="CANCELED"),
            "4": order(code="035420", side="SELL")}),
            last_book={"003380": (9930, 9900, 5, 5, now - dt.timedelta(seconds=42))})
        rows = res.open_buys(r, now)
        self.assertEqual(rows, [{"code": "003380", "requested": 39, "filled": 3, "age_min": 90.0, "book_age_s": 42.0}])
        self.assertEqual(res.open_buys(runner(), now), [])                                       # no GMO engine: nothing, no error
        self.assertEqual(res.sample(r, now)["open_buys"][0]["code"], "003380")

    def test_install_wraps_the_entry_points_once_and_is_wired_in_the_runtime(self):
        text = (PY / "android_runtime_v078.py").read_text("utf8")
        self.assertIn("from stock_feed_resilience_v0450 import install", text)
        self.assertGreater(text.index("stock_feed_resilience_v0450"), text.index("market_ctx_memo_v0420"))
        self.assertGreater(text.index("stock_feed_resilience_v0450"), text.index("stock_feed_diag_v0400"))
        runner_type = type("R", (), {"_health": lambda self, s, d="": None, "_process_completed_records": lambda self, b: b})
        fake = types.SimpleNamespace(LivePaperRunner=runner_type, asyncio=asyncio)
        res._STATE["installed"] = False
        try:
            res.install(fake, threads=False)
            self.assertTrue(getattr(runner_type._health, "_v0450"))
            self.assertEqual(runner_type._process_completed_records._v0450_shed, "history")
            self.assertIsInstance(fake.asyncio, res._AsyncioProxy)
            first = runner_type._process_completed_records
            res.install(fake, threads=False)
            self.assertIs(runner_type._process_completed_records, first)                      # idempotent
        finally:
            res._STATE["installed"] = False


if __name__ == "__main__":
    unittest.main()
