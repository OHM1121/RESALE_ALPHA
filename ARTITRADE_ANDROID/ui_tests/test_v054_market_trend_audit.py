"""Fail-first regressions for MT54 buy/watch/sell and durable research boundary."""
from __future__ import annotations

import copy
import datetime as dt
import json
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app' / 'src' / 'main' / 'python'))

import market_trend_v043 as mt
from market_trend_runtime_v043 import Coordinator
from market_trend_wallet_v054 import TrendWalletV054
from ai_trend_analysis_v047 import analyze


class TestRt:
    def __init__(self, root):
        self.root = Path(root)
        self.stock_runner = None
        self.coin_runner = None
        self._halted = {}
    def live_enabled(self, market):
        return False
    def halt_market(self, *args):
        raise AssertionError('PAPER must not send LIVE orders')


class TrendAudit(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.rt = TestRt(self.root)
        self.at = dt.datetime(2026, 11, 15, 10, 4, 48, tzinfo=mt.KST)
        self.wallet = TrendWalletV054(self.rt)

    def buy(self, symbol='KRW-BTC', bar='2026-11-15T10:05:00+09:00'):
        p, status = self.wallet.buy('coin', '5m', symbol, symbol, 10000, 100,
                                     self.at, bar, 29.0)
        self.assertEqual(status, 'BOUGHT')
        return p

    def test_missed_preclose_window_expires_rsi35_watch_without_virtual(self):
        co = Coordinator(self.rt)
        bar = '2026-11-15T10:05:00+09:00'
        co.watch.observe('coin', 'KRW-BTC', '5m', bar, 34.8, True,
                         observed_at=time.time())
        self.assertEqual(len(co.watch.snapshot('coin')), 1)
        co._preclose_candidates('coin', self.at + dt.timedelta(seconds=3))
        self.assertEqual(co.watch.snapshot('coin'), [])
        self.assertEqual(co._virtual, {})

    def test_stop_loss_records_reentry_ban_without_legacy_position_duplicate(self):
        co = Coordinator(self.rt)
        co.standalone_wallet.legacy_owned = lambda market,symbol: False
        p, result = co.standalone_wallet.buy('coin','5m','KRW-BTC','BTC',10000,100,
                         self.at,'2026-11-15T10:05:00+09:00',29)
        self.assertEqual(result,'BOUGHT')
        co._positions[('coin','trend','5m','KRW-BTC')] = p
        co._quote = lambda market,symbol: {'bid':9000,'bid_size':1000}
        co._process_holds('coin',self.at+dt.timedelta(seconds=10))
        self.assertTrue(co.journal.stop_reentry_blocked('coin','KRW-BTC',self.at))
        with co.journal.connect() as db:
            legacy = db.execute("SELECT count(*) FROM position_state WHERE strategy='trend'").fetchone()[0]
        self.assertEqual(legacy,0, 'An MT54 stop must not create an MT43 position row')
        report=analyze(self.root,'coin')
        self.assertEqual(report['closed'],1, 'One closed trade must not count twice')

    def test_original_at_ma_mode_survives_market_trend_monitor_failure(self):
        co=Coordinator(self.rt)
        co._update_score=lambda m: co.modes.__setitem__(m,'ORIGINAL')
        def broken(_market):
            raise RuntimeError('temporary history unavailable')
        co._process_closed=broken
        co.stop_event.wait=lambda _:co.stop_event.set()
        co._loop('coin')
        self.assertEqual(co.mode('coin'),'ORIGINAL')
        self.assertIn('COORD:',co._last_fetch_error.get('coin',''))

    def test_same_bar_rebuy_after_closed_position_is_safe_and_idempotent(self):
        p=self.buy()
        self.wallet.sell(p,9000,self.at+dt.timedelta(seconds=3),
                         'MT_STOP_9PCT',bid_size=1000)
        first_cash=self.wallet.allocation('coin','5m')['cash']
        second,status=self.wallet.buy('coin','5m','KRW-BTC','BTC',10000,100,
                         self.at+dt.timedelta(seconds=7),
                         '2026-11-15T10:05:00+09:00',29)
        self.assertIsNone(second)
        self.assertEqual(status,'ENTRY_ALREADY_PROCESSED_BAR')
        self.assertEqual(self.wallet.allocation('coin','5m')['cash'],first_cash)
        with self.wallet._connect('coin') as c:
            count=c.execute("SELECT count(*) FROM executions WHERE side='BUY'").fetchone()[0]
        self.assertEqual(count,1)

    def test_stale_position_checkpoint_cannot_reopen_closed_sell(self):
        p=self.buy()
        stale=copy.deepcopy(p)
        self.wallet.sell(p,9000,self.at+dt.timedelta(seconds=3),
                         'MT_STOP_9PCT',bid_size=1000)
        self.wallet.save_position(stale)
        self.assertEqual(self.wallet.open_positions('coin'), [])
        self.assertEqual(self.wallet.allocation('coin','5m')['positions'],0)

    def test_existing_legacy_duplicate_mt54_row_is_read_once_in_trend_ai(self):
        co=Coordinator(self.rt)
        co.standalone_wallet.legacy_owned=lambda market,symbol:False
        p,status=co.standalone_wallet.buy('coin','5m','KRW-BTC','BTC',10000,100,
                     self.at,'2026-11-15T10:05:00+09:00',29)
        self.assertEqual(status,'BOUGHT')
        sold,status=co.standalone_wallet.sell(p,9000,self.at+dt.timedelta(seconds=4),
                                             'MT_STOP_9PCT',bid_size=1000)
        self.assertEqual(status,'SOLD')
        # v0.7.54 already released can have created this duplicate in MT43.
        # Read-only research must tolerate it without deleting original rows.
        co.journal.save_position(p,p.position_id)
        self.assertEqual(analyze(self.root,'coin')['closed'],1)

    def test_uncertain_buy_does_not_create_virtual_fill(self):
        co=Coordinator(self.rt)
        now=self.at
        finish=dt.datetime(2026,11,15,10,5,tzinfo=mt.KST)
        co._candidates['coin']={'KRW-BTC':('CRYPTO','KRW-BTC')}
        co._quote=lambda market,code:{'trade':75,'ask':75,'bid':75,
                                     'ask_size':1000,'bid_size':1000}
        co._closed_series=lambda m,code,tf,end:[{'close':100-i,'end':end-dt.timedelta(seconds=300*(21-i))}
                              for i in range(21)] if tf=='5m' else []
        co.journal.save_latch('coin','KRW-BTC','5m',mt.SignalLatch(True))
        co._prechecked[('coin','KRW-BTC','5m',finish.isoformat())]=25
        co.standalone_wallet.buy=lambda *a,**kw: (_ for _ in ()).throw(
                                                  RuntimeError('UNKNOWN_BROKER_OUTCOME'))
        co._preclose_candidates('coin',now)
        self.assertEqual(co._virtual,{})
        signals=[r for r in co.journal.events_recent(20)
                 if r['event_key'].startswith('MT54:SIG:')]
        self.assertEqual(len(signals),1)
        self.assertEqual(signals[0]['mode'],'UNSAFE')

    def test_sell_same_second_retries_do_not_duplicate_event_or_raise(self):
        p=self.buy()
        first,state=self.wallet.sell(p,9000,self.at+dt.timedelta(seconds=3),
                                     'MT_STOP_9PCT',bid_size=1)
        self.assertEqual(state,'SOLD')
        self.assertGreater(p.qty,0)
        second,state=self.wallet.sell(p,9000,self.at+dt.timedelta(seconds=3,milliseconds=150),
                                      'MT_STOP_9PCT',bid_size=1)
        self.assertEqual(second,0)
        self.assertEqual(state,'SALE_ALREADY_PROCESSED_SECOND')
        with self.wallet._connect('coin') as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM executions WHERE side='SELL'").fetchone()[0],1)

    def test_failed_sell_database_commit_keeps_in_memory_position_unchanged(self):
        p=self.buy()
        before=copy.deepcopy(p.to_dict())
        balance=self.wallet.allocation('coin','5m')['cash']
        with self.wallet._connect('coin') as c:
            c.execute("""CREATE TRIGGER mt54_reject_sell BEFORE INSERT ON executions
                      WHEN NEW.side='SELL'
                      BEGIN SELECT RAISE(ABORT,'INJECTED_LEDGER_FAILURE'); END""")
        with self.assertRaises(Exception):
            self.wallet.sell(p,9000,self.at+dt.timedelta(seconds=3),
                             'MT_STOP_9PCT',bid_size=1000)
        # Latest quote may change on a failed exit, but sale quantity/stage
        # cannot change unless the execution and cash delta commit together.
        for field in ('qty','stage','tp1_sold','tp2_sold','last_action'):
            self.assertEqual(p.to_dict()[field],before[field])
        self.assertEqual(self.wallet.allocation('coin','5m')['cash'],balance)
        self.assertEqual(len(self.wallet.open_positions('coin')),1)

    def test_stop_reentry_safety_survives_missing_optional_old_journal_marker(self):
        p=self.buy()
        self.wallet.sell(p,9000,self.at+dt.timedelta(seconds=3),
                         'MT_STOP_9PCT',bid_size=1000)
        fresh=TrendWalletV054(self.rt)
        self.assertTrue(fresh.stop_reentry_blocked('coin','KRW-BTC',self.at))
        self.assertFalse(fresh.stop_reentry_blocked('coin','KRW-BTC',self.at+dt.timedelta(days=1)))

    def test_old_precheck_and_order_cache_bounded_after_many_bars(self):
        co=Coordinator(self.rt)
        now=dt.datetime(2026,11,15,10,10,tzinfo=mt.KST)
        past=(now-dt.timedelta(days=3)).isoformat()
        for i in range(3500):
            key=('coin','KRW-A'+str(i),'5m',past)
            co._prechecked[key]=25
            co._ordered.add(key)
        co._preclose_candidates('coin',now)
        self.assertLessEqual(len(co._prechecked),100)
        self.assertLessEqual(len(co._ordered),100)

    def test_new_process_prunes_old_cache_even_when_uptime_below_five_minutes(self):
        from unittest.mock import patch
        co=Coordinator(self.rt)
        now=dt.datetime(2026,11,15,10,10,tzinfo=mt.KST)
        past=(now-dt.timedelta(days=3)).isoformat()
        for i in range(3500):
            key=('coin','KRW-A'+str(i),'5m',past)
            co._prechecked[key]=25
            co._ordered.add(key)
        with patch('market_trend_runtime_v043.time.monotonic',return_value=20.0):
            co._prune_evaluation_cache(now)
        self.assertLess(len(co._prechecked),100)
        self.assertLess(len(co._ordered),100)

    def test_partial_profit_sales_survive_restart_and_keep_frame_ledger(self):
        p=self.buy()
        p.close_entry_bar(p.bar_key,10000,9800,29.0)
        p.__dict__['bar_finalized']=True
        self.wallet.save_position(p)
        sold,state=self.wallet.sell(p,p.target1,self.at+dt.timedelta(seconds=1),
                                    'MT_TP1',bid_size=1000)
        self.assertEqual(state,'SOLD')
        self.assertGreater(sold,0)
        self.assertEqual(p.stage,1)
        recovered=self.wallet.open_positions('coin')[0]
        self.assertEqual(recovered.stage,1)
        self.assertAlmostEqual(recovered.qty,p.qty,places=7)
        sold,state=self.wallet.sell(recovered,recovered.target2,
                        self.at+dt.timedelta(seconds=2),'MT_TP2',bid_size=1000)
        self.assertEqual(state,'SOLD')
        self.assertEqual(recovered.stage,2)
        recovered=self.wallet.open_positions('coin')[0]
        self.assertEqual(recovered.stage,2)
        sold,state=self.wallet.sell(recovered,recovered.target2-1,
                        self.at+dt.timedelta(seconds=3),'MT_TP3_TRAIL',bid_size=1000)
        self.assertEqual(state,'SOLD')
        self.assertEqual(self.wallet.open_positions('coin'),[])
        with self.wallet._connect('coin') as c:
            fills=c.execute('SELECT side,net_pnl FROM executions ORDER BY id').fetchall()
        self.assertEqual(len(fills),4)
        self.assertEqual([r['side'] for r in fills],['BUY','SELL','SELL','SELL'])
        self.assertEqual(sum(int(r['net_pnl']) for r in fills),
                         self.wallet.allocation('coin','5m')['realized'])
        self.assertEqual(self.wallet.allocation('coin','30m')['cash'],2_000_000)

    def test_old_open_position_is_not_hidden_by_recent_closed_history(self):
        p=self.buy()
        with self.wallet._connect('coin') as c:
            template=c.execute('SELECT * FROM positions WHERE id=?',(p.position_id,)).fetchone()
            for n in range(505):
                c.execute("""INSERT INTO positions(tf,symbol,name,entry_at,entry,
                    qty0,qty,entry_fee,mark,status,execution_mode,broker_order_id,
                    bar_key,payload,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (template['tf'],'KRW-SOLD'+str(n),'old',template['entry_at'],
                     10000,1,0,0,10000,'CLOSED','PAPER','',
                     template['bar_key'],template['payload'],template['updated_at']))
        rows=self.wallet.snapshot('coin')['positions']
        self.assertTrue(any(x['id']==p.position_id and x['status']=='TREND_OPEN'
                            for x in rows))
        self.assertLessEqual(len(rows),505)

    def test_audit_release_increments_versioncode_for_in_place_upgrade(self):
        gradle=(ROOT/'app/build.gradle').read_text('utf8')
        self.assertRegex(gradle,r'versionCode (?:62|63|64|65|66|67|68|69)')
        self.assertIn("versionName '0.7.62-MA-EPOCH-RESEARCH'", gradle)

    def test_trend_ui_is_not_mislabeled_as_actual_broker_account(self):
        ui=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text('utf8')
        self.assertIn('"trend".equals(strategy)?"● 독립 마켓트렌드":"● 전략별 계좌"',ui)
        self.assertIn('"trend".equals(strategy)?"업비트 · KRW TOP40"',ui)

    def test_broker_overfill_cannot_exceed_requested_slot_budget(self):
        def impossible_fill(market,symbol,side,qty,price,event_key,reason):
            return {'qty':float(qty)*1.8,'price':price,'order_id':'','mode':'PAPER'}
        self.wallet._order_fill=impossible_fill
        first_cash=self.wallet.allocation('coin','5m')['cash']
        try:
            p,status=self.wallet.buy('coin','5m','KRW-BTC','BTC',10000,100,
                           self.at,'2026-11-15T10:05:00+09:00',29)
        except Exception as exc:
            # Failed closed with an explicit unsafe fill exception is permitted.
            self.assertIn('FILL',str(exc).upper())
            p=None
        self.assertIsNone(p)
        self.assertEqual(self.wallet.allocation('coin','5m')['cash'],first_cash)
        self.assertEqual(self.wallet.open_positions('coin'),[])


if __name__=='__main__': unittest.main()
