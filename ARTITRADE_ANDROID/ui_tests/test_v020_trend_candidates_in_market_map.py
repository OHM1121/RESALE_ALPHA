"""0.0.20: MarketTrend stock candidates must be in the live symbol map (#116).

The original feed parser drops every 0B print whose code is not in
LivePaperRunner.market_map. The map is rebuilt every 30 minutes while
MarketTrend refreshes its TOP50 candidates every 5 minutes, so a stock that
entered the TOP50 in between got NO prices and could never become a buy-pending
watch or a BUY. Synthetic data only; no broker, no order.
"""
import datetime as dt
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
import stock_candidate_universe_v020 as cu

try:
    import d_live
    from d_storage import KST
    ENGINE = True
except Exception:  # pragma: no cover
    ENGINE = False


def core_with(candidates):
    trend = SimpleNamespace(lock=threading.RLock(), _candidates={"stock": candidates, "coin": {}})
    return SimpleNamespace(_RUNTIME=SimpleNamespace(market_trend=trend))


class CandidateIdentity(unittest.TestCase):
    def test_only_valid_kospi_kosdaq_six_digit_codes_are_taken(self):
        core = core_with({"005930": ("KOSPI", "A"), "123456": ["KOSDAQ", "B"],
                          "12345": ("KOSPI", "short"), "abcdef": ("KOSPI", "alpha"),
                          "000660": ("NASDAQ", "foreign"), "111111": "bad-shape",
                          "222222": ("KOSPI",)})
        self.assertEqual(cu.trend_stock_candidates(core),
                         {"005930": ("KOSPI", "A"), "123456": ("KOSDAQ", "B")})

    def test_no_runtime_or_trend_means_no_candidates_not_an_error(self):
        self.assertEqual(cu.trend_stock_candidates(SimpleNamespace()), {})
        self.assertEqual(cu.trend_stock_candidates(SimpleNamespace(_RUNTIME=None)), {})
        self.assertEqual(cu.trend_stock_candidates(SimpleNamespace(_RUNTIME=SimpleNamespace())), {})

    def test_extra_identities_are_bounded(self):
        many = {f"{i:06d}": ("KOSPI", "x") for i in range(1, 400)}
        self.assertEqual(len(cu.trend_stock_candidates(core_with(many))), cu.MAX_EXTRA_IDENTITIES)


class CopyOnWriteMapExtension(unittest.TestCase):
    def test_missing_identities_are_added_without_mutating_the_old_dict(self):
        old_market = {"000001": ("KOSPI", "keep")}
        old_krx = {"000001": ("KOSPI", "keep")}
        runner = SimpleNamespace(market_map=old_market, krx_market_map=old_krx)
        added = cu.extend_market_map(runner, {"000001": ("KOSPI", "keep"), "005930": ("KOSPI", "new")})
        self.assertEqual(added, 1)
        self.assertEqual(set(runner.market_map), {"000001", "005930"})
        self.assertEqual(set(runner.krx_market_map), {"000001", "005930"})
        self.assertEqual(set(old_market), {"000001"}, "a thread iterating the old dict is undisturbed")
        self.assertIsNot(runner.market_map, old_market)

    def test_existing_identity_is_never_overwritten_and_second_call_is_a_noop(self):
        runner = SimpleNamespace(market_map={"005930": ("KOSPI", "native")}, krx_market_map={})
        self.assertEqual(cu.extend_market_map(runner, {"005930": ("KOSPI", "other")}), 0)
        self.assertEqual(runner.market_map["005930"], ("KOSPI", "native"))
        before = runner.market_map
        self.assertEqual(cu.extend_market_map(runner, {}), 0)
        self.assertIs(runner.market_map, before)
        self.assertEqual(cu.extend_market_map(None, {"1": ("KOSPI", "x")}), 0)


