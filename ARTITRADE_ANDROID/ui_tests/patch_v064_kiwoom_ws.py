"""Patch the pinned original Kiwoom WebSocket runner only, no broker calls.
Do not alter signing, credential stores, original accounts, or other engines.
"""
from pathlib import Path
import base64,hashlib,io,re,zipfile
ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/'app/src/main/assets/strategy_v070'
JAVA=ROOT/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
ORIGINAL='e7518cb90a73cf62522aaa8962d872890e9e517ae988f52c22b3d94ed88531e0'
TARGET='d_live.py'
parts=[ASSETS/f'part_{i:02d}.txt' for i in range(22)]
archive=base64.b64decode(''.join(p.read_text(encoding='ascii') for p in parts))
sha=hashlib.sha256(archive).hexdigest()
assert sha==ORIGINAL,('UNEXPECTED_KIWOOM_SOURCE_BASE',sha)
java=JAVA.read_text(encoding='utf-8')
assert java.count(ORIGINAL)==1
with zipfile.ZipFile(io.BytesIO(archive)) as source:
 entries={n:source.read(n) for n in source.namelist()}
 info=source.infolist()
text=entries[TARGET].decode('utf-8')
old='async with websockets.connect(WS_HOST, open_timeout=18, ping_interval=20, ping_timeout=20, max_size=6000000) as ws:'
new='async with websockets.connect(WS_HOST, open_timeout=18, ping_interval=20, ping_timeout=45, max_size=6000000) as ws:'
assert text.count(old)==1
text=text.replace(old,new)
start=text.index('    async def _refresh_universe_if_due(self, ws):')
end=text.index('    def _record_group3_ack(',start)
method=text[start:end]
block_start=method.index('            with self.store.lock, self.store.connect() as db:')
block_end=method.index('            if krx_ranked:',block_start)
block=method[block_start:block_end]
assert 'INSERT INTO stocks' in block and 'for code, (market, name) in combined.items()' in block
# Move the existing SQL operation unchanged to a worker: never hold the
# websocket event loop while SQLite writes block on a foreign transaction.
wrapped='            def _persist_universe_db():\n'+''.join('    '+line for line in block.splitlines(keepends=True))
wrapped+='            await asyncio.to_thread(_persist_universe_db)\n'
method=method[:block_start]+wrapped+method[block_end:]
old_rank='                self.store.record_rank_snapshot(krx_ranked, self.krx_market_map, at=now.strftime(\'%%Y%%m%%d%%H%%M%%S\'))'.replace('%%','%')
new_rank='                await asyncio.to_thread(self.store.record_rank_snapshot, krx_ranked, self.krx_market_map, at=now.strftime(\'%%Y%%m%%d%%H%%M%%S\'))'.replace('%%','%')
assert method.count(old_rank)==1,('MISSING_KRX_RANK_SNAPSHOT',old_rank)
method=method.replace(old_rank,new_rank)
text=text[:start]+method+text[end:]
compile(text,TARGET,'exec')
updated=text.encode('utf-8')
assert b'ping_timeout=45' in updated
assert b'await asyncio.to_thread(_persist_universe_db)' in updated
output=io.BytesIO()
with zipfile.ZipFile(output,'w') as target:
 for item in info:
  target.writestr(item,updated if item.filename==TARGET else entries[item.filename])
result=output.getvalue()
with zipfile.ZipFile(io.BytesIO(result)) as check:
 assert all(check.read(x)==data for x,data in entries.items() if x!=TARGET)
new_sha=hashlib.sha256(result).hexdigest()
b64=base64.b64encode(result).decode('ascii')
width=(len(b64)+21)//22
for i,p in enumerate(parts):p.write_text(b64[i*width:(i+1)*width],encoding='ascii')
JAVA.write_text(java.replace(ORIGINAL,new_sha),encoding='utf-8')
assert hashlib.sha256(base64.b64decode(''.join(p.read_text(encoding='ascii') for p in parts))).hexdigest()==new_sha
print('V064_KIWOOM_SOURCE_SHA',new_sha,'SINGLE_MODULE_ONLY',TARGET,'OTHER_MODULES_IDENTICAL',len(entries)-1)
