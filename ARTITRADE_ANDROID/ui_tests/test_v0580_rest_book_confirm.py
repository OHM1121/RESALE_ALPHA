"""0.5.8: stock MA / close-betting / MarketTrend confirm the current book with one ka10004 inquiry when the stream is quiet (owner 2026-11-27).

Synthetic data and fakes only: no broker, no network. The shared `Book`, the MA queue/policy and the close-betting `BookTracker` are the real classes.
"""
from __future__ import annotations

import datetime as dt
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import rest_book_confirm_v0580 as rbc
    from d_v19_portfolio import Book
    RUNTIME = True
except Exception:  # pragma: no cover - needs the original engine on sys.path (CI provides it)
    RUNTIME = False

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 26, 10, 29, 52, tzinfo=KST)          # 8 s before the 10:30 close of a 30m bar
CODE = "090710"


def reply(code=CODE, bid="-4650", ask="+4660", bq="120", aq="80", clock="102900", ret=0):
    return {"stk_cd": code, "return_code": ret, "bid_req_base_tm": clock, "buy_fpr_bid": bid, "sel_fpr_bid": ask, "buy_fpr_req": bq, "sel_fpr_req": aq}


class FakeApi:
    def __init__(self, answer=None, error=None):
        self.answer = reply() if answer is None else answer
        self.error = error
        self.calls = []

    def post(self, api_id, path, body, *a, **k):
        self.calls.append((api_id, path, body))
        if self.error:
            raise self.error
        return self.answer


