"""0.3.7 regression (owner 2026-11-26 + stock error export 11:50, app 0.3.6).

1. Reference-bar hard stop = BUY price minus the reference bar's high-low range (replaces low - range).
2. Stock MA never bought: the legacy ma_v061 tick hook retired EVERY v067 candidate as
   MA_V061_BAD_OPEN_WINDOW (132 of 132 in the export) because v067 candidates have no ma_v061_open_at.
3. Stock MarketTrend: the overnight gap no longer blinds the first bar of a session, and a qualified
   closed-bar signal that is dropped before the BUY is recorded with its reason.
4. MarketTrend never shows the word "기준봉" (it is "전환봉").
5. Every BUY evaluation carries the RR and the MA 5/10/20/60/120 slopes (entry_ctx) for the position id.
Synthetic data only."""
import datetime as dt
import json
import sqlite3
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

import entry_context_v0370 as ctx
import new_legacy_1118_policy as nl
import rr_size_v0340 as rr

BASE = Path(__file__).resolve().parents[1]
PY = BASE / "app/src/main/python"
JAVA = BASE / "app/src/main/java/com/artitrade/mobile"
KST = dt.timezone(dt.timedelta(hours=9))


def read(name):
    return (PY / name).read_text(encoding="utf-8")


class ReferenceStop(unittest.TestCase):
    def test_stop_is_buy_price_minus_bar_range(self):
        self.assertEqual(nl.reference_stop(100, 90, 95), 95.0)      # range 5
        self.assertEqual(nl.reference_stop(92, 90, 100), 82.0)      # filled above the low
        self.assertEqual(nl.reference_stop(88, 90, 100), 78.0)      # filled below the low
        self.assertEqual(nl.reference_stop(5, 90, 100), 0.0)        # never negative

    def test_targets_use_the_new_stop_for_reference_only(self):
        plan = nl.targets(100, "30m", "reference", ref_low=90, ref_high=95)
        self.assertEqual(plan["stop"], 95.0)
        self.assertEqual(nl.targets(100, "30m", "ma")["stop"], 85.0)
        self.assertEqual(nl.at_hard_stop(90, 100), 80.0)             # old helper kept for history

    def test_unverified_bar_or_entry_is_refused(self):
        with self.assertRaises(ValueError):
            nl.reference_stop(100, 100, 90)
        with self.assertRaises(ValueError):
            nl.reference_stop(0, 90, 95)

    def test_exit_fires_at_the_new_stop(self):
        t = nl.targets(100, "30m", "reference", ref_low=90, ref_high=95)
        t.update({"qty0": 100, "qty": 100, "market": "coin", "stage": 0, "stage1_filled": 0.,
                  "stage2_filled": 0., "seen_above_entry": False, "score_positive_at_buy": True})
        now = dt.datetime(2026, 11, 26, 10, 0, tzinfo=KST)
        self.assertIsNone(nl.exit_signal(t, 95.5, now))
        self.assertEqual(nl.exit_signal(t, 95.0, now)[0], "NL1118_REFERENCE_HARD_STOP")


