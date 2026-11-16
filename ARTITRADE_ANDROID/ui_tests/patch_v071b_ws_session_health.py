"""Follow-up to pinned v071: propagate REG-confirmed socket lifecycle to CW
and reconnect read-only when *no* genuine 0B trades arrive for 45s in KRX.
Does not extend executable tick/book freshness or make any broker order.
"""
from pathlib import Path
import ast,base64,hashlib,io,zipfile
root=Path(__file__).resolve().parents[1]
parts=[root/"app/src/main/assets/strategy_v070"/("part_%02d.txt"%i) for i in range(22)]
bridge=root/"app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java"
OLD="72d72b81541f313b50f0124ff3cc9321a5fe93f6f21f6b90738b55f34689a5e8"
raw=base64.b64decode("".join(p.read_text('ascii') for p in parts))
assert hashlib.sha256(raw).hexdigest()==OLD
with zipfile.ZipFile(io.BytesIO(raw)) as zin:
    infos=zin.infolist()
    originals={x.filename:zin.read(x.filename) for x in infos}
source=originals["d_live.py"].decode('utf8')
original_source=source
def once(old,new):
    global source
    assert source.count(old)==1,(old[:125],source.count(old))
    source=source.replace(old,new,1)

once(
    "                    connected_at = time.monotonic()\n"
    "                    # A prior socket's REG confirmation cannot validate this socket.\n",
    "                    connected_at = time.monotonic()\n"
    "                    # Fresh packet count begins ONLY with current socket; old\n"
    "                    # pre-disconnect 0B records are never proof of recovery.\n"
    "                    self._v071_0b_watch_since=time.time()\n"
    "                    # A prior socket's REG confirmation cannot validate this socket.\n")

once(
    "                            self._health('실시간 구독 승인','키움 WS 등록 승인 · 실제 시세는 개별 종목별 확인')\n",
    "                            # CW's shared transport recognizes exactly\n"
    "                            # '실시간 연결됨'; its broker-frame gate remains <=3s.\n"
    "                            self._health('실시간 연결됨','키움 REG 승인 · 0B 실체결은 종목별 독립 확인')\n")

once(
    "                        if now.strftime('%H%M') >= '2005':\n                            break\n",
    "                        if now.strftime('%H%M') >= '2005':\n                            break\n"
    "                        # REG success alone cannot prove a working 0B feed.\n"
    "                        # If 200 subscribed stocks produce no VALID 0B for\n"
    "                        # 45s during KRX, refresh ONLY the single socket.\n"
    "                        # Outside the liquid regular session, do not force\n"
    "                        # pointless reconnects on genuinely quiet markets.\n"
    "                        if '0901' <= now.strftime('%H%M') < '1529':\n"
    "                            last=max(float(getattr(self,'_watch_feed_last_epoch',0) or 0),\n"
    "                                     float(getattr(self,'_v071_0b_watch_since',0) or 0))\n"
    "                            if time.time()-last>45.0:\n"
    "                                self.store.issue('V071_0B_STREAM_SILENT',\n"
    "                                    'KRX 실체결 0B 45초 미수신 · 동일 200종목 재연결 대기',\n"
    "                                    key='v071-no-0b:'+now.strftime('%Y%m%d%H%M'),\n"
    "                                    details={'last_0b_age_sec':round(time.time()-last,1),\n"
    "                                             'queue_depth':self._feed_message_queue.qsize(),\n"
    "                                             'reg_ack_seen':bool(self._confirmed_ws_session)})\n"
    "                                raise RuntimeError('V071_KRX_0B_SILENT_45S_RECONNECT')\n")

tree=ast.parse(source)
compile(source,"d_live.py","exec")
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="LivePaperRunner")
assert any(isinstance(n,ast.AsyncFunctionDef) and n.name=="run" for n in cls.body)
assert source!=original_source
assert "self._health('실시간 연결됨','키움 REG 승인" in source
assert "V071_KRX_0B_SILENT_45S_RECONNECT" in source
assert "valid_broker_frame" not in source # CW still owns it in unchanged at_close_bridge.py
out=io.BytesIO()
with zipfile.ZipFile(out,"w") as z:
    for item in infos:
        z.writestr(item,source.encode('utf8') if item.filename=='d_live.py' else originals[item.filename])
new=out.getvalue()
with zipfile.ZipFile(io.BytesIO(new)) as z:
    for name,oldbytes in originals.items():
        if name!='d_live.py':assert z.read(name)==oldbytes,name
sha=hashlib.sha256(new).hexdigest()
b64=base64.b64encode(new).decode('ascii')
width=(len(b64)+21)//22
for i,p in enumerate(parts):p.write_text(b64[i*width:(i+1)*width],encoding='ascii')
j=bridge.read_text('utf8')
assert j.count('CORE_SHA256="'+OLD+'"')==1
bridge.write_text(j.replace('CORE_SHA256="'+OLD+'"','CORE_SHA256="'+sha+'"'),encoding='utf8')
print('V071b_NEW_SHA',sha)
print('V071b_OTHER_MODULES_IDENTICAL',len(originals)-1)
print('V071b_BROKER_FRAME_FRESHNESS_UNCHANGED')
