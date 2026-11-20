"""0.0.20: MarketTrend stock candidates must be part of the live symbol map.

Defect (issue #116): the Kiwoom live feed only processes symbols present in
``LivePaperRunner.market_map``. ``parse_venue_real`` drops every 0B/0D print
whose code is not in it, and the shared-socket subscription planner only
subscribes symbols that map can identify. The map is the TOP-N by trading
value and is rebuilt every 30 minutes, while MarketTrend refreshes its own
TOP-50-per-market candidate list every 5 minutes. A stock that rises into the
TOP50 between two map rebuilds (typically a high-volume plunge, exactly the
case RSI<=30 looks for) was a Trend candidate but received no prices, so it
could neither show up as a buy-pending watch nor be bought.

This module only adds the candidates' IDENTITY (market, name) to the map:
  * after every MarketTrend stock rank refresh, and
  * whenever the runner rebuilds the map.
It never creates a price, quote, order, fill, candle or position. ETF/ETN
names are excluded by the original rank call already. The swap is
copy-on-write, so threads iterating the old dict are never disturbed.
"""
from __future__ import annotations

_INSTALLED = False
MAX_EXTRA_IDENTITIES = 150


def trend_stock_candidates(core):
    """Current MarketTrend stock candidates as {code: (market, name)}."""
    runtime = getattr(core, "_RUNTIME", None)
    trend = getattr(runtime, "market_trend", None) if runtime is not None else None
    if trend is None:
        return {}
    with trend.lock:
        raw = dict(trend._candidates.get("stock", {}) or {})
    out = {}
    for code, identity in raw.items():
        code = str(code).strip()
        if not (len(code) == 6 and code.isdigit()):
            continue
        if not isinstance(identity, (tuple, list)) or len(identity) < 2:
            continue
        market, name = str(identity[0]), str(identity[1])
        if market not in ("KOSPI", "KOSDAQ"):
            continue
        out[code] = (market, name)
        if len(out) >= MAX_EXTRA_IDENTITIES:
            break
    return out


def extend_market_map(runner, extra):
    """Add identities that are missing; return how many were added."""
    if runner is None or not extra:
        return 0
    current = getattr(runner, "market_map", None) or {}
    missing = {c: i for c, i in extra.items() if c not in current}
    if not missing:
        return 0
    # Copy-on-write: assignment is atomic; readers keep a consistent dict.
    runner.market_map = {**current, **missing}
    krx = getattr(runner, "krx_market_map", None) or {}
    still = {c: i for c, i in missing.items() if c not in krx}
    if still:
        runner.krx_market_map = {**krx, **still}
    return len(missing)


def install(core):
    global _INSTALLED
    if _INSTALLED:
        return
    import d_live
    import market_trend_runtime_v043 as trend_runtime

    original_pins = d_live.LivePaperRunner._verified_rank_and_pins

    def pins_with_candidates(self, ranked):
        merged = original_pins(self, ranked)
        try:
            for code, identity in trend_stock_candidates(core).items():
                merged.setdefault(code, identity)
        except Exception:
            # Candidate identity is an addition only; the native map stands.
            pass
        return merged

    d_live.LivePaperRunner._verified_rank_and_pins = pins_with_candidates

    original_refresh = trend_runtime.Coordinator._refresh_rank

    def refresh_then_extend(self, market):
        result = original_refresh(self, market)
        if market == "stock":
            try:
                extend_market_map(getattr(self.rt, "stock_runner", None),
                                  trend_stock_candidates(core))
            except Exception:
                pass
        return result

    trend_runtime.Coordinator._refresh_rank = refresh_then_extend
    _INSTALLED = True
