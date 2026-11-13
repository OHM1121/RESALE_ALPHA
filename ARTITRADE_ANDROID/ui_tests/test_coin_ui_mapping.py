#!/usr/bin/env python3
"""Regression for inherited PC coin paper holding fields (without importing Chaquopy)."""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).parents[1] / "app" / "src" / "main" / "python" / "android_runtime_v078.py"
source_ast = ast.parse(SOURCE.read_text(encoding="utf-8"))
functions = [node for node in source_ast.body
             if isinstance(node, ast.FunctionDef) and
                node.name in ("_first_existing", "_normalize_coin_holdings")]
assert len(functions) == 2, "Coin position normalizer missing"
namespace = {}
exec(compile(ast.Module(body=functions, type_ignores=[]), str(SOURCE), "exec"), namespace)
normalize = namespace["_normalize_coin_holdings"]


class CoinPositionFieldsTest(unittest.TestCase):
    def test_existing_snapshot_fields_win_over_mismatched_legacy_names(self):
        actual = [{"id": 2, "tf": "5m", "symbol": "KRW-BTC",
                   "ref_ts": "20261101090500", "entry_price": 75000, "last_price": 76000}]
        db = [{"id": 2, "tf": "5m", "symbol": "KRW-BTC", "status": "OPEN",
               "entry": 74000, "mark": 74500, "ref_at": "20261001090000"}]
        result = normalize(actual, db)[0]
        self.assertEqual(result["ref_ts"], "20261101090500")
        self.assertEqual(result["entry_price"], 75000)
        self.assertEqual(result["last_price"], 76000)
        self.assertEqual(actual[0]["entry_price"], 75000)  # read-only

    def test_unique_imported_db_id_recovers_missing_snapshot_values(self):
        actual = [{"id": 13, "tf": "30m", "symbol": "KRW-ETH", "entry_price": None}]
        db = [{"id": 13, "tf": "30m", "symbol": "KRW-ETH", "status": "OPEN",
               "ref_at": "20261103103500", "entry": 251000, "mark": 252000}]
        row = normalize(actual, db)[0]
        self.assertEqual(row["ref_ts"], "20261103103500")
        self.assertEqual(row["entry_price"], 251000)
        self.assertEqual(row["last_price"], 252000)

    def test_original_exe_buy_time_is_joined_without_price_overwrite(self):
        compact=[{"id":77,"tf":"5m","market":"KRW-BTC","entry_price":101000}]
        source=[{"id":77,"tf":"5m","market":"KRW-BTC","status":"OPEN",
                 "entry":99000,"entry_at":"2026-11-13T00:30:00+00:00",
                 "first_sold_qty":"0.25","ref_at":"2026-11-13T00:00:00Z"}]
        row=normalize(compact,source)[0]
        self.assertEqual(row["entry_price"],101000)
        self.assertEqual(row["entry_at"],"2026-11-13T00:30:00+00:00")
        self.assertEqual(row["first_sold_qty"],"0.25")
        self.assertEqual(row["ref_ts"],"2026-11-13T00:00:00Z")
        self.assertNotIn("entry_at",compact[0]) # read-only source

    def test_ambiguous_symbol_does_not_borrow_another_positions_price(self):
        actual = [{"tf": "5m", "symbol": "KRW-SOL"}]
        db = [{"id": 1, "tf": "5m", "symbol": "KRW-SOL", "status": "OPEN",
               "entry_price": 1},
              {"id": 2, "tf": "5m", "symbol": "KRW-SOL", "status": "OPEN",
               "entry_price": 999999}]
        row = normalize(actual, db)[0]
        self.assertNotIn("entry_price", row)
        self.assertNotIn("last_price", row)

    def test_zero_or_absent_quote_is_not_a_fabricated_current_price(self):
        row = normalize([{"id": 9, "tf": "day", "symbol": "KRW-XRP",
                          "mark": 0, "entry": ""}], [])[0]
        self.assertNotIn("last_price", row)
        self.assertNotIn("entry_price", row)


if __name__ == "__main__":
    unittest.main()
