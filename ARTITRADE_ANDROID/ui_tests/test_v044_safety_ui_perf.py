"""v044 source-level and persistent-state protections; networkless by design."""
import datetime as dt
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import sys
ROOT=Path(__file__).parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import market_trend_v043 as mt

RUNTIME=(ROOT/"app/src/main/python/market_trend_runtime_v043.py").read_text(encoding="utf-8")
UI=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text(encoding="utf-8")
GRADLE=(ROOT/"app/build.gradle").read_text(encoding="utf-8")

class V044Safeguards(TestCase):
    def test_stop_day_block_is_durable_scoped_and_not_a_legacy_buy_ban(self):
        friday=dt.datetime(2026,11,13,10,5,30,tzinfo=mt.KST)
        tomorrow=friday+dt.timedelta(days=1)
        with TemporaryDirectory() as root:
            journal=mt.TrendJournal(root)
            p=mt.TrendPosition("coin","general","5m","KRW-BTC",friday,10000,1,
                               "2026-11-13T10:10:00+09:00")
            p.apply_sell("MT_STOP_9PCT",1,9000)
            p.__dict__["stop_fill_at"]=friday.isoformat()
            journal.save_position(p,12)
            restarted=mt.TrendJournal(root)
            self.assertTrue(restarted.stop_reentry_blocked("coin","KRW-BTC",friday))
            self.assertFalse(restarted.stop_reentry_blocked("coin","KRW-ETH",friday))
            self.assertFalse(restarted.stop_reentry_blocked("stock","KRW-BTC",friday))
            self.assertFalse(restarted.stop_reentry_blocked("coin","KRW-BTC",tomorrow))
            with restarted.connect() as con:
                self.assertEqual(con.execute("SELECT count(*) FROM stop_reentry_blocks").fetchone()[0],1)
                self.assertEqual(con.execute("SELECT count(*) FROM position_state").fetchone()[0],1)

    def test_fresh_stop_guard_in_runtime_and_no_delete_pending(self):
        self.assertIn("self.journal.stop_reentry_blocked(market,code,now)",RUNTIME)
        self.assertIn('p.__dict__["stop_fill_at"]=now.isoformat()',RUNTIME)
        method=RUNTIME.split("    def _cancel_native_pending(self,market):",1)[1].split(
            "    def observe_stock_tick",1)[0]
        self.assertNotIn("DELETE FROM pending",method)
        self.assertNotIn("UPDATE candidates",method)
        self.assertNotIn("UPDATE orders SET status",method)
        self.assertIn("PENDING_BROKER_RECONCILIATION_REQUIRED",method)

    def test_ui_bounds_and_no_stale_version(self):
        self.assertIn("getPackageManager().getPackageInfo(getPackageName(),0).versionName",UI)
        self.assertNotIn('v0.7.42 · 통합',UI)
        self.assertIn("int netWidth=Math.max(92,Math.min(142,",UI)
        self.assertIn("amount.setGravity(Gravity.CENTER);",UI)
        self.assertIn("symbol.setEllipsize(android.text.TextUtils.TruncateAt.END)",UI)
        self.assertIn("versionCode 51",GRADLE)

    def test_idle_sqlite_writes_throttled_without_changing_exit_rules(self):
        self.assertIn("if now_mono-last>=10.0:",RUNTIME)
        self.assertIn('self._process_holds(market,dt.datetime.now(mt.KST))',RUNTIME)
        self.assertIn("MT_STOP_9PCT",RUNTIME)
