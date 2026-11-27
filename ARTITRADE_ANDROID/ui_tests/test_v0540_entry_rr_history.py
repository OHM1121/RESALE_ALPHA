"""0.5.4 (#169): BUY-time reward / risk shown in the execution history (read-only join of the sidecars by the native position id)."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import entry_rr_v0540 as rr

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"
JAVA = Path(__file__).resolve().parents[1] / "app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java"
RR_SIZE = {"version": "RR_SIZE_V0340", "ratio": 0.71, "gain_pct": 3.0, "loss_pct": 4.225, "applied": False, "stop_source": "REFERENCE_HARD_STOP"}


def make(root, rel, table, id_col, json_col, rows):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(path)
    cx.execute("CREATE TABLE %s(%s INTEGER PRIMARY KEY, %s TEXT)" % (table, id_col, json_col))
    for ident, payload in rows:
        cx.execute("INSERT INTO %s VALUES(?,?)" % table, (ident, json.dumps(payload)))
    cx.commit()
    cx.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name
        rr._CACHE.clear()


class Extract(Base):
    def test_entry_ctx_rr_wins_and_rr_size_is_the_fallback(self):
        both = {"entry_ctx": {"rr": {"ratio": 1.2, "gain_pct": 2.0, "loss_pct": 1.7, "stop_source": "X"}}, "rr_size": RR_SIZE}
        self.assertEqual(rr.extract(both)["ratio"], 1.2)
        self.assertEqual(rr.extract({"rr_size": RR_SIZE})["ratio"], 0.71)
        self.assertEqual(rr.extract({"rr_size": RR_SIZE})["stop_source"], "REFERENCE_HARD_STOP")

    def test_unrecorded_or_unverified_values_are_never_invented(self):
        self.assertIsNone(rr.extract({}))
        self.assertIsNone(rr.extract({"rr_size": {"ratio": None, "reason": "RR_UNVERIFIED"}}))
        self.assertIsNone(rr.extract({"entry_ctx": {"rr": None}}))
        self.assertIsNone(rr.extract("text"))


class Annotate(Base):
    def packet(self):
        return {"stock": {"at": {"positions": [{"id": 7, "code": "446840"}, {"id": 8, "code": "000001"}],
                                 "executions": [{"position_id": 7, "side": "BUY"}, {"position_id": 7, "side": "SELL"}, {"position_id": 99, "side": "BUY"}]}},
                "coin": {"ma": {"positions": [{"id": 5}], "executions": [{"position_id": 5, "side": "BUY"}]},
                         "general": {"executions": [{"position_id": 31}]}},
                "stock_gmo_like": {"gmo": {"executions": [{"position_id": 1}]}}}

    def test_every_strategy_family_is_joined_by_its_own_position_id(self):
        make(self.root, "stock/ARTITRADE_D.sqlite3", "nl1118_reference_positions", "position_id", "intent_json", [(7, {"rr_size": RR_SIZE})])
        make(self.root, "coin/ma_epoch_v067/upbit_paper.sqlite3", "ma_v067_positions", "position_id", "state_json",
             [(5, {"rr_size": dict(RR_SIZE, ratio=1.264, gain_pct=2.0, loss_pct=1.582)})])
        make(self.root, "coin/base/upbit_paper.sqlite3", "nl1118_reference_positions", "position_id", "intent_json", [(31, {"entry_ctx": {"rr": {"ratio": 0.3}}})])
        packet = rr.annotate_packet(self.root, self.packet())
        at = packet["stock"]["at"]
        self.assertEqual(at["positions"][0]["entry_rr"]["ratio"], 0.71)
        self.assertNotIn("entry_rr", at["positions"][1])                         # no record: nothing is shown
        self.assertEqual([("entry_rr" in x) for x in at["executions"]], [True, True, False])
        self.assertEqual(packet["coin"]["ma"]["executions"][0]["entry_rr"]["ratio"], 1.264)
        self.assertEqual(packet["coin"]["general"]["executions"][0]["entry_rr"]["ratio"], 0.3)
        self.assertNotIn("entry_rr", packet["stock_gmo_like"]["gmo"]["executions"][0])     # supply-demand has no stop: no reward / risk
        self.assertEqual(packet["entry_rr_v0540"], {"schema": "ENTRY_RR_V0540", "rows": 6, "read_only": True})

    def test_the_trend_wallet_uses_its_id_and_payload_columns(self):
        make(self.root, "coin/trend/market_trend_wallet_v054.sqlite3", "positions", "id", "payload", [(31, {"rr_size": dict(RR_SIZE, ratio=0.2333, applied=True)})])
        packet = rr.annotate_packet(self.root, {"coin": {"trend": {"positions": [{"id": 31}], "executions": [{"position_id": 31}]}}})
        self.assertEqual(packet["coin"]["trend"]["positions"][0]["entry_rr"]["ratio"], 0.2333)
        self.assertEqual(packet["coin"]["trend"]["executions"][0]["entry_rr"]["gain_pct"], 3.0)

    def test_missing_databases_tables_and_bad_json_never_break_the_snapshot(self):
        packet = rr.annotate_packet(self.root, self.packet())                          # no databases at all
        self.assertNotIn("entry_rr", packet["stock"]["at"]["positions"][0])
        path = Path(self.root) / "stock/ARTITRADE_D.sqlite3"
        path.parent.mkdir(parents=True)
        cx = sqlite3.connect(path)
        cx.execute("CREATE TABLE unrelated(x)")
        cx.commit()
        cx.close()
        rr._CACHE.clear()
        self.assertEqual(rr.annotate_packet(self.root, self.packet())["stock"]["at"]["executions"][0].get("entry_rr"), None)
        make(self.root, "coin/ma_epoch_v067/upbit_paper.sqlite3", "ma_v067_positions", "position_id", "state_json", [(5, None)])
        cx = sqlite3.connect(Path(self.root) / "coin/ma_epoch_v067/upbit_paper.sqlite3")
        cx.execute("UPDATE ma_v067_positions SET state_json='{not json'")
        cx.commit()
        cx.close()
        rr._CACHE.clear()
        self.assertNotIn("entry_rr", rr.annotate_packet(self.root, self.packet())["coin"]["ma"]["executions"][0])
        self.assertIsNone(rr.annotate_packet(self.root, None))

    def test_it_only_reads_and_the_values_are_cached_briefly(self):
        make(self.root, "stock/ARTITRADE_D.sqlite3", "nl1118_reference_positions", "position_id", "intent_json", [(7, {"rr_size": RR_SIZE})])
        path = Path(self.root) / "stock/ARTITRADE_D.sqlite3"
        before = path.read_bytes()
        first = rr.load(self.root, "stock/at")
        second = rr.load(self.root, "stock/at")
        self.assertIs(first, second)                                                   # cached, not re-read at every refresh
        self.assertEqual(path.read_bytes(), before)

    def test_existing_values_of_a_row_are_never_replaced(self):
        make(self.root, "stock/ARTITRADE_D.sqlite3", "nl1118_reference_positions", "position_id", "intent_json", [(7, {"rr_size": RR_SIZE})])
        packet = {"stock": {"at": {"executions": [{"position_id": 7, "entry_rr": {"ratio": 9.9}, "price": 100}]}}}
        rr.annotate_packet(self.root, packet)
        self.assertEqual(packet["stock"]["at"]["executions"][0], {"position_id": 7, "entry_rr": {"ratio": 9.9}, "price": 100})


class Wiring(unittest.TestCase):
    def test_the_hub_packet_passes_through_the_annotation_and_the_history_card_shows_it(self):
        uid = (PY / "position_uid_v015.py").read_text("utf8")
        self.assertIn("entry_rr_v0540.annotate_packet(self.root,mapped)", uid)
        self.assertLess(uid.index("mapped=annotate_packet(self.root,source)"), uid.index("entry_rr_v0540.annotate_packet"))
        java = JAVA.read_text("utf8")
        self.assertIn('optJSONObject("entry_rr")', java)
        self.assertIn("매수시 손익비", java)
        self.assertGreater(java.index("매수시 손익비"), java.index("private LinearLayout buildTradeGroupCard"))   # inside the history trade card


if __name__ == "__main__":
    unittest.main()
