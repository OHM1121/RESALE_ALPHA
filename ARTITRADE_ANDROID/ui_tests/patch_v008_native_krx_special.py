"""Patch ORIGINAL bundled stock core after approved v092/v105 quote fixes.

No real orders are submitted here. The unpacked core remains byte-identical
except named session/venue authority replacements; entire output is AST parsed.
Designed for pinned temporary CI checkouts, never mutate a user's ledger.
"""
from __future__ import annotations
import ast,base64,hashlib,io,zipfile
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
PARTS=[BASE/"app/src/main/assets/strategy_v070"/f"part_{i:02d}.txt" for i in range(22)]
BRIDGE=BASE/"app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java"
raw=base64.b64decode("".join(p.read_text("ascii") for p in PARTS),validate=True)
old_sha=hashlib.sha256(raw).hexdigest()
b=BRIDGE.read_text("utf8")
assert b.count('CORE_SHA256="'+old_sha+'"')==1
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    infos=z.infolist()
    assert len(infos)==82 and z.testzip() is None
    original={i.filename:z.read(i.filename) for i in infos}
modified={}

def update(name,old,new,expected=1):
    data=modified.get(name,original[name]).decode("utf8")
    assert data.count(old)==expected,(name,old[:110],data.count(old))
    data=data.replace(old,new,expected)
    modified[name]=data.encode("utf8")

# Shared KRX/NXT session is authoritative by exact calendar date.
update("d_v19_portfolio.py",
"""    if venue=='NXT':
        return '080000'<=tm<'083000'
    if venue=='KRX':
        return ('090000'<=tm<'153000' or
                (now.strftime('%Y%m%d')>='20260914' and '160000'<=tm<'200000'))
""",
"""    from krx_session_v041 import venue_bounds
    bounds=venue_bounds(now,venue)
    if venue=='NXT':
        return bool(bounds and bounds[0]<=now.hour*60+now.minute<bounds[1])
    if venue=='KRX':
        if bounds and bounds[0]<=now.hour*60+now.minute<bounds[1]:
            return True
        # The original KRX after-market window is not a regular-session BUY.
        # On a shortened/extended session, do NOT mark 16:00 live as after-hours.
        after=17*60 if now.date().isoformat() in ("2026-11-19",) else 16*60
        return now.strftime('%Y%m%d')>='20260914' and after<=now.hour*60+now.minute<20*60
""")
update("d_v19_portfolio.py",
"""    if venue=='NXT' and '0800'<=hh<'0830':
        return 'PRE',ts[:8]+'080000',ts[:8]+'083000'
    if venue=='KRX' and ts[:8]>='20260914' and '1600'<=hh<'2000':
        return 'POST',ts[:8]+'160000',ts[:8]+'200000'
""",
"""    from krx_session_v041 import venue_bounds
    when=datetime.strptime(ts[:14],'%Y%m%d%H%M%S')
    bounds=venue_bounds(when,venue)
    if venue=='NXT' and bounds and bounds[0]<=when.hour*60+when.minute<bounds[1]:
        return 'PRE',ts[:8]+f'{bounds[0]//60:02d}{bounds[0]%60:02d}00',ts[:8]+f'{bounds[1]//60:02d}{bounds[1]%60:02d}00'
    start=17*60 if ts[:8]=='20261119' else 16*60
    if venue=='KRX' and ts[:8]>='20260914' and start<=when.hour*60+when.minute<20*60:
        return 'POST',ts[:8]+f'{start//60:02d}{start%60:02d}00',ts[:8]+'200000'
""")
# The original GMO previously recognized ONLY a 09:00 official opening.
# Restart after opening must never synthesize the opening; reference D close
# fallback is handled separately in v097 from the signed D-day signal.
update("d_gmo_runtime.py",
"""if event and q.trading_day_index == 1 and key not in self.opens and (at.hour, at.minute) == (9, 0):""",
"""if event and q.trading_day_index == 1 and key not in self.opens and (
                (at.hour,at.minute)==tuple(divmod(__import__('krx_session_v041').venue_bounds(at,'KRX')[0],60))):""")
