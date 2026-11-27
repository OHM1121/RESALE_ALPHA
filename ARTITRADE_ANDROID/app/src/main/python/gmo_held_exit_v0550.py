"""0.5.5 (owner 2026-11-27, 하림지주): the exit of a HELD supply-demand (GMO) position must not depend on the busy tick consumer, and a missed touch must leave evidence.

Case (device data 2026-11-23..26): 하림지주 (003380), 3 sh @ 9,930, first take-profit 5 % = 10,426.5, stage NORMAL, no pending sell, `first_profit_triggered=False`
although the live feed printed 10,520 at 09:15:07, 10,510..10,530 at 09:57-10:05 and 10,430 at 10:12:00 on 2026-11-26 (bars `LIVE_WS_0B`). The vendor `GMOService` /
`Engine` are correct: with the real classes a fresh print >= target creates TP5_FIRST_20PCT at once (tests/test_v0550_gmo_held_exit.py reproduces it).
So those prints never reached `Engine.on_quote` in time. In the vendor feed path a held code's print goes feed worker -> `_gmo_tick_queue` -> ONE consumer thread that,
per print, runs GMO first and then the heavy DIVERSE/MA engine (`process_realtime_tick`), whose DB-gate waits were measured at 1.0-14.2 s on 2026-11-26 (issues
"가상주문 평가 잠금 대기 1초 초과"); every print queued behind it is older than the 3 s executable-freshness gate when it is dequeued and is dropped. The drop is silent: the
vendor emits `HELD_RISK_PRIORITY_STALE_TICK` but its diagnostics loop has no handler for it. (UNVERIFIED on the device: the exact stage that dropped the 11-26 prints cannot be
read back from the stored data, which is why this change also adds the evidence.)

Change (no order, fill, price or rule is invented; the 3 s executable-freshness gate is NOT weakened):
* Watchdog thread: every WATCHDOG_PERIOD seconds, for each held GMO code, the newest in-memory KRX print is offered to `GMOService.on_live_tick` when it is still fresh (the
  same `exchange_stamp_is_fresh` test, 3 s) and newer than the last print already offered. The held-position exit therefore no longer waits behind the heavy consumer.
  The engine's own `q.at <= p.last_at` guard makes a print that the consumer already handled harmless.
* Evidence: every held print is counted at the feed ingress and at `on_live_tick`; a print at or above the first-target price that did not turn the position into
  TP_PENDING within TOUCH_GRACE seconds writes the issue `GMO_HELD_TP_TOUCH_UNPROCESSED` (once per code and day) with the counters, and `stock/gmo_held_lane_v0550.json`
  keeps the counters for the day.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

VERSION = "GMO_HELD_EXIT_V0550"
WATCHDOG_PERIOD = 0.4
TOUCH_GRACE = 5.0
STATE_FILE = "gmo_held_lane_v0550.json"
KST = dt.timezone(dt.timedelta(hours=9))

_STATE = {"installed": False, "lock": threading.Lock(), "day": "", "codes": {}, "offered": {}, "reported": set(), "threads": set(), "held": frozenset(), "touches": {}}


def _now():
    return dt.datetime.now(KST)


def _new_code_stats():
    return {"ingress": 0, "ingress_max": 0, "ingress_max_at": "", "ingress_lag_max": 0.0, "engine_calls": 0, "watchdog_offered": 0,
            "watchdog_stale": 0, "touch_at": "", "touch_price": 0, "last_ingress_at": ""}


def _stats(code):
    day = _now().date().isoformat()
    with _STATE["lock"]:
        if _STATE["day"] != day:
            _STATE.update({"day": day, "codes": {}, "offered": {}, "reported": set()})
        return _STATE["codes"].setdefault(str(code), _new_code_stats())


def _lag(tick):
    try:
        return max(0.0, (_now() - dt.datetime.fromisoformat(str(tick[8]))).total_seconds())
    except (ValueError, TypeError, IndexError):
        return 0.0


def held_target(gmo, code):
    """First take-profit price of a held position, or None."""
    pos = gmo.engine.positions.get(code)           # lock-free read: this runs on the feed worker, which must never wait for the GMO lock
    if pos is None or getattr(pos, "qty", 0) <= 0:
        return None
    return float(pos.entry_price) * (1 + float(pos.first_profit_target)), pos.stage, bool(pos.first_profit_triggered)


def record_ingress(runner, tick):
    """Count a KRX print of a held GMO code at the feed ingress (before the queue)."""
    try:
        if len(tick) < 9 or tick[7] != "KRX":
            return
        code = str(tick[0])
        if code not in _STATE["held"]:
            return
        s = _stats(code)
        price = int(tick[4])
        s["ingress"] += 1
        s["last_ingress_at"] = str(tick[6])
        s["ingress_lag_max"] = max(s["ingress_lag_max"], round(_lag(tick), 2))
        if price > s["ingress_max"]:
            s["ingress_max"], s["ingress_max_at"] = price, str(tick[6])
        target = held_target(runner.gmo, code)
        if target and price >= target[0] and not target[2] and not s["touch_at"]:
            s["touch_at"], s["touch_price"] = str(tick[6]), price
        if target and price >= target[0] and not target[2] and target[1] == "NORMAL" and code not in _STATE["touches"]:
            # 0.5.6: the first print at/after the target is kept for the resting take-profit (applied by the watchdog under the GMO lock)
            _STATE["touches"][code] = (price, str(tick[8]), str(tick[6]))
    except Exception:
        pass


def check_touches(runner, now_mono=None):
    """A print at/above the first target that did not start the exit within TOUCH_GRACE seconds is reported once per day."""
    for code, s in list(_STATE["codes"].items()):
        if not s["touch_at"] or code in _STATE["reported"]:
            continue
        try:
            touched = dt.datetime.fromisoformat(s["touch_at"])
        except ValueError:
            continue
        if (_now() - touched).total_seconds() < TOUCH_GRACE:
            continue
        target = held_target(runner.gmo, code)
        if target is None or target[2]:
            _STATE["reported"].add(code)
            continue
        _STATE["reported"].add(code)
        try:
            runner.store.issue("GMO_HELD_TP_TOUCH_UNPROCESSED", "수급매매 보유종목 1차 목표가 체결이 관측됐으나 매도 판단 미처리 - 큐/신선도 증거 기록", key="gmo-held-touch:%s:%s" % (code, _STATE["day"]),
                               details={"code": code, "target": round(target[0], 1), "stage": target[1], "counters": dict(s), "version": VERSION})
        except Exception:
            pass


def apply_touches(runner):
    """0.5.6: apply the first print at or above the target to the resting take-profit (gmo_resting_tp_v0560). Returns the number of fills started."""
    import gmo_resting_tp_v0560 as resting
    from projectgmo.engine import Quote
    gmo = runner.gmo
    eng = gmo.engine
    done = 0
    for code, (price, exchange_iso, _seen) in list(_STATE["touches"].items()):
        try:
            with gmo._lock:
                p = eng.positions.get(code)
                if (p is None or p.qty <= 0 or p.stage != "NORMAL" or p.first_profit_triggered or p.blocked
                        or price < float(p.entry_price) * (1 + float(p.first_profit_target))):
                    _STATE["touches"].pop(code, None)
                    continue
                placed = resting.placed_at(eng, code)
                moment = dt.datetime.fromisoformat(exchange_iso)
                if placed is not None and moment <= placed:
                    _STATE["touches"].pop(code, None)          # the print is older than the order: it cannot have filled it
                    continue
                quote = Quote(code, exchange_iso, max(1, int(p.last_day)), float(price), 0.0, 0.0, 0.0, 0.0, "KRX", 0.0)
                eng._trigger_first_tp(p, quote)
                gmo._persist(True)
            _STATE["touches"].pop(code, None)
            _stats(code)["resting_applied"] = _stats(code).get("resting_applied", 0) + 1
            done += 1
        except Exception as exc:
            try:
                runner.store.write_runtime_error(exc, "gmo_resting_tp_apply:" + str(code))
            except Exception:
                pass
    return done


def offer_latest(runner):
    """Offer the newest fresh in-memory print of each held code to the GMO engine (idempotent)."""
    from d_minute_repair_fast import exchange_stamp_is_fresh
    done = 0
    try:
        codes = sorted(runner.gmo.holding_codes())
        _STATE["held"] = frozenset(codes)
    except Exception:
        return 0
    for code in codes:
        try:
            tick = runner.latest_krx_trade(code)
            if not tick or len(tick) < 9 or tick[7] != "KRX":
                continue
            s = _stats(code)
            if _STATE["offered"].get(code) == tick[6]:
                continue
            if not exchange_stamp_is_fresh(tick[8], tick[6]):
                s["watchdog_stale"] += 1
                continue
            _STATE["offered"][code] = tick[6]
            s["watchdog_offered"] += 1
            runner.gmo.on_live_tick(tick)
            done += 1
        except Exception as exc:
            try:
                runner.store.write_runtime_error(exc, "gmo_held_exit_watchdog:" + str(code))
            except Exception:
                pass
    return done


def write_state(runner):
    try:
        path = Path(runner.gmo.root) / STATE_FILE
        payload = {"version": VERSION, "day": _STATE["day"], "at": _now().isoformat(timespec="seconds"), "codes": _STATE["codes"]}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass


def _loop(runner):
    last_write = 0.0
    while not runner.stop_event.is_set():
        try:
            apply_touches(runner)
            offer_latest(runner)
            check_touches(runner)
            if time.monotonic() - last_write > 30.0:
                last_write = time.monotonic()
                write_state(runner)
        except Exception:
            pass
        runner.stop_event.wait(WATCHDOG_PERIOD)


def install():
    if _STATE["installed"]:
        return
    import d_gmo_runtime
    import d_live
    cls = d_live.LivePaperRunner
    native_new_tick = cls._new_tick

    def _new_tick(self, tick):
        record_ingress(self, tick)
        return native_new_tick(self, tick)
    cls._new_tick = _new_tick

    native_workers = cls._start_market_workers

    def _start_market_workers(self):
        native_workers(self)
        if id(self) not in _STATE["threads"]:
            _STATE["threads"].add(id(self))
            threading.Thread(target=_loop, args=(self,), name="ARTITRADE_V0550_GMO_HELD_EXIT_WATCHDOG", daemon=True).start()
    cls._start_market_workers = _start_market_workers

    native_live = d_gmo_runtime.GMOService.on_live_tick

    def on_live_tick(self, tick):
        try:
            if len(tick) >= 8 and tick[7] == "KRX" and str(tick[0]) in _STATE["held"]:
                _stats(tick[0])["engine_calls"] += 1
        except Exception:
            pass
        return native_live(self, tick)
    d_gmo_runtime.GMOService.on_live_tick = on_live_tick
    _STATE["installed"] = True
