"""0.4.3 (#158): owner workbook 2026-11-27 (pink) - coin +40 / negative-UP / listing blocks, trend + score recorded at the BUY."""
import datetime as dt
import types
import unittest
from pathlib import Path
from unittest import mock

import at_market_score_gate_v107 as gate
import buy_record_v0430 as record
import entry_context_v0370 as ctx
import market_regime_v0290 as regime
import rr_size_v0340 as rr

PY = Path(__file__).resolve().parents[1] / "app/src/main/python"
UP_CLOSES = [100 + i for i in range(80)]                       # close > MA20 > MA60, MA20 rising
DOWN_CLOSES = [200 - i for i in range(80)]


def candles(n, step_minutes, end, base=100.0):
    """Upbit-style rows (newest first); the newest one is still forming when `end` is inside it."""
    rows = []
    for i in range(n):
        start = end - dt.timedelta(minutes=step_minutes * (i + 1)) + dt.timedelta(minutes=step_minutes // 2)
        rows.append({"candle_date_time_utc": start.replace(tzinfo=None).isoformat(timespec="seconds"),
                     "trade_price": base + (n - i)})
    return rows


class Base(unittest.TestCase):
    def setUp(self):
        for store in (record._CLOSES, record._FAIL, record._LISTING):
            store.clear()


class Rules(Base):
    def test_score_block_is_plus_40_for_coin(self):
        self.assertIn("COIN_SCORE_AT_OR_ABOVE_40_V0430", record.coin_score_block("KRW-A", "5m", 40.0))
        self.assertIn("COIN_SCORE_AT_OR_ABOVE_40_V0430", record.coin_score_block("KRW-A", "5m", 85))
        self.assertIsNone(record.coin_score_block("KRW-A", "5m", 39.9))
        self.assertIsNone(record.coin_score_block("KRW-A", "5m", None))
        self.assertIsNone(record.coin_score_block("KRW-A", "5m", "abc"))

    def test_ma_and_trend_decision_blocks_coin_at_40_even_in_an_up_market(self):
        up = lambda s: regime.ma_trend_decision("/x", "coin", score=s, score_known=True, symbol="KRW-A",
                                                frame="5m", closes=UP_CLOSES)
        self.assertEqual(up(39.0)["regime"], regime.UP)
        self.assertIsNone(up(39.0)["block"])
        self.assertIn("COIN_SCORE_AT_OR_ABOVE_40_V0430", up(41.0)["block"])
        side = regime.ma_trend_decision("/x", "coin", score=41.0, score_known=True, symbol="KRW-A", frame="5m",
                                        closes=[100.0] * 80)
        self.assertIsNotNone(side["block"])                                  # the older SIDEWAYS rule still applies
        stock = regime.ma_trend_decision("/x", "stock", score=45.0, score_known=True, symbol="005930", frame="5m",
                                         closes=UP_CLOSES)
        self.assertIsNone(stock["block"])                                    # stock sheet had no pink text

    def _allowed(self, market, score, closes):
        evidence = {"score": score, "positive": bool(score is not None and score > 0),
                    "negative": bool(score is not None and score < 0), "basis": "T"}
        with mock.patch.object(gate, "score_evidence", return_value=evidence), \
             mock.patch.object(regime, "closes_for", return_value=closes):
            return gate.buy_allowed("/x", market, "KRW-A" if market == "coin" else "005930", "5m")

    def test_coin_reference_bar_rules(self):
        self.assertTrue(self._allowed("coin", 12.0, DOWN_CLOSES))            # positive and below 40
        self.assertFalse(self._allowed("coin", 40.0, UP_CLOSES))             # +40 or more
        self.assertFalse(self._allowed("coin", 85.0, UP_CLOSES))
        self.assertFalse(self._allowed("coin", -4.9, UP_CLOSES))             # negative score + UP trend: now blocked
        self.assertFalse(self._allowed("coin", -4.9, DOWN_CLOSES))           # (already blocked before)
        self.assertTrue(self._allowed("coin", None, UP_CLOSES))              # unknown score: the older UP rule decides
        self.assertFalse(self._allowed("coin", None, DOWN_CLOSES))
        self.assertTrue(self._allowed("stock", -4.9, UP_CLOSES))             # stock reference bar unchanged
        self.assertFalse(self._allowed("stock", -4.9, DOWN_CLOSES))

    def test_listing_age(self):
        now = dt.datetime(2026, 11, 27, tzinfo=dt.timezone.utc)
        young = [{"candle_date_time_utc": (now - dt.timedelta(days=i + 1)).replace(tzinfo=None).isoformat(timespec="seconds"),
                  "trade_price": 1} for i in range(40)]
        with mock.patch.object(record, "_fetch", return_value=young):
            self.assertIn("COIN_LISTED_UNDER_3_MONTHS_V0430", record.coin_listing_block("KRW-NEW", now))
        old = [{"candle_date_time_utc": (now - dt.timedelta(days=i + 1)).replace(tzinfo=None).isoformat(timespec="seconds"),
                "trade_price": 1} for i in range(200)]
        calls = []
        with mock.patch.object(record, "_fetch", side_effect=lambda url: calls.append(url) or old):
            self.assertIsNone(record.coin_listing_block("KRW-OLD", now))
            self.assertIsNone(record.coin_listing_block("KRW-OLD", now))
        self.assertEqual(len(calls), 1)                                      # a passed check is remembered
        with mock.patch.object(record, "_fetch", side_effect=OSError("down")):
            self.assertIn("COIN_LISTING_AGE_UNVERIFIED_V0430", record.coin_listing_block("KRW-UNK", now))

    def test_rest_closes_only_completed_candles_and_cached_per_bar(self):
        now = dt.datetime(2026, 11, 27, 3, 2, tzinfo=dt.timezone.utc)
        forming = {"candle_date_time_utc": "2026-11-27T03:00:00", "trade_price": 999.0}
        done = [{"candle_date_time_utc": (dt.datetime(2026, 11, 27, 2, 55) - dt.timedelta(minutes=5 * i)).isoformat(
            timespec="seconds"), "trade_price": 100.0 + i} for i in range(130)]
        calls = []
        with mock.patch.object(record, "_fetch", side_effect=lambda url: calls.append(url) or [forming] + done):
            closes, why = record.rest_closes("KRW-A", "5m", now)
            again, _ = record.rest_closes("KRW-A", "5m", now)
        self.assertIsNone(why)
        self.assertEqual(len(closes), 130)
        self.assertNotIn(999.0, closes)                                      # the forming candle is excluded
        self.assertEqual(closes, again)
        self.assertEqual(len(calls), 1)
        with mock.patch.object(record, "_fetch", side_effect=OSError("down")):
            self.assertIsNone(record.rest_closes("KRW-B", "5m", now)[0])
            self.assertEqual(record.rest_closes("KRW-B", "5m", now)[1], "REST_CLOSES_RECENT_FAILURE")
        self.assertEqual(record.rest_closes("BTC", "5m", now)[1], "REST_CLOSES_UNSUPPORTED")

    def test_regime_uses_rest_only_when_the_app_holds_fewer_than_65_bars(self):
        with mock.patch.object(regime, "_coin_cached", return_value=None), \
             mock.patch.object(regime, "_trend_history", return_value=None), \
             mock.patch.object(record, "rest_closes", return_value=(UP_CLOSES, None)) as rest:
            self.assertEqual(regime.symbol_regime("coin", "KRW-A", "30m")[0], regime.UP)
            self.assertEqual(rest.call_count, 1)
        with mock.patch.object(regime, "_coin_cached", return_value=UP_CLOSES), \
             mock.patch.object(record, "rest_closes") as rest:
            self.assertEqual(regime.symbol_regime("coin", "KRW-A", "30m")[0], regime.UP)
            rest.assert_not_called()
        with mock.patch.object(regime, "_coin_cached", return_value=None), \
             mock.patch.object(regime, "_trend_history", return_value=None), \
             mock.patch.object(record, "rest_closes", return_value=(None, "REST_CLOSES_OSError")):
            self.assertIsNone(regime.symbol_regime("coin", "KRW-A", "30m")[0])   # still honestly unverified


class Record(Base):
    def test_entry_ctx_carries_trend_and_score(self):
        with mock.patch.object(record, "score_snapshot", return_value={"score": 12.5, "basis": "T", "stale": False}):
            built = ctx.build(150.0, UP_CLOSES, {"ratio": 1.2}, None, "coin")
        self.assertEqual(built["trend"], "UP")
        self.assertEqual(built["market_score"]["score"], 12.5)
        short = ctx.build(150.0, UP_CLOSES[:20], None, None, None)
        self.assertEqual(short["trend"], "UNVERIFIED")
        self.assertEqual(short["trend_bars"], 20)
        self.assertNotIn("market_score", short)

    def test_missing_coin_bars_are_read_from_the_candle_endpoint_for_the_record(self):
        info = rr.evaluate(None, None, None)
        with mock.patch.object(record, "rest_closes", return_value=(UP_CLOSES + [200.0] * 60, None)), \
             mock.patch.object(record, "score_snapshot", return_value={"score": -3.0, "basis": "T", "stale": False}):
            out = rr._finish(info, 150.0, lambda: (None, "ENTRY_CTX_NO_NATIVE_HISTORY"), "coin", "KRW-A", "5m")
        self.assertGreaterEqual(out["entry_ctx"]["bars"], 130)
        self.assertNotEqual(out["entry_ctx"]["trend"], "UNVERIFIED")
        with mock.patch.object(record, "rest_closes", return_value=(None, "REST_CLOSES_OSError")), \
             mock.patch.object(record, "score_snapshot", return_value={"score": None, "basis": "X"}):
            out = rr._finish(rr.evaluate(None, None, None), 150.0, lambda: (None, "ENTRY_CTX_NO_NATIVE_HISTORY"),
                             "coin", "KRW-A", "5m")
        self.assertEqual(out["entry_ctx"]["trend"], "UNVERIFIED")
        self.assertEqual(out["entry_ctx"]["reason"], "ENTRY_CTX_NO_NATIVE_HISTORY")

    def test_score_snapshot_keeps_the_last_known_score_when_the_live_one_is_stale(self):
        row = {"score": None, "stale": True, "asof": None,
               "recent_scores": [{"ts": 1.0, "score": 3.0, "band": "x"}, {"ts": 2.0, "score": 31.5, "band": "y"}]}
        worker = types.SimpleNamespace(snapshot=lambda: {"stock": row})
        evidence = {"score": None, "positive": False, "negative": False, "basis": "STALE_SCORE_NO_DECISION"}
        with mock.patch.object(gate, "score_evidence", return_value=evidence), \
             mock.patch("market_research_v033._WORKERS", {"/r": worker}), \
             mock.patch.object(gate, "_score_worker_root", return_value="/r"):
            got = record.score_snapshot("stock", "/r")
        self.assertEqual(got["score"], 31.5)
        self.assertTrue(got["stale"])
        self.assertEqual(got["basis"], "LAST_KNOWN_SCORE_STALE_AT_BUY")
        self.assertEqual(got["asof"], 2.0)
        live = {"score": 12.0, "positive": True, "negative": False, "basis": "SIGNED_MARKET_SCORE_V033"}
        with mock.patch.object(gate, "score_evidence", return_value=live), \
             mock.patch("market_research_v033._WORKERS", {"/r": worker}), \
             mock.patch.object(gate, "_score_worker_root", return_value="/r"):
            got = record.score_snapshot("stock", "/r")
        self.assertEqual((got["score"], got["stale"]), (12.0, False))

    def test_hooks_are_wired(self):
        text = (PY / "ma_v067_coin_runtime.py").read_text("utf8")
        self.assertIn("record.coin_listing_block(symbol)", text)
        self.assertIn("if not block and order is not None", text)       # NEW entries only, never top-ups
        self.assertIn("coin_reference_allowed", (PY / "at_market_score_gate_v107.py").read_text("utf8"))
        self.assertIn("coin_score_block", (PY / "market_regime_v0290.py").read_text("utf8"))


if __name__ == "__main__":
    unittest.main()
