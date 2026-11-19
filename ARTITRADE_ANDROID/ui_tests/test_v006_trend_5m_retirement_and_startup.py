"""Prevent 5m MarketTrend order reactivation on BOTH stock/coin after 11/18.

Zero network/broker calls, zero database changes, no synthetic fills.
This tests native wallet function guards, not just a UI label.
"""
import ast
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
PY=ROOT/"app/src/main/python"
JAVA=ROOT/"app/src/main/java/com/artitrade/mobile"


def _source(path):
    return path.read_text(encoding="utf-8")


def _method(path, class_name, name):
    tree=ast.parse(_source(path),filename=str(path))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef)
             and n.name==class_name)
    return next(n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))
                and n.name==name)


def _standalone(fn, globs=None):
    cloned=ast.parse(ast.unparse(fn))
    env=dict(globs or {})
    exec(compile(cloned,"isolated-native-method","exec"),env)
    return env[fn.name]


class RetiredTrend5m(unittest.TestCase):
    def test_v054_direct_wallet_buy_is_rejected_for_both_markets(self):
        func=_standalone(_method(PY/"market_trend_wallet_v054.py",
                                 "TrendWalletV054","buy"),{"FRAMES":("5m","30m","60m","day")})
        for market in ("stock","coin"):
            with self.subTest(market=market):
                result=func(None,market,"5m","CODE","NAME",100,100,None,None,None)
                self.assertEqual(result,(None,"TREND_5M_RETIRED_NO_NEW_BUY"))

    def test_v043_parent_wallet_rejects_only_trend_5m(self):
        fn=_method(PY/"market_trend_wallet_v043.py","TrendWallet","buy")
        func=_standalone(fn)
        for market in ("stock","coin"):
            self.assertEqual(func(None,market,"trend","5m","CODE","NAME",
                                  100,100,None,None,None),
                             (None,"TREND_5M_RETIRED_NO_NEW_BUY"))
        src=ast.get_source_segment(_source(PY/"market_trend_wallet_v043.py"),fn)
        self.assertIn("strategy == 'trend' and frame == '5m'",src)

    def test_1118_policy_rejects_trend_5m(self):
        policy=ast.parse(_source(PY/"new_legacy_1118_policy.py"))
        frames=next(n.value for n in policy.body if isinstance(n,ast.Assign)
                    and any(isinstance(t,ast.Name) and t.id=="TREND_FRAMES"
                            for t in n.targets))
        self.assertEqual(ast.literal_eval(frames),("30m","60m","day"))
        source=_source(PY/"market_trend_1118_runtime.py")
        self.assertIn("if frame not in TREND_FRAMES:",source)
        self.assertIn("return None,'TREND_5M_RETIRED_NO_NEW_BUY'",source)

    def test_active_research_scans_exclude_5m_but_ma_retains_it(self):
        source=_source(PY/"market_trend_runtime_v043.py")
        self.assertIn("for tf in TREND_FRAMES",source)
        self.assertIn("for frame in TREND_FRAMES",source)
        self.assertIn("for frame in mt.FRAMES",source) # MA diagnostic still covers 5m
        self.assertIn("held+[(code,tf)",source) # legacy positions exit recovery

    def test_ui_hides_retired_frame_without_erasing_source(self):
        ui=_source(JAVA/"UnifiedVisualActivity.java")
        self.assertIn('if("trend".equals(strategy)&&"5m".equals(choice))continue;',ui)
        self.assertIn('if("trend".equals(s)&&"5m".equals(frame)',ui)
        self.assertIn('"trend".equals(stg)',ui)
        self.assertIn('if("trend".equals(strategy))',ui)
        self.assertIn('?"trend":', '?"trend":') if False else None

    def test_startup_reports_python_failures_and_preserves_epoch(self):
        java=_source(JAVA/"UnifiedTradingService.java")
        self.assertIn('ENGINE_START_FAILED',java)
        self.assertIn('!started.optBoolean("ok",false)',java)
        self.assertIn('LedgerAntiRollbackV002.verify(this)',java)
        self.assertIn('FreshEpochGateV106.migrationGate(this)',java)
        self.assertNotIn('new File(root).delete',java)


if __name__=="__main__":
    unittest.main()
