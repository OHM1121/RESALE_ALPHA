"""v060 carryover: original quantity policy and MA special retained unchanged.

These tests explicitly REVOKE the incorrect v059 turnover-volume and
mandatory-MA-record assumptions while retaining audited UI/LIVE coverage.
"""
import datetime as dt
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BASE/'app/src/main/python'))
import reference_integrity_v059 as guard
import phone_policy_reference_v059 as timeline
from market_trend_wallet_v054 import TrendWalletV054
from market_trend_v043 import KST


def coin_history():
    end=dt.datetime(2026,11,15,23,0,tzinfo=KST)
    start=end-dt.timedelta(minutes=30*146)
    rows=[]
    for i in range(146):
        t=start+dt.timedelta(minutes=30*i)
        turnover=Decimal(1000000)
        quantity=Decimal(100)
        if (t.hour,t.minute,t.day)==(16,0,15):
            turnover=Decimal(236000000);quantity=Decimal(230)
        if (t.hour,t.minute,t.day)==(22,30,15):
            turnover=Decimal(240000000);quantity=Decimal(240)
        rows.append({'start':t.astimezone(dt.timezone.utc),
                    'open':Decimal(100),'high':Decimal(104),
                    'low':Decimal(99),'close':Decimal(103),
                    'volume':quantity,'turnover':turnover})
    assert rows[-1]['start'].astimezone(KST).strftime('%m-%d %H:%M')=='11-15 22:30'
    return rows


