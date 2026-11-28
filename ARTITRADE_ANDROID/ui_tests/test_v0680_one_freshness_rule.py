"""0.6.8 (owner 2026-11-28 "모든 전략에 일괄 적용 ... 코인 주식 둘 다 ... 매도도 3초 룰 없이"): ONE freshness rule (fresh_rule_v0680) for every strategy, stock and coin, buy and sell."""
import datetime as dt
import re
import time
import unittest
from pathlib import Path

import fresh_rule_v0680 as fr

KST = dt.timezone(dt.timedelta(hours=9))
PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


class Rule(unittest.TestCase):
    def setUp(self):
        fr._LAST["stock"] = fr._LAST["coin"] = 0.0

    def test_a_book_within_3_seconds_is_fresh_as_before_even_with_a_dead_line(self):
        for age in (-0.4, 0.0, 2.9, 3.0):
            self.assertTrue(fr.book_ok(age), age)
        self.assertFalse(fr.book_ok(3.5))                         # dead line: the old 3 second behaviour
        self.assertFalse(fr.book_ok(-2.0))

    def test_a_quiet_book_stays_usable_while_the_line_is_alive(self):
        fr.touch("stock")
        self.assertTrue(fr.book_ok(45.0))                          # nothing changed for 45 s: the book is still the book
        self.assertTrue(fr.book_ok(119.0))
        self.assertFalse(fr.book_ok(121.0))                        # beyond two minutes the direct inquiry has to renew it
        self.assertFalse(fr.book_ok(-3.0))

    def test_a_queued_print_that_reached_the_target_is_usable_while_the_line_is_alive(self):
        fr.touch("stock")
        self.assertTrue(fr.print_ok(8.0))
        self.assertTrue(fr.print_ok(114.0))
        self.assertFalse(fr.print_ok(116.0))

    def test_the_two_markets_are_independent(self):
        fr.touch("coin")
        self.assertTrue(fr.book_ok(20.0, "coin"))
        self.assertFalse(fr.book_ok(20.0, "stock"))

    def test_the_line_dies_after_the_alive_window(self):
        fr.touch("stock", time.monotonic() - 25.0)
        self.assertFalse(fr.alive("stock"))
        self.assertFalse(fr.print_ok(10.0))
        fr.touch("stock")
        self.assertTrue(fr.alive("stock"))

    def test_garbage_is_not_fresh(self):
        fr.touch("stock")
        for bad in (None, "x", float("nan")):
            self.assertFalse(fr.book_ok(bad))
            self.assertFalse(fr.print_ok(bad))


class VendorBook(unittest.TestCase):
    """The vendor order book the stock strategies trade against (d_v19_portfolio.Book, with the real-parity overlay)."""

    def setUp(self):
        try:
            import d_v19_portfolio as portfolio
        except Exception:
            self.skipTest("vendor engine not on the path")
        import real_parity_v0500 as parity
        parity.install_book_depth()
        self.portfolio = portfolio
        fr._LAST["stock"] = 0.0
        self.now = dt.datetime(2026, 11, 26, 10, 0, 0)
        self.old = self.now - dt.timedelta(seconds=40)
        self.book = portfolio.Book()
        self.book._items[("X", "KRX")] = (4950, 4900, 10, self.old)
        self.book._bid_depth[("X", "KRX")] = (4900, 10, self.old)

    def test_buy_a_quiet_book_with_a_dead_line_is_refused_as_before(self):
        quote, why = self.book.available("X", "KRX", self.now, 4950, 5)
        self.assertEqual((quote, why), (None, "STALE_ORDERBOOK"))

    def test_buy_a_quiet_book_with_a_live_line_is_used(self):
        fr.touch("stock")
        quote, why = self.book.available("X", "KRX", self.now, 4950, 5)
        self.assertIsNotNone(quote, why)
        self.assertEqual((quote[0], quote[1]), (4950, 5))
        self.assertTrue(self.book.reserve("X", "KRX", self.now, 4950, 5))

    def test_sell_the_bid_quantity_at_the_price_decides_not_the_age(self):
        self.assertEqual(self.book.available_sell("X", "KRX", self.now, 4900, 6), 0)       # dead line: as before
        fr.touch("stock")
        self.assertEqual(self.book.available_sell("X", "KRX", self.now, 4900, 6), 6)       # the displayed bid size is there: sell
        self.assertEqual(self.book.available_sell("X", "KRX", self.now, 4900, 50), 10)     # never more than the displayed size
        self.assertEqual(self.book.available_sell("X", "KRX", self.now, 4800, 6), 0)       # another price level: nothing invented

    def test_a_book_older_than_two_minutes_is_not_traded_on(self):
        fr.touch("stock")
        self.book._items[("X", "KRX")] = (4950, 4900, 10, self.now - dt.timedelta(minutes=5))
        self.assertEqual(self.book.available("X", "KRX", self.now, 4950, 5)[1], "STALE_ORDERBOOK")


