"""0.5.8 (owner 2026-11-27): stock MA, close-betting (종가베팅) and MarketTrend entries confirm the CURRENT order book with one official REST inquiry (ka10004)
instead of requiring a stream message inside the last 3 seconds.

Why (device data 2026-11-23..26): 23,329 of 47,943 stock MA decisions were `MA_NO_FRESH_OBSERVED_TICK_IN_10_TO_2_SECOND_WINDOW`; close-betting rejected with `NO_FRESH_ORDERBOOK`
(book older than 3 s); MarketTrend skipped any stock whose last print was older than 3 s. Kiwoom's 0B/0D stream sends a message only when a trade happens / the book changes,
so a quiet stock looks "stale" although its last price and its book are exactly as they were. A real trader looks the quote up; this module does that.

Rule (nothing is invented): for a stock WITHOUT a fresh stream message that has a known last trade of today, ONE ka10004 inquiry per stock and per decision window is made
(never in a burst: >= MIN_GAP seconds between inquiries, result cached for CACHE_SECONDS). The answer is used only if it is a valid book (ask >= bid > 0, ask quantity > 0, the
code matches, return_code 0, the book time is today's) AND the last trade price lies inside [bid, ask] (otherwise the price moved and only a real print may decide). Then
* the shared engine book (`runner.engine.book`) gets that snapshot with the fetch time (a NEW actual snapshot, the same bookkeeping as a 0D message),
* the strategy sees the last trade price as current (MA: one queued tick stamped with the fetch time; MarketTrend: the stock quote stamp is renewed; close-betting: its
  BookTracker and latest-trade record are renewed).
The strategy rules, order limits, wallets, slots and fills are unchanged: the paper fill still needs the book's displayed ask quantity. A stock whose price is outside the book or
whose inquiry fails behaves exactly as before. Real (broker) orders remain disabled.
"""
from __future__ import annotations

import datetime as dt
import threading
import time

VERSION = "REST_BOOK_CONFIRM_V0580"
KST = dt.timezone(dt.timedelta(hours=9))
MIN_GAP = 0.5            # seconds between two inquiries (all stocks together); the official limit is not published in the pages that could be read
CACHE_SECONDS = 2.0
REST_API = "ka10004"
REST_PATH = "/api/dostk/mrkcond"
CLOSE_START_HHMM = "1505"
CLOSE_STOP_HHMM = "1519"
CLOSE_PER_CODE_SECONDS = 60.0

_STATE = {"installed": False, "lock": threading.Lock(), "last_call": 0.0, "cache": {}, "done": {}, "threads": set(),
          "stats": {"inquiries": 0, "ok": 0, "invalid": 0, "price_outside_book": 0, "errors": 0, "throttled_cached": 0,
                    "ma_ticks": 0, "trend_renewed": 0, "close_renewed": 0}}


