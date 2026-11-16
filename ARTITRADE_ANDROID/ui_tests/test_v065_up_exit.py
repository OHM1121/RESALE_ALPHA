"""MA REVERSAL_UP exit targets: user handoff. Pure regression, no live broker IO."""
from __future__ import annotations
import datetime as dt
import sys
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app/src/main/python'))
import ma_v065_policy as ma

T=dt.datetime(2026,11,16,10,35,tzinfo=dt.timezone(dt.timedelta(hours=9)))
ENTRY=D('100')
Q=D('100')
FIRST='MA_V065_UP_FIRST_10PCT'
SECOND='MA_V065_UP_SECOND_HALF_REMAINING'


def quote(state,price,remaining,at=T,forced=''):
    return ma.up_exit(state,D(str(price)),ENTRY,D(str(remaining)),Q,at,forced)


class UpwardTargetRates(unittest.TestCase):
    def test_all_four_first_and_second_percentages_are_separate(self):
        expected={
            '5m':(D('102'),D('104.04')),
            '30m':(D('102.5'),D('105.0625')),
            '60m':(D('103'),D('106.09')),
            'day':(D('105'),D('108.15')),
        }
        for frame,(first,second) in expected.items():
            with self.subTest(frame=frame):
                self.assertEqual(ma.up_targets(ENTRY,frame),(first,second))
                state=ma.initial_up_exit_state(frame)
                _,amount,reason=quote(state,first-D('.00001'),Q)
                self.assertEqual((amount,reason),(D('0'),''))
                _,amount,reason=quote(state,first,Q)
                self.assertEqual((amount,reason),(D('10'),FIRST))
                # TOUCH is NOT a fill: repeated state remains stage zero.
                self.assertEqual(state['first_sold'],'0')
                state=ma.apply_up_fill(state,FIRST,10,T,Q,first)
                self.assertEqual(state['first_sold'],'10')
                self.assertEqual(state['first_completed_at'],T.isoformat())
                _,amount,reason=quote(state,second-D('.00001'),90)
                self.assertEqual((amount,reason),(D('0'),''))
                _,amount,reason=quote(state,second,90)
                self.assertEqual((amount,reason),(D('45'),SECOND))
                state=ma.apply_up_fill(state,SECOND,45,T+dt.timedelta(seconds=10),Q,second)
                self.assertEqual(state['second_sold'],'45')
                self.assertEqual(state['peak_after_second'],str(second))
                self.assertEqual(state['second_completed_at'],(T+dt.timedelta(seconds=10)).isoformat())

    def test_pre_first_entry_retrace_sells_entire_position(self):
        st=ma.initial_up_exit_state('5m')
        st,q,reason=quote(st,100,100)
        self.assertEqual((q,reason),(D(0),''))
        st,q,reason=quote(st,101.2,100)
        self.assertTrue(st['seen_above_entry'])
        self.assertEqual(q,0)
        st,q,reason=quote(st,100,100)
        self.assertEqual((q,reason),(D(100),'MA_V065_UP_PRE_FIRST_ENTRY_RETURN'))

    def test_immediate_lower_price_does_not_fabricate_a_retrace(self):
        st=ma.initial_up_exit_state('5m')
        st,q,reason=quote(st,98,100)
        self.assertEqual((q,reason),(D(0),''))
        self.assertFalse(st['seen_above_entry'])

    def test_first_partial_cannot_fake_completion_or_skip_first_exit(self):
        st=ma.initial_up_exit_state('5m')
        st,q,reason=quote(st,102,100)
        self.assertEqual((q,reason),(D(10),FIRST))
        st=ma.apply_up_fill(st,FIRST,4,T,Q,102)
        self.assertEqual(st['first_completed_at'],'')
        _,q,reason=quote(st,102,96)
        self.assertEqual((q,reason),(D(6),FIRST))
        st=ma.apply_up_fill(st,FIRST,6,T+dt.timedelta(seconds=1),Q,102)
        self.assertEqual(st['first_sold'],'10')
        self.assertEqual(st['first_completed_at'],(T+dt.timedelta(seconds=1)).isoformat())
        # Replaying a duplicate fill must not further change quantities.
        self.assertEqual(ma.apply_up_fill(st,FIRST,6,T+dt.timedelta(seconds=2),Q,102),st)

    def test_after_first_before_second_entry_plus_one_hard_stop(self):
        for frame in ma.FRAMES:
            st=ma.initial_up_exit_state(frame)
            first,_=ma.up_targets(100,frame)
            st=ma.apply_up_fill(st,FIRST,10,T,Q,first)
            with self.subTest(frame=frame):
                _,q,reason=quote(st,'101.0000001',90)
                self.assertEqual((q,reason),(D(0),''))
                _,q,reason=quote(st,101,90)
                self.assertEqual((q,reason),(D(90),'MA_V065_UP_AFTER_FIRST_ENTRY_PLUS_1'))

    def test_second_partial_is_not_complete_for_peak_tracker(self):
        st=ma.initial_up_exit_state('30m')
        first,second=ma.up_targets(ENTRY,'30m')
        st=ma.apply_up_fill(st,FIRST,10,T,Q,first)
        st=ma.apply_up_fill(st,SECOND,20,T+dt.timedelta(seconds=2),Q,second)
        self.assertEqual(st['second_completed_at'],'')
        self.assertEqual(st['peak_after_second'],'0')
        _,q,reason=quote(st,second,70)
        self.assertEqual((q,reason),(D(25),SECOND))
        st=ma.apply_up_fill(st,SECOND,25,T+dt.timedelta(seconds=3),Q,second)
        self.assertEqual(st['second_sold'],'45')
        self.assertNotEqual(st['second_completed_at'],'')

    def test_second_complete_tracks_peak_and_exit_three_percent(self):
        st=ma.initial_up_exit_state('5m')
        first,second=ma.up_targets(ENTRY,'5m')
        st=ma.apply_up_fill(st,FIRST,10,T,Q,first)
        st=ma.apply_up_fill(st,SECOND,45,T+dt.timedelta(seconds=5),Q,second)
        st,q,reason=quote(st,'120',45,T+dt.timedelta(seconds=6))
        self.assertEqual((q,reason),(D(0),''))
        self.assertEqual(st['peak_after_second'],'120')
        st,q,reason=quote(st,'116.401',45,T+dt.timedelta(seconds=7))
        self.assertEqual(q,D(0))
        _,q,reason=quote(st,'116.4',45,T+dt.timedelta(seconds=8))
        self.assertEqual((q,reason),(D(45),'MA_V065_UP_PEAK_MINUS_3'))

    def test_after_second_first_target_floor_outranks_trailing(self):
        for frame in ma.FRAMES:
            first,second=ma.up_targets(ENTRY,frame)
            st=ma.initial_up_exit_state(frame)
            st=ma.apply_up_fill(st,FIRST,10,T,Q,first)
            st=ma.apply_up_fill(st,SECOND,45,T+dt.timedelta(seconds=5),Q,second)
            _,q,reason=quote(st,first,45,T+dt.timedelta(seconds=10))
            with self.subTest(frame=frame):
                self.assertEqual((q,reason),(D(45),'MA_V065_UP_AFTER_SECOND_FIRST_TARGET_FLOOR'))

    def test_forced_stops_outrank_all_profit_logic(self):
        st=ma.initial_up_exit_state('5m')
        _,q,reason=quote(st,110,100,forced='MA_V065_FORCE_120_UP_FOUR_ABOVE_TURN_DOWN')
        self.assertEqual((q,reason),(D(100),'MA_V065_FORCE_120_UP_FOUR_ABOVE_TURN_DOWN'))
        # d+1 stop will be resolved by external candle-sequence verifier.
        _,q,reason=quote(st,110,100,forced='MA_V065_D_PLUS_1_CLOSE_BELOW_ENTRY')
        self.assertEqual((q,reason),(D(100),'MA_V065_D_PLUS_1_CLOSE_BELOW_ENTRY'))

    def test_invalid_book_and_unconfirmed_events_cannot_advance_state(self):
        st=ma.initial_up_exit_state('30m')
        self.assertEqual(quote(st,0,100)[1:],(D(0),''))
        self.assertEqual(quote(st,110,-1)[1:],(D(0),''))
        self.assertEqual(ma.apply_up_fill(st,FIRST,10,T,Q,0),st)
        self.assertEqual(ma.apply_up_fill(st,'BROKER_REJECTED',10,T,Q,110),st)
        self.assertEqual(ma.apply_up_fill(st,SECOND,45,T,Q,110),st)
        self.assertEqual(ma.apply_up_fill(st,FIRST,11,T,Q,110),st)
        with self.assertRaises(ValueError):
            ma.up_targets(100,'2m')

    def test_bear_case_policy_is_unchanged(self):
        first=ma.initial_bear_exit_state('30m',120)
        self.assertEqual(first['policy'],'MA_V065_REVERSAL_BEAR')
        self.assertEqual(ma.FIRST_FRACTION,D('.30'))
        self.assertEqual(ma.UP_FIRST_FRACTION,D('.10'))


if __name__=='__main__':
    unittest.main()
