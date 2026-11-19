"""V083 native Upbit WS fix. Only d_upbit.py is modified."""
import ast,base64,hashlib,io,zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parts=[root/'app/src/main/assets/strategy_v070'/('part_%02d.txt'%i) for i in range(22)]
bridge=root/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
gradle=root/'app/build.gradle'
old=base64.b64decode(''.join(p.read_text('ascii') for p in parts))
old_sha=hashlib.sha256(old).hexdigest()
java=bridge.read_text()
assert java.count('CORE_SHA256="'+old_sha+'"')==1
with zipfile.ZipFile(io.BytesIO(old)) as z:
    assert z.testzip() is None
    infos=z.infolist()
    files={i.filename:z.read(i.filename) for i in infos}
assert len(files)==82
src=files['d_upbit.py'].decode('utf-8')
def once(a,b):
    global src
    assert src.count(a)==1,(src.count(a),a[:100])
    src=src.replace(a,b,1)
once("        self.ws_event_thread=None\n        self._ws_event_queue=queue.Queue(maxsize=4096)",
"""        self.ws_event_thread=None
        self._ws_event_queue=queue.Queue(maxsize=4096)
        self._v083_lock=threading.Lock()
        self._v083_sub_thread=None;self._v083_tick_thread=None
        self._v083_codes=frozenset();self._v083_bids=frozenset()
        self._v083_sub_updated=0.0
        self._v083_active_codes=frozenset();self._v083_active_bids=frozenset()
        self._v083_tick_seen={};self._v083_book_seen={}
        self._v083_tick_queue=queue.Queue(maxsize=2048)
        self._v083_tick_drops=0;self._v083_tick_late=0
        self._v083_tick_processed=0;self._v083_tick_max_lag_ms=0.0""")
once('    def _ws(self):\n        """Self-healing public WebSocket supervisor.',
'''    def _v083_refresh_subscriptions(self):
        # Ledger.watched_codes() uses SQLite. Keep it off recv/PING.
        while not self.stop_event.is_set():
            try:
                codes=frozenset(self.tracked)
                bids=frozenset(self._both_watched_codes())
                with self._v083_lock:
                    self._v083_codes=codes | bids
                    self._v083_bids=bids
                    self._v083_sub_updated=time.monotonic()
            except Exception as exc:
                self._ws_last_reason="SUB_REFRESH:"+type(exc).__name__
            self.stop_event.wait(1.5)

    def _v083_consume_ticks(self):
        # Original MA and Trend callbacks execute on a dedicated worker.
        # Drop delayed frames before they can become false fresh prints.
        while not self.stop_event.is_set():
            try:code,price,received=self._v083_tick_queue.get(timeout=.2)
            except queue.Empty:continue
            try:
                age=time.monotonic()-received
                self._v083_tick_max_lag_ms=max(self._v083_tick_max_lag_ms,age*1000)
                if age<0 or age>0.75:
                    self._v083_tick_late+=1
                    continue
                self.latest_prices[code]=str(price)
                self._v083_tick_processed+=1
            except Exception as exc:
                self._ws_last_reason="TICK_CALLBACK:"+type(exc).__name__
            finally:
                self._v083_tick_queue.task_done()

    def _ws(self):
        """Self-healing public WebSocket supervisor.''')
once("                with self._lock:codes=frozenset(self.tracked)\n                if not codes:\n                    self.stop_event.wait(2);continue\n                bids=frozenset(self._both_watched_codes())\n                codes=frozenset(codes | bids)",
"""                with self._v083_lock:
                    codes=self._v083_codes
                    bids=self._v083_bids
                if not codes:
                    self.stop_event.wait(.5);continue""")
once("                    subscribed_codes=codes;subscribed_bids=bids\n                    opened=time.monotonic();self._ws_connected=True;self._ws_last_frame=opened",
"""                    subscribed_codes=codes;subscribed_bids=bids
                    self._v083_active_codes=codes;self._v083_active_bids=bids
                    opened=time.monotonic();self._ws_connected=True;self._ws_last_frame=opened""")
once("                        with self._lock:next_codes=frozenset(self.tracked)\n                        next_bids=frozenset(self._both_watched_codes())\n                        next_codes=frozenset(next_codes | next_bids)",
"""                        with self._v083_lock:
                            next_codes=self._v083_codes
                            next_bids=self._v083_bids""")
once("                            subscribed_codes=next_codes;subscribed_bids=next_bids",
"""                            subscribed_codes=next_codes;subscribed_bids=next_bids
                            self._v083_active_codes=next_codes;self._v083_active_bids=next_bids""")
once("                                stamp=time.monotonic();self.latest_prices[code]=str(price)\n                                if code in self._held_codes and stamp-self._last_mark.get(code,0)>2.0:",
"""                                stamp=time.monotonic()
                                self._v083_tick_seen[code]=(stamp,timestamp)
                                try:self._v083_tick_queue.put_nowait((code,price,stamp))
                                except queue.Full:self._v083_tick_drops+=1
                                if code in self._held_codes and stamp-self._last_mark.get(code,0)>2.0:""")
once("                            with self._lock:self.last_book[code]=book\n                            self._enqueue_ws_event('book',book);self._ws_connected=True",
"""                            with self._lock:self.last_book[code]=book
                            self._v083_book_seen[code]=(time.monotonic(),timestamp)
                            self._enqueue_ws_event('book',book);self._ws_connected=True""")
once("        if self.ws_event_thread is None or not self.ws_event_thread.is_alive():\n            self.ws_event_thread=threading.Thread(target=self._ws_event_worker,",
"""        if self._v083_sub_thread is None or not self._v083_sub_thread.is_alive():
            self._v083_sub_thread=threading.Thread(target=self._v083_refresh_subscriptions,
                name='artitrade-v083-scope',daemon=True)
            self._v083_sub_thread.start()
        if self._v083_tick_thread is None or not self._v083_tick_thread.is_alive():
            self._v083_tick_thread=threading.Thread(target=self._v083_consume_ticks,
                name='artitrade-v083-tick-consumer',daemon=True)
            self._v083_tick_thread.start()
        if self.ws_event_thread is None or not self.ws_event_thread.is_alive():
            self.ws_event_thread=threading.Thread(target=self._ws_event_worker,""")
ast.parse(src)
assert 'self.latest_prices[code]=str(price)' in src
assert "ping_interval=20,ping_timeout=30" in src
assert "if typ=='ticker'" in src and "elif typ=='orderbook'" in src
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for info in infos:
        z.writestr(info,src.encode() if info.filename=='d_upbit.py' else files[info.filename])
new=out.getvalue()
with zipfile.ZipFile(io.BytesIO(new)) as z:
    assert z.testzip() is None
    for name in files:
        if name!='d_upbit.py':assert z.read(name)==files[name]
new_sha=hashlib.sha256(new).hexdigest()
b64=base64.b64encode(new).decode('ascii')
width=(len(b64)+21)//22
for i,p in enumerate(parts):p.write_text(b64[i*width:(i+1)*width])
bridge.write_text(java.replace('CORE_SHA256="'+old_sha+'"','CORE_SHA256="'+new_sha+'"'))
g=gradle.read_text()
assert g.count('versionCode 88')==1
assert g.count("versionName '0.7.82-LAZY-FILL-DETAILS'")==1
gradle.write_text(g.replace('versionCode 88','versionCode 89').replace(
    "versionName '0.7.82-LAZY-FILL-DETAILS'",
    "versionName '0.7.83-WS-RECEIVE-ISOLATION'"))
print('V083_SOURCE_PATCH_PASS',old_sha,new_sha)
