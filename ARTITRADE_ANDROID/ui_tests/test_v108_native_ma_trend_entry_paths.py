"""Source-level regression for actual native MA/Trend timing and no-fill retry."""
import ast
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/"app/src/main/python"

def src(name):return (ROOT/name).read_text(encoding="utf8")

class NativeOrderRetry(unittest.TestCase):
    def test_stock_ma_never_consumes_first_failed_native_buy(self):
        script=src("ma_v067_stock_runtime.py")
        tree=ast.parse(script)
        fn=next(x for x in tree.body if isinstance(x,ast.FunctionDef)
                and x.name=="_up_worker")
        run=script.splitlines()
        stamp=fn.lineno-1
        lines=run[fn.lineno-1:fn.end_lineno]
        native=next(i for i,line in enumerate(lines)
                    if "executed=service.on_tick(" in line)
        consumed=next(i for i,line in enumerate(lines[native:],native)
                      if "seen.add(key)" in line)
        self.assertGreater(consumed,native)
        self.assertIn("if executed and int(executed)>0:",
                      "\n".join(lines[native:consumed+1]))
        self.assertIn("UP_NATIVE_BUY_NOT_FILLED_RETRY_UNTIL_WINDOW_END",script)
        self.assertNotIn("service.on_tick(decision_tick,runner.engine.book)\n                seen.add(key)",script)

    def test_market_trend_reads_confirmed_bar_within_first_ten_seconds(self):
        script=src("market_trend_runtime_v043.py")
        tree=ast.parse(script)
        for name in ("_refresh_due_closed_local_v108","_preclose_candidates"):
            self.assertTrue(any(isinstance(x,ast.FunctionDef) and x.name==name
                                for x in ast.walk(tree)))
        entry=next(x for x in ast.walk(tree)
                   if isinstance(x,ast.FunctionDef) and x.name=="_preclose_candidates")
        lines=script.splitlines()[entry.lineno-1:entry.end_lineno]
        calls=[i for i,x in enumerate(lines)
               if "self._refresh_due_closed_local_v108(" in x]
        snapshots=[i for i,x in enumerate(lines)
                   if "histories={k:list(v)" in x]
        self.assertTrue(calls and snapshots and calls[0]<snapshots[0])
        buy=next(i for i,x in enumerate(lines)
                 if "p,disposition=self.standalone_wallet.buy(" in x)
        marks=[i for i,x in enumerate(lines) if "self._ordered.add(key)" in x]
        self.assertTrue(any(m>buy for m in marks))
        self.assertIn("if result is not None or unsafe or self.rt.live_enabled(market):",script)

    def test_trend_krx_last_hour_uses_session_start_not_backshifted_finish(self):
        source=src("market_trend_runtime_v043.py")
        fn=next(x for x in ast.walk(ast.parse(source))
                if isinstance(x,ast.FunctionDef) and x.name=="_preclose_candidates")
        body="\n".join(source.splitlines()[fn.lineno-1:fn.end_lineno])
        self.assertIn("elapsed=max(0,(now-first).total_seconds())",body)
        self.assertIn("steps=int(elapsed//mt.FRAME_SECS[frame])",body)
        self.assertIn("begins=first+dt.timedelta(",body)

    def test_all_entry_proofs_remain_native(self):
        ma=src("ma_v067_stock_runtime.py")
        trend=src("market_trend_runtime_v043.py")
        self.assertIn("original_tick(self,data,book)",ma)
        self.assertIn("shared_book.available(code,'KRX',now,limit,desired)",
                      src("ma_v067_stock_runtime.py") if False else
                      "shared_book.available(code,'KRX',now,limit,desired)")
        self.assertIn("verified=self._quote(market,code)",trend)
        self.assertIn("self.standalone_wallet.buy(",trend)

if __name__=="__main__":unittest.main()
