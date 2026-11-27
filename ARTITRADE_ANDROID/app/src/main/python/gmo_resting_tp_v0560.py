"""0.5.6 (owner 2026-11-27, issue #171 option C; rule R15): the first take-profit of a held supply-demand (GMO) position is a RESTING sell order at the target price.

Why: in real trading the first take-profit is a limit sell placed right after the buy. It lives at the exchange, so a phone that is slow or disconnected does not
change whether it fills. The paper model waited for the app to SEE a print at the target, then built a marketable sell (best bid - 3 ticks); when the print never reached the
engine in time (하림지주 2026-11-26) nothing was sold. That difference to real trading was the defect (R15: paper follows the real process).

What a resting sell does here, exactly:
* price = the target rounded UP to a valid tick (`up_tick(entry * (1 + first_profit_target))`), so the gain is never below the target.
* it fills when a REAL print (any KRX print the feed accepted: parsed, positive price and volume, exchange time inside the last 115 s) trades at or above that price AFTER the
  order was placed (the time of the latest BUY fill of the code; every later buy fill re-prices it, as an amended real order would). The fill price is the order's own limit
  price (a resting sell executes at its limit; the better price of a gap is not assumed), the quantity is the whole first-sale quantity (the price-reached rule already used
  for the resting split-buy rungs), fees and tax are the engine's. No book, no 3 s freshness gate, no print older than the placement time: a print is evidence that the exchange
  price reached the order, not a quote to trade against.
* the engine's own rules around it are unchanged: the remaining split-buy orders are cancelled at the touch, the stage moves exactly as after any first-target fill
  (PROTECTED / FIRST_PARTIAL), the later exits (+2 % floor, peak -3 %, second target, trailing, D+16) are untouched and still decided on fresh prints.
Two delivery paths, one rule: (1) the vendor `Engine._trigger_first_tp` (reached when the tick consumer sees the print) is replaced by the resting model; (2) the feed ingress
(`gmo_held_exit_v0550.record_ingress`, before the busy queue) notes the first qualifying print and the watchdog applies it under the GMO lock, so the consumer's delay or a
dropped queue entry cannot lose a touch. The vendor engine file is not modified.
"""
from __future__ import annotations

import datetime as dt
import math

VERSION = "GMO_RESTING_TP_V0560"
KST = dt.timezone(dt.timedelta(hours=9))
_STATE = {"installed": False, "native": None}


def resting_price(position):
    from projectgmo.prices import up_tick
    return int(up_tick(float(position.entry_price) * (1 + float(position.first_profit_target))))


def placed_at(engine, code):
    """When the resting order was (re)placed: the latest BUY fill of the code, or None when unknown."""
    latest = None
    for fill in engine.buy_fills:
        if fill.get("code") != code:
            continue
        try:
            moment = dt.datetime.fromisoformat(str(fill.get("time")))
        except (ValueError, TypeError):
            continue
        if latest is None or moment > latest:
            latest = moment
    return latest


def first_sale_quantity(engine, position):
    target = min(position.qty, max(1, math.floor(position.total_qty * engine.cfg.first_sell_fraction)))
    return max(0, target - position.first_filled_qty)


def trigger_resting(engine, position, quote):
    """Replacement of `Engine._trigger_first_tp`: place the resting sell at the target and fill it, the print being at or above it."""
    if position.blocked:
        engine._log(position.code, "EXIT_CONDITION_WHILE_BLOCKED", "FIRST_TP; blocked=" + str(position.blocked), quote.at)
        return
    engine._cancel_pending_buys(position.code, "profit milestone stops buy remainder", quote.at)
    position.first_profit_triggered = True
    position.stage = "TP_PENDING"
    position.protected_peak = max(position.protected_peak, quote.last)
    remaining = first_sale_quantity(engine, position)
    if not remaining:
        return
    price = resting_price(position)
    purpose = "TP%d_FIRST_20PCT" % int(round(position.first_profit_target * 100))
    order = engine._order(position.code, "SELL", purpose, price, price, min(position.qty, remaining), quote.at)
    position.pending_sell_id = order.id
    if quote.last + 1e-9 >= price:
        try:
            engine.fill(order.id, order.requested, price, quote.at)
            engine._log(position.code, "RESTING_TP_FILL_V0560", "%s %d@%d by a print at %s (resting sell at the target; no book, no freshness gate)" % (order.id, order.requested, price, quote.last), quote.at)
        except ValueError as exc:
            engine._log(position.code, "RESTING_TP_FILL_REFUSED_V0560", "%s %s" % (order.id, str(exc)[:160]), quote.at)


def install():
    if _STATE["installed"]:
        return
    from projectgmo.engine import Engine
    _STATE["native"] = Engine._trigger_first_tp
    Engine._trigger_first_tp = trigger_resting
    _STATE["installed"] = True
