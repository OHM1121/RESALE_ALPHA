"""v081: two real feeds before dashboard, no duplicate brokers or fake fills."""
import datetime as dt
import pathlib
import threading
import unittest
from unittest.mock import patch
import sys

ROOT=pathlib.Path(__file__).resolve().parents[1]
PY=ROOT/'app/src/main/python'
sys.path.insert(0,str(PY))

class V081TwoFeedsPreload(unittest.TestCase):
    def test_first_genuine_coin_universe_starts_only_original_single_ws(self):
        import android_runtime_v078 as start
        class Gate:
            ready=False
            def snapshot(self):return {'ready':self.ready}
        class Runner:
            tracked=['KRW-BTC']
            stop_event=threading.Event()
            preload_gate=Gate()
            def __init__(self):self.started=0
            def _ensure_ws_workers(self):self.started+=1
        dummy=Runner()
        with patch.object(start,'_V081_COIN_UNIVERSE_ORIG',lambda runner:'UNIVERSE_OK'):
            self.assertEqual(start._v081_coin_universe_then_single_ws(dummy),'UNIVERSE_OK')
        self.assertEqual(dummy.started,1)
        self.assertFalse(dummy.preload_gate.ready)
        dummy.preload_gate.ready=True
        with patch.object(start,'_V081_COIN_UNIVERSE_ORIG',lambda runner:'LATER'):
            self.assertEqual(start._v081_coin_universe_then_single_ws(dummy),'LATER')
        self.assertEqual(dummy.started,1,'Never create a new public socket on ordinary refresh')

    def test_early_genuine_market_ticks_do_not_execute_during_ledger_preflight(self):
        import android_runtime_v078 as start
        class Gate:
            ready=False
            def snapshot(self):return {'ready':self.ready}
        class Runner:
            preload_gate=Gate()
        runner=Runner()
        sent=[]
        with patch.object(start,'_V081_COIN_WS_EVENT_ORIG',
                          side_effect=lambda obj,kind,*a,**k:sent.append((kind,a))):
            for kind in ('book','mark'):
                self.assertFalse(start._v081_coin_verified_ws_prime_before_orders(
                    runner,kind,{'market':'KRW-BTC'}))
            self.assertEqual(sent,[])
            runner.preload_gate.ready=True
            start._v081_coin_verified_ws_prime_before_orders(
                runner,'book',{'market':'KRW-BTC','timestamp':1})
            self.assertEqual(sent[0][0],'book')
        self.assertEqual(len(sent),1)

    def test_dashboard_never_assumes_rest_marks_are_verified_live_ticks(self):
        source=(PY/'android_runtime_v078.py').read_text()
        self.assertIn("coin_node['rest_connected']",source)
        self.assertIn("coin_node['connected']=coin_trade",source)
        self.assertIn("'ws_supervisor_running':coin_watching",source)
        self.assertIn("'both_feeds_available'",source)
        self.assertIn("'rsi_history':warm",source)
        self.assertIn("self.preload_gate.snapshot().get('ready',False)",source)
        trend=(PY/'market_trend_runtime_v043.py').read_text()
        self.assertIn("if gate is not None and not gate.snapshot().get('ready',False):",trend)

    def test_original_service_polls_both_feeds_outside_mserver_and_keeps_disarmed(self):
        java=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedTradingService.java').read_text()
        bridge=(ROOT/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java').read_text()
        self.assertIn('new Thread(()->safeStartStrategy()',java)
        self.assertIn('preloadObserver=new Thread(this::observeStartupReadiness',java)
        self.assertIn('getSharedPreferences("artitrade_runtime_snapshot"',java)
        self.assertIn('getSharedPreferences("artitrade_local_records_v075"',java)
        self.assertIn('private volatile boolean started=false;',bridge)
        self.assertIn('.putBoolean("live_stock",false)',bridge)
        self.assertIn('.putBoolean("live_coin",false)',bridge)

    def test_real_quote_and_original_ledger_are_gated_before_cold_shell(self):
        java=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertIn('PRELOAD_WATCHDOG_MS=40_000L',java)
        self.assertIn('PRELOAD_WATCHDOG_MS=40_000L',java)
        self.assertIn('startupNativeAuditReady',java)
        self.assertIn('updateVerifiedStartupProgress(',java)
        self.assertNotIn('if(elapsed>=PRELOAD_BOTH_FEEDS_WARM_MIN_MS)',java)
        self.assertIn('startupReadinessTick()',java)
        self.assertIn('coin.optBoolean("ws_ready",false)',java)
        self.assertIn('stock.optBoolean("ws_ready",false)',java)
        self.assertIn('uiSnapshot.totals.size()>=6',java)
        self.assertIn('coinGate.optBoolean("ready",false)',java)
        self.assertIn('실시간 시세 연결 확인 · RSI20 확정봉',java)
        self.assertIn('장외/휴장 · WS 체결 필수 아님',java)
        self.assertIn('RSI20 확정봉 주식',java)
        self.assertNotIn('finishPreload(engineReady)',java)
        # Since v089, a rotating cold-start preserves the original watchdog
        # deadline rather than starting another 40-second waiting period.
        self.assertIn('main.postDelayed(this::preloadWatchdog,',java)
        self.assertIn('PRELOAD_WATCHDOG_MS-elapsed',java)
        self.assertIn('v089_preload_ready',java)


if __name__=='__main__':
    unittest.main()
