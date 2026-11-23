"""0.3.2 regression (issue #147): supply-demand D selection is fixed at the D close ranking and the
pending card shows the D+1 buy price (D close x (1 + EWY)). Synthetic data only."""
import contextlib
import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import gmo_d_universe_v0320 as g
import night_futures_gate_v0260 as night

KST = dt.timezone(dt.timedelta(hours=9))
BASE = Path(__file__).resolve().parents[1]
PY = BASE / "app/src/main/python"
JAVA = BASE / "app/src/main/java/com/artitrade/mobile"
D = "2026-11-20"          # Friday
D1 = "2026-11-23"         # next trading day (Monday)


def at(day, h, m=0):
    y, mo, d = map(int, day.split("-"))
    return dt.datetime(y, mo, d, h, m, tzinfo=KST)


class FakeService:
    def __init__(self, root):
        self.root = Path(root)
        self.db_path = self.root / "PROJECTGMO_V1.0_research.sqlite3"
        c = sqlite3.connect(self.db_path)
        c.executescript("""
            CREATE TABLE gmo_scan(trade_date TEXT,code TEXT,status TEXT,detail TEXT,checked_at TEXT,
                selection_version TEXT,PRIMARY KEY(trade_date,code));
            CREATE TABLE gmo_candidates(code TEXT,name TEXT,event_date TEXT,entry_date TEXT,status TEXT,
                saved_at TEXT,opening_price INTEGER,gmo_score INTEGER,first_tp_pct REAL,evidence TEXT);
            CREATE TABLE gmo_issues(category TEXT,code TEXT,detail TEXT);""")
        c.commit(); c.close()
        s = sqlite3.connect(self.root / "ARTITRADE_D.sqlite3")
        s.execute("CREATE TABLE rank_snapshots(ts TEXT,code TEXT,market TEXT,rank INTEGER,subscribed INTEGER,source TEXT)")
        s.commit(); s.close()
        self.engine = SimpleNamespace(signals={}, positions={}, orders={})
        self.dismissed = []
        self.logged = []

    @contextlib.contextmanager
    def _db(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def rank(self, ts, *codes):
        s = sqlite3.connect(self.root / "ARTITRADE_D.sqlite3")
        s.executemany("INSERT INTO rank_snapshots VALUES(?,?,?,?,1,'KIWOOM_KA10032')",
                      [(ts, c, "KOSPI", i + 1) for i, c in enumerate(codes)])
        s.commit(); s.close()

    def scanned(self, code, checked_at):
        with self._db() as c:
            c.execute("INSERT INTO gmo_scan VALUES(?,?,?,?,?,?)",
                      (D.replace("-", ""), code, "COMPLETE", "NO_3PARTY_CANDIDATE", checked_at.isoformat(), "PROJECTGMO V1.2"))

    def candidate(self, code, saved_at, close=10000):
        with self._db() as c:
            c.execute("INSERT INTO gmo_candidates VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (code, "N" + code, D, D1, "후보", saved_at.isoformat(), None, 80, 0.05, "scoring_version"))
        self.engine.signals[code] = SimpleNamespace(code=code, event_date=D, entry_date=D1, event_close=close)

    def dismiss(self, code):
        s = self.engine.signals.pop(code, None)
        if not s:
            return False
        with self._db() as c:
            c.execute("UPDATE gmo_candidates SET status='후보 제외' WHERE code=? AND event_date=?", (code, s.event_date))
        self.dismissed.append(code)
        return True

    def _log(self, category, code="", detail=""):
        self.logged.append((category, code))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.svc = FakeService(self.tmp.name)
        self.svc.rank("20261120143000", "AAA", "MID")          # intraday only
        self.svc.rank("20261120160001", "BBB", "CCC")          # D close ranking
        self.svc.rank("20261120200308", "CCC", "DDD")
        self.svc.rank("20261123100000", "NEW")                  # D+1 intraday ranking

    def tearDown(self):
        with night._LOCK:
            night._STATE.pop(D1, None)
        self.tmp.cleanup()


class SelectionUniverse(Base):
    def test_cutoff_is_next_trading_day_0745(self):
        self.assertEqual(g.selection_cutoff(D, self.svc.root), at(D1, 7, 45))

    def test_close_universe_is_the_ranking_from_1520_on_D_only(self):
        self.assertEqual(g.close_universe(self.svc.root / "ARTITRADE_D.sqlite3", D), {"BBB", "CCC", "DDD"})

    def test_before_cutoff_the_live_ranking_is_still_Ds(self):
        for when in (at(D, 15, 31), at(D, 23, 0), at("2026-11-22", 12, 0), at(D1, 7, 44)):
            self.assertTrue(g.allowed(self.svc, "NEW", D, when)[0], when)

    def test_after_cutoff_only_Ds_close_ranking_or_already_scanned(self):
        late = at(D1, 10, 0)
        self.assertEqual(g.allowed(self.svc, "NEW", D, late), (False, g.SCAN_DETAIL))     # the 11-23 case
        self.assertEqual(g.allowed(self.svc, "AAA", D, late), (False, g.SCAN_DETAIL))     # intraday-only on D
        self.assertTrue(g.allowed(self.svc, "BBB", D, late)[0])
        self.svc.scanned("XYZ", at(D, 16, 0))
        self.assertEqual(g.allowed(self.svc, "XYZ", D, late), (True, "SCANNED_FOR_D_BEFORE_CUTOFF"))

    def test_blocked_scan_is_recorded_and_never_judged(self):
        from d_gmo_runtime import GMOService
        g._INSTALLED = False
        saved = (GMOService._scan_one, GMOService.start, GMOService.snapshot)
        try:
            g.install()
            original_now = g._now
            g._now = lambda: at(D1, 10, 0)
            try:
                self.assertIsNone(GMOService._scan_one(self.svc, None, "NEW", "n", "KOSPI", D))
            finally:
                g._now = original_now
            with self.svc._db() as c:
                row = c.execute("SELECT status,detail FROM gmo_scan WHERE code='NEW'").fetchone()
            self.assertEqual(tuple(row), ("COMPLETE", g.SCAN_DETAIL))
            g._now = lambda: at(D, 16, 0)
            try:
                with self.assertRaises(Exception):          # allowed -> the vendor scan really runs
                    GMOService._scan_one(self.svc, None, "OTHER", "n", "KOSPI", D)
            finally:
                g._now = original_now
        finally:
            GMOService._scan_one, GMOService.start, GMOService.snapshot = saved
            g._INSTALLED = False


class Cleanup(Base):
    def test_late_outside_candidates_are_dismissed_others_kept(self):
        self.svc.candidate("NEW", at(D1, 9, 40))        # the 11-23 case -> dismissed
        self.svc.candidate("BBB", at(D1, 9, 40))        # late but in D close ranking -> kept
        self.svc.candidate("EARLY", at(D, 15, 40))      # registered on D -> kept
        removed = g.cleanup(self.svc, at(D1, 10, 0))
        self.assertEqual(removed, ["NEW"])
        self.assertEqual(self.svc.logged, [(g.DISMISS_LOG, "NEW")])
        self.assertIn("BBB", self.svc.engine.signals)
        self.assertIn("EARLY", self.svc.engine.signals)

    def test_never_touches_a_bought_or_ordered_candidate(self):
        self.svc.candidate("HELD", at(D1, 9, 40))
        self.svc.candidate("ORDERED", at(D1, 9, 40))
        self.svc.engine.positions["HELD"] = object()
        self.svc.engine.orders["1"] = SimpleNamespace(code="ORDERED", side="BUY", state="NEW")
        self.assertEqual(g.cleanup(self.svc, at(D1, 10, 0)), [])


class PendingPrice(Base):
    def test_d1_with_verified_ewy_shows_the_ceiling(self):
        night.set_state_for_test({"date": D1, "status": "VERIFIED", "change_pct": -1.23})
        sig = SimpleNamespace(event_close=10000, entry_date=D1)
        rule = g.buy_rule(sig, at(D1, 9, 5))
        self.assertEqual(rule["gmo_buy_ceiling"], 9870)          # 9,877 floored to the 10-won tick
        self.assertIn("9,870원 이하", rule["gmo_buy_rule"])
        self.assertIn("10,000원", rule["gmo_buy_rule"])
        self.assertIn("-1.23%", rule["gmo_buy_rule"])

    def test_up_night_raises_the_ceiling(self):
        night.set_state_for_test({"date": D1, "status": "VERIFIED", "change_pct": 2.0})
        rule = g.buy_rule(SimpleNamespace(event_close=10000, entry_date=D1), at(D1, 9, 5))
        self.assertEqual(rule["gmo_buy_ceiling"], 10200)

    def test_before_d1_or_unverified_shows_the_rule_and_wait_label(self):
        rule = g.buy_rule(SimpleNamespace(event_close=10000, entry_date=D1), at(D, 18, 0))
        self.assertIsNone(rule["gmo_buy_ceiling"])
        self.assertEqual(rule["gmo_buy_wait_label"], "EWY 확인 대기")
        self.assertIn("10,000원", rule["gmo_buy_rule"])
        night.set_state_for_test({"date": D1, "status": "NOT_CHECKED_YET", "change_pct": None})
        self.assertIsNone(g.buy_rule(SimpleNamespace(event_close=10000, entry_date=D1), at(D1, 7, 0))["gmo_buy_ceiling"])

    def test_enrich_pending_fills_price_time_status_and_reason(self):
        night.set_state_for_test({"date": D1, "status": "VERIFIED", "change_pct": -1.23})
        self.svc.candidate("BBB", at(D, 15, 40))
        out = {"pending": [{"code": "BBB", "gmo": True, "gmo_event_date": D, "limit_won": None,
                            "first_target_won": None, "reason": "기준봉 조건 미적용 · 수급매매 후보"}]}
        g.enrich_pending(self.svc, out, at(D1, 9, 5))
        row = out["pending"][0]
        self.assertEqual(row["limit_price"], 9870)
        self.assertEqual(row["at"], at(D, 15, 40).isoformat())
        self.assertEqual(row["status"], "D+1 매수가 대기")
        self.assertNotIn("기준봉", row["reason"])
        self.assertIn("수급", row["reason"])

    def test_plan_status_and_limit_are_kept(self):
        night.set_state_for_test({"date": D1, "status": "VERIFIED", "change_pct": -1.23})
        self.svc.candidate("BBB", at(D, 15, 40))
        out = {"pending": [{"code": "BBB", "gmo": True, "gmo_event_date": D, "limit_price": 9800,
                            "status": "D+1 가상매수 KRX PREPARED · S0"}]}
        g.enrich_pending(self.svc, out, at(D1, 9, 5))
        self.assertEqual(out["pending"][0]["limit_price"], 9800)
        self.assertTrue(out["pending"][0]["status"].startswith("D+1 가상매수"))


class Wiring(unittest.TestCase):
    def test_installed_last_in_the_runtime(self):
        src = (PY / "android_runtime_v078.py").read_text(encoding="utf-8")
        self.assertLess(src.index("_install_at_surge_v0290()"), src.index("_install_gmo_d_universe_v0320(_core)"))

    def test_card_shows_the_buy_price_and_rule(self):
        src = (JAVA / "UnifiedVisualActivity.java").read_text(encoding="utf-8")
        self.assertIn('formatMaybePrice(r,"limit_price","gmo_buy_ceiling","entry_price","price")', src)
        self.assertIn('r.optString("gmo_buy_wait_label","")', src)
        self.assertIn('actualReason=r.optString("gmo_buy_rule","")+"\\n"+actualReason;', src)


if __name__ == "__main__":
    unittest.main()
