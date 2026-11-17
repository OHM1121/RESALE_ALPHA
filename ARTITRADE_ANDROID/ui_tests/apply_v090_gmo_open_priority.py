"""Apply v0.7.90 strictly to original GMO service, leaving other 81 modules intact.

The 09:00 official-open recovery must not sit behind 2,000+ sequential D
research HTTP calls or a 40-second maintenance sleep. No broker orders are
added, no historical PAPER fills or DB state are changed.
"""
from __future__ import annotations
import ast,base64,hashlib,io,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MAIN=ROOT/"app/src/main"
ASSETS=MAIN/"assets/strategy_v070"
PARTS=[ASSETS/("part_%02d.txt"%i) for i in range(22)]
BRIDGE=MAIN/"java/com/artitrade/mobile/StrategyRuntimeBridge.java"
GRADLE=ROOT/"app/build.gradle"
blob=base64.b64decode("".join(x.read_text("ascii") for x in PARTS))
old_sha=hashlib.sha256(blob).hexdigest()
java=BRIDGE.read_text(encoding="utf-8")
assert ('CORE_SHA256="'+old_sha+'"') in java, "Original embedded hash mismatch"
assert old_sha=="5285721f3eda241b6f80e5ef804d3ba98144f537ae78993733173eda58e7dbe3",old_sha
with zipfile.ZipFile(io.BytesIO(blob)) as src:
    assert src.testzip() is None
    infos=src.infolist()
    assert len(infos)==82
    original={meta.filename:src.read(meta.filename) for meta in infos}
source=original["d_gmo_runtime.py"].decode("utf-8")
begin=source.index("    def _loop(self):")
end=source.index("    def start(self, api_provider, universe_provider):",begin)
old_loop=source[begin:end]
assert "                target = now.date() if hm >= '1530'" in old_loop
assert "                self._fetch_missing_opens(api)" in old_loop
assert "            self._stop.wait(40)" in old_loop
body_start=old_loop.index("                target = now.date() if hm >= '1530'")
body_end=old_loop.rindex("            self._stop.wait(40)")
scan_body=old_loop[body_start:body_end]
# The low-priority scanner yields before 08:50 even if a long scan began
# at 06:30. The hot 0B/0D processing workers are never changed.
guard="""                            if self._stop.is_set():
                                break
                            try:
                                self._scan_one(api, code, name, market, scan_key)"""
replacement="""                            if self._stop.is_set():
                                break
                            # Never let large D-research HTTP bursts crowd out
                            # 08:50-09:30 official open queries on shared API.
                            while ('0850' <= datetime.now(KST).strftime('%H%M') < '0930'
                                   and not self._stop.is_set()):
                                self._stop.wait(0.5)
                            if self._stop.is_set():
                                break
                            try:
                                self._scan_one(api, code, name, market, scan_key)"""
assert scan_body.count(guard)==1,scan_body.count(guard)
scan_body=scan_body.replace(guard,replacement,1)
# Only this original GMO module is changed. All other native paper engines,
# live API arming guards, sell stages and original books remain byte-for-byte.
new_loop='''    def _loop(self):
        """High-priority D+1 official opening-price recovery.

        Old loop ran full-universe D/ohlc REST scans synchronously AND slept
        40 seconds, so ka10001 early-zero could not be retried near 09:00.
        No historical data are used as executable quotes or fills.
        """
        last_open_try = 0.0
        while not self._stop.is_set():
            now = datetime.now(KST)
            hm = now.strftime('%H%M')
            if systime.monotonic() - self._last_overdue_check >= 40.0:
                self._last_overdue_check = systime.monotonic()
                self._warn_overdue_positions(now)
            api = self._api_provider() if self._api_provider else None
            if api is not None and '0900' <= hm < '2000':
                open_retry = 3.0 if hm < GMO_PRIORITY_END else 90.0
                if systime.monotonic() - last_open_try >= open_retry:
                    last_open_try = systime.monotonic()
                    self._fetch_missing_opens(api)
            # Fast polling only around KRX open. Other times retain the
            # original quiet 40-second background cadence.
            self._stop.wait(1.0 if '0850' <= hm < GMO_PRIORITY_END else 40.0)

    def _maintenance_loop(self):
        """Original D-selection and OHLC work, never blocking D+1 open checks."""
        last_scan = None
        last_ohlc = None
        last_scan_try = 0.0
        while not self._stop.is_set():
            now = datetime.now(KST)
            api = self._api_provider() if self._api_provider else None
            if api is not None:
                hm = now.strftime('%H%M')
                # Preserve pre-open REST capacity for already accepted D picks.
                if '0850' <= hm < GMO_PRIORITY_END:
                    self._stop.wait(1.0)
                    continue
'''
new_loop+=scan_body+"            self._stop.wait(40)

