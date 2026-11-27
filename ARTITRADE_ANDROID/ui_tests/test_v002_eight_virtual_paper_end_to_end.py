"""Eight real native PAPER end-to-end transactions from SYNTHETIC books.
No external exchange/API, installed device, LIVE broker or real portfolio used.
Each case requires qualifying candidate -> PENDING/watch -> native BUY fill ->
native SELL fill(s) -> position CLOSED / zero. Original wallet code is retained.
"""
from __future__ import annotations
import datetime as dt, json, tempfile, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import ma_v067_policy as pm
import ma_v067_stock_runtime as hs
import ma_v067_coin_runtime as hc
import stock_ma_engine as sm
import reference_1118_runtime as ref
import market_trend_1118_runtime as nt
import market_trend_runtime_v043 as tr
from stock_ma_engine import StockMAService
from coin_ma_engine import MACoinLedger
from d_storage import DStore,KST
from d_v19_portfolio import D19PaperEngine
from d_upbit import CoinLedger
from projectgmo.engine import Engine as GMO,Quote
from projectgmo.selector import DSignal
from at_close_engine import evaluate,PaperEngine as CloseEngine
from at_close_store import AtCloseStore
ref.install();hs.install();hc.install();nt.install()
ST=dt.datetime(2026,11,18,13,28,5,tzinfo=KST)
CT=dt.datetime(2026,11,18,9,29,55,tzinfo=KST)

class Depth:
    def __init__(self):self._items={}
    def available(self,code,venue,at,price,qty):return (int(price),max(10000,int(qty))),None
    def reserve(self,*a):return True
    def available_sell(self,*a):return 100000
    def reserve_sell(self,*a):return True

class Runtime:
    def __init__(self,root):
        self.root=Path(root)
        self.stock_runner=SimpleNamespace(
            market_map={'990003':('KOSPI','SIMULATED')},
            engine=SimpleNamespace(book=Depth()))
        self.coin_runner=None
    def live_enabled(self,market):return False
    def halt_market(self,*a):raise AssertionError('NO LIVE')

