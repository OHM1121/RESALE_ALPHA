"""v090 source-level regression: no forged orders, prints or ledger resets."""
import datetime as dt
import tempfile
import unittest
from unittest.mock import patch
import d_gmo_runtime as gmo
from projectgmo.selector import DSignal
class FixedDateTime(dt.datetime):
    @classmethod
    def now(cls,tz=None):
        return cls(2026,11,17,9,12,tzinfo=tz or gmo.KST)
def signal():
    return DSignal('005930','Test','2026-11-16','2026-11-17',
                   100.,90.,100.,100.,-3000,1200,900,[-400]*20,
                   'SHORT','KOSPI','SYNTHETIC_OFFICIAL_SOURCE')
def prepare(root):
    service=gmo.GMOService(root)
    assert service.register_signal(signal()).accepted
    with service._db() as c:
        c.execute("UPDATE gmo_candidates SET status='미체결/후보' WHERE code='005930'")
    service.engine.signals.pop('005930')
    service._persist(True)
    return service
class GMORecovery(unittest.TestCase):
    def test_pending_unfilled_signal_restored_after_restart(self):
        with tempfile.TemporaryDirectory() as root,patch.object(gmo,'datetime',FixedDateTime):
            prepare(root)
            restored=gmo.GMOService(root)
            self.assertIn('005930',restored.engine.signals)
            self.assertIn('005930',restored.priority_watchlist(FixedDateTime.now(gmo.KST)))
            self.assertEqual(len(restored.engine.buy_fills),0)
            self.assertEqual(len(restored.engine.orders),0)
    def test_missing_official_open_retried_for_unfilled_candidate(self):
        with tempfile.TemporaryDirectory() as root,patch.object(gmo,'datetime',FixedDateTime):
            prepare(root)
            restored=gmo.GMOService(root)
            class OfficialApi:
                def post(self,code,endpoint,params):
                    self_test.assertEqual(code,'ka10001')
                    return {'open_pric':'090'}
            self_test=self
            restored._fetch_missing_opens(OfficialApi())
            self.assertEqual(restored.opens[('005930','2026-11-17')],90)
            self.assertEqual(restored.engine.buy_fills,[])
    def test_used_entry_key_never_rearmed(self):
        with tempfile.TemporaryDirectory() as root,patch.object(gmo,'datetime',FixedDateTime):
            before=prepare(root)
            before.engine.used.add('005930|2026-11-17')
            before._persist(True)
            restored=gmo.GMOService(root)
            self.assertNotIn('005930',restored.priority_watchlist(FixedDateTime.now(gmo.KST)))
            self.assertIn('005930|2026-11-17',restored.engine.used)
    def test_d2_never_becomes_new_entry(self):
        from projectgmo.engine import Engine
        e=Engine()
        e.record_signal(signal())
        with self.assertRaisesRegex(ValueError,r'D\+1 KRX regular-session'):
            e.open_entry('005930','2026-11-17',90,'2026-11-18T09:12:00+09:00',
                         observed_price=89,observed_ask=89)
if __name__=='__main__':unittest.main()
