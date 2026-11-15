"""v061 regression: two MA entry kinds, 4 frames and priority exits.

All signals use COMPLETED candles and never fabricate actual broker fills.
"""
import datetime as dt
import json
import sqlite3
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import ma_v061_rules as ma
import ma_v061_coin_runtime as coin
import ma_v061_stock_runtime as stock
from market_trend_v043 import KST


def bars(kind,frame):
    unit={'5m':300,'30m':1800,'60m':3600,'day':86400}[frame]
    start=dt.datetime(2026,11,12,0,0,tzinfo=dt.timezone.utc)
    values=[]
    if kind=='TURNPOINT':
        # Big old negative 120 trend, recovering shorter periods below MA120,
        # and current candle is the FIRST all-short-positive point.
        values=[Decimal(200-i*100/63) for i in range(63)]
        values += [Decimal(92+j*.2) for j in range(57)]
        values += [Decimal('90'),Decimal('105')]
    else:
        values=[Decimal(200-i*.2) for i in range(122)]
    result=[]
    for i,close in enumerate(values):
        op=(close-Decimal('.3') if kind=='TURNPOINT' else
            close+Decimal('.3'))
        lo=min(close,op)-Decimal('.2')
        hi=max(close,op)+Decimal('.3')
        result.append({'start':start+dt.timedelta(seconds=unit*i),
            'open':op,'high':hi,'low':lo,'close':close,
            'volume':Decimal(100),'code':'005930','market':'KOSPI',
            'ts':(dt.datetime(2026,11,10,9,0,tzinfo=KST)+
                   dt.timedelta(seconds=unit*i)).strftime('%Y%m%d%H%M%S')})
    return result


