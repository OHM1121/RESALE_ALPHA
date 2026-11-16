"""Stock display uses only recent per-symbol original Kiwoom 0B prints.

Synthetic replay; NEVER starts Kiwoom, accesses broker credentials or writes
ledger SQLite. Tests AT, MA, GMO, CLOSE, Trend, KRX/NXT, reconnect, stale,
out-of-order event, UI timestamp TTL and callback transparency.
"""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import stock_quote_truth_v066 as q

T=dt.datetime(2026,11,16,10,45,0,tzinfo=q.KST)


def tick(symbol='005930',venue='KRX',price=73400,when=T,
         receive=T,exchange=None):
    exchange=exchange if exchange is not None else when
    return (symbol,'KOSPI','삼성전자','20261116104500',price,123,
            receive.isoformat(),venue,exchange.isoformat(),price+100,price-100)


class Runner:
    def __init__(self):
        self.calls=[]
        self._confirmed_ws_session=True
    def _new_tick(self,tx):
        self.calls.append(tx)
        return 919


def sample():
    def p(code,mark,entry=70000):
        return {'code':code,'name':'보유', 'status':'OPEN', 'qty':10,
                'entry_price':entry,'last_price':mark,
                'mark':mark,'paper_pnl':(mark-entry)*10}
    return {'stock':{
        'at':{'ledger':{'positions':[p('005930',67500)],'ma_subledger':{
            'positions':[p('005930',68000)]},'gmo':{'positions':[p('005930',70000)]},
            'executions':[{'side':'BUY','price':70100}]}},
        'cw':{'ledger':{'positions':[p('005930',65000)]}},
        'trend':{'ledger':{'positions':[p('005930',69000)]}}},
        'coin':{'ma':{'ledger':{'positions':[{'code':'KRW-BTC','last_price':99}]}}}}


