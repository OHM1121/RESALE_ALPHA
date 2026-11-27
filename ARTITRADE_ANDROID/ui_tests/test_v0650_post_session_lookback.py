"""0.6.5: POST (16:00-20:00) and PRE (08:00-08:30) reference frames need 3 previous 5m / 2 previous 30m-60m bars of the same session (the vendor's own comment), not 100 / 48."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

try:
    import d_v19_portfolio as pf
    from d_storage import DStore
except Exception:                                      # the vendor engine is on the path in CI (/tmp/artitrade-native) and in the local runner
    pf = None

KST = dt.timezone(dt.timedelta(hours=9))
SENTINEL = RuntimeError("REACHED_SIGNAL_SCORE")


@unittest.skipIf(pf is None, "vendor engine not on the path")
class PostSessionLookback(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = DStore(Path(self.tmp.name))
        self.engine = pf.D19PaperEngine(self.store)
        self.now = {"t": None}
        self.old = (pf.now_kst, pf.signal_score)
        pf.now_kst = lambda: self.now["t"]

        def reached(*a, **k):
            raise SENTINEL
        pf.signal_score = reached
        self.addCleanup(self.restore)

    def restore(self):
        pf.now_kst, pf.signal_score = self.old

    def add_minutes(self, code, first, minutes, volume_of, low=990.0, high=1100.0, venue="KRX"):
        with self.store.lock, self.store.connect() as db:
            for i in range(minutes):
                ts = (first + dt.timedelta(minutes=i)).strftime("%Y%m%d%H%M%S")
                db.execute("INSERT OR IGNORE INTO venue_minute_bars(code,market,venue,ts,open,high,low,close,volume,tick_count,first_seen,last_seen,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (code, "KOSPI", venue, ts, 1000.0, high, low, 1090.0, volume_of(i), 5, ts, ts, "TEST"))

    def run_minute(self, minute, venue="KRX"):
        self.now["t"] = (dt.datetime.strptime(minute, "%Y%m%d%H%M%S") + dt.timedelta(minutes=1, seconds=10)).replace(tzinfo=KST)
        try:
            self.engine.on_extended_minute(minute, venue)
        except RuntimeError as exc:
            return exc is SENTINEL
        return False

    def frames(self, code, count, venue="KRX", period="POST"):
        """`count` completed 5m frames of low volume right before the reference bar are written the way the engine writes them."""
        return count

    def post_case(self, previous):
        """Reference 5m bar 17:00-17:04 (volume 100 per minute) after `previous` five-minute frames of volume 5 per minute from 16:00."""
        day = dt.datetime(2026, 11, 26, 16, 0)
        self.add_minutes("000001", day, previous * 5, lambda i: 1.0)
        ref_start = day + dt.timedelta(minutes=previous * 5)
        self.add_minutes("000001", ref_start, 5, lambda i: 100.0)
        for k in range(previous):                                   # the engine stores each completed frame when its last minute arrives
            last = day + dt.timedelta(minutes=k * 5 + 4)
            self.run_minute(last.strftime("%Y%m%d%H%M%S"))
        return (ref_start + dt.timedelta(minutes=4)).strftime("%Y%m%d%H%M%S")

    def test_three_previous_five_minute_frames_are_enough(self):
        last_minute = self.post_case(previous=3)
        self.assertTrue(self.run_minute(last_minute), "the reference bar must reach the signal scoring with 3 previous POST 5m frames")

    def test_two_previous_five_minute_frames_are_not(self):
        last_minute = self.post_case(previous=2)
        self.assertFalse(self.run_minute(last_minute))

    def test_the_source_has_the_patched_rule(self):
        source = Path(pf.__file__).read_text("utf8")
        self.assertIn("if len(prev)<(3 if tf=='5m' else 2):continue", source)
        self.assertNotIn("reference_volume_lookback(tf) if tf in ('5m','60m') else 2", source)


if __name__ == "__main__":
    unittest.main()
