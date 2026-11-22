"""Owner rule 2026-11-21: MA and MarketTrend (stock + coin) buy at HALF the per-symbol limit while the
signed market score of their market is <= -10 at the moment of the BUY.

  per-symbol limit = frame seed // frame max positions            (unchanged)
  limit used        = limit * 1/2  while score <= -10, else the full limit

The reduction is evaluated again at every BUY (and every top-up), so it recovers by itself as soon as the
score is above -10. It never stops a buy (market score still gates only the reference-bar AT strategy),
never sells, never touches holdings, cash or seeds. A missing / stale / unverified score is NOT a reason
to reduce: unknown is not -10.

0.2.9 (owner sheet 11.22, supersedes the rule above): the factor now follows the market regime
(market_regime_v0290.ma_trend_decision). UP market: full limit, the <= -10 half rule is deleted.
0.3.0: the regime is judged on the bought symbol on its strategy frame (symbol=, frame=).
DOWN: score >= -10 -> 50 %, -20..-10 -> 70 %, < -20 -> 100 %, >= +5 -> no NEW buy.
SIDEWAYS: score >= -10 -> 50 %, < -10 -> 100 %, >= +10 -> no NEW buy.
`new_buy_block` is checked by the NEW-entry paths only; top-ups use the factor.
"""
from __future__ import annotations
from decimal import Decimal

THRESHOLD=-10.0
REDUCED=Decimal("0.5")
FULL=Decimal(1)


def decision(root,market,symbol=None,frame=None,conn=None,closes=None):
    """Full regime sizing decision; never raises (falls back to the full limit)."""
    try:
        import market_regime_v0290 as regime
        return regime.ma_trend_decision(root,market,symbol=symbol,frame=frame,
                                        conn=conn,closes=closes)
    except Exception:
        return {"factor":FULL,"block":None,"regime":None,"applied":None,"score":None}


def evidence(root,market,symbol=None,frame=None,conn=None):
    """(score or None, factor). Never raises."""
    d=decision(root,market,symbol,frame,conn)
    return d.get("score"),d.get("factor",FULL)


def new_buy_block(root,market,symbol=None,frame=None,conn=None):
    """Reason string when a NEW MA/MarketTrend entry must not be opened now, else None."""
    return decision(root,market,symbol,frame,conn).get("block")


def factor(root,market,symbol=None,frame=None,conn=None):
    return evidence(root,market,symbol,frame,conn)[1]


def limit_krw(per_symbol,factor_value):
    """Integer KRW limit after the factor (floor)."""
    return int(Decimal(int(per_symbol))*Decimal(factor_value))
