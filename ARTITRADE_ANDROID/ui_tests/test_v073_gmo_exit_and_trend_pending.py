"""v073 evidence-based chain regression: real 0B -> GMO -> sell intent,
and genuine MarketTrend RSI watch -> UI pending. NO real broker use.
"""
from __future__ import annotations

import base64,datetime as dt,io,json,os,sys,tempfile,threading,time,unittest,zipfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/'app/src/main/assets/strategy_v070'
KST=dt.timezone(dt.timedelta(hours=9))

class GmoLivePath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        data=base64.b64decode(''.join((ASSETS/f'part_{i:02d}.txt').read_text() for i in range(22)))
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(cls.tmp.name)
            cls.gmo_source=z.read('d_gmo_runtime.py').decode('utf-8')
            cls.feed_source=z.read('d_live.py').decode('utf-8')
        sys.path.insert(0,cls.tmp.name)
        from projectgmo.engine import Engine,Position,Quote
        from d_gmo_runtime import GMOService
        cls.Engine=Engine;cls.Position=Position;cls.Quote=Quote
        cls.GMOService=GMOService

    @classmethod
    def tearDownClass(cls):
        if cls.tmp.name in sys.path:sys.path.remove(cls.tmp.name)
        cls.tmp.cleanup()

    def _make(self,price=101.1):
        svc=object.__new__(self.GMOService)
        svc._lock=threading.RLock()
        svc.root=Path(self.tmp.name)
        svc.engine=self.Engine()
        svc.engine.positions['089890']=self.Position(
            '089890','테스트',8,10,100.0,'2026-11-13',
            'SHORT',800.0,stage='PROTECTED',first_profit_triggered=True,
            first_profit_target=.04,exit_stage=1,first_filled_qty=2,
            protected_peak=104.0)
        svc._candidate_events_by_code={}
        svc.opens={}
        svc._recent_prices={}
        svc._prearmed_day='';svc._prearmed_krx=set()
        svc._log=lambda *a,**k:None
        svc._persist=lambda *a,**k:None
        svc._update_candidate_status=lambda *a,**k:None
        svc._track_candle=lambda *a,**k:None
        now=dt.datetime.now(KST)
        q=self.Quote('089890',now.isoformat(timespec='seconds'),2,
                     float(price),int(price+1),int(price),100,100,'KRX',0)
        svc._quote=lambda tick:q
        tick=('089890','KOSDAQ','테스트',
              now.strftime('%Y%m%d%H%M')+'00',float(price),12,
              now.isoformat(),'KRX',now.isoformat(),int(price+1),int(price))
        return svc,tick

    def test_production_service_calls_original_exit_engine(self):
        self.assertIn('self.engine.on_quote(q)',self.gmo_source)
        self.assertLess(self.gmo_source.index('self.engine.on_quote(q)'),
                        self.gmo_source.index('self.engine.paper_match(q)'))
        svc,tick=self._make()
        # Real-mode mock means no PAPER matching and does NOT supply a broker;
        # we assert the signal was generated, never a fictitious fill.
        with patch.dict(os.environ,{'ARTITRADE_REAL_MODE':'1'}):
            self.GMOService.on_live_tick(svc,tick)
        sells=[o for o in svc.engine.orders.values() if o.side=='SELL']
        self.assertEqual(len(sells),1)
        self.assertEqual(sells[0].purpose,'ENTRY_PLUS2_FLOOR')
        self.assertEqual(sells[0].requested,8)
        self.assertEqual(len(svc.engine.fills),0)

    def test_old_trade_must_never_create_a_retroactive_exit(self):
        svc,tick=self._make()
        past=dt.datetime.now(KST)-dt.timedelta(seconds=8)
        old=list(tick)
        old[6]=past.isoformat();old[8]=past.isoformat()
        with patch.dict(os.environ,{'ARTITRADE_REAL_MODE':'1'}):
            self.GMOService.on_live_tick(svc,tuple(old))
        self.assertFalse([o for o in svc.engine.orders.values() if o.side=='SELL'])

    def test_all_existing_gmo_holds_route_to_priority_worker(self):
        self.assertIn('def holding_codes(self)',self.gmo_source)
        self.assertIn('self.gmo.holding_codes()',self.feed_source)
        self.assertIn('self.gmo.on_live_tick(tick)',self.feed_source)
        self.assertIn('exchange_stamp_is_fresh(tick[8],tick[6])',self.feed_source)
        self.assertNotIn('V071_KRX_0B_SILENT_45S_RECONNECT',self.feed_source)

class TrendPendingVisibility(unittest.TestCase):
    def test_verified_watch_appears_in_pending_without_broker_order(self):
        root=ROOT/'app/src/main/python'
        sys.path.insert(0,str(root))
        try:
            from market_trend_runtime_v043 import Coordinator
            with tempfile.TemporaryDirectory() as work:
                class FakeRt:
                    def __init__(self):
                        self.root=Path(work);self.stock_runner=None
                        self.coin_runner=None;self._halted={}
                    def live_enabled(self,market):return False
                engine=Coordinator(FakeRt())
                engine.watch.observe('coin','KRW-BTC','5m',
                    '2026-11-16T11:00:00+09:00',32.2,False,price=100000)
                packet=json.loads(engine.attach_history(json.dumps({
                    'stock':{},'coin':{}})))
                watched=packet['market_trend_watch']['coin']
                pending=packet['coin']['trend']['ledger']['pending']
                self.assertEqual(len(watched),1)
                self.assertEqual(len(pending),1)
                self.assertEqual(pending[0]['code'],'KRW-BTC')
                self.assertEqual(pending[0]['status'],'WATCH_PENDING')
                self.assertFalse(pending[0]['external_order_pending'])
                self.assertEqual(packet['coin']['trend']['ledger']['executions'],[])
        finally:
            sys.path.remove(str(root))

if __name__=='__main__':
    unittest.main(verbosity=2)
