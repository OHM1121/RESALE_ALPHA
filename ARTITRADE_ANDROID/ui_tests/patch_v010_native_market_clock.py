"""0.0.10 original 82-module follow-up patch applied AFTER v009.

Source of truth: 0.0.9 APK native SHA256=53a5ee6e...;
no ledger reset and no new broker execution privileges.
"""
from __future__ import annotations
import ast,base64,hashlib,io,zipfile
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
PARTS=[BASE/"app/src/main/assets/strategy_v070"/f"part_{i:02d}.txt" for i in range(22)]
BRIDGE=BASE/"app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java"
raw=base64.b64decode("".join(p.read_text("ascii") for p in PARTS),validate=True)
oldsha=hashlib.sha256(raw).hexdigest()
assert oldsha=="53a5ee6e82d43dd7ae154c74e4519e69515806c1a6740f1d079d7b47bfeac877",oldsha
java=BRIDGE.read_text("utf8")
assert java.count('CORE_SHA256="'+oldsha+'"')==1
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    infos=z.infolist()
    assert len(infos)==82 and z.testzip() is None
    original={i.filename:z.read(i.filename) for i in infos}
modified={}

def replace(name,old,new,n=1):
    assert name in original
    s=modified.get(name,original[name]).decode("utf8")
    assert s.count(old)==n,(name,s.count(old),n,old[:110])
    modified[name]=s.replace(old,new,n).encode("utf8")

# The 0.0.9 special KRX 10:00->16:30 transport was fixed, but the
# original ProjectGMO PAPER execution matcher was STILL 09:00->15:30.
replace("projectgmo/engine.py",
    "not time(9, 0) <= t.time() < time(15, 30) or",
    "not __import__('krx_session_v041').in_regular_clock(t,'KRX') or")
replace("projectgmo/engine.py",
    "time(9, 0) <= self._time(q.at).time() < time(15, 30))",
    "__import__('krx_session_v041').in_regular_clock(self._time(q.at),'KRX'))")
replace("projectgmo/engine.py",
    "if not (time(9, 0) <= t.time() < time(15, 30)):",
    "if not __import__('krx_session_v041').in_regular_clock(t,'KRX'):")
replace("projectgmo/engine.py",
    "purpose = 'D+1_OPEN_AT_OR_BELOW_OPEN' if (observed_price <= opening_price and observed_ask <= opening_price and (t.hour,t.minute)==(9,0))",
    "purpose = 'D+1_OPEN_AT_OR_BELOW_OPEN' if (observed_price <= opening_price and observed_ask <= opening_price and (t.hour,t.minute)==tuple(divmod(__import__('krx_session_v041').venue_bounds(t,'KRX')[0],60)))")

# Same D+1 re-arm, remaining shares, minute-bars and official open fallback
# must inherit the date's ACTUAL trading hours.
replace("d_gmo_runtime.py",
    "if not '0900' <= at.strftime('%H%M') < '1530':",
    "if not __import__('krx_session_v041').in_regular_clock(at,'KRX'):")
replace("d_gmo_runtime.py",
    "if ts[:12] == today + '0900':",
    "if ts[:12] == today + (lambda start:f'{start//60:02d}{start%60:02d}')(__import__('krx_session_v041').venue_bounds(datetime.strptime(today+'120000','%Y%m%d%H%M%S'),'KRX')[0]):")
replace("d_gmo_runtime.py",
    "if 9 <= at.hour < 16 and (at.hour != 15 or at.minute < 30):",
    "if __import__('krx_session_v041').in_regular_clock(at,'KRX'):")
replace("d_gmo_runtime.py",
    "target = now.date() if hm >= '1530' and _in_session(now.date(), self.root) else _date(_previous_day(now.date(), self.root))",
    "target = now.date() if (now.hour*60+now.minute>=__import__('krx_session_v041').venue_bounds(now,'KRX')[1] and _in_session(now.date(), self.root)) else _date(_previous_day(now.date(), self.root))")
replace("d_gmo_runtime.py",
    "ohlc_key = now.strftime('%Y%m%d') + ('_CLOSED' if hm >= '1530' and _in_session(now.date(), self.root) else '_PRIOR')",
    "ohlc_key = now.strftime('%Y%m%d') + ('_CLOSED' if now.hour*60+now.minute>=__import__('krx_session_v041').venue_bounds(now,'KRX')[1] and _in_session(now.date(), self.root) else '_PRIOR')")
# The background D-selection job must not flood the same official REST pool
# while a delayed KRX opening is being authenticated.
replace("d_gmo_runtime.py",
    """                            while ('0850' <= datetime.now(KST).strftime('%H%M') < '0930'
                                   and not self._stop.is_set()):""",
    """                            while (lambda t,v:v[0]-10<=t.hour*60+t.minute<v[0]+30)(
                                    datetime.now(KST),__import__('krx_session_v041').venue_bounds(datetime.now(KST),'KRX')) and not self._stop.is_set():""")