def _num(value):
    try:
        return abs(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0.0


def parse_book(data, code, now):
    """Return {'ask','bid','ask_qty','bid_qty','at'} or None."""
    if not isinstance(data, dict):
        return None
    returned = str(data.get("stk_cd") or code).strip()
    if returned.startswith("A") and len(returned) == 7:
        returned = returned[1:]
    if returned != code:
        return None
    result = data.get("return_code")
    if result is not None and str(result) not in ("0", "00"):
        return None
    clock = "".join(c for c in str(data.get("bid_req_base_tm") or "") if c.isdigit())
    if len(clock) != 6:
        return None
    try:
        base = dt.datetime.combine(now.date(), dt.time(int(clock[:2]), int(clock[2:4]), int(clock[4:6])), tzinfo=KST)
    except ValueError:
        return None
    if base > now + dt.timedelta(seconds=1) or base.time() < dt.time(8, 0):
        return None                                        # a book time of the future or before the day's sessions is not today's book
    bid, ask = _num(data.get("buy_fpr_bid")), _num(data.get("sel_fpr_bid"))
    bid_qty, ask_qty = _num(data.get("buy_fpr_req")), _num(data.get("sel_fpr_req"))
    if bid <= 0 or ask < bid or ask_qty <= 0:
        return None
    return {"ask": int(ask), "bid": int(bid), "ask_qty": int(ask_qty), "bid_qty": int(bid_qty), "at": now, "base_time": base}


def confirm(runner, code, last_price, token, now=None, sleep=time.sleep):
    """One inquiry per (code, token). Returns the snapshot dict (valid and consistent with last_price) or None."""
    given = now is not None                                    # 0.6.5: an injected decision moment is also the moment the book time is compared with (the clock must not leak into a test or a replay)
    now = now or dt.datetime.now(KST)
    stats = _STATE["stats"]
    with _STATE["lock"]:
        if token in _STATE["done"]:
            stats["throttled_cached"] += 1
            return _STATE["done"][token]
        cached = _STATE["cache"].get(code)
        if cached and time.monotonic() - cached[0] <= CACHE_SECONDS:
            snap = cached[1]
        else:
            snap = False
    if snap is False:
        with _STATE["lock"]:
            moment = time.monotonic()
            start = max(moment, _STATE["last_call"] + MIN_GAP)
            _STATE["last_call"] = start
            wait = start - moment
        if wait > 0:
            sleep(wait)
        stats["inquiries"] += 1
        try:
            api = getattr(runner, "api", None)
            data = api.post(REST_API, REST_PATH, {"stk_cd": code}) if api is not None else None
            snap = parse_book(data, code, now if given else dt.datetime.now(KST))
        except Exception:
            stats["errors"] += 1
            snap = None
        if snap is None:
            stats["invalid"] += 1
        with _STATE["lock"]:
            _STATE["cache"][code] = (time.monotonic(), snap)
    result = snap
    if snap is not None and not (snap["bid"] <= float(last_price) <= snap["ask"]):
        stats["price_outside_book"] += 1
        result = None
    elif snap is not None:
        stats["ok"] += 1
    with _STATE["lock"]:
        _STATE["done"][token] = result
        if len(_STATE["done"]) > 6000:
            _STATE["done"].clear()
        if len(_STATE["cache"]) > 1500:
            _STATE["cache"].clear()
    return result


def inject_book(runner, code, snap):
    """Store the snapshot in the shared engine book like a new 0D message (own fetch time, fresh liquidity budget)."""
    book = runner.engine.book
    with book._lock:
        key = (code, "KRX")
        book._items[key] = (snap["ask"], snap["bid"], snap["ask_qty"], snap["at"])
        book._reserved[key] = 0
        book._bid_depth[key] = (snap["bid"], snap["bid_qty"], snap["at"])
        book._bid_reserved[key] = 0


def ensure_ma_worker(runner):
    """The MA entry queue and worker exist only after the first fresh tick near a close; a market that is quiet everywhere needs them too."""
    import ma_v067_stock_runtime as mastock
    queue = getattr(runner, "_ma_v067_up_queue", None)
    if queue is None:
        queue = mastock._LatestVerifiedMAQueue(maxsize=mastock.MAX_QUEUE)
        runner._ma_v067_up_queue = queue
    worker = getattr(runner, "_ma_v067_up_worker", None)
    if worker is None or not worker.is_alive():
        worker = threading.Thread(target=mastock._up_worker, args=(runner,), daemon=True, name="artitrade-ma-v067-stock-preclose")
        runner._ma_v067_up_worker = worker
        worker.start()
    return queue


# ---------------------------------------------------------------- MA (stock): quiet stocks inside the close-minus-10..2 s entry window
def ma_pass(runner, now=None):
    """One pass over the watched stocks. Returns the number of ticks offered to the MA entry worker."""
    import ma_v067_stock_runtime as mastock
    from ma_v061_stock_runtime import _slope_closes
    from d_minute_repair_fast import exchange_stamp_is_fresh
    now = now or dt.datetime.now(KST)
    offered = 0
    service = getattr(runner, "ma", None)
    if service is None:
        return 0
    frames = []
    for frame in ("5m", "30m", "60m", "day"):
        remain, finish = mastock._close_seconds(now, frame)
        if mastock.policy.in_up_entry_window(remain):
            frames.append((frame, remain, finish))
    if not frames:
        return 0
    try:
        codes = sorted(service.watched_codes())
    except Exception:
        return 0
    for code in codes:
        with runner._quote_lock:
            last = runner._latest_krx_trades.get(code)
        if not last or len(last) < 9 or last[7] != "KRX":
            continue
        try:
            seen = dt.datetime.fromisoformat(str(last[6]))
        except ValueError:
            continue
        if seen.date() != now.date() or exchange_stamp_is_fresh(last[8], last[6], now):
            continue                                         # no known price today, or the normal stream path already serves it
        for frame, remain, finish in frames:
            token = ("MA", code, frame, finish.isoformat())
            if token in _STATE["done"]:
                continue
            prices = _slope_closes(service, code, frame, now)
            if mastock.policy.upward_transition_on_forming(prices, last[4], remain).get("kind") != "REVERSAL_UP":
                continue                                     # the rule does not fire at the last price: no inquiry
            snap = confirm(runner, code, last[4], token, now)
            if snap is None:
                continue
            inject_book(runner, code, snap)
            queue = ensure_ma_worker(runner)
            stamp = snap["at"].isoformat()
            tick = (code, last[1], last[2], now.strftime("%Y%m%d%H%M") + "00", last[4], 0, stamp, "KRX", stamp, snap["ask"], snap["bid"], "REST_CONFIRMED_LAST_TRADE")
            if queue is not None and queue.offer(tick):
                offered += 1
                _STATE["stats"]["ma_ticks"] += 1
    return offered


# ---------------------------------------------------------------- MarketTrend (stock): an armed RSI watch whose stock is quiet
def trend_pre_step(co, market, now):
    if market != "stock":
        return 0
    runner = getattr(co.rt, "stock_runner", None)
    watches = getattr(co, "_nl1118_watches", None) or {}
    if runner is None or runner.api is None:
        return 0
    from market_trend_runtime_v043 import _end_clock
    renewed = 0
    armed = {}
    for key, watch in list(watches.items()):
        if key[0] == "stock" and isinstance(watch, dict) and watch.get("status") == "WATCHING":
            armed.setdefault(key[1], []).append(key[2])
    for code, frames in armed.items():
        with co.lock:
            raw = co._quotes["stock"].get(code)
        if not raw or len(raw) < 2:
            continue                                         # no known price: only a real print can start
        book = None
        with runner.engine.book._lock:
            book = runner.engine.book._items.get((code, "KRX"))
        quote_fresh = time.monotonic() - raw[1] <= 3.0
        book_fresh = bool(book) and (now - book[3]).total_seconds() <= 3.0
        if quote_fresh and book_fresh:
            continue
        finish = _end_clock(now, "stock", frames[0])
        token = ("TREND", code, finish.isoformat() if finish else "")
        snap = confirm(runner, code, raw[0], token, now)
        if snap is None:
            continue
        inject_book(runner, code, snap)
        with co.lock:
            co._quotes["stock"][code] = (raw[0], time.monotonic())
        renewed += 1
        _STATE["stats"]["trend_renewed"] += 1
    return renewed


# ---------------------------------------------------------------- close-betting (종가베팅)
def close_pass(close_runner, stock_runner, now=None):
    """Renew the book and last-trade record of the focus stocks that are quiet. Returns the number renewed."""
    now = now or dt.datetime.now(KST)
    if not (CLOSE_START_HHMM <= now.strftime("%H%M") < CLOSE_STOP_HHMM):
        return 0
    renewed = 0
    for code in sorted(getattr(close_runner, "focus", ()) or ()):
        live = close_runner.latest.get(code)
        if not live:
            continue
        try:
            trade_age = (now - dt.datetime.fromisoformat(live["ts"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            continue
        book = close_runner.books.snapshot(code)
        if trade_age <= 3 and book["age"] <= 2:
            continue
        token = ("CLOSE", code, int(time.time() // CLOSE_PER_CODE_SECONDS))
        snap = confirm(stock_runner, code, live["price"], token, now)
        if snap is None:
            continue
        close_runner.books.update(code, snap["ask"], snap["bid"], snap["ask_qty"], snap["bid_qty"], snap["at"])
        close_runner.latest[code] = {"price": live["price"], "volume": 0, "ts": snap["at"].isoformat(timespec="seconds")}
        renewed += 1
        _STATE["stats"]["close_renewed"] += 1
    return renewed


def _loop(runner):
    while not runner.stop_event.is_set():
        try:
            ma_pass(runner)
        except Exception as exc:
            try:
                runner.store.write_runtime_error(exc, "rest_book_confirm_ma")
            except Exception:
                pass
        try:
            import android_runtime_v078 as bridge
            rt = getattr(getattr(bridge, "_core", None), "_RUNTIME", None)
            close_runner = getattr(rt, "close_runner", None)
            if close_runner is not None:
                close_pass(close_runner, runner)
        except Exception:
            pass
        runner.stop_event.wait(0.5)


def install():
    if _STATE["installed"]:
        return
    import d_live
    import market_trend_runtime_v043 as runtime
    native_workers = d_live.LivePaperRunner._start_market_workers

    def _start_market_workers(self):
        native_workers(self)
        if id(self) not in _STATE["threads"]:
            _STATE["threads"].add(id(self))
            threading.Thread(target=_loop, args=(self,), name="ARTITRADE_V0580_REST_BOOK_CONFIRM", daemon=True).start()
    d_live.LivePaperRunner._start_market_workers = _start_market_workers

    native_preclose = runtime.Coordinator._preclose_candidates

    def _preclose_candidates(self, market, now):
        try:
            trend_pre_step(self, market, now)
        except Exception:
            pass
        return native_preclose(self, market, now)
    runtime.Coordinator._preclose_candidates = _preclose_candidates
    _STATE["installed"] = True
