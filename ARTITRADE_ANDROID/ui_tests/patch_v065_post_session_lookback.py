"""0.6.5 (owner 2026-11-27): (1) stocks supported until 20:00 must get 5-minute signals and orders: the volume look-back of the KRX POST (16:00-20:00) and NXT PRE (08:00-08:30) reference frames;
(2) the KRX continuous trading ends at 15:20: 15:20-15:30 is the closing single-price auction (frozen quote, nothing matched until the single price at 15:30), so the engine's session clock
`live_session(now,'KRX')` is 09:00-15:20 (the session end minus `krx_session_v041.CLOSING_AUCTION_MINUTES`) and 16:00-20:00, not 09:00-15:30.

`D19PaperEngine.on_extended_minute` builds the separate session frames and says in its own comment: "Require three previous 5m (two 30m/60m) bars within the same exchange session".
The code required `reference_volume_lookback(tf)` previous bars for 5m and 60m, i.e. 100 five-minute and 48 sixty-minute bars. A POST session has only 48 five-minute and 4 sixty-minute bars
(PRE: 6 and 0), so a 5m / 60m POST or PRE reference was structurally impossible. Device export 2026-11-27: 22,374 POST 5m frames of 273 stocks, 445 POST 60m frames, 1,179 PRE 5m frames and
only 3 POST signals in a week, all 30m. This patch makes the code do what its comment says (3 previous 5m bars, 2 previous 30m / 60m bars); every other rule (2x the average volume, above the
maximum, shape rejection, DIVERSE gate, upper-wick gate, order limits) is untouched.
Real-data replay of the five POST sessions 11-20..11-26 (device copy): POST 5m signals 0 -> 1 / 2 / 0 / 0 / 4, POST 60m signals 0 -> 0 / 0 / 1 / 2 / 0.

Runs on a throwaway checkout in CI AFTER patch_v010 (chain: v092, v093, v105, v008, v009, v010, v065).
"""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTS = [ROOT / "app/src/main/assets/strategy_v070" / f"part_{i:02d}.txt" for i in range(22)]
BRIDGE = ROOT / "app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java"
EXPECTED_BEFORE = "0daf09fe2753efb980f350a200a45c16caa2d5b57e75e24eede1c456dffd44fd"   # the engine after the chain v092..v010
raw = base64.b64decode("".join(p.read_text("ascii") for p in PARTS), validate=True)
old_sha = hashlib.sha256(raw).hexdigest()
assert old_sha == EXPECTED_BEFORE, ("UNEXPECTED_ENGINE_BEFORE_V065", old_sha)
source = BRIDGE.read_text("utf8")
assert source.count('CORE_SHA256="' + old_sha + '"') == 1, "Unexpected bridge hash"
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    infos = z.infolist()
    assert len(infos) == 82 and z.testzip() is None
    files = {info.filename: z.read(info.filename) for info in infos}
text = files["d_v19_portfolio.py"].decode("utf8")
before = "if len(prev)<(reference_volume_lookback(tf) if tf in ('5m','60m') else 2):continue"
after = "if len(prev)<(3 if tf=='5m' else 2):continue"
assert text.count(before) == 1 and text.count(after) == 0
text = text.replace(before, after, 1)
session_before = "        if bounds and bounds[0]<=now.hour*60+now.minute<bounds[1]:\n            return True"
session_after = "        if bounds and bounds[0]<=now.hour*60+now.minute<bounds[1]-__import__('krx_session_v041').CLOSING_AUCTION_MINUTES:\n            return True"
assert text.count(session_before) == 1 and text.count(session_after) == 0
text = text.replace(session_before, session_after, 1)
ast.parse(text)
files["d_v19_portfolio.py"] = text.encode("utf8")
out = io.BytesIO()
with zipfile.ZipFile(out, "w") as z:
    for info in infos:
        z.writestr(info, files[info.filename])
updated = out.getvalue()
with zipfile.ZipFile(io.BytesIO(updated)) as z:
    assert z.testzip() is None and len(z.namelist()) == 82
    assert all(z.read(i.filename) == files[i.filename] for i in infos)
new_sha = hashlib.sha256(updated).hexdigest()
assert new_sha != old_sha
encoded = base64.b64encode(updated).decode("ascii")
width = (len(encoded) + 21) // 22
for i, p in enumerate(PARTS):
    p.write_text(encoded[i * width:(i + 1) * width], "ascii")
BRIDGE.write_text(source.replace('CORE_SHA256="' + old_sha + '"', 'CORE_SHA256="' + new_sha + '"'), "utf8")
print("NEW_SHA256=" + new_sha)
print("V065_POST_SESSION_LOOKBACK_PATCH_PASS")
print("UNCHANGED_ORIGINAL_MODULES=81")
print("PAPER_EXECUTION_POLICY_UNCHANGED=true")
