"""Static source-only native original 8-ledger audit; no user trade data."""
import ast
from pathlib import Path

R=Path("/tmp/artitrade-v019-native")
NAMES=("d_v19_portfolio.py","d_upbit.py","stock_ma_engine.py",
       "coin_ma_engine.py","at_close_engine.py","at_close_store.py",
       "projectgmo/engine.py","d_gmo_runtime.py","projectgmo/scoring.py")
KEY=("sell","fill","exit","position","book","history","tick","close",
     "settle","plan","pnl","record","buy","paper")
for filename in NAMES:
    path=R/filename
    if not path.is_file():continue
    ls=path.read_text("utf8").splitlines()
    module=ast.parse("\n".join(ls),filename)
    print("===ORIGINAL",filename,"LINES",len(ls),"===")
    for top in module.body:
        if isinstance(top,ast.ClassDef):
            print("CLASS",top.name)
            for node in top.body:
                if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                    if any(k in node.name.lower() for k in KEY):
                        print(" METHOD",node.name,node.lineno,node.end_lineno)
        elif isinstance(top,(ast.FunctionDef,ast.AsyncFunctionDef)):
            if any(k in top.name.lower() for k in KEY):
                print(" FUNC",top.name,top.lineno,top.end_lineno)
    print("DATABASE DDL AND REASON CONTRACT")
    for i,l in enumerate(ls):
        if any(k in l.upper() for k in ("CREATE TABLE IF NOT EXISTS POSITIONS",
              "CREATE TABLE IF NOT EXISTS EXECUTIONS",
              "CREATE TABLE IF NOT EXISTS ACCOUNTS",
              "CREATE TABLE IF NOT EXISTS WALLETS",
              "FIRST_TARGET","FIRST_PROFIT","SECOND_TARGET",
              "INSERT INTO EXECUTIONS","UPDATE ACCOUNTS SET",
              "UPDATE WALLETS SET","EXECUTION_MODE","_EXECUTION_MODE")):
            print(i+1,l[:210])
