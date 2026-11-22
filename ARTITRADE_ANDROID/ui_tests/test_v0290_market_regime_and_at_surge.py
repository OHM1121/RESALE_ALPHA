"""0.2.9 regression (owner sheet 11.22, red text): market regime UP/DOWN/SIDEWAYS, MA/MarketTrend
regime sizing and no-new-buy, reference-bar UP-market exception, reference-bar +20 % surge filter."""
import datetime as dt
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import at_surge_filter_v0290 as surge
import market_regime_v0290 as regime
import market_score_size_v0200 as size

KST = dt.timezone(dt.timedelta(hours=9))
PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


def read(name):
    return (PY / name).read_text(encoding="utf-8")


def verified(market, value, now=None):
    now = now or dt.datetime.now(KST)
    regime.set_state_for_test(market, {"status": "VERIFIED", "regime": value,
                                       "fetched_at": now.isoformat()})


class Classify(unittest.TestCase):
    def test_up_down_sideways(self):
        rising = [100 + i for i in range(80)]
        falling = [200 - i for i in range(80)]
        flat = [100 + (i % 2) for i in range(80)]
        self.assertEqual(regime.classify(rising)[0], regime.UP)
        self.assertEqual(regime.classify(falling)[0], regime.DOWN)
        self.assertEqual(regime.classify(flat)[0], regime.SIDEWAYS)

    def test_close_against_trend_is_sideways(self):
        closes = [100 + i for i in range(79)] + [150.0]      # MA up but close dumped below MA20
        self.assertEqual(regime.classify(closes)[0], regime.SIDEWAYS)

    def test_too_few_bars_is_unverified(self):
        self.assertIsNone(regime.classify([100] * (regime.MIN_BARS - 1))[0])


class DailyParsing(unittest.TestCase):
    def test_yahoo_drops_todays_bar_until_the_close(self):
        day1 = dt.datetime(2026, 11, 20, 9, 0, tzinfo=KST)
        day2 = dt.datetime(2026, 11, 23, 9, 0, tzinfo=KST)
        raw = {"chart": {"result": [{"timestamp": [int(day1.timestamp()), int(day2.timestamp())],
                                     "indicators": {"quote": [{"close": [2500.0, 2450.0]}]}}]}}
        during = regime.parse_yahoo_daily(raw, dt.datetime(2026, 11, 23, 10, 0, tzinfo=KST))
        after = regime.parse_yahoo_daily(raw, dt.datetime(2026, 11, 23, 16, 0, tzinfo=KST))
        self.assertEqual(during, [("2026-11-20", 2500.0)])
        self.assertEqual(after[-1], ("2026-11-23", 2450.0))

    def test_upbit_drops_the_forming_day(self):
        rows = [{"candle_date_time_kst": "2026-11-22T09:00:00", "trade_price": 9},
                {"candle_date_time_kst": "2026-11-21T09:00:00", "trade_price": 8}]
        self.assertEqual(regime.parse_upbit_daily(rows, dt.datetime(2026, 11, 22, 15, 0, tzinfo=KST)),
                         [("2026-11-21", 8.0)])
        # 03:00 KST still belongs to the 11-21 Upbit day
        self.assertEqual(regime.parse_upbit_daily(rows[1:], dt.datetime(2026, 11, 22, 3, 0, tzinfo=KST)), [])


class StateLifecycle(unittest.TestCase):
    def tearDown(self):
        regime.set_state_for_test("coin", None)

    def test_refresh_verifies_and_expires(self):
        now = dt.datetime(2026, 11, 22, 15, 0, tzinfo=KST)
        bars = [("2026-%03d" % i, 100.0 + i) for i in range(80)]
        row = regime.refresh("coin", now, fetcher=lambda m, n: bars)
        self.assertEqual(row["regime"], regime.UP)
        self.assertEqual(regime.state("coin", now)["regime"], regime.UP)
        later = now + dt.timedelta(seconds=regime.VALID_SECONDS + 1)
        self.assertEqual(regime.state("coin", later)["status"], "UNVERIFIED")

    def test_failed_refresh_keeps_the_last_verified_value(self):
        now = dt.datetime(2026, 11, 22, 15, 0, tzinfo=KST)
        regime.refresh("coin", now, fetcher=lambda m, n: [("d%d" % i, 200.0 - i) for i in range(80)])

        def boom(m, n):
            raise RuntimeError("offline")
        regime.refresh("coin", now + dt.timedelta(minutes=30), fetcher=boom)
        self.assertEqual(regime.state("coin", now + dt.timedelta(minutes=31))["regime"], regime.DOWN)


