"""0.4.1: GMO fills of one position share the position's identity (history shows them as one trade)."""
import types
import unittest
from pathlib import Path

import gmo_exec_link_v0410 as link
import position_uid_v015 as uid

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


def engine():
    return types.SimpleNamespace(
        used={"003380|2026-11-23", "000680|2026-11-19"},
        positions={"003380": types.SimpleNamespace(entry_date="2026-11-23"),
                   "000680": types.SimpleNamespace(entry_date="2026-11-19")},
        signals={"241560": types.SimpleNamespace(entry_date="2026-11-26")})


class Link(unittest.TestCase):
    def test_three_partial_buys_and_a_sell_get_the_position_identity(self):
        rows = [{"id": -9000000, "code": "003380", "side": "BUY", "ts": "2026-11-23T10:44:08+09:00", "qty": 1},
                {"id": -9000001, "code": "003380", "side": "BUY", "ts": "2026-11-23T10:44:08+09:00", "qty": 1},
                {"id": -9000002, "code": "003380", "side": "BUY", "ts": "2026-11-23T10:44:09+09:00", "qty": 1},
                {"id": -10000000, "code": "003380", "side": "SELL", "ts": "2026-11-27T09:05:00+09:00", "qty": 3}]
        self.assertEqual(link.link(rows, engine()), 4)
        keys = {uid._identity_key(r, "execution")[0] for r in rows}
        position = {"id": -1, "code": "003380", "entry_date": "2026-11-23", "tf": "gmo"}
        self.assertEqual(keys, {"entry:003380|2026-11-23"})
        self.assertEqual(uid._identity_key(position, "position")[0], "entry:003380|2026-11-23")

    def test_before_the_fix_every_fill_was_its_own_trade(self):
        rows = [{"id": -9000000, "code": "003380", "side": "BUY", "ts": "2026-11-23T10:44:08+09:00"},
                {"id": -9000001, "code": "003380", "side": "BUY", "ts": "2026-11-23T10:44:08+09:00"}]
        self.assertEqual(len({uid._identity_key(r, "execution")[0] for r in rows}), 2)

    def test_a_later_reentry_of_the_same_symbol_is_a_different_trade(self):
        e = engine()
        e.used.add("003380|2026-12-05")
        rows = [{"id": -1, "code": "003380", "side": "BUY", "ts": "2026-11-23T10:44:08+09:00"},
                {"id": -2, "code": "003380", "side": "BUY", "ts": "2026-12-05T09:10:00+09:00"}]
        link.link(rows, e)
        self.assertEqual([r["entry_date"] for r in rows], ["2026-11-23", "2026-12-05"])

    def test_unknown_symbol_or_existing_ids_are_left_alone_and_values_never_change(self):
        rows = [{"id": -1, "code": "999999", "side": "BUY", "ts": "2026-11-23T10:44:08+09:00", "price": 100, "qty": 5},
                {"id": 5, "position_id": 7, "code": "003380", "side": "BUY", "ts": "2026-11-23T10:44:08+09:00"},
                {"id": -3, "code": "003380", "entry_date": "2026-11-19", "side": "BUY", "ts": "2026-11-23T10:44:08+09:00"}]
        before = [dict(r) for r in rows]
        self.assertEqual(link.link(rows, engine()), 0)
        self.assertEqual(rows, before)
        self.assertEqual(link.link(None, engine()), 0)
        self.assertEqual(link.link(rows, object()), 0)

    def test_runtime_calls_it_in_the_gmo_snapshot_before_the_enrichment(self):
        src = (PY / "android_runtime_v078.py").read_text(encoding="utf-8")
        self.assertIn("_link_gmo_fills_v0410(snap.get('executions') or [],gmo.engine)", src)
        self.assertLess(src.index("_link_gmo_fills_v0410(snap"), src.index("_v086_enrich_gmo_executions(snap.get('executions') or [])"))


if __name__ == "__main__":
    unittest.main()
