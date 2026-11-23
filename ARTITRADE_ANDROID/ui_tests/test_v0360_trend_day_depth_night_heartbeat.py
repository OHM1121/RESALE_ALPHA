"""0.3.6 regression (issue #151): owner 2026-11-23 + stock error export 15:13 (app 0.3.5).

1. MarketTrend (stock AND coin): the daily-frame RSI<=30 watch lasts 4 days (others stay 24 h).
2. KRX 0D rows whose KRX-specific quantity FID is "0" but whose combined FID 61/71 is positive are read from
   61/71 (AT book + supply-demand book) - the 하림지주 3-of-39 / 지슨 1-share cases.
3. The persisted EWY night value is loaded again after a process restart (10:44:08 GMO order limit 10,240
   instead of the EWY ceiling 9,560, 10 s after a supervisor restart).
4. The engine heartbeat never waits more than 0.5 s for the coin ledger lock.
5. Card facts (target / stop / basis) survive a failed read; GMO shows its first target price.
Synthetic data only."""
import datetime as dt
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
PY = BASE / "app/src/main/python"
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 11, 23, 12, 0, tzinfo=KST)


def read(name):
    return (PY / name).read_text(encoding="utf-8")


class TrendDailyFourDays(unittest.TestCase):
    def setUp(self):
        import market_trend_1118_runtime as mt
        self.mt = mt
        saved = []
        self.me = SimpleNamespace(_nl1118_watches={}, _nl1118_watch_db=SimpleNamespace(
            save=lambda key, row: saved.append((key, dict(row)))))
        self.saved = saved

    def test_hours_per_frame(self):
        import new_legacy_1118_policy as nl
        self.assertEqual(nl.rsi_track_hours("day"), 96)
        for frame in ("30m", "60m", "5m"):
            self.assertEqual(nl.rsi_track_hours(frame), 24)

    def test_new_daily_watch_expires_after_four_days_for_stock_and_coin(self):
        for market, code in (("stock", "005930"), ("coin", "KRW-BTC")):
            key = (market, code, "day")
            row = self.mt._mark_rsi_watch(self.me, market, key, None, 25.0, NOW, evidence={"x": 1})
            self.assertEqual(dt.datetime.fromisoformat(row["expires_at"]), NOW + dt.timedelta(hours=96))

    def test_other_frames_keep_24_hours(self):
        for frame in ("30m", "60m"):
            key = ("stock", "005930", frame)
            row = self.mt._mark_rsi_watch(self.me, "stock", key, None, 25.0, NOW, evidence={"x": 1})
            self.assertEqual(dt.datetime.fromisoformat(row["expires_at"]), NOW + dt.timedelta(hours=24))

    def test_a_daily_watch_armed_under_the_24h_rule_is_extended_once(self):
        key = ("coin", "KRW-ETH", "day")
        old = {"observed_at": NOW.isoformat(), "expires_at": (NOW + dt.timedelta(hours=24)).isoformat(),
               "rsi20": 25.0, "status": "WATCHING", "purchased_at": "", "evidence": {"x": 1}}
        later = NOW + dt.timedelta(hours=30)           # past the old expiry, inside the 4-day window
        row = self.mt._mark_rsi_watch(self.me, "coin", key, old, 28.0, later)
        self.assertEqual(row["status"], "WATCHING")
        self.assertEqual(dt.datetime.fromisoformat(row["expires_at"]), NOW + dt.timedelta(hours=96))
        beyond = NOW + dt.timedelta(hours=97)
        row = self.mt._mark_rsi_watch(self.me, "coin", key, row, 28.0, beyond)
        self.assertEqual(row["status"], "EXPIRED")

    def test_30m_watch_still_expires_after_24h(self):
        key = ("coin", "KRW-ETH", "30m")
        old = {"observed_at": NOW.isoformat(), "expires_at": (NOW + dt.timedelta(hours=24)).isoformat(),
               "rsi20": 25.0, "status": "WATCHING", "purchased_at": "", "evidence": {"x": 1}}
        row = self.mt._mark_rsi_watch(self.me, "coin", key, old, 28.0, NOW + dt.timedelta(hours=25))
        self.assertEqual(row["status"], "EXPIRED")

    def test_basis_text_says_four_days_for_daily(self):
        self.assertIn('"4일" if frame=="day" else "24시간"', read("position_view_v0200.py"))


def kiwoom_message(item, **fids):
    vals = {"9081": "1" if not item.endswith("_NX") else "2"}
    vals.update(fids)
    return {"trnm": "REAL", "data": [{"type": "0D", "item": item, "values": vals}]}


