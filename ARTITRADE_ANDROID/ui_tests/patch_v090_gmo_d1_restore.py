"""v090: exact v089 GMO D+1 'unfilled/pending' recovery, non-destructive."""
import ast, base64, hashlib, io, zipfile
from pathlib import Path
app=Path(__file__).resolve().parents[1]
assets=app/'app/src/main/assets/strategy_v070'
parts=[assets/f'part_{i:02d}.txt' for i in range(22)]
bridge=app/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
gradle=app/'app/build.gradle'
blob=base64.b64decode(''.join(p.read_text('ascii') for p in parts))
old=hashlib.sha256(blob).hexdigest()
assert old=='5285721f3eda241b6f80e5ef804d3ba98144f537ae78993733173eda58e7dbe3',old
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    originals=[(i,z.read(i.filename)) for i in z.infolist()]
source=next(data for i,data in originals if i.filename=='d_gmo_runtime.py').decode()
for before,after in (
    ("(row['status'] in ('후보', '대기', '매수대기'))",
     "(row['status'] in ('후보', '대기', '매수대기', '미체결/후보'))"),
    ("status IN ('후보','대기','매수대기')",
     "status IN ('후보','대기','매수대기','미체결/후보')"),
):
    assert source.count(before)==1,(before,source.count(before))
    source=source.replace(before,after,1)
ast.parse(source)
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for info,data in originals:
        z.writestr(info,source.encode() if info.filename=='d_gmo_runtime.py' else data)
after=out.getvalue()
with zipfile.ZipFile(io.BytesIO(after)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    for info,data in originals:
        assert z.read(info.filename)==(source.encode() if info.filename=='d_gmo_runtime.py' else data),info.filename
sha=hashlib.sha256(after).hexdigest()
java=bridge.read_text()
assert java.count('CORE_SHA256="'+old+'"')==1
build=gradle.read_text()
assert build.count('versionCode 95')==1
assert build.count("versionName '0.7.89-MA120-RSI-SELL-FIX'")==1
txt=base64.b64encode(after).decode()
size=(len(txt)+21)//22
for i,p in enumerate(parts):
    p.write_text(txt[i*size:(i+1)*size],encoding='ascii')
bridge.write_text(java.replace('CORE_SHA256="'+old+'"','CORE_SHA256="'+sha+'"'))
gradle.write_text(build.replace('versionCode 95','versionCode 96').replace(
    "versionName '0.7.89-MA120-RSI-SELL-FIX'",
    "versionName '0.7.90-GMO-D1-RESTORE'"))
print('V090_GMO_D1_RESTORE_PATCH_PASS',old,sha,'unchanged_modules=81')
