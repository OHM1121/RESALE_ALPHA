"""Display-only stock quote bridge regression. No network, orders or real accounts."""
import datetime as dt
import json
import threading
import time
import unittest

import stock_quote_truth_v066 as quote

T=dt.datetime(2026,11,16,10,55,tzinfo=quote.KST)

def packet():
    return {'stock': {'at': {'ledger': {'positions': [
        {'code':'005930','status':'OPEN','entry_price':70000,'qty':10,'last_price':65000}
    ]}}}}

class Runner:
    def __init__(self):
        self._confirmed_ws_session=True
        self._new_tick=lambda tick:None
        self.subscriptions=['005930','000660']

class PriceDisplayV072(unittest.TestCase):
    def setUp(self):
        self.runner=Runner()
        quote.install_ui_quote_tap(self.runner)
    def annotate(self):
        return quote.annotate_stock_packet(packet(),self.runner,T)['stock']['at']['ledger']['positions'][0]

    def test_readonly_official_rest_price_in_correct_symbol(self):
        self.runner._v072_rest_quotes={'005930':{'price':74000,
            'received_at':T.isoformat(), 'source':'KIWOOM_REST_KA10001'}}
        row=self.annotate()
        self.assertEqual(row['stock_quote_status'],'REST_FRESH')
        self.assertEqual(row['stock_quote_display_price'],74000)
        self.assertNotIn('last_price',row)
        self.assertEqual(row['ledger_last_price'],65000)

    def test_legitimate_quiet_stock_remains_last_real_trade_not_black_screen(self):
        tick=('005930','KOSPI','Samsung','20261116105400',73500,20,
            (T-dt.timedelta(seconds=25)).isoformat(),'KRX',
            (T-dt.timedelta(seconds=27)).isoformat(),73500,73400)
        self.runner._new_tick(tick)
        row=self.annotate()
        self.assertEqual(row['stock_quote_status'],'LAST_0B')
        self.assertEqual(row['stock_quote_display_price'],73500)
        self.assertEqual(row['stock_quote_source'],'KIWOOM_REAL_0B_LAST')
        self.assertNotIn('last_price',row)

    def test_broker_disconnected_does_not_pretend_ws_price_is_live(self):
        tick=('005930','KOSPI','Samsung','20261116105400',73200,20,
            T.isoformat(),'KRX',T.isoformat(),73200,73000)
        self.runner._new_tick(tick)
        self.runner._confirmed_ws_session=False
        row=self.annotate()
        self.assertEqual(row['stock_quote_status'],'LAST_0B')
        self.assertEqual(row['stock_quote_display_price'],73200)

    def test_wrong_symbol_rest_does_not_fill_holding(self):
        self.runner._v072_rest_quotes={'000660':{'price':200000,
            'received_at':T.isoformat()}}
        row=self.annotate()
        self.assertEqual(row['stock_quote_status'],'LAST_LEDGER')
        self.assertEqual(row['stock_quote_source'],'STORED_LEDGER_MARK_NOT_LIVE')
        self.assertEqual(row['stock_quote_display_price'],65000)
        self.assertNotEqual(row['stock_quote_display_price'],200000)

    def test_rest_worker_calls_verified_ka10001_and_writes_only_ui_cache(self):
        runner=self.runner
        runner.stop_event=threading.Event()
        runner._v072_quote_requested={'005930'}
        runner._v072_quote_attempts={}
        runner._v072_rest_quotes={}
        runner._v072_rest_errors={}
        calls=[]
        class API:
            def post(self,api_id,path,payload):
                calls.append((api_id,path,payload))
                runner.stop_event.set()
                return {'return_code':0,'stk_cd':'005930','cur_prc':'-74100'}
        runner.api=API()
        runner._v073_display_api_factory=lambda raw:raw
        quote._rest_quote_loop(runner)
        self.assertEqual(calls,[('ka10001','/api/dostk/stkinfo',{'stk_cd':'005930'})])
        self.assertEqual(runner._v072_rest_quotes['005930']['price'],74100)
        self.assertEqual(runner._v072_rest_errors,{})

    def test_rest_rejects_wrong_symbol_and_invalid_price(self):
        self.assertIsNone(quote._rest_positive_price(''))
        self.assertIsNone(quote._rest_positive_price('0'))
        self.assertIsNone(quote._rest_positive_price(True))
        self.assertEqual(quote._rest_positive_price('+73000'),73000)

if __name__=='__main__':
    unittest.main()
