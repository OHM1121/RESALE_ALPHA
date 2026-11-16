"""Read-only investigation of the SHA-pinned packaged strategy core.

No broker API calls, no order invocation, and no changes to trading databases.
Only prints selected source identifiers, branch conditions and nearby lines.
"""
from __future__ import annotations
import ast
import base64
import io
import re
import zipfile
from pathlib import Path

assets=Path("ARTITRADE_ANDROID/app/src/main/assets/strategy_v070")
parts=sorted(assets.glob("part_*.txt"))
assert len(parts)==22, f"Unexpected strategy archive chunks: {len(parts)}"
compressed=base64.b64decode("".join(x.read_text(encoding="ascii") for x in parts))
with zipfile.ZipFile(io.BytesIO(compressed)) as archive:
    entries=[x for x in archive.namelist() if x.endswith(".py") and not x.startswith("__MACOSX/")]
    print("=== MODULES",len(entries),"===")
    for x in entries:
        if any(k in x.lower() for k in ("gmo","live","kiwoom","stock","upbit","websocket","engine","close")):
            print("MODULE",x)
    patterns=[
        "PRELOAD_BUDGET_EXPIRED", "keepalive", "ping_interval", "ping_timeout",
        "websockets.connect", "connect(", "_confirmed_ws_session",
        "REG", "0B", "0D", "preload_gate", "_watch_feed_last_epoch",
        "first_target", "second_target", "target1", "target2",
        "target_1", "target_2", "gmo", "take_profit", "trailing",
        "entry_price", "buy_price", "stop_loss", "sell_price",
    ]
    rx=re.compile("|".join(re.escape(k) for k in patterns),re.I)
    for name in entries:
        low=name.lower()
        if not any(x in low for x in ("gmo","d_live","d_engine","kiwoom","stock","at_close","websocket")):
            continue
        try:content=archive.read(name).decode("utf-8")
        except UnicodeError:continue
        lines=content.splitlines()
        print(f"\n=== FILE {name} ({len(lines)} lines) ===")
        try:
            tree=ast.parse(content)
            functions=[(getattr(n,"lineno",0),n.name) for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and
                (any(w in n.name.lower() for w in ("preload","websocket","connect","reconnect","quote","gmo","exit","sell","stop","target","tick","listen","stage","trade","order","buy","goal")))]
            for n,fn in sorted(functions)[:110]:print("FUNC",n,fn)
        except SyntaxError as exc:print("AST_ERROR",type(exc).__name__,str(exc)[:80])
        indices=[i for i,line in enumerate(lines) if rx.search(line) and not line.lstrip().startswith("#")]
        keep=set()
        for i in indices[:130]:
            for k in range(max(0,i-2),min(len(lines),i+3)):keep.add(k)
        for k in sorted(keep)[:380]:
            print(f"{k+1:04d} {lines[k][:220]}")

    slices={
        "projectgmo/engine.py":[(1,100),(260,370),(420,645)],
        "d_live.py":[(620,710),(1650,1795),(1840,2110),(2140,2300)],
        "d_gmo_runtime.py":[(1,105),(390,550),(740,930)],
    }
    for name,regions in slices.items():
        if name not in entries:continue
        text=archive.read(name).decode("utf-8",errors="replace").splitlines()
        print("SLICE_FILE",name,len(text))
        for start,end in regions:
            print("SLICE_RANGE",name,start,end)
            for i in range(start-1,min(end,len(text))):
                print(f"SLICE {name}:{i+1:04d} {text[i][:240]}")