update("d_gmo_runtime.py",
"'D+1 official KRX 09:00 open unavailable'",
"'D+1 official KRX venue opening proof unavailable; D-close fallback may still qualify'")
# Prevent phantom time-window limits in native original broker guard.
update("projectgmo/engine.py",
"""                time(9, 0) <= t.time() < time(15, 30))""",
"""                __import__('krx_session_v041').in_regular_clock(t,'KRX'))""",expected=2)
update("projectgmo/engine.py",
"""if t.date().isoformat() != entry_date or not ('09:00' <= t.strftime('%H:%M') < '15:30'):""",
"""if t.date().isoformat() != entry_date or not __import__('krx_session_v041').in_regular_clock(t,'KRX'):""")
# The real-time 0B/0D parser already calls d_v19.live_session;
# align the other UTC/KST REST/maintenance limits to the same schedule.
update("d_live.py",
"""        if venue == 'KRX' and '0900' <= ts[8:12] < '1530':""",
"""        if venue == 'KRX' and __import__('krx_session_v041').in_regular_clock(
                datetime.strptime(ts[:14],'%Y%m%d%H%M%S').replace(tzinfo=KST),'KRX'):""")
update("d_live.py",
"""        if now.second < 4 or not '0901' <= now.strftime('%H%M') <= '1530':
            return""",
"""        from krx_session_v041 import venue_bounds
        bounds=venue_bounds(now,'KRX')
        minute=now.hour*60+now.minute
        if now.second<4 or not bounds or not bounds[0]+1<=minute<=bounds[1]:
            return""")
update("d_live.py",
"""            if not '0900' <= ts[8:12] < '1530':
                continue""",
"""            if not __import__('krx_session_v041').in_regular_clock(
                    datetime.strptime(ts[:14],'%Y%m%d%H%M%S').replace(tzinfo=KST),'KRX'):
                continue""")
# The transport MUST NOT PAUSE during the last hour of the extended KRX day.
update("d_live.py",
"""            if active and '1530' <= clock < '1600':""",
"""            from krx_session_v041 import venue_bounds
            bounds=venue_bounds(now_kst(),'KRX')
            close_minutes=bounds[1] if bounds else 15*60+30
            after_start=close_minutes+30
            minute=int(clock[:2])*60+int(clock[2:])
            if active and close_minutes<=minute<after_start:""")
update("d_live.py",
"""            if active and '0830' <= clock < '0900' and not self.gmo.priority_watchlist(now_kst()):""",
"""            if active and bounds and bounds[0]-30<=minute<bounds[0] and not self.gmo.priority_watchlist(now_kst()):""")
update("d_live.py",
"""                         cutover=(now.strftime('%Y%m%d%H%M') if now.strftime('%H%M')
                                  in ('0830','0850','0900','1530','1600') else '')""",
"""                         from krx_session_v041 import venue_bounds
                         vb=venue_bounds(now,'KRX')
                         closing=vb[1] if vb else 15*60+30
                         before=vb[0] if vb else 9*60
                         boundaries={(8,30),(8,50)}
                         boundaries.update((divmod(before,60),divmod(closing,60),
                                            divmod(closing+30,60)))
                         cutover=(now.strftime('%Y%m%d%H%M') if
                                  (now.hour,now.minute) in boundaries else '')""")
update("d_live.py",
"""                                     not '1530' <= now.strftime('%H%M') < '1600'))""",
"""                                     not closing<=now.hour*60+now.minute<closing+30))""")
update("d_live.py",
"""                pause = active and '1530' <= now_kst().strftime('%H%M') < '1600'""",
"""                observed=now_kst()
                from krx_session_v041 import venue_bounds
                v=venue_bounds(observed,'KRX')
                m=observed.hour*60+observed.minute
                pause = active and bool(v and v[1]<=m<v[1]+30)""")
# As a final principle, no other strategy / account/order logic is touched.
for name,blob in modified.items():
    ast.parse(blob.decode('utf8'),filename=name)
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for info in infos:z.writestr(info,modified.get(info.filename,original[info.filename]))
payload=out.getvalue()
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    assert all(z.read(n)==original[n] for n in original if n not in modified)
new_sha=hashlib.sha256(payload).hexdigest()
encoded=base64.b64encode(payload).decode('ascii')
width=(len(encoded)+21)//22
for i,part in enumerate(PARTS):
    part.write_text(encoded[i*width:(i+1)*width],encoding='ascii')
BRIDGE.write_text(b.replace('CORE_SHA256="'+old_sha+'"','CORE_SHA256="'+new_sha+'"'),encoding='utf8')
print('V008_NATIVE_KRX_SESSION_ADAPTER_PASS')
print('OLD_SHA256='+old_sha)
print('NEW_SHA256='+new_sha)
print('MODIFIED_ORIGINAL_MODULES='+','.join(sorted(modified)))
print('V008_NATIVE_KRX_SOURCE_COMPILED_AND_OTHER_MODULES_INTACT')
