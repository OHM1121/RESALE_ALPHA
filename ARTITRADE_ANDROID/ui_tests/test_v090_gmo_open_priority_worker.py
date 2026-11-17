"""v090 GMO 09:00 official-open recovery must outrank long historical D scans.

The original combined _loop synchronously scans the full stock universe;
a blocked/slow _scan_one stops the next official-open check even if the first
09:00 reply had open_pric=0. This reproduces the head-of-line blocking without
broker auth, real orders, network access, or modifying other strategies.
"""
import base64
import datetime as dt
import io
import itertools
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
PARTS=ROOT/"app/src/main/assets/strategy_v070"

class GmoOpenPriorityRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix="gmo-open-no-starvation-")
        packed=base64.b64decode("".join((PARTS/("part_%02d.txt"%i)).read_text()
                                      for i in range(22)))
        with zipfile.ZipFile(io.BytesIO(packed)) as z:
            assert z.testzip() is None and len(z.namelist())==82
            z.extractall(cls.temp.name)
        sys.path.insert(0,cls.temp.name)
        import d_gmo_runtime as runtime
        cls.runtime=runtime

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(cls.temp.name)
        cls.temp.cleanup()

    def test_official_open_retries_even_when_full_universe_scan_stalls(self):
        g=self.runtime
        service=g.GMOService.__new__(g.GMOService)
        class FastStop:
            def __init__(self):self._e=threading.Event()
            def is_set(self):return self._e.is_set()
            def set(self):self._e.set()
            def wait(self,seconds):return self._e.wait(0.006)
        stop=FastStop()
        service._stop=stop
        service._thread=None
        service._lock=threading.RLock()
        service.root=Path(self.temp.name)
        service._last_overdue_check=0.0
        service._warn_overdue_positions=lambda now: None
        service._log=lambda *args: None
        service._backfill_ohlc=lambda api: None
        service._last_overdue_codes=set()
        entered_scan=threading.Event()
        release_scan=threading.Event()
        tries=[]
        def slow_d_scan(api, code, name, market, target):
            entered_scan.set()
            release_scan.wait(1.0)
        def check_krx_open(api):
            tries.append(time.monotonic())
        service._scan_one=slow_d_scan
        service._fetch_missing_opens=check_krx_open
        class FixedClock(dt.datetime):
            @classmethod
            def now(cls,tz=None):
                return dt.datetime(2026,11,17,9,0,7,tzinfo=g.KST)
        times=itertools.count(start=100)
        fake_time=SimpleNamespace(monotonic=lambda:next(times)*5)
        try:
            with patch.object(g,"datetime",FixedClock), patch.object(g,"systime",fake_time):
                service.start(lambda:object(),
                    lambda:{"012200":("KOSPI","계양전기")})
                self.assertTrue(entered_scan.wait(.55),
                    "Must reproduce a truly blocking D-history scan")
                time.sleep(.18)
                self.assertGreaterEqual(len(tries),2,
                    "KRX D+1 official-open recovery is starved behind slow D scan")
        finally:
            stop.set()
            release_scan.set()
            if service._thread:service._thread.join(.6)
            maintenance=getattr(service,"_maintenance_thread",None)
            if maintenance:maintenance.join(.6)

if __name__=="__main__":
    unittest.main()
