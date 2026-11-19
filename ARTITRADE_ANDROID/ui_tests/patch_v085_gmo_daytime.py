"""v0.7.85 pinned original archive patch; preserve 81 unrelated original modules.
Trade policies, RSI, quote TTL, saved wallets and historical SQLite are unchanged.
Only GMO confirmed-D scan time and D->D+1 pending visibility are changed.
"""
from pathlib import Path
import base64,hashlib,io,zipfile,ast

root=Path(__file__).resolve().parents[1]
base=root/'app/src/main'
parts=[base/'assets/strategy_v070'/f'part_{i:02d}.txt' for i in range(22)]
java=base/'java/com/artitrade/mobile/StrategyRuntimeBridge.java'
gradle=root/'app/build.gradle'
blob=base64.b64decode(''.join(p.read_text('ascii') for p in parts))
before_sha=hashlib.sha256(blob).hexdigest()
assert before_sha=='0b23a61cf4f2a5bbfa6f3d409829e41f8a2ebac5167abdd0ae0a0112e05fa6d0',before_sha
j=java.read_text()
assert 'CORE_SHA256="'+before_sha+'"' in j
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    infos=z.infolist()
    original={i.filename:z.read(i.filename) for i in infos}
filename='d_gmo_runtime.py'
s=original[filename].decode('utf-8')
def once(old,new):
    global s
    assert s.count(old)==1,(old[:110],s.count(old))
    s=s.replace(old,new,1)
once("target = now.date() if now.strftime('%H%M') >= '2020' else _date(_previous_day(now.date(), self.root))",
     "target = now.date() if hm >= '1530' and _in_session(now.date(), self.root) else _date(_previous_day(now.date(), self.root))")
once("(now.strftime('%H%M') >= '2020' or '0630' <= now.strftime('%H%M') < '2000')",
     "('0630' <= hm)")
once("ohlc_key = now.strftime('%Y%m%d') + ('_CLOSED' if now.strftime('%H%M') >= '2020' else '_PRIOR')",
     "ohlc_key = now.strftime('%Y%m%d') + ('_CLOSED' if hm >= '1530' and _in_session(now.date(), self.root) else '_PRIOR')")
once("if _date(x['entry_date']) != datetime.now(KST).date():\n                        continue",
     """# D-day accepted candidates remain visible BEFORE the next KRX
                    # session, not just on their D+1 entry morning.
                    today=datetime.now(KST).date()
                    if _date(x['entry_date']) < today or _date(x['event_date']) > today:
                        continue""")
ast.parse(s)
assert "target = now.date() if hm >= '1530'" in s
assert "if _date(x['entry_date']) < today" in s
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for info in infos:
        z.writestr(info,s.encode('utf-8') if info.filename==filename else original[info.filename])
newblob=out.getvalue()
with zipfile.ZipFile(io.BytesIO(newblob)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    for n in original:
        if n!=filename:assert original[n]==z.read(n)
new_sha=hashlib.sha256(newblob).hexdigest()
txt=base64.b64encode(newblob).decode('ascii')
width=(len(txt)+21)//22
for i,part in enumerate(parts):part.write_text(txt[i*width:(i+1)*width])
java.write_text(j.replace('CORE_SHA256="'+before_sha+'"','CORE_SHA256="'+new_sha+'"'))
g=gradle.read_text()
assert g.count('versionCode 90')==1
assert g.count("versionName '0.7.84-WS-TOP40-TICK-COALESCE'")==1
gradle.write_text(g.replace('versionCode 90','versionCode 91').replace(
    "versionName '0.7.84-WS-TOP40-TICK-COALESCE'",
    "versionName '0.7.85-RESEARCH-GMO-PAPER'"))
for file in ('test_v054_ui.py','test_v054_market_trend_audit.py'):
    f=root/'ui_tests'/file
    tests=f.read_text()
    old="versionName '0.7.84-WS-TOP40-TICK-COALESCE'"
    assert tests.count(old)==1,(file,tests.count(old))
    f.write_text(tests.replace(old,"versionName '0.7.85-RESEARCH-GMO-PAPER'"))
print('V085_GMO_D_1530_DPLUS1_PENDING_PASS',before_sha,new_sha,'unchanged_modules=81')
