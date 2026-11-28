"""0.5.0 (#165): PAPER trading must follow the real-trading process - artificial haircuts that exist only for the simulation are removed.

Owner decision 2026-11-27 (standing rule R15): the paper program will become the real trading program. Paper-only conservatism that a real
order would not meet is removed; nothing is added that a real order could not do. Example from the owner: an order placed for 10 shares
that finds no quantity left at its price because the price jumped does NOT fill - that is correct and stays.

What a real marketable limit order does: it takes the displayed quantity at the best ask (or bid) at that ask (or bid) price, up to the
whole displayed size, and nothing beyond it. The paper code did less than that in two places:
1. Stock AT / MA / MarketTrend wallets (`d_v19_portfolio.Book`): only 50 % of the displayed best ask / bid size could be taken. The whole
   displayed size can be taken now. The reservation across wallets (one snapshot is shared by every timeframe wallet) is unchanged, and
   the resting-order proxy (50 % of an observed print at the limit price, a stand-in for the unknown queue position) is unchanged.
2. Supply-demand (GMO) engine: a paper buy was priced one tick plus 0.05 % above the displayed ask and a paper sell one tick plus 0.05 %
   below the bid. A real order is filled at the displayed price. The slippage is set to zero; fees and tax stay.

NOT changed on purpose (they protect real orders too): the 3 s freshness of a book / print used for a decision, the displayed-size limit
(no invented depth), the order limit, fees, tax and the wallet / budget limits.
"""
from __future__ import annotations

import dataclasses

VERSION = "REAL_PARITY_V0500"
DEPTH_PARTICIPATION = 1.0        # share of the DISPLAYED best ask / bid size one aggressive order can take


def install_book_depth():
    """Whole displayed best ask / bid size is takeable (was 50 %). Returns True when the patch was applied."""
    import d_v19_portfolio as m
    book = m.Book
    if getattr(book, "_v0500_depth", False):
        return False

    def available(self, code, venue, now, limit, desired_qty):
        with self._lock:
            rec = self._items.get((code, venue))
        if not rec:
            return None, 'NO_LIVE_ORDERBOOK'
        ask, bid, qty, when = rec
        age = (now - when).total_seconds()
        if not __import__('fresh_rule_v0680').book_ok(age, 'stock', m.BOOK_MAX_AGE_SEC):                 # 0.6.8: one freshness rule for every strategy
            return None, 'STALE_ORDERBOOK'
        if ask > int(limit):
            return None, 'BEST_ASK_ABOVE_LIMIT'
        if ask < bid or qty < 1:
            return None, 'INVALID_BEST_ASK'
        with self._lock:
            already = self._reserved.get((code, venue), 0)
        absorb = min(int(desired_qty), max(0, int(qty * DEPTH_PARTICIPATION) - already))
        if absorb < 1:
            return None, 'INSUFFICIENT_BEST_ASK_LIQUIDITY'
        return (ask, absorb, qty, age), 'VALID_OBSERVED_DEPTH_PAPER_PROXY'

    def available_sell(self, code, venue, now, bid, desired_qty):
        with self._lock:
            rec = self._bid_depth.get((code, venue))
            if rec is None:
                return 0
            px, qty, when = rec
            if px != int(bid) or not __import__('fresh_rule_v0680').book_ok((now - when).total_seconds(), 'stock', m.BOOK_MAX_AGE_SEC):
                return 0
            return max(0, min(int(desired_qty), int(qty * DEPTH_PARTICIPATION) - self._bid_reserved.get((code, venue), 0)))

    def reserve(self, code, venue, when, ask, qty):
        with self._lock:
            key = (code, venue)
            record = self._items.get(key)
            if not record or record[0] != int(ask) or not __import__('fresh_rule_v0680').book_ok((when - record[3]).total_seconds(), 'stock', m.BOOK_MAX_AGE_SEC):
                return False
            budget = int(record[2] * DEPTH_PARTICIPATION)
            already = self._reserved.get(key, 0)
            if qty < 1 or already + int(qty) > budget:
                return False
            self._reserved[key] = already + int(qty)
            return True

    book.available = available
    book.available_sell = available_sell
    book.reserve = reserve
    book._v0500_depth = True
    return True


def real_price_cfg(engine):
    """GMO paper engine: fills at the displayed ask / bid (no artificial slippage). `Settings` is frozen, so it is replaced."""
    cfg = engine.cfg
    if float(getattr(cfg, "paper_buy_slip", 0.0)) == 0.0 and float(getattr(cfg, "paper_sell_slip", 0.0)) == 0.0:
        return False
    engine.cfg = dataclasses.replace(cfg, paper_buy_slip=0.0, paper_sell_slip=0.0)
    return True
