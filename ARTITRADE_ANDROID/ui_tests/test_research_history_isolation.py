#!/usr/bin/env python3
"""Regression: one missing/corrupt PC research table must not hide other strategies."""
import ast
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1] / "app" / "src" / "main" / "python"
SRC = ROOT / "android_runtime_v078.py"
nodes = ast.parse(SRC.read_text(encoding="utf8"))
fn = next(x for x in nodes.body if isinstance(x, ast.FunctionDef) and x.name == "_research_history")
audit_fn = next(x for x in nodes.body if isinstance(x, ast.FunctionDef) and x.name == "_research_source_audit")

class RuntimeStub:
    def __init__(self):
        self.root = "unused"
        self.errors = []
    def _err(self, key, ex):
        self.errors.append((key, str(ex)))

def failed_at(_root):
    raise ValueError("PC AT candidate snapshots table missing")

def good(name):
    return lambda _root, *args: {"items":[{"code":name, "tf":"5m", "score":60, "bought":False, "outcome":"HIT"}]}

def run_isolated(monkey):
    ns = {
        "Path": Path,
        "sqlite3": sqlite3,
        "_stock_at_research": failed_at,
        "_stock_ma_research": good("MA"),
        "_close_research": good("CLOSE"),
        "_coin_research": lambda root, which: good(which)(root),
    }
    exec(compile(ast.Module(body=[audit_fn,fn],type_ignores=[]),str(SRC),"exec"),ns)
    stub=RuntimeStub()
    out=ns["_research_history"](stub,{})
    return stub,out

class ResearchLoadFaultIsolation(unittest.TestCase):
    def test_one_bad_stock_table_does_not_hide_ma_close_or_coin(self):
        stub, out=run_isolated(None)
        self.assertEqual(out["stock"]["at"]["research"]["ma"]["items"][0]["code"],"MA")
        self.assertEqual(out["stock"]["cw"]["research"]["items"][0]["code"],"CLOSE")
        self.assertEqual(out["coin"]["general"]["research"]["items"][0]["code"],"base")
        self.assertEqual(out["coin"]["ma"]["research"]["items"][0]["code"],"ma")
        self.assertTrue(stub.errors)
        self.assertEqual(out["stock"]["at"]["research"].get("load_error"),"AT")
    def test_failure_is_recorded_without_rewriting_any_db(self):
        stub,out=run_isolated(None)
        self.assertEqual(len(stub.errors),1)
        self.assertEqual(out["stock"]["at"]["research"]["load_error"],"AT")

if __name__=="__main__":
    unittest.main()
