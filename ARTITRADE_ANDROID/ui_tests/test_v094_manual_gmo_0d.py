"""GMO manual PAPER SELL: genuine fresh KRX 0D should schedule a pending
SELL intent even if no new KRX 0B trade happened in previous 3 seconds.
Do NOT mark any unverified order as FILLED.
"""
import datetime as dt
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import android_manual_paper_sell_v085 as manual
from projectgmo.engine import Engine,Position
from android_manual_paper_sell_v085 import _gmo
KST=dt.timezone(dt.timedelta(hours=9))

class StockManualGMO(unittest.TestCase):
    def test_verified_krx_0d_without_last_trade_can_submit_pending_sell_intent(self):
        engine=Engine()
        # Deterministic KRX 12:00 test; runtime actually enforces market hours.
        now=dt.datetime.now(KST).replace(hour=12,minute=0,second=0,microsecond=0)
        p=Position('005930','Test',10,10,10000,now.date().isoformat(),'KRX',100000)
        engine.positions[p.code]=p
        svc=type('Svc',(),{})()
        svc._lock=threading.RLock()
        svc.engine=engine
        svc.last_book={'005930':(10010,10000,200,25,now)}
        svc._persist=lambda flag:None
        runner=type('Runner',(),{})()
        runner.gmo=svc
        runner.latest_krx_trade=lambda code:None
        rt=type('Runtime',(),{'stock_runner':runner})()
        request={'code':'005930','position_id':99,'expected_qty':'10',
                 'percent':100,'request_id':'00000000-0000-4000-8000-000000009094'}
        class KRXTestClock:
            @staticmethod
            def now(tz):return now
        with patch.object(manual,'dt',SimpleNamespace(datetime=KRXTestClock,time=dt.time)):
            result=_gmo(rt,request)
        self.assertEqual(result['status'],'PENDING_REAL_KRX_PRINT')
        self.assertEqual(result['qty_requested'],10)
        self.assertTrue(any(o.side=='SELL' for o in engine.orders.values()))
        self.assertEqual(p.qty,10)
        self.assertEqual(engine.fills,[])
    def test_stale_bid_depth_never_creates_manual_exit_intent(self):
        engine=Engine();now=dt.datetime.now(KST)
        p=Position('005930','Test',10,10,10000,now.date().isoformat(),'KRX',100000)
        engine.positions[p.code]=p
        svc=type('Svc',(),{})();svc._lock=threading.RLock();svc.engine=engine
        svc.last_book={'005930':(10010,10000,200,100,now-dt.timedelta(seconds=6))}
        svc._persist=lambda flag:None
        runner=type('Runner',(),{})();runner.gmo=svc;runner.latest_krx_trade=lambda code:None
        rt=type('Runtime',(),{'stock_runner':runner})()
        req={'code':'005930','position_id':0,'expected_qty':'10',
                 'percent':100,'request_id':'00000000-0000-4000-8000-000000009094'}
        with self.assertRaises(ValueError):
            _gmo(rt,req)
        self.assertEqual(engine.orders,{})

if __name__=='__main__':unittest.main()