# 0.0.9 12:50 log showed the 0D numeric keys present, but validity failed.
# Presence is NOT positive tradable depth. Record only numerical public quote
# values and failure classification, never auth/account/order values.
replace("d_gmo_runtime.py",
    """                    keys='KRX6044='+str(bool(vals.get('6044')))+',KRX61='+str(bool(vals.get('61')))+',Q41='+str(bool(vals.get('41')))
                    self._log('GMO_BOOK_INVALID',code,
                        '0D no valid ask/ask_qty (same-venue only; '+keys+')')""",
    """                    reason=('ASK_ZERO' if ask<=0 else 'BID_ZERO' if bid<=0
                            else 'CROSSED_ASK_BID' if ask<bid else 'KRX_ASK_DEPTH_ZERO')
                    self._log('GMO_BOOK_INVALID',code,
                        '0D_'+reason+' KRX ask='+str(ask)+' bid='+str(bid)+
                        ' ask_qty='+str(ask_qty)+' bid_qty='+str(bid_qty)+
                        ' specific_depth_present='+str(bool(vals.get('6044')))+
                        ' combined_depth_present='+str(bool(vals.get('61')))+
                        ' (no invented 0D or broker receipt)')""")

# Original frame repair selected impossible 09:00 completed parents on the
# one-off 10:00 opening even though v009 patched parent validation elsewhere.
replace("d_live.py",
    "day_before_close = tf == 'day' and latest is None and ('0900' <= stamp[8:12] < '1530')",
    """session_bounds=__import__('krx_session_v041').venue_bounds(clock,'KRX')
            day_before_close = tf == 'day' and latest is None and (session_bounds[0] <= clock.hour*60+clock.minute < session_bounds[1])""")
replace("d_live.py",
    "expected_count = (datetime.strptime(latest, '%Y%m%d%H%M%S') - datetime.strptime(stamp[:8] + '090000', '%Y%m%d%H%M%S')).total_seconds()",
    """session_start=stamp[:8]+f'{session_bounds[0]//60:02d}{session_bounds[0]%60:02d}00'
                    expected_count = (datetime.strptime(latest, '%Y%m%d%H%M%S') - datetime.strptime(session_start, '%Y%m%d%H%M%S')).total_seconds()""")
replace("d_live.py",
    "(tf, stamp[:8] + '090000', latest)).fetchall()",
    "(tf, session_start, latest)).fetchall()")


# This SECOND live AT completion path was still comparing against imaginary
# 09:00 parents on the one-off delayed 10:00 open. It is independent from
# the higher-frame REST repair patched above. Apply the same date-specific
# KRX opening to coverage counting, without inventing missing candles.
replace("d_live.py",
    """                                required_bars = int((datetime.strptime(ref_ts, '%Y%m%d%H%M%S') - datetime.strptime(ref_ts[:8] + '090000', '%Y%m%d%H%M%S')).total_seconds()) // ({'5m': 5, '30m': 30, '60m': 60}[tf] * 60) + 1""",
    """                                from krx_session_v041 import venue_bounds
                                ref_datetime=datetime.strptime(ref_ts, '%Y%m%d%H%M%S')
                                open_min=venue_bounds(ref_datetime,'KRX')[0]
                                start_ref_ts=ref_ts[:8]+f'{open_min//60:02d}{open_min%60:02d}00'
                                required_bars = int((ref_datetime - datetime.strptime(start_ref_ts,'%Y%m%d%H%M%S')).total_seconds()) // ({'5m': 5, '30m': 30, '60m': 60}[tf] * 60) + 1""")
replace("d_live.py",
    """(tf, ref_ts[:8] + '090000', ref_ts)).fetchall()""",
    """(tf, start_ref_ts, ref_ts)).fetchall()""")

for name,body in modified.items():
    ast.parse(body.decode("utf8"),filename=name)
out=io.BytesIO()
with zipfile.ZipFile(out,"w") as z:
    for item in infos:z.writestr(item,modified.get(item.filename,original[item.filename]))
new=out.getvalue()
with zipfile.ZipFile(io.BytesIO(new)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    assert all(z.read(n)==original[n] for n in original if n not in modified)
newsha=hashlib.sha256(new).hexdigest()
encoded=base64.b64encode(new).decode("ascii")
width=(len(encoded)+21)//22
for i,path in enumerate(PARTS):
    path.write_text(encoded[i*width:(i+1)*width],encoding="ascii")
BRIDGE.write_text(java.replace('CORE_SHA256="'+oldsha+'"','CORE_SHA256="'+newsha+'"'),encoding="utf8")
print("V010_NATIVE_GMO_CLOCK_0D_AT_PARENT_PATCH_PASS")
print("OLD_SHA256="+oldsha)
print("NEW_SHA256="+newsha)
print("MODIFIED_MODULES="+",".join(sorted(modified)))
