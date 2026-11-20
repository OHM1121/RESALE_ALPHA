"""0.0.17 original-source KRX PENDING recovery tests.

All fixtures synthetic. No user ZIP, device database, authentication,
real order, historical fill, or broker endpoint is used.
"""
import datetime as dt
import queue
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"app/src/main/python"))
import stock_pending_recovery_v017 as recovery


def universe(count):
    return {f"{i:06d}":("KOSPI","FIXTURE") for i in range(1,count+1)}


class NativeProgressContract(unittest.TestCase):
    def test_459_unique_verified_stock_histories_progress_monotonically(self):
        r=SimpleNamespace(krx_market_map=universe(459),market_map=universe(459))
        self.assertEqual(recovery.loading_snapshot(r)["total"],0)
        with patch.object(recovery,"_ORIGINAL_BOOT",lambda self,clock:None):
            recovery._native_boot_recheck(r,None)
        self.assertEqual(recovery.loading_snapshot(r),{
            "done":0,"total":459,
            "source":"NATIVE_KIWOOM_VERIFIED_HISTORY_PER_SYMBOL"})
        results=[]
        for i in range(1,460):
            recovery._record_native_history_success(r,f"{i:06d}")
            results.append(recovery.loading_snapshot(r)["done"])
        self.assertEqual(results,list(range(1,460)))
        recovery._record_native_history_success(r,"000459")
        recovery._record_native_history_success(r,"999999")
        self.assertEqual(recovery.loading_snapshot(r)["done"],459)
        # Dynamic REST/book/health status is NOT fed into this counter.
        r.market_map=universe(300)
        self.assertEqual(recovery.loading_snapshot(r)["total"],459)

    def test_incomplete_original_market_map_is_not_frozen_by_ui_observer(self):
        r=SimpleNamespace(krx_market_map={},market_map=universe(3))
        self.assertEqual(recovery.loading_snapshot(r)["total"],0)
        r.krx_market_map=universe(459)
        with patch.object(recovery,"_ORIGINAL_BOOT",lambda self,clock:None):
            recovery._native_boot_recheck(r,None)
        self.assertEqual(recovery.loading_snapshot(r)["total"],459)


class _MockStore:
    def __init__(self):
        self.messages=[]
        self.lock=threading.RLock()
        self.db=sqlite3.connect(":memory:")
        self.db.row_factory=sqlite3.Row
        self.db.executescript("""
            CREATE TABLE v19_orders(signal_id INTEGER PRIMARY KEY,code TEXT,
                tf TEXT,venue TEXT,status TEXT);
            CREATE TABLE v19_higher_frame_audit14(signal_id INTEGER,
                state TEXT,checked_at TEXT);
            CREATE TABLE bars(code TEXT,frame TEXT,ts TEXT,source TEXT);
        """)
    def connect(self):
        return self.db
    def write_runtime_error(self,e,context):
        self.messages.append((context,type(e).__name__))
    def issue(self,*args,**kwargs):
        self.messages.append((args[0],"ISSUE"))


def runner_with_one_pending(*,blocked=True):
    store=_MockStore()
    code="000001";tf="30m"
    store.db.execute("INSERT INTO v19_orders VALUES(1,?,'30m','KRX','PENDING')",(code,))
    store.db.execute("INSERT INTO v19_higher_frame_audit14 VALUES(1,?,?)",
                     ("BLOCKED" if blocked else "VERIFIED","20261119143200"))
    r=SimpleNamespace(
        stop_event=threading.Event(),
        api=object(),
        market_map=universe(1),
        krx_market_map=universe(1),
        _frame_repair_lock=threading.Lock(),
        _frame_repair_queue=queue.PriorityQueue(),
        _frame_repair_queued={(code,tf)},
        _frame_repair_retry={},
        _frame_repair_last_attempt={},
        _frame_repair_seq=1,
        _minute_repair_queue=queue.Queue(),
        _tick_queue=queue.Queue(),
        store=store,
        engine=SimpleNamespace(store=store),
        _ledger_changed=lambda:None,
        _queue_pipeline_event=lambda *a:None,
    )
    r._frame_repair_queue.put((0,1,code,tf))
    return r


class OriginalNativeRetryBoundary(unittest.TestCase):
    def test_dns_failure_is_retried_without_a_new_order_or_fake_execution(self):
        runner=runner_with_one_pending()
        def outage(*args,**kwargs):
            runner.stop_event.set()
            raise ConnectionError("fixture DNS unavailable")
        current=dt.datetime(2026,11,19,14,31,tzinfo=dt.timezone(
            dt.timedelta(hours=9)))
        with patch("d_storage.now_kst",return_value=current),\
             patch("d_multiframe_recovery_fix14.recover_frame",side_effect=outage):
            recovery._reliable_original_frame_loop(runner)
        self.assertIn(("000001","30m"),runner._frame_repair_retry)
        self.assertEqual(runner.store.db.execute(
            "SELECT COUNT(*) FROM v19_orders WHERE status='PENDING'").fetchone()[0],1)
        self.assertEqual(runner.store.db.execute(
            "SELECT state FROM v19_higher_frame_audit14").fetchone()[0],"BLOCKED")
        self.assertTrue(any(x[1]=="ConnectionError" for x in runner.store.messages))

    def test_original_rest_success_cannot_drop_a_still_blocked_pending_audit(self):
        runner=runner_with_one_pending()
        def partially_verified(*args,**kwargs):
            runner.stop_event.set()
            return {"state":"VERIFIED_RESTORED","new_pending":0,
                    "expired_pending":0,"selected":0}
        current=dt.datetime(2026,11,19,14,31,tzinfo=dt.timezone(
            dt.timedelta(hours=9)))
        with patch("d_storage.now_kst",return_value=current),\
             patch("d_multiframe_recovery_fix14.recover_frame",
                   side_effect=partially_verified):
            recovery._reliable_original_frame_loop(runner)
        self.assertIn(("000001","30m"),runner._frame_repair_retry)
        self.assertEqual(runner.store.db.execute(
            "SELECT status FROM v19_orders").fetchone()[0],"PENDING")
        self.assertTrue(any(x[0]=="V017_PENDING_AUDIT_NOT_VERIFIED"
                            for x in runner.store.messages))

    def test_native_verified_parent_cannot_be_reblocked_by_global_checkpoint(self):
        r=runner_with_one_pending(blocked=False)
        expected="20261119140000"
        r.store.db.execute("INSERT INTO bars VALUES(?,?,?,?)",
                           ("000001","30m",expected,"FIX15_VERIFIED_KIWOOM_REST"))
        dt_now=dt.datetime(2026,11,19,14,31,tzinfo=dt.timezone(
            dt.timedelta(hours=9)))
        calls=[]
        def original(*args):
            calls.append(args)
            return 1
        with patch.object(recovery,"_ORIGINAL_GUARD",original):
            self.assertEqual(recovery._guard_after_verified_native_parent(
                r.engine,"000001","30m",dt_now),0)
            self.assertFalse(calls)
            r.store.db.execute(
                "UPDATE v19_higher_frame_audit14 SET state='BLOCKED'")
            self.assertEqual(recovery._guard_after_verified_native_parent(
                r.engine,"000001","30m",dt_now),1)
            self.assertEqual(len(calls),1)


if __name__=="__main__":
    unittest.main(verbosity=2)
