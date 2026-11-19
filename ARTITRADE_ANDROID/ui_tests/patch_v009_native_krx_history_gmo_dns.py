"""0.0.9 root-cause-native patch applied AFTER v092/v093/v105/v008.
No source wallet reset or fabricated broker fills. Enforces exact patches of
calendar, 0D subscription recovery and historical REST outage circuits.
"""
from __future__ import annotations
import ast,base64,hashlib,io,zipfile
from pathlib import Path

BASE=Path(__file__).resolve().parents[1]
PARTS=[BASE/"app/src/main/assets/strategy_v070"/f"part_{i:02d}.txt" for i in range(22)]
BRIDGE=BASE/"app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java"
raw=base64.b64decode("".join(p.read_text("ascii") for p in PARTS),validate=True)
old_sha=hashlib.sha256(raw).hexdigest()
bridge=BRIDGE.read_text("utf8")
assert bridge.count('CORE_SHA256="'+old_sha+'"')==1, "V009_CORE_SOURCE_MISMATCH"
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    infos=z.infolist()
    assert z.testzip() is None and len(infos)==82
    orig={i.filename:z.read(i.filename) for i in infos}
modified={}

def replace(name,before,after,count=1):
    assert name in orig,name
    text=modified.get(name,orig[name]).decode("utf8")
    found=text.count(before)
    assert found==count,(name,"expected",count,"actual",found,before[:150])
    modified[name]=text.replace(before,after,count).encode("utf8")

# ORIGINAL AT parent validators still presumed every day opened 09:00, even
# though the v008 trading 0B/0D gates used 2026-11-19 10:00.
replace("d_engine.py",
"""    if tf == 'day':
        return ts[:8] + '153000'
    dt = datetime.strptime(ts[:14], '%Y%m%d%H%M%S') + timedelta(minutes=PERIOD[tf])
    day = ts[:8]
    end = day + '153000'
    stamp = dt.strftime('%Y%m%d%H%M%S')
    return min(stamp, end) if '1500' <= ts[8:12] < '1530' else stamp""",
"""    from krx_session_v041 import venue_bounds
    when = datetime.strptime(ts[:14], '%Y%m%d%H%M%S')
    bounds = venue_bounds(when,'KRX')
    closing = when.replace(hour=bounds[1]//60,minute=bounds[1]%60,second=0)
    if tf == 'day':
        return closing.strftime('%Y%m%d%H%M%S')
    next_end = when + timedelta(minutes=PERIOD[tf])
    minute = when.hour*60+when.minute
    if bounds[1]-30 <= minute < bounds[1]:
        next_end = min(next_end,closing)
    return next_end.strftime('%Y%m%d%H%M%S')""")
replace("d_engine.py",
"""        period = PERIOD[tf]
        minutes = d.hour * 60 + d.minute - 540
        cap = 380 if tf == '5m' else 390
        return 0 <= minutes < cap and minutes % period == 0""",
"""        from krx_session_v041 import venue_bounds
        bounds = venue_bounds(d,'KRX')
        period = PERIOD[tf]
        minutes = d.hour*60+d.minute-bounds[0]
        cap = (bounds[1]-bounds[0]-10 if tf=='5m'
               else bounds[1]-bounds[0])
        return 0 <= minutes < cap and minutes % period == 0""")
replace("d_engine.py",
"""    day = ref_ts[:8]
    start = datetime.strptime(day + '090000', '%Y%m%d%H%M%S')
    end = datetime.strptime(ref_ts[:14], '%Y%m%d%H%M%S')""",
"""    from krx_session_v041 import venue_bounds
    day = ref_ts[:8]
    bounds=venue_bounds(datetime.strptime(day+'120000','%Y%m%d%H%M%S'),'KRX')
    start = datetime.strptime(
        day+f'{bounds[0]//60:02d}{bounds[0]%60:02d}00','%Y%m%d%H%M%S')
    end = datetime.strptime(ref_ts[:14], '%Y%m%d%H%M%S')""")
