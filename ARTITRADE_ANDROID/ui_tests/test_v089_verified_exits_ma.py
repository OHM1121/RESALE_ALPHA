"""v089 regression: MA120 location, verified bid exits, and durable manual results."""
import datetime as dt
import json
import sys
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

BASE=Path(__file__).resolve().parents[1]
PY=BASE/'app/src/main/python'
JAVA=BASE/'app/src/main/java/com/artitrade/mobile'
sys.path.insert(0,str(PY))
import ma_v067_policy as policy
import ma_v067_coin_runtime as coin
import ma_v067_stock_runtime as stock

KST=dt.timezone(dt.timedelta(hours=9))


class MaEntryAlignment(unittest.TestCase):
    def test_short_four_above_120_cannot_open_up_reversal(self):
        # 120 declining, all short slopes turn positive, but all short SMA
        # values are ABOVE SMA120. This is the invalid FOLD-like setup.
        prior=[Decimal('20')]*2+[Decimal('2')]*59+[Decimal('10')]*60
        verdict=policy.upward_transition_on_forming(prior,Decimal('11'),5)
        self.assertNotEqual(verdict.get('kind'),'REVERSAL_UP')
        self.assertIn('BELOW_120',str(verdict.get('reason')))

    def test_first_short_four_positive_below_120_remains_eligible(self):
        prior=[Decimal('20')]*61+[Decimal('8')]*60
        result=policy.upward_transition_on_forming(prior,Decimal('9'),5)
        self.assertEqual(result['kind'],'REVERSAL_UP')
        self.assertEqual(result.get('short4_below120'),True)
        # Real entry still requires fresh ticker/book and submission window;
        # this pure predicate does NOT place or pretend to fill an order.

    def test_existing_positive_alignment_is_not_new_turnpoint(self):
        prior=[Decimal('20')]*61+[Decimal('8')]*58+[Decimal('8.5')]*2
        verdict=policy.upward_transition_on_forming(prior,Decimal('9'),5)
        self.assertNotEqual(verdict.get('kind'),'REVERSAL_UP')


class FakeCoinDB:
    def __init__(self,position):
        self.position=position
        self.rows=[]
        self.filled=None
        self.state={}
    def __enter__(self):return self
    def __exit__(self,*exc):return False
    def execute(self,sql,args=()):
        if 'SELECT p.*,s.kind,s.state_json' in sql:
            self.rows=[self.position]
        elif 'SELECT qty,price FROM executions' in sql:
            self.one={'qty':str(self.filled),'price':'102'} if self.filled else None
        elif 'UPDATE ma_v067_positions SET state_json' in sql:
            self.state=json.loads(args[0])
        return self
    def fetchall(self):return self.rows
    def fetchone(self):return getattr(self,'one',None)


class VerifiedExitTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime(2026,11,17,9,15,0,tzinfo=KST)

    def test_coin_first_target_uses_fresh_real_bid_without_separate_ticker(self):
        state=policy.initial_up_exit_state('5m')
        position={'id':1,'market':'KRW-DEMO','tf':'5m','status':'OPEN',
            'entry':'100','qty':'100','qty0':'100','kind':'REVERSAL_UP',
            'state_json':json.dumps(state)}
        db=FakeCoinDB(position)
        class Ledger:
            lock=threading.RLock()
            def connect(self):return db
            def _sell(self,c,p,qty,bid,at,reason,event_key=None):
                self.called.append((qty,reason,bid,event_key))
                c.filled=qty
                return True
        ledger=Ledger()
        ledger.called=[]
        book={'market':'KRW-DEMO','timestamp':int(self.now.timestamp()*1000),
            'bid':'102','bid_size':'200'}
        with patch.object(coin,'_recent_trade',return_value=None),\
             patch.object(coin,'_table',return_value=None),\
             patch.object(coin,'_prior_verified_closes',return_value=[]):
            coin._process_v067_exits(ledger,book,self.now)
        self.assertEqual(len(ledger.called),1,
            'Verified orderbook must still execute +2% first profit even when ticker is late')
        self.assertEqual(ledger.called[0][1],'MA_V065_UP_FIRST_10PCT')
        self.assertEqual(Decimal(db.state['first_sold']),Decimal('10'))

    def test_stock_first_target_uses_broker_eligible_bid_without_separate_trade(self):
        state=policy.initial_up_exit_state('5m')
        pos={'id':1,'code':'000001','tf':'5m','entry':'100','qty':100,'qty0':100}
        class Conn:
            def execute(self,sql,args=()):return self
            def fetchone(self):return {'price':102,'ts':'2026-11-17'}
        class Service:
            def __init__(self):self.calls=[]
            def _sell(self,c,p,qty,bid,when,reason,book):
                self.calls.append((qty,reason));return qty
        service=Service()
        with patch.object(stock,'_read_state',return_value=('REVERSAL_UP',state)),\
             patch.object(stock,'_latest_0b',return_value=None),\
             patch.object(stock,'_store_state',return_value=None):
            stock._exit_policy(service,Conn(),pos,102,self.now,{},lambda *a:None)
        self.assertEqual(service.calls,[(10,'MA_V065_UP_FIRST_10PCT')])

    def test_first_second_third_fills_need_actual_execution(self):
        now=self.now
        state=policy.initial_up_exit_state('5m')
        next_state,qty,reason=policy.up_exit(state,Decimal('102'),100,100,100,now)
        self.assertEqual((qty,reason),(Decimal('10.0'),'MA_V065_UP_FIRST_10PCT'))
        untouched,_,_=policy.up_exit(state,Decimal('104.1'),100,100,100,now)
        self.assertEqual(untouched['first_sold'],'0') # no inferred stage
        state=policy.apply_up_fill(next_state,reason,qty,now,100,102)
        _,qty2,reason2=policy.up_exit(state,Decimal('105'),100,90,100,now)
        self.assertEqual((qty2,reason2),(Decimal('45.00'),'MA_V065_UP_SECOND_HALF_REMAINING'))
        state=policy.apply_up_fill(state,reason2,qty2,now,100,105)
        state,_,_=policy.up_exit(state,Decimal('110'),100,45,100,now)
        _,third,why=policy.up_exit(state,Decimal('106.6'),100,45,100,now)
        self.assertEqual(why,'MA_V065_UP_PEAK_MINUS_3')
        self.assertEqual(third,Decimal('45'))


class ManualAndErrorProvenance(unittest.TestCase):
    def test_manual_sell_results_have_request_scoped_durable_status(self):
        service=(JAVA/'UnifiedTradingService.java').read_text(encoding='utf-8')
        activity=(JAVA/'UnifiedVisualActivity.java').read_text(encoding='utf-8')
        self.assertIn('MANUAL_SELL_PENDING',service)
        self.assertIn('"result_"+requestId',service)
        self.assertIn('"result_"+requestId',activity)
        self.assertIn('매도 요청 상태 다시 확인',activity)
        # No automatic retry with a new request ID when ledger status is unknown.

    def test_java_error_records_own_app_version(self):
        text=(JAVA/'RuntimeCrashStore.java').read_text(encoding='utf-8')
        self.assertIn('"app_version"',text)
        self.assertIn('getPackageInfo',text)


if __name__=='__main__':
    unittest.main(verbosity=2)
