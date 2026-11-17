"""GMO D=2026-11-16 D+1=2026-11-17 opening BUY regression.

Test pure original PROJECTGMO PAPER logic only, never place a real broker order.
The previously archived D-day closing alternative must NOT exclude D+1 open.
"""
from __future__ import annotations
import base64
import io
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ASSET=ROOT/"app/src/main/assets/strategy_v070"

class GmoNov17OpenIncludesPrice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix="gmo-nov17-open-")
        packed=base64.b64decode("".join((ASSET/("part_%02d.txt"%i)).read_text()
                                      for i in range(22)))
        with zipfile.ZipFile(io.BytesIO(packed)) as z:
            assert z.testzip() is None and len(z.namelist())==82
            z.extractall(cls.tmp.name)
        sys.path.insert(0,cls.tmp.name)
        from projectgmo.engine import Engine,Quote,d1_allowed_ceiling
        from projectgmo.selector import DSignal
        cls.Engine,cls.Quote=Engine,Quote
        cls.ceiling=staticmethod(d1_allowed_ceiling)
        cls.DSignal=DSignal

    @classmethod
    def tearDownClass(cls):
        if cls.tmp.name in sys.path:sys.path.remove(cls.tmp.name)
        cls.tmp.cleanup()

    @staticmethod
    def now(second=1):
        return f"2026-11-17T09:00:{second:02d}+09:00"

    def signal(self,code,name):
        return self.DSignal(code,name,"2026-11-16","2026-11-17",
            4950,4900,4800,4800,-10000,5000,5000,[-1000]*20,
            "SHORT","KOSPI","SYNTHETIC_REGRESSION_NO_REAL_LEDGER")

    def test_first_krx_open_is_inclusive_even_if_d_close_is_below_open(self):
        self.assertEqual(self.ceiling(5000,4900),5000)

    def test_above_krx_open_is_still_forbidden_if_d_close_alternative_lower(self):
        self.assertEqual(self.ceiling(5000,4900),5000)
        self.assertGreater(5005,self.ceiling(5000,4900))

    def test_both_reported_d16_candidates_can_create_09_00_open_intents(self):
        engine=self.Engine()
        for code,name in (("012200","계양전기"),("278650","HLB바이오스텝")):
            engine.signals[code]=self.signal(code,name)
        for code in ("012200","278650"):
            order=engine.open_entry(code,"2026-11-17",5000,self.now(),
                observed_price=5000,observed_ask=5000)
            self.assertEqual(order.side,"BUY")
            self.assertEqual(order.purpose,"D+1_OPEN_AT_OR_BELOW_OPEN")
            self.assertEqual(order.limit,5000)
            self.assertGreater(order.requested,0)
        self.assertEqual(len(engine.orders),2)

    def test_paper_fill_uses_fresh_krx_0d_and_0b_not_an_invented_fill(self):
        engine=self.Engine()
        engine.signals["012200"]=self.signal("012200","계양전기")
        order=engine.open_entry("012200","2026-11-17",5000,self.now(),
            observed_price=5000,observed_ask=5000)
        self.assertEqual(len(engine.buy_fills),0)
        book=self.Quote("012200",self.now(2),1,5000,5000,4995,
                        1000,1000,"KRX",0.5)
        engine.paper_match(book)
        self.assertGreater(order.filled,0)
        self.assertGreater(len(engine.buy_fills),0)

    def test_d_plus_two_and_above_limit_do_not_create_new_buys(self):
        engine=self.Engine()
        engine.signals["012200"]=self.signal("012200","계양전기")
        with self.assertRaisesRegex(ValueError,"D\\+1"):
            engine.open_entry("012200","2026-11-17",5000,
                "2026-11-18T09:00:00+09:00",observed_price=5000,observed_ask=5000)
        with self.assertRaisesRegex(ValueError,"above"):
            engine.open_entry("012200","2026-11-17",5000,self.now(),
                observed_price=5005,observed_ask=5005)
        self.assertFalse(engine.orders)

if __name__=="__main__":
    unittest.main()