@unittest.skipUnless(ENGINE, "needs the original engine on sys.path")
class ParserProof(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 20, 10, 15, 5, tzinfo=KST) if ENGINE else None
    MSG = {"trnm": "REAL", "data": [{"type": "0B", "item": "005930", "values": {
        "9081": "1", "20": "101500", "10": "+70000", "15": "+10", "27": "70100", "28": "70000"}}]}

    def test_print_is_dropped_before_and_accepted_after_the_candidate_joins_the_map(self):
        runner = SimpleNamespace(market_map={"000660": ("KOSPI", "OTHER")}, krx_market_map={})
        self.assertEqual(d_live.parse_venue_real(self.MSG, runner.market_map, seen_at=self.NOW), [],
                         "the defect: an unmapped code gets no price at all")
        cu.extend_market_map(runner, {"005930": ("KOSPI", "CANDIDATE")})
        ticks = d_live.parse_venue_real(self.MSG, runner.market_map, seen_at=self.NOW)
        self.assertEqual(len(ticks), 1)
        self.assertEqual(ticks[0][:3], ("005930", "KOSPI", "CANDIDATE"))
        self.assertEqual(ticks[0][4], 70000, "the real print's price is untouched")

    def test_install_wraps_both_entry_points_once_and_keeps_native_results(self):
        import market_trend_runtime_v043 as trend_runtime
        core = core_with({"005930": ("KOSPI", "CANDIDATE")})
        native_pins = d_live.LivePaperRunner._verified_rank_and_pins
        native_refresh = trend_runtime.Coordinator._refresh_rank
        try:
            cu._INSTALLED = False
            cu.install(core)
            cu.install(core)   # idempotent
            runner = d_live.LivePaperRunner.__new__(d_live.LivePaperRunner)
            runner._pinned_stocks = lambda: {"000001": ("KOSPI", "held")}
            merged = d_live.LivePaperRunner._verified_rank_and_pins(runner, {"000660": ("KOSPI", "rank")})
            self.assertEqual(set(merged), {"000001", "000660", "005930"})
            self.assertEqual(merged["000001"], ("KOSPI", "held"))
        finally:
            d_live.LivePaperRunner._verified_rank_and_pins = native_pins
            trend_runtime.Coordinator._refresh_rank = native_refresh
            cu._INSTALLED = False

    def test_refresh_hook_extends_the_map_for_stock_only(self):
        import market_trend_runtime_v043 as trend_runtime
        core = core_with({"005930": ("KOSPI", "CANDIDATE")})
        native_pins = d_live.LivePaperRunner._verified_rank_and_pins
        native_refresh = trend_runtime.Coordinator._refresh_rank
        calls = []
        trend_runtime.Coordinator._refresh_rank = lambda self, market: calls.append(market) or "native-result"
        try:
            cu._INSTALLED = False
            cu.install(core)
            coordinator = trend_runtime.Coordinator.__new__(trend_runtime.Coordinator)
            runner = SimpleNamespace(market_map={}, krx_market_map={})
            coordinator.rt = SimpleNamespace(stock_runner=runner)
            self.assertEqual(trend_runtime.Coordinator._refresh_rank(coordinator, "coin"), "native-result")
            self.assertEqual(runner.market_map, {}, "coin refresh must not touch the stock map")
            self.assertEqual(trend_runtime.Coordinator._refresh_rank(coordinator, "stock"), "native-result")
            self.assertEqual(calls, ["coin", "stock"])
            self.assertIn("005930", runner.market_map)
        finally:
            d_live.LivePaperRunner._verified_rank_and_pins = native_pins
            trend_runtime.Coordinator._refresh_rank = native_refresh
            cu._INSTALLED = False


class WiringContract(unittest.TestCase):
    def test_runtime_installs_it_after_the_trend_and_priority_modules(self):
        text = (Path(__file__).resolve().parents[1] / "app/src/main/python/android_runtime_v078.py").read_text(encoding="utf8")
        a = text.index("_install_market_trend_v043(_core)")
        b = text.index("_install_stock_priority_v093(_core)")
        c = text.index("_install_stock_candidates_v020(_core)")
        self.assertLess(a, c)
        self.assertLess(b, c)


if __name__ == "__main__":
    unittest.main()