class StockDisplayTests(unittest.TestCase):
    def setUp(self):
        self.r=Runner()
        q.install_ui_quote_tap(self.r)

    def test_one_existing_socket_observer_never_changes_original_tick_path(self):
        self.assertEqual(self.r._new_tick(tick()),919)
        self.assertEqual(len(self.r.calls),1)
        self.assertTrue(q.install_ui_quote_tap(self.r))
        self.assertEqual(self.r._new_tick(tick()),919)
        self.assertEqual(len(self.r.calls),2)
        self.assertEqual(len(self.r._v066_quote_by_venue),1)

    def test_all_five_stock_strategies_same_quote_without_ledger_mutation(self):
        self.r._new_tick(tick())
        packet=sample()
        result=q.annotate_stock_packet(packet,self.r,now=T+dt.timedelta(seconds=2))
        ledger=result['stock']['at']['ledger']
        for row in [ledger['positions'][0],ledger['ma_subledger']['positions'][0],
                    ledger['gmo']['positions'][0],
                    result['stock']['cw']['ledger']['positions'][0],
                    result['stock']['trend']['ledger']['positions'][0]]:
            with self.subTest(old=row['ledger_last_price']):
                self.assertEqual(row['last_price'],73400)
                self.assertEqual(row['stock_quote_status'],'FRESH')
                self.assertEqual(row['stock_quote_venue'],'KRX')
                self.assertEqual(row['stock_quote_source'],'KIWOOM_REAL_0B')
                self.assertGreater(row['stock_quote_expires_ms'],0)
                self.assertEqual(row['current_price'],73400)
                self.assertEqual(row['stock_quote_unrealized_estimate'],34000.0)
        self.assertEqual(ledger['positions'][0]['ledger_last_price'],67500)
        self.assertEqual(ledger['ma_subledger']['positions'][0]['ledger_last_price'],68000)
        self.assertEqual(ledger['gmo']['positions'][0]['ledger_last_price'],70000)
        self.assertEqual(ledger['executions'][0]['price'],70100)
        self.assertEqual(result['coin']['ma']['ledger']['positions'][0]['last_price'],99)

    def test_stale_quote_must_not_disguise_ledger_mark_as_current(self):
        self.r._new_tick(tick())
        p=q.annotate_stock_packet(sample(),self.r,now=T+dt.timedelta(seconds=11))
        row=p['stock']['at']['ledger']['gmo']['positions'][0]
        self.assertNotIn('last_price',row)
        self.assertNotIn('paper_pnl',row)
        self.assertNotIn('mark',row)
        self.assertEqual(row['ledger_last_price'],70000)
        self.assertEqual(row['stock_quote_status'],'STALE')
        self.assertEqual(row['stock_quote_last_observed_price'],73400)
        self.assertEqual(row['stock_quote_expires_ms'],0)

    def test_no_quote_cannot_use_entry_price_as_gmo_current(self):
        p=q.annotate_stock_packet(sample(),self.r,now=T)
        row=p['stock']['at']['ledger']['gmo']['positions'][0]
        self.assertEqual(row['stock_quote_status'],'NOT_RECEIVED')
        self.assertNotIn('last_price',row)
        self.assertNotIn('paper_pnl',row)
        self.assertEqual(row['ledger_last_price'],70000)

    def test_wrong_symbol_never_fills_other_symbol(self):
        self.r._new_tick(tick('000660',price=133000))
        p=q.annotate_stock_packet(sample(),self.r,now=T)
        row=p['stock']['at']['ledger']['gmo']['positions'][0]
        self.assertEqual(row['stock_quote_status'],'NOT_RECEIVED')

    def test_venue_not_merged_and_explicit_venue_label(self):
        self.r._new_tick(tick(price=73400))
        self.r._new_tick(tick(venue='NXT',price=75000,exchange=T+dt.timedelta(seconds=1),
                              receive=T+dt.timedelta(seconds=1)))
        a=q.annotate_stock_packet(sample(),self.r,now=T+dt.timedelta(seconds=2))
        row=a['stock']['at']['ledger']['gmo']['positions'][0]
        self.assertEqual((row['last_price'],row['stock_quote_venue']),(73400,'KRX'))
        self.assertEqual(len(self.r._v066_quote_by_venue),2)

    def test_nxt_only_is_reported_as_nxt_not_krx(self):
        self.r._new_tick(tick(venue='NXT',price=75300))
        a=q.annotate_stock_packet(sample(),self.r,now=T+dt.timedelta(seconds=1))
        row=a['stock']['at']['ledger']['gmo']['positions'][0]
        self.assertEqual((row['last_price'],row['stock_quote_venue']),(75300,'NXT'))

    def test_future_exchange_stamp_rejected(self):
        self.r._new_tick(tick(exchange=T+dt.timedelta(seconds=10)))
        self.assertEqual(self.r._v066_quote_by_venue,{})

    def test_old_exchange_with_new_receipt_rejected(self):
        self.r._new_tick(tick(exchange=T-dt.timedelta(seconds=7),receive=T))
        self.assertEqual(self.r._v066_quote_by_venue,{})

    def test_out_of_order_older_trade_cannot_replace_latest(self):
        self.r._new_tick(tick(price=73400))
        self.r._new_tick(tick(price=73000,exchange=T-dt.timedelta(seconds=1),
                              receive=T+dt.timedelta(seconds=1)))
        p=q.annotate_stock_packet(sample(),self.r,now=T+dt.timedelta(seconds=2))
        self.assertEqual(p['stock']['at']['ledger']['gmo']['positions'][0]['last_price'],73400)

    def test_unconfirmed_ws_even_recent_quote_is_not_current(self):
        self.r._new_tick(tick())
        self.r._confirmed_ws_session=False
        p=q.annotate_stock_packet(sample(),self.r,now=T+dt.timedelta(seconds=2))
        row=p['stock']['at']['ledger']['gmo']['positions'][0]
        self.assertNotIn('last_price',row)
        self.assertEqual(row['stock_quote_status'],'STALE')

    def test_bad_code_and_malformed_price_never_refreshed(self):
        self.r._new_tick(tick('KRW-BTC'))
        self.r._new_tick(tick(price=0))
        self.r._new_tick(tick(price=True))
        self.assertEqual(len(self.r._v066_quote_by_venue),0)

    def test_original_execution_and_cash_are_never_edited(self):
        self.r._new_tick(tick())
        p=sample()
        p['stock']['at']['ledger']['accounts']=[{'cash':120000,'seed':200000}]
        original=json.loads(json.dumps(p))
        q.annotate_stock_packet(p,self.r,now=T+dt.timedelta(seconds=1))
        self.assertEqual(p['stock']['at']['ledger']['accounts'],original['stock']['at']['ledger']['accounts'])
        self.assertEqual(p['stock']['at']['ledger']['executions'],original['stock']['at']['ledger']['executions'])

    def test_stale_after_feed_halt_does_not_reuse_last_seen_as_current(self):
        self.r._new_tick(tick())
        self.r._confirmed_ws_session=False
        p=q.annotate_stock_packet(sample(),self.r,now=T+dt.timedelta(seconds=1))
        self.assertEqual(p['stock']['at']['ledger']['positions'][0]['stock_quote_source'],
                         'NO_FRESH_VERIFIED_0B')

    def test_runtime_wrapper_preserves_packet_type_and_calls_original(self):
        class Core:
            class Runtime:
                hub_records=lambda self:json.dumps(sample(),ensure_ascii=False)
        q.install(Core)
        class Runtime:
            stock_runner=self.r
        inst=Core.Runtime()
        inst.stock_runner=self.r
        val=inst.hub_records()
        self.assertIsInstance(val,str)
        parsed=json.loads(val)
        self.assertIn('stock_quote_checked_v066',
                      parsed['stock']['at']['ledger']['positions'][0])

if __name__=='__main__':
    unittest.main()