class NewMaRules(unittest.TestCase):
    def test_only_bearish_3down_and_turnpoint_no_volume_record(self):
        for tf in ma.FRAMES:
            with self.subTest(tf=tf):
                bear=ma.candidate_from_closed(bars('DOWN',tf),tf)
                self.assertEqual(bear['kind'],'DOWN_BEAR')
                self.assertEqual(bear['ma_down'],5)
                pivot=ma.candidate_from_closed(bars('TURNPOINT',tf),tf)
                self.assertEqual(pivot['kind'],'TURNPOINT')
                self.assertTrue(pivot['pivot_transition_first'])
                self.assertLessEqual(Decimal(pivot['width_pct']),2)
                # The next bar still satisfies the positive-slope alignment,
                # but MUST NOT generate another first-turnpoint signal.
                extended=bars('TURNPOINT',tf)
                future=dict(extended[-1],start=extended[-1]['start']+
                    dt.timedelta(seconds={'5m':300,'30m':1800,'60m':3600,'day':86400}[tf]),
                    close=Decimal('106'),open=Decimal('105.5'),
                    high=Decimal('106.3'),low=Decimal('105.3'))
                self.assertNotEqual(ma.candidate_from_closed(
                    extended+[future],tf)['kind'],'TURNPOINT')

    def test_pivot_width_over_2_percent_is_not_actionable(self):
        x=bars('TURNPOINT','5m')
        x[-1]['low']=Decimal('101')
        x[-1]['high']=Decimal('106')
        p=ma.candidate_from_closed(x,'5m')
        self.assertIsNone(p['kind'])
        self.assertEqual(p['reason'],'TURNPOINT_RANGE_OVER_2PCT')

    def test_no_lookahead_and_zero_slopes_do_not_qualify(self):
        x=bars('TURNPOINT','5m')
        self.assertIsNone(ma.candidate_from_closed(x[:-1],'5m')['kind'])
        x[-1]['open']=x[-1]['close']
        self.assertEqual(ma.candidate_from_closed(x,'5m')['kind'],'TURNPOINT')

    def test_bearish_signal_is_not_contingent_on_volume_or_large_body(self):
        x=bars('DOWN','30m')
        x[-1]['volume']=Decimal('0')
        x[-1]['open']=x[-1]['close']+Decimal('.01')
        x[-1]['high']=x[-1]['open']+Decimal('.01')
        x[-1]['low']=x[-1]['close']-Decimal('.01')
        self.assertEqual(ma.candidate_from_closed(x,'30m')['kind'],'DOWN_BEAR')

    def test_exact_four_targets_and_stepwise_first_second_third(self):
        for tf,pct in ma.FIRST_TARGET_PCT.items():
            with self.subTest(frame=tf):
                entry=Decimal('100')
                state={'frame':tf,'stage':0,'first_anchor':'0',
                    'first_sold':'0','second_sold':'0','peak2':'0'}
                before=entry*Decimal('.99')
                state,wanted,reason=ma.evaluate_pivot_exit(
                    state,before,entry,100,1000)
                self.assertEqual(wanted,0)
                self.assertEqual(state['stage'],0)
                # Must arm the guard even with zero bid liquidity.
                goal=entry*(1+pct/100)
                state,wanted,reason=ma.evaluate_pivot_exit(
                    state,goal,entry,0,1000)
                self.assertEqual(state['stage'],1)
                self.assertEqual(wanted,0)
                state,wanted,reason=ma.evaluate_pivot_exit(
                    state,goal,entry,1000,1000)
                self.assertEqual((wanted,reason),(Decimal(200),
                                                 'MA_V061_FIRST_20PCT'))
                state=ma.quantity_after_fill(state,reason,200)
                first=goal
                rise=first*Decimal('1.03')
                state,wanted,reason=ma.evaluate_pivot_exit(
                    state,rise,entry,800,1000)
                self.assertEqual(state['stage'],2)
                self.assertEqual((wanted,reason),(Decimal(400),
                                                 'MA_V061_SECOND_HALF_REMAINING'))
                state=ma.quantity_after_fill(state,reason,400)
                state,wanted,reason=ma.evaluate_pivot_exit(
                    state,rise*Decimal('1.1'),entry,400,1000)
                self.assertEqual(wanted,0)
                peak=Decimal(state['peak2'])
                state,wanted,reason=ma.evaluate_pivot_exit(
                    state,peak*Decimal('.96'),entry,400,1000)
                self.assertEqual(reason,'MA_V061_THIRD_PEAK_MINUS4')
                self.assertEqual(wanted,400)

    def test_entry_guard_applies_after_first_touch_not_before(self):
        state={'frame':'5m','stage':0,'first_anchor':'0',
            'first_sold':'0','second_sold':'0','peak2':'0'}
        p,qty,reason=ma.evaluate_pivot_exit(state,100,100,100,100)
        self.assertEqual(qty,0)
        p,qty,reason=ma.evaluate_pivot_exit(p,101.5,100,100,100)
        self.assertEqual(p['stage'],1)
        p,qty,reason=ma.evaluate_pivot_exit(p,100,100,100,100)
        self.assertEqual((qty,reason),(Decimal(100),
            'MA_V061_AFTER_FIRST_ENTRY_GUARD'))

    def test_forced_stops_beat_all_profit_targets(self):
        x=[Decimal(190-i*.4) for i in range(120)]
        ma_down=ma.forced_exit_reason(x,Decimal(90))
        self.assertEqual(ma_down,'MA_V061_FORCE_ALL5_DOWN')
        s={'frame':'5m','stage':2,'first_anchor':'101.5',
           'first_sold':'20','second_sold':'40','peak2':'120'}
        _,wanted,reason=ma.evaluate_pivot_exit(s,130,100,40,100,ma_down)
        self.assertEqual((wanted,reason),(Decimal(40),ma_down))
        closes=[Decimal(100+i*1.8) for i in range(60)]+[
            Decimal(208-i*.3) for i in range(60)]
        ma_top=ma.forced_exit_reason(closes,Decimal(180))
        self.assertEqual(ma_top,'MA_V061_FORCE_120UP_TOP4_DOWN')

    def test_coin_and_stock_carry_new_signal_without_reference_gate(self):
        for tf in ma.FRAMES:
            with self.subTest(frame=tf):
                history=bars('DOWN',tf)
                seconds={'5m':300,'30m':1800,'60m':3600,'day':86400}[tf]
                now=history[-1]['start']+dt.timedelta(seconds=seconds+1)
                audit,candidate=coin._candidate_decision(None,history,tf,
                    'KRW-NCT',Decimal('.01'),now)
                self.assertEqual(candidate['features']['ma_v061_kind'],'DOWN_BEAR')
                self.assertEqual(candidate['features']['ma_bear_override'],True)
                self.assertEqual(candidate['limit'],
                                 str(coin_dec_floor(history[-1]['low'],Decimal('.01'))))
                stock_rows=[dict(x) for x in history]
                audit_stock,stock_candidate=stock._native_candle_candidate(
                    __import__('stock_ma_engine'),stock_rows,tf,now)
                self.assertEqual(stock_candidate['audit']['ma_v061_kind'],'DOWN_BEAR')
                self.assertEqual(stock_candidate['limit'],int(history[-1]['low']))

    def test_stock_turnpoint_opens_with_five_second_deadline(self):
        history=bars('TURNPOINT','30m')
        audit,candidate=stock._native_candle_candidate(
            __import__('stock_ma_engine'),history,'30m',
            dt.datetime(2026,11,16,9,30,tzinfo=KST))
        self.assertEqual(candidate['audit']['ma_v061_kind'],'TURNPOINT')
        a=dt.datetime.fromisoformat(audit['ma_v061_open_at'])
        b=dt.datetime.fromisoformat(audit['ma_v061_deadline_at'])
        self.assertEqual((b-a).total_seconds(),5)


def coin_dec_floor(price,tick):
    return (Decimal(price)//Decimal(tick))*Decimal(tick)


if __name__=='__main__':
    unittest.main()
