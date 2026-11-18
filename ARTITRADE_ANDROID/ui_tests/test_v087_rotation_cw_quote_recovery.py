"""v087 non-destructive regression: orientation, RSI end clock, CW transport and API throttling."""
from __future__ import annotations
import tempfile
import unittest
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]/'app/src/main'
JAVA=(ROOT/'java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
MANIFEST=(ROOT/'AndroidManifest.xml').read_text()
CORE=(ROOT/'python/android_runtime_v078.py').read_text()
QUOTE=(ROOT/'python/stock_quote_truth_v066.py').read_text()
STAGE=(ROOT/'python/stock_at_catchup_v086.py').read_text()

class PortraitAndReverseLandscape(unittest.TestCase):
    def test_manifest_all_four_sensor_orientations_and_safe_activity_recreation(self):
        root=ET.fromstring(MANIFEST)
        a=root.find('application/activity')
        android='{http://schemas.android.com/apk/res/android}'
        self.assertEqual(a.attrib[android+'screenOrientation'],'fullSensor')
        self.assertNotIn(android+'configChanges',a.attrib,
            'activity must recreate views after physical rotation')
        self.assertIn('restoreNavigation(state);',JAVA)
        self.assertIn('showShell();',JAVA)

    def test_portrait_remains_scrollable(self):
        self.assertIn('new ScrollView(this)',JAVA)
        self.assertIn('screenWidthDp>=720',JAVA)
        self.assertIn('screenWidthDp-76',JAVA)

class RsiReferenceClock(unittest.TestCase):
    def test_future_bar_end_not_displayed_as_verified_rsi_decision(self):
        self.assertIn('rsiCandleEndIsFuture(bar)',JAVA)
        self.assertIn('판정 대상봉 마감예정',JAVA)
        self.assertIn('RSI 실시간 관측',JAVA)
        self.assertNotIn('return "RSI 판정봉  "+(bar==null',JAVA)

class CloseTradingParity(unittest.TestCase):
    def test_original_cw_strategy_remains_unchanged_and_status_visible(self):
        self.assertIn('cw_runtime_diagnostic_v087',CORE)
        self.assertIn('requested_shared_codes',CORE)
        self.assertIn('subscribed_shared_codes',CORE)
        self.assertIn('cw_runtime_diagnostic_v087',JAVA)
        self.assertIn("self.close_store.recent_candidates(250)",CORE)

class PureDisplayRateGuard(unittest.TestCase):
    def test_held_only_no_research_watchers(self):
        import stock_quote_truth_v066 as q
        class Store:
            def __init__(self,path):self.path=path
            def connect(self):return sqlite3.connect(self.path)
        with tempfile.TemporaryDirectory() as d:
            db=str(Path(d)/'artitrade.sqlite3')
            with sqlite3.connect(db) as c:
                c.execute("CREATE TABLE positions(code TEXT,status TEXT,qty INTEGER)")
                c.executemany("INSERT INTO positions VALUES(?,?,?)",
                    [('005930','OPEN',10),('000660','CLOSED',0)])
            runner=SimpleNamespace(store=Store(db),
                _paper_interest_held={'005930'},ma=SimpleNamespace(pinned_stocks=lambda:[]),
                gmo=SimpleNamespace(holding_codes=lambda:[]),
                _pinned_stocks=lambda:{'005930':None,'000660':None,'999999':None})
            codes=q._held_rest_display_codes(runner)
            self.assertIn('005930',codes)
            self.assertNotIn('000660',codes)
            self.assertNotIn('999999',codes)
        self.assertIn('held=_held_rest_display_codes(runner)',QUOTE)
        self.assertIn('runner.stop_event.wait(2.0)',QUOTE)

class OriginalPaperCatchupPreserved(unittest.TestCase):
    def test_verified_0b_only_and_original_sell_accounting(self):
        self.assertIn("LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE",STAGE)
        self.assertIn('original_sell(self,c,p,at,qty,price,reason',STAGE)
        self.assertIn("SELECT 1 FROM executions",STAGE)
        self.assertIn('GAP_SECONDS=15.0',STAGE)

if __name__=='__main__':unittest.main()
