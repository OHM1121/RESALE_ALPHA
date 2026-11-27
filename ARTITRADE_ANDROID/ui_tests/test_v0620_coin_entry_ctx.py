"""0.6.2: BUY-time MA / trend / market score of COIN positions completed after the fact from official Upbit candles; stock positions get the last recorded score with its age."""
import datetime as dt
import json
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path

import entry_ctx_backfill_v0600 as base
import entry_ctx_backfill_v0620 as coin

KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc


def kst(y, mo, d, h, mi=0, s=0):
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=KST)


class Client:
    """Upbit stand-in: newest first, candle_date_time_utc strictly before `to` (like the real API), price rises 1 per candle."""

    def __init__(self, fail=False, empty=False):
        self.calls, self.fail, self.empty = [], fail, empty

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.fail:
            raise ConnectionError("no route")
        if self.empty:
            return []
        step = {"/v1/candles/minutes/5": 300, "/v1/candles/minutes/30": 1800, "/v1/candles/minutes/60": 3600, "/v1/candles/days": 86400}[path]
        to = dt.datetime.strptime(params["to"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        rows = []
        for i in range(1, params["count"] + 1):
            start = to - dt.timedelta(seconds=step * i)
            rows.append({"candle_date_time_utc": start.strftime("%Y-%m-%dT%H:%M:%S"), "trade_price": 1000.0 - i, "market": params["market"]})
        return rows


class Boundary(unittest.TestCase):
    def test_boundary_is_the_frame_start_at_or_before_the_buy(self):
        self.assertEqual(coin.boundary(kst(2026, 11, 21, 9, 8, 35), "30m"), dt.datetime(2026, 11, 21, 0, 0, tzinfo=UTC))
        self.assertEqual(coin.boundary(kst(2026, 11, 21, 9, 38, 0), "30m"), dt.datetime(2026, 11, 21, 0, 30, tzinfo=UTC))
        self.assertEqual(coin.boundary(kst(2026, 11, 21, 9, 8, 35), "5m"), dt.datetime(2026, 11, 21, 0, 5, tzinfo=UTC))
        self.assertEqual(coin.boundary(kst(2026, 11, 21, 15, 0, 0), "day"), dt.datetime(2026, 11, 21, 0, 0, tzinfo=UTC))

    def test_closes_are_completed_only_oldest_first_and_at_most_140(self):
        client = Client()
        buy = kst(2026, 11, 21, 9, 8, 35)
        closes = coin.completed_closes(client, "KRW-BARD", "30m", buy, sleep=lambda s: None)
        self.assertEqual(len(closes), 140)
        self.assertEqual(closes[-1], 999.0)                     # the newest completed candle (start 08:30 KST, end 09:00) is last
        self.assertGreater(closes[-1], closes[0])
        path, params = client.calls[0]
        self.assertEqual((path, params["count"], params["to"]), ("/v1/candles/minutes/30", 200, "2026-11-21T00:00:00Z"))

    def test_a_candle_that_ends_after_the_buy_is_dropped(self):
        class Late(Client):
            def get(self, path, params=None):
                rows = super().get(path, params)
                rows.insert(0, {"candle_date_time_utc": "2026-11-21T00:00:00", "trade_price": 5.0, "market": "x"})   # starts 09:00 KST, ends 09:30 > buy 09:08
                return rows
        closes = coin.completed_closes(Late(), "KRW-BARD", "30m", kst(2026, 11, 21, 9, 8, 35), sleep=lambda s: None)
        self.assertNotIn(5.0, closes)

    def test_same_market_frame_boundary_is_requested_once(self):
        client, cache = Client(), {}
        a = coin.completed_closes(client, "KRW-A", "30m", kst(2026, 11, 21, 9, 8, 35), cache=cache, sleep=lambda s: None)
        b = coin.completed_closes(client, "KRW-A", "30m", kst(2026, 11, 21, 9, 21, 0), cache=cache, sleep=lambda s: None)
        self.assertEqual((a, len(client.calls)), (b, 1))

    def test_unknown_frame_and_errors_raise(self):
        with self.assertRaises(ValueError):
            coin.completed_closes(Client(), "KRW-A", "15m", kst(2026, 11, 21, 9), sleep=lambda s: None)
        with self.assertRaises(ConnectionError):
            coin.completed_closes(Client(fail=True), "KRW-A", "5m", kst(2026, 11, 21, 9), sleep=lambda s: None)


class ScoreBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def book(self, rows, max_age):
        path = self.root / "scores.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE IF EXISTS s")
            db.execute("CREATE TABLE s(ts INTEGER, score REAL)")
            db.executemany("INSERT INTO s VALUES(?,?)", rows)
        return base.ScoreBook(path, "SELECT ts,score FROM s ORDER BY ts", max_age)

    def test_last_recorded_score_with_its_age_and_the_age_limit(self):
        buy = kst(2026, 11, 26, 17, 0)
        t = int(kst(2026, 11, 26, 15, 59).timestamp())
        book = self.book([(t - 600, -3.0), (t, -4.5)], 20 * 3600)
        got = book.at(buy)
        self.assertEqual((got["score"], got["age_s"], got["basis"]), (-4.5, 3660, "LAST_RECORDED_BEFORE_BUY"))
        self.assertIsNone(self.book([(t, -4.5)], 1800).at(buy))                 # older than the limit: not used
        self.assertIsNone(self.book([(t + 7200, 1.0)], 3600).at(buy))           # only a later score exists: never from the future

    def test_a_missing_database_is_an_empty_book(self):
        self.assertIsNone(base.ScoreBook(self.root / "none.sqlite3", "SELECT 1,1", 60).at(kst(2026, 11, 26, 9)))

    def test_the_strategys_own_stored_score_wins(self):
        fn = base.score_fn_for(None)
        self.assertEqual(fn(kst(2026, 11, 26, 9), {"market_score_at_buy": -15.0})["score"], -15.0)
        self.assertIsNone(fn(kst(2026, 11, 26, 9), {"score_positive_at_buy": True}))     # a boolean is not a score


class PlanRR(unittest.TestCase):
    def test_reference_bar_rr_from_the_stored_plan_matches_the_recorded_formula(self):
        got = coin.plan_rr({"entry": 131.0, "stop": 122.0, "first": 134.93}, 131.0)
        self.assertEqual((got["gain_pct"], got["loss_pct"], got["stop_source"]), (3.0, 6.8702, "REFERENCE_HARD_STOP"))
        self.assertAlmostEqual(got["ratio"], 0.4367, places=3)
        self.assertEqual(got["reason"], "RECOMPUTED_FROM_STORED_PLAN")

    def test_missing_or_inconsistent_plans_give_no_ratio_and_a_reason(self):
        self.assertEqual(coin.plan_rr({"entry": 131.0}, 131.0), {"reason": "NO_RR_RECORDED_AT_BUY"})
        self.assertEqual(coin.plan_rr({"entry": 131.0, "stop": 140.0, "first": 134.0}, 131.0), {"reason": "NO_RR_PLAN_INCONSISTENT"})
        self.assertEqual(coin.rr_fn_for("MA")({"entry": 1}, 1), {"reason": "NO_RR_RECORDED_AT_BUY"})

    def test_the_recorded_rr_is_never_replaced_by_the_recomputed_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            sc = {"entry": 131.0, "stop": 122.0, "first": 134.93, "rr_size": {"ratio": 9.9, "gain_pct": 1.0, "loss_pct": 0.1, "stop_source": "X"}}
            report = base._blank()
            written = {}
            base._process("T", [{"id": 1, "code": "KRW-A", "frame": "30m", "at": "2026-11-21T09:08:35+09:00", "entry": "131", "sidecar": json.dumps(sc)}], None,
                          lambda pid, text: written.update({pid: text}), kst(2026, 11, 27, 21), tmp, report, closes_fn=lambda c, f, m: [100.0 + i for i in range(130)],
                          rr_fn=coin.plan_rr, log_fn=lambda *a: None)
            self.assertEqual(json.loads(written[1])["entry_ctx"]["rr"]["ratio"], 9.9)


class Better(unittest.TestCase):
    def test_a_score_alone_replaces_an_equal_context_but_never_a_richer_one(self):
        with_score = {"bars": 100, "market_score": {"score": 1.0}}
        no_score = {"bars": 100, "market_score": {"score": None}}
        self.assertTrue(base.better(no_score, with_score))
        self.assertFalse(base.better(with_score, no_score))
        self.assertFalse(base.better({"bars": 120, "market_score": {"score": None}}, with_score))     # fewer bars: keep the old one
        self.assertTrue(base.better(None, no_score))
        self.assertTrue(base.better({"bars": 10}, {"bars": 11, "market_score": {"score": None}}))


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "coin" / "base").mkdir(parents=True)
        (self.root / "coin" / "ma_epoch_v067").mkdir(parents=True)
        (self.root / "coin" / "trend").mkdir(parents=True)
        (self.root / "coin" / "ai").mkdir(parents=True)
        with sqlite3.connect(self.root / "coin" / "base" / "upbit_paper.sqlite3") as db:
            db.executescript("""CREATE TABLE positions(id INTEGER PRIMARY KEY, market TEXT, tf TEXT, entry_at TEXT, entry TEXT);
                CREATE TABLE nl1118_reference_positions(position_id INTEGER PRIMARY KEY, intent_json TEXT, updated_at TEXT);""")
            db.execute("INSERT INTO positions VALUES(16,'KRW-BARD','30m','2026-11-21T09:08:35.505006+09:00','131')")
            db.execute("INSERT INTO nl1118_reference_positions VALUES(16,?,NULL)", (json.dumps({"entry": 131.0, "score_positive_at_buy": True, "rr_size": {"ratio": 1.2, "gain_pct": 3.0, "loss_pct": 2.5, "stop_source": "REF_LOW"}}),))
            db.execute("INSERT INTO positions VALUES(17,'KRW-NOSIDE','30m','2026-11-21T09:10:00+09:00','10')")
            db.execute("INSERT INTO positions VALUES(18,'KRW-NOTIME','30m','','10')")
            db.execute("INSERT INTO nl1118_reference_positions VALUES(18,?,NULL)", (json.dumps({"entry": 10.0}),))
        with sqlite3.connect(self.root / "coin" / "ma_epoch_v067" / "upbit_paper.sqlite3") as db:
            db.executescript("""CREATE TABLE positions(id INTEGER PRIMARY KEY, market TEXT, tf TEXT, entry_at TEXT, entry TEXT);
                CREATE TABLE ma_v067_positions(position_id INTEGER PRIMARY KEY, state_json TEXT, updated_at TEXT);""")
            db.execute("INSERT INTO positions VALUES(50,'KRW-PIEVERSE','5m','2026-11-21T00:34:57.339492+09:00','3574')")
            db.execute("INSERT INTO ma_v067_positions VALUES(50,?,NULL)", (json.dumps({"market_score_at_buy": -15.0, "seen_above_entry": True}),))
        with sqlite3.connect(self.root / "coin" / "trend" / "market_trend_wallet_v054.sqlite3") as db:
            db.executescript("CREATE TABLE positions(id INTEGER PRIMARY KEY, symbol TEXT, tf TEXT, entry_at TEXT, entry REAL, payload TEXT, updated_at TEXT);")
            db.execute("INSERT INTO positions VALUES(17,'KRW-NEAR','day','2026-11-21T07:30:34+09:00',3955.0,?,NULL)", (json.dumps({"entry": 3955.0, "market_score_at_buy": -24.8}),))
        t = int(kst(2026, 11, 21, 9, 5).timestamp())
        with sqlite3.connect(self.root / "coin" / "ai" / "AI_RESEARCH_V040.sqlite3") as db:
            db.execute("CREATE TABLE market_score_observations(score_ts INTEGER PRIMARY KEY, score REAL NOT NULL, source TEXT NOT NULL)")
            db.execute("INSERT INTO market_score_observations VALUES(?,?,?)", (t, 0.5, "x"))
        self.runtime = types.SimpleNamespace(root=str(self.root))

    def sidecar(self, owner):
        path, sql = {"BASE": ("coin/base/upbit_paper.sqlite3", "SELECT intent_json FROM nl1118_reference_positions WHERE position_id=16"),
                     "MA": ("coin/ma_epoch_v067/upbit_paper.sqlite3", "SELECT state_json FROM ma_v067_positions WHERE position_id=50"),
                     "TREND": ("coin/trend/market_trend_wallet_v054.sqlite3", "SELECT payload FROM positions WHERE id=17")}[owner]
        with sqlite3.connect(self.root / path) as db:
            return json.loads(db.execute(sql).fetchone()[0])

    def test_all_three_coin_owners_get_the_context_and_the_score(self):
        client = Client()
        report = coin.run(self.runtime, now=kst(2026, 11, 27, 21), client=client, sleep=lambda s: None)
        base_ctx = self.sidecar("BASE")["entry_ctx"]
        self.assertEqual(base_ctx["bars"], 140)
        self.assertEqual(set(base_ctx["ma"]), {"5", "10", "20", "60", "120"})
        self.assertEqual(base_ctx["ma"]["5"]["entry_vs_ma"], "BELOW")           # entry 131 vs a MA around 995
        self.assertEqual(base_ctx["trend"], "UP")
        self.assertEqual(base_ctx["market_score"]["basis"], "LAST_RECORDED_BEFORE_BUY")
        self.assertEqual(base_ctx["market_score"]["score"], 0.5)
        self.assertEqual(base_ctx["rr"]["ratio"], 1.2)                          # copied from the sidecar, never recomputed
        self.assertEqual(base_ctx["backfill"]["source"], "UPBIT_PUBLIC_CANDLES")
        self.assertEqual(self.sidecar("MA")["entry_ctx"]["market_score"], {"score": -15.0, "basis": "STORED_AT_BUY_BY_STRATEGY", "age_s": 0})
        self.assertEqual(self.sidecar("TREND")["entry_ctx"]["market_score"]["score"], -24.8)
        self.assertEqual(self.sidecar("MA")["seen_above_entry"], True)          # nothing else in the sidecar changes
        self.assertEqual(report["sources"]["BASE"]["filled"], 1)
        self.assertEqual(report["requests"], len(client.calls))

    def test_a_second_run_asks_nothing_again(self):
        coin.run(self.runtime, now=kst(2026, 11, 27, 21), client=Client(), sleep=lambda s: None)
        client = Client()
        report = coin.run(self.runtime, now=kst(2026, 11, 27, 22), client=client, sleep=lambda s: None)
        self.assertEqual(len(client.calls), 0)
        self.assertEqual(report["sources"]["BASE"]["filled"], 0)

    def test_unresolved_positions_are_listed_with_their_reason(self):
        report = coin.run(self.runtime, now=kst(2026, 11, 27, 21), client=Client(), sleep=lambda s: None)
        reasons = {(u["id"], u["reason"]) for u in report["sources"]["BASE"]["unresolved"]}
        self.assertIn((17, "NO_SIDECAR_ROW"), reasons)
        self.assertIn((18, "NO_ENTRY_TIME"), reasons)
        saved = json.loads((self.root / "coin" / coin.STATE_FILE).read_text("utf8"))
        self.assertEqual(saved["sources"]["BASE"]["unresolved"], report["sources"]["BASE"]["unresolved"])

    def test_a_request_error_leaves_the_sidecar_unchanged_and_is_listed(self):
        report = coin.run(self.runtime, now=kst(2026, 11, 27, 21), client=Client(fail=True), sleep=lambda s: None)
        self.assertNotIn("entry_ctx", self.sidecar("BASE"))
        self.assertTrue(any(u["reason"].startswith("BARS_UNAVAILABLE_ConnectionError") for u in report["sources"]["BASE"]["unresolved"]))
        # and the next round tries again
        coin.run(self.runtime, now=kst(2026, 11, 27, 22), client=Client(), sleep=lambda s: None)
        self.assertIn("entry_ctx", self.sidecar("BASE"))

    def test_no_candles_before_the_buy_is_reported_not_guessed(self):
        report = coin.run(self.runtime, now=kst(2026, 11, 27, 21), client=Client(empty=True), sleep=lambda s: None)
        self.assertNotIn("entry_ctx", self.sidecar("BASE"))
        self.assertIn("NO_COMPLETED_BARS_BEFORE_BUY", {u["reason"] for u in report["sources"]["BASE"]["unresolved"]})

    def test_the_request_budget_is_respected(self):
        old = coin.MAX_REQUESTS
        coin.MAX_REQUESTS = 1
        try:
            client = Client()
            coin.run(self.runtime, now=kst(2026, 11, 27, 21), client=client, sleep=lambda s: None)
            self.assertEqual(len(client.calls), 1)
        finally:
            coin.MAX_REQUESTS = old

    def test_pace_and_log(self):
        slept = []
        coin.run(self.runtime, now=kst(2026, 11, 27, 21), client=Client(), sleep=slept.append)
        self.assertTrue(slept and all(s == coin.PAUSE for s in slept))
        lines = (self.root / "coin" / coin.LOG_FILE).read_text("utf8").splitlines()
        self.assertEqual(len(lines), 3)
        self.assertFalse((self.root / "stock" / base.LOG_FILE).exists())

    def test_installed_in_the_quiet_hours_loop(self):
        import inspect
        import offhours_prework_v0530 as off
        self.assertIn("entry_ctx_backfill_v0620", inspect.getsource(off._loop))


