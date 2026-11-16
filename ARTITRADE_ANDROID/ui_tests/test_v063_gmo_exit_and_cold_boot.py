"""Regression tests for an owner-requested 1% GMO floor and true cold start.

Uses only the SHA-pinned packaged core and synthetic quotes. No broker,
network, or real account paths are invoked.
"""
from __future__ import annotations
import base64
import io
import re
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/"app/src/main/assets/strategy_v070"
UI=ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java"

class V063GmoSafeguards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        parts=sorted(ASSETS.glob("part_*.txt"))
        assert len(parts)==22
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(
                "".join(p.read_text(encoding="ascii") for p in parts)))) as z:
            z.extractall(cls.tmp.name)
        sys.path.insert(0,cls.tmp.name)
        from projectgmo.engine import Engine,Position,Quote
        cls.Engine,cls.Position,cls.Quote=Engine,Position,Quote

    @classmethod
    def tearDownClass(cls):
        if cls.tmp.name in sys.path:sys.path.remove(cls.tmp.name)
        cls.tmp.cleanup()

    def position(self,stage="PROTECTED"):
        return self.Position("089890","제주반도체",8,10,100.0,"2026-11-13",
                             "SHORT",800.0,stage=stage,first_profit_triggered=True,
                             first_profit_target=0.04,exit_stage=(2 if stage=="TRAILING_FINAL" else 1),
                             first_filled_qty=2,second_filled_qty=(4 if stage=="TRAILING_FINAL" else 0),
                             protected_peak=104.0 if stage=="PROTECTED" else 108.0)

    def tick(self,price,second=1):
        return SimpleNamespace(code="089890",at=f"2026-11-16T10:30:{second:02d}+09:00",
                               trading_day_index=2,last=price,bid=price,ask=price,
                               bid_size=200,ask_size=200,venue="KRX",receive_delay_seconds=0)

    def test_new_gmo_accounts_default_to_entry_plus_one_percent(self):
        self.assertAlmostEqual(self.Engine().cfg.entry_profit_floor,0.01)

    def test_existing_gmo_state_policy_migrates_without_erasing_fills(self):
        old=self.Engine()
        old.positions["089890"]=self.position()
        old.fills.append({"code":"089890","qty":2,"price":104,"purpose":"TP4_FIRST_20PCT"})
        original=old.export_state()
        upgraded=self.Engine.from_state(original)
        self.assertAlmostEqual(upgraded.cfg.entry_profit_floor,0.01)
        self.assertEqual(upgraded.positions["089890"].qty,8)
        self.assertEqual(upgraded.fills[0]["qty"],2)

    def test_first_target_protection_waits_until_buy_plus_one(self):
        eng=self.Engine()
        eng.positions["089890"]=self.position()
        self.assertEqual(eng.on_quote(self.tick(101.5,1)),[])
        intents=eng.on_quote(self.tick(100.9,2))
        self.assertEqual(len(intents),1)
        self.assertEqual(intents[0].purpose,"ENTRY_PLUS1_FLOOR")
        self.assertEqual(intents[0].requested,8)

    def test_first_target_exit_preempts_unfilled_tp2_intent_safely(self):
        eng=self.Engine()
        p=self.position()
        eng.positions[p.code]=p
        pending=eng._exit(p,"TP2_AFTER_FIRST_PLUS3_40PCT",4,104.0,
                          "2026-11-16T10:30:00+09:00")
        self.assertIsNotNone(pending)
        self.assertEqual(eng.on_quote(self.tick(100.9,2)),[])
        self.assertEqual(pending.state,"CANCEL_REQUESTED")
        # Do not place duplicate sell before broker/paper cancellation ack.
        self.assertEqual(p.pending_sell_id,pending.id)

    def test_second_target_already_protects_first_target_and_sells_all(self):
        eng=self.Engine()
        p=self.position("TRAILING_FINAL")
        eng.positions[p.code]=p
        intents=eng.on_quote(self.tick(104.0,1))
        self.assertEqual(len(intents),1)
        self.assertEqual(intents[0].purpose,"THIRD_TRAIL_EXIT")
        self.assertEqual(intents[0].requested,p.qty)

    def test_stale_quote_does_not_fabricate_protection_fill(self):
        eng=self.Engine()
        eng.positions["089890"]=self.position("TRAILING_FINAL")
        q=self.tick(98.0,1);q.receive_delay_seconds=4
        self.assertEqual(eng.on_quote(q),[])
        self.assertEqual(len(eng.fills),0)

    def test_ui_does_not_skip_true_cold_boot_merely_due_to_old_pid(self):
        java=UI.read_text(encoding="utf-8")
        start=java.find("private boolean shouldRestoreShellImmediately(Bundle saved)")
        end=java.find("    private void preloadWatchdog()",start)
        self.assertGreaterEqual(start,0)
        self.assertGreater(end,start)
        body=java[start:end]
        self.assertIn("isRecentBackgroundHeartbeat()",body)
        self.assertNotIn("priorUiSessionKnown",body)

    def test_kiwoom_wait_dialog_exposes_feed_freshness_and_true_order_mode(self):
        java=UI.read_text(encoding="utf-8")
        start=java.find("private void showKiwoomFeedDiagnosis()")
        end=java.find("    private void addMini(",start)
        self.assertGreaterEqual(start,0)
        self.assertGreater(end,start)
        method=java[start:end]
        self.assertIn('last_frame_ms',method)
        self.assertIn('live_enabled',method)
        self.assertIn('mserver_auth',method)

if __name__=="__main__":unittest.main(verbosity=2)
