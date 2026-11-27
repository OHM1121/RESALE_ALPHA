"""0.6.5: a not-allowed IP / no network / request limit pauses the after-close work only for 5 minutes, says why, and does not burn the stock's attempts; the card explains the wait during the after-hours trading."""
import datetime as dt
import time
import unittest

import offhours_prework_v0530 as base
import offhours_weekend_v0590 as wk
from test_v0590_offhours_weekend import Base as WeekendBase, FRI, SAT, at

KST = dt.timezone(dt.timedelta(hours=9))


class Classify(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(base.classify_error(RuntimeError("키움 시장 데이터 조회 실패: ka10080 · [8050:허용되지 않은 IP]")), "AUTH")
        self.assertEqual(base.classify_error(RuntimeError("HTTP 401 Unauthorized")), "AUTH")
        self.assertEqual(base.classify_error(RuntimeError("429 Too Many Requests")), "RATE")
        self.assertEqual(base.classify_error(ConnectionError("no route")), "NETWORK")
        self.assertEqual(base.classify_error(TimeoutError("x")), "NETWORK")
        self.assertEqual(base.classify_error(ValueError("bad row")), "OTHER")

    def test_the_pause_text_says_why_and_how_long(self):
        base._STATE["last_error"] = "AUTH"
        text = base.paused_text("주말 데이터 점검", time.monotonic() + 290)
        self.assertEqual(text, "주말 데이터 점검 잠시 중지 · 키움 허용 IP·인증 확인 · 5분 뒤 재시도")
        base._STATE["last_error"] = ""
        self.assertEqual(base.paused_text("장마감 후 데이터 수집", time.monotonic() + 10), "장마감 후 데이터 수집 잠시 중지 · 1분 뒤 재시도")

    def test_the_pause_is_five_minutes(self):
        self.assertEqual(base.PAUSE_SECONDS, 300.0)


class Rejecting:
    def __init__(self):
        self.calls = 0

    def post(self, *a, **k):
        self.calls += 1
        raise RuntimeError("키움 시장 데이터 조회 실패: ka10080 · [8050:허용되지 않은 IP]")


class Weekend(WeekendBase):
    def test_a_rejected_ip_pauses_five_minutes_burns_no_attempt_and_lists_nothing_as_unresolved(self):
        for code in ("000001", "000002", "000003", "000004"):
            self.put(code, "5m", wk.expected_slots(FRI, "5m")[0])
        api = Rejecting()
        before = time.monotonic()
        wk.run_audit(self.runtime(api), at(SAT, 2), sleep=lambda s: None)
        task = wk.load_state(str(self.root))[FRI.isoformat()]
        self.assertEqual(task["attempts"], {})
        self.assertEqual(task["unresolved"], [])
        self.assertFalse(task.get("finished"))
        self.assertGreater(wk._STATE["paused_until"], before + 200)
        self.assertLess(wk._STATE["paused_until"], time.monotonic() + 301)
        self.assertEqual(base._STATE["last_error"], "AUTH")
        self.assertIn("키움 허용 IP·인증 확인", wk.status_line(str(self.root), FRI, at(SAT, 2))["label"])
        log = (self.root / "stock" / wk.LOG_FILE).read_text("utf8")
        self.assertIn('"kind": "AUTH"', log)

    def test_after_the_pause_the_work_goes_on_and_finishes(self):
        for code in ("000001", "000002"):
            self.put(code, "5m", wk.expected_slots(FRI, "5m")[0])
        wk.run_audit(self.runtime(Rejecting()), at(SAT, 2), sleep=lambda s: None)
        wk._STATE.update(paused_until=0.0, next_round=0.0, failures=0)
        from test_v0590_offhours_weekend import Api
        out = wk.run_audit(self.runtime(Api()), at(SAT, 2, 10), sleep=lambda s: None)
        self.assertTrue(out["ran"])
        self.assertTrue(wk.load_state(str(self.root))[FRI.isoformat()]["finished"])


class Card(unittest.TestCase):
    def test_during_the_after_hours_trading_the_card_says_why_nothing_runs(self):
        out = base.status("/nonexistent", at(FRI, 17, 0))
        self.assertEqual(out["phase"], "waiting")
        self.assertIn("20:05", out["label"])
        self.assertEqual(base.status("/nonexistent", at(FRI, 11, 0)), {})
        self.assertEqual(base.status("/nonexistent", at(FRI, 15, 45))["phase"], "waiting")

    def test_the_loop_starts_after_one_minute(self):
        import inspect
        self.assertIn("time.sleep(60.0)", inspect.getsource(base._loop))


if __name__ == "__main__":
    unittest.main()
