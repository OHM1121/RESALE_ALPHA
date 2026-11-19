"""0.0.7 stock error original data -> 0.0.9 regression; no user ledger data.

Synthetic 2026-11-19 delayed KRX and ordinary-date reference coverage,
DNS failure shared cooldown and MA's latest-per-symbol bounded queue.
"""
import datetime as dt
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app/src/main/python"))

import artitrade_history_dns_circuit_v009 as dns


class NetworkCircuit(unittest.TestCase):
    def test_shared_dns_pause_no_forged_response(self):
        now=[100.0]
        g=dns.HistoryChartCooldown(clock=lambda:now[0])
        self.assertEqual(g.allow(),(True,0.0))
        d=g.failure(RuntimeError("NameResolutionError: Failed to resolve api.kiwoom.com"))
        self.assertAlmostEqual(d,20.)
        self.assertEqual(g.allow(),(False,20.))
        now[0]+=21
        self.assertTrue(g.allow()[0])
        self.assertEqual(g.failure(RuntimeError("gaierror: No address associated with hostname")),40.)
        self.assertFalse(g.allow()[0])
        g.success()
        self.assertEqual(g.allow(),(True,0.0))

    def test_other_logic_errors_do_not_disable_chart_recovery(self):
        g=dns.HistoryChartCooldown(clock=lambda:50.)
        self.assertEqual(g.failure(ValueError("invalid stock candlestick")),0.)
        self.assertTrue(g.allow()[0])


class SpecialKRXNative(unittest.TestCase):
    def test_no_ghost_0900_parent_on_20261119(self):
        from d_engine import is_krx_regular_reference,bar_end
        from d_multiframe_recovery_fix14 import expected_last_parent
        self.assertFalse(is_krx_regular_reference("20261119090000","5m"))
        self.assertTrue(is_krx_regular_reference("20261119100000","5m"))
        self.assertTrue(is_krx_regular_reference("20261119161500","5m"))
        self.assertFalse(is_krx_regular_reference("20261119162500","5m"))
        self.assertIsNone(expected_last_parent("20261119095900","5m"))
        self.assertIsNone(expected_last_parent("20261119100400","5m"))
        self.assertEqual(expected_last_parent("20261119100500","5m"),"20261119100000")
        self.assertEqual(expected_last_parent("20261119163000","5m"),"20261119161500")
        self.assertEqual(expected_last_parent("20261119163000","60m"),"20261119160000")
        self.assertIsNone(expected_last_parent("20261119162900","day"))
        self.assertEqual(expected_last_parent("20261119163000","day"),"20261119000000")
        self.assertEqual(bar_end("20261119000000","day"),"20261119163000")
        self.assertEqual(bar_end("20261119160000","60m"),"20261119163000")

    def test_ordinary_krx_open_unmodified(self):
        from d_engine import is_krx_regular_reference,bar_end
        from d_multiframe_recovery_fix14 import expected_last_parent
        self.assertTrue(is_krx_regular_reference("20261118090000","5m"))
        self.assertFalse(is_krx_regular_reference("20261118085000","5m"))
        self.assertTrue(is_krx_regular_reference("20261118151500","5m"))
        self.assertFalse(is_krx_regular_reference("20261118152500","5m"))
        self.assertEqual(expected_last_parent("20261118090500","5m"),"20261118090000")
        self.assertEqual(bar_end("20261118000000","day"),"20261118153000")

    def test_special_krx_5m_session_continuity_uses_1000_anchor(self):
        from d_engine import Candle,complete_session_coverage
        stamps=[f"2026111910{i:02d}00" for i in (0,5,10,15)]
        candles=[Candle(t,100,101,99,100,500) for t in stamps]
        self.assertEqual(complete_session_coverage(candles,stamps[-1],"5m"),(True,[]))
        missing=candles[:2]+candles[3:]
        result=complete_session_coverage(missing,stamps[-1],"5m")
        self.assertFalse(result[0])
        self.assertEqual(result[1],["20261119101000"])
        self.assertNotIn("20261119090000",result[1])

    def test_no_preopen_ghost_minute_repair(self):
        from d_minute_repair_fast import missing_regular_minutes
        self.assertEqual(missing_regular_minutes("20261119095000","20261119100600"),[])
        repaired=missing_regular_minutes("20261119100000","20261119100300",limit=6)
        self.assertEqual(repaired,["20261119100100","20261119100200"])


