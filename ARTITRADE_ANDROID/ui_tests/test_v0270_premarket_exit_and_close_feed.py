"""0.2.7 regression: pre-emptive exits from 08:00 (NXT pre-market), NXT bid only for
the automatic exit, MarketTrend wallet accepts the automatic reason, close-betting
feed coalescing (issue #142)."""
import datetime as dt
import queue
import time
import unittest
from pathlib import Path

import close_feed_coalesce_v0270 as feed
import night_futures_gate_v0260 as night

KST = dt.timezone(dt.timedelta(hours=9))
PY = Path(__file__).resolve().parents[1] / "app/src/main/python"


def t(h, m, s=0, day=23):
    return dt.datetime(2026, 11, day, h, m, s, tzinfo=KST)


class ExitVenue(unittest.TestCase):
    def test_nxt_from_eight_then_krx_from_the_open(self):
        self.assertEqual(night.exit_venue(t(8, 0, 1)), "NXT")
        self.assertEqual(night.exit_venue(t(8, 29, 59)), "NXT")
        self.assertIsNone(night.exit_venue(t(8, 40)))
        self.assertEqual(night.exit_venue(t(9, 0, 1)), "KRX")
        self.assertEqual(night.exit_venue(t(9, 29, 59)), "KRX")
        self.assertIsNone(night.exit_venue(t(9, 30, 0)))

    def test_decision_happens_before_eight(self):
        self.assertEqual(night.PREMARKET_OPEN, dt.time(8, 0))
        self.assertEqual(night.PREEMPT_DECIDE_BEFORE_OPEN_SECONDS, 300)

    def test_requests_carry_the_venue_and_automatic_reason(self):
        src = (PY / "night_futures_gate_v0260.py").read_text(encoding="utf-8")
        self.assertIn('"auto_reason": PREEMPT_REASON, "venue": venue', src)


class NxtOnlyForAutomaticExit(unittest.TestCase):
    def test_user_sell_can_never_use_the_nxt_book(self):
        import stock_manual_v086 as manual
        with self.assertRaises(ValueError) as ctx:
            manual._bid_for(object(), {"venue": "NXT", "code": "005930",
                                       "request_id": "12345678-1234-5678-1234-567812345678"})
        self.assertEqual(str(ctx.exception), "NXT_MANUAL_EXIT_NOT_AUTHORIZED")

    def test_nxt_depth_reserves_on_its_own_venue_key(self):
        import threading
        import stock_manual_v086 as manual

        class Book:
            def __init__(self):
                self._lock = threading.RLock()
                self._bid_depth = {("005930", "NXT"): (70000, 10, None)}
                self._bid_reserved = {}
        now = t(8, 0, 5)
        book = Book()
        book._bid_depth[("005930", "NXT")] = (70000, 10, now)
        depth = manual.VerifiedDepth("005930", 70000, 10, now, book, venue="NXT")
        # Native sell owners pass the literal 'KRX'; it maps to this NXT depth.
        self.assertEqual(depth.available_sell("005930", "KRX", now, 70000, 4), 4)
        self.assertTrue(depth.reserve_sell("005930", "KRX", now, 70000, 4))
        self.assertEqual(book._bid_reserved, {("005930", "NXT"): 4})
        self.assertEqual(depth.available_sell("005930", "KRX", now, 70000, 10), 6)

    def test_manual_at_ma_trend_use_the_venue_aware_bid(self):
        src = (PY / "stock_manual_v086.py").read_text(encoding="utf-8")
        for fn in ("def manual_stock_at", "def manual_stock_ma", "def manual_stock_trend"):
            i = src.index(fn); j = src.index("\ndef ", i + 5)
            self.assertIn("book=_bid_for(rt,request)", src[i:j])


class TrendWalletAcceptsAutomaticReason(unittest.TestCase):
    def test_prefixes(self):
        import market_trend_wallet_v054 as wallet
        self.assertIn("USER_SIM_MANUAL:", wallet.MANUAL_REASON_PREFIXES)
        self.assertIn("AUTO_NIGHT_EWY_PREEMPTIVE_STOP:", wallet.MANUAL_REASON_PREFIXES)
        src = (PY / "market_trend_wallet_v054.py").read_text(encoding="utf-8")
        self.assertEqual(src.count("startswith(MANUAL_REASON_PREFIXES)"), 2)
        self.assertNotIn("startswith('USER_SIM_MANUAL:')", src)


def real(code, typ, value):
    return {"trnm": "REAL", "data": [{"item": code, "type": typ, "values": {"10": value}}]}


class CloseFeedCoalescing(unittest.TestCase):
    def test_backlog_keeps_only_newest_row_per_symbol_and_type(self):
        q = feed.CoalescingCloseQueue(maxsize=4096)
        base = time.monotonic()
        for i in range(5000):                     # far more than 4,096 messages
            q.put_nowait((base + i * 1e-4, real("005930", "0D", i)))
        q.put_nowait((base + 1.0, real("000660", "0B", 7)))
        self.assertEqual(q.qsize(), 2)
        arrived, msg = q.get_nowait()
        self.assertEqual(msg["data"][0]["values"]["10"], 4999)   # newest, not the oldest
        self.assertAlmostEqual(arrived, base + 4999 * 1e-4)
        self.assertEqual(q.get_nowait()[1]["data"][0]["item"], "000660")
        self.assertEqual(q.stats["queue_full"], 0)
        self.assertEqual(q.stats["coalesced"], 4999)

    def test_control_messages_pass_unchanged_and_empty_raises(self):
        q = feed.CoalescingCloseQueue()
        ping = {"trnm": "REG", "return_code": "0"}
        q.put_nowait((1.0, ping))
        self.assertEqual(q.get_nowait(), (1.0, ping))
        with self.assertRaises(queue.Empty):
            q.get(timeout=0.01)

    def test_installed_after_runtime_overlays(self):
        src = (PY / "android_runtime_v078.py").read_text(encoding="utf-8")
        self.assertLess(src.index("_install_stock_coverage_v0260(_core)"),
                        src.index("_install_close_feed_v0270(_core)"))


if __name__ == "__main__":
    unittest.main()