class VendorPrint(unittest.TestCase):
    def test_the_exchange_stamp_test_follows_the_rule(self):
        try:
            from d_minute_repair_fast import exchange_stamp_is_fresh
        except Exception:
            self.skipTest("vendor engine not on the path")
        now = dt.datetime.now(KST)
        stamp = (now - dt.timedelta(seconds=20)).isoformat()
        fr._LAST["stock"] = 0.0
        self.assertFalse(exchange_stamp_is_fresh(stamp, stamp, now))
        fr.touch("stock")
        self.assertTrue(exchange_stamp_is_fresh(stamp, stamp, now))
        old = (now - dt.timedelta(minutes=5)).isoformat()
        self.assertFalse(exchange_stamp_is_fresh(old, old, now))
        # the receipt may not lag the exchange stamp by more than 3 s (a transport defect, not a quiet market)
        late = (now - dt.timedelta(seconds=5)).isoformat()
        self.assertFalse(exchange_stamp_is_fresh(stamp, late, now))


def _read(path):
    return Path(path).read_text("utf8")


class EverySiteUsesTheRule(unittest.TestCase):
    """No strategy keeps its own fixed 3 second test (source level; vendor files from the patched engine when it is on the path)."""
    OVERLAY = {
        "real_parity_v0500.py": 3, "market_trend_runtime_v043.py": 3, "market_trend_preplan_v099.py": 3, "rest_book_confirm_v0580.py": 1,
        "ma_v067_coin_runtime.py": 4, "coin_held_depth_v014.py": 1, "coin_partial_entry_v094.py": 1,
        "android_runtime_v078.py": 2, "gmo_d1_preplan_v097.py": 6, "gmo_partial_entry_v094.py": 1, "ma_v061_fast_entry.py": 1, "ma_v061_stock_runtime.py": 3,
        "ma_v067_stock_runtime.py": 4, "reference_1118_runtime.py": 1, "trend_partial_entry_v094.py": 2, "unified_recovery_v092.py": 2,
    }
    # not trading decisions of a strategy: a person's own tap (manual sale: the price on the screen must be the price sold), screens / diagnostics, candle counts, the broker-time transport check
    EXEMPT = {"android_manual_paper_sell_v085.py", "stock_manual_v086.py", "market_feed_health_v004.py", "market_trend_history_v0550.py", "stock_quote_truth_v066.py", "ai_entry_context_v047.py"}

    def test_overlays(self):
        for name, count in self.OVERLAY.items():
            self.assertGreaterEqual(_read(PY / name).count("fresh_rule_v0680"), count, name)

    def test_no_overlay_keeps_a_raw_3_second_comparison(self):
        raw = re.compile(r"(<=\s*3\.0\b|>\s*3\.0\b|<=\s*MAX_BOOK_AGE_SEC|>\s*MAX_BOOK_AGE_SEC|BOOK_MAX_AGE_SEC\s*(<|>))")
        for name in self.OVERLAY:
            for number, line in enumerate(_read(PY / name).splitlines(), 1):
                code = line.split("#")[0]
                if raw.search(code) and "fresh_rule_v0680" not in code:
                    self.fail("%s:%d still has a fixed age test: %s" % (name, number, line.strip()[:120]))

    def test_the_whole_overlay_folder_has_no_other_fixed_3_second_test(self):
        pattern = re.compile(r"(total_seconds\(\)\)?\s*(<=|>)\s*3(\.0)?\b|(monotonic\(\)\s*-\s*\w+\s*(<=|>)\s*3(\.0)?\b)|(age\s*(<=|>)\s*3(\.0)?\b)|(lag\s*(>)\s*3(\.0)?\b)|<=\s*3\s*\)|(<=|>)\s*MAX_BOOK_AGE_SEC|>\s*\(\s*10\s+if\s+market[^)]*else\s+3\s*\))")
        found = []
        for path in sorted(PY.glob("*.py")):
            if path.name in self.EXEMPT or path.name == "fresh_rule_v0680.py":
                continue
            for number, line in enumerate(path.read_text("utf8").splitlines(), 1):
                code = line.split("#")[0]
                if "3*" in code.replace(" ", "") or "fresh_rule_v0680" in code:
                    continue
                if pattern.search(code):
                    found.append("%s:%d %s" % (path.name, number, line.strip()[:100]))
        self.assertEqual(found, [])

    def test_vendor_files(self):
        try:
            import d_live
        except Exception:
            self.skipTest("vendor engine not on the path")
        import importlib
        sites = {"d_minute_repair_fast": 1, "d_live": 4, "d_v19_portfolio": 4, "d_gmo_runtime": 3, "at_close_engine": 1, "at_close_runner": 3, "at_close_bridge": 1, "d_upbit": 5}
        for module, count in sites.items():
            text = _read(importlib.import_module(module).__file__)
            self.assertGreaterEqual(text.count("fresh_rule_v0680"), count, module)

    def test_the_streams_report_that_the_line_is_alive(self):
        try:
            import d_live
        except Exception:
            self.skipTest("vendor engine not on the path")
        self.assertIn("touch('stock')", _read(d_live.__file__))
        import d_upbit
        self.assertIn("touch('coin')", _read(d_upbit.__file__))


class PatchSource(unittest.TestCase):
    def test_the_patch_carries_every_site(self):
        text = (Path(__file__).parent / "patch_v065_post_session_lookback.py").read_text("utf8")
        for module in ("d_minute_repair_fast.py", "d_live.py", "d_gmo_runtime.py", "at_close_engine.py", "at_close_runner.py", "at_close_bridge.py", "d_upbit.py"):
            self.assertIn('edit_file("%s"' % module, text)
        self.assertGreaterEqual(text.count("fresh_rule_v0680"), 20)


if __name__ == "__main__":
    unittest.main()
