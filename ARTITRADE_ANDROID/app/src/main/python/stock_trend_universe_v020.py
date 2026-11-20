"""0.0.20 (owner decision, issue #117): stock MarketTrend uses the AT universe.

The trading-value TOP50 candidate list applies to COIN only. For stocks the
MarketTrend candidates are exactly the symbols the reference-bar (AT) strategy
already works with: ``LivePaperRunner.krx_market_map`` (top-N per market by
trading value, ETF/ETN excluded, plus pinned holdings), rebuilt by the runner.

Consequences: no separate Kiwoom ranking request for the Trend, no mismatch
between the Trend list and the symbol map (the feed drops prints for unmapped
codes), and the same live-quote subscription as AT. The coin path is untouched.
Identity only; never a price, order or fill.
"""
from __future__ import annotations

_INSTALLED = False
FLAG = "_v020_stock_universe_candidates"


def universe_identities(runner):
    """{code: (market, name)} of valid KOSPI/KOSDAQ six-digit symbols."""
    if runner is None:
        return {}
    source = getattr(runner, "krx_market_map", None) or getattr(runner, "market_map", None) or {}
    out = {}
    for code, identity in dict(source).items():
        code = str(code).strip()
        if not (len(code) == 6 and code.isdigit()):
            continue
        if not isinstance(identity, (tuple, list)) or len(identity) < 2:
            continue
        market, name = str(identity[0]), str(identity[1])
        if market in ("KOSPI", "KOSDAQ"):
            out[code] = (market, name)
    return out


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    import time
    import market_trend_runtime_v043 as trend_runtime

    original = trend_runtime.Coordinator._refresh_rank

    def refresh(self, market):
        if market != "stock":
            return original(self, market)          # coin: TOP50 by trading value
        runner = getattr(self.rt, "stock_runner", None)
        ids = universe_identities(runner)
        if not ids:
            # The AT universe is not built yet. Keep what we have; do not fall
            # back to a separate ranking request.
            self._last_fetch_error["stock"] = "AT_UNIVERSE_NOT_READY"
            return None
        with self.lock:
            if self._candidates.get("stock") != ids:
                self._candidates["stock"] = ids
            self._last_rank["stock"] = time.monotonic()
            self._last_fetch_error["stock"] = ""
        setattr(self, FLAG, True)
        return None

    trend_runtime.Coordinator._refresh_rank = refresh
    _INSTALLED = True
