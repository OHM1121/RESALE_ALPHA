"""MA v067 user policy, fresh feed separation and zero-history boot safety.

No broker, no real order or live account is contacted in this suite.
"""
from __future__ import annotations
import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app/src/main/python"))
import ma_v067_policy as ma
import ma_epoch_v067 as epoch
import ma_v067_stock_runtime as stock
import ma_v067_coin_runtime as coin

KST=dt.timezone(dt.timedelta(hours=9))
NOW=dt.datetime(2026,11,16,12,30,0,tzinfo=KST)


def decline(count=131,body=None):
    bars=[]
    for i in range(count):
        close=D(300-i)
        opening=close+D('0.2')
        bars.append({'open':str(opening),'high':str(opening+D(1)),
                     'low':str(close-D(1)),'close':str(close),
                     'code':'005930','market':'KOSPI',
                     'volume':int(12000+i*11),
                     'ts':f"20261116{i:06d}"})
    if body is not None:
        op=D(bars[-1]['open'])
        cl=op*(D(1)-D(str(body))/100)
        bars[-1].update(close=str(cl),low=str(cl-D(1)))
    return bars


class SustainedAlignment(unittest.TestCase):
    def test_multiple_old_aligned_bars_do_not_disqualify_current_signal(self):
        for frame,pct in [('5m',3),('30m',4),('60m',5),('day',7)]:
            with self.subTest(frame=frame):
                bars=decline(body=pct+D('0.2'))
                self.assertTrue(ma.bear_setup(bars[:-2]))
                self.assertTrue(ma.bear_setup(bars[:-1]))
                self.assertTrue(ma.bear_setup(bars))
                self.assertEqual(ma.bear_reversal_completed(bars,frame)['kind'],
                                 'REVERSAL_BEAR')
                self.assertEqual(ma.bear_reversal_completed(bars,frame)['reason'],
                                 'SUSTAINED_ALIGNMENT_LARGE_BEAR')

    def test_frame_body_minimum_is_strict(self):
        for frame,pct in ma.BEAR_BODY_PCT.items():
            bar=decline(body=pct-D('.1'))
            self.assertIsNone(ma.bear_reversal_completed(bar,frame)['kind'])

    def test_late_new_alignment_or_missing_bars_disqualified(self):
        bad=[{'open':'100','high':'101','low':'99','close':'100'}]*130
        self.assertFalse(ma.bear_setup(bad))
        self.assertIsNone(ma.bear_reversal_completed(bad,'5m')['kind'])
        self.assertIsNone(ma.bear_reversal_completed(bad[:110],'5m')['kind'])

    def test_stock_signal_price_is_verified_low_not_close(self):
        bars=decline(body=7.5)
        v,c=stock._candidate_from_closed(bars,'day','20261116153000')
        self.assertEqual(v['decision'],'QUALIFIED')
        self.assertEqual(c['audit']['ma_v067_kind'],'REVERSAL_BEAR')
        self.assertGreater(c['target'],c['limit'])
        self.assertGreater(D(bars[-1]['close']),0)
        self.assertEqual(c['first_fraction'],0.30)

    def test_second_stage_genuinely_requires_first_fill(self):
        state=ma.initial_up_exit_state('5m')
        initial=D(100)
        first,second=ma.up_targets(D(100),'5m')
        after,want,why=ma.up_exit(state,D(104.04),100,100,initial,NOW)
        self.assertEqual((want,why),(D(10),'MA_V065_UP_FIRST_10PCT'))
        self.assertEqual(after['first_sold'],'0')
        filled=ma.apply_up_fill(after,'MA_V065_UP_FIRST_10PCT',D(10),NOW,initial,D(102))
        _,want,why=ma.up_exit(filled,second,100,90,initial,NOW)
        self.assertEqual(why,'MA_V065_UP_SECOND_HALF_REMAINING')
        self.assertEqual(want,D(45))

    def test_upward_order_is_not_eligible_after_15s(self):
        self.assertTrue(ma.within_up_fill_deadline(NOW,NOW+dt.timedelta(seconds=15)))
        self.assertFalse(ma.within_up_fill_deadline(NOW,NOW+dt.timedelta(seconds=15.001)))

    def test_unobserved_price_path_cannot_trigger_synthetic_buy(self):
        s,eligible=ma.bear_low_entry({},D(99),D(100))
        self.assertTrue(eligible)
        s,eligible=ma.bear_low_entry(s,D(101),D(100))
        self.assertFalse(eligible)
        _,eligible=ma.bear_low_entry(s,D(100),D(100))
        self.assertFalse(eligible)

    def test_120_above_all_forced_exit_is_preserved(self):
        self.assertEqual(ma.forced_ma_exit([],D(100)),'')

    def test_up_5_seconds_window(self):
        self.assertFalse(ma.upward_transition_on_forming(
            [1]*121,100,D('3.99'))['kind'])
        self.assertFalse(ma.upward_transition_on_forming(
            [1]*121,100,D('5.01'))['kind'])

    def test_coin_close_gate_boundaries(self):
        t=NOW.replace(minute=34,second=55,microsecond=0)
        r,end=coin._bar_close(t,'5m')
        self.assertEqual(r,5)
        self.assertEqual(end.minute,35)
        self.assertEqual(end.second,0)


