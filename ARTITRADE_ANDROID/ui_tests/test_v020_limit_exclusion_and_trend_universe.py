"""0.0.20 owner decisions (issue #117), synthetic data only.

1. Stock AT orders whose one-share cost is above the per-symbol budget can never
   fill; they are excluded instead of staying "buy pending".
2. Stock MarketTrend uses the SAME universe as the AT strategy; the trading-value
   TOP50 list stays coin-only.
"""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app/src/main/python"))
import stock_limit_exclusion_v020 as lx
import stock_trend_universe_v020 as tu

try:
    import d_v19_portfolio as v19
    from d_storage import DStore, KST
    ENGINE = True
except Exception:  # pragma: no cover
    ENGINE = False

NOW = "20261120101500"


@unittest.skipUnless(ENGINE, "needs the original engine on sys.path")
class LimitExclusion(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="v020-limit-")
        self.addCleanup(tmp.cleanup)
        self.store = DStore(Path(tmp.name) / "stock")
        self.engine = v19.D19PaperEngine(self.store)
        self.n = 0

    def add(self, code, tf, limit, status="PENDING", decision="PENDING"):
        self.n += 1
        with self.store.lock, self.store.connect() as c:
            c.execute("""INSERT INTO signals(event_key,ts,tf,code,name,market,ref_ts,buy_price,target,score,features,decision)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("SIM%d" % self.n, "20261120093000", tf, code, "SIM", "KOSPI", "20261120090000",
                 limit, limit + 100, 60., '{"ranking_engine":"AT_DIVERSE_1_1_ACTIVE"}', decision))
            sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute("INSERT INTO v19_orders(signal_id,code,tf,venue,score,limit_won,since_ts,status) VALUES(?,?,?,?,?,?,?,?)",
                      (sid, code, tf, "KRX", 60., limit, "20261120093000", status))
        return sid

    def state(self, sid):
        with self.store.connect() as c:
            return (c.execute("SELECT status FROM v19_orders WHERE signal_id=?", (sid,)).fetchone()["status"],
                    c.execute("SELECT decision FROM signals WHERE id=?", (sid,)).fetchone()["decision"])

    def sweep(self):
        with self.store.lock, self.store.connect() as c:
            n = lx.sweep(self.engine, c, NOW)
            c.commit()
        return n

    def test_defect_replay_an_order_above_the_budget_is_rejected_on_every_touch(self):
        sid = self.add("990001", "30m", 250_000)
        with patch("d_v19_portfolio.live_session", return_value=True), \
             patch.object(v19, "MAX_SYMBOL_WON_BY_TF", {"30m": 200_000}):
            with self.store.lock, self.store.connect() as c:
                row = c.execute("SELECT o.*,s.* FROM v19_orders o JOIN signals s ON s.id=o.signal_id").fetchone()
                ok, reason = self.engine._buy_limit_depth(
                    c, row, dt.datetime(2026, 11, 20, 10, 15, 5, tzinfo=KST), "KRX", 249_000, 100)
        self.assertFalse(ok)
        self.assertEqual(reason, "BELOW_ONE_SHARE", "structurally unfillable, yet it stayed PENDING")
        self.assertEqual(self.state(sid)[0], "PENDING")

    def test_unaffordable_pending_is_excluded_with_a_reason_event(self):
        with patch.object(v19, "MAX_SYMBOL_WON_BY_TF", {"30m": 200_000, "60m": 250_000, "day": 500_000, "5m": 200_000}):
            high = self.add("990001", "30m", 250_000)
            self.assertEqual(self.sweep(), 1)
            self.assertEqual(self.state(high), (lx.STATUS, "REJECTED"))
            with self.store.connect() as c:
                ev = c.execute("SELECT event,detail FROM v19_events WHERE event=?", (lx.EVENT,)).fetchall()
            self.assertEqual(len(ev), 1)
            self.assertIn("ONE_SHARE_COST_ABOVE_PER_SYMBOL_BUDGET", ev[0]["detail"])
            self.assertIn("no_order_or_fill_created", ev[0]["detail"])

    def test_affordable_orders_and_other_statuses_are_untouched(self):
        with patch.object(v19, "MAX_SYMBOL_WON_BY_TF", {"30m": 200_000, "60m": 250_000, "day": 500_000, "5m": 200_000}):
            cheap = self.add("990002", "30m", 150_000)
            at_cap_plus_fee = self.add("990003", "60m", 249_950)     # +fee still <= 250,000
            bought = self.add("990004", "30m", 900_000, status="BOUGHT", decision="BOUGHT")
            expired = self.add("990005", "30m", 900_000, status="EXPIRED", decision="EXPIRED_REFERENCE")
            self.assertEqual(self.sweep(), 0)
            self.assertEqual(self.state(cheap)[0], "PENDING")
            self.assertEqual(self.state(at_cap_plus_fee)[0], "PENDING")
            self.assertEqual(self.state(bought), ("BOUGHT", "BOUGHT"))
            self.assertEqual(self.state(expired)[0], "EXPIRED")

    def test_the_fee_is_part_of_the_one_share_cost(self):
        with patch.object(v19, "MAX_SYMBOL_WON_BY_TF", {"30m": 200_000, "60m": 250_000, "day": 500_000, "5m": 200_000}):
            exactly_cap = self.add("990006", "30m", 200_000)         # 200,000 + fee > 200,000
            self.assertEqual(self.sweep(), 1)
            self.assertEqual(self.state(exactly_cap)[0], lx.STATUS)

    def test_budget_is_read_at_call_time_so_a_user_change_applies(self):
        sid = self.add("990007", "day", 400_000)
        with patch.object(v19, "MAX_SYMBOL_WON_BY_TF", {"day": 500_000}):
            self.assertEqual(self.sweep(), 0)
        with patch.object(v19, "MAX_SYMBOL_WON_BY_TF", {"day": 300_000}):
            self.assertEqual(self.sweep(), 1)
            self.assertEqual(self.state(sid)[0], lx.STATUS)

    def test_second_sweep_is_a_noop_and_an_excluded_order_is_never_chosen_again(self):
        with patch.object(v19, "MAX_SYMBOL_WON_BY_TF", {"30m": 200_000}):
            sid = self.add("990008", "30m", 300_000)
            self.assertEqual(self.sweep(), 1)
            self.assertEqual(self.sweep(), 0)
            with self.store.lock, self.store.connect() as c:
                self.assertEqual(self.engine._choose_orders(c, "990008", NOW, "KRX"), [])

    def test_installed_wrapper_sweeps_after_the_native_enqueue_and_keeps_its_result(self):
        original = v19.D19PaperEngine._enqueue_signals
        try:
            lx._INSTALLED = False
            sentinel = object()
            v19.D19PaperEngine._enqueue_signals = lambda self, c, now: sentinel
            lx.install()
            with patch.object(v19, "MAX_SYMBOL_WON_BY_TF", {"30m": 200_000}):
                sid = self.add("990009", "30m", 300_000)
                with self.store.lock, self.store.connect() as c:
                    self.assertIs(v19.D19PaperEngine._enqueue_signals(self.engine, c, NOW), sentinel)
                    c.commit()
            self.assertEqual(self.state(sid)[0], lx.STATUS)
        finally:
            v19.D19PaperEngine._enqueue_signals = original
            lx._INSTALLED = False

    def test_a_failing_sweep_never_breaks_order_creation(self):
        original = v19.D19PaperEngine._enqueue_signals
        try:
            lx._INSTALLED = False
            v19.D19PaperEngine._enqueue_signals = lambda self, c, now: "native-ok"
            lx.install()
            with patch.object(lx, "sweep", side_effect=RuntimeError("boom")):
                self.assertEqual(v19.D19PaperEngine._enqueue_signals(self.engine, None, NOW), "native-ok")
        finally:
            v19.D19PaperEngine._enqueue_signals = original
            lx._INSTALLED = False


def coordinator(runner, candidates=None):
    co = SimpleNamespace(rt=SimpleNamespace(stock_runner=runner), lock=threading.RLock(),
                         _candidates={"stock": dict(candidates or {}), "coin": {}},
                         _last_rank={"stock": 0.0, "coin": 0.0}, _last_fetch_error={"stock": "", "coin": ""})
    return co


try:
    import market_trend_runtime_v043 as trend_runtime
    TREND = True
except Exception:  # pragma: no cover
    TREND = False


class UniverseIdentities(unittest.TestCase):
    def test_only_valid_kospi_kosdaq_six_digit_symbols(self):
        runner = SimpleNamespace(krx_market_map={"005930": ("KOSPI", "A"), "000660": ["KOSDAQ", "B"],
                                                 "12345": ("KOSPI", "short"), "abcdef": ("KOSPI", "x"),
                                                 "111111": ("NASDAQ", "f"), "222222": "bad"},
                                 market_map={"999999": ("KOSPI", "ignored")})
        self.assertEqual(tu.universe_identities(runner),
                         {"005930": ("KOSPI", "A"), "000660": ("KOSDAQ", "B")})

    def test_falls_back_to_market_map_and_tolerates_missing_runner(self):
        self.assertEqual(tu.universe_identities(SimpleNamespace(krx_market_map={}, market_map={"005930": ("KOSPI", "A")})),
                         {"005930": ("KOSPI", "A")})
        self.assertEqual(tu.universe_identities(None), {})
        self.assertEqual(tu.universe_identities(SimpleNamespace()), {})


@unittest.skipUnless(TREND, "needs the original engine on sys.path")
class StockUsesTheAtUniverse(unittest.TestCase):
    def setUp(self):
        self.native = trend_runtime.Coordinator._refresh_rank
        self.calls = []
        trend_runtime.Coordinator._refresh_rank = lambda s, m: self.calls.append(m) or "native"
        tu._INSTALLED = False
        tu.install()

    def tearDown(self):
        trend_runtime.Coordinator._refresh_rank = self.native
        tu._INSTALLED = False

    def test_stock_candidates_are_the_at_universe_and_no_ranking_request_is_made(self):
        runner = SimpleNamespace(krx_market_map={"005930": ("KOSPI", "A"), "000660": ("KOSDAQ", "B")})
        co = coordinator(runner)
        trend_runtime.Coordinator._refresh_rank(co, "stock")
        self.assertEqual(co._candidates["stock"], {"005930": ("KOSPI", "A"), "000660": ("KOSDAQ", "B")})
        self.assertEqual(self.calls, [], "the separate TOP50 ranking request is coin-only now")
        self.assertTrue(getattr(co, tu.FLAG))
        self.assertEqual(co._last_fetch_error["stock"], "")

    def test_the_candidate_list_follows_the_universe_when_it_is_rebuilt(self):
        runner = SimpleNamespace(krx_market_map={"005930": ("KOSPI", "A")})
        co = coordinator(runner)
        trend_runtime.Coordinator._refresh_rank(co, "stock")
        runner.krx_market_map = {"000660": ("KOSDAQ", "B")}
        trend_runtime.Coordinator._refresh_rank(co, "stock")
        self.assertEqual(set(co._candidates["stock"]), {"000660"})

    def test_coin_still_uses_the_native_top50(self):
        co = coordinator(SimpleNamespace(krx_market_map={"005930": ("KOSPI", "A")}))
        self.assertEqual(trend_runtime.Coordinator._refresh_rank(co, "coin"), "native")
        self.assertEqual(self.calls, ["coin"])
        self.assertEqual(co._candidates["coin"], {})

    def test_universe_not_ready_keeps_existing_candidates_and_reports_it(self):
        co = coordinator(SimpleNamespace(krx_market_map={}, market_map={}), {"005930": ("KOSPI", "A")})
        trend_runtime.Coordinator._refresh_rank(co, "stock")
        self.assertEqual(set(co._candidates["stock"]), {"005930"})
        self.assertEqual(co._last_fetch_error["stock"], "AT_UNIVERSE_NOT_READY")
        self.assertEqual(self.calls, [])

    def test_install_is_idempotent(self):
        wrapped = trend_runtime.Coordinator._refresh_rank
        tu.install()
        self.assertIs(trend_runtime.Coordinator._refresh_rank, wrapped)


class WiringContract(unittest.TestCase):
    def test_runtime_and_planner_are_wired(self):
        base = Path(__file__).resolve().parents[1] / "app/src/main/python"
        runtime = (base / "android_runtime_v078.py").read_text(encoding="utf8")
        self.assertLess(runtime.index("_install_market_trend_v043(_core)"),
                        runtime.index("_install_stock_trend_universe_v020()"))
        self.assertIn("_install_stock_limit_exclusion_v020()", runtime)
        self.assertNotIn("stock_candidate_universe_v020", runtime)
        planner = (base / "stock_priority_runtime_v093.py").read_text(encoding="utf8")
        self.assertIn("_v020_stock_universe_candidates", planner)


if __name__ == "__main__":
    unittest.main()
