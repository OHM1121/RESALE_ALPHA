"""0.0.5 observed-error regression tests; offline, no broker/device/data writes."""
from pathlib import Path
import ast
import datetime as dt
import json
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
PY=ROOT/"app/src/main/python"
JAVA=ROOT/"app/src/main/java/com/artitrade/mobile"


class ErrorHistoryRootContracts(unittest.TestCase):
    def test_hub_for_ui_contract_propagates_at_every_decorator(self):
        names={
            "market_trend_runtime_v043.py":"runtime_hub",
            "post_exit_runtime_v053.py":"tracking_hub",
            "phone_policy_reference_v059.py":"chronologized",
            "ma_v061_packet.py":"scoped",
            "ma_v062_packet.py":"records",
            "stock_quote_truth_v066.py":"hub_with_verified_prices",
        }
        for file,fn in names.items():
            source=(PY/file).read_text(encoding="utf8")
            with self.subTest(file=file):
                methods=[n for n in ast.walk(ast.parse(source))
                         if isinstance(n,ast.FunctionDef) and n.name==fn]
                self.assertEqual(len(methods),1)
                arg_names=[n.arg for n in methods[0].args.args]
                self.assertIn("for_ui",arg_names)
                calls=[n for n in ast.walk(methods[0]) if isinstance(n,ast.Call)]
                self.assertTrue(any(any(k.arg=="for_ui" and isinstance(k.value,ast.Name)
                            and k.value.id=="for_ui" for k in call.keywords)
                            for call in calls),file)

    def test_outer_stock_quote_decorator_receives_true_and_false(self):
        from stock_quote_truth_v066 import install
        seen=[]
        class Runtime:
            def hub_records(self,for_ui=False):
                seen.append(for_ui)
                return json.dumps({"stock":{"at":{"ledger":{"positions":[]}}},"mode":for_ui})
        core=SimpleNamespace(Runtime=Runtime)
        install(core)
        runtime=Runtime()
        self.assertTrue(json.loads(runtime.hub_records(for_ui=True))["mode"])
        self.assertFalse(json.loads(runtime.hub_records(for_ui=False))["mode"])
        self.assertEqual(seen,[True,False])

    def test_android_kst_backup_without_system_zoneinfo_files(self):
        import android_daily_paper_backup_v003 as backup
        self.assertEqual(backup.KST.utcoffset(None),dt.timedelta(hours=9))
        stamp=dt.datetime(2026,11,19,11,32,tzinfo=dt.timezone(dt.timedelta(hours=9)))
        self.assertEqual(backup.due_date("coin",now=stamp),"2026-11-19")
        early=stamp.replace(hour=11,minute=29)
        self.assertEqual(backup.due_date("coin",now=early),"2026-11-18")
        runtime=(PY/"android_runtime_v078.py").read_text(encoding="utf8")
        self.assertNotIn('ZoneInfo("Asia/Seoul")',runtime)

    def test_coin_ma_legacy_feature_missing_high_cannot_reach_native_match(self):
        module=ast.parse((PY/"ma_v067_coin_runtime.py").read_text(encoding="utf8"))
        install=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=="install")
        candidate=next(n for n in ast.walk(install) if isinstance(n,ast.FunctionDef)
                       and n.name=="verified_or_fail_closed_range")
        calls=[]
        def native(feature):
            calls.append(dict(feature))
            return True
        from ma_v067_policy import number
        stmt=ast.parse(ast.unparse(candidate))
        env={"previous_range":native,"policy":SimpleNamespace(number=number)}
        exec(compile(stmt,"isolated-ma-range-guard","exec"),env)
        f=env["verified_or_fail_closed_range"]
        self.assertFalse(f({"ma_v061_kind":"DOWN_BEAR","low":100}))
        self.assertFalse(f({"ma_v061_kind":"DOWN_BEAR","high":100}))
        self.assertFalse(f({"high":99,"low":100}))
        self.assertFalse(f({"high":"NOT_REAL","low":100}))
        self.assertEqual(calls,[])
        self.assertTrue(f({"high":110,"low":100}))
        self.assertEqual(len(calls),1)

    def test_stock_ma_queue_coalesces_latest_per_symbol(self):
        from ma_v067_stock_runtime import _LatestVerifiedMAQueue
        q=_LatestVerifiedMAQueue(maxsize=2)
        base=("AAA","KOSPI","Name","",100,0,"2026-11-19T09:59:50+09:00","KRX",0)
        for i in range(1000):
            t=list(base);t[4]=100+i;t[6]="2026-11-19T09:59:59+09:00"
            self.assertTrue(q.offer(tuple(t)))
        self.assertEqual(q.qsize(),1)
        older=list(base);older[6]="2026-11-19T09:59:40+09:00"
        self.assertTrue(q.offer(tuple(older)))
        latest=q.get(timeout=0.1)
        self.assertEqual(latest[4],1099)
        q.task_done()
        self.assertTrue(q.offer(base))
        self.assertTrue(q.offer(("BBB",)+base[1:]))
        self.assertFalse(q.offer(("CCC",)+base[1:]))
        self.assertEqual(q.qsize(),2)

    def test_gmo_empty_state_not_mislabelled_corrupt(self):
        source=(JAVA/"UnifiedVisualActivity.java").read_text(encoding="utf8")
        self.assertIn('boolean sourceFailed=selected.sourceError',source)
        self.assertIn('sourceFailed||!selected.sourceReady',source)
        self.assertIn('if(sourceFailed||!selected.sourceReady)',source)
        self.assertNotIn('TextView warn=text("◉  수급매매 원장 확인 필요',source)


if __name__=="__main__":
    unittest.main()
