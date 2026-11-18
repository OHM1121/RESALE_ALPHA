"""Read-only NEW LEGACY 11.18 AI provenance regression.

Use synthetic *in-memory/on-temp* SQLite only. No broker, APK, original
device ledger, remote MSERVER, or actual order is touched.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import trade_decision_basis_v095 as basis


KST = dt.timezone(dt.timedelta(hours=9))
WHEN = "2026-11-18T10:20:00+09:00"
EPOCH = dt.datetime.fromisoformat(WHEN).timestamp()


class NewLegacy1118AiBasis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def _db(self, subpath, ddl):
        file = self.root / subpath
        file.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(file))
        connection.executescript(ddl)
        return connection

    def test_ma_all_new_stage_stop_and_expiry_reasons_are_explicit(self):
        labels = {
            "MA_V1118_UP_FIRST_30PCT": ("1차", "30%"),
            "MA_V1118_UP_SECOND_35PCT": ("2차", "35%"),
            "MA_V1118_UP_AFTER_SECOND_TRAIL": ("3차", "잔량 전량"),
            "MA_V1118_STOP_MINUS_15": ("손절", "-15%"),
            "MA_V1118_HOLD_EXPIRY": ("보유기한", "48/80/120/360"),
        }
        for reason, words in labels.items():
            with self.subTest(reason=reason):
                result = basis._ma_exit(reason, "30m", {}, {"price": 103})
                self.assertIn("NEW LEGACY 11.18", result)
                self.assertTrue(all(word in result for word in words), result)
                self.assertIn("실제 SELL 체결가 103", result)
                self.assertNotIn("식별자", result)

    def test_trend_watch_entry_and_all_new_protection_reasons(self):
        payload = json.dumps({
            "rsi20_live": 29.4,
            "entry_signal_policy": "NL1118_RSI20_24H_BOLL_20_1_5",
            "bar_key": "2026-11-18T10:00:00+09:00",
        })
        text, source, evidence = basis._trend_entry(
            {"payload": payload},
            {"reason": "MARKET_TREND_RSI20_BUY"},
            "30m",
        )
        self.assertIn("24시간", text)
        self.assertIn("볼린저밴드(20,1.5)", text)
        self.assertIn("29.4", text)
        self.assertEqual(source, "ORIGINAL_MT54_RSI20_EXECUTION")
        self.assertEqual(evidence["rsi20_live"], 29.4)
        exits = {
            "MT1118_STOP_15PCT": "-15%",
            "MT1118_HOLD_DEADLINE": "100시간/8일/15일",
            "NL1118_AFTER_FIRST_PLUS_1_STOP": "+1%",
            "NL1118_AFTER_SECOND_FIRST_FLOOR": "첫 목표가",
            "NL1118_AFTER_SECOND_TRAIL": "-4/-5/-6%",
            "NL1118_PRE_FIRST_RETURN_TO_ENTRY": "첫 목표가 미달",
        }
        for reason, phrase in exits.items():
            with self.subTest(reason=reason):
                actual = basis._trend_exit(
                    reason, {"payload": payload}, {"price": 89}, "30m"
                )
                self.assertIn("NEW LEGACY 11.18", actual)
                self.assertIn(phrase, actual)

    def test_exact_verified_stock_ma_execution_only(self):
        with self._db(
            "stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
            """CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,code TEXT);
               CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,
                 side TEXT,reason TEXT,price REAL,ts TEXT);""",
        ) as db:
            db.execute("INSERT INTO positions VALUES(101,'30m','005930')")
            db.execute("INSERT INTO positions VALUES(102,'30m','000660')")
            db.execute(
                "INSERT INTO executions VALUES(7,101,'SELL',"
                "'MA_V1118_UP_FIRST_30PCT',103,?)", (WHEN,)
            )
            db.execute(
                "INSERT INTO executions VALUES(8,102,'SELL',"
                "'MA_V1118_STOP_MINUS_15',85,?)", (WHEN,)
            )
        exact = basis.from_original(
            self.root, "stock", "ma", "30m", "005930", "101",
            "SELL", execution_id="7", at=EPOCH,
        )
        self.assertIn("NEW LEGACY 11.18 1차 매도",
                      exact["verified_trade_basis"])
        self.assertEqual(exact["verified_original_execution_id"], "7")
        self.assertEqual(
            basis.from_original(self.root,"stock","ma","30m","000660","101",
                                "SELL",execution_id="7",at=EPOCH), {}
        )
        self.assertEqual(
            basis.from_original(self.root,"stock","ma","30m","005930","101",
                                "SELL",execution_id="8",at=EPOCH), {}
        )
        self.assertEqual(
            basis.from_original(self.root,"stock","ma","30m","005930","101",
                                "SELL",execution_id="7",at=EPOCH+3600), {}
        )

    def test_exact_verified_coin_trend_buy_and_sell_after_restart(self):
        payload = json.dumps({
            "rsi20_live": 29.25,
            "entry_signal_policy": "NL1118_RSI20_24H_BOLL_20_1_5",
        })
        with self._db(
            "coin/trend/market_trend_wallet_v054.sqlite3",
            """CREATE TABLE positions(id INTEGER PRIMARY KEY,tf TEXT,symbol TEXT,
                 payload TEXT);
               CREATE TABLE executions(id INTEGER PRIMARY KEY,position_id INTEGER,
                 side TEXT,reason TEXT,price REAL,at TEXT);""",
        ) as db:
            db.execute("INSERT INTO positions VALUES(501,'60m','KRW-TEST',?)",
                       (payload,))
            db.execute(
                "INSERT INTO executions VALUES(19,501,'BUY',"
                "'MARKET_TREND_RSI20_BUY',101,?)", (WHEN,)
            )
            db.execute(
                "INSERT INTO executions VALUES(20,501,'SELL',"
                "'MT1118_STOP_15PCT',85,?)", (WHEN,)
            )
        buy = basis.from_original(
            self.root,"coin","trend","60m","KRW-TEST","501",
            "BUY",execution_id="19",at=EPOCH,
        )
        self.assertIn("볼린저밴드(20,1.5)", buy["verified_trade_basis"])
        self.assertIn("24시간", buy["verified_trade_basis"])
        sell = basis.from_original(
            self.root,"coin","trend","60m","KRW-TEST","501",
            "SELL",execution_id="20",at=EPOCH,
        )
        self.assertIn("-15%", sell["verified_trade_basis"])
        self.assertEqual(
            basis.from_original(self.root,"coin","trend","60m","KRW-OTHER",
                                "501","BUY",execution_id="19",at=EPOCH), {}
        )
        summary = basis.summary_for_ai(self.root,"coin","trend")
        self.assertEqual(summary["verified_buy_count"],1)
        self.assertEqual(summary["verified_sell_count"],1)
        self.assertEqual(summary["unmatched_source_fills"],0)

    def test_old_trend_position_must_not_be_relabelled_as_new(self):
        old_payload = json.dumps({"rsi20_live":28.0})
        text, source, details = basis._trend_entry(
            {"payload":old_payload},
            {"reason":"MARKET_TREND_RSI20_BUY"},
            "30m",
        )
        self.assertIn("기존 기록", text)
        self.assertNotIn("볼린저밴드(20,1.5)", text)
        self.assertEqual(source,"ORIGINAL_MT54_RSI20_EXECUTION")
        self.assertEqual(details["rsi20_live"],28.0)


if __name__ == "__main__":
    unittest.main()