class KrxDepthFallback(unittest.TestCase):
    def test_row_rules(self):
        import krx_depth_fallback_v0360 as f
        vals = {"6044": "0", "61": "+12", "6054": "", "71": "30"}
        self.assertEqual(f.normalize_row(vals, "KRX"), (True, True))
        self.assertEqual((vals["6044"], vals["6054"]), ("+12", "30"))
        kept = {"6044": "5", "61": "12", "6054": "7", "71": "30"}
        self.assertEqual(f.normalize_row(kept, "KRX"), (False, False))
        self.assertEqual((kept["6044"], kept["6054"]), ("5", "7"))
        both_zero = {"6044": "0", "61": "0"}
        self.assertEqual(f.normalize_row(both_zero, "KRX"), (False, False))
        nxt = {"6066": "0", "61": "12"}
        self.assertEqual(f.normalize_row(nxt, "NXT"), (False, False))

    def test_message_only_touches_krx_0d_rows(self):
        import krx_depth_fallback_v0360 as f
        krx = kiwoom_message("003380", **{"41": "9900", "51": "9880", "61": "12", "71": "30", "6044": "0", "6054": "0"})
        nxt = kiwoom_message("003380_NX", **{"41": "9900", "51": "9880", "61": "12", "71": "30", "6066": "0"})
        trade = {"trnm": "REAL", "data": [{"type": "0B", "item": "003380", "values": {"6044": "0", "61": "9"}}]}
        self.assertEqual(f.normalize_message(krx), 1)
        self.assertEqual(krx["data"][0]["values"]["6044"], "12")
        self.assertEqual(f.normalize_message(nxt), 0)
        self.assertEqual(nxt["data"][0]["values"]["6066"], "0")
        self.assertEqual(f.normalize_message(trade), 0)
        self.assertEqual(f.normalize_message("not a message"), 0)

    def test_the_vendor_at_book_now_holds_the_displayed_quantity(self):
        import d_v19_portfolio as vend
        import krx_depth_fallback_v0360 as f
        from d_gmo_runtime import GMOService
        original = (vend.Book.on_message, GMOService.on_book_message, f._INSTALLED)
        live = vend.live_session
        vend.live_session = lambda *a, **k: True
        try:
            at = vend.now_kst()
            msg = lambda: kiwoom_message("003380", **{"41": "9900", "51": "9880", "61": "12", "71": "30",
                                                     "6044": "0", "6054": "0"})
            before = vend.Book()
            self.assertEqual(before.on_message(msg(), None, {"003380": 1}, at), 0)      # vendor drops the row
            self.assertEqual(before.available("003380", "KRX", at, 9930, 20)[0], None)
            f._INSTALLED = False
            f.install(root=None)
            after = vend.Book()
            self.assertEqual(after.on_message(msg(), None, {"003380": 1}, at), 1)
            quote, why = after.available("003380", "KRX", at, 9930, 20)
            self.assertEqual(quote[0], 9900)
            self.assertEqual(quote[1], 6)                       # int(12 x 50 % participation)
            self.assertEqual(why, "VALID_OBSERVED_DEPTH_PAPER_PROXY")
            self.assertEqual(GMOService.on_book_message.__name__, "gmo_on_book_message")
        finally:
            vend.live_session = live
            vend.Book.on_message, GMOService.on_book_message, f._INSTALLED = original

    def test_installed_in_the_runtime_and_samples_are_numeric_only(self):
        self.assertIn("_install_krx_depth_fallback_v0360()", read("android_runtime_v078.py"))
        src = read("krx_depth_fallback_v0360.py")
        for word in ("requests", "urllib", "socket", "order_id", "api_key", "token", "secret"):
            self.assertNotIn(word, src)


