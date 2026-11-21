"""Owner rule 2026-11-21: MA and MarketTrend (stock + coin) buy at HALF the per-symbol limit while the
signed market score of their market is <= -10 at the moment of the BUY.

  per-symbol limit = frame seed // frame max positions            (unchanged)
  limit used        = limit * 1/2  while score <= -10, else the full limit

The reduction is evaluated again at every BUY (and every top-up), so it recovers by itself as soon as the
score is above -10. It never stops a buy (market score still gates only the reference-bar AT strategy),
never sells, never touches holdings, cash or seeds. A missing / stale / unverified score is NOT a reason
to reduce: unknown is not -10.
"""
from __future__ import annotations
from decimal import Decimal

THRESHOLD=-10.0
REDUCED=Decimal("0.5")
FULL=Decimal(1)


def evidence(root,market):
    """(score or None, factor). Never raises."""
    try:
        from at_market_score_gate_v107 import score_evidence
        score=score_evidence(root,market).get("score")
    except Exception:
        score=None
    if score is not None and score<=THRESHOLD:
        return score,REDUCED
    return score,FULL


def factor(root,market):
    return evidence(root,market)[1]


def limit_krw(per_symbol,factor_value):
    """Integer KRW limit after the factor (floor)."""
    return int(Decimal(int(per_symbol))*Decimal(factor_value))
