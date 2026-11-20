"""0.0.20: CLOSE / MA / TREND candidate symbols must actually get live quotes.

Defect replayed here (issue #116): the shared Kiwoom socket has 200 slots. The
whole-market band lists every symbol and was de-duplicated FIRST, so the
CLOSE/MA/TREND/GMO candidate bands became EMPTY and their candidates were live
only ~40% of the time (whenever the rotating whole-market slice included them).
A candidate that is not subscribed has no 0B print and no 0D book, so it can
neither reach "buy pending" nor be bought.

Synthetic symbols only; no broker, no orders.
"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
import stock_priority_runtime_v093 as sp

WEIGHTS = (5, 4, 3, 2, 1)


def legacy_allocate(bands, protected=(), capacity=200, phase=0):
    """The pre-0.0.20 algorithm, kept verbatim to prove the defect."""
    limit = max(0, min(int(capacity), 200))
    chosen, assigned = [], set()
    for code in protected:
        code = str(code)
        if code and code not in assigned:
            chosen.append(code)
            assigned.add(code)
    groups = []
    for band in bands:
        values = []
        for code in band:
            code = str(code)
            if code and code not in assigned:
                assigned.add(code)
                values.append(code)
        groups.append(values)
    room = limit - len(chosen)
    quotas = [0] * len(groups)
    while room > 0:
        progressed = False
        for i, weight in enumerate(WEIGHTS):
            take = min(weight, room, len(groups[i]) - quotas[i])
            if take > 0:
                quotas[i] += take
                room -= take
                progressed = True
            if room <= 0:
                break
        if not progressed:
            break
    for group, amount in zip(groups, quotas):
        if amount <= 0:
            continue
        anchor = min(12, amount // 3)
        chosen.extend(group[:anchor])
        remainder = amount - anchor
        tail = group[anchor:]
        if remainder > 0:
            shift = (max(0, int(phase)) * remainder) % len(tail)
            chosen.extend((tail[shift:] + tail[:shift])[:remainder])
    return chosen


def scenario(trend=50, ma=40, close=6, gmo=6, held=5, seed=7):
    rnd = random.Random(seed)
    universe = [f"{i:06d}" for i in range(1, 460)]
    pick = lambda n: rnd.sample(universe, n)
    return {"universe": universe, "trend": pick(trend), "ma": pick(ma), "close": pick(close),
            "gmo": pick(gmo), "held": pick(held)}


def bands_of(s):
    return [s["universe"], s["close"], s["ma"], s["trend"], s["gmo"]]


class DefectReplay(unittest.TestCase):
    def test_legacy_algorithm_left_candidates_live_less_than_60_percent(self):
        s = scenario()
        fractions = []
        for phase in range(40):
            live = set(legacy_allocate(bands_of(s), s["held"], 200, phase))
            fractions.append(len(live & set(s["trend"])) / len(s["trend"]))
        self.assertLess(sum(fractions) / len(fractions), 0.6)
        self.assertLessEqual(min(fractions), 0.35)

    def test_legacy_candidate_bands_were_empty_after_deduplication(self):
        s = scenario()
        assigned, sizes = set(s["held"]), []
        for band in bands_of(s):
            fresh = [c for c in band if c not in assigned]
            assigned.update(fresh)
            sizes.append(len(fresh))
        self.assertEqual(sizes[1:], [0, 0, 0, 0])


class FixedCoverage(unittest.TestCase):
    def test_every_candidate_is_live_at_every_rotation_phase_when_they_fit(self):
        s = scenario(trend=50, ma=40, close=6, gmo=6)
        want = set(s["trend"]) | set(s["ma"]) | set(s["close"]) | set(s["gmo"])
        for phase in range(60):
            live = sp.allocate_priority(bands_of(s), s["held"], 200, phase)
            self.assertEqual(len(live), 200)
            self.assertEqual(len(set(live)), 200)
            self.assertFalse(set(s["held"]) - set(live), phase)
            self.assertFalse(want - set(live), (phase, len(want - set(live))))

    def test_whole_market_scan_keeps_its_weighted_floor(self):
        s = scenario(trend=100, ma=60, close=40, gmo=10)
        live = sp.allocate_priority(bands_of(s), s["held"], 200, 3)
        specific = set(s["trend"]) | set(s["ma"]) | set(s["close"]) | set(s["gmo"]) | set(s["held"])
        only_scan = [c for c in live if c not in specific]
        room = 200 - len(s["held"])
        self.assertGreaterEqual(len(only_scan), min(room * 5 // 15, len(set(s["universe"]) - specific)))

    def test_candidate_bands_share_overflow_by_weight_close_ma_trend_gmo(self):
        bands = [[f"U{i:03d}" for i in range(459)]] + \
                [[f"{p}{i:03d}" for i in range(200)] for p in "CMTG"]
        live = sp.allocate_priority(bands, [], 200, 0)
        counts = {p: sum(c.startswith(p) for c in live) for p in "UCMTG"}
        self.assertGreater(counts["C"], counts["M"])
        self.assertGreater(counts["M"], counts["T"])
        self.assertGreater(counts["T"], counts["G"])
        self.assertGreaterEqual(counts["G"], 1)
        self.assertEqual(sum(counts.values()), 200)

    def test_spare_capacity_flows_to_the_whole_market_scan(self):
        bands = [[f"U{i:03d}" for i in range(459)], ["C1"], ["M1"], ["T1"], ["G1"]]
        live = sp.allocate_priority(bands, [], 200, 0)
        self.assertEqual(len(live), 200)
        for code in ("C1", "M1", "T1", "G1"):
            self.assertIn(code, live)

    def test_a_symbol_in_several_lists_uses_one_slot_and_is_never_lost(self):
        live = sp.allocate_priority([["A", "B", "C"], ["A"], ["A", "B"], ["B", "C"], ["C"]], [], 200, 0)
        self.assertEqual(sorted(live), ["A", "B", "C"])

    def test_protected_symbols_still_come_first_and_the_cap_still_holds(self):
        s = scenario()
        live = sp.allocate_priority(bands_of(s), s["held"], 200, 9)
        self.assertEqual(live[:len(s["held"])], s["held"])
        self.assertLessEqual(len(live), 200)
        self.assertEqual(sp.allocate_priority(bands_of(s), s["held"], 0, 0), [])


if __name__ == "__main__":
    unittest.main()
