"""Regression for v085 paper-only exits, original evidence and D+1 scanning."""
import ast,base64,hashlib,io,json,pathlib,sys,types,unittest,zipfile

BASE=pathlib.Path(__file__).resolve().parents[1]/'app/src/main'
PY=BASE/'python'
sys.path.insert(0,str(PY))

def source(name):
    return (PY/name).read_text(encoding='utf-8')

def bundled(name):
    a=base64.b64decode(''.join((BASE/'assets/strategy_v070'/f'part_{i:02d}.txt').read_text() for i in range(22)))
    with zipfile.ZipFile(io.BytesIO(a)) as z:return z.read(name).decode()

class V085Safety(unittest.TestCase):
    def test_original_source_pinned_and_date_logic(self):
        parts=[(BASE/'assets/strategy_v070'/f'part_{i:02d}.txt').read_text() for i in range(22)]
        blob=base64.b64decode(''.join(parts))
        sha=hashlib.sha256(blob).hexdigest()
        self.assertIn('CORE_SHA256="'+sha+'"',(BASE/'java/com/artitrade/mobile/StrategyRuntimeBridge.java').read_text())
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            self.assertEqual(len(z.namelist()),82)
            self.assertIsNone(z.testzip())
            d=z.read('d_gmo_runtime.py').decode()
        self.assertIn("hm >= '1530'",d)
        self.assertIn("if _date(x['entry_date']) < today",d)
        self.assertNotIn("target = now.date() if now.strftime('%H%M') >= '2020'",d)
        ast.parse(d)

    def test_real_ma_epoch_paths_used_by_after_sale(self):
        for name in ('post_exit_high_v053.py','post_exit_sources_v053.py'):
            data=source(name)
            self.assertIn('stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3',data)
            self.assertIn('coin/ma_epoch_v062/upbit_paper.sqlite3',data)
            self.assertNotIn('stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3',data)
            self.assertNotIn('"coin/ma/upbit_paper.sqlite3"',data)

    def test_preserved_ma_research_not_lost_after_hub_rebuild(self):
        tree=ast.parse(source('android_runtime_v078.py'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and
                n.name=='_v085_preserve_research')
        ns={}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<test>','exec'),ns)
        result=ns[fn.name]({'items':[{'id':'native','code':'000001','at':'2026-11-17'}]},
            {'items':[{'id':'v074','strategy':'ma','code':'000001','at':'2026-11-17'},
                      {'id':'native','code':'000001','at':'2026-11-17'}]})
        self.assertEqual(len(result['items']),2)
        self.assertEqual(result['upstream_decisions_preserved_v085'],1)
        self.assertIn('original_ma=prior_stock.get',source('android_runtime_v078.py'))

    def test_manual_sell_api_never_calls_broker_adapters(self):
        import android_manual_paper_sell_v085 as manual
        for raw in ('','{}', '{"market":"stock","strategy":"at","code":"005930","percent":0}'):
            self.assertFalse(json.loads(manual.perform(raw))['ok'])
        stub=types.ModuleType('android_live_bridge')
        class BrokerArmed:
            started=True
            def live_enabled(self,m):return m=='stock'
        stub._RUNTIME=BrokerArmed()
        old=sys.modules.get('android_live_bridge')
        sys.modules['android_live_bridge']=stub
        try:
            import uuid
            req=dict(market='stock',strategy='at',code='005930',
                     request_id=str(uuid.uuid4()),expected_qty='1',
                     position_id=1,percent=100)
            self.assertEqual(json.loads(manual.perform(json.dumps(req)))['error'],
                             'BROKER_MODE_ARMED_DISALLOWED')
        finally:
            if old is None:sys.modules.pop('android_live_bridge',None)
            else:sys.modules['android_live_bridge']=old

    def test_ui_has_no_broker_arm_and_no_pending_footnotes(self):
        java=(BASE/'java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text()
        section=java[java.index('private void renderMaEntryObservations()'):
                     java.index('private LinearLayout marketEvidenceCard')]
        self.assertNotIn('MA 사전관측: 마감',section)
        self.assertNotIn('RSI20 관측대기는 미주문',section)
        self.assertNotIn('미진입 사유(누적)',section)
        operations=java[java.index('private void renderOperation()'):
                        java.index('private void renderMore()')]
        self.assertNotIn('button("실거래 ON"',operations)
        self.assertIn('MANUAL_PAPER_SELL', (BASE/'java/com/artitrade/mobile/UnifiedTradingService.java').read_text())
        self.assertIn('if(enabled)return error("SIMULATED_ACCOUNT_ONLY_NO_BROKER_ORDERS"',
            (BASE/'java/com/artitrade/mobile/StrategyRuntimeBridge.java').read_text())

    def test_preserved_policy(self):
        trend=source('market_trend_runtime_v043.py')
        self.assertIn('COIN_VERIFIED_TRADE_MAX_AGE_SEC=10.0',trend)
        self.assertIn('abs(time.time()-float(book.get("timestamp",0))/1000)>3',trend)
        self.assertIn('if rsi<=30:',trend)
        self.assertIn('if submit and entry not in self._ordered:',trend)
        self.assertIn('manual_fraction=None',source('market_trend_wallet_v054.py'))
        self.assertIn('USER_SIM_MANUAL',source('android_manual_paper_sell_v085.py'))

if __name__=='__main__':unittest.main()