class MaTrendTable(unittest.TestCase):
    def tearDown(self):
        regime.set_state_for_test("stock", None)

    def decide(self, value, score):
        if value is None:
            regime.set_state_for_test("stock", None)
        else:
            verified("stock", value)
        return regime.ma_trend_decision("/x", "stock", score=score, score_known=True)

    def test_down_market(self):
        self.assertEqual(self.decide("DOWN", -10.0)["factor"], Decimal("0.5"))
        self.assertEqual(self.decide("DOWN", 4.9)["factor"], Decimal("0.5"))
        self.assertEqual(self.decide("DOWN", -10.01)["factor"], Decimal("0.7"))
        self.assertEqual(self.decide("DOWN", -20.0)["factor"], Decimal("0.7"))
        self.assertEqual(self.decide("DOWN", -20.01)["factor"], Decimal(1))
        self.assertIsNone(self.decide("DOWN", 4.99)["block"])
        self.assertIn("DOWN", self.decide("DOWN", 5.0)["block"])

    def test_sideways_market(self):
        self.assertEqual(self.decide("SIDEWAYS", -10.0)["factor"], Decimal("0.5"))
        self.assertEqual(self.decide("SIDEWAYS", -10.5)["factor"], Decimal(1))
        self.assertIsNone(self.decide("SIDEWAYS", 9.99)["block"])
        self.assertIn("SIDEWAYS", self.decide("SIDEWAYS", 10.0)["block"])

    def test_up_market_has_no_reduction_and_no_block(self):
        for score in (-50.0, -10.0, 0.0, 30.0):
            d = self.decide("UP", score)
            self.assertEqual(d["factor"], Decimal(1))
            self.assertIsNone(d["block"])

    def test_unverified_regime_uses_the_sideways_table(self):
        d = self.decide(None, 12.0)
        self.assertEqual(d["applied"], regime.SIDEWAYS)
        self.assertIsNotNone(d["block"])

    def test_unknown_score_never_reduces_or_blocks(self):
        d = self.decide("DOWN", None)
        self.assertEqual((d["factor"], d["block"]), (Decimal(1), None))


class AtUpMarketException(unittest.TestCase):
    def tearDown(self):
        regime.set_state_for_test("coin", None)

    def test_positive_score_or_up_market(self):
        verified("coin", "DOWN")
        self.assertTrue(regime.at_buy_allowed("/x", "coin", True))
        self.assertFalse(regime.at_buy_allowed("/x", "coin", False))
        verified("coin", "UP")
        self.assertTrue(regime.at_buy_allowed("/x", "coin", False))
        regime.set_state_for_test("coin", None)
        self.assertFalse(regime.at_buy_allowed("/x", "coin", False))

    def test_gate_wiring(self):
        gate = read("at_market_score_gate_v107.py")
        self.assertEqual(gate.count("not buy_allowed(root,"), 3)
        ref = read("reference_1118_runtime.py")
        self.assertEqual(ref.count('_up_market("stock")'), 2)
        self.assertEqual(ref.count('_up_market("coin")'), 1)
        self.assertIn('"score_positive_at_buy":bool(getattr(_BUY_CTX,"positive",True))', ref)


class NewBuyBlockWiring(unittest.TestCase):
    def test_trend_wallet_refuses_new_entry(self):
        self.assertIn("block=size.new_buy_block(self.root,market)\n                    if block:return None,block",
                      read("market_trend_wallet_v054.py"))

    def test_coin_ma_refuses_new_entry(self):
        src = read("ma_v067_coin_runtime.py")
        self.assertIn("block=_new_buy_block(self)", src)
        self.assertIn("MA_REGIME_NO_NEW_BUY_V0290", src)

    def test_stock_ma_clamp_refuses_only_pending_ma_entries(self):
        import ma_v067_stock_runtime as stock
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "ma.sqlite3"
            c = sqlite3.connect(db)
            c.executescript("""CREATE TABLE accounts(tf TEXT PRIMARY KEY,seed INTEGER,cash INTEGER,
                realized INTEGER,max_positions INTEGER);
                CREATE TABLE candidates(id INTEGER PRIMARY KEY,tf TEXT,code TEXT,limit_price INTEGER,status TEXT);
                INSERT INTO accounts VALUES('5m',2000000,2000000,0,10);
                INSERT INTO candidates VALUES(1,'5m','005930',10100,'PENDING');""")
            c.commit(); c.close()
            service = SimpleNamespace(db=db, market_store=SimpleNamespace(root=Path(d)))

            class Book:
                def available(self_, code, venue, now, limit, desired):
                    return (10000, 20, 99, 0.1), "VALID"
            book = Book()
            stock._install_score_size_clamp(book)
            blocked = {"score": 7.0, "factor": Decimal("0.5"), "block": "MARKET_REGIME_X"}
            with patch("market_score_size_v0200.decision", return_value=blocked):
                stock._SIZE_CTX.ctx = (service, "005930")
                try:
                    proof, why = book.available("005930", "KRX", None, 10100, 20)
                    other, _ = book.available("005930", "KRX", None, 9999, 20)   # no pending MA entry at this limit
                finally:
                    stock._SIZE_CTX.ctx = None
                outside, _ = book.available("005930", "KRX", None, 10100, 20)   # other strategies
            self.assertIsNone(proof)
            self.assertEqual(why, "MARKET_REGIME_X")
            self.assertEqual(other[1], 20)
            self.assertEqual(outside[1], 20)

    def test_top_ups_keep_using_the_factor_only(self):
        for name in ("trend_partial_entry_v094.py", "coin_partial_entry_v094.py"):
            self.assertNotIn("new_buy_block", read(name))


