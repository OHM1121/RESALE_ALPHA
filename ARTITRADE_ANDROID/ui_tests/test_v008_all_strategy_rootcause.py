"""Cross-strategy regressions: genuine v005 MA evidence, GMO shared WS,
CLOSE as-of followups, Trend/Recovery/PostExit duplicate starts (no broker).
"""
import ast,datetime as dt,math,sqlite3,threading,json
from pathlib import Path
from types import SimpleNamespace
import unittest

BASE=Path(__file__).resolve().parents[1]/'app/src/main/python'

def extracted(filename,name,scope):
    tree=ast.parse((BASE/filename).read_text(encoding='utf8'))
    funcs=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(funcs)==1,(filename,name,len(funcs))
    env=dict(scope)
    exec(compile(ast.Module(body=[funcs[0]],type_ignores=[]),'isolated contract','exec'),env)
    return env[name]

class V008RootCause(unittest.TestCase):
    def test_ma_5m_turnpoint_from_native_v005_does_not_require_reference_high(self):
        from ma_v067_policy import number
        visited=[]
        def original(f):
            visited.append(f);return True
        f=extracted('ma_v067_coin_runtime.py','verified_or_fail_closed_range',
            {'policy':SimpleNamespace(number=number),'previous_range':original})
        valid={'ma_v061_kind':'TURNPOINT','ma_v067_kind':'REVERSAL_UP',
               'source':'UPBIT_V079_LIVE_10_TO_2_TICK_AND_BOOK',
               'ma_v067_bar_start':'20261119090000','ma_v067_120_delta':'0.0005'}
        self.assertTrue(f(valid)) # v005 actual SQLite schema; no high/low
        self.assertFalse(f({**valid,'source':'STALE_REST'}))
        self.assertFalse(f({**valid,'ma_v067_120_delta':'not_number'}))
        self.assertFalse(f({'ma_v061_kind':'DOWN_BEAR','ma_v067_kind':'REVERSAL_BEAR',
                            'ma_v067_low':100,'ma_v067_high':105}))
        self.assertTrue(f({'ma_v061_kind':'DOWN_BEAR','ma_v067_kind':'REVERSAL_BEAR',
                           'source':'UPBIT_MA_V067_SUSTAINED_ALIGNMENT',
                           'ma_v067_low':100,'ma_v067_high':105}))
        self.assertFalse(f({'low':100}))
        self.assertTrue(f({'low':100,'high':110}))
        self.assertEqual(len(visited),1)

    def test_gmo_preopen_subscriptions_use_shared_owner_and_preserve_pins(self):
        calls=[]
        def owner(rt,at):
            calls.append(at)
            return ['AT_HELD','CW_HELD','MA_HELD','TREND_HELD','GMO_CANDIDATE']
        class Planner: runner=None
        planner=Planner()
        pre=extracted('gmo_d1_preplan_v097.py','subscriptions',
            {'original_subscriptions':owner,'d_live':SimpleNamespace(now_kst=lambda:None)})
        rt=SimpleNamespace(gmo=SimpleNamespace(_v097_planner=planner))
        before=dt.datetime(2026,11,19,7,59,20,tzinfo=dt.timezone(dt.timedelta(hours=9)))
        self.assertEqual(pre(rt,before),['AT_HELD','CW_HELD','MA_HELD','TREND_HELD','GMO_CANDIDATE'])
        self.assertEqual(calls[0].strftime('%H:%M:%S'),'08:00:00')
        self.assertIs(planner.runner,rt)
        now=before.replace(hour=9)
        pre(rt,now)
        self.assertEqual(calls[-1],now)

    def test_gmo_bad_quote_fails_closed_without_breaking_shared_ws(self):
        from gmo_d1_preplan_v097 import _verified
        stamp=dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        for bad in (None,(),['short'],['x','KOSPI','x',0,'bad',2,stamp.isoformat(),'KRX','x'],
                    ['x','KOSPI','x',0,float('nan'),2,stamp.isoformat(),'KRX','x']):
            with self.subTest(tick=repr(bad)):self.assertIsNone(_verified(bad,stamp))

    def test_close_asof_rejects_late_live_and_previous_day_record(self):
        from close_followup_integrity_v008 import asof_price
        kst=dt.timezone(dt.timedelta(hours=9))
        due=dt.datetime(2026,11,19,15,15,tzinfo=kst)
        later=due+dt.timedelta(minutes=20)
        self.assertIsNone(asof_price({'price':110,'ts':later.isoformat()},None,due,later))
        self.assertIsNone(asof_price({'price':110,'ts':due.isoformat()},None,due,later))
        self.assertIsNone(asof_price(None,{'price':99,'source':'RESEARCH_DAY',
                                         'bar_ts':'20261118'},due,later))
        self.assertIsNone(asof_price(None,{'price':99,'source':'RESEARCH_3M',
                                         'bar_ts':'20261119145500'},due,later))
        valid=asof_price(None,{'price':101,'source':'RESEARCH_3M',
                              'bar_ts':'20261119151200'},due,later)
        self.assertEqual(valid[0],101)
        self.assertIsNotNone(asof_price({'price':110,'ts':due.isoformat()},None,
                                         due,due+dt.timedelta(seconds=2)))

    def test_close_followup_ledger_stays_pending_until_true_observation(self):
        from close_followup_integrity_v008 import install
        from at_close_runner import AtCloseRunner
        from at_close_store import now_kst
        install()
        con=sqlite3.connect(':memory:');con.row_factory=sqlite3.Row
        con.execute('''CREATE TABLE research_followups(
            id INTEGER PRIMARY KEY, status TEXT, due_ts TEXT, code TEXT,
            horizon TEXT,source_signal_id INTEGER,source_candidate_id INTEGER,
            base_ts TEXT,base_price INTEGER,observed_ts TEXT,
            observed_price INTEGER,return_pct REAL,max_price REAL,min_price REAL,
            mfe_pct REAL,mae_pct REAL,bars_observed INTEGER,outcome_json TEXT)''')
        due=now_kst()-dt.timedelta(minutes=15)
        con.execute('INSERT INTO research_followups(status,due_ts,code,horizon,base_ts,base_price) VALUES(?,?,?,?,?,?)',
                    ('PENDING',due.isoformat(timespec='seconds'),'TEST','15M',due.isoformat(),100))
        con.commit()
        class Store:
            lock=threading.RLock()
            def connect(self):return con
            def followup_price(self,*a):return {'price':99,'source':'RESEARCH_DAY',
                                                'bar_ts':due.strftime('%Y%m%d')}
            def followup_extremes(self,*a):return {}
        rt=SimpleNamespace(store=Store(),latest={'TEST':{'price':999,'ts':now_kst().isoformat()}})
        AtCloseRunner._process_followups(rt)
        self.assertEqual(con.execute('SELECT status FROM research_followups').fetchone()[0],'PENDING')
        rt.store.followup_price=lambda *a:{'source':'RESEARCH_3M','price':102,
                     'bar_ts':(due-dt.timedelta(minutes=3)).strftime('%Y%m%d%H%M%S')}
        AtCloseRunner._process_followups(rt)
        row=con.execute('SELECT status,observed_price,outcome_json FROM research_followups').fetchone()
        self.assertEqual((row[0],row[1]),('DONE',102))
        self.assertEqual(json.loads(row[2])['observation_source'],'RESEARCH_3M_VERIFIED_ASOF')
        con.close()

    def test_duplicate_start_preserves_original_trend_postexit_recovery(self):
        new=[]
        class Coordinator:
            def __init__(self,rt):new.append(rt)
        rt=SimpleNamespace(started=True,root='/unused',market_trend=object(),
                           post_sale_tracker_v053=object(),_v092_recovery=object())
        trend=extracted('market_trend_runtime_v043.py','runtime_start',
              {'original_start':lambda self:'OK','Coordinator':Coordinator})
        self.assertEqual(trend(rt),'OK')
        post=extracted('post_exit_runtime_v053.py','tracking_start',
              {'original_start':lambda self:'OK','Tracker':lambda root:new.append(root)})
        self.assertEqual(post(rt),'OK')
        rec=extracted('unified_recovery_v092.py','start',
             {'original_start':lambda self:'OK','RecoveryObserver':lambda root:new.append(root)})
        self.assertEqual(rec(rt),'OK')
        self.assertEqual(new,[])

    def test_trend_registry_owner_lock_covers_buy_sell_and_research_snapshots(self):
        # v008 source contract: stock+coin Coordinator workers share these
        # dictionaries with GMO/AT subscription and wallet owner lookups.
        trend=(BASE/'market_trend_runtime_v043.py').read_text(encoding='utf8')
        new_legacy=(BASE/'market_trend_1118_runtime.py').read_text(encoding='utf8')
        expected=(
            'with self.lock:\n                        self._positions[(market,"trend",frame,code)]=p',
            'with self.lock:\n                        self._virtual[vkey]=v',
            'with self.lock:\n            held_snapshot=list(self._positions.items())',
            'with self.lock:\n            virtual_snapshot=list(self._virtual.items())',
            'with self.lock:\n                owned_snapshot=tuple(self._positions.values())',
        )
        for fragment in expected:
            with self.subTest(fragment=fragment[:48]):
                self.assertIn(fragment,trend)
        import re
        self.assertRegex(trend,r'with self\.lock:\n\s+if self\._positions\.get\(key\) is p:')
        self.assertRegex(trend,r'with self\.lock:\n\s+if self\._virtual\.get\(key\) is v:')
        self.assertIn('with self.lock:\n            self._positions[(market,"trend",frame,code)]=position',new_legacy)

    def test_all_five_strategies_still_have_original_entry_exit_owners(self):
        # Fixing one live feed must not delete or replace other wallet owners.
        py=BASE
        must_have={
            'gmo_d1_preplan_v097.py':['original_subscriptions(self,opening)','original_topup(engine,q)'],
            'market_trend_runtime_v043.py':['self.standalone_wallet.buy(','owner.sell(','TREND_FRAMES'],
            'ma_v067_stock_runtime.py':['service.on_tick(decision_tick,runner.engine.book)'],
            'ma_v067_coin_runtime.py':['return previous_sell(self,c,p,desired,price,at,reason,event_key=event_key)'],
            'android_runtime_v078.py':['from close_followup_integrity_v008 import install',
                                        'from gmo_d1_preplan_v097 import install'],
        }
        for name,fragments in must_have.items():
            source=(py/name).read_text('utf8')
            for fragment in fragments:
                with self.subTest(file=name,fragment=fragment[:50]):
                    self.assertIn(fragment,source)


if __name__=='__main__':unittest.main()
