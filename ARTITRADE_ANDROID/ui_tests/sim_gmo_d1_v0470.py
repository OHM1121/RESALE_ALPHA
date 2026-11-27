"""Synthetic end-to-end harness for the supply-demand (GMO) D+1 path: candidate -> plan -> order -> fills -> position.

Runs the REAL vendor `GMOService` / `Engine` with the app's overlays installed (preplan, 0D depth fallback) on a simulated clock
and a scripted feed (0D books in the app's wire format, 0B prints as tuples). No ledger file, no broker, no network.
"""
import contextlib
import datetime as dt
import threading
import types
from unittest import mock

import d_gmo_runtime
import d_minute_repair_fast
import gmo_d1_preplan_v097 as pre
import krx_depth_fallback_v0360 as depth_fix
from projectgmo.selector import DSignal

KST = dt.timezone(dt.timedelta(hours=9))
CODE = "241560"
DAY = dt.date(2026, 11, 26)
MARKET_MAP = {CODE: ("KOSPI", "두산밥캣")}


_MONO = [1000.0]                 # the overlay keeps module-level throttle state: the monotonic clock must never go backwards


class Clock:
    def __init__(self, start):
        self.now = start
        self.mono = _MONO[0]

    def set(self, moment):
        self.mono += max(0.0, (moment - self.now).total_seconds())
        self.now = moment


def at(h, m, s=0, ms=0):
    return dt.datetime(DAY.year, DAY.month, DAY.day, h, m, s, ms * 1000, tzinfo=KST)


def fids(ask, bid, ask_qty, bid_qty=5, krx_specific_zero=False):
    values = {"21": "", "41": "+%d" % ask, "51": "-%d" % bid, "61": str(ask_qty), "71": str(bid_qty)}
    if krx_specific_zero:                       # the 11-23 defect: KRX-specific FIDs present but zero, combined FIDs hold the size
        values.update({"6044": "0", "6054": "0"})
    else:
        values.update({"6044": str(ask_qty), "6054": str(bid_qty)})
    return values


class Sim:
    def __init__(self, tmp, *, book_trigger=True, close=48450, open_=48500, ladder=False):
        self.clock = Clock(at(8, 50))
        self.stack = contextlib.ExitStack()
        fake = self.clock

        class FakeDT(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return fake.now if tz is None else fake.now.astimezone(tz)

        self.stack.enter_context(mock.patch.object(d_gmo_runtime, "datetime", FakeDT))
        self.stack.enter_context(mock.patch.object(d_minute_repair_fast, "now_kst", lambda: fake.now))
        self.stack.enter_context(mock.patch.object(pre, "time", types.SimpleNamespace(monotonic=lambda: fake.mono)))
        self.stack.enter_context(mock.patch.object(pre.Preplanner, "background", lambda self: None))
        self.stack.enter_context(mock.patch.object(d_gmo_runtime.GMOService, "_loop", lambda self: None))
        self.stack.enter_context(mock.patch.object(d_gmo_runtime.GMOService, "_maintenance_loop", lambda self: None))
        # 0.5.1: the mechanics tests of 0.4.7-0.5.0 run the single-order entry; the split-buy entry (default in the app) is ladder=True
        self.stack.enter_context(mock.patch.object(pre, "LADDER_ENABLED", bool(ladder)))
        if not book_trigger:
            self.stack.enter_context(mock.patch.object(pre.Preplanner, "on_book", lambda self, code, at=None: False))
        pre.install(None)                       # the order the app uses: preplan first, depth fallback last
        depth_fix.install(None)
        self.svc = d_gmo_runtime.GMOService(tmp)
        self.svc.engine.signals[CODE] = DSignal(
            code=CODE, name="두산밥캣", market="KOSPI", event_date="2026-11-25", entry_date="2026-11-26",
            event_close=close, previous_close=49200, close_5_ago=52300, close_20_ago=49400, session_type="SHORT",
            foreign_net=123259, institution_net=16494, personal_net=-39808, previous_20_personal_net=[0] * 20,
            source="SIM")
        self.svc.engine.signal_scores[CODE] = 75
        self.svc.start(None, None)
        self.planner = self.svc._v097_planner
        self.planner.clock = lambda: fake.now
        self.svc.opens[(CODE, DAY.isoformat())] = open_
        self.planner.prepare("KRX", at(8, 50))                                   # 08:50 plan, as on the phone
        self.prints = 0

    def close(self):
        self.svc._stop.set()
        _MONO[0] = self.clock.mono + 10.0
        self.stack.close()

    @property
    def budget(self):
        return float(self.plan["max_budget_won"])

    # ---- scripted feed
    def book(self, when, ask, bid, ask_qty, **kw):
        self.clock.set(when)
        message = {"trnm": "REAL", "data": [{"type": "0D", "item": CODE, "values": fids(ask, bid, ask_qty, **kw)}]}
        self.svc.on_book_message(message, MARKET_MAP)

    def print_(self, when, price, volume, ask=0, bid=0):
        self.clock.set(when)
        self.prints += 1
        tick = (CODE, "KOSPI", "두산밥캣", when.strftime("%Y%m%d%H%M%S"), price, volume, when.isoformat(), "KRX",
                when.isoformat(), ask, bid)
        self.svc.on_live_tick(tick)

    # ---- observations
    @property
    def plan(self):
        return next(iter(self.planner.plans.values()))

    @property
    def orders(self):
        return [o for o in self.svc.engine.orders.values() if o.code == CODE and o.side == "BUY"]

    @property
    def filled(self):
        return sum(f["qty"] for f in self.svc.engine.buy_fills if f["code"] == CODE)

    @property
    def spent(self):
        return sum(f["paid"] for f in self.svc.engine.buy_fills if f["code"] == CODE)