class SurgeFilter(unittest.TestCase):
    def test_close_to_close_twenty_percent(self):
        self.assertTrue(surge.violates([100, 120]))
        self.assertFalse(surge.violates([100, 119.9]))

    def test_window_is_ten_bars_including_the_reference(self):
        base = [100.0] * 20
        old = base[:8] + [130.0] + [130.0] * 11     # +30 % bar is the 12th from the end -> outside
        self.assertFalse(surge.violates(old))
        inside = [100.0] * 10 + [125.0] + [125.0] * 9   # +25 % bar is the 10th from the end -> inside
        self.assertTrue(surge.violates(inside))
        ref_surge = [100.0] * 15 + [121.0]
        self.assertTrue(surge.violates(ref_surge))

    def test_stock_check_reads_the_native_bars(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE bars(code TEXT,frame TEXT,ts TEXT,close INTEGER)")
        for i, close in enumerate([1000] * 9 + [1250, 1260, 1270]):
            c.execute("INSERT INTO bars VALUES('005930','30m',?,?)", ("202611200%05d" % i, close))
        ref = "202611200%05d" % 11
        self.assertTrue(surge.stock_check(c, "30m", "005930", ref).startswith(surge.REASON))
        clean = "202611200%05d" % 8
        self.assertIsNone(surge.stock_check(c, "30m", "005930", clean))
        before = surge.UNVERIFIED_COUNT["stock"]
        self.assertIsNone(surge.stock_check(c, "30m", "005930", "29990101000000"))  # unknown never blocks
        self.assertIsNone(surge.stock_check(c, "30m", "005930", None))
        self.assertEqual(surge.UNVERIFIED_COUNT["stock"], before + 2)

    def test_d19_order_resolves_ref_ts_through_the_signal(self):
        src = read("reference_1118_runtime.py")
        self.assertIn('SELECT ref_ts FROM signals WHERE id=?', src)

    def test_coin_reference_record_is_dropped_when_the_window_surged(self):
        start = dt.datetime(2026, 11, 22, tzinfo=dt.timezone.utc)
        bars = [{"start": start + dt.timedelta(minutes=30 * i), "close": Decimal(100)} for i in range(15)]
        bars[12]["close"] = Decimal(125)
        rec = {"ref_at": bars[14]["start"].isoformat()}
        self.assertTrue(surge.coin_violates(bars, rec))
        rec_before = {"ref_at": bars[11]["start"].isoformat()}
        self.assertFalse(surge.coin_violates(bars, rec_before))

    def test_wrapper_leaves_shadow_research_unchanged(self):
        import d_upbit
        surge._INSTALLED = False
        native = d_upbit.ref_features
        start = dt.datetime(2026, 11, 22, tzinfo=dt.timezone.utc)
        bars = [{"start": start + dt.timedelta(minutes=30 * i), "close": Decimal(100 if i < 14 else 130)}
                for i in range(15)]
        rec = {"ref_at": bars[14]["start"].isoformat()}
        try:
            d_upbit.ref_features = lambda *a, **k: dict(rec)
            surge.install()
            self.assertIsNone(d_upbit.ref_features(bars, "30m", "KRW-X", None, None))
            self.assertEqual(d_upbit.ref_features(bars, "30m", "KRW-X", None, None,
                                                  allow_strong_bear=True), rec)
        finally:
            d_upbit.ref_features = native
            surge._INSTALLED = False

    def test_stock_buy_paths_call_the_filter(self):
        ref = read("reference_1118_runtime.py")
        self.assertEqual(ref.count("_surge_reason(c,") - ref.count("def _surge_reason(c,"), 2)


class Installation(unittest.TestCase):
    def test_installed_after_close_feed(self):
        src = read("android_runtime_v078.py")
        self.assertLess(src.index("_install_close_feed_v0270(_core)"),
                        src.index("_start_market_regime_v0290("))
        self.assertIn("_install_at_surge_v0290()", src)


if __name__ == "__main__":
    unittest.main()
