"""v056: isolated AI analysis per native strategy, trend only in trend tab."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
UI=(ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text('utf-8')
sys.path.insert(0,str(ROOT/'app/src/main/python'))
import ai_strategy_analysis_v040 as ai

class AiScopeIsolationV056(unittest.TestCase):
    def test_ai_nav_has_no_parent_trend_override(self):
        view=UI.split('private void renderAiAnalysis(){',1)[1].split('private void renderResearch(){',1)[0]
        self.assertNotIn('renderCohortToggle(true)',view)
        self.assertNotIn('aiTrendOnly',UI)
        self.assertIn('return market+"/"+strategy;',UI)
        self.assertIn('final String chosenStrategy=strategy;',UI)
        self.assertIn('if("trend".equals(strategy))addTrendResearchCard(report);',view)
        self.assertIn('{"trend","마켓트렌드"}',UI)

    def test_trend_research_and_ai_remain_available_in_separate_strategy(self):
        self.assertIn('renderResearchCohortToggle()',UI)
        self.assertIn('private void renderTrendResearch(){',UI)
        self.assertIn('if strategy=="trend":',
            (ROOT/'app/src/main/python/ai_strategy_analysis_v040.py').read_text('utf-8'))

    def test_native_analysis_discards_mt43_mt54_candidates_and_history(self):
        for market,strategy in (('stock','at'),('stock','ma'),
                                ('coin','general'),('coin','ma')):
            with self.subTest(market=market,strategy=strategy):
                sym='005930' if market=='stock' else 'KRW-BTC'
                original={'id':'ORIGINAL-10','tf':'5m','code':sym,
                          'entry_mode':'ORIGINAL','status':'CLOSED','score':30}
                legacy={'id':'MT43:SIG:11','tf':'5m','code':sym,
                        'entry_mode':'MARKET_TREND','status':'CLOSED','score':30}
                independent={'id':'MT54:SIG:12','tf':'5m','code':sym,
                             'status':'CLOSED','score':30}
                all_rows=[original,legacy,independent]
                fills=[{'position_id':r['id'],'tf':'5m','code':sym} for r in all_rows]
                raw={'candidates':all_rows,'positions':all_rows,'executions':fills}
                research={'items':all_rows}
                ledger={'positions':all_rows,'executions':fills}
                archive={'features':[{'signal_key':r['id']+'|5m|'+sym}
                                     for r in all_rows],
                         'positions':[{'position_key':r['id']+'|5m|'+sym,
                                       'status':'CLOSED'} for r in all_rows]}
                with tempfile.TemporaryDirectory() as root:
                    a,b,c,d=ai._exclude_trend_cohort(root,market,strategy,
                                                      raw,research,ledger,archive)
                self.assertEqual([r['id'] for r in a['candidates']],['ORIGINAL-10'])
                self.assertEqual([r['id'] for r in a['positions']],['ORIGINAL-10'])
                self.assertEqual([r['id'] for r in b['items']],['ORIGINAL-10'])
                self.assertEqual([r['id'] for r in c['positions']],['ORIGINAL-10'])
                self.assertEqual(len(a['executions']),1)
                self.assertEqual(len(c['executions']),1)
                self.assertEqual(len(d['features']),1)
                self.assertEqual(len(d['positions']),1)

    def test_native_ai_filters_before_persisting_observation(self):
        native={'signal_id':'orig','tf':'5m','code':'KRW-BTC','score':80}
        trend={'signal_id':'MT43:SIG:x','tf':'5m','code':'KRW-BTC',
               'entry_mode':'MARKET_TREND','score':70}
        raw={'source':'test','candidates':[native,trend],
             'positions':[],'executions':[]}
        captured=[]
        class ProbeDone(Exception):pass
        def probe(root,market,strategy,research,ledger,now):
            captured.append(research)
            raise ProbeDone()
        with tempfile.TemporaryDirectory() as root, \
                patch.object(ai.old,'_read_source',return_value=raw), \
                patch.object(ai.journal,'capture_scope',side_effect=probe):
            with self.assertRaises(ProbeDone):
                ai.analyze(root,'coin','general')
        self.assertEqual([r['signal_id'] for r in captured[0]['items']],['orig'])

if __name__=='__main__':
    unittest.main()
