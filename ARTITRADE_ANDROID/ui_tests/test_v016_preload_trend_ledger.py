"""0.0.16 original-market-data and independently recorded MT54 trend wallet audit.

Synthetic fixtures; no live account or user device data. Verifies original
SQLite BUY and SELL receipts, cost, holding %, closed returns and id linkage.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
import unittest

ROOT=Path(__file__).resolve().parents[1]
PY=ROOT/"app/src/main/python"
sys.path.insert(0,str(PY))
from preload_audit_v016 import verify_window,audit,SCHEMA
from market_trend_v043 import KST
from market_trend_wallet_v054 import TrendWalletV054

TIME=dt.datetime(2026,11,19,14,31,tzinfo=KST)
SEC=1800

def official_bars(last=TIME-dt.timedelta(minutes=1),missing=False):
    bars=[]
    for n in range(21):
        end=last-dt.timedelta(seconds=SEC*(20-n))
        if missing and n==12:
            end-=dt.timedelta(seconds=SEC)
        bars.append({"end":end,"close":100,"high":101,"low":99,
            "source":"UPBIT_CLOSED"})
    return bars

class NativePreloadProof(TestCase):
    def test_original_upbit_closed_history_is_not_falsely_missing(self):
        result=verify_window(official_bars(),"coin","30m",TIME)
        self.assertEqual(result["status"],"VERIFIED_COMPLETE")
        self.assertEqual(result["count"],21)
        self.assertEqual(result["gaps"],0)

    def test_missing_middle_candle_never_claims_complete(self):
        self.assertIn(verify_window(official_bars(missing=True),"coin","30m",TIME)["status"],
                      ("OFFICIAL_CANDLE_GAP","MISSING_OFFICIAL_CANDLES"))

    def test_old_contiguous_history_is_still_missing_newest_official_bar(self):
        old=official_bars(last=TIME-dt.timedelta(minutes=31))
        r=verify_window(old,"coin","30m",TIME)
        self.assertEqual(r["status"],"LATEST_OFFICIAL_CANDLE_MISSING")
        self.assertIn("expected_last_closed",r)

    def test_unknown_provenance_and_incomplete_data_do_not_validate(self):
        missing=official_bars()
        missing[10]["source"]="REST_INVENTED"
        self.assertEqual(verify_window(missing,"coin","30m",TIME)["status"],
                         "MISSING_OFFICIAL_CANDLES")
        missing[10]["source"]="UPBIT_CLOSED"
        missing[10]["low"]=102
        self.assertEqual(verify_window(missing,"coin","30m",TIME)["status"],
                         "MISSING_OFFICIAL_CANDLES")

    def test_boot_check_is_readonly_and_missing_0d_is_visible(self):
        stage={"startup_preload_v081":{
            "engine_initialized":True,
            "stock":{"keys_present":True,"session_required":True,
                     "ws_ready":True,"subscriptions":5,
                     "preload":{"ready":True}},
            "coin":{"ws_ready":True,"ws_supervisor_running":True,
                    "preload":{"ready":True}}}}
        stage["market_feed_health_v004"]={
            "stock":{"fresh_0b_count":4,"fresh_0d_count":0},
            "coin":{"last_ws_quote_age_sec":0.4,"requested_book_count":2,
                    "active_book_count":1}}
        p=audit(SimpleNamespace(market_trend=None),stage,TIME)
        self.assertEqual(p["schema"],SCHEMA)
        self.assertEqual(p["stock"]["trade_0b"],True)
        self.assertEqual(p["stock"]["book_0d"],False)
        self.assertEqual(p["coin"]["book"],False)
        self.assertIn("STOCK_VERIFIED_0D",p["failed_check_names"])
        self.assertIn("COIN_VERIFIED_BOOK",p["failed_check_names"])
        self.assertFalse(p["ready"])
        self.assertLess(p["percent"],100)
        self.assertTrue(p["all_missing_recovery_owned_by_original_workers"])
        self.assertFalse(p["new_exchange_connection"])
        self.assertFalse(p["synthetic_fills"])

    def test_real_coin_trend_histories_after_existing_preload_are_counted(self):
        from threading import RLock
        trend=SimpleNamespace(
          lock=RLock(),
          _candidates={"coin":{"KRW-SIMCOIN":("CRYPTO","KRW-SIMCOIN")},
                       "stock":{}},
          _positions={},
          _history={("coin","KRW-SIMCOIN","30m"):official_bars()})
        b={"startup_preload_v081":{
           "engine_initialized":True,
           "stock":{"keys_present":False,"session_required":False,
                    "preload":{"ready":True}},
           "coin":{"ws_ready":True,"ws_supervisor_running":True,
                   "preload":{"ready":True}}},
           "market_feed_health_v004":{
              "stock":{"fresh_0b_count":0,"fresh_0d_count":0},
              "coin":{"last_ws_quote_age_sec":0.2,
                      "requested_book_count":1,"active_book_count":1}}}
        p=audit(SimpleNamespace(market_trend=trend),b,TIME)
        self.assertEqual(p["official_history"]["coin"]["required"],3)
        self.assertEqual(p["official_history"]["coin"]["ready"],1)
        self.assertEqual(p["official_history"]["coin"]["short"],2)
        self.assertLess(p["percent"],100)

    def test_android_has_real_progress_eta_and_never_restarts_second_feed(self):
        java=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text("utf8")
        py=(PY/"android_runtime_v078.py").read_text("utf8")
        self.assertIn("private void updateVerifiedStartupProgress(",java)
        self.assertIn("startupPercent.setText",java)
        self.assertIn("startupEta.setText",java)
        self.assertIn("startupMissingCause",java)
        self.assertIn("return false;",java.split("private boolean shouldRestoreShellImmediately(",1)[1].split("private void preloadWatchdog",1)[0])
        self.assertIn("preloadExitNote",java)
        self.assertIn("from preload_audit_v016 import audit as _v016_audit",py)
        self.assertIn("from market_feed_health_v004 import snapshot as _v016_feed",py)
        auditor=(PY/"preload_audit_v016.py").read_text("utf8")
        for forbidden in ("requests.get(","WebSocket(","sqlite3.connect(","c.execute(","self.rt.buy(",):
            self.assertNotIn(forbidden,auditor)

class PaperRT:
    def __init__(self,root):
        self.root=Path(root)
        self._halted={}
        self.stock_runner=SimpleNamespace(market_map={"005930":("KOSPI","EXAMPLE")})
        self.upbit=None
        self.kiwoom=None
    def live_enabled(self,market):return False
    def halt_market(self,*args):raise AssertionError("PAPER must not broker-send")

class TrendSourceCost(TestCase):
    def setUp(self):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.rt=PaperRT(temp.name)
        self.wallet=TrendWalletV054(self.rt)
        self.now=TIME
    def _buy(self,market,tf,symbol,ask=10000):
        p,status=self.wallet.buy(market,tf,symbol,symbol,ask,500,self.now,
            self.now.isoformat(),24.3)
        self.assertEqual(status,"BOUGHT")
        self.assertIsNotNone(p)
        return p

    def test_coin_native_buy_and_sell_unit_cost_pct_and_no_fake_fills(self):
        p=self._buy("coin","30m","KRW-SIMCOIN")
        before=self.wallet.snapshot("coin")
        self.assertEqual(len(before["executions"]),1)
        hold=next(x for x in before["positions"] if x["id"]==p.position_id)
        buy=next(x for x in before["executions"] if x["side"]=="BUY")
        self.assertEqual(hold["entry_price"],10000)
        self.assertEqual(hold["buy_price"],10000)
        self.assertEqual(hold["last_price"],10000)
        self.assertAlmostEqual(hold["unrealized_pct"],0)
        self.assertEqual(buy["buy_price"],10000)
        self.assertEqual(buy["position_id"],p.position_id)
        self.assertEqual(buy["buy_cost_evidence"],"ORIGINAL_MT54_NATIVE_POSITION_ID_AND_BUY")
        # Read-only screen load never books extra broker orders or changes account funds.
        self.assertEqual(self.wallet.snapshot("coin")["executions"],before["executions"])
        cash=self.wallet.allocation("coin","30m")["cash"]
        self.assertLess(cash,2_000_000)
        sold,status=self.wallet.sell(p,11000,self.now+dt.timedelta(seconds=5),
            "USER_SIM_MANUAL:regression-v016",bid_size=1000,manual_fraction=0.5)
        self.assertEqual(status,"SOLD")
        self.assertGreater(sold,0)
        after=self.wallet.snapshot("coin")
        sell=next(x for x in after["executions"] if x["side"]=="SELL")
        self.assertEqual(sell["position_id"],p.position_id)
        self.assertEqual(sell["buy_price"],10000)
        self.assertEqual(sell["entry_price"],10000)
        self.assertEqual(sell["net_profit"],sell["net_pnl"])
        self.assertAlmostEqual(
            sell["realized_pct"],sell["net_profit"]/sell["buy_amount"]*100,places=5)
        holding=next(x for x in after["positions"] if x["id"]==p.position_id)
        self.assertGreater(holding["qty"],0)
        self.assertEqual(holding["entry_price"],10000)
        self.assertNotEqual(after["realized_pnl"],0)
        self.assertEqual(after["native_execution_cost_join"],"EXACT_POSITION_ID_READONLY")

    def test_stock_trend_preserves_saved_mark_as_nonlive_and_buy_receipts(self):
        p=self._buy("stock","60m","005930")
        with self.wallet._connect("stock") as c:
            c.execute("UPDATE positions SET mark=11000 WHERE id=?",(p.position_id,))
        snapshot=self.wallet.snapshot("stock")
        held=next(x for x in snapshot["positions"] if x["id"]==p.position_id)
        self.assertEqual(held["valuation_source"],"ORIGINAL_MT54_SAVED_MARK_NOT_LIVE")
        self.assertEqual(held["entry_price"],10000)
        self.assertEqual(held["buy_price"],10000)
        self.assertEqual(held["last_price"],11000)
        self.assertAlmostEqual(held["unrealized_pct"],10.0)
        self.assertGreater(snapshot["unrealized_pnl"],0)
        java=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text("utf8")
        self.assertIn("마켓트렌드 마지막 저장 평가가",java)
        self.assertIn("ORIGINAL_MT54_SAVED_MARK_NOT_LIVE",java)
        self.assertIn("실시간 시세 아님",java)
        self.assertIn('historyEvidenceCell(fields,"마켓트렌드 원본 매수가"',java)
        self.assertIn('historyEvidenceCell(fields,"원본 실현손익"',java)
        self.assertIn('historyEvidenceCell(fields,"원본 매도수익률"',java)

    def test_retired_fiveminute_trend_not_reopened(self):
        p,status=self.wallet.buy("coin","5m","KRW-SIMCOIN","test",10000,100,
            self.now,self.now.isoformat(),25)
        self.assertIsNone(p)
        self.assertEqual(status,"TREND_5M_RETIRED_NO_NEW_BUY")
        self.assertEqual(self.wallet.snapshot("coin")["executions"],[])

    def test_missing_native_fill_position_is_not_linked_by_symbol(self):
        p=self._buy("coin","day","KRW-SAME")
        with self.wallet._connect("coin") as c:
            c.execute("""INSERT INTO executions(event_key,position_id,tf,symbol,
                   at,side,qty,price,fees,tax,net_pnl,cash_after,execution_mode,
                   broker_order_id,reason)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(
                   "synthetic-unlinked-sell",99999,"day","KRW-SAME",
                   self.now.isoformat(),"SELL",1,1000,0,0,0,0,"PAPER","",
                   "TEST_MISSING_OWNER"))
        report=self.wallet.snapshot("coin")
        unknown=next(e for e in report["executions"] if e["position_id"]==99999)
        self.assertEqual(unknown["buy_cost_evidence"],"NATIVE_POSITION_ID_NOT_LINKED")
        self.assertNotIn("buy_price",unknown)
        found=next(e for e in report["executions"] if e["position_id"]==p.position_id)
        self.assertEqual(found["buy_price"],10000)

if __name__=="__main__":
    unittest.main(verbosity=2)