class EightRealPaperWallets(unittest.TestCase):
    def fresh(self):
        t=tempfile.TemporaryDirectory(prefix='artitrade-v002-e2e-')
        self.addCleanup(t.cleanup)
        return Path(t.name)
    def test_01_stock_at(self):
        store=DStore(self.fresh()/'stock');e=D19PaperEngine(store);code='990001'
        with store.lock,store.connect() as c:
            c.execute('INSERT INTO bars(code,market,frame,ts,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?,?,?)',
              (code,'KOSDAQ','30m','20261117133000',2600,3200,2500,2800,15000,'SYNTHETIC_0B'))
            c.execute('INSERT INTO signals(event_key,ts,tf,code,name,market,ref_ts,buy_price,target,score,features,decision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
              ('SIM_REF','20261117140000','30m',code,'SIM_AT','KOSDAQ','20261117133000',2500,2800,69.,'{"ranking_engine":"AT_DIVERSE_1_1_ACTIVE"}','PENDING'))
            sid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO v19_orders(signal_id,code,tf,venue,score,limit_won,since_ts,status) VALUES(?,?,?,?,?,?,?,?)',
              (sid,code,'30m','KRX',69.,2600,'20261117140000','PENDING'))
            order=c.execute('SELECT * FROM v19_orders WHERE signal_id=?',(sid,)).fetchone()
            self.assertEqual(order['status'],'PENDING')
            e.book._items[(code,'KRX')]=(2600,2580,10000,ST)
            import market_research_v033 as score_research
            # The real STOCK AT BUY now reads the genuine shared app-root
            # signed-score publisher, not a patched _market_score shortcut.
            # Supply a deterministic publisher under that same canonical
            # root to prove the ORIGINAL native D19 transaction path.
            key=str(store.root.parent.resolve())
            fake_publisher=SimpleNamespace(
                snapshot=lambda:{"stock":{"score":10.,"stale":False}})
            with patch.dict(score_research._WORKERS,{key:fake_publisher},clear=True),\
                patch('d_v19_portfolio.live_session',return_value=True),\
                patch.object(ref,'_market_score',return_value=10.):
                actual=e._buy_limit_depth(c,order,ST,'KRX',2600,1000)
            self.assertTrue(actual[0],actual)
        with store.connect() as c:
            p=c.execute('SELECT * FROM positions WHERE code=?',(code,)).fetchone()
            self.assertIsNotNone(p);pid=p['id'];initial=p['qty']
            self.assertIsNotNone(ref._read(c,pid))
        for n,price in enumerate((2660,2690,2760,2900,2800)):
            timestamp=(ST+dt.timedelta(seconds=20*n+20)).strftime('%Y%m%d%H%M%S')
            with store.lock,store.connect() as c:
                p=c.execute('SELECT * FROM positions WHERE id=?',(pid,)).fetchone()
                if p['status']!='OPEN':break
                e._execution_context.fill_method='LIVE_KRX_0B_TRADE_TOUCH_PAPER_ESTIMATE'
                with patch.object(ref,'_market_score',return_value=10.):
                    e._minute_exits(c,p,dict(code=code,open=price,high=price,low=price,close=price,volume=1500),timestamp)
        with store.connect() as c:
            rows=list(c.execute('SELECT side,qty FROM executions WHERE position_id=? ORDER BY id',(pid,)))
            self.assertEqual([x['side'] for x in rows],['BUY','SELL','SELL','SELL'])
            # 0.3.4 (#149): the fixture's reference candle puts the hard stop 30.8 % under the 2,600 limit
            # (gain/loss = 3 % / 30.8 % = 0.0975 <= 0.5) -> buy x 0.195: 76 shares -> 14 (5+5+4).
            # 0.3.7 (owner 2026-11-26): the stop is now BUY price - bar range = 2,600 - 700 = 1,900 (-26.9 %),
            # gain/loss = 3 % / 26.9 % = 0.1115 -> buy x 0.223: 76 shares -> 17 (6+6+5).
            self.assertEqual([x['qty'] for x in rows[1:]],[6,6,5])
            self.assertEqual(sum(x['qty'] for x in rows[1:]),initial)
            self.assertEqual(c.execute('SELECT qty FROM positions WHERE id=?',(pid,)).fetchone()[0],0)

    def test_02_coin_at(self):
        led=CoinLedger(self.fresh()/'coin'/'base');code='KRW-SIMAT'
        candidate=dict(frame='30m',market=code,ref_at='2026-11-18T09:00:00+09:00',
           ref_end='2026-11-18T09:30:00+09:00',score=80,limit='115',target='119',
           features=dict(low=105,high=120,bodytop=115,open=118,close=114,v24_first_fraction='.30'))
        self.assertTrue(led.record_candidate(candidate,'SIM_AT',CT))
        with led.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM orders').fetchone()['status'],'PENDING')
        for i,bid,ask in ((1,114,115),(3,118,119),(5,122,123),(7,126,127),(9,131,132),(11,126,127)):
            now=CT+dt.timedelta(seconds=i)
            b=dict(market=code,bid=str(bid),ask=str(ask),bid_size='10000000',ask_size='10000000',
                   timestamp=int(now.timestamp()*1000),sr_tick_size='0.01')
            with patch.object(ref,'_market_score',return_value=10.):
                led.process_book(b,now)
        with led.connect() as c:
            p=c.execute('SELECT * FROM positions WHERE market=?',(code,)).fetchone()
            rows=list(c.execute('SELECT side,qty FROM executions WHERE position_id=? ORDER BY id',(p['id'],)))
            self.assertEqual([x['side'] for x in rows],['BUY','SELL','SELL','SELL'])
            self.assertEqual((p['status'],float(p['qty'])),('CLOSED',0.))
            self.assertAlmostEqual(sum(float(x['qty']) for x in rows[1:]),float(rows[0]['qty']),places=7)

    def test_03_stock_ma(self):
        code='990002';start=dt.datetime(2026,11,18,9,30,55,tzinfo=KST)
        bars=[150.]*61+[100+i*.2 for i in range(59)]+[99.]
        self.assertEqual(pm.upward_transition_on_forming(bars,114,5)['kind'],'REVERSAL_UP')
        root=self.fresh();market=DStore(root/'market');w=StockMAService(root/'ma',market)
        with sm._connect(w.db) as c:
            c.execute('INSERT INTO candidates(tf,code,name,market,ref_ts,created_at,limit_price,target,score,special,low,close_price,high,bodytop,recovery1,first_fraction,features,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
              ('30m',code,'SIM_MA','KOSPI','20261118090000',start.isoformat(),115,119,80,0,105,114,120,115,105,.30,
               json.dumps({'ma_v067_kind':'REVERSAL_UP','ma_v067_bar_start':'2026-11-18T09:30:00+09:00'}),'PENDING'))
            self.assertEqual(c.execute('SELECT status FROM candidates').fetchone()['status'],'PENDING')
        w._refresh_watched();w.watched_codes=lambda:{code}
        book=Depth();clock=[start]
        class FakeDate(dt.datetime):
            @classmethod
            def now(cls,tz=None):return clock[0] if tz is not None else clock[0].replace(tzinfo=None)
        for i,price in enumerate((114,117,119,122,130,125)):
            clock[0]=start+dt.timedelta(seconds=i*5)
            book._items[(code,'KRX')]=(price+1,price,100000,clock[0])
            tick=(code,'KOSPI','SIM_MA',clock[0].strftime('%Y%m%d%H%M'),price,200000,
                  clock[0].isoformat(),'KRX',clock[0].isoformat())
            with patch.object(sm,'datetime',FakeDate),patch.object(sm,'live_session',return_value=True),\
                patch.object(sm,'after_market_entitlement',return_value=(True,'')),\
                patch.object(sm,'trading_sessions_elapsed',return_value=0),\
                patch('d_minute_repair_fast.exchange_stamp_is_fresh',return_value=True):
                w.on_tick(tick,book)
        with sm._connect(w.db) as c:
            p=c.execute('SELECT * FROM positions WHERE code=?',(code,)).fetchone()
            self.assertEqual((p['status'],p['qty']),('CLOSED',0))
            rows=list(c.execute('SELECT side,qty FROM executions WHERE position_id=? ORDER BY id',(p['id'],)))
            self.assertEqual([x['side'] for x in rows],['BUY','SELL','SELL','SELL'])
            self.assertEqual([x['qty'] for x in rows[1:]],[1305,1522,1520])

    def test_04_coin_ma(self):
        w=MACoinLedger(self.fresh()/'ma');code='KRW-SIMMA'
        bars=[150.]*61+[100+i*.2 for i in range(59)]+[99.]
        v=pm.upward_transition_on_forming(bars,114,5)
        self.assertEqual(v['kind'],'REVERSAL_UP')
        features=dict(ma_v067_kind='REVERSAL_UP',ma_v061_kind='TURNPOINT',
            ma_v067_bar_start='2026-11-18T09:00:00+09:00',ma_v067_submit_at=CT.isoformat(),
            ma_v067_120_delta=v['ma_120_delta'],
            ma_v067_15s_fill_until=(CT+dt.timedelta(seconds=15)).isoformat())
        rec=dict(frame='30m',market=code,ref_at='2026-11-18T09:00:00+09:00',
                 ref_end='2026-11-18T09:30:00+09:00',score=80,limit='115',target='119',features=features)
        self.assertTrue(w.record_candidate(rec,'SIM_MA',CT))
        with w.connect() as c:
            self.assertEqual(c.execute('SELECT status FROM orders').fetchone()['status'],'PENDING')
        for i,bid,ask in ((1,114,115),(3,118,119),(5,122,123),(7,125,126),(9,131,132),(11,126,127)):
            now=CT+dt.timedelta(seconds=i)
            w.process_book(dict(market=code,bid=str(bid),ask=str(ask),
                   bid_size='10000000',ask_size='10000000',
                   timestamp=int(now.timestamp()*1000),sr_tick_size='0.01'),now)
        with w.connect() as c:
            p=c.execute('SELECT * FROM positions WHERE market=?',(code,)).fetchone()
            rows=list(c.execute('SELECT side,qty,reason FROM executions WHERE position_id=? ORDER BY id',(p['id'],)))
            self.assertEqual([x['side'] for x in rows],['BUY','SELL','SELL','SELL'])
            self.assertTrue(rows[1]['reason'].startswith('MA_V1118_UP_FIRST'))
            self.assertTrue(rows[2]['reason'].startswith('MA_V1118_UP_SECOND'))
            self.assertEqual((p['status'],float(p['qty'])),('CLOSED',0.))
            self.assertAlmostEqual(sum(float(x['qty']) for x in rows[1:]),float(rows[0]['qty']),places=7)
            state=json.loads(c.execute('SELECT state_json FROM ma_v067_positions WHERE position_id=?',(p['id'],)).fetchone()['state_json'])
            self.assertEqual(state['coin_qty_step'],'0.00000001')

    def _trend(self,market,code):
        """Updated for NEW LEGACY 11.20 (explicit user supersession, issue #114).

        BUY trigger is a COMPLETED bar closing above the Bollinger MIDDLE with
        the MIDDLE slope inside (-15,+20) degrees, after the RSI<=30 watch has
        begun. The sell side of the lifecycle below is unchanged.
        """
        root=self.fresh();co=tr.Coordinator(Runtime(root));self.addCleanup(co.stop)
        t1=dt.datetime(2026,11,18,10,5,0,tzinfo=KST)
        t2=dt.datetime(2026,11,18,10,30,5,tzinfo=KST)
        closes=[100.0]*21
        closes[3],closes[7]=95.0,105.0
        for i in range(10,21):closes[i]=100.0+(i-10)*0.3    # ~16 deg, last > MIDDLE
        source=('UPBIT_CLOSED' if market=='coin' else 'LOCAL_KRX_VERIFIED')
        def history(last_end):
            return [dict(end=last_end-dt.timedelta(minutes=30*(20-i)),close=c,source=source)
                    for i,c in enumerate(closes)]
        co._candidates[market]={code:('KOSPI' if market=='stock' else 'CRYPTO',code)}
        co._history[(market,code,'30m')]=history(t1.replace(minute=0,second=0,microsecond=0))
        clock={'now':t1}
        co._decision_clock=lambda:clock['now']
        co._quote=lambda m,c:dict(trade=co._quotes[m][c][0],
              bid=co._quotes[m][c][0],ask=co._quotes[m][c][0]+.2,bid_size=100000,ask_size=100000)
        with patch.object(nt,'_ready_market',return_value=True), \
             patch.object(tr.mt if hasattr(tr,'mt') else nt.mt,'rsi20',return_value=25.0):
            # First genuine print arms the 24h watch; the 10:00 bar ended BEFORE it.
            co._quotes[market][code]=(98.5,time.monotonic())
            nt._entry_window(co,market,t1)
            self.assertEqual(co.standalone_wallet.open_positions(market),[])
            self.assertEqual(co._nl1118_watches[(market,code,'30m')]['status'],'WATCHING')
            # The 10:30 bar completes above its MIDDLE -> BUY on a fresh verified quote.
            clock['now']=t2
            co._history[(market,code,'30m')]=history(t2.replace(second=0,microsecond=0))
            co._quotes[market][code]=(103.0,time.monotonic()+.001)
            nt._entry_window(co,market,t2)
        found=co.standalone_wallet.open_positions(market)
        self.assertEqual(len(found),1,co._last_fetch_error)
        p=found[0]
        self.assertEqual(getattr(p,'entry_rule',None),'NL1120_BAR_CLOSE_ABOVE_BOLL_MID')
        self.assertEqual(getattr(p,'entry_signal_policy',None),'NL1118_RSI20_24H_BOLL_20_1_5')
        for i,price in enumerate((p.target1*1.002,p.target2*1.004,110.,105.)):
            co._quotes[market][code]=(price,time.monotonic())
            co._process_holds(market,t2+dt.timedelta(seconds=10+5*i))
        self.assertEqual(co.standalone_wallet.open_positions(market),[])
        with co.standalone_wallet._connect(market) as c:
            rows=list(c.execute('SELECT side,qty FROM executions ORDER BY id'))
            self.assertEqual([x['side'] for x in rows],['BUY','SELL','SELL','SELL'])
            self.assertAlmostEqual(sum(float(x['qty']) for x in rows[1:]),float(rows[0]['qty']),places=6)

    def test_05_stock_trend(self):self._trend('stock','990003')
    def test_06_coin_trend(self):self._trend('coin','KRW-SIMT')

    def test_07_gmo(self):
        code='990004';sig=DSignal(code,'SIM_GMO','2026-11-17','2026-11-18',
           100,95,99,98,-1000,500,800,[-100]*20,'SHORT','KOSPI','SYNTHETIC')
        e=GMO()
        d=e.record_signal(sig,score=40);self.assertTrue(d.accepted,d)
        pending=e.open_entry(code,sig.entry_date,95,'2026-11-18T09:00:03+09:00',
                             observed_price=95,observed_ask=95)
        self.assertIsNotNone(pending);self.assertEqual(len(e.positions),0)
        e.paper_match(Quote(code,'2026-11-18T09:00:04+09:00',1,95,
                ask=95,bid=94,ask_size=100000,bid_size=100000,
                venue='KRX',receive_delay_seconds=0))
        self.assertIn(code,e.positions)
        self.assertEqual(len(e.buy_fills),1)
        quantity=e.positions[code].qty
        e.paper_match(Quote(code,'2026-12-10T09:01:00+09:00',e.cfg.final_open_day,110,
                ask=111,bid=109,ask_size=100000,bid_size=100000))
        self.assertNotIn(code,e.positions)
        self.assertEqual(len(e.fills),1)
        self.assertEqual(e.fills[0]['qty'],quantity)

    def test_08_close(self):
        code='990005';features=dict(ask=100,bid=100,book_age_sec=.1,spread_pct=0.,
            heavy_ask=False,suspicious_bid=False,change_pct=0,
            market_cap_krw=2000000000000,daily_green=True,
            daily_lower_wick_ratio=.5,daily_close_position=.8,
            sma20=105.,daily_low=100.,daily_close=103.)
        self.assertTrue(evaluate('CLOSE',features,'1510')[0])
        store=AtCloseStore(self.fresh()/'cw');e=CloseEngine(store)
        ok,reason=e.open('CLOSE',dict(code=code,name='SIM_CW'),'SIM_SIGNAL',100,100000,features)
        self.assertTrue(ok,reason)
        with store.connect() as c:
            p=c.execute('SELECT * FROM positions WHERE code=?',(code,)).fetchone()
            self.assertIsNotNone(p)
            pid=p['id']
            self.assertEqual(c.execute('SELECT COUNT(*) FROM executions WHERE position_id=? AND side="BUY"',(pid,)).fetchone()[0],1)
        e.on_tick(code,102)
        with store.connect() as c:
            p=c.execute('SELECT * FROM positions WHERE id=?',(pid,)).fetchone()
            sides=[x['side'] for x in c.execute('SELECT side FROM executions WHERE position_id=? ORDER BY id',(pid,))]
            self.assertEqual(sides,['BUY','SELL'])
            self.assertEqual((p['status'],p['qty']),('CLOSED',0))

if __name__=='__main__':unittest.main(verbosity=2)
