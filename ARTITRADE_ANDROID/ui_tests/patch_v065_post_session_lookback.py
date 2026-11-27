"""0.6.5 / 0.6.6 engine patch (owner 2026-11-27), applied AFTER patch_v010 in CI (chain: v092, v093, v105, v008, v009, v010, v065).

1. After-hours reference volume look-back (0.6.6). `D19PaperEngine.on_extended_minute` builds the separate completed KRX POST (16:00-20:00) and NXT PRE (08:00-08:30) frames. It looked only at
   earlier frames of the SAME extended session for the volume comparison ("previous 100 five-minute / 48 sixty-minute bars"), but a POST session has 48 five-minute and 4 sixty-minute bars (PRE: 6 / 0): a 5m / 60m
   after-hours reference was structurally impossible (device export: 22,374 POST 5m frames, only 3 POST signals in a week, all 30m). The owner: KRX and NXT are one market, look at the KRX bars too.
   The previous bars are now the ONE time-ordered series of the stock: the regular-session `bars` plus the earlier extended frames; the required count (100 / 48 / 2) and every other rule are unchanged.
2. The completed POST / PRE frames are also written to `bars` (source LIVE_EXT_<venue>_<segment>), so every strategy reads one continuous series across the sessions (0.6.6).
3. KRX closing single-price auction 15:20-15:30 (0.6.5): `live_session(now,'KRX')` ends at the session end minus `krx_session_v041.CLOSING_AUCTION_MINUTES`.
4. Supply-demand (GMO) paper matcher and D+1 entry / matching clock: `in_regular_clock` -> `in_tradable_clock` (continuous 09:00-15:20 or POST 16:00-20:00) at the six session checks of projectgmo/engine.py (0.6.6).
5. MA paper engine: new entries are also allowed in the after-hours session 16:00:00-19:59:15 (the tick path above already requires an after-market entitlement), besides 09:00:00-15:19:15 (0.6.6).
6. The closing single-price auction 15:20-15:30 is never counted as missing candles / unobserved minutes (0.6.7): the candidate forward observation counts a 380 minute session (was 390, +10 "unobserved"
   every night) and the fast one-minute repair does not queue the minutes 15:20-15:29.
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
def swap(old, new, count=1):
    global text
    assert text.count(old) == count and text.count(new) == 0, (old[:90], text.count(old))
    text = text.replace(old, new)


# 1. one time-ordered series: regular bars + earlier extended frames
swap("""                    prev=c.execute('''SELECT ts,open,high,low,close,volume FROM v19_venue_frames
                        WHERE code=? AND venue=? AND segment=? AND frame=? AND ts<?
                        ORDER BY ts DESC LIMIT ?''',(code,venue,period,tf,first,reference_volume_lookback(tf))).fetchall()""",
     """                    prev=c.execute('''SELECT ts,open,high,low,close,volume FROM (
                          SELECT ts,open,high,low,close,volume FROM bars WHERE code=? AND frame=? AND ts<? AND source<>'' AND source NOT LIKE '%EXT_%'
                          UNION ALL
                          SELECT ts,open,high,low,close,volume FROM v19_venue_frames WHERE code=? AND frame=? AND ts<?)
                        ORDER BY ts DESC LIMIT ?''',(code,tf,first,code,tf,first,reference_volume_lookback(tf))).fetchall()""")
# 2. completed extended frames also go to bars
swap("""                    if (tf=='5m' and offset//n<2) or rejection_reason(bar,mkt,tf):continue""",
     """                    c.execute('''INSERT OR IGNORE INTO bars(code,market,frame,ts,open,high,low,close,volume,source)
                        VALUES(?,?,?,?,?,?,?,?,?,?)''',(code,mkt,tf,first,bar.open,bar.high,bar.low,bar.close,bar.volume,'LIVE_EXT_'+venue+'_'+period))
                    if (tf=='5m' and offset//n<2) or rejection_reason(bar,mkt,tf):continue""")
# 3. closing auction
swap("        if bounds and bounds[0]<=now.hour*60+now.minute<bounds[1]:\n            return True",
     "        if bounds and bounds[0]<=now.hour*60+now.minute<bounds[1]-__import__('krx_session_v041').CLOSING_AUCTION_MINUTES:\n            return True")
files["d_v19_portfolio.py"] = text.encode("utf8")

# 4. supply-demand clock
gmo = files["projectgmo/engine.py"].decode("utf8")
assert gmo.count("__import__('krx_session_v041').in_regular_clock(") == 6, gmo.count("__import__('krx_session_v041').in_regular_clock(")
gmo = gmo.replace("__import__('krx_session_v041').in_regular_clock(", "__import__('krx_session_v041').in_tradable_clock(")
ast.parse(gmo)
files["projectgmo/engine.py"] = gmo.encode("utf8")

# 5. MA paper engine: entries in the after-hours session too
ma = files["stock_ma_engine.py"].decode("utf8")
ma_old = "            if '090000'<=now.strftime('%H%M%S')<'151915' and not after:"
ma_new = "            if ('090000'<=now.strftime('%H%M%S')<'151915' and not after) or (after and '160000'<=now.strftime('%H%M%S')<'195915'):"
assert ma.count(ma_old) == 1 and ma.count(ma_new) == 0
ma = ma.replace(ma_old, ma_new)
ast.parse(ma)
files["stock_ma_engine.py"] = ma.encode("utf8")
# 6. owner 2026-11-28: the closing single-price auction 15:20-15:30 is not "missing candles".
# (a) candidate forward observation counted a 390 minute session (09:00-15:30): every night added ~10 "unobserved" minutes -> now 380 (09:00-15:20)
ch = files["d_candidate_history.py"].decode("utf8")
for old, new, n in (("min (390 ,(int (t [8 :10 ])*60 +int (t [10 :12 ]))-(9 *60 ))", "min (380 ,(int (t [8 :10 ])*60 +int (t [10 :12 ]))-(9 *60 ))", 1),
                    ("total =390 -_mins_intraday (start )", "total =380 -_mins_intraday (start )", 1),
                    ("if day .weekday ()<5 :total +=390 ", "if day .weekday ()<5 :total +=380 ", 1)):
    assert ch.count(old) == n and ch.count(new) == 0, (old, ch.count(old))
    ch = ch.replace(old, new)
ast.parse(ch)
files["d_candidate_history.py"] = ch.encode("utf8")
# (b) the fast one-minute repair never queues a minute of the closing auction (15:20-15:29): nothing trades there, the official record cannot exist
mr = files["d_minute_repair_fast.py"].decode("utf8")
mr_old = "(when.hour==15 and when.minute<30)"
mr_new = "(when.hour==15 and when.minute<20)"
assert mr.count(mr_old) == 1 and mr.count(mr_new) == 0
mr = mr.replace(mr_old, mr_new)
ast.parse(mr)
files["d_minute_repair_fast.py"] = mr.encode("utf8")
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
