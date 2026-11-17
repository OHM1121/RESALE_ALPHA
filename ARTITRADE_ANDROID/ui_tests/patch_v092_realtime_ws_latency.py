"""v092 surgical quote transport patch, based on source-reviewed vendor protocols.

Kiwoom official ws_client.py: echo app-level PING while awaiting LOGIN ACK.
Upbit official websocket-orderbook: KRW-BTC.15 limits payload to 15 levels.
The orderbook execution owner already uses units[:15]; keep real exchange
timestamps, three-second quote age, existing order ownership and PING/PONG.
UI change is DISPLAY ONLY: remove unnecessary 22s post-connected cover wait.
"""
import ast
import base64
import hashlib
import io
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PARTS=[ROOT/'app/src/main/assets/strategy_v070'/f'part_{i:02d}.txt'
       for i in range(22)]
BRIDGE=ROOT/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
GRADLE=ROOT/'app/build.gradle'
UI=ROOT/'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java'

encoded=''.join(p.read_text(encoding='ascii') for p in PARTS)
original=base64.b64decode(encoded)
old_sha=hashlib.sha256(original).hexdigest()
bridge_src=BRIDGE.read_text(encoding='utf8')
assert bridge_src.count('CORE_SHA256="'+old_sha+'"')==1,'EXACT_ORIGINAL_CORE_REQUIRED'
with zipfile.ZipFile(io.BytesIO(original)) as z:
    infos=z.infolist()
    assert z.testzip() is None and len(infos)==82
    blobs={info.filename:z.read(info.filename) for info in infos}

stock=blobs['d_live.py'].decode('utf8')
coin=blobs['d_upbit.py'].decode('utf8')

before="                    ack = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))"
after="                    ack = await _v092_read_login_ack(ws, timeout=15)"
assert stock.count(before)==1
stock=stock.replace(before,after,1)
# Called by the original one authenticated Kiwoom socket, no parallel login.
# Official Kiwoom-Securities/Kiwoom-REST-API ws_client.py waits for login
# while echoing server PING frames and refusing unrelated messages.
helper=r'''

async def _v092_read_login_ack(ws, timeout=15):
    """Handle vendor application PING while waiting for LOGIN only.

    A single absolute monotonic deadline bounds the handshake even if
    the server sends continuous PINGs. A REG/REAL/unknown packet cannot
    impersonate a LOGIN acknowledgment. No order/price is touched.
    """
    deadline=time.monotonic()+timeout
    while True:
        left=deadline-time.monotonic()
        if left<=0:
            raise asyncio.TimeoutError('KIWOOM_WS_LOGIN_ACK_TIMEOUT')
        raw=await asyncio.wait_for(ws.recv(),timeout=left)
        if isinstance(raw,bytes):
            raw=raw.decode('utf-8')
        if not isinstance(raw,str):
            raise RuntimeError('KIWOOM_WS_LOGIN_NON_TEXT')
        try:
            msg=json.loads(raw)
        except (TypeError,ValueError):
            if raw.strip().upper()=='PING':
                await ws.send('PING')
                continue
            raise RuntimeError('KIWOOM_WS_LOGIN_MALFORMED_PACKET')
        if msg=='PING':
            await ws.send('PING')
            continue
        if isinstance(msg,dict) and str(msg.get('trnm','')).upper()=='PING':
            await ws.send(raw)
            continue
        if not isinstance(msg,dict) or msg.get('trnm')!='LOGIN':
            raise RuntimeError('KIWOOM_WS_LOGIN_ACK_UNEXPECTED_FRAME')
        return msg
'''
assert '_v092_read_login_ack' not in blobs['d_live.py'].decode()
stock+=helper
assert stock.count('async def _v092_read_login_ack(')==1
assert stock.count('async with websockets.connect(')==1
assert "ping_interval=20, ping_timeout=45" in stock

old="                    if bids_now:msg.append({'type':'orderbook','codes':sorted(bids_now),'is_only_realtime':True})"
new="""                    if bids_now:
                        # Official Upbit 'KRW-BTC.15': 15 genuine best levels
                        # rather than its default 30. Existing matching uses
                        # top 1 for executable depth and at most 15 for SR.
                        # Keep ticker and held-position symbol IDs untouched.
                        depth_codes=sorted(code+'.15' if code.startswith('KRW-') else code
                                           for code in bids_now)
                        msg.append({'type':'orderbook','codes':depth_codes,'is_only_realtime':True})"""
assert coin.count(old)==1
coin=coin.replace(old,new,1)
assert "ping_interval=20,ping_timeout=30" in coin
assert "trade_stamp=item.get('trade_timestamp')" in coin
assert "abs(time.time()-timestamp/1000)>MAX_BOOK_AGE_SEC" in coin

modified={'d_live.py':stock.encode('utf8'),'d_upbit.py':coin.encode('utf8')}
for name,blob in modified.items():
    ast.parse(blob.decode('utf8'),filename=name)
with zipfile.ZipFile(io.BytesIO(original)) as z:
    for name in z.namelist():
        if name.endswith('.py') and name not in modified:
            ast.parse(z.read(name).decode('utf8'),filename=name)
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for info in infos:
        z.writestr(info,modified.get(info.filename,blobs[info.filename]))
result=out.getvalue()
with zipfile.ZipFile(io.BytesIO(result)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    unchanged=[i.filename for i in infos if i.filename not in modified]
    for name in unchanged:
        assert z.read(name)==blobs[name],name
    assert len(unchanged)==80
new_sha=hashlib.sha256(result).hexdigest()

ui=UI.read_text(encoding='utf8')
assert ui.count('PRELOAD_BOTH_FEEDS_WARM_MIN_MS=22_000L')==1
assert 'if(engine&&uiReady&&stockReady&&coinReady)' in ui
assert 'PRELOAD_WATCHDOG_MS=40_000L' in ui
ui=ui.replace('PRELOAD_BOTH_FEEDS_WARM_MIN_MS=22_000L',
              'PRELOAD_BOTH_FEEDS_WARM_MIN_MS=8_000L')
g=GRADLE.read_text(encoding='utf8')
assert g.count('versionCode 97')==1
assert g.count("versionName '0.7.91-MA-STAGE-LEDGER'")==1
g=g.replace('versionCode 97','versionCode 98').replace(
    "versionName '0.7.91-MA-STAGE-LEDGER'",
    "versionName '0.7.92-QUOTE-WS-LATENCY'")
new_encoded=base64.b64encode(result).decode('ascii')
width=(len(new_encoded)+21)//22
for i,p in enumerate(PARTS):
    p.write_text(new_encoded[i*width:(i+1)*width],encoding='ascii')
BRIDGE.write_text(bridge_src.replace('CORE_SHA256="'+old_sha+'"',
                                     'CORE_SHA256="'+new_sha+'"'),encoding='utf8')
UI.write_text(ui,encoding='utf8')
GRADLE.write_text(g,encoding='utf8')
print('V092_QUOTE_PATCH_PASS')
print('OLD_SHA256='+old_sha)
print('NEW_SHA256='+new_sha)
print('UNCHANGED_ORIGINAL_PYTHON_MODULES=80')
print('KIWOOM_PING_LOGIN_ABSOLUTE_TIMEOUT=15')
print('UPBIT_BOOK_LEVELS=15')
print('UI_VERIFIED_FEEDS_HISTORY_GRACE_MS=8000')
print('APP_CODE=98')