class StockScore(unittest.TestCase):
    def test_a_stock_after_hours_buy_gets_the_last_recorded_score_with_its_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stock").mkdir()
            with sqlite3.connect(root / "market_research_v033.sqlite3") as db:
                db.execute("CREATE TABLE score_events(market TEXT NOT NULL, ts INTEGER NOT NULL, score REAL NOT NULL, band TEXT NOT NULL, meta TEXT NOT NULL, PRIMARY KEY(market,ts))")
                db.execute("INSERT INTO score_events VALUES('stock',?,?, 'x','{}')", (int(kst(2026, 11, 26, 15, 59).timestamp()), -81.9))
                db.execute("INSERT INTO score_events VALUES('coin',?,?, 'x','{}')", (int(kst(2026, 11, 26, 16, 30).timestamp()), 7.0))
            book = base.ScoreBook(root / "market_research_v033.sqlite3", base.STOCK_SCORE_SQL, base.STOCK_SCORE_MAX_AGE)
            got = base.score_fn_for(book)(kst(2026, 11, 26, 16, 0, 38), {})
            self.assertEqual((got["score"], got["basis"]), (-81.9, "LAST_RECORDED_BEFORE_BUY"))
            self.assertEqual(got["age_s"], 98)                                   # the coin score is not used for a stock


if __name__ == "__main__":
    unittest.main()
