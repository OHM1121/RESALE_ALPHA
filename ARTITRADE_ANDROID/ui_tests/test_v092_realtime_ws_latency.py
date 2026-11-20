"""v092: official Kiwoom LOGIN/PING and Upbit 15-level WS performance contracts.

No network, broker, live account or original phone SQLite is accessed.
"""
import ast
import asyncio
import base64
import io
import json
from pathlib import Path
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PARTS=ROOT/'app/src/main/assets/strategy_v070'
with zipfile.ZipFile(io.BytesIO(base64.b64decode(''.join(
        (PARTS/f'part_{i:02d}.txt').read_text('ascii') for i in range(22))))) as z:
    assert z.testzip() is None and len(z.namelist()) == 82
    STOCK=z.read('d_live.py').decode('utf-8')
    COIN=z.read('d_upbit.py').decode('utf-8')
UI=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text('utf-8')


def extract_global(source,name,globals_dict=None):
    tree=ast.parse(source)
    nodes=[node for node in tree.body
           if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))
           and node.name==name]
    if len(nodes)!=1:
        raise AssertionError(f'EXPECTED_ONE_GLOBAL_FUNCTION_{name}; FOUND_{len(nodes)}')
    ns=dict(globals_dict or {})
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'v092_real_source','exec'),ns)
    return ns[name]


class FakeWS:
    def __init__(self,*replies):
        self.replies=list(replies)
        self.sent=[]
    async def recv(self):
        if self.replies:return self.replies.pop(0)
        await asyncio.sleep(0.5)
    async def send(self,payload):
        self.sent.append(payload)


class KiwoomOfficialLoginPing(unittest.IsolatedAsyncioTestCase):
    async def test_ping_before_login_ack_echoed_and_not_mistaken_for_login_error(self):
        handler=extract_global(STOCK,'_v092_read_login_ack',
            {'asyncio':asyncio,'json':json,'time':__import__('time')})
        ws=FakeWS('{"trnm":"PING"}','{"trnm":"LOGIN","return_code":0}')
        ack=await handler(ws,timeout=0.3)
        self.assertEqual(ack['trnm'],'LOGIN')
        self.assertEqual(ws.sent,['{"trnm":"PING"}'])
        self.assertIn('ack = await _v092_read_login_ack(ws, timeout=15)',STOCK)

    async def test_plain_ping_or_bytes_ping_are_echoed_as_text(self):
        handler=extract_global(STOCK,'_v092_read_login_ack',
            {'asyncio':asyncio,'json':json,'time':__import__('time')})
        ws=FakeWS('PING',b'{"trnm":"PING"}',b'{"trnm":"LOGIN","return_code":"0"}')
        result=await handler(ws,timeout=0.3)
        self.assertEqual(result['trnm'],'LOGIN')
        self.assertEqual(ws.sent,['PING','{"trnm":"PING"}'])

    async def test_ping_stream_does_not_extend_absolute_login_timeout(self):
        handler=extract_global(STOCK,'_v092_read_login_ack',
            {'asyncio':asyncio,'json':json,'time':__import__('time')})
        class EndlessPing:
            async def recv(self):
                await asyncio.sleep(0.004)
                return '{"trnm":"PING"}'
            async def send(self,raw):pass
        with self.assertRaises(asyncio.TimeoutError):
            await handler(EndlessPing(),timeout=0.016)

    async def test_non_ping_before_login_fails_closed_without_authentication(self):
        handler=extract_global(STOCK,'_v092_read_login_ack',
            {'asyncio':asyncio,'json':json,'time':__import__('time')})
        with self.assertRaises(RuntimeError):
            await handler(FakeWS('{"trnm":"REG","return_code":0}'),timeout=0.2)

    def test_one_official_websocket_no_added_login_or_price_freshness_bypass(self):
        self.assertEqual(STOCK.count('async with websockets.connect('),1)
        self.assertIn('ping_interval=20, ping_timeout=45',STOCK)
        self.assertIn("await ws.send(json.dumps({'trnm': 'LOGIN', 'token': token}))",STOCK)
        self.assertIn("if msg.get('trnm') == 'PING':",STOCK)
        self.assertIn("if len(codes) > 200 or len(codes) != len(set(codes)):",STOCK)
        self.assertIn("if lag>3.0 and kind!='flush':",STOCK)


class UpbitPublicOrderbook15(unittest.TestCase):
    def setUp(self):
        tree=ast.parse(COIN)
        funcs=[node for node in ast.walk(tree)
               if isinstance(node,ast.FunctionDef) and node.name=='make_sub']
        self.assertEqual(len(funcs),1,'One existing original WS supervisor')
        scope={}
        exec(compile(ast.Module(body=funcs,type_ignores=[]),'native_upbit_ws','exec'),scope)
        self.make_sub=scope['make_sub']

    def test_only_reduced_orderbook_depth_ticker_codes_remain_identical(self):
        pkt=self.make_sub('ABC',frozenset(['KRW-BTC','KRW-ETH']),
                          frozenset(['KRW-BTC','KRW-ETH']))
        self.assertEqual(pkt[0],{'ticket':'ABC'})
        self.assertEqual(pkt[1]['type'],'ticker')
        self.assertEqual(pkt[1]['codes'],['KRW-BTC','KRW-ETH'])
        self.assertTrue(pkt[1]['is_only_realtime'])
        self.assertEqual(pkt[2]['type'],'orderbook')
        self.assertEqual(pkt[2]['codes'],['KRW-BTC.15','KRW-ETH.15'])
        self.assertTrue(pkt[2]['is_only_realtime'])
        self.assertEqual(pkt[-1],{'format':'DEFAULT'})
        self.assertIn('units[:15]',COIN)

    def test_non_krw_market_codes_never_receive_unadvertised_depth_suffix(self):
        pkt=self.make_sub('ABC',{'KRW-BTC','BTC-ETH'},
                          {'KRW-BTC','BTC-ETH'})
        self.assertEqual(pkt[2]['codes'],['BTC-ETH','KRW-BTC.15'])

    def test_upbit_exchange_timestamp_and_original_connect_are_preserved(self):
        self.assertIn("ping_interval=20,ping_timeout=30",COIN)
        self.assertIn("trade_stamp=item.get('trade_timestamp')",COIN)
        self.assertIn("timestamp=item.get('timestamp')",COIN)
        self.assertIn("if trade_age < -0.5 or trade_age > 10.0:",COIN)
        self.assertIn("abs(time.time()-timestamp/1000)>MAX_BOOK_AGE_SEC",COIN)
        self.assertIn("self._enqueue_ws_event('book',book)",COIN)


class StartupScreenLatency(unittest.TestCase):
    def test_verified_live_feeds_do_not_wait_22_seconds_for_history_warmup(self):
        self.assertIn('PRELOAD_BOTH_FEEDS_WARM_MIN_MS=8_000L',UI)
        self.assertNotIn('PRELOAD_BOTH_FEEDS_WARM_MIN_MS=22_000L',UI)
        self.assertIn('if(engine&&uiReady&&stockReady&&coinReady)',UI)
        self.assertIn('if(coinWarm&&stockWarm)',UI)
        self.assertIn('finishPreload(false)',UI)
        self.assertIn('PRELOAD_WATCHDOG_MS=40_000L',UI)

if __name__=='__main__':unittest.main(verbosity=2)
