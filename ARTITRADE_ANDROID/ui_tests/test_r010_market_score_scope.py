"""Owner statement 2026-11-20: the signed market score gates ONLY the reference-bar (AT)
strategy. Stock MA and stock MarketTrend must never be stopped by it (issue #118)."""
import re
import unittest
from pathlib import Path

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


def text(name):
    return (PY / name).read_text("utf8")


class MarketScoreScope(unittest.TestCase):
    def test_ma_and_trend_modules_do_not_use_the_score_gate(self):
        for name in ("ma_v067_stock_runtime.py", "ma_v067_policy.py",
                     "market_trend_1118_runtime.py", "market_trend_runtime_v043.py",
                     "market_trend_closed_entry_v107.py"):
            body = text(name)
            for forbidden in ("score_evidence", "validate_stock_score",
                              "at_market_score_gate_v107", "SCORE_UNVERIFIED",
                              "MARKET_SCORE_NOT_POSITIVE"):
                self.assertNotIn(forbidden, body, (name, forbidden))

    def test_gate_wraps_only_reference_bar_buy_functions(self):
        gate = text("at_market_score_gate_v107.py")
        wrapped = re.findall(r"native_\w+\s*=\s*([\w.]+)", gate)
        self.assertEqual(sorted(wrapped), sorted([
            "d_engine.PaperEngine._buy_if_touch",
            "d_v19_portfolio.D19PaperEngine._buy_limit_depth",
            "d_upbit.CoinLedger._buy"]))
        self.assertIn('path.parent.name=="base"', gate)   # coin: base ledger only

    def test_reference_runtime_gate_is_limited_to_reference_buys(self):
        ref = text("reference_1118_runtime.py")
        for needle in ("validate_stock_score",):
            uses = [m.start() for m in re.finditer(needle, ref)]
            self.assertGreaterEqual(len(uses), 3)   # buy_d19, buy_legacy, buy_coin
        self.assertIn("if _is_base_coin(self):", ref)   # coin MA / trend ledgers are exempt


if __name__ == "__main__":
    unittest.main()
