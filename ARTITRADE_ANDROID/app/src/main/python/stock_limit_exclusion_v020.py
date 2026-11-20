"""0.0.20 (owner decision, issue #117): stock AT orders that can never fill are
not kept as "buy pending".

The engine buys at most ``symbol_limit_won(tf)`` (timeframe seed / max positions,
user configurable) per stock. A PENDING order whose one-share cost (limit price
plus buy fee) is above that budget is rejected on every touch (BELOW_ONE_SHARE /
NO_SHARES_AFTER_FEES) and used to stay on screen as "buy pending" until it
expired, also taking a slot in the Kiwoom history-repair queue.

After every order-creating pass this module moves such orders to the explicit
terminal status EXCLUDED_ABOVE_SYMBOL_LIMIT, marks the candidate signal REJECTED
so it is not queued again, and writes one audit event with the reason. It never
creates a quote, order, fill or position, never touches holdings or cash, and
does not change the budget itself. Raising the budget later affects new
references only.
"""
from __future__ import annotations

STATUS = "EXCLUDED_ABOVE_SYMBOL_LIMIT"
EVENT = "PENDING_EXCLUDED_ABOVE_SYMBOL_LIMIT"
_INSTALLED = False


def one_share_cost(limit_won, fee_rate, charge):
    price = int(limit_won)
    return price + int(charge(price, fee_rate))


def sweep(engine, c, now):
    """Exclude unaffordable PENDING orders inside the caller's transaction."""
    import d_v19_portfolio as v19
    rows = c.execute(
        "SELECT signal_id,tf,code,limit_won FROM v19_orders WHERE status='PENDING'").fetchall()
    excluded = 0
    for row in rows:
        try:
            budget = int(v19.symbol_limit_won(row["tf"]))
            cost = one_share_cost(row["limit_won"], v19.BUY_FEE, v19.charge)
        except (TypeError, ValueError, KeyError):
            continue
        if cost <= budget:
            continue
        changed = c.execute(
            "UPDATE v19_orders SET status=? WHERE signal_id=? AND status='PENDING'",
            (STATUS, row["signal_id"]))
        if changed.rowcount != 1:
            continue
        c.execute("UPDATE signals SET decision='REJECTED' WHERE id=? "
                  "AND decision IN ('NEW','SELECTED','PENDING')", (row["signal_id"],))
        engine._log(c, now, row["code"], EVENT, {
            "tf": row["tf"], "limit_won": int(row["limit_won"]),
            "one_share_cost_won": int(cost), "per_symbol_budget_won": budget,
            "reason": "ONE_SHARE_COST_ABOVE_PER_SYMBOL_BUDGET",
            "paper_only": True, "no_order_or_fill_created": True})
        excluded += 1
    return excluded


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    import d_v19_portfolio as v19
    original = v19.D19PaperEngine._enqueue_signals

    def enqueue_then_exclude(self, c, now):
        result = original(self, c, now)
        try:
            sweep(self, c, now)
        except Exception as exc:          # never break order creation itself
            try:
                self.store.write_runtime_error(exc, "v020_limit_exclusion")
            except Exception:
                pass
        return result

    v19.D19PaperEngine._enqueue_signals = enqueue_then_exclude
    _INSTALLED = True
