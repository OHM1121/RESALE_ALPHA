"""GMO verified manual PAPER SELL with real broker 0D and no 0B.

v086 explicitly confirmed user PAPER sell may consume demonstrably genuine
best bid depth via the original GMO intent/fill ledger. A stale 0D can never
create an intent/fill. No actual broker API order or fabricated trade print.
"""
import datetime as dt
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from projectgmo.engine import Engine,Position
from android_manual_paper_sell_v085 import _gmo
KST=dt.timezone(dt.timedelta(hours=9))


def stock(rt,engine,now,age=0):
    class SharedBook:pass
    book=SharedBook()
    book._lock=threading.RLock()
    when=now-dt.timedelta(seconds=age)
    book._items={('005930','KRX'):(10010,10000,200,when)}
    book._bid_depth={('005930','KRX'):(10000,25,when)}
    book._bid_reserved={}
    service=SimpleNamespace(_lock=threading.RLock(),engine=engine,
                            _persist=lambda flag:None)
    runner=SimpleNamespace(gmo=service,engine=SimpleNamespace(book=book),
                           _confirmed_ws_session=True,
                           latest_krx_trade=lambda code:None)
    rt.stock_runner=runner
    return service


class StockManualGMO(unittest.TestCase):
    def request(self):
        return {'code':'005930','position_id':99,'expected_qty':'10',
                'percent':100,'request_id':'00000000-0000-4000-8000-000000009094'}

    def test_fresh_0d_without_0b_confirms_native_paper_fill_once(self):
        engine=Engine()
        now=dt.datetime.now(KST)
        p=Position('005930','Test',10,10,10000,now.date().isoformat(),'KRX',100000)
        engine.positions[p.code]=p
        rt=SimpleNamespace()
        stock(rt,engine,now)
        self.assertIsNone(rt.stock_runner.latest_krx_trade('005930'))
        with patch('stock_manual_v086._krx_session',return_value=True):
            result=_gmo(rt,self.request())
            replay=_gmo(rt,self.request())
        self.assertEqual((result['status'],result['qty']),('FILLED',10))
        self.assertTrue(replay['already_executed'])
        self.assertEqual(len([x for x in engine.orders.values() if x.side=='SELL']),1)
        self.assertEqual(len(engine.fills),1)
        self.assertEqual(engine.fills[0]['qty'],10)
        self.assertNotIn('005930',engine.positions)
        self.assertNotEqual(result.get('quote_source'),None)

    def test_stale_bid_depth_never_creates_manual_exit_intent(self):
        engine=Engine()
        now=dt.datetime.now(KST)
        p=Position('005930','Test',10,10,10000,now.date().isoformat(),'KRX',100000)
        engine.positions[p.code]=p
        rt=SimpleNamespace()
        stock(rt,engine,now,age=8)
        with patch('stock_manual_v086._krx_session',return_value=True):
            with self.assertRaises(ValueError):
                _gmo(rt,self.request())
        self.assertEqual(engine.orders,{})
        self.assertEqual(engine.fills,[])

if __name__=='__main__':unittest.main()
