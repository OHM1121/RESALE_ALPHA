"""0.6.6: the volume history of a POST (16:00-20:00) / PRE (08:00-08:30) reference is the stock's ONE series (regular bars + earlier extended frames); the extended frames are also written to `bars`."""
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

    def regular_bars(self, count, code="000001"):
        """`count` completed regular-session 5m bars before 16:00 of 2026-11-26 (volume 1 each)."""
        start = dt.datetime(2026, 11, 25, 9, 0)
        with self.store.lock, self.store.connect() as db:
            for i in range(count):
                ts = (start + dt.timedelta(minutes=5 * i)).strftime("%Y%m%d%H%M%S")
                db.execute("INSERT OR IGNORE INTO bars(code,market,frame,ts,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?,?,?)", (code, "KOSPI", "5m", ts, 1000.0, 1000.0, 1000.0, 1000.0, 1.0, "LIVE_WS_0B"))

    def post_case(self, regular, post):
        """`regular` regular 5m bars + `post` earlier POST frames (volume 1 per minute), then a reference 5m bar of volume 100 per minute."""
        self.regular_bars(regular)
        day = dt.datetime(2026, 11, 26, 16, 0)
        self.add_minutes("000001", day, post * 5, lambda i: 1.0)
        ref_start = day + dt.timedelta(minutes=post * 5)
        self.add_minutes("000001", ref_start, 5, lambda i: 100.0)
        for k in range(post):
            last = day + dt.timedelta(minutes=k * 5 + 4)
            self.run_minute(last.strftime("%Y%m%d%H%M%S"))
        return (ref_start + dt.timedelta(minutes=4)).strftime("%Y%m%d%H%M%S")

    def test_the_regular_session_counts_as_history_of_the_after_hours_reference(self):
        last_minute = self.post_case(regular=98, post=2)                   # 98 regular + 2 earlier POST frames = the required 100 previous 5m bars
        self.assertTrue(self.run_minute(last_minute), "KRX and NXT are one market: the reference reaches the signal scoring")

    def test_one_bar_fewer_is_still_not_enough(self):
        last_minute = self.post_case(regular=97, post=2)                   # 99 previous bars
        self.assertFalse(self.run_minute(last_minute))

    def test_the_after_hours_session_alone_is_not_the_only_history(self):
        last_minute = self.post_case(regular=0, post=10)                   # the old behaviour: only the session itself: far below 100
        self.assertFalse(self.run_minute(last_minute))

    def test_completed_after_hours_frames_are_also_written_to_the_continuous_bar_table(self):
        self.post_case(regular=100, post=2)
        with self.store.connect() as db:
            rows = db.execute("SELECT frame,ts,volume,source FROM bars WHERE code='000001' AND source LIKE 'LIVE_EXT_%' ORDER BY ts").fetchall()
        self.assertEqual([(r["frame"], r["ts"]) for r in rows[:2]], [("5m", "20261126160000"), ("5m", "20261126160500")])
        self.assertEqual(rows[0]["source"], "LIVE_EXT_KRX_POST")
        self.assertEqual(rows[0]["volume"], 5.0)

    def test_the_extended_bars_are_not_counted_twice_in_the_history(self):
        last_minute = self.post_case(regular=98, post=2)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM bars WHERE source LIKE 'LIVE_EXT_%'").fetchone()[0], 2)   # inserted, yet the history query uses each frame once
        self.assertTrue(self.run_minute(last_minute))

    def test_the_source_has_the_patched_rule(self):
        source = Path(pf.__file__).read_text("utf8")
        self.assertIn("SELECT ts,open,high,low,close,volume FROM v19_venue_frames WHERE code=? AND frame=? AND ts<?)", source)
        self.assertIn("source NOT LIKE '%EXT_%'", source)
        self.assertIn("'LIVE_EXT_'+venue+'_'+period", source)
        self.assertIn("if len(prev)<(reference_volume_lookback(tf) if tf in ('5m','60m') else 2):continue", source)   # the required count is unchanged


if __name__ == "__main__":
    unittest.main()