class TestV059(unittest.TestCase):
    def test_original_reference_uses_quantity_not_turnover(self):
        h=coin_history()
        chosen=guard.newest_superseding_record(
            h,'30m',dt.datetime(2026,11,15,16,0,tzinfo=KST))
        self.assertEqual(chosen['metric'],'BASE_ASSET_QUANTITY')
        self.assertEqual(chosen['current'],'240')
        self.assertEqual(chosen['prior_max'],'230')
        self.assertTrue(chosen['ref_start'].endswith('13:30:00+00:00'))
        for row in h:
            if row['start']==h[-1]['start']:
                row['volume']=Decimal(220)
        self.assertIsNone(guard.newest_superseding_record(
            h,'30m',dt.datetime(2026,11,15,16,0,tzinfo=KST)))

    def test_missing_full_48h_window_is_not_a_record(self):
        h=coin_history()[-45:]
        self.assertIsNone(guard.newest_superseding_record(
            h,'30m',dt.datetime(2026,11,15,16,0,tzinfo=KST)))

    def test_original_ma_special_has_no_mandatory_volume_record(self):
        from coin_ma_engine import ma_reference_decision
        ref=dt.datetime(2026,11,15,22,30,tzinfo=KST).astimezone(dt.timezone.utc)
        bars=[]
        for i in range(122):
            at=ref-dt.timedelta(minutes=30*(121-i))
            close=Decimal(124.2)-Decimal(i)*Decimal('.20')
            opening=close+(Decimal(3) if i==121 else Decimal('.1'))
            vol=Decimal(100)
            if i==60:vol=Decimal(2000)
            if i==121:vol=Decimal(1000)
            bars.append({'start':at,'open':opening,'close':close,
                         'low':close-Decimal('.5'),'high':opening+Decimal('.5'),
                         'volume':vol})
        audit,candidate=ma_reference_decision(bars,'30m','KRW-NCT',Decimal('.01'),
            dt.datetime(2026,11,16,0,50,35,tzinfo=KST))
        self.assertEqual(audit['reason'],'MA_BEAR_SPECIAL_PLUS10')
        self.assertEqual(candidate['limit'],'99.50')
        self.assertEqual(candidate['features']['ma_down'],5)
        self.assertTrue(candidate['features']['ma_bear_override'])

    def test_position_id_join_distinguishes_two_candidates_for_same_symbol(self):
        with tempfile.TemporaryDirectory() as root:
            d=Path(root)/'stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1'
            d.mkdir(parents=True)
            db=d/'ma_paper.sqlite3'
            with sqlite3.connect(db) as c:
                c.execute("CREATE TABLE positions(id INTEGER PRIMARY KEY,candidate_id INTEGER,entry_at TEXT)")
                c.execute("CREATE TABLE candidates(id INTEGER PRIMARY KEY,ref_ts TEXT,features TEXT)")
                c.execute("INSERT INTO candidates VALUES(10,'20261115160000','{}')")
                c.execute("INSERT INTO candidates VALUES(20,'20261115223000','{}')")
                c.execute("INSERT INTO positions VALUES(1,10,'2026-11-15T22:00:00+09:00')")
                c.execute("INSERT INTO positions VALUES(2,20,'2026-11-16T00:00:00+09:00')")
            trend={'positions':[{'id':1,'code':'005930','tf':'30m'},
                                {'id':2,'code':'005930','tf':'30m'}],
                   'executions':[{'position_id':2,'code':'005930','side':'BUY'}]}
            packet={'stock':{'at':{'ledger':{'ma_subledger':trend}}}}
            class Runtime:
                def __init__(self,root):self.root=root;self.coin_runner=None
            timeline.enrich_packet(Runtime(root),packet)
            self.assertEqual(trend['positions'][0]['reference_candle_at'],'20261115160000')
            self.assertEqual(trend['positions'][1]['reference_candle_at'],'20261115223000')
            self.assertEqual(trend['executions'][0]['reference_candle_at'],'20261115223000')
            self.assertEqual(trend['positions'][0]['source_buy_at'],
                             '2026-11-15T22:00:00+09:00')

    def test_trend_market_live_uses_existing_lease_and_records_confirmed_fill(self):
        class Broker:
            def __init__(self):self.calls=[]
            def ready(self):return True
            def fok(self,*args,**kw):
                self.calls.append((args,kw))
                return {'qty':float(args[3]),'price':float(args[4]),
                        'order_id':'TEST_CONFIRMED_FOK'}
        class Rt:
            def __init__(self,root):
                self.root=Path(root);self._halted={}
                self.upbit=Broker();self.coin_runner=None;self.stock_runner=None
                self.fills=[]
                self.live={'coin':True,'stock':False}
            def live_enabled(self,market):return self.live.get(market,False)
            def on_live_fill(self,*args,**kw):self.fills.append((args,kw))
            def halt_market(self,*args):raise AssertionError('unexpected halt')
        with tempfile.TemporaryDirectory() as root:
            rt=Rt(root);w=TrendWalletV054(rt)
            self.assertTrue(w._is_live('coin'))
            now=dt.datetime(2026,11,16,1,0,tzinfo=KST)
            p,result=w.buy('coin','30m','KRW-POLYSWARM','폴리스웜',
                           10000,1000,now,now.isoformat(),29)
            self.assertEqual(result,'BOUGHT')
            self.assertEqual(p.execution_mode,'LIVE')
            self.assertEqual(len(rt.upbit.calls),1)
            self.assertEqual(rt.upbit.calls[0][1]['strategy'],'trend')
            self.assertEqual(len(rt.fills),1)
            rt.live['coin']=False
            self.assertFalse(w._is_live('coin'))
            self.assertEqual(w.sell(p,9000,now+dt.timedelta(seconds=4),
                                    'MT_STOP_9PCT',1000)[1],
                             'LIVE_POSITION_BROKER_DISABLED')
            self.assertEqual(len(rt.upbit.calls),1)

    def test_java_policy_and_holdings_have_separate_reference_line(self):
        java=(BASE/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text('utf8')
        self.assertIn('referenceTimelineOf(r,source)',java)
        self.assertIn('values.put("reference",referenceTimelineOf(record,item.source))',java)
        self.assertIn('evidencePercent3(e,"post_sale_peak_pct_from_buy")',java)
        self.assertIn('gmo_d_day',java)
        self.assertIn('close_buy_date',java)

if __name__=='__main__':unittest.main()