class NightValueSurvivesRestart(unittest.TestCase):
    def test_persisted_value_is_loaded_lazily(self):
        import night_futures_gate_v0260 as night
        saved = (dict(night._STATE), night._ROOT, night._LOADED[0])
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "stock").mkdir()
            (Path(d) / "stock" / "night_futures_gate_v0260.json").write_text(json.dumps(
                {"date": "2026-11-23", "status": "VERIFIED", "change_pct": -6.6424, "severe": True,
                 "threshold_pct": -3.0, "source": "YAHOO_EWY_US_SESSION_OPEN_TO_CLOSE"}), encoding="utf-8")
            try:
                night._STATE.clear()
                night._ROOT = d
                night._LOADED[0] = False
                now = dt.datetime(2026, 11, 23, 10, 44, 8, tzinfo=KST)
                self.assertEqual(night.state(now)["status"], "VERIFIED")
                ceiling, info = night.gmo_ceiling(10250, now)
                self.assertEqual(ceiling, 9560)                 # not the open-based 10,240
                self.assertEqual(info["reason"], "EWY_ADJUSTED_D_CLOSE")
            finally:
                night._STATE.clear()
                night._STATE.update(saved[0])
                night._ROOT, night._LOADED[0] = saved[1], saved[2]

    def test_another_days_file_is_not_used(self):
        import night_futures_gate_v0260 as night
        saved = (dict(night._STATE), night._ROOT, night._LOADED[0])
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "stock").mkdir()
            (Path(d) / "stock" / "night_futures_gate_v0260.json").write_text(json.dumps(
                {"date": "2026-11-20", "status": "VERIFIED", "change_pct": -6.0}), encoding="utf-8")
            try:
                night._STATE.clear()
                night._ROOT = d
                night._LOADED[0] = False
                self.assertEqual(night.state(dt.datetime(2026, 11, 23, 10, 0, tzinfo=KST))["status"], "NOT_CHECKED_YET")
            finally:
                night._STATE.clear()
                night._STATE.update(saved[0])
                night._ROOT, night._LOADED[0] = saved[1], saved[2]


class HeartbeatNeverWaitsForTheLedger(unittest.TestCase):
    def test_busy_ledger_lock_returns_the_cached_symbols_fast(self):
        from unified_recovery_v092 import RecoveryObserver
        me = SimpleNamespace()
        ledger = SimpleNamespace(lock=threading.RLock(), watched_codes=lambda: {"KRW-A", "KRW-B"})
        self.assertEqual(RecoveryObserver._bounded_watched(me, ledger, "coin/general"), {"KRW-A", "KRW-B"})
        held, release = threading.Event(), threading.Event()

        def hold():
            with ledger.lock:
                held.set()
                release.wait(5)
        t = threading.Thread(target=hold, daemon=True)
        t.start()
        held.wait(2)
        try:
            started = time.monotonic()
            self.assertEqual(RecoveryObserver._bounded_watched(me, ledger, "coin/general"), {"KRW-A", "KRW-B"})
            self.assertLess(time.monotonic() - started, 1.5)
            other = RecoveryObserver._bounded_watched(me, ledger, "coin/ma")       # never cached: empty, not a wait
            self.assertEqual(other, set())
        finally:
            release.set()
            t.join(2)
        self.assertTrue(ledger.lock.acquire(blocking=False))                       # not left locked
        ledger.lock.release()

    def test_trend_lock_is_tried_not_waited_for(self):
        src = read("unified_recovery_v092.py")
        self.assertIn("trend.lock.acquire(timeout=0.5)", src)
        self.assertNotIn("with trend.lock:", src)
        self.assertNotIn("coin.ledger.watched_codes()", src)


class CardFacts(unittest.TestCase):
    def test_last_good_facts_fill_only_what_is_missing(self):
        import position_view_v0200 as view
        view._FACT_CACHE.clear()
        good = [{"id": 1, "first_profit_target": 6936.0, "stop_loss_price": 6130.0, "stop_loss_pct": -9.9,
                 "buy_basis": "기준봉 ..."}]
        view.remember_facts("stock_at", good)
        bare = [{"id": 1, "first_profit_target": 7470}, {"id": 2}]
        filled = view.restore_facts("stock_at", bare)
        self.assertEqual(bare[0]["first_profit_target"], 7470)            # an existing value is never replaced
        self.assertEqual(bare[0]["stop_loss_price"], 6130.0)
        self.assertEqual(bare[0]["buy_basis"], "기준봉 ...")
        self.assertEqual(filled, 3)
        self.assertNotIn("stop_loss_price", bare[1])
        view._FACT_CACHE.clear()

    def test_wiring(self):
        src = read("android_runtime_v078.py")
        self.assertIn("self._err('stock_at_buy_evidence_v0360',evidence_exc)", src)
        self.assertIn("_view.restore_facts('stock_at',positions)", src)
        self.assertIn("rt._err('position_facts_%s_v0360'%kind,exc)", src)
        self.assertIn("pos['first_profit_target']=int(math.ceil(entry_price*(1+fraction)))", src)


class Version(unittest.TestCase):
    def test_unified_version_is_at_least_0_3_6(self):
        import re
        g = (BASE / "app/build.gradle").read_text(encoding="utf-8")
        m = re.search(r"unified \{\s*dimension 'market'\s*versionCode (\d+)\s*versionName '([0-9.]+)'", g)
        self.assertGreaterEqual(int(m.group(1)), 169)
        self.assertGreaterEqual(tuple(map(int, m.group(2).split("."))), (0, 3, 6))


if __name__ == "__main__":
    unittest.main()