"
assert source.count(old_loop)==1
source=source.replace(old_loop,new_loop,1)
assert source.count("        self._thread = None")==1
source=source.replace("        self._thread = None",
                      "        self._thread = None\n        self._maintenance_thread = None",1)
old_start="""        self._thread = threading.Thread(target=self._loop, daemon=True, name='PROJECTGMO_READONLY_FLOW_AND_OHLC')
        self._thread.start()"""
new_start="""        self._thread = threading.Thread(target=self._loop, daemon=True, name='PROJECTGMO_D1_OPEN_PRIORITY')
        self._thread.start()
        self._maintenance_thread = threading.Thread(
            target=self._maintenance_loop, daemon=True,
            name='PROJECTGMO_BACKGROUND_D_SCAN_AND_OHLC')
        self._maintenance_thread.start()"""
assert source.count(old_start)==1
source=source.replace(old_start,new_start,1)
ast.parse(source)
assert "def _maintenance_loop(self):" in source
assert "self._stop.wait(1.0 if '0850' <= hm < GMO_PRIORITY_END else 40.0)" in source
assert "self._scan_one(api, code, name, market, scan_key)" in source
assert "if q.last <= ceiling and entry_ask <= ceiling and paper:" in source
assert "self.engine.paper_match(q)" in source
assert "self.engine.on_quote(q)" in source
original["d_gmo_runtime.py"]=source.encode("utf-8")
out=io.BytesIO()
with zipfile.ZipFile(out,"w") as z:
    for info in infos:z.writestr(info,original[info.filename])
newblob=out.getvalue()
with zipfile.ZipFile(io.BytesIO(blob)) as prior, zipfile.ZipFile(io.BytesIO(newblob)) as new:
    assert new.testzip() is None and len(new.namelist())==82
    for name in prior.namelist():
        if name!="d_gmo_runtime.py":
            assert prior.read(name)==new.read(name), "Unrelated native strategy changed: "+name
new_sha=hashlib.sha256(newblob).hexdigest()
encoded=base64.b64encode(newblob).decode("ascii")
stride=(len(encoded)+21)//22
for i,path in enumerate(PARTS):
    path.write_text(encoded[i*stride:(i+1)*stride],encoding="ascii")
BRIDGE.write_text(java.replace('CORE_SHA256="'+old_sha+'"',
                               'CORE_SHA256="'+new_sha+'"'),encoding="utf-8")
gradle=GRADLE.read_text(encoding="utf-8")
assert gradle.count("versionCode 95")==1
assert gradle.count("versionName '0.7.89-MA120-RSI-SELL-FIX'")==1
GRADLE.write_text(gradle.replace("versionCode 95","versionCode 96",1).replace(
    "versionName '0.7.89-MA120-RSI-SELL-FIX'",
    "versionName '0.7.90-GMO-D1-OPEN-PRIORITY'",1),encoding="utf-8")
for filename in ("test_v054_ui.py","test_v054_market_trend_audit.py"):
    path=ROOT/"ui_tests"/filename
    text=path.read_text(encoding="utf-8")
    old="versionName '0.7.89-MA120-RSI-SELL-FIX'"
    assert text.count(old)==1,(filename,text.count(old))
    path.write_text(text.replace(old,
        "versionName '0.7.90-GMO-D1-OPEN-PRIORITY'"),encoding="utf-8")
print("V090_ORIGINAL_SHA",old_sha)
print("V090_PATCHED_SHA",new_sha)
print("V090_GMO_ONLY_ONE_OF_82_ORIGINAL_MODULES_CHANGED")
print("V090_ORIGINAL_0B_0D_DPLUS1_DATE_SELL_MATCHER_UNTOUCHED")
print("V090_VERSIONCODE96_ORIGINAL_PACKAGENAME_AND_SIGNER_UNTOUCHED")
