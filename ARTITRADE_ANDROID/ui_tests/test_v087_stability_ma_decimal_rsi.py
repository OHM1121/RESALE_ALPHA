"""v087 true SNT 5-minute MA first-profit SELL, OS exit cause, UI memory and decimals."""
import ast,datetime as dt,sys,unittest
from decimal import Decimal
from pathlib import Path
BASE=Path(__file__).resolve().parents[1]
PY=BASE/'app/src/main/python'
JAVA=BASE/'app/src/main/java/com/artitrade/mobile'
sys.path.insert(0,str(PY))
import ma_v067_policy as ma
import market_trend_v043 as trend

class V087Regression(unittest.TestCase):
    def test_snt_5min_reversal_up_target_and_first_qty(self):
        now=dt.datetime(2026,11,17,9,5,tzinfo=dt.timezone(dt.timedelta(hours=9)))
        state=ma.initial_up_exit_state('5m')
        self.assertEqual(ma.up_targets(Decimal('8.20'),'5m')[0],Decimal('8.3640'))
        _,want,reason=ma.up_exit(state,Decimal('8.40'),Decimal('8.20'),
                                  Decimal('100'),Decimal('100'),now)
        self.assertEqual(want,Decimal('10.00'))
        self.assertEqual(reason,'MA_V065_UP_FIRST_10PCT')
        # A print/high alone never proves a profitable SELL. Bid must execute.
        _,want2,reason2=ma.up_exit(state,Decimal('8.30'),Decimal('8.20'),
                                    Decimal('100'),Decimal('100'),now)
        self.assertEqual((want2,reason2),(Decimal('0'),''))
        source=(PY/'ma_v067_coin_runtime.py').read_text()
        self.assertIn("str(reason).startswith(('MA_V065_','MA_V067_'))",source)
        self.assertIn("MA_V067:",source)
        self.assertIn("if not self._sell(c,p,desired,bid",source)
        self.assertIn("if abs(now.timestamp()-float(book['timestamp'])/1000)>3:return",source)
        # v086: nested first/second profit attempt loop must STOP this
        # position when no exact native SELL evidence was written.  The old
        # "continue" would advance to a different position in a flat loop,
        # but v086 must "break" before attempting an unverified stage 2.
        self.assertIn("if executed is None:break",source)
        self.assertIn("for attempt in range(2):",source)
        self.assertIn("if not first_reason or confirmed<first_goal:break",source)

    def test_v067_sell_blocked_for_unverified_legacy_reasons(self):
        source=(PY/'ma_v067_coin_runtime.py').read_text()
        tree=ast.parse(source)
        install=next(x for x in tree.body if isinstance(x,ast.FunctionDef) and x.name=='install')
        func=next(x for x in install.body if isinstance(x,ast.FunctionDef) and x.name=='scoped_sell')
        # Execute only the pure nested gate against a fake authoritative SQLite
        # cursor and prior native SELL; no actual wallet/database ever opened.
        class DB:
            def execute(self,*_):
                return self
            def fetchone(self):
                return (1,)
        class Holder:
            pass
        def verified_orig_sell(*args,**kwargs):
            return 'VERIFIED_NATIVE_FILL'
        class MA:
            MACoinLedger=type('Dummy',(),{})
        ns={'_table':lambda c:None,'previous_sell':verified_orig_sell}
        exec(compile(ast.Module(body=[func],type_ignores=[]),'V067_SCOPED','exec'),ns)
        fn=ns['scoped_sell']
        params=(Holder(),DB(),{'id':123},Decimal('10'),Decimal('8.40'),
                dt.datetime.now().isoformat())
        self.assertEqual(fn(*params,'MA_V065_UP_FIRST_10PCT'),
                         'VERIFIED_NATIVE_FILL')
        self.assertEqual(fn(*params,'MA_V065_UP_PRE_FIRST_ENTRY_RETURN'),
                         'VERIFIED_NATIVE_FILL')
        self.assertEqual(fn(*params,'MA_V067_SECOND_FOLLOWUP_BELOW_ENTRY'),
                         'VERIFIED_NATIVE_FILL')
        self.assertFalse(fn(*params,'MA_V061_ARBITRARY_SELL'))
        self.assertFalse(fn(*params,'V24_BLIND_EXIT'))

    def test_main_wilder_rsi_3rd_bar_exit_only_after_contiguous_under30(self):
        at=dt.datetime(2026,11,17,9,4,dt.timedelta(0).seconds,
                       tzinfo=trend.KST)
        p=trend.TrendPosition('coin','trend','5m','KRW-SNT',at,8.2,100,
                              '2026-11-17T09:05:00+09:00')
        p.close_entry_bar(p.bar_key,8.4,8.1,25.0)
        self.assertEqual(p.closed_seq,0)
        p.close_next_bar('2026-11-17T09:10:00+09:00',29.0)
        self.assertEqual(p.closed_seq,1)
        p.close_next_bar('2026-11-17T09:15:00+09:00',27.0)
        self.assertEqual(p.closed_seq,2)
        reason=p.action(8.25,dt.datetime(2026,11,17,9,19,50,tzinfo=trend.KST),
                        True,True)
        self.assertEqual(reason,('MT_RSI_3_BARS',1.0))
        p.close_next_bar('2026-11-17T09:20:00+09:00',31.0)
        self.assertEqual(p.closed_seq,-1)
        self.assertNotEqual(p.action(8.25,dt.datetime(2026,11,17,9,24,50,tzinfo=trend.KST),
                                    True,True),('MT_RSI_3_BARS',1.0))
        r=(PY/'market_trend_runtime_v043.py').read_text()
        self.assertIn('"rsi_signal_ma9_used":False',r)
        self.assertIn('mt.rsi20(completed_closes,observed_trade)',r)

    def test_price_2digits_only_not_won_pnl(self):
        money=(JAVA/'WonFormat.java').read_text()
        ui=(JAVA/'UnifiedVisualActivity.java').read_text()
        self.assertIn('static String price(double amount)',money)
        self.assertIn('setMaximumFractionDigits(2)',money)
        self.assertIn('setMinimumFractionDigits(amount==Math.rint(amount)?0:2)',money)
        self.assertIn('private String formatMaybePrice',ui)
        self.assertIn('return finite(n)&&n>0?WonFormat.price(n):"가격 조회 중"',ui)
        self.assertIn('double n=positionMark(r);',ui)
        self.assertIn('private String money(double v){return WonFormat.won(v);}',ui)
        self.assertIn('static String won(double amount)',money)

    def test_android_previous_process_exit_and_app_memory_not_assumed(self):
        s=(JAVA/'ProcessHealthV087.java').read_text()
        self.assertIn('getHistoricalProcessExitReasons',s)
        for status in ('REASON_LOW_MEMORY','REASON_CRASH',
                       'REASON_CRASH_NATIVE','REASON_ANR',
                       'REASON_EXCESSIVE_RESOURCE_USAGE','REASON_SIGNALED'):
            self.assertIn(status,s)
        self.assertIn('getProcessMemoryInfo',s)
        self.assertIn('Debug.getNativeHeapAllocatedSize()',s)
        self.assertIn('recordPreviousSystemExits',s)
        self.assertIn('SYSTEM_PROCESS_EXIT_',s)
        self.assertIn('UNCAUGHT_JAVA_',s)
        self.assertIn('SIGKILL (status 9) is NOT automatically proven to be OOM',s)
        activity=(JAVA/'UnifiedVisualActivity.java').read_text()
        service=(JAVA/'UnifiedTradingService.java').read_text()
        self.assertIn('ProcessHealthV087.install(this)',activity)
        self.assertIn('ProcessHealthV087.install(this)',service)
        self.assertIn('onTrimMemory(int level)',activity)
        self.assertIn('releaseHiddenUiSnapshots()',activity)

    def test_source_db_and_trading_workers_unchanged_when_ui_lag(self):
        service=(JAVA/'UnifiedTradingService.java').read_text()
        activity=(JAVA/'UnifiedVisualActivity.java').read_text()
        disk=(JAVA/'LargeRecordStoreV087.java').read_text()
        self.assertIn('scheduleUiRecordsIfDue(hub);',service)
        self.assertIn('new Thread(()->safeStartStrategy()',service)
        self.assertIn('Thread.sleep(2500)',service)
        self.assertIn('6_500L:17_000L',service)
        self.assertIn('AtomicBoolean uiRecordCollecting',service)
        self.assertIn('LargeRecordStoreV087.write(this,serialized);',service)
        self.assertIn('.edit().remove("records").putString("storage","file-v087")',service)
        self.assertIn('LargeRecordStoreV087.read(this)',activity)
        self.assertIn('cachedLocalFileVersion',activity)
        self.assertIn('new AtomicFile',disk)
        self.assertIn('MAX_UI_RECORD_CHARS',disk)
        self.assertIn('atomic.finishWrite(output);',disk)
        self.assertIn('atomic.failWrite(output);',disk)
        self.assertIn('broker/order authority',service)
        self.assertIn('uiSnapshot=new UiSnapshot(new JSONObject()',activity)

if __name__=='__main__':
    unittest.main(verbosity=2)
