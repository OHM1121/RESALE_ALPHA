"""V092 all-eight offline/network/quote/reference evidence regression.

No API call. All fills remain original strategy-only. These tests prove
routing decisions and held queue safety without changing real user wallets.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from unified_recovery_v092 import (
    STRATEGIES,SCHEMA,EXIT_PROOF,plan_scenario,HeldFirstQueue,
    RecoveryObserver,_price_book_recent,
)

BASE=dict(process_running=True,market_open=True,internet_ok=True,
          ws_connected=True,subscribed=True,exit_proof=True,
          entry_proof=True,reference_ready=True,minute_ready=True,
          has_holding=True,outstanding_order=False,recovering=False,
          policy_proof=True)

def decide(**kw):
    x=dict(BASE);x.update(kw)
    return plan_scenario(**x)

class AllScenarios(unittest.TestCase):
    def test_eight_original_strategies_have_explicit_source_contract(self):
        self.assertEqual(set(EXIT_PROOF),
                         {m+"/"+s for m,scopes in STRATEGIES.items() for s in scopes})
        self.assertEqual([len(STRATEGIES['stock']),len(STRATEGIES['coin'])],[5,3])

    def test_network_shutdown_cannot_ever_claim_a_fill(self):
        for key in EXIT_PROOF:
            for inputs,state in (
                ({'process_running':False},'PROCESS_STOPPED'),
                ({'internet_ok':False},'NETWORK_OFFLINE'),
                ({'market_open':False},'MARKET_CLOSED'),
                ({'ws_connected':False},'BROKER_FEED_DOWN'),
                ({'subscribed':False},'HELD_NOT_SUBSCRIBED'),
                ({'outstanding_order':True},'UNRESOLVED_EXECUTION'),
                ({'policy_proof':False},'POLICY_MISSING'),
                ({'exit_proof':False},'NO_FRESH_EXIT_EVIDENCE')):
                out=decide(**inputs)
                self.assertEqual(out['scenario'],state,(key,inputs))
                self.assertEqual(out['fill_allowed'],False,key)
                self.assertEqual(out['exit'],'WAIT',(key,state))

    def test_missing_parent_or_one_minute_does_not_disable_held_price_exit(self):
        for k in ('minute_ready','reference_ready'):
            out=decide(**{k:False})
            self.assertEqual(out['scenario'],'CANDLE_GAP_EXIT_FIRST')
            self.assertEqual(out['exit'],'NATIVE_EXIT_EVALUATION')
            self.assertEqual(out['entry'],'WAIT')
            self.assertIn('REPAIR_OFFICIAL_CLOSED_FRAMES_READ_ONLY',out['repair'])
            self.assertFalse(out['fill_allowed'])

    def test_reconnection_rechecks_current_native_stages_and_blocks_past_fills(self):
        out=decide(recovering=True)
        self.assertEqual(out['scenario'],'RESTART_CATCH_UP')
        self.assertEqual(out['entry'],'NATIVE_ENTRY_EVALUATION')
        self.assertEqual(out['exit'],'NATIVE_EXIT_EVALUATION')
        self.assertIn('NATIVE_HELD_EXIT_STAGES_ON_NEW_LIVE_QUOTE',out['repair'])
        self.assertFalse(out['fill_allowed'])

    def test_no_holding_only_checks_current_entry_and_never_requires_exit(self):
        out=decide(has_holding=False,entry_proof=False,exit_proof=False)
        self.assertEqual(out['scenario'],'WAIT_ENTRY_SOURCE')
        self.assertEqual(out['exit'],'NO_HOLDING')
        self.assertEqual(out['entry'],'WAIT')

    def test_full_live_route_does_not_skip_native_fill_verification(self):
        out=decide()
        self.assertEqual(out['scenario'],'LIVE_VERIFIED')
        self.assertEqual(out['exit'],'NATIVE_EXIT_EVALUATION')
        self.assertEqual(out['entry'],'NATIVE_ENTRY_EVALUATION')
        self.assertFalse(out['fill_allowed'])


class HeldStockQueue(unittest.TestCase):
    def test_held_stock_exit_jumps_ahead_of_backlogged_candidates(self):
        q=HeldFirstQueue(10,held=lambda e:e[0]=='HELD')
        for code in ('A','B','C'):q.put_nowait((code,1))
        q.put_nowait(('HELD',1))
        self.assertEqual(q.get_nowait(),('HELD',1));q.task_done()
        self.assertEqual([q.get_nowait()[0] for _ in range(3)],['A','B','C'])
        for _ in range(3):q.task_done()
        q.join()

    def test_fairness_one_candidate_after_eight_held(self):
        q=HeldFirstQueue(30,held=lambda e:e[0]=='HELD')
        for i in range(10):q.put_nowait(('HELD',i))
        q.put_nowait(('OTHER',1))
        result=[q.get_nowait()[0] for _ in range(9)]
        self.assertEqual(result,['HELD']*8+['OTHER'])
        for _ in range(9):q.task_done()
        self.assertEqual(q.qsize(),2)

    def test_bounded_overflow_protects_held_without_retiming_any_event(self):
        q=HeldFirstQueue(2,held=lambda e:e[0]=='HELD')
        q.put_nowait(('A','old-1'));q.put_nowait(('B','old-2'))
        q.put_nowait(('HELD','new-authentic-ws-0b'))
        self.assertEqual(q.dropped_candidate_on_full,1)
        self.assertEqual(q.get_nowait(),('HELD','new-authentic-ws-0b'))
        q.task_done();q.get_nowait();q.task_done();q.join()
        self.assertEqual(q.qsize(),0)


class RecoveryPersistence(unittest.TestCase):
    def test_scenario_sidecar_only_and_all_eight_visible(self):
        with tempfile.TemporaryDirectory() as t:
            o=RecoveryObserver(t)
            stock=Mock()
            stock._paper_interest_held=frozenset(['005930'])
            stock.ma.pinned_stocks.return_value={}
            stock.gmo.holding_codes.return_value=[]
            stock.preload_gate.snapshot.return_value={'ready':False}
            stock._subscribed_groups={}
            stock.engine.book._bid_depth={}
            coin=Mock()
            coin.preload_gate.snapshot.return_value={'ready':False}
            coin._v083_active_codes=frozenset()
            coin._ws_connected=False
            coin.ledger.watched_codes.return_value=[]
            coin.ma_ledger.watched_codes.return_value=[]
            coin.last_book={}
            runtime=Mock()
            runtime.stock_runner=stock
            runtime.coin_runner=coin
            runtime.market_trend=None
            result=o.snapshot(runtime,{'stock':{'connected':False}})
            self.assertEqual(result['schema'],SCHEMA)
            self.assertEqual(len(result['scenario_by_strategy']),8)
            self.assertEqual(result['api_requests_added'],0)
            self.assertFalse(result['new_real_orders_enabled'])
            self.assertTrue(result['history_backfill_is_not_execution'])
            self.assertTrue((Path(t)/'recovery_scenarios_v092.json').exists())
            previous=json.loads((Path(t)/'recovery_scenarios_v092.json').read_text())
            self.assertEqual(previous['schema'],SCHEMA)
            o.stopped()
            assert (Path(t)/'recovery_scenarios_v092.json').exists()

    def test_stale_book_never_becomes_execution_proof(self):
        import time
        now=time.time()
        self.assertFalse(_price_book_recent({'bid':100,'bid_size':1,'timestamp':(now-5)*1000},now))
        self.assertFalse(_price_book_recent({'bid':100,'bid_size':0,'timestamp':now*1000},now))
        self.assertTrue(_price_book_recent({'bid':100,'bid_size':3,'timestamp':now*1000},now))

class PortraitHeader(unittest.TestCase):
    def test_version_is_beside_title_on_narrow_header(self):
        ui=(Path(__file__).resolve().parents[1]/
            'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        self.assertIn('String installedVersion=marketVersionLabel();',ui)
        self.assertIn('if(!wideHeader)',ui)
        self.assertIn('brandLine.addView(ver,',ui)
        self.assertIn('TextView ver=text("  ·  "+installedVersion,wideHeader?12:11);',ui)

if __name__=='__main__':unittest.main()
