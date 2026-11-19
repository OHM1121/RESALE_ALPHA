"""v010 phone-log regression: D+1 prior-close OR open and genuine 0B PAPER recovery.

Never submit broker orders, infer KRX ask, or backfill 0D quantity.
Root: 2026-11-19 12:50 original 0.0.9 bundle LS000680 0B trades,
193 GMO_BOOK_INVALID and 41 V097_PREPLANNED_D1_ENTRY_OWNER suppressions.
"""
import datetime as dt
import os,sys,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]/"app/src/main/python"
sys.path.insert(0,str(ROOT))
from gmo_d1_preplan_v097 import Preplanner,KST
from projectgmo.engine import Engine,Quote

class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.at=dt.datetime(2026,11,17,9,2,0,tzinfo=KST)
        engine=Engine()
        engine._v097_managed_codes=set()
        engine.signals['005930']=SimpleNamespace(
            code="005930",name="GMO_TEST",event_date="2026-11-16",
            entry_date="2026-11-17",event_close=9900,session_type="SHORT")
        svc=SimpleNamespace(root=Path(self.tmp.name),engine=engine,
             _lock=threading.RLock(),opens={},priority_books={},
             _stop=threading.Event(),_persist=lambda *_a,**_kw:None)
        self.svc=svc;self.p=Preplanner(svc,clock=lambda:self.at)
        self.assertEqual(self.p.prepare('KRX',self.at-dt.timedelta(minutes=3)),
                         ('005930',))
        self.plan=self.p.plans[('2026-11-17','005930','KRX')]
        self.plan['opening_price']=10000
    def tearDown(self):self.tmp.cleanup()
    def q(self,last,ask,volume=50):
        return Quote('005930',self.at.isoformat(),1,float(last),
                     float(ask),ask-10 if ask else 0,
                     80 if ask else 0,70 if ask else 0,"KRX",0)
    def tick(self,last,volume=50,received=None):
        received=received or self.at.isoformat()
        return ('005930','KOSPI','GMO_TEST','202611170902',last,volume,
                self.at.isoformat(),'KRX',received,0,0)
    def test_verified_d_close_is_independent_even_when_open_is_lower(self):
        self.svc.engine.signals['005930'].event_close=10000
        self.plan['opening_price']=9800
        q=self.q(9950,9950)
        self.p._step(self.plan,self.at,9950,9950,50,"KRX",q=q,tick=self.tick(9950))
        self.assertTrue(self.svc.engine.orders,
                        '9950 below verified D close 10000 must not be rejected merely because official open was 9800')
        self.assertEqual(self.plan.get('entry_reference_source'),'VERIFIED_D_CLOSE_STRICT_BELOW')
        self.assertLessEqual(max(x.limit for x in self.svc.engine.orders.values()),9990)
    def test_fresh_zero_book_0b_print_allows_bounded_paper_recovery(self):
        q=self.q(9850,0,volume=200)
        self.p._step(self.plan,self.at,9850,0,0,'KRX',q=q,tick=self.tick(9850,volume=200))
        self.assertGreater(len(self.svc.engine.buy_fills),0,
                           'genuine KRX 0B trade with no 0D must be eligible for bounded explicit PAPER proxy')
        self.assertLessEqual(sum(f['qty'] for f in self.svc.engine.buy_fills),20)
        self.assertEqual(self.plan.get('entry_quote_proof'),'KRX_0B_VOLUME_BOUNDED_PAPER_PROXY_NOT_BROKER')
    def test_stale_exchange_timestamp_does_not_buy_without_0d(self):
        q=self.q(9850,0)
        stale=(self.at-dt.timedelta(minutes=1)).isoformat()
        self.p._step(self.plan,self.at,9850,0,0,'KRX',q=q,tick=self.tick(9850,received=stale))
        self.assertFalse(self.svc.engine.orders)
        self.assertEqual(self.plan.get('status'),'WAIT_FRESH_KRX_0B_OR_0D')
    def test_missing_both_book_and_tick_no_order(self):
        q=self.q(9850,0)
        self.p._step(self.plan,self.at,9850,0,0,'KRX',q=q,tick=None)
        self.assertFalse(self.svc.engine.orders)
    def test_real_mode_cannot_use_paper_0b_proxy(self):
        q=self.q(9850,0)
        with patch.dict(os.environ,{'ARTITRADE_REAL_MODE':'1'}):
            self.p._step(self.plan,self.at,9850,0,0,'KRX',q=q,tick=self.tick(9850))
        self.assertFalse(self.svc.engine.orders)

if __name__=="__main__":unittest.main()