def runner_with(api):
    r = types.SimpleNamespace(api=api, engine=types.SimpleNamespace(book=Book()), stop_event=threading.Event(),
                              _quote_lock=threading.Lock(), _latest_krx_trades={}, store=types.SimpleNamespace(write_runtime_error=lambda e, c: None))
    return r


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class Inquiry(unittest.TestCase):
    def setUp(self):
        rbc._STATE["last_call"] = 0.0
        rbc._STATE["cache"].clear()
        rbc._STATE["done"].clear()
        for key in rbc._STATE["stats"]:
            rbc._STATE["stats"][key] = 0

    def test_parse_valid_book_with_signed_fields(self):
        b = rbc.parse_book(reply(), CODE, NOW)
        self.assertEqual((b["ask"], b["bid"], b["ask_qty"], b["bid_qty"]), (4660, 4650, 80, 120))

    def test_parse_rejects_wrong_code_rejected_reply_future_time_crossed_book_and_empty_ask(self):
        self.assertIsNone(rbc.parse_book(reply(code="000001"), CODE, NOW))
        self.assertIsNone(rbc.parse_book(reply(ret=5), CODE, NOW))
        self.assertIsNone(rbc.parse_book(reply(clock="103500"), CODE, NOW))
        self.assertIsNone(rbc.parse_book(reply(bid="4700", ask="4660"), CODE, NOW))
        self.assertIsNone(rbc.parse_book(reply(aq="0"), CODE, NOW))
        self.assertIsNone(rbc.parse_book(reply(clock=""), CODE, NOW))
        self.assertIsNone(rbc.parse_book("x", CODE, NOW))

    def test_an_old_book_time_of_today_is_valid_because_a_quiet_book_does_not_change(self):
        self.assertIsNotNone(rbc.parse_book(reply(clock="094500"), CODE, NOW))

    def test_one_inquiry_per_token_and_pacing(self):
        api = FakeApi()
        r = runner_with(api)
        slept = []
        a = rbc.confirm(r, CODE, 4655, ("T", 1), NOW, sleep=slept.append)
        b = rbc.confirm(r, CODE, 4655, ("T", 1), NOW, sleep=slept.append)
        self.assertEqual(len(api.calls), 1)
        self.assertEqual(a, b)
        self.assertIsNotNone(a)
        rbc.confirm(r, "000002", 4655, ("T", 2), NOW, sleep=slept.append)       # another stock right after: paced, not simultaneous
        self.assertTrue(slept and slept[0] > 0)

    def test_price_outside_the_book_is_refused(self):
        r = runner_with(FakeApi())
        self.assertIsNone(rbc.confirm(r, CODE, 4700, ("T", 1), NOW, sleep=lambda s: None))
        self.assertEqual(rbc._STATE["stats"]["price_outside_book"], 1)

    def test_failure_is_none_and_counted(self):
        r = runner_with(FakeApi(error=ConnectionError("no route")))
        self.assertIsNone(rbc.confirm(r, CODE, 4655, ("T", 1), NOW, sleep=lambda s: None))
        self.assertEqual(rbc._STATE["stats"]["errors"], 1)

    def test_inject_book_is_a_new_snapshot_in_the_shared_book(self):
        r = runner_with(FakeApi())
        snap = rbc.confirm(r, CODE, 4655, ("T", 1), NOW, sleep=lambda s: None)
        rbc.inject_book(r, CODE, snap)
        quote, why = r.engine.book.available(CODE, "KRX", snap["at"], 4700, 1)
        self.assertIsNotNone(quote, why)
        self.assertEqual(quote[0], 4660)


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class MaQuietStock(unittest.TestCase):
    def setUp(self):
        Inquiry.setUp(self)
        import ma_v067_stock_runtime as mastock
        self.mastock = mastock
        self.offered = []
        self.queue = types.SimpleNamespace(offer=lambda t: self.offered.append(t) or True)
        self.stack = [mock.patch.object(rbc, "ensure_ma_worker", lambda r: self.queue),
                      mock.patch.object(mastock, "_close_seconds", lambda now, frame: (8.0, NOW + dt.timedelta(seconds=8)) if frame == "30m" else (400.0, NOW + dt.timedelta(seconds=400))),
                      mock.patch("ma_v061_stock_runtime._slope_closes", lambda *a, **k: [1, 2, 3])]
        for p in self.stack:
            p.start()
        self.runner = runner_with(FakeApi())
        self.runner.ma = types.SimpleNamespace(watched_codes=lambda: {CODE})
        old = (NOW - dt.timedelta(seconds=40)).isoformat()
        self.runner._latest_krx_trades[CODE] = (CODE, "KOSDAQ", "휴림로봇", "202611261029", 4655, 3, old, "KRX", old)

    def tearDown(self):
        for p in self.stack:
            p.stop()

    def rule(self, kind):
        return mock.patch.object(self.mastock.policy, "upward_transition_on_forming", lambda prices, price, remain: {"kind": kind})

    def test_quiet_stock_whose_rule_fires_gets_one_inquiry_and_one_tick(self):
        with self.rule("REVERSAL_UP"):
            self.assertEqual(rbc.ma_pass(self.runner, NOW), 1)
            self.assertEqual(rbc.ma_pass(self.runner, NOW), 0)               # same bar: once
        self.assertEqual(len(self.runner.api.calls), 1)
        tick = self.offered[0]
        self.assertEqual((tick[0], tick[4], tick[7], tick[11]), (CODE, 4655, "KRX", "REST_CONFIRMED_LAST_TRADE"))
        self.assertEqual(tick[6], tick[8])
        self.assertEqual(self.runner.engine.book._items[(CODE, "KRX")][0], 4660)

    def test_no_inquiry_when_the_rule_does_not_fire(self):
        with self.rule("NO_MATCH"):
            self.assertEqual(rbc.ma_pass(self.runner, NOW), 0)
        self.assertEqual(self.runner.api.calls, [])

    def test_a_stock_with_a_fresh_stream_print_is_left_to_the_normal_path(self):
        fresh = (NOW - dt.timedelta(seconds=1)).isoformat()
        self.runner._latest_krx_trades[CODE] = (CODE, "KOSDAQ", "x", "202611261029", 4655, 3, fresh, "KRX", fresh)
        with self.rule("REVERSAL_UP"), mock.patch("d_minute_repair_fast.now_kst", lambda: NOW):
            self.assertEqual(rbc.ma_pass(self.runner, NOW), 0)
        self.assertEqual(self.runner.api.calls, [])

    def test_last_price_outside_the_book_offers_nothing(self):
        self.runner.api = FakeApi(reply(bid="4700", ask="4720"))
        with self.rule("REVERSAL_UP"):
            self.assertEqual(rbc.ma_pass(self.runner, NOW), 0)

    def test_no_known_price_today_means_no_inquiry(self):
        self.runner._latest_krx_trades.clear()
        with self.rule("REVERSAL_UP"):
            self.assertEqual(rbc.ma_pass(self.runner, NOW), 0)
        self.assertEqual(self.runner.api.calls, [])


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class TrendQuietStock(unittest.TestCase):
    def setUp(self):
        Inquiry.setUp(self)

    def coordinator(self, quote_age):
        runner = runner_with(FakeApi())
        co = types.SimpleNamespace(rt=types.SimpleNamespace(stock_runner=runner), lock=threading.RLock(), _quotes={"stock": {CODE: (4655.0, time.monotonic() - quote_age)}, "coin": {}},
                                   _nl1118_watches={("stock", CODE, "30m"): {"status": "WATCHING"}})
        return co, runner

    def test_armed_watch_on_a_quiet_stock_renews_the_quote_and_the_book_once(self):
        co, runner = self.coordinator(quote_age=40)
        with mock.patch("market_trend_runtime_v043._end_clock", lambda now, market, frame: NOW + dt.timedelta(seconds=8)):
            self.assertEqual(rbc.trend_pre_step(co, "stock", NOW), 1)
            self.assertEqual(rbc.trend_pre_step(co, "stock", NOW), 0)
        self.assertLess(time.monotonic() - co._quotes["stock"][CODE][1], 1.0)
        self.assertEqual(co._quotes["stock"][CODE][0], 4655.0)
        self.assertEqual(len(runner.api.calls), 1)
        self.assertIn((CODE, "KRX"), runner.engine.book._items)

    def test_no_armed_watch_no_inquiry(self):
        co, runner = self.coordinator(quote_age=40)
        co._nl1118_watches = {("stock", CODE, "30m"): {"status": "READY"}}
        self.assertEqual(rbc.trend_pre_step(co, "stock", NOW), 0)
        self.assertEqual(runner.api.calls, [])

    def test_coin_is_untouched(self):
        co, runner = self.coordinator(quote_age=40)
        self.assertEqual(rbc.trend_pre_step(co, "coin", NOW), 0)
        self.assertEqual(runner.api.calls, [])

    def test_a_fresh_quote_and_book_need_no_inquiry(self):
        co, runner = self.coordinator(quote_age=0.5)
        rbc.inject_book(runner, CODE, rbc.parse_book(reply(), CODE, NOW))                  # 0.6.5: the fixture's own moment, not the real clock (the test failed between 00:00 and 10:30 KST)
        self.assertEqual(rbc.trend_pre_step(co, "stock", NOW), 0)
        self.assertEqual(runner.api.calls, [])


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class CloseBetting(unittest.TestCase):
    def setUp(self):
        Inquiry.setUp(self)
        from at_close_runner import BookTracker
        self.now = dt.datetime(2026, 11, 26, 15, 12, 0, tzinfo=KST)
        self.cr = types.SimpleNamespace(focus={CODE}, books=BookTracker(), latest={CODE: {"price": 4655, "volume": 3, "ts": (self.now - dt.timedelta(seconds=90)).isoformat(timespec="seconds")}})
        self.stock = runner_with(FakeApi())

    def test_quiet_focus_stock_gets_its_book_and_trade_record_renewed(self):
        with mock.patch("at_close_runner.now_kst", lambda: self.now):
            self.assertEqual(rbc.close_pass(self.cr, self.stock, self.now), 1)
            snap = self.cr.books.snapshot(CODE)
        self.assertEqual((snap["ask"], snap["bid"], snap["ask_qty"]), (4660, 4650, 80))
        self.assertEqual(self.cr.latest[CODE]["price"], 4655)
        self.assertEqual(len(self.stock.api.calls), 1)

    def test_outside_the_close_window_nothing_happens(self):
        self.assertEqual(rbc.close_pass(self.cr, self.stock, self.now.replace(hour=11)), 0)
        self.assertEqual(self.stock.api.calls, [])

    def test_price_outside_the_book_keeps_the_old_behaviour(self):
        self.stock.api = FakeApi(reply(bid="4700", ask="4720"))
        self.assertEqual(rbc.close_pass(self.cr, self.stock, self.now), 0)
        self.assertEqual(self.cr.latest[CODE]["ts"][:10], "2026-11-26")
        self.assertLess((self.now - dt.datetime.fromisoformat(self.cr.latest[CODE]["ts"])).total_seconds(), 200)


@unittest.skipUnless(RUNTIME, "needs the original engine on sys.path")
class Wiring(unittest.TestCase):
    def test_install_wraps_the_two_vendor_entry_points_once(self):
        import d_live
        import market_trend_runtime_v043 as runtime
        keep = (d_live.LivePaperRunner._start_market_workers, runtime.Coordinator._preclose_candidates)
        try:
            with mock.patch.dict(rbc._STATE, {"installed": False, "threads": set()}):
                rbc.install()
                first = (d_live.LivePaperRunner._start_market_workers, runtime.Coordinator._preclose_candidates)
                self.assertNotEqual(first, keep)
                rbc.install()
                self.assertEqual(first, (d_live.LivePaperRunner._start_market_workers, runtime.Coordinator._preclose_candidates))
        finally:
            d_live.LivePaperRunner._start_market_workers, runtime.Coordinator._preclose_candidates = keep


if __name__ == "__main__":
    unittest.main()
