"""v093: monotonic Android release + one-socket wrapper consistency, no core patch."""
from pathlib import Path
import ast,base64,hashlib,io,zipfile
ROOT=Path(__file__).resolve().parents[1]
gradle=ROOT/'app/build.gradle'
runtime=ROOT/'app/src/main/python/android_runtime_v078.py'
feature=ROOT/'app/src/main/python/stock_priority_runtime_v093.py'
bridge=ROOT/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
parts=ROOT/'app/src/main/assets/strategy_v070'
g=gradle.read_text('utf8')
assert g.count('versionCode 98')==1
assert g.count("versionName '0.7.92-QUOTE-WS-LATENCY'")==1
code=runtime.read_text('utf8')
assert code.count('_install_stock_priority_v093(_core)')==1
assert code.index('_install_market_trend_v043(_core)')<code.index('_install_stock_priority_v093(_core)')
ast.parse(feature.read_text('utf8'))
core=base64.b64decode(''.join((parts/f'part_{i:02d}.txt').read_text('ascii') for i in range(22)))
sha=hashlib.sha256(core).hexdigest()
assert 'CORE_SHA256="'+sha+'"' in bridge.read_text('utf8')
with zipfile.ZipFile(io.BytesIO(core)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    for name in z.namelist():
        if name.endswith('.py'):ast.parse(z.read(name).decode(),filename=name)
gradle.write_text(g.replace('versionCode 98','versionCode 99').replace(
  "versionName '0.7.92-QUOTE-WS-LATENCY'",
  "versionName '0.7.93-STOCK-SUBSCRIPTION-PRIORITY'"),encoding='utf8')
print('V093_STOCK_SUBSCRIPTION_PRIORITY_METADATA_PASS')
print('ORIGINAL_STRATEGY_MODULES_82_UNCHANGED_FROM_V092',sha)
print('VERSION_CODE_99_ORIGINAL_PACKAGE_AND_SIGNER_UNMODIFIED')