replace("d_multiframe_recovery_fix14.py",
"""    d=datetime.strptime(stamp[:14],'%Y%m%d%H%M%S')
    day=d.strftime('%Y%m%d')
    if tf=='day':
        return day+'000000' if stamp[8:12]>='1530' else None
    t0=d.replace(hour=9,minute=0,second=0)
    minute=int((d-t0).total_seconds()//60)
    period={'5m':5,'30m':30,'60m':60}[tf]
    if minute<period:return None
    # 15:20-15:30 is KRX closing single-price auction. An ordinary 15:25
    # 5-minute print is NOT mandatory: no executions need occur until 15:30.
    # Last completed continuous-auction-free 5m reference is 15:15.
    # Do NOT synthesize 15:20/15:25 bars or treat 15:30 as a 5m start.
    if tf=='5m' and minute>=380:return day+'151500'
    # 60m 15:00-15:30 is a final 30m regular-session slot.
    if tf=='60m' and minute>=390:return day+'150000'
    idx=min(minute//period-1,(390-1)//period)
    if idx<0:return None
    return (t0+timedelta(minutes=idx*period)).strftime('%Y%m%d%H%M%S')""",
"""    from krx_session_v041 import venue_bounds
    d=datetime.strptime(stamp[:14],'%Y%m%d%H%M%S')
    day=d.strftime('%Y%m%d')
    bounds=venue_bounds(d,'KRX')
    duration=bounds[1]-bounds[0]
    minute_of_day=d.hour*60+d.minute
    if tf=='day':
        return day+'000000' if minute_of_day>=bounds[1] else None
    t0=d.replace(hour=bounds[0]//60,minute=bounds[0]%60,second=0)
    minute=int((d-t0).total_seconds()//60)
    period={'5m':5,'30m':30,'60m':60}[tf]
    if minute<period:return None
    # The last ten minutes are closing auction on EVERY approved calendar day.
    # No imaginary 09:xx parents are required on a delayed 10:00 KRX opening.
    if tf=='5m' and minute>=duration-10:
        return (t0+timedelta(minutes=duration-15)).strftime('%Y%m%d%H%M%S')
    if tf=='60m' and minute>=duration:
        return (t0+timedelta(minutes=duration-30)).strftime('%Y%m%d%H%M%S')
    idx=min(minute//period-1,(duration-1)//period)
    if idx<0:return None
    return (t0+timedelta(minutes=idx*period)).strftime('%Y%m%d%H%M%S')""")
replace("d_multiframe_recovery_fix14.py",
"""    earliest=(stamp[:8]+'090000' if tf!='day'""",
"""    from krx_session_v041 import venue_bounds
    clock=venue_bounds(datetime.strptime(stamp[:14],'%Y%m%d%H%M%S'),'KRX')[0]
    earliest=(stamp[:8]+f'{clock//60:02d}{clock%60:02d}00' if tf!='day'""")
replace("d_minute_repair_fast.py",
"""    if not (('0900'<=old.strftime('%H%M')<'1530') and
            ('0900'<=now.strftime('%H%M')<'1530')):return []""",
"""    from krx_session_v041 import in_regular_clock
    if not (in_regular_clock(old,'KRX') and
            in_regular_clock(now,'KRX')):return []""")

# Root cause in user's 0.0.7 dump: 135 GMO 0D invalid entries while 0B
# was actually received, and original re-REG stopped at 09:30 even though
# 2026-11-19 KRX opened 10:00. Repair 0D on SAME subscribed socket all day.
# Successful REG is never considered genuine depth or an accepted order.
replace("d_live.py",
"""        minute = current.strftime('%H%M')
        # Allow premarket 0D warmup as well as immediate opening recovery.
        if not ('0852' <= minute < '0930'):
            return []""",
"""        from krx_session_v041 import venue_bounds
        bounds=venue_bounds(current,'KRX')
        minute=current.hour*60+current.minute
        # Includes preopen, delayed opening and EVERY in-session reconnect.
        # Never re-register after the venue closes.
        if not (bounds[0]-8 <= minute < bounds[1]):
            return []""")