class GMODepthReconnect(unittest.TestCase):
    def test_0d_missing_can_re_register_after_special_krx_open(self):
        import asyncio
        import threading
        import d_live
        from types import SimpleNamespace
        from krx_session_v041 import KST
        class FakeGmo:
            _lock=threading.RLock()
            last_book={}
            def priority_watchlist(self,at):return set()
            def active_buy_watchlist(self,at):return set()
            def watchlist(self):return {'000680'}
        class WS:
            sent=[]
            async def send(self,message):
                self.sent.append(__import__("json").loads(message))
        host=object.__new__(d_live.LivePaperRunner)
        host.gmo=FakeGmo()
        host._subscribed_groups={'1':['000680','005930']}
        host._gmo_depth_repair_last_check=-1e9
        host._gmo_depth_repair_at={}
        recorded=[]
        host.store=SimpleNamespace(issue=lambda *a,**kw:recorded.append((a,kw)))
        ws=WS()
        at=dt.datetime(2026,11,19,11,25,0,tzinfo=KST)
        renewed=asyncio.run(host._repair_gmo_depth_on_existing_ws(ws,at))
        self.assertEqual(renewed,['000680'])
        self.assertEqual(ws.sent[0]['data'][0],
                         {'item':['000680'],'type':['0D']})
        self.assertEqual(ws.sent[0]['trnm'],'REG')
        self.assertEqual(ws.sent[0]['refresh'],'1')
        self.assertEqual(len(recorded),1)
        # Still no actual 0D; do not infer broker order or executable depth.
        self.assertNotIn('order',ws.sent[0])
        self.assertEqual(asyncio.run(host._repair_gmo_depth_on_existing_ws(
            ws,dt.datetime(2026,11,19,16,30,tzinfo=KST))),[])

    def test_gmo_priority_opening_tracks_actual_day_not_fixed_0900(self):
        import d_gmo_runtime
        cls=next(getattr(d_gmo_runtime,n) for n in dir(d_gmo_runtime)
                 if isinstance(getattr(d_gmo_runtime,n),type)
                 and hasattr(getattr(d_gmo_runtime,n),'_priority_phase'))
        owner=object.__new__(cls)
        KST=dt.timezone(dt.timedelta(hours=9))
        def at(date,h,m):
            return dt.datetime.fromisoformat(
                f"{date}T{h:02d}:{m:02d}:00+09:00")
        self.assertEqual(owner._priority_phase(at("2026-11-19",9,50)),
                         'KRX_PREOPEN_ARM')
        self.assertEqual(owner._priority_phase(at("2026-11-19",10,5)),
                         'KRX_GMO_PRIORITY')
        self.assertEqual(owner._priority_phase(at("2026-11-19",10,31)),'')
        self.assertEqual(owner._priority_phase(at("2026-11-18",9,5)),
                         'KRX_GMO_PRIORITY')


class MAQueueSafety(unittest.TestCase):
    def test_1001_repeated_genuine_ticks_do_not_fill_unique_queue(self):
        from ma_v067_stock_runtime import _LatestVerifiedMAQueue
        q=_LatestVerifiedMAQueue(maxsize=2)
        for i in range(1001):
            tick=("000680","KOSPI","L","20261119100000",2405,1,
                  f"2026-11-19T10:00:59.{i:06d}+09:00","KRX",i)
            self.assertTrue(q.offer(tick))
        self.assertEqual(q.qsize(),1)
        self.assertEqual(q.get()[8],1000)
        q.task_done()

if __name__=="__main__":
    unittest.main()
