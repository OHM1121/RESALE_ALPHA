"""Add only official Kiwoom read-only ka10004 depth to the original D core.

This runs on a throwaway checkout during CI. It neither adds a broker-order
endpoint nor changes a PAPER strategy, execution or SQLite/WAL file.
"""
import ast
import base64
import hashlib
import io
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PARTS=[ROOT/'app/src/main/assets/strategy_v070'/f'part_{i:02d}.txt'
       for i in range(22)]
BRIDGE=ROOT/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
raw=base64.b64decode(''.join(p.read_text('ascii') for p in PARTS),validate=True)
old_sha=hashlib.sha256(raw).hexdigest()
source=BRIDGE.read_text('utf8')
assert source.count('CORE_SHA256="'+old_sha+'"')==1, 'Unexpected original source'
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    infos=z.infolist()
    assert len(infos)==82 and z.testzip() is None
    files={info.filename:z.read(info.filename) for info in infos}
live=files['d_live.py'].decode('utf8')
before="        allowed = {('ka10001','/api/dostk/stkinfo'),"
after="        allowed = {('ka10004','/api/dostk/mrkcond'),('ka10001','/api/dostk/stkinfo'),"
assert live.count(before)==1
assert live.count("('ka10004','/api/dostk/mrkcond')")==0
live=live.replace(before,after,1)
assert "if (api_id,path) not in allowed:raise RuntimeError('실주문 및 계좌 접근 API는 D버전에서 영구 차단')" in live
ast.parse(live)
files['d_live.py']=live.encode('utf8')
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for info in infos:z.writestr(info,files[info.filename])
updated=out.getvalue()
with zipfile.ZipFile(io.BytesIO(updated)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    assert all(z.read(info.filename)==files[info.filename] for info in infos)
new_sha=hashlib.sha256(updated).hexdigest()
assert new_sha!=old_sha
encoded=base64.b64encode(updated).decode('ascii')
width=(len(encoded)+21)//22
for i,p in enumerate(PARTS):
    p.write_text(encoded[i*width:(i+1)*width],'ascii')
BRIDGE.write_text(source.replace('CORE_SHA256="'+old_sha+'"',
                                 'CORE_SHA256="'+new_sha+'"'),'utf8')
print('V105_KIWOOM_READONLY_KA10004_PATCH_PASS')
print('UNCHANGED_ORIGINAL_MODULES=81')
print('PAPER_EXECUTION_POLICY_UNCHANGED=true')
