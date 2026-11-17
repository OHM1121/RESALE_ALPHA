"""v080 reproduced buyer starvation: verified preclose timing and history sharing."""
import base64,datetime as dt,hashlib,io,sys,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[1]
APP=BASE/'app/src/main/python'
if str(APP) not in sys.path:sys.path.insert(0,str(APP))
import market_trend_v043 as rules
import market_trend_runtime_v043 as runtime
import ma_v061_coin_runtime as native_ma

KST=rules.KST
def kst(h,m,s=0):
    return dt.datetime(2026,11,16,h,m,s,tzinfo=KST)


class V080MissedEntries(unittest.TestCase):
    def test_coin_rsi_precheck_and_buy_have_retry_windows(self):
        close=kst(18,35)
        for remain in (22,20,15,12.5):
            self.assertTrue(rules.live_rsi_check_due(close-dt.timedelta(seconds=remain),close,'5m','coin'))
            self.assertFalse(rules.preclose_due(close-dt.timedelta(seconds=remain),close,'5m','coin'))
        for remain in (12,11.25,9,7):
            self.assertTrue(rules.preclose_due(close-dt.timedelta(seconds=remain),close,'5m','coin'))
            self.assertFalse(rules.live_rsi_check_due(close-dt.timedelta(seconds=remain),close,'5m','coin'))
        for remain in (23,6.99,1,0):
            now=close-dt.timedelta(seconds=remain)
            self.assertFalse(rules.preclose_due(now,close,'5m','coin'))

    def test_stock_session_close_special_and_regular_window(self):
        close=kst(15,30)
        # Final KRX 60-minute/day bar follows existing T-10min entry exclusion.
        for frame in ('30m','60m','day'):
            for remain in (12,9,7):
                now=kst(15,20)-dt.timedelta(seconds=remain)
                self.assertTrue(rules.preclose_due(now,close,frame,'stock'))
            self.assertFalse(rules.preclose_due(kst(15,21),close,frame,'stock'))
        midday=kst(12,30)
        self.assertTrue(rules.preclose_due(midday-dt.timedelta(seconds=10),midday,'5m','stock'))

    def test_watch_is_not_sealed_at_first_failed_submission(self):
        close=kst(18,35)
        f=runtime.Coordinator._evaluation_window_passed
        self.assertFalse(f(close-dt.timedelta(seconds=12),close,'5m','coin'))
        self.assertFalse(f(close-dt.timedelta(seconds=10),close,'5m','coin'))
        self.assertFalse(f(close-dt.timedelta(seconds=7),close,'5m','coin'))
        self.assertTrue(f(close-dt.timedelta(seconds=6.9),close,'5m','coin'))
        src=(APP/'market_trend_runtime_v043.py').read_text()
        self.assertIn('Do NOT expire RSI35 observation on the first -12s attempt',src)

    def test_verified_native_coin_history_reuses_only_exact_completed_bars(self):
        now=kst(18,32,10)
        finish=kst(18,30)
        arr=[]
        for i in range(122):
            start=finish-dt.timedelta(seconds=300*(122-i))
            close=100+i/10
            arr.append({'start':start.astimezone(dt.timezone.utc),
                        'close':close,'high':close+2,'low':close-2})
        key=('5m','KRW-V080-MA-TEST')
        with native_ma._HISTORY_LOCK:native_ma._HISTORY[key]=(finish,tuple(arr))
        try:
            actual=runtime.Coordinator._native_coin_history('5m',key[1],now)
            self.assertEqual(len(actual),122)
            self.assertEqual(actual[-1]['end'],finish)
            self.assertEqual(actual[-1]['source'],'NATIVE_UPBIT_VERIFIED_COMPLETED')
            with native_ma._HISTORY_LOCK:
                native_ma._HISTORY[key]=(finish-dt.timedelta(minutes=5),tuple(arr))
            self.assertEqual(runtime.Coordinator._native_coin_history('5m',key[1],now),[])
        finally:
            with native_ma._HISTORY_LOCK:native_ma._HISTORY.pop(key,None)

    def test_native_feed_is_pinned_and_never_stale_trades_authorize_orders(self):
        root=BASE/'app/src/main'
        parts=root/'assets/strategy_v070'
        source=base64.b64decode(''.join((parts/f'part_{i:02d}.txt').read_text()
                                      for i in range(22)))
        bridge=(root/'java/com/artitrade/mobile/StrategyRuntimeBridge.java').read_text()
        self.assertIn('CORE_SHA256="'+hashlib.sha256(source).hexdigest()+'"',bridge)
        with zipfile.ZipFile(io.BytesIO(source)) as z:
            self.assertIsNone(z.testzip())
            src=z.read('d_upbit.py').decode()
            self.assertIn('ping_interval=20,ping_timeout=30,max_size=2**20,max_queue=64',src)
            self.assertIn('self._v083_codes=codes | bids',src)
            self.assertIn('self._v083_active_codes=codes;self._v083_active_bids=bids',src)
            self.assertIn('self._v084_accept_tick(code,price,stamp,timestamp)',src)
            compile(src,'d_upbit.py','exec')
        overlay=(APP/'market_trend_runtime_v043.py').read_text()
        self.assertIn('if abs(time.time()-float(book.get("timestamp",0))/1000)>3',overlay)
        self.assertIn('if not is_regular_open(now) or not live_session(now,\'KRX\'):',overlay)
        policy=(APP/'ma_v067_policy.py').read_text()
        self.assertIn('MAX_UP_FILL_DELAY_SECONDS=15',policy)
        self.assertIn('UP_SUBMIT_BEFORE_CLOSE_SECONDS=10',policy)
        self.assertIn('UP_SUBMIT_END_BEFORE_CLOSE_SECONDS=2',policy)


if __name__=='__main__':
    unittest.main(verbosity=2)
