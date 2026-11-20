"""v089 user-visible WS quote plane and 4-orientation preload safety.

Uses only in-memory fake event sources; no exchange requests or SQLite writes.
"""
from __future__ import annotations
import json
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]/'app/src/main'
PY=(ROOT/'python/android_runtime_v078.py').read_text()
JAVA=(ROOT/'java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
SERVICE=(ROOT/'java/com/artitrade/mobile/UnifiedTradingService.java').read_text()
BRIDGE=(ROOT/'java/com/artitrade/mobile/StrategyRuntimeBridge.java').read_text()
MANIFEST=(ROOT/'AndroidManifest.xml').read_text()

class SafePreloadRotation(unittest.TestCase):
    def test_original_progress_survives_activity_recreation(self):
        for token in ('v089_preload_ready','v089_preload_elapsed_at',
                      'v089_preload_wall_at','v089_preload_pct','v089_preload_stage'):
            self.assertIn(token,JAVA)
        self.assertIn('return saved.getBoolean("v089_preload_ready",true)',JAVA)
        self.assertIn('state.putBoolean("v089_preload_ready",preloadScreenReady)',JAVA)
        self.assertIn('if(state!=null&&!state.getBoolean("v089_preload_ready",true))',JAVA)
        self.assertIn('screenOrientation="fullUser"',MANIFEST)
        self.assertIn('configChanges="orientation|screenSize',MANIFEST)

class ReadOnlyQuoteLane(unittest.TestCase):
    def test_uses_only_cached_websocket_observations(self):
        from android_runtime_v078 import price_snapshot_v089,_core
        original=getattr(_core,'_RUNTIME',None)
        now=int(time.time()*1000)
        coin=SimpleNamespace(
            _v086_last_trade_by_code={
                "KRW-BTC":(now-100,70000000),
                "KRW-XRP":(now-30000,3500),
                "KRW-NOT_WATCHED":(now-100,1000)},
            _held_codes={"KRW-BTC"},tracked={"KRW-BTC","KRW-XRP"},
            _ws_connected=True,_ws_last_frame=time.monotonic())
        _core._RUNTIME=SimpleNamespace(started=True,coin_runner=coin,stock_runner=None)
        try:
            view=json.loads(price_snapshot_v089())
            self.assertTrue(view['ready'])
            self.assertEqual(view['coin']['quotes']['KRW-BTC']['price'],70000000)
            self.assertEqual(view['coin']['quotes']['KRW-BTC']['status'],'FRESH')
            self.assertEqual(view['coin']['quotes']['KRW-XRP']['status'],'LAST_WS')
            self.assertNotIn('KRW-NOT_WATCHED',view['coin']['quotes'])
            coin._ws_connected=False
            disconnected=json.loads(price_snapshot_v089())
            self.assertEqual(disconnected['coin']['quotes']['KRW-BTC']['status'],'LAST_WS')
            self.assertFalse(disconnected['fills_allowed'])
        finally:
            _core._RUNTIME=original

    def test_price_plane_no_new_websocket_or_rest(self):
        body=PY.split('def price_snapshot_v089():',1)[1].split(
            '# v045 explicit score-independent',1)[0]
        for forbidden in ('.post(','.connect(','.execute(',
                          'sqlite3.connect(','requests.', 'WebSocket('):
            self.assertNotIn(forbidden,body)
        self.assertIn('_v086_last_trade_by_code',body)
        self.assertIn('live_quote_snapshot',body)
        self.assertIn('trade timestamp',body)

    def test_separate_from_original_ledger_snapshots_and_original_orders(self):
        self.assertIn('priceOnlyPublisher=new Thread(this::priceOnlyLoop',SERVICE)
        self.assertIn('interval=foreground?1200L:3500L',SERVICE)
        self.assertIn('manualSellOutstanding.get()==0',SERVICE)
        self.assertIn('quoteSnapshotV089()',BRIDGE)
        self.assertIn('private volatile PyObject module',BRIDGE)
        self.assertIn('FAST_PRICE_VIEW_MS=900L',JAVA)
        self.assertIn('refreshVisiblePriceViews()',JAVA)
        self.assertIn('livePriceBindings.clear()',JAVA)
        self.assertIn('setQuoteDot(item.status',JAVA)  # 0.1.7: status is a colour dot, not a sentence
        self.assertIn("'fills_allowed':False",PY)

if __name__=='__main__':unittest.main()