replace("d_live.py",
"""            # Max one attempt per 24 seconds per group; no catch-up bursts.
            if mono-self._gmo_depth_repair_at.get(key,-1e9)<24.0:""",
"""            # Opening gets priority; later outages remain bounded to 60s
            # per group. This is only REG on the already authorized WS.
            interval=24.0 if minute<bounds[0]+15 else 60.0
            if mono-self._gmo_depth_repair_at.get(key,-1e9)<interval:""")
replace("d_live.py",
"""                if moment.weekday() >= 5 or not '0855' <= moment.strftime('%H%M') <= '1540':
                    self._frame_repair_retry[key] = time.monotonic() + 180.0
                    continue""",
"""                from krx_session_v041 import venue_bounds
                bounds=venue_bounds(moment,'KRX')
                m=moment.hour*60+moment.minute
                if moment.weekday() >= 5 or not bounds[0]-5 <= m <= bounds[1]+10:
                    self._frame_repair_retry[key] = time.monotonic() + 180.0
                    continue""")
# Common *history-only* circuit after DNS; skip work rather than launching
# hundreds of REST queries per stocked symbol during a connectivity outage.
replace("d_live.py",
"""                result = recover_frame(self.store, self.engine, self.api, code, tf)
                state = result['state']""",
"""                from artitrade_history_dns_circuit_v009 import shared_circuit
                permitted,delay=shared_circuit().allow()
                if not permitted:
                    self._frame_repair_retry[key]=time.monotonic()+delay+0.2
                    continue
                result = recover_frame(self.store, self.engine, self.api, code, tf)
                shared_circuit().success()
                state = result['state']""")
replace("d_live.py",
"""            except Exception as exc:
                self.store.write_runtime_error(exc, 'fix14_higher_frame_repair:' + code + ':' + tf)""",
"""            except Exception as exc:
                from artitrade_history_dns_circuit_v009 import shared_circuit
                wait=shared_circuit().failure(exc)
                if wait>0:
                    self._frame_repair_retry[key]=time.monotonic()+wait+0.2
                self.store.write_runtime_error(exc, 'fix14_higher_frame_repair:' + code + ':' + tf)""")
replace("d_live.py",
"""                try:
                    packet = self.api.post('ka10080', '/api/dostk/chart', {'stk_cd': code, 'tic_scope': '1', 'upd_stkpc_tp': '1', 'base_dt': now_kst().strftime('%Y%m%d')})
                    official = parse_official_one_minute(packet, code, minute)""",
"""                try:
                    from artitrade_history_dns_circuit_v009 import shared_circuit
                    allowed,wait=shared_circuit().allow()
                    if not allowed:
                        self._minute_repair_retry[key]=time.monotonic()+wait+0.2
                        continue
                    packet = self.api.post('ka10080', '/api/dostk/chart', {'stk_cd': code, 'tic_scope': '1', 'upd_stkpc_tp': '1', 'base_dt': now_kst().strftime('%Y%m%d')})
                    shared_circuit().success()
                    official = parse_official_one_minute(packet, code, minute)""")
replace("d_live.py",
"""                    if self._minute_repair_tries.get(key, 0) < 2:
                        self._minute_repair_retry[key] = time.monotonic() + 12
                    self.store.write_runtime_error(exc, 'fast_1m_official_restore:' + code)""",
"""                    from artitrade_history_dns_circuit_v009 import shared_circuit
                    wait=shared_circuit().failure(exc)
                    if self._minute_repair_tries.get(key, 0) < 2 or wait>0:
                        self._minute_repair_retry[key] = time.monotonic() + max(12.0,wait)+0.2
                    self.store.write_runtime_error(exc, 'fast_1m_official_restore:' + code)""")
