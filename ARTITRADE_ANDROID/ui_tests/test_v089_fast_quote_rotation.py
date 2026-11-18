"""V089: no extra broker calls, truthful fast prices, and rotation-safe preload.

Tests use an isolated stub of the 82-module EXE runtime; actual trading and
SQLite wallets are not opened or altered.
"""
import ast
import hashlib
import json
import math
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]/"app/src/main"
JAVA=(ROOT/"java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
SERVICE=(ROOT/"java/com/artitrade/mobile/UnifiedTradingService.java").read_text()
BRIDGE=(ROOT/"java/com/artitrade/mobile/StrategyRuntimeBridge.java").read_text()
MODULE=(ROOT/"python/android_runtime_v078.py").read_text()

def fast_function(rt):
    tree=ast.parse(MODULE)
    node=next(x for x in tree.body if isinstance(x,ast.FunctionDef)
              and x.name=="quote_snapshot_v089")
    isolated=compile(ast.Module(body=[node],type_ignores=[]),"<isolated-v089>", "exec")
    ns={"_core":types.SimpleNamespace(_RUNTIME=rt),"time":time,
        "math":math,"hashlib":hashlib,"json":json}
    exec(isolated,ns)
    return ns["quote_snapshot_v089"]

class PureExistingTickerQuote(unittest.TestCase):
    def setUp(self):
        self.now=time.monotonic()
        coin=types.SimpleNamespace(
            _ws_connected=True,_ws_last_frame=self.now,
            latest_prices={"KRW-BTC":"98500000","KRW-ETH":"4300000"},
            _held_codes={"KRW-BTC"},tracked={"KRW-ETH"},
            _v084_tick_lock=threading.RLock(),
            _v084_tick_delivered={"KRW-BTC":(time.time(),self.now)})
        self.coin=coin
        self.rt=types.SimpleNamespace(coin_runner=coin,stock_runner=object())
        self.fn=fast_function(self.rt)
        mod=types.SimpleNamespace(live_quote_snapshot=lambda r:{
            "quotes":{"005930":{"price":68000,"status":"FRESH",
                "source":"KIWOOM_REAL_0B","venue":"KRX",
                "expires_ms":int(time.time()*1000)+5000}},
            "revision":"stock-source-v089"})
        self.patch=patch.dict(sys.modules,{"stock_quote_truth_v066":mod})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_ws_trade_fresh_rest_fallback_separate_and_no_network(self):
        result=json.loads(self.fn())
        self.assertEqual(result["schema"],"ARTITRADE_FAST_QUOTE_V089")
        self.assertEqual(result["stock"]["quotes"]["005930"]["price"],68000)
        btc=result["coin"]["quotes"]["KRW-BTC"]
        self.assertEqual(btc["price"],98500000)
        self.assertEqual(btc["status"],"WS_FRESH")
        self.assertEqual(btc["source"],"UPBIT_WS_TRADE")
        eth=result["coin"]["quotes"]["KRW-ETH"]
        self.assertEqual(eth["status"],"REST_OR_STORED")
        self.assertEqual(eth["received_at_ms"],0)
        self.assertNotIn("secret",json.dumps(result).lower())
        self.assertNotIn("api_key",json.dumps(result).lower())

    def test_disconnected_ws_cannot_claim_realtime_even_if_tick_exists(self):
        self.coin._ws_connected=False
        q=json.loads(self.fn())["coin"]["quotes"]["KRW-BTC"]
        self.assertEqual(q["status"],"LAST_WS")

    def test_bounded_cache_and_repeatable_revision_if_no_ticks(self):
        self.coin.latest_prices.update(
            {"KRW-%03d"%i:str(i+1) for i in range(350)})
        self.coin.tracked.update(self.coin.latest_prices)
        first=json.loads(self.fn())
        second=json.loads(self.fn())
        self.assertLessEqual(len(first["coin"]["quotes"]),220)
        # Time-based stale statuses may change, but no trade takes place.
        self.assertTrue(first["revision"])
        self.assertEqual(set(first["coin"]["quotes"]),set(second["coin"]["quotes"]))

    def test_fast_plane_never_becomes_order_or_extra_exchange_poller(self):
        body=MODULE[MODULE.index("def quote_snapshot_v089():"):
                    MODULE.index("# Re-export the stable Android bridge API.")]
        for forbidden in ("requests.get(","requests.post(","api.post(",
                          "sqlite3.connect(","create_connection(","place_order("):
            self.assertNotIn(forbidden,body)
        self.assertIn('Thread.sleep(nap)',SERVICE)
        self.assertIn('manualSellOutstanding.get()>0',SERVICE)
        self.assertIn('artitrade_fast_quote_v089',SERVICE)
        self.assertIn('synchronized JSONObject quoteSnapshot()',BRIDGE)

class PreloadOrientationSafety(unittest.TestCase):
    def test_rotation_restores_waiting_gate_and_deadline(self):
        self.assertIn('preload_ready_v089',JAVA)
        self.assertIn('preload_started_elapsed_v089',JAVA)
        self.assertIn('preload_started_wall_v089',JAVA)
        self.assertIn('preload_percent_v089',JAVA)
        self.assertIn('PRELOAD_WATCHDOG_MS-elapsed',JAVA)
        self.assertIn('saved.getBoolean("preload_ready_v089",false)',JAVA)
        self.assertNotIn("return saved!=null||isRecentBackgroundHeartbeat();",JAVA)

    def test_visible_positions_price_refresh_is_not_entire_ledger_rebuild(self):
        self.assertIn('FAST_QUOTE_REPAINT_MS=900L',JAVA)
        self.assertIn('paintCurrentQuoteValues()',JAVA)
        self.assertIn('bindFastQuoteView(market,r,fastPrice,net,false)',JAVA)
        self.assertIn('bindFastQuoteView(holding.scopeMarket,p,prices,result,true)',JAVA)
        self.assertIn('main.removeCallbacks(fastQuoteRepaint)',JAVA)
        self.assertIn('artitrade_fast_quote_v089',JAVA)
        self.assertIn('표시 전용 · 매매 체결 근거 아님',JAVA)

if __name__=="__main__":unittest.main()
