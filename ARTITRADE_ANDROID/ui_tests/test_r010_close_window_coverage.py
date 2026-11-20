"""0.1.0: close-betting (종가베팅) must have its whole universe live inside its window.

Evidence (owner-supplied 0.0.18 error history, 2026-11-19/20): 70 of 90 close-betting
evaluations were rejected NO_FRESH_ORDERBOOK (book age 3-300 s) and 157 scan skips
were NO_LIVE_TRADE / STALE_TRADE_PRE_SCAN. The strategy scans 80 symbols but only a
rotating part of them was subscribed, so most had no fresh quote when evaluated.
Synthetic symbols only; no broker, no orders, no invented quotes.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
import stock_priority_runtime_v093 as sp

SRC = (Path(__file__).resolve().parents[1] / "app/src/main/python/stock_priority_runtime_v093.py").read_text("utf8")


def bands():
    universe = ["U%03d" % i for i in range(200)]            # whole-market band
    close = universe[:80]                                    # CW scan universe
    ma = ["U%03d" % i for i in range(120, 190)] + ["M%02d" % i for i in range(20)]
    gmo = ["G%02d" % i for i in range(30)]
    return [universe, close, ma, [], gmo], close


def select(hhmm, phase, held=("H0", "H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9")):
    five, close = bands()
    critical = list(held)
    critical.extend(sp.close_window_codes(hhmm, close, critical))
    return sp.allocate_priority(five, protected=critical, capacity=200, phase=phase), close


class CloseWindow(unittest.TestCase):
    def test_defect_without_the_window_rule_some_scan_symbols_are_not_live(self):
        five, close = bands()
        missing = 0
        for phase in range(12):
            chosen = set(sp.allocate_priority(five, protected=["H%d" % i for i in range(10)],
                                              capacity=200, phase=phase))
            missing += len([c for c in close if c not in chosen])
        self.assertGreater(missing, 0, "synthetic data no longer reproduces the shortfall")

    def test_inside_the_window_every_scan_symbol_is_subscribed_in_every_rotation(self):
        for hhmm in ("1450", "1500", "1510", "1519", "1520"):
            for phase in range(12):
                chosen, close = select(hhmm, phase)
                self.assertTrue(set(close) <= set(chosen), (hhmm, phase))
                self.assertLessEqual(len(chosen), 200)
                self.assertEqual(len(chosen), len(set(chosen)))

    def test_held_symbols_still_come_first(self):
        chosen, _ = select("1510", 3)
        self.assertEqual(chosen[:10], ["H%d" % i for i in range(10)])

    def test_outside_the_window_the_unchanged_allocation_applies(self):
        for hhmm in ("0900", "1449", "1521", "1530"):
            self.assertEqual(sp.close_window_codes(hhmm, ["A", "B"], ()), [])

    def test_already_protected_codes_are_not_duplicated_and_cap_is_enforced(self):
        self.assertEqual(sp.close_window_codes("1510", ["A", "B", "C"], ["B"]), ["A", "C"])
        many = ["S%03d" % i for i in range(300)]
        self.assertEqual(len(sp.close_window_codes("1510", many, ())), sp.CLOSE_WINDOW_MAX)

    def test_planner_applies_the_rule(self):
        self.assertIn("close_window_codes(hhmm, eligible[1],", SRC)
        # applied before allocation, on the already validated (exchange-eligible) band
        self.assertLess(SRC.index("close_window_codes(hhmm, eligible[1],"),
                        SRC.index("selected = allocate_priority("))


if __name__ == "__main__":
    unittest.main()
