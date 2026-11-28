"""0.6.8 (owner 2026-11-28 "모든 전략에 일괄 적용 ... 코인 주식 둘 다 적용 ... 매도도 목표가격에 물량이 있으면 3초 룰 없이 그 가격 오면 매도"): ONE freshness rule for every strategy, stock and coin.

What the "3 second rule" was: every strategy refused to buy or sell unless the last trade print / order book it looked at had arrived within the last 3 seconds. The broker's stream sends a
message only when something CHANGES (a trade, a book change), so a quiet stock or coin looks "stale" after 3 seconds although its price and its book are exactly as they were; and a print that waited
a few seconds in the app's queue was thrown away although the price HAD reached the target (supply-demand "하림지주" 2026-11-26: the first take-profit print was dropped, nothing was sold).

The one rule (every vendor / overlay site that compared an age with 3 s now asks this module):
* STREAM ALIVE: the app's real-time connection of that market (`stock` = Kiwoom, `coin` = Upbit) delivered ANY message within the last ALIVE seconds (`touch()` is called by the receivers).
  While the line is alive, "no new message about a stock / coin" means "unchanged".
* BOOK (order book): fresh when its snapshot is at most 3 s old (as before) OR the line is alive and the snapshot is at most BOOK_HOLD_SECONDS old (a book nobody replaced is still the book).
  Older than that, or with a dead line, nothing is traded on it (the stock-side direct book inquiry `rest_book_confirm_v0580` then renews it).
* PRINT (a trade message): usable when at most 3 s old (as before) OR the line is alive and it is at most BACKLOG_SECONDS old (the same 115 s the vendor already uses for "exchange time inside
  the last 115 s"). A print that reached the target price is the evidence that a resting order at that price would have been filled (R15, the 0.5.6 resting take-profit model).
* NOT changed: the print must still be AFTER the order / position existed, the book quantity limits (whole displayed size, no phantom depth), fees, tax, budgets, one position per stock,
  and a dead line (no message at all for ALIVE seconds) falls back to exactly the old 3 s behaviour.
No order, fill, price or quantity is invented here: this module only answers "is this evidence still usable?".
"""
from __future__ import annotations

import threading
import time

VERSION = "FRESH_RULE_V0680"
OLD_LIMIT_SECONDS = 3.0
ALIVE_SECONDS = {"stock": 20.0, "coin": 15.0}
BOOK_HOLD_SECONDS = 120.0
BACKLOG_SECONDS = 115.0
_LAST = {"stock": 0.0, "coin": 0.0}
_LOCK = threading.Lock()


def touch(market="stock", at=None):
    """The receiver of a market's real-time stream got a message (any message)."""
    value = time.monotonic() if at is None else float(at)
    with _LOCK:
        if value > _LAST.get(market, 0.0):
            _LAST[market] = value


def alive(market="stock", now=None):
    now = time.monotonic() if now is None else float(now)
    with _LOCK:
        last = _LAST.get(market, 0.0)
    return last > 0.0 and 0.0 <= now - last <= ALIVE_SECONDS.get(market, 20.0)


def book_ok(age, market="stock", limit=OLD_LIMIT_SECONDS):
    """`age` = seconds since the book snapshot was received / stamped. True when the book may be traded on."""
    try:
        age = float(age)
    except (TypeError, ValueError):
        return False
    if -0.5 <= age <= limit:
        return True
    return alive(market) and -0.5 <= age <= BOOK_HOLD_SECONDS


def print_ok(age, market="stock", limit=OLD_LIMIT_SECONDS):
    """`age` = seconds since the trade print happened / was received. True when it may trigger a decision."""
    try:
        age = float(age)
    except (TypeError, ValueError):
        return False
    if -0.5 <= age <= limit:
        return True
    return alive(market) and -0.5 <= age <= BACKLOG_SECONDS


def status():
    now = time.monotonic()
    return {m: {"alive": alive(m, now), "seconds_since_last_message": (round(now - v, 2) if v else None)} for m, v in list(_LAST.items())}