class StockMaHook(unittest.TestCase):
    def test_hook_skips_v067_candidates(self):
        src = read("ma_v061_stock_runtime.py")
        self.assertIn("""features NOT LIKE '%"ma_v067_kind"%'""", src)

    def test_query_keeps_legacy_candidates_and_skips_v067(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE candidates(id INTEGER PRIMARY KEY, code TEXT, status TEXT, features TEXT)")
        legacy = json.dumps({"ma_v061_kind": "TURNPOINT", "ma_v061_open_at": "x"})
        v067 = json.dumps({"ma_v061_kind": "TURNPOINT", "ma_v067_kind": "REVERSAL_UP"})
        c.execute("INSERT INTO candidates VALUES(1,'005930','PENDING',?)", (legacy,))
        c.execute("INSERT INTO candidates VALUES(2,'005930','PENDING',?)", (v067,))
        rows = c.execute("""SELECT id FROM candidates WHERE code=? AND status='PENDING' AND
            features LIKE '%"ma_v061_kind": "TURNPOINT"%' AND
            features NOT LIKE '%"ma_v067_kind"%'""", ("005930",)).fetchall()
        c.close()
        self.assertEqual([r[0] for r in rows], [1])


class TrendFixes(unittest.TestCase):
    def _coordinator(self, rows):
        import market_trend_runtime_v043 as m
        fake = SimpleNamespace(lock=threading.RLock(), _history={("stock", "000990", "30m"): rows})
        return m.Coordinator._closed_series, fake

    def _bars(self, last_end):
        out = []
        end = last_end
        for _ in range(30):
            out.append({"end": end, "close": 100.0})
            end -= dt.timedelta(minutes=30)
        return sorted(out, key=lambda r: r["end"])

    def test_first_bar_of_session_accepts_previous_session_last_bar(self):
        fn, fake = self._coordinator(self._bars(dt.datetime(2026, 11, 23, 15, 30, tzinfo=KST)))
        got = fn(fake, "stock", "000990", "30m", dt.datetime(2026, 11, 24, 9, 30, tzinfo=KST))
        self.assertGreaterEqual(len(got), 21)

    def test_later_bar_with_a_real_gap_is_still_missing(self):
        fn, fake = self._coordinator(self._bars(dt.datetime(2026, 11, 23, 15, 30, tzinfo=KST)))
        self.assertEqual(fn(fake, "stock", "000990", "30m", dt.datetime(2026, 11, 24, 11, 0, tzinfo=KST)), [])

    def test_dropped_signal_is_recorded_with_its_reason(self):
        import market_trend_1118_runtime as r
        seen = []
        fake = SimpleNamespace(_record_entry_block=lambda *a, **k: seen.append(a))
        signal = ("30m", 28.5, {"signal_bar_end": "2026-11-24T09:30:00+09:00"}, {})
        r._signal_blocked(fake, "stock", "000990", signal, "INSUFFICIENT_FRESH_BEST_ASK")
        self.assertEqual(seen, [("stock", "000990", "30m", "2026-11-24T09:30:00+09:00",
                                 "INSUFFICIENT_FRESH_BEST_ASK", 28.5)])
        src = read("market_trend_1118_runtime.py")
        for reason in ("NL1118_SIGNAL_QUOTE_UNVERIFIED", "NL1118_SIGNAL_QUOTE_CHANGED_BEFORE_BUY",
                       "NL1118_BUY_EXCEPTION_", "str(result)[:80]"):
            self.assertIn(reason, src)


class TrendWording(unittest.TestCase):
    def test_trend_history_uses_jeonhwanbong(self):
        act = (JAVA / "UnifiedVisualActivity.java").read_text(encoding="utf-8")
        self.assertIn('"전환봉 발생 / 매수일"', act)
        self.assertIn('"전환봉·매수일"', act)
        self.assertIn('text.replace("기준봉","전환봉")', act)
        self.assertEqual(act.count("trendWord("), 4)       # definition + 3 call sites
        sel = (JAVA / "HistorySelection.java").read_text(encoding="utf-8")
        self.assertIn('"trend".equals(strategy))return "마켓트렌드 · "', sel)
        for name in ("market_trend_runtime_v043.py", "market_trend_1118_runtime.py",
                     "market_trend_wallet_v054.py", "market_trend_v043.py"):
            self.assertNotIn("기준봉", read(name))


class EntryContext(unittest.TestCase):
    def test_ma_slopes_and_position_for_all_five_periods(self):
        closes = [100 + i for i in range(130)]
        out = ctx.build(250, closes, {"ratio": 0.8, "gain_pct": 1.2, "loss_pct": 1.5, "stop_source": "S"})
        self.assertEqual(set(out["ma"]), {"5", "10", "20", "60", "120"})
        for n in (5, 10, 20, 60, 120):
            row = out["ma"][str(n)]
            self.assertGreater(row["slope_pct"], 0)
            self.assertIn(row["entry_vs_ma"], ("ABOVE", "BELOW"))
        self.assertEqual(out["ma"]["5"]["entry_vs_ma"], "ABOVE")
        self.assertEqual(out["ma"]["120"]["entry_vs_ma"], "ABOVE")
        self.assertEqual(out["rr"]["ratio"], 0.8)
        self.assertEqual(out["bars"], 130)

    def test_not_enough_bars_is_missing_not_guessed(self):
        out = ctx.build(100, [100.0] * 30, None, "ENTRY_CTX_X")
        self.assertIsNone(out["ma"]["60"]["slope_pct"])
        self.assertIsNone(out["ma"]["120"]["ma"])
        self.assertIsNotNone(out["ma"]["20"]["slope_pct"])
        self.assertEqual(ctx.build(100, [], None, "WHY")["reason"], "WHY")

    def test_record_writes_entry_ctx_next_to_rr_size(self):
        info = ctx.attach(rr.evaluate(100, 102, 97), 100, [100.0] * 130)
        target = rr.record({}, info)
        self.assertIn("rr_size", target)
        self.assertEqual(target["entry_ctx"]["version"], "ENTRY_CTX_V0370")

    def test_evaluators_return_context_even_when_rr_is_unverified(self):
        self.assertEqual(rr._finish(rr.evaluate(None, None, None), 100, lambda: (None, "NO"))["entry_ctx"]["reason"], "NO")
        self.assertEqual(rr._finish(rr.evaluate(100, 102, 97), 100, lambda: ([100.0] * 130, None))["entry_ctx"]["bars"], 130)

    def test_gmo_is_not_touched(self):
        for name in ("gmo_d_universe_v0320.py",):
            self.assertNotIn("entry_context_v0370", read(name))


if __name__ == "__main__":
    unittest.main()
