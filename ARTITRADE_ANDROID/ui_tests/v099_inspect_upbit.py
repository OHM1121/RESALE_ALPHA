"""Read only the archived Upbit public WS and depth cap for v099."""
import base64,io,zipfile,ast
from pathlib import Path
p=Path(__file__).resolve().parents[1]/"app/src/main/assets/strategy_v070"
blob=base64.b64decode("".join((p/f"part_{i:02d}.txt").read_text(encoding="ascii") for i in range(22)))
with zipfile.ZipFile(io.BytesIO(blob)) as z:
 for name in ("d_upbit.py","market_trend_runtime_v043.py"):
  if name not in z.namelist():continue
  s=z.read(name).decode()
  ls=s.splitlines()
  print("=== FILE",name,"LINES",len(ls))
  tree=ast.parse(s)
  for cls in tree.body:
   if not isinstance(cls,ast.ClassDef):continue
   print("CLASS",cls.name)
   for f in cls.body:
    if not isinstance(f,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
    if any(q in f.name for q in ("watched","depth","book","subscribe","track","_ws","market","ticker","run")):
     print("METHOD",f.name,f.lineno,f.end_lineno)
     for i in range(f.lineno-1,min(f.end_lineno,f.lineno+100)):
      print(f"{i+1}: {ls[i]}")
  print("=== LINES TOP 40/CAPS ===")
  for i,line in enumerate(ls):
   if any(t in line for t in ("[:40]","[:50]","TOP40","TOP50","40)","40]","_both_watched_codes","orderbook", "tracked")):
    print(f"{i+1}: {line}")
