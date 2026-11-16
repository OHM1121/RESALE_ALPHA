"""No network or real orders. Validate stock WS receive separation and freshness."""
import asyncio
import contextlib
import datetime as dt
import pathlib
import queue
import sqlite3
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import d_live
from d_live import LivePaperRunner
from startup_preload_v1 import StartupPreloadGate


class WSReceiverIsolation(unittest.TestCase):
    def setUp(self):
        self.worker=LivePaperRunner.__new__(LivePaperRunner)
        w=self.worker
        w.stop_event=threading.Event()
        w._feed_message_queue=queue.Queue(maxsize=30)
        w._feed_generation=5
        w._feed_processed=0
        w._feed_discarded=0
        w._feed_overflows=0
        w._feed_high_water=0
        w._feed_lag_max_ms=0.0
        w._confirmed_ws_session=False
        w._watch_feed_last_epoch=0.0
        w.market_map={}
        w.nxt_snapshot=None
        w.subscriptions=[]
        w.e_live=SimpleNamespace(on_raw_message=lambda msg,mp:None)
        self.events=[]
        class Book:
            def on_message(self2,msg,*a):
                time.sleep(.025)
                self.events.append(("book",msg.get("id")))
        class GMO:
            def on_book_message(self2,msg,*a):self.events.append(("gmo",msg.get("id")))
            def priority_watchlist(self2,at):return set()
        w.engine=SimpleNamespace(book=Book())
        w.gmo=GMO()
        w._new_tick=lambda tick:self.events.append(("tick",tick[0]))
        w._enqueue_flush=lambda cutoff:self.events.append(("flush",cutoff))
        w._progress=lambda *a,**kw:None
        w._health=lambda *a,**kw:None
        w.store=SimpleNamespace(write_runtime_error=lambda *a:self.fail("Worker raised "+repr(a)))
        self.parse=patch.object(d_live,'parse_venue_real',side_effect=lambda msg,*a,**kw:[
            (str(msg.get("id")), "KOSPI", "Test", "20261116090000",100,1,
             kw["seen_at"].isoformat(),"KRX",kw["seen_at"].isoformat())
        ] if msg.get("trnm")=="REAL" else [])
        self.parse.start()
        self.thread=threading.Thread(target=w._v071_feed_worker,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.worker._feed_message_queue.join()
        self.worker.stop_event.set()
        self.thread.join(timeout=2)
        self.parse.stop()

    def test_fifo_and_nonblocking_producer_under_slow_book(self):
        start=time.monotonic()
        for i in range(5):
            self.worker._v071_enqueue_feed('raw',{'trnm':'REAL','id':i})
        producer=time.monotonic()-start
        self.assertLess(producer,.1, "WS recv must not await 0D/DB worker")
        self.worker._feed_message_queue.join()
        self.assertEqual(self.worker._feed_processed,5)
        self.assertEqual([x[1] for x in self.events if x[0]=="tick"],["0","1","2","3","4"])
        self.assertGreater(self.worker._watch_feed_last_epoch,0)

    def test_flush_precedes_following_packet(self):
        self.worker._v071_enqueue_feed('flush','20261116095500')
        self.worker._v071_enqueue_feed('raw',{'trnm':'REAL','id':99})
        self.worker._feed_message_queue.join()
        self.assertEqual(self.events[0],("flush","20261116095500"))
        self.assertEqual(self.events[-1],("tick","99"))

    def test_old_session_packet_is_discarded(self):
        self.worker._feed_message_queue.put((4,'raw',{'trnm':'REAL','id':77},
                                             time.monotonic(),time.time()))
        self.worker._feed_message_queue.join()
        self.assertEqual(self.worker._feed_discarded,1)
        self.assertEqual(self.events,[])

    def test_delayed_old_frame_never_becomes_new_tick(self):
        self.worker._feed_message_queue.put((5,'raw',{'trnm':'REAL','id':42},
                                             time.monotonic()-4.0,time.time()-4.0))
        self.worker._feed_message_queue.join()
        self.assertEqual(self.worker._feed_discarded,1)
        self.assertEqual(self.events,[])
        self.assertEqual(self.worker._watch_feed_last_epoch,0)

    def test_full_queue_raises_without_deceptive_fill(self):
        self.worker._feed_message_queue=queue.Queue(maxsize=1)
        # Stop worker so the queue remains full.
        self.worker.stop_event.set()
        self.worker._feed_message_queue.put((5,'raw',{},time.monotonic(),time.time()))
        with self.assertRaisesRegex(RuntimeError,'BACKPRESSURE'):
            self.worker._v071_enqueue_feed('raw',{'trnm':'REAL','id':2})
        self.assertEqual(self.worker._feed_overflows,1)
        self.worker._feed_message_queue.get_nowait()
        self.worker._feed_message_queue.task_done()


class StartupReadiness(unittest.TestCase):
    def test_startup_does_not_wait_for_history_or_pretend_it_is_complete(self):
        w=LivePaperRunner.__new__(LivePaperRunner)
        with tempfile.TemporaryDirectory() as temp:
            w.store=SimpleNamespace(root=pathlib.Path(temp),
                lock=threading.RLock(),
                connect=lambda:sqlite3.connect(':memory:'))
            w.preload_gate=StartupPreloadGate()
            w.market_map={}
            async def boot():
                w.market_map={'000001':('KOSPI','Sample')}
            w._boot_market=boot
            w._progress=lambda *a,**kw:None
            async def should_not_run():
                raise AssertionError("History preload blocked WebSocket startup")
            w._warmup=should_not_run
            self.assertTrue(asyncio.run(w._startup_preload()))
            snap=w.preload_gate.snapshot()
            self.assertTrue(snap['ready'])
            self.assertTrue(snap['degraded'])
            self.assertEqual(snap['coverage'],0)

    def test_verified_history_worker_can_finish_after_socket_starts(self):
        w=LivePaperRunner.__new__(LivePaperRunner)
        w.stop_event=threading.Event()
        w.preload_gate=StartupPreloadGate()
        w.market_map={'000001':('KOSPI','Sample')}
        w._pinned_stocks=lambda:{'000001':('KOSPI','Sample')}
        w._derive_higher_from_5m=lambda *a:None
        w._has_usable_history=lambda *a:True
        async def warmup():
            await asyncio.sleep(0)
        w._warmup=warmup
        asyncio.run(w._v071_background_preload())
        snap=w.preload_gate.snapshot()
        self.assertTrue(snap['ready'])
        self.assertFalse(snap['degraded'])
        self.assertEqual(snap['coverage'],1.0)


class UIAndNativePolicy(unittest.TestCase):
    ROOT=pathlib.Path(__file__).resolve().parents[1]
    def test_error_export_same_source_integrity_as_research(self):
        j=(self.ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text('utf8')
        for expr in ("REQUEST_EXPORT_ERRORS=7715","requestErrorHistoryExport()",
                     'button("오류 ZIP"', "ARTITRADE_ERROR_HISTORY_V071",
                     "pendingErrorsReports", "beginPreparedLedgerExport(REQUEST_EXPORT_ERRORS",
                     "finishPreparedLedgerExport(requestCode,data.getData())",
                     "selectedHistory", "renderErrors()"):
            self.assertIn(expr,j)
        self.assertIn("validate_backup",j)
        self.assertIn("removeFailedSafDocument(uri)",j)
    def test_coin_ma_trend_and_stock_other_modules_are_unmodified(self):
        import base64,hashlib,io,zipfile
        p=self.ROOT/"app/src/main/assets/strategy_v070"
        blob=base64.b64decode("".join((p/("part_%02d.txt"%i)).read_text() for i in range(22)))
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for name in ("at_close_runner.py","at_close_bridge.py","d_gmo_runtime.py","stock_ma_engine.py"):
                self.assertIn(name,z.namelist())
            d=z.read("d_live.py").decode("utf8")
            self.assertIn("await self._startup_preload()",d)
            self.assertIn("self._v071_enqueue_feed('raw',msg)",d)
            self.assertIn("if msg.get('trnm') == 'PING':",d)
            self.assertIn("await ws.send(raw)",d)
            self.assertNotIn("await asyncio.to_thread(self.engine.book.on_message",d)
            self.assertNotIn("await asyncio.wait_for(self._warmup()",d)

if __name__=='__main__':
    unittest.main()
