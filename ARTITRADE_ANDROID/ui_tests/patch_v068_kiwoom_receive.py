"""Patch only the exact pinned Kiwoom receive loop; preserve trading policy and other modules."""
from pathlib import Path
import ast, base64, hashlib, io, zipfile

ROOT = Path(__file__).resolve().parents[1]
PART_DIR = ROOT / "app/src/main/assets/strategy_v070"
BRIDGE = ROOT / "app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java"
GRADLE = ROOT / "app/build.gradle"
OLD_SHA = "10ff2cc9cda6ebba1dfa183f893bcea76f38445bb109dac66b99d578cc9c5447"
files = [PART_DIR / ("part_%02d.txt" % i) for i in range(22)]
raw = base64.b64decode("".join(f.read_text("ascii") for f in files))
assert hashlib.sha256(raw).hexdigest() == OLD_SHA, "Unexpected base APK engine: stop"
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    infos=z.infolist()
    original={item.filename:z.read(item.filename) for item in infos}
source=original["d_live.py"].decode("utf8")
old_source=source
tree=ast.parse(source)
runner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="LivePaperRunner")
methods={n.name:n for n in runner.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
lines=source.splitlines(keepends=True)

def patch_method(method_name, before, after):
    global lines
    # Reparse after each change so source location cannot drift.
    source="".join(lines)
    tree=ast.parse(source)
    runner=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="LivePaperRunner")
    node=next(n for n in runner.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==method_name)
    segment="".join(lines[node.lineno-1:node.end_lineno])
    assert segment.count(before)==1, (method_name,"expected exact statement",segment.count(before))
    segment=segment.replace(before,after,1)
    lines[node.lineno-1:node.end_lineno]=segment.splitlines(keepends=True)

patch_method("_switch_venue_subscriptions",
    "        want = self._desired_subscriptions()\n",
    "        want = await asyncio.to_thread(self._desired_subscriptions)\n")
patch_method("_repair_gmo_depth_on_existing_ws",
    "        priority=self.gmo.priority_watchlist(current)\n",
    "        priority=await asyncio.to_thread(self.gmo.priority_watchlist,current)\n")
patch_method("run",
    "                        self.engine.book.on_message(msg, self.nxt_snapshot, self.market_map)\n",
    "                        await asyncio.to_thread(self.engine.book.on_message, msg, self.nxt_snapshot, self.market_map)\n")
patch_method("run",
    "                                self.gmo.on_book_message(msg, self.market_map)\n",
    "                                await asyncio.to_thread(self.gmo.on_book_message, msg, self.market_map)\n")
patch_method("run",
    "                        observed_ticks = parse_venue_real(msg, self.market_map, nxt_snapshot=self.nxt_snapshot, gmo_priority_codes=self.gmo.priority_watchlist(now_kst()))\n",
    "                        observed_ticks = await asyncio.to_thread(\n"
    "                            lambda: parse_venue_real(msg, self.market_map, nxt_snapshot=self.nxt_snapshot, gmo_priority_codes=self.gmo.priority_watchlist(now_kst())))\n")
patch_method("run",
    "                            self._new_tick(tick)\n",
    "                            await asyncio.to_thread(self._new_tick, tick)\n")
source="".join(lines)
ast.parse(source)
compile(source,"d_live.py","exec")
assert source != old_source
assert source.count("await asyncio.to_thread(self._new_tick, tick)")==1
assert source.count("ping_timeout=45")==1
# Protect the 81 non-d_live bundled modules, credentials, and strategy policy.
updated=source.encode("utf8")
out=io.BytesIO()
with zipfile.ZipFile(out,"w") as z:
    for info in infos:
        z.writestr(info,updated if info.filename=="d_live.py" else original[info.filename])
new_raw=out.getvalue()
with zipfile.ZipFile(io.BytesIO(new_raw)) as z:
    assert z.read("d_live.py")==updated
    for name,data in original.items():
        if name!="d_live.py":
            assert z.read(name)==data, ("Unexpected modified module",name)
sha=hashlib.sha256(new_raw).hexdigest()
b64=base64.b64encode(new_raw).decode("ascii")
width=(len(b64)+21)//22
for i,f in enumerate(files):
    f.write_text(b64[i*width:(i+1)*width],encoding="ascii")
java=BRIDGE.read_text("utf8")
assert java.count('CORE_SHA256="'+OLD_SHA+'"')==1
BRIDGE.write_text(java.replace('CORE_SHA256="'+OLD_SHA+'"','CORE_SHA256="'+sha+'"'),encoding="utf8")
gradle=GRADLE.read_text("utf8")
assert gradle.count("versionCode 73")==1
assert gradle.count("versionName '0.7.67-MA-TREND-WS-REPAIR'")==1
GRADLE.write_text(gradle.replace("versionCode 73","versionCode 74").replace("versionName '0.7.67-MA-TREND-WS-REPAIR'","versionName '0.7.68-KIWOOM-WS-RESPONSIVENESS'"),encoding="utf8")
print("V068_NEW_CORE_SHA256",sha)
print("V068_RECEIVE_LOOP_OFFLOAD_COUNT",6)
print("V068_OTHER_CORE_MODULES_IDENTICAL",len(original)-1)
print("V068_STOCK_COIN_AT_MA_GMO_TRADING_POLICY_UNCHANGED")
