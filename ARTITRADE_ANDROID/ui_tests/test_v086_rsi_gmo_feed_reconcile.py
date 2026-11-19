"""ARTITRADE v086: first RSI20 downward trigger, source-preserving P&L
and exchange-verified ticker freshness. Never create fictitious orders.
"""
import ast,base64,hashlib,io,math,pathlib,sys,unittest,zipfile
from datetime import datetime,timedelta
BASE=pathlib.Path(__file__).resolve().parents[1]/'app/src/main'
PY=BASE/'python'
sys.path.insert(0,str(PY))
import market_trend_v043 as rules

def py(name):
    return (PY/name).read_text()

def archive_module(name):
    blob=base64.b64decode(''.join(
        (BASE/'assets/strategy_v070'/f'part_{i:02d}.txt').read_text()
        for i in range(22)))
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist())==82
        payload=archive.read(name).decode()
    java=(BASE/'java/com/artitrade/mobile/StrategyRuntimeBridge.java').read_text()
    assert 'CORE_SHA256="'+hashlib.sha256(blob).hexdigest()+'"' in java
    return payload

class V086FirstCrossAndGMO(unittest.TestCase):
    def test_rsi_wilder_main_only_not_display_signal(self):
        src=py('market_trend_v043.py')
        self.assertIn('RSI_PERIOD=20',src)
        self.assertIn('gain=(gain*19+',src)
        self.assertIn('loss=(loss*19+',src)
        self.assertNotIn('rsi_signal_ma9',src)
        # Main Wilder RSI cannot become an arbitrary blue display signal.
        self.assertEqual(rules.rsi20([100.0]*21),50.0)
        self.assertEqual(rules.rsi20(list(range(100,79,-1))),0.0)
        self.assertEqual(rules.rsi20(list(range(100,121))),100.0)

    def test_different_days_already_below_30_cannot_rebuy(self):
        latch=rules.SignalLatch(armed=True,fired=False)
        latch.completed('2026-11-12/day',23)
        self.assertFalse(latch.armed)
        self.assertFalse(latch.offer('2026-11-17/day',19.51))
        latch.completed('2026-11-16/day',22)
        self.assertFalse(latch.offer('2026-11-17/day',19.51))
        # FIRST genuine new cross after a confirmed >30 re-arm.
        latch.completed('2026-11-18/day',31.0)
        self.assertTrue(latch.armed)
        self.assertTrue(latch.offer('2026-11-19/day',29.7))
        self.assertFalse(latch.offer('2026-11-19/day',29.0))
        self.assertFalse(latch.offer('2026-11-20/day',28.0))
        latch.completed('2026-11-20/day',32.0)
        self.assertTrue(latch.offer('2026-11-21/day',29.9))

    def test_first_cross_precheck_checked_before_any_submitted_order(self):
        src=py('market_trend_runtime_v043.py')
        before=src.index('prior_closed_rsi=mt.rsi20(completed_closes)')
        reject=src.index("if prior_closed_rsi<=mt.RSI_ARM:",before)
        precheck=src.index('if check:',reject)
        self.assertLess(before,reject)
        self.assertLess(reject,precheck)
        self.assertIn('RSI_ALREADY_BELOW_30_ON_PREVIOUS_CLOSE',src)
        self.assertIn('RSI_FIRST_CROSS_HISTORY_UNVERIFIED',src)
        self.assertIn("self._prechecked.pop(entry,None)",src[before:precheck])
        self.assertIn('if submit and entry not in self._ordered:',src)
        self.assertIn('COIN_VERIFIED_TRADE_MAX_AGE_SEC=10.0',src)
        self.assertIn('abs(time.time()-float(book.get("timestamp",0))/1000)>3',src)

    def test_original_preclose_window_unchanged(self):
        close=datetime(2026,11,17,9,0,tzinfo=rules.KST)
        for sec in (22,19,12.5):
            self.assertTrue(rules.live_rsi_check_due(
                close-timedelta(seconds=sec),close,'day','coin'))
        for sec in (12,10,7):
            self.assertTrue(rules.preclose_due(
                close-timedelta(seconds=sec),close,'day','coin'))
        self.assertFalse(rules.preclose_due(
            close-timedelta(seconds=6),close,'day','coin'))

    def test_gmo_exact_buy_total_sell_cost_roi(self):
        src=py('android_runtime_v078.py')
        fn=next(n for n in ast.parse(src).body
                if isinstance(n,ast.FunctionDef) and
                n.name=='_v086_enrich_gmo_executions')
        ns={'math':math}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'gmo_v086','exec'),ns)
        records=[
           {'side':'BUY','code':'005930','qty':10,'price':1000,'fees':15},
           {'side':'SELL','code':'005930','qty':4,'price':1500,
            'net_profit':1900,'fees':20,'tax':20},
           {'side':'SELL','code':'005930','qty':2,'price':900,
            'net_profit':-250,'fees':15,'tax':10},
           {'side':'SELL','code':'005930','qty':1,'price':1000,
            'net_profit':100}, # no fee/tax -> must NOT invent cost/ROI
        ]
        ns[fn.name](records)
        self.assertEqual(records[0]['buy_price'],1000)
        self.assertEqual(records[0]['buy_amount'],10000)
        self.assertEqual(records[1]['sell_qty'],4)
        self.assertEqual(records[1]['buy_amount'],4060)
        self.assertAlmostEqual(records[1]['realized_pct'],1900/4060*100,places=4)
        self.assertEqual(records[2]['buy_amount'],2025)
        self.assertAlmostEqual(records[2]['realized_pct'],-250/2025*100,places=4)
        self.assertNotIn('realized_pct',records[3])
        self.assertNotIn('buy_amount',records[3])

    def test_gmo_dashboard_and_held_same_source(self):
        java=(BASE/'java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertIn('if("stock".equals(m)&&"gmo".equals(s))',java)
        self.assertIn('reconciled+=value;',java)
        self.assertIn('st.unrealized=complete?reconciled:Double.NaN;',java)
        self.assertIn('double acquisition=usefulNumber(r,"gmo_cost_basis");',java)
        self.assertIn('return mark*qty*(1-fee-tax)-acquisition;',java)
        self.assertIn('for(String[] def:strategyList(m))',java)
        self.assertIn('Stats v=stats(m,def[0]);',java)
        self.assertIn('tradeMetricTile(figures,"매도량"',java)
        self.assertIn('tradeMetricTile(figures,"매수금액"',java)
        self.assertIn('return profit/bought*100d;',java)
        self.assertIn('return profit/paid*100d;',java)
        self.assertIn('gmo_cost_basis',py('android_runtime_v078.py'))
        self.assertIn('ORIGINAL_NET_PROFIT_OVER_ALLOCATED_COST',
                      py('android_runtime_v078.py'))

    def test_upbit_genuine_ticker_and_book_ages(self):
        src=archive_module('d_upbit.py')
        self.assertIn("trade_stamp=item.get('trade_timestamp')",src)
        self.assertIn("trade_age > 10.0",src)
        self.assertIn('self._v086_tick_exchange_stale_rejected',src)
        self.assertIn('self._v086_tick_replay_rejected',src)
        self.assertIn('self._v086_book_exchange_stale_rejected',src)
        self.assertIn('not isinstance(timestamp,(int,float))',src)
        self.assertIn('MAX_BOOK_AGE_SEC',src)
        self.assertIn('ping_interval=20,ping_timeout=30',src)
        self.assertIn('self._v084_accept_tick(code,price,stamp,trade_stamp)',src)
        self.assertIn('age>0.75',src)
        self.assertNotIn('REST',src[src.index('def _ws(self):'):src.index('def _ws(self):')+700])
        compile(src,'d_upbit.py','exec')

    def test_kiwoom_one_socket_stable_queue_order_and_200_limit(self):
        src=archive_module('d_live.py')
        self.assertIn('if len(codes) > 200',src)
        self.assertIn('codes[i * 100:(i + 1) * 100]',src)
        self.assertIn('self._v071_enqueue_feed',src)
        self.assertIn('self._v071_feed_worker',src)
        self.assertIn('lag>3.0',src)
        self.assertIn("raise RuntimeError('V071_STOCK_WS_QUEUE_BACKPRESSURE')",src)
        overlay=py('kiwoom_ws_v067.py')
        self.assertIn('asyncio.create_task(perform())',overlay)
        self.assertIn('await old_refresh(self,ws)',overlay)
        self.assertIn('task.cancel()',overlay)

    def test_no_decorative_notes_and_never_arm_real_broker(self):
        java=(BASE/'java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        for phrase in ('실제 매도는 원장에 기록됨. 후속고가는',
                       '정량 비교표·근거·시장점수·주문·AI원본 전체를',
                       '실제 열 더블탭 → 실제 진입만',
                       '5분 · 30분 · 1시간 · 일봉 각각 별도 시드 / 최대보유 설정',
                       'button("실거래 ON"'):
            self.assertNotIn(phrase,java)
        self.assertIn('SIMULATED_ACCOUNT_ONLY_NO_BROKER_ORDERS',
                      py('android_runtime_v078.py'))

if __name__=='__main__':
    unittest.main(verbosity=2)
