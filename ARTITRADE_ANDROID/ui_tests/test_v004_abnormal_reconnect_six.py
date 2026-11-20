"""ARTITRADE 0.0.4: six abnormal market scenarios on ORIGINAL PAPER wallets.

Everything is synthetic; native BUY, native SELL and SQLite cash/positions
remain authoritative. Missing/stale/reordered quotes never produce fake fills.
"""
import datetime as dt
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import market_research_v033 as score
import reference_1118_runtime as ref
import market_trend_1118_runtime as nltrend
import market_trend_runtime_v043 as trend
from d_storage import DStore, KST
from d_v19_portfolio import D19PaperEngine
from d_upbit import CoinLedger
from market_trend_wallet_v054 import TrendWalletV054

ref.install()
nltrend.install()
STOCK=dt.datetime(2026,11,18,13,28,5,tzinfo=KST)
COIN=dt.datetime(2026,11,18,9,29,55,tzinfo=KST)


class Runtime:
    def __init__(self,root):
        self.root=Path(root);self.stock_runner=None;self.coin_runner=None
    def live_enabled(self,market):return False
    def halt_market(self,*a):raise AssertionError('NO_LIVE_BROKER')


class SixAbnormal(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix='ARTITRADE_V004_ABNORMAL_')
        self.addCleanup(t.cleanup);self.root=Path(t.name)

    def _stock(self):
        store=DStore(self.root/'stock'/'base');owner=D19PaperEngine(store)
        code='990001'
        with store.lock,store.connect() as c:
            c.execute('''INSERT INTO bars(code,market,frame,ts,open,high,low,close,volume,source)
                     VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (code,'KOSDAQ','30m','20261117133000',2600,3200,2500,2800,15000,'TEST_0B'))
            c.execute('''INSERT INTO signals(event_key,ts,tf,code,name,market,ref_ts,buy_price,
                      target,score,features,decision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                ('SIM_REF','20261117140000','30m',code,'SIM_STOCK','KOSDAQ',
                '20261117133000',2500,2800,69.,'{"ranking_engine":"AT_DIVERSE_1_1_ACTIVE"}','PENDING'))
            sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('''INSERT INTO v19_orders(signal_id,code,tf,venue,score,limit_won,since_ts,status)
                       VALUES(?,?,?,?,?,?,?,?)''',(sid,code,'30m','KRX',69.,2600,'20261117140000','PENDING'))
            row=c.execute('SELECT * FROM v19_orders WHERE signal_id=?',(sid,)).fetchone()
            self.assertEqual(row['status'],'PENDING')
            owner.book._items[(code,'KRX')]=(2600,2580,10000,STOCK)
            # Genuine 11.18 BUY requires the signed-score source, not
            # the old synthetic ref._market_score shortcut alone.
            class SyntheticSignedWorker:
                def snapshot(self):return {"stock":{"score":10.,"stale":False}}
            publisher_key=str(__import__('at_market_score_gate_v107')._score_worker_root(
                store.root,"stock"))
            with patch.dict(score._WORKERS,{publisher_key:SyntheticSignedWorker()},
                            clear=True),\
                patch('d_v19_portfolio.live_session',return_value=True),\
                patch.object(ref,'_market_score',return_value=10):
                ok=owner._buy_limit_depth(c,row,STOCK,'KRX',2600,1000)
            self.assertTrue(ok[0],ok)
            pid=c.execute('SELECT id FROM positions WHERE code=?',(code,)).fetchone()[0]
        return store,owner,pid

    def _stock_tick(self,store,owner,pid,price,seconds):
        when=(STOCK+dt.timedelta(seconds=seconds)).strftime('%Y%m%d%H%M%S')
        with store.lock,store.connect() as c:
            pos=c.execute('SELECT * FROM positions WHERE id=?',(pid,)).fetchone()
            if pos['status']!='OPEN':return
            owner._execution_context.fill_method='LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE'
            with patch.object(ref,'_market_score',return_value=10):
                owner._minute_exits(c,pos,dict(close=price,open=price,high=price,
                                  low=price,volume=1000),when)

    def _coin(self):
        path=self.root/'coin'/'base'
        wallet=CoinLedger(path)
        row={'frame':'30m','market':'KRW-SIMAT','ref_at':'2026-11-18T09:00:00+09:00',
            'ref_end':'2026-11-18T09:30:00+09:00','score':80,'limit':'115','target':'119',
            'features':{'low':105,'high':120,'bodytop':115,'open':118,'close':114,'v24_first_fraction':'.30'}}
        self.assertTrue(wallet.record_candidate(row,'SIM_COIN',COIN))
        self._coin_book(wallet,1,114,115)
        with wallet.connect() as c:
            pos=c.execute('SELECT * FROM positions WHERE market=?',('KRW-SIMAT',)).fetchone()
            self.assertIsNotNone(pos)
            self.assertIsNotNone(ref._read(c,pos['id']))
        return wallet

    def _coin_book(self,w,seconds,bid,ask,bid_size=10000000,stamp=None):
        now=COIN+dt.timedelta(seconds=seconds)
        stamp=int(now.timestamp()*1000) if stamp is None else stamp
        payload={'market':'KRW-SIMAT','bid':str(bid),'ask':str(ask),
                 'bid_size':str(bid_size),'ask_size':'10000000',
                 'timestamp':stamp,'sr_tick_size':'0.01'}
        with patch.object(ref,'_market_score',return_value=10):
            w.process_book(payload,now)

    def _rows(self,w):
        with w.connect() as c:
            p=c.execute('SELECT * FROM positions WHERE market=?',('KRW-SIMAT',)).fetchone()
            events=[dict(z) for z in c.execute('SELECT * FROM executions WHERE position_id=? ORDER BY id',(p['id'],))]
            return p,events,ref._read(c,p['id'])

    def test_01_restart_reopens_existing_coin_paper_position_without_second_buy(self):
        w=self._coin()
        self._coin_book(w,2,119,120)
        initial,rows,_=self._rows(w)
        self.assertEqual(len([r for r in rows if r['side']=='BUY']),1)
        # A new engine object uses the original same SQLite path, NOT a new wallet.
        restarted=CoinLedger(self.root/'coin'/'base')
        previous,events,_=self._rows(restarted)
        self.assertEqual((previous['id'],previous['qty']),(initial['id'],initial['qty']))
        self._coin_book(restarted,3,124,125)
        self._coin_book(restarted,4,135,136)
        self._coin_book(restarted,5,127,128)
        final,events,_=self._rows(restarted)
        self.assertEqual([r['side'] for r in events],['BUY','SELL','SELL','SELL'])
        self.assertEqual((final['status'],float(final['qty'])),('CLOSED',0.))

    def test_02_quote_outage_then_verified_gap_up_first_two_stages_same_book(self):
        w=self._coin()
        state,events,_=self._rows(w)
        # Exchange communication failure: a stale source cannot authorize SELL.
        now=COIN+dt.timedelta(seconds=10)
        self._coin_book(w,10,130,131,stamp=int((now-dt.timedelta(seconds=15)).timestamp()*1000))
        self.assertEqual(len(self._rows(w)[1]),1)
        # First NEW validated bid >TP1 & TP2. Both prices missed during outage.
        self._coin_book(w,11,130,131)
        p,rows,plan=self._rows(w)
        self.assertEqual([r['side'] for r in rows],['BUY','SELL','SELL'])
        self.assertEqual(plan['stage'],2)
        self.assertGreater(float(p['qty']),0)
        self._coin_book(w,12,140,141)
        self._coin_book(w,13,132,133)
        p,rows,_=self._rows(w)
        self.assertEqual((p['status'],float(p['qty'])),('CLOSED',0.))
        self.assertEqual(len([r for r in rows if r['side']=='SELL']),3)

    def test_03_gap_down_below_first_stop_exits_without_fake_price(self):
        store,owner,pid=self._stock()
        # No quote received during outage -> no trade/SELL journal added.
        with store.connect() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM executions WHERE side="SELL"').fetchone()[0],0)
        self._stock_tick(store,owner,pid,1700,30)  # verified new price below hard stop
        with store.connect() as c:
            p=c.execute('SELECT qty,status FROM positions WHERE id=?',(pid,)).fetchone()
            rows=list(c.execute('SELECT side,reason FROM executions WHERE position_id=? ORDER BY id',(pid,)))
            self.assertEqual((p['qty'],p['status']),(0,'CLOSED'))
            self.assertEqual([r['side'] for r in rows],['BUY','SELL'])
            self.assertEqual(rows[1]['reason'],'NL1118_REFERENCE_HARD_STOP')

    def test_04_reverse_and_duplicate_book_never_go_back_to_old_stage(self):
        w=self._coin()
        self._coin_book(w,3,130,131)
        p,rows,plan=self._rows(w)
        self.assertEqual(plan['stage'],2)
        self._coin_book(w,2,85,86,stamp=int((COIN+dt.timedelta(seconds=2)).timestamp()*1000))
        repeated=int((COIN+dt.timedelta(seconds=3)).timestamp()*1000)
        self._coin_book(w,3,85,86,stamp=repeated)
        p2,rows2,plan2=self._rows(w)
        self.assertEqual(len(rows2),len(rows))
        self.assertEqual(plan2['stage'],2)
        self.assertEqual(plan2.get('last_verified_book_ms'),repeated)
        # One genuinely new fresh drop may trigger a single protective SELL.
        self._coin_book(w,4,85,86)
        closed,rows3,_=self._rows(w)
        self.assertEqual((closed['status'],float(closed['qty'])),('CLOSED',0.))
        self.assertEqual(len(rows3),len(rows)+1)
        self._coin_book(w,4,85,86)
        self.assertEqual(len(self._rows(w)[1]),len(rows3))

    def test_05_limited_depth_partial_tp_recovers_on_next_quote(self):
        w=self._coin()
        self._coin_book(w,3,130,131,bid_size=10)
        p,rows,plan=self._rows(w)
        self.assertLess(float(rows[-1]['qty']),plan['qty0']*.3)
        self.assertEqual(plan['stage'],0)
        # Simulate the app stopping after a persisted partial TP execution.
        # The next engine must read existing SQLite progress, not re-buy or
        # replay already sold quantity.
        before_count=len(rows)
        before_qty=float(p['qty'])
        w=CoinLedger(self.root/'coin'/'base')
        recovered,existing,existing_plan=self._rows(w)
        self.assertEqual(len(existing),before_count)
        self.assertEqual(float(recovered['qty']),before_qty)
        self.assertEqual(existing_plan['stage'],0)
        self.assertEqual(len([e for e in existing if e['side']=='BUY']),1)
        # New verified order book with deeper liquidity completes the rest.
        self._coin_book(w,4,130,131,bid_size=10000000)
        p,rows,plan=self._rows(w)
        self.assertEqual(plan['stage'],2)
        self._coin_book(w,5,140,141)
        self._coin_book(w,6,132,133)
        final,rows,_=self._rows(w)
        self.assertEqual(float(final['qty']),0.)
        self.assertAlmostEqual(sum(float(r['qty']) for r in rows if r['side']=='SELL'),
                               float(next(r['qty'] for r in rows if r['side']=='BUY')),places=7)

    def test_06_restart_trend_gapped_quote_and_score_outage_do_not_duplicate_fills(self):
        rt=Runtime(self.root)
        co=trend.Coordinator(rt)
        self.addCleanup(co.stop)
        at=dt.datetime(2026,11,18,10,10,tzinfo=KST)
        p,outcome=co.standalone_wallet.buy('coin','30m','KRW-SIMT','TEST',100,100000,at,at.isoformat(),25)
        self.assertEqual(outcome,'BOUGHT')
        self.assertEqual(p.nl1118_policy,nltrend.POLICY)
        co.stop()
        resumed=trend.Coordinator(rt)
        self.addCleanup(resumed.stop)
        recovered=resumed._positions[('coin','trend','30m','KRW-SIMT')]
        self.assertEqual(recovered.position_id,p.position_id)
        # Offline/unknown quote -> no attempt, even if stale mark was high.
        resumed._quote=lambda market,symbol:None
        resumed._process_holds('coin',at+dt.timedelta(seconds=3))
        self.assertEqual(recovered.qty,p.original_qty)
        # Gapped real bid above BOTH targets => two distinct native verified SELLs.
        resumed._quote=lambda market,symbol:{'bid':110,'bid_size':10000000}
        resumed._process_holds('coin',at+dt.timedelta(seconds=5))
        with resumed.standalone_wallet._connect('coin') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM executions WHERE side="SELL"').fetchone()[0],2)
        resumed._process_holds('coin',at+dt.timedelta(seconds=5))
        with resumed.standalone_wallet._connect('coin') as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM executions WHERE side="SELL"').fetchone()[0],2)
        resumed._quote=lambda market,symbol:{'bid':118,'bid_size':10000000}
        resumed._process_holds('coin',at+dt.timedelta(seconds=8))
        resumed._quote=lambda market,symbol:{'bid':110,'bid_size':10000000}
        resumed._process_holds('coin',at+dt.timedelta(seconds=13))
        with resumed.standalone_wallet._connect('coin') as c:
            rows=[dict(r) for r in c.execute('SELECT * FROM executions ORDER BY id')]
            self.assertEqual([r['side'] for r in rows],['BUY','SELL','SELL','SELL'])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM positions WHERE status="TREND_OPEN"').fetchone()[0],0)


class MarketSourceRecovery(unittest.TestCase):
    def test_score_failure_invalidates_prior_positive_score_immediately(self):
        now=1795000000
        snap={'score':80.,'asof':now-2,'active_band':'80~100','coverage':1.}
        with patch('krx_session_v041.status',return_value=(True,'장중')):
            valid=score.display_snapshot('stock',snap,now=now)
            denied=score.display_snapshot('stock',snap,now=now,poll_error='YahooTimeout')
        self.assertEqual(valid['score'],80.)
        self.assertIsNone(denied['score'])
        self.assertTrue(denied['stale'])
        self.assertEqual(denied['poll_error'],'YahooTimeout')
        denied_coin=score.display_snapshot('coin',snap,now=now,poll_error='UPBIT_HTTP_500')
        self.assertIsNone(denied_coin['score'])
        again=score.display_snapshot('coin',{**snap,'asof':now},now=now)
        self.assertEqual(again['score'],80.)

    def test_upbit_recovered_ticker_snapshots_require_original_exchange_time(self):
        now=1795000000
        rows=[{'market':'KRW-'+str(i),'timestamp':int((now-10)*1000),
               'trade_price':101,'signed_change_rate':.02,'acc_trade_price_24h':10000}
              for i in range(15)]
        rows+=[{'market':'KRW-STALE','timestamp':int((now-500)*1000),
               'trade_price':999,'signed_change_rate':.99,'acc_trade_price_24h':10000},
               {'market':'KRW-UNKNOWN','trade_price':999,'signed_change_rate':.99,'acc_trade_price_24h':10000}]
        self.assertEqual(len(score._fresh_upbit_tickers(rows,now=now)),15)
        self.assertEqual(len(score._fresh_upbit_tickers(rows,now=now+400)),0)

    def test_naver_domestic_snapshot_is_used_before_old_yahoo_daily_for_current_score(self):
        def source(url,params):
            if 'm.stock.naver.com/api/index/KOSPI/basic' in url:
                return {'fluctuationsRatio':'1.7'}
            if 'm.stock.naver.com/api/index/KOSDAQ/basic' in url:
                return {'fluctuationsRatio':'-0.3'}
            if 'finance.yahoo.com' in url:
                return {'chart':{'result':[{'indicators':{'quote':[{'close':[100,100.1,100.2,100.3,100.4,100.5]}]}}]}}
            raise AssertionError(url)
        with patch.object(score,'_public_json',side_effect=source):
            data=score._fetch_stock()
        self.assertEqual(data['score'] is not None,True)
        self.assertEqual(set(data['naver_fallback']),{'KOSPI','KOSDAQ'})
        self.assertIn('KOSPI',data['received'])
        factors={x['name']:x['value'] for x in data['factors']}
        self.assertEqual(factors['KOSPI'],1.7)
        self.assertEqual(factors['KOSDAQ'],-.3)


    def test_yahoo_intraday_domestic_last_traded_at_must_be_current(self):
        now=1795000000
        def payload(_url,_params):
            return {'chart':{'result':[{'meta':{'regularMarketTime':now-30,
                'regularMarketPrice':103.,'previousClose':100.}}]}}
        with patch.object(score,'_public_json',side_effect=payload):
            self.assertEqual(score._yahoo_current_index_change('KOSPI','^KS11',now=now),3.)
        def stale(_url,_params):
            return {'chart':{'result':[{'meta':{'regularMarketTime':now-1800,
                'regularMarketPrice':103.,'previousClose':100.}}]}}
        with patch.object(score,'_public_json',side_effect=stale):
            self.assertIsNone(score._yahoo_current_index_change('KOSPI','^KS11',now=now))

    def test_worker_is_restarted_by_strategy_coordinator_without_ui_poll(self):
        from types import SimpleNamespace
        fake=SimpleNamespace(snapshot=lambda:{'coin':{'score':14.,'stale':False}})
        co=trend.Coordinator.__new__(trend.Coordinator)
        co.rt=SimpleNamespace(root='/tmp/fake_worker')
        with patch.object(score,'ensure_worker',return_value=fake) as start:
            self.assertEqual(co._read_score('coin')['score'],14.)
            start.assert_called_once_with(co.rt.root)
        with patch.object(score,'ensure_worker',side_effect=OSError('SQLite unready')):
            r=co._read_score('coin')
            self.assertIsNone(r['score'])
            self.assertTrue(r['stale'])

    def test_live_source_health_separates_stock_ws_upbit_ws_and_rest_mark(self):
        import threading
        from types import SimpleNamespace
        import market_feed_health_v004 as feed
        now=1795000000;mono=1000.
        stock=SimpleNamespace(api=object(),_confirmed_ws_session=True,
            _v066_quote_lock=threading.RLock(),
            _v066_quote_by_venue={('990001','KRX'):{'exchange_ms':int((now-1)*1000),'is_fresh':True}},
            engine=SimpleNamespace(book=SimpleNamespace(_bid_depth={})))
        coin=SimpleNamespace(client=object(),_ws_connected=True,
            _v083_lock=threading.RLock(),_v083_codes={'KRW-BTC'},
            _v083_active_codes={'KRW-BTC'},_v083_bids={'KRW-BTC'},
            _v083_active_bids={'KRW-BTC'},_last_ws_quote=mono-1,
            last_live=mono-5,_ws_last_reason='')
        rt=SimpleNamespace(root='/tmp/nonexistent_feed_health',stock_runner=stock,coin_runner=coin)
        r=feed.snapshot(rt,now=now,clock=mono)
        self.assertEqual(r['stock']['quote_status'],'RECEIVING_VERIFIED_EXCHANGE_BOOK_OR_TRADE')
        self.assertEqual(r['coin']['quote_status'],'WS_CURRENT')
        coin._ws_connected=False;coin._last_ws_quote=mono-100
        r=feed.snapshot(rt,now=now,clock=mono)
        self.assertEqual(r['coin']['quote_status'],'REST_MARK_ONLY_NOT_TRADE_PROOF')
        self.assertNotEqual(r['coin']['quote_status'],'WS_CURRENT')


if __name__=='__main__':unittest.main()