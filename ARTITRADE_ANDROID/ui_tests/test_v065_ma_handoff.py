"""Executable specification for exact 2026-11-16 MA handoff; no broker IO."""
from pathlib import Path
import datetime as dt
import sys
import unittest
from decimal import Decimal
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app/src/main/python'))
import ma_v065_policy as p

KST=dt.timezone(dt.timedelta(hours=9))
AT=dt.datetime(2026,11,16,10,45,tzinfo=KST)

def candles(last_open='100',last_close='97'):
    prices=[Decimal('100')]*121+[Decimal('90')]
    out=[{'open':str(v),'high':str(v+1),'low':str(v-1),'close':str(v)}
         for v in prices]
    op=Decimal(last_open);close=Decimal(last_close)
    out.append({'open':str(op),'close':str(close),
                'high':str(max(op,close)+1),'low':str(min(op,close)-1)})
    return out

class StrictEntry(unittest.TestCase):
    def test_five_frames_require_exact_first_next_bar_large_negative_body(self):
        for frame,pct in p.BEAR_BODY_PCT.items():
            with self.subTest(frame=frame):
                good=candles(last_close=str(Decimal('100')-pct))
                result=p.bear_reversal_completed(good,frame)
                self.assertEqual(result['kind'],'REVERSAL_BEAR')
                less=candles(last_close=str(Decimal('100')-pct+Decimal('.01')))
                self.assertIsNone(p.bear_reversal_completed(less,frame)['kind'])
    def test_three_negative_lines_is_not_enough(self):
        old=[Decimal('100')]*120+[Decimal('99'),Decimal('98'),Decimal('90')]
        bars=[{'open':str(x+1),'close':str(x),'high':str(x+2),'low':str(x-1)}
              for x in old]
        self.assertIsNone(p.bear_reversal_completed(bars,'5m')['kind'])
    def test_setup_must_be_new_and_exactly_previous_bar(self):
        rows=candles('100','90')
        self.assertTrue(p.bear_setup(rows[:-1]))
        self.assertFalse(p.bear_setup(rows[:-2]))
        duplicated=[*rows[:121],rows[121].copy(),rows[121].copy(),rows[-1].copy()]
        self.assertIsNone(p.bear_reversal_completed(duplicated,'5m')['kind'])
    def test_low_or_below_works_except_rebound_back_to_low(self):
        state,ok=p.bear_low_entry({},90,90)
        self.assertTrue(ok)
        state,ok=p.bear_low_entry(state,88,90)
        self.assertTrue(ok)
        state,ok=p.bear_low_entry(state,89,90)
        self.assertTrue(ok)
        state,ok=p.bear_low_entry(state,90,90)
        self.assertFalse(ok)
        state,ok=p.bear_low_entry(state,85,90)
        self.assertFalse(ok)
    def test_close_minus_five_first_all_four_up_without_below_requirement(self):
        prior=[Decimal(200-i) for i in range(121)]
        self.assertEqual(p.upward_transition_on_forming(prior,190,5)['kind'],'REVERSAL_UP')
        self.assertIsNone(p.upward_transition_on_forming(prior,190,6)['kind'])
        self.assertIsNone(p.upward_transition_on_forming(prior,190,3)['kind'])
        self.assertIsNone(p.upward_transition_on_forming(prior,75,5)['kind'])
    def test_preclose_fill_must_be_within_fifteen_elapsed_seconds(self):
        self.assertTrue(p.within_up_fill_deadline(AT,AT+dt.timedelta(seconds=15)))
        self.assertFalse(p.within_up_fill_deadline(AT,AT+dt.timedelta(milliseconds=15001)))

