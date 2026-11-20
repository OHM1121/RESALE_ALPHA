"""v105: fresh orderbook fallback for malformed Upbit WS PAPER exit evidence.
No broker order calls, real credentials, strategy changes or SQLite writes.
"""
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import android_manual_paper_sell_v085 as manual

class VerifiedCoinDepthFallback(unittest.TestCase):
    @staticmethod
    def _runner(book):
        return SimpleNamespace(coin_runner=SimpleNamespace(
            _lock=__import__('threading').RLock(),
            last_book={'KRW-BTC':book}))

    def test_malformed_fresh_websocket_book_can_use_one_verified_public_book(self):
        stale={'market':'KRW-BTC','timestamp':int(time.time()*1000),
               'bid':'INVALID','bid_size':'0'}
        expected={'market':'KRW-BTC','timestamp':int(time.time()*1000),
                  'orderbook_units':[{'bid_price':'100','bid_size':'2',
                   'ask_price':'101','ask_size':'1'}]}
        class Response:
            def raise_for_status(self):pass
            def json(self):return [expected]
        with patch('requests.get',return_value=Response()) as get:
            result=manual._coin_book(self._runner(stale),'KRW-BTC')
        self.assertEqual(result['bid'],'100')
        self.assertEqual(result['bid_size'],'2')
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0],'https://api.upbit.com/v1/orderbook')

    def test_verified_websocket_book_does_not_make_http_request(self):
        book={'market':'KRW-BTC','timestamp':int(time.time()*1000),
              'bid':'100','bid_size':'3'}
        with patch('requests.get') as get:
            self.assertEqual(manual._coin_book(self._runner(book),'KRW-BTC'),book)
        get.assert_not_called()

    def test_bad_public_book_never_becomes_paper_fill(self):
        book={'market':'KRW-BTC','timestamp':int(time.time()*1000),
              'bid':'INVALID','bid_size':'0'}
        class Response:
            def raise_for_status(self):pass
            def json(self):return []
        with patch('requests.get',return_value=Response()) as get:
            with self.assertRaisesRegex(ValueError,'FRESH_COIN_ORDERBOOK_MISSING'):
                manual._coin_book(self._runner(book),'KRW-BTC')
        get.assert_called_once()

    def test_untrusted_future_ws_stamp_uses_public_book_or_rejects(self):
        book={'market':'KRW-BTC','timestamp':int((time.time()+2)*1000),
              'bid':'100','bid_size':'3'}
        with patch('requests.get',side_effect=OSError('offline')):
            with self.assertRaisesRegex(ValueError,'FRESH_COIN_ORDERBOOK_MISSING'):
                manual._coin_book(self._runner(book),'KRW-BTC')

if __name__=='__main__':unittest.main()