# Native GMO priority windows were ALSO pinned to the ordinary 09:00
# opening. Merely patching the 0D timer did not keep priority/REST/watch
# alive for the special 10:00 open or a later reconnect.
replace("d_gmo_runtime.py",
"""    def _priority_phase(self, at):
        hm = at.strftime('%H%M')
        if GMO_PRIORITY_START <= hm < GMO_NXT_MONITOR_END:
            return 'NXT_PREMARKET_MONITOR'
        if GMO_KRX_PREOPEN_START <= hm < SESSION_START:
            return 'KRX_PREOPEN_ARM'
        if SESSION_START <= hm < GMO_PRIORITY_END:
            return 'KRX_GMO_PRIORITY'
        return ''""",
"""    def _priority_phase(self, at):
        from krx_session_v041 import venue_bounds
        bounds=venue_bounds(at,'KRX')
        minute=at.hour*60+at.minute
        if bounds[0]!=10*60 and 8*60<=minute<8*60+50:
            return 'NXT_PREMARKET_MONITOR'
        if bounds[0]-10<=minute<bounds[0]:
            return 'KRX_PREOPEN_ARM'
        if bounds[0]<=minute<bounds[0]+30:
            return 'KRX_GMO_PRIORITY'
        return ''""")
replace("d_gmo_runtime.py",
"""                if code in priority and GMO_PRIORITY_START <= hm < GMO_PRIORITY_END:""",
"""                if code in priority and self._priority_phase(now):""")
replace("d_gmo_runtime.py",
"""            if api is not None and '0900' <= hm < '2000':
                open_retry = 3.0 if hm < GMO_PRIORITY_END else 90.0""",
"""            from krx_session_v041 import venue_bounds
            bounds=venue_bounds(now,'KRX')
            minute=now.hour*60+now.minute
            if api is not None and bounds[0] <= minute < 20*60:
                open_retry = 3.0 if minute < bounds[0]+30 else 90.0""")
replace("d_gmo_runtime.py",
"""            self._stop.wait(1.0 if '0850' <= hm < GMO_PRIORITY_END else 40.0)""",
"""            self._stop.wait(1.0 if bounds[0]-10<=minute<bounds[0]+30 else 40.0)""")
replace("d_gmo_runtime.py",
"""                if '0850' <= hm < GMO_PRIORITY_END:""",
"""                if (lambda v:v[0]-10<=now.hour*60+now.minute<v[0]+30)(
                        __import__('krx_session_v041').venue_bounds(now,'KRX')):""")
replace("d_live.py",
"""        priority=await asyncio.to_thread(self.gmo.priority_watchlist,current)
        if not priority:
            return []""",
"""        priority=set(await asyncio.to_thread(
            self.gmo.priority_watchlist,current))
        # A reconnected D+1 watcher may have an authentic signal but no
        # outstanding BUY yet, precisely because 0D never arrived.
        priority.update(await asyncio.to_thread(
            self.gmo.active_buy_watchlist,current))
        priority.update(await asyncio.to_thread(self.gmo.watchlist))
        if not priority:
            return []""")

# Enforce all 82 original modules compile and none else changes.
for name,code in modified.items():
    ast.parse(code.decode("utf8"),filename=name)
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for info in infos:
        z.writestr(info,modified.get(info.filename,orig[info.filename]))
payload=out.getvalue()
with zipfile.ZipFile(io.BytesIO(payload)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    assert all(z.read(n)==orig[n] for n in orig if n not in modified)
new_sha=hashlib.sha256(payload).hexdigest()
encoded=base64.b64encode(payload).decode("ascii")
width=(len(encoded)+21)//22
for i,part in enumerate(PARTS):
    part.write_text(encoded[i*width:(i+1)*width],encoding="ascii")
BRIDGE.write_text(bridge.replace('CORE_SHA256="'+old_sha+'"',
                                 'CORE_SHA256="'+new_sha+'"'),encoding="utf8")
print("V009_KRX_PARENTS_GMO_DEPTH_DNS_PATCH_PASS")
print("OLD_SHA256="+old_sha)
print("NEW_SHA256="+new_sha)
print("PATCHED_VENDOR_MODULES="+",".join(sorted(modified)))