class AccountEpoch(unittest.TestCase):
    def test_empty_new_ma_both_markets_are_immutable_across_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            old_s=epoch.old_active(root,'stock')
            old_c=epoch.old_active(root,'coin')
            for source in (old_s,old_c):
                source.parent.mkdir(parents=True,exist_ok=True)
                source.write_bytes(b'OLD_MA_ARCHIVE_DO_NOT_DELETE')
            mark_s=epoch.ensure_epoch(root,'stock')
            mark_c=epoch.ensure_epoch(root,'coin')
            self.assertEqual(epoch.ensure_epoch(root,'stock'),mark_s)
            self.assertEqual(epoch.ensure_epoch(root,'coin'),mark_c)
            self.assertEqual(mark_s['strategy_version'],epoch.VERSION)
            self.assertTrue('MA_EPOCH_V067' in mark_s['active_db_relative'])
            self.assertTrue('ma_epoch_v067' in mark_c['active_db_relative'])
            self.assertNotEqual(epoch.old_active(root,'stock'),epoch.stock_active(root))
            self.assertNotEqual(epoch.old_active(root,'coin'),epoch.coin_active(root))
            self.assertFalse(epoch.stock_active(root).exists())
            self.assertFalse(epoch.coin_active(root).exists())
            self.assertEqual(old_s.read_bytes(),b'OLD_MA_ARCHIVE_DO_NOT_DELETE')
            self.assertEqual(old_c.read_bytes(),b'OLD_MA_ARCHIVE_DO_NOT_DELETE')
            self.assertEqual(epoch.epoch_state(root,'stock')['started_at'],mark_s['started_at'])
            self.assertEqual(epoch.blank_archive(root,'stock')['executions'],[])
            self.assertEqual(epoch.blank_archive(root,'coin')['positions'],[])


class WiringAndProvenance(unittest.TestCase):
    def test_trend_precheck_cannot_block_on_missing_orderbook(self):
        p=Path(__file__).resolve().parents[1]/'app/src/main/python/market_trend_runtime_v043.py'
        s=p.read_text('utf8')
        segment=s[s.index('def _preclose_candidates('):s.index('def _process_holds(')]
        self.assertIn("observed_prices=dict(self._quotes[market])",segment)
        self.assertIn("q=self._quote(market,code)",segment)
        self.assertIn("FRESH_BOOK_MISSING_AT_SUBMIT",segment)
        self.assertIn("self.standalone_wallet.buy(market",segment)

    def test_ws_does_not_double_dispatch_gmo_in_receiver_callback(self):
        p=Path(__file__).resolve().parents[1]/'app/src/main/python/android_runtime_v078.py'
        s=p.read_text('utf8')
        segment=s[s.index('def _patched_gmo(self):'):s.index('def _patched_stop(self):')]
        self.assertNotIn('def tick_with_gmo',segment)
        self.assertNotIn('def book_with_gmo',segment)
        self.assertIn('gmo.start(lambda: self.stock_runner.api',segment)

    def test_native_socket_universe_refresh_is_nonblocking(self):
        p=Path(__file__).resolve().parents[1]/'app/src/main/python/kiwoom_ws_v067.py'
        text=p.read_text('utf8')
        self.assertIn('asyncio.create_task(perform())',text)
        self.assertIn('old_refresh(self,ws)',text)
        self.assertIn('if owner is ws:',text)

    def test_new_ma_install_does_not_erase_old_db(self):
        s=Path(__file__).resolve().parents[1]/'app/src/main/python/ma_epoch_v067.py'
        source=s.read_text('utf8')
        for forbidden in ('.unlink(','.rmtree(', 'DELETE FROM positions',
                          'DROP TABLE', '.replace(old_active('):
            self.assertNotIn(forbidden,source)
        self.assertIn('ai062.ai_root=lambda root:Path(root)/"MA_EPOCH_V067_AI"',source)

    def test_native_engines_remain_original_order_fill_owners(self):
        for source in ('ma_v067_stock_runtime.py','ma_v067_coin_runtime.py'):
            text=(Path(__file__).resolve().parents[1]/'app/src/main/python'/source).read_text('utf8')
            self.assertIn('ma_v067_positions',text)
            self.assertNotIn('self.kiwoom.fok(',text)
            self.assertNotIn('self.upbit.fok(',text)

if __name__=='__main__':
    unittest.main(verbosity=2)
