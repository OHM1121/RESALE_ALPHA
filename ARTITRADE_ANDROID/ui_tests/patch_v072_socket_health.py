"""Guarded v072 core surgery: no-trade is NOT a broken socket.
Repair success REG replies without grp_no (official reply may omit it).
Only d_live.py inside embedded 82-module original core may differ.
"""
from pathlib import Path
import ast,base64,hashlib,io,zipfile

ROOT=Path(__file__).resolve().parents[1]
PARTS=[ROOT/'app/src/main/assets/strategy_v070'/('part_%02d.txt'%i) for i in range(22)]
BRIDGE=ROOT/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
GRADLE=ROOT/'app/build.gradle'
OLD_SHA='83d0f0baa454f85e7e0de06274a0a54f49ccacd67c863afb1d2855a6310cb987'
raw=base64.b64decode(''.join(p.read_text('ascii') for p in PARTS))
assert hashlib.sha256(raw).hexdigest()==OLD_SHA,'BASELINE_CORE_SHA_CHANGED'
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    entries=z.infolist()
    original={v.filename:z.read(v.filename) for v in entries}
assert len(original)==82
source=original['d_live.py'].decode('utf-8')

def once(before,after):
    global source
    n=source.count(before)
    if n!=1:raise AssertionError('PATCH_ANCHOR_COUNT_'+str(n)+': '+before[:120])
    source=source.replace(before,after,1)

# The native websocket ping_interval and ping_timeout detect a broken TCP/
# websocket transport. 0B trade silence on illiquid stocks proves nothing.
# This removes the 45s price-silence RECONNECT but preserves read-only age.
once(
"""                        # REG success alone cannot prove a working 0B feed.
                        # If 200 subscribed stocks produce no VALID 0B for
                        # 45s during KRX, refresh ONLY the single socket.
                        # Outside the liquid regular session, do not force
                        # pointless reconnects on genuinely quiet markets.
                        if '0901' <= now.strftime('%H%M') < '1529':
                            last=max(float(getattr(self,'_watch_feed_last_epoch',0) or 0),
                                     float(getattr(self,'_v071_0b_watch_since',0) or 0))
                            if time.time()-last>45.0:
                                self.store.issue('V071_0B_STREAM_SILENT',
                                    'KRX 실체결 0B 45초 미수신 · 동일 200종목 재연결 대기',
                                    key='v071-no-0b:'+now.strftime('%Y%m%d%H%M'),
                                    details={'last_0b_age_sec':round(time.time()-last,1),
                                             'queue_depth':self._feed_message_queue.qsize(),
                                             'reg_ack_seen':bool(self._confirmed_ws_session)})
                                raise RuntimeError('V071_KRX_0B_SILENT_45S_RECONNECT')
""",
"""                        # No 0B may legitimately arrive for a quiet symbol/session.
                        # Only transport close, ping/pong failure, explicit broker
                        # rejection, or receive-queue integrity failures reconnect.
                        # _watch_feed_last_epoch is diagnostic, not a health gate.
"""
)
# Kiwoom's successful REG reply may lack grp_no. The previous implementation
# counted ONLY group-labelled replies and never confirmed the initial
# authenticated 200-symbol subscription in that documented case.
once(
"""                            # A successful index REG (group 3) is NOT a stock-market REG.
                            # Count only unique acknowledgements for subscribed stock groups.
                            ack_group = str(msg.get('grp_no', ''))
                            if ack_group in self._subscribed_groups and self._subscribed_groups[ack_group]:
                                seen_reg_groups.add(ack_group)
                            seen_reg_acks = len(seen_reg_groups)
                            if expected_reg_acks > 0 and seen_reg_acks >= expected_reg_acks:
                                fully_registered = True
""",
"""                            # Explicit grp_no is preferred. Official success
                            # replies may omit it: count each in-order, positive
                            # REG response against the finite initial D groups.
                            # Group 3 (index E) never confirms a stock group.
                            ack_group = str(msg.get('grp_no') or '')
                            if ack_group in self._subscribed_groups and self._subscribed_groups[ack_group]:
                                seen_reg_groups.add(ack_group)
                                seen_reg_acks=max(seen_reg_acks,len(seen_reg_groups))
                            elif not ack_group and seen_reg_acks < expected_reg_acks:
                                seen_reg_acks+=1
                            if expected_reg_acks > 0 and seen_reg_acks >= expected_reg_acks:
                                fully_registered = True
"""
)
ast.parse(source)
compile(source,'d_live.py','exec')
assert "V071_KRX_0B_SILENT_45S_RECONNECT" not in source
assert 'ping_interval=20, ping_timeout=45' in source
assert "self._v071_enqueue_feed('raw',msg)" in source
assert "fully_registered = True" in source
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for item in entries:
        z.writestr(item,source.encode('utf-8') if item.filename=='d_live.py' else original[item.filename])
updated=out.getvalue()
with zipfile.ZipFile(io.BytesIO(updated)) as z:
    for name,blob in original.items():
        if name!='d_live.py':assert z.read(name)==blob,name
new_sha=hashlib.sha256(updated).hexdigest()
b64=base64.b64encode(updated).decode('ascii')
width=(len(b64)+21)//22
for i,p in enumerate(PARTS):
    p.write_text(b64[i*width:(i+1)*width],encoding='ascii')
j=BRIDGE.read_text('utf-8')
assert j.count('CORE_SHA256="'+OLD_SHA+'"')==1
BRIDGE.write_text(j.replace('CORE_SHA256="'+OLD_SHA+'"','CORE_SHA256="'+new_sha+'"'),encoding='utf-8')
g=GRADLE.read_text('utf-8')
assert g.count('versionCode 77')==1
assert g.count("versionName '0.7.71-WS-ORDERED-RECV-HISTORY'")==1
GRADLE.write_text(g.replace('versionCode 77','versionCode 78')
                   .replace("versionName '0.7.71-WS-ORDERED-RECV-HISTORY'",
                            "versionName '0.7.72-VERIFIED-STOCK-QUOTE'"),encoding='utf-8')
print('NEW_CORE_SHA',new_sha)
print('NON_D_LIVE_MODULES_IDENTICAL',len(original)-1)
print('REG_ANONYMOUS_ACK_SUPPORTED')
print('ZERO_0B_IS_NOT_CONNECTION_FAILURE')
print('KEEPALIVE_PING_PONG_AND_ORIGINAL_ORDERS_UNCHANGED')