class StrictExit(unittest.TestCase):
    def test_bear_high_30_then_high_plus_two_half_remaining(self):
        state=p.initial_bear_exit_state('30m',120)
        _,q,reason=p.bear_exit(state,120,100,100,100,AT)
        self.assertEqual((q,reason),(Decimal(30),'MA_V065_FIRST_30_AT_REVERSAL_HIGH'))
        state=p.apply_bear_fill(state,reason,30,AT)
        state=p.mark_stage_completion(state,100,AT)
        self.assertEqual(state['first_completed_at'],AT.isoformat())
        _,q,reason=p.bear_exit(state,Decimal('122.4'),100,70,100,AT)
        self.assertEqual((q,reason),(Decimal(35),'MA_V065_SECOND_HALF_REMAINING'))
    def test_pre_first_entry_retrace_only_after_rise(self):
        state=p.initial_bear_exit_state('5m',120)
        state,_,reason=p.bear_exit(state,100,100,100,100,AT)
        self.assertEqual(reason,'')
        state,_,_=p.bear_exit(state,110,100,100,100,AT)
        _,q,reason=p.bear_exit(state,100,100,100,100,AT)
        self.assertEqual((q,reason),(Decimal(100),'MA_V065_PRE_FIRST_ENTRY_RETRACE'))
    def test_after_first_grace_and_entry_plus_one_hard_stop(self):
        state=p.initial_bear_exit_state('5m',120)
        state=p.apply_bear_fill(state,'MA_V065_FIRST_30_AT_REVERSAL_HIGH',30,AT)
        state=p.mark_stage_completion(state,100,AT)
        _,q,reason=p.bear_exit(state,120,100,70,100,AT+dt.timedelta(minutes=4))
        self.assertEqual(q,0)
        _,q,reason=p.bear_exit(state,120,100,70,100,AT+dt.timedelta(minutes=5,seconds=1))
        self.assertEqual((q,reason),(Decimal(70),'MA_V065_FIRST_HIGH_RETURN_AFTER_FIVE_MIN'))
        _,q,reason=p.bear_exit(state,101,100,70,100,AT+dt.timedelta(seconds=1))
        self.assertEqual((q,reason),(Decimal(70),'MA_V065_HARD_ENTRY_PLUS_1_AFTER_FIRST'))
    def test_after_second_trailing_midpoint_and_entry_plus_one(self):
        state=p.initial_bear_exit_state('5m',120)
        state=p.apply_bear_fill(state,'MA_V065_FIRST_30_AT_REVERSAL_HIGH',30,AT)
        state=p.mark_stage_completion(state,100,AT)
        state=p.apply_bear_fill(state,'MA_V065_SECOND_HALF_REMAINING',35,AT)
        state=p.mark_stage_completion(state,100,AT,execution_price=122.4)
        state,_,_=p.bear_exit(state,130,100,35,100,AT+dt.timedelta(minutes=1))
        self.assertEqual(state['peak_after_second'],'130')
        _,q,reason=p.bear_exit(state,126.1,100,35,100,AT+dt.timedelta(minutes=2))
        self.assertEqual((q,reason),(Decimal(35),'MA_V065_PEAK_MINUS_3'))
        _,q,reason=p.bear_exit(state,121.2,100,35,100,AT+dt.timedelta(minutes=2))
        self.assertEqual((q,reason),(Decimal(35),'MA_V065_HIGH_TARGET_MIDPOINT_STOP'))
        _,q,reason=p.bear_exit(state,101,100,35,100,AT+dt.timedelta(minutes=2))
        self.assertEqual((q,reason),(Decimal(35),'MA_V065_HARD_ENTRY_PLUS_1_AFTER_FIRST'))
    def test_forced_all_five_down_removed_but_120up_four_above_down_kept(self):
        all_down=[Decimal(190-i*.4) for i in range(120)]
        self.assertEqual(p.forced_ma_exit(all_down,90),'')
        mixed=[Decimal(100)]*50+[Decimal(220)]*40+[Decimal(220-i) for i in range(30)]
        self.assertEqual(p.forced_ma_exit(mixed,190),
           'MA_V065_FORCE_120_UP_FOUR_ABOVE_TURN_DOWN')
    def test_d_is_next_bar_and_d_plus_one_is_only_second_after_entry(self):
        for n in (10,11,13):
            self.assertFalse(p.forced_second_followup_bar(10,n,99,100))
        self.assertTrue(p.forced_second_followup_bar(10,12,99,100))
        self.assertFalse(p.forced_second_followup_bar(10,12,100,100))

if __name__=='__main__':unittest.main()
