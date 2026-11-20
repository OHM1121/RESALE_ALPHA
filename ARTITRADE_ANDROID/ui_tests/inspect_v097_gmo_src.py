"""Read-only v097 inspection of the current SHA-pinned GMO core.
Outputs source excerpts from repository assets; never reads live credentials or ledgers.
"""
import ast,base64,io,zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parents[1]/"app/src/main"
blob=base64.b64decode("".join((HERE/"assets/strategy_v070"/f"part_{i:02d}.txt").read_text() for i in range(22)))
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    for name in z.namelist():
        if not (name.startswith("projectgmo/") or name=="d_gmo_runtime.py"):
            continue
        if not name.endswith(".py"):continue
        s=z.read(name).decode("utf-8")
        tree=ast.parse(s)
        lines=s.splitlines()
        print(f"=== FILE {name} lines={len(lines)} ===")
        print("MODULE_VARS")
        print("\n".join(f"{i+1}: {lines[i]}" for i in range(min(115,len(lines)))))
        for obj in tree.body:
            if isinstance(obj,ast.ClassDef):
                print("CLASS",obj.name,":",[(m.name,m.lineno,m.end_lineno) for m in obj.body if isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef))])
                for m in obj.body:
                    if not isinstance(m,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
                    wanted=(name=="d_gmo_runtime.py" and obj.name=="GMOService" and 
                            (m.name in ("__init__","start","stop","_loop","_maintenance_loop",
                                "_scan_one","_fetch_missing_opens","on_live_tick",
                                "_persist","_prepare_d1","_rest","snapshot","holding_codes") or
                            "open" in m.name or "d1" in m.name or "scan" in m.name or "pending" in m.name)) or (
                            name=="projectgmo/engine.py" and (
                            "open" in m.name or "paper" in m.name or "order" in m.name or "signal" in m.name or 
                            m.name in ("__init__","on_quote","fill","_exit","_buy","snapshot")))
                    if wanted:
                        count=m.end_lineno-m.lineno+1
                        print(f"--- {name}:{obj.name}.{m.name} {m.lineno}-{m.end_lineno} ---")
                        chunk=lines[m.lineno-1:m.end_lineno]
                        limit=260
                        for i,line in enumerate(chunk[:limit]):
                            print(f"{m.lineno+i}: {line}")
                        if count>limit:print(f"---TRUNCATED {count-limit} LINES---")
            elif isinstance(obj,ast.FunctionDef) and name=="projectgmo/engine.py":
                print("FUNCTION",obj.name,obj.lineno,obj.end_lineno)
                if "tick" in obj.name or "ceiling" in obj.name:
                    for i in range(obj.lineno-1,obj.end_lineno):print(f"{i+1}: {lines[i]}")
