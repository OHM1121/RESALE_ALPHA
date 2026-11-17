"""Inspect existing original quote/feed and GMO NXT support without live requests."""
import ast,base64,io,zipfile
from pathlib import Path
p=Path(__file__).resolve().parents[1]/"app/src/main/assets/strategy_v070"
blob=base64.b64decode("".join((p/f"part_{i:02d}.txt").read_text() for i in range(22)))
with zipfile.ZipFile(io.BytesIO(blob)) as z:
  for name in ["projectgmo/prices.py","d_gmo_runtime.py","d_live.py"]:
    s=z.read(name).decode();lines=s.splitlines();tree=ast.parse(s)
    print("===FILE",name,len(lines))
    if name=="d_gmo_runtime.py":
      for a,b in [(1,80),(390,516),(518,622)]:
        print("===RANGE",a,b);print("\n".join(f"{i+1}: {lines[i]}" for i in range(a-1,min(len(lines),b))))
    if name=="projectgmo/prices.py":
      print("\n".join(f"{i+1}: {line}" for i,line in enumerate(lines)))
    if name=="d_live.py":
      for obj in tree.body:
        if isinstance(obj,ast.ClassDef):
          print("===CLASS",obj.name,[(x.name,x.lineno,x.end_lineno) for x in obj.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))])
          if obj.name not in ("LivePaperRunner","KiwoomFeed","RealtimeKiwoom"):continue
          for fn in obj.body:
            if not isinstance(fn,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
            if any(term in fn.name for term in ("tick","book","gmo","nxt","priority","_ws","subscription","start")):
              print("===METHOD",obj.name,fn.name,fn.lineno,fn.end_lineno)
              print("\n".join(f"{i+1}: {lines[i]}" for i in range(fn.lineno-1,min(len(lines),min(fn.end_lineno,fn.lineno+145)))))
      print("===NXT_GMO_LINES")
      for i,l in enumerate(lines):
        if ("gmo" in l.lower() and ("nxt" in l.lower() or "tick" in l.lower() or "book" in l.lower())) or ("NXT" in l and ("0D" in l or "0B" in l)):
          print("\n".join(f"{k+1}: {lines[k]}" for k in range(max(0,i-2),min(len(lines),i+4))))
