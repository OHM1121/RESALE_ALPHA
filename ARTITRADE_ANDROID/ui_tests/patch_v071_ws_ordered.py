"""Guarded v071 patch: live WebSocket starts before historical warmup; inbound 0B/0D
processing is ordered on a bounded, generation-scoped worker, never on recv/PING.
Every other original strategy module stays byte-identical. Never manufacture orders.
"""
from pathlib import Path
import ast, base64, hashlib, io, zipfile

ROOT=Path(__file__).resolve().parents[1]
PART_DIR=ROOT/"app/src/main/assets/strategy_v070"
BRIDGE=ROOT/"app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java"
GRADLE=ROOT/"app/build.gradle"
OLD_SHA="03a91207dde198685a0d44bcf33624c5a74ebd188c1643005043f535eca5c247"
parts=[PART_DIR/("part_%02d.txt"%i) for i in range(22)]
raw=base64.b64decode("".join(p.read_text("ascii") for p in parts))
assert hashlib.sha256(raw).hexdigest()==OLD_SHA, "Pinned original core mismatch"
with zipfile.ZipFile(io.BytesIO(raw)) as zin:
    infos=zin.infolist()
    original={x.filename:zin.read(x.filename) for x in infos}
assert len(original)>=80
source=original["d_live.py"].decode("utf8")
old_source=source

def rewrite_method(name, transformer):
    global source
    lines=source.splitlines(keepends=True)
    t=ast.parse(source)
    cls=next(x for x in t.body if isinstance(x,ast.ClassDef) and x.name=="LivePaperRunner")
    fn=next(x for x in cls.body if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef)) and x.name==name)
    original_method="".join(lines[fn.lineno-1:fn.end_lineno])
    changed=transformer(original_method)
    assert changed!=original_method, ("unchanged",name)
    lines[fn.lineno-1:fn.end_lineno]=changed.splitlines(keepends=True)
    source="".join(lines)
    ast.parse(source)

def one(old,new):
    def modify(s):
        assert s.count(old)==1,(old[:100],s.count(old))
        return s.replace(old,new)
    return modify

rewrite_method("__init__",one(
    "        self._tick_queue = queue.Queue(maxsize=3000)\n",
    "        self._tick_queue = queue.Queue(maxsize=3000)\n"
    "        # Only verified current-generation realtime packets can reach strategy workers.\n"
    "        self._feed_message_queue = queue.Queue(maxsize=4096)\n"
    "        self._feed_worker = None\n"
    "        self._feed_generation = 0\n"
    "        self._feed_processed = 0\n"
    "        self._feed_discarded = 0\n"
    "        self._feed_overflows = 0\n"
    "        self._feed_high_water = 0\n"
    "        self._feed_lag_max_ms = 0.0\n"
))
rewrite_method("_start_market_workers",one(
    "        self._market_workers_started = True\n",
    "        self._market_workers_started = True\n"
    "        self._feed_worker=threading.Thread(target=self._v071_feed_worker,daemon=True,\n"
    "                                           name='ARTITRADE_V071_WS_ORDERED_RECEIVER')\n"
    "        self._feed_worker.start()\n"
))

anchor="    async def _startup_preload(self):"
assert source.count(anchor)==1

new_methods="""    def _v071_enqueue_feed(self,kind,payload):
        # The socket receives/PINGs first, even during slow DB processing.
        entry=(self._feed_generation,kind,payload,time.monotonic(),time.time())
        try:self._feed_message_queue.put_nowait(entry)
        except queue.Full:
            self._feed_overflows+=1
            # Never pretend overflowed quotes are usable. Explicitly reconnect.
            raise RuntimeError('V071_STOCK_WS_QUEUE_BACKPRESSURE')
        self._feed_high_water=max(self._feed_high_water,self._feed_message_queue.qsize())

    def _v071_feed_worker(self):
        # One worker preserves the original book -> GMO -> 0B -> MA order
        # and makes all EXE/phone 3s execution freshness checks authoritative.
        while not self.stop_event.is_set():
            try:
                gen,kind,payload,arrival,epoch=self._feed_message_queue.get(timeout=.2)
            except queue.Empty:
                continue
            try:
                if gen!=self._feed_generation:
                    self._feed_discarded+=1
                    continue
                lag=max(0.0,time.monotonic()-arrival)
                self._feed_lag_max_ms=max(self._feed_lag_max_ms,lag*1000)
                # No buffered tick may become a newly timestamped fill.
                if lag>3.0:
                    self._feed_discarded+=1
                    continue
                if kind=='flush':
                    self._enqueue_flush(payload)
                    continue
                if kind!='raw':
                    continue
                msg=payload
                try:self.e_live.on_raw_message(msg,self.market_map)
                except Exception as exc:self.store.write_runtime_error(exc,'e_live_observe')
                self.engine.book.on_message(msg,self.nxt_snapshot,self.market_map)
                if msg.get('trnm')=='REAL':
                    try:self.gmo.on_book_message(msg,self.market_map)
                    except Exception as exc:self.store.write_runtime_error(exc,'gmo_readonly_book')
                # Use the actual reception time, not delayed worker time.
                received_at=datetime.fromtimestamp(epoch,KST)
                ticks=parse_venue_real(msg,self.market_map,seen_at=received_at,
                    nxt_snapshot=self.nxt_snapshot,
                    gmo_priority_codes=self.gmo.priority_watchlist(received_at))
                if ticks and gen==self._feed_generation:
                    self._watch_feed_last_epoch=epoch
                    if not self._confirmed_ws_session:
                        self._confirmed_ws_session=True
                        self._health('실시간 연결됨','키움 WS 실체결 수신 확인 · 종목별 0B 유효성 개별 검증')
                        self._progress('실시간 수신 확인',1,1,'키움 공식 0B 수신 확인',state='complete')
                for tick in ticks:
                    if gen!=self._feed_generation:break
                    self._new_tick(tick)
                self._feed_processed+=1
            except Exception as exc:
                # Never suppress an actual order/quote-processing failure.
                try:self.store.write_runtime_error(exc,'v071_stock_feed_worker')
                except Exception:pass
            finally:
                self._feed_message_queue.task_done()

    async def _v071_background_preload(self):
        # The scanner already rejects each symbol with unverifiable candles.
        # Warmup must not own the WebSocket heartbeat or delay its first LOGIN.
        try:
            await asyncio.to_thread(lambda: asyncio.run(self._warmup()))
            if self.stop_event.is_set():return
            def assess():
                now=now_kst()
                codes=list(self.market_map)
                critical=set(self._pinned_stocks())
                total=max(1,len(codes)*4)
                covered=0
                critical_missing=[]
                for code in codes:
                    market=self.market_map[code][0]
                    try:self._derive_higher_from_5m(code,market,now)
                    except Exception:pass
                    for frame in ('5m','30m','60m','day'):
                        try:ready=self._has_usable_history(code,frame,now)
                        except Exception:ready=False
                        if ready:covered+=1
                        elif code in critical:critical_missing.append(code+'/'+frame)
                return covered,total,critical_missing
            covered,total,missing=await asyncio.to_thread(assess)
            if self.stop_event.is_set():return
            coverage=covered/total
            degraded=coverage<.90 or bool(missing)
            detail=(f'실시간 WS 선연결 · 공식 과거봉 {covered}/{total} ({coverage*100:.1f}%)'
                    +(' · 중요종목 미완료 '+str(len(missing)) if missing else ''))
            self.preload_gate.finish(detail,coverage=coverage,degraded=degraded)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.preload_gate.fail_open_degraded(
                '실시간 WS 유지 · 역사봉 복구 실패 · 미검증 종목은 매매 차단',str(exc)[:180])
            try:self.store.issue('V071_BACKGROUND_HISTORY_FAILED',
                '실시간 수신과 독립된 과거봉 준비 실패 · 종목별 검증 유지',
                details={'error':type(exc).__name__})
            except Exception:pass

    async def _v071_subscription_maintenance(self,ws,switch,allow_repair):
        if switch:await self._switch_venue_subscriptions(ws)
        if allow_repair:
            await self._refresh_universe_if_due(ws)
            await self._repair_gmo_depth_on_existing_ws(ws)

"""
source=source.replace(anchor,new_methods+anchor)

def truncate_startup(s):
    anchor="        budget=max(30,min(120,int(os.environ.get('ARTITRADE_STOCK_PRELOAD_SECONDS','90'))))\n"
    assert s.count(anchor)==1
    tail="""        # Phase 1 is DB integrity + universe only. Do not await 800 REST/SQLite
        # checks before starting Kiwoom 0B/0D WebSocket subscriptions.
        # All missing per-symbol bars stay unverified until official retrieval.
        gate.finish('WS 선연결 · 과거봉은 독립 작업자에서 준비 중',
                    coverage=0.0,degraded=True)
        self._progress('실시간 연결 우선',1,1,
                       '키움 0B/0D 연결 먼저 · 불완전 시간봉은 매매 금지',state='waiting')
        return True
"""
    return s[:s.index(anchor)]+tail
rewrite_method("_startup_preload",truncate_startup)
rewrite_method("run",one(
    "        warmup_key = (_pre_now.strftime('%Y%m%d'), 'NXT' if live_session(_pre_now, 'NXT') else 'KRX')\n",
    "        warmup_key = (_pre_now.strftime('%Y%m%d'), 'NXT' if live_session(_pre_now, 'NXT') else 'KRX')\n"
    "        last_repair_task = None\n"))
rewrite_method("run",one(
    "            connected_at = None\n            fully_registered = False\n            try:\n",
    "            connected_at = None\n            fully_registered = False\n"
    "            maintenance_task = None\n"
    "            self._feed_generation += 1\n"
    "            try:\n"))
rewrite_method("run",one(
    "                    pending_check = guard_stored_pending(self.engine, reason='WS_RECONNECT')\n",
    "                    pending_check = await asyncio.to_thread(\n"
    "                        guard_stored_pending,self.engine,reason='WS_RECONNECT')\n"))
rewrite_method("run",one(
    "                    frames_to_check = guard_existing_higher_pending(self.engine, reason='WS_RECONNECT')\n",
    "                    frames_to_check = await asyncio.to_thread(\n"
    "                        guard_existing_higher_pending,self.engine,reason='WS_RECONNECT')\n"))
rewrite_method("run",one(
    "                    await self._register_subscriptions(ws)\n",
    "                    await self._register_subscriptions(ws)\n"
    "                    # Start official-history worker asynchronously after first\n"
    "                    # successful authenticated websocket LOGIN/REG request.\n"
    "                    if warmup_task is None or warmup_task.done():\n"
    "                        warmup_task=asyncio.create_task(self._v071_background_preload())\n"
))
rewrite_method("run",one(
    "                    self._queue_boot_historical_frame_recheck(now_kst())\n",
    "                    asyncio.create_task(asyncio.to_thread(\n"
    "                        self._queue_boot_historical_frame_recheck,now_kst()))\n"))
rewrite_method("run",one(
    "                    last_session_cutover=''\n",
    "                    last_session_cutover=''\n"
    "                    last_maintenance_launch=0.0\n"
    "                    last_flush_cutoff=''\n"
    "                    last_repair_cutoff=''\n"))
rewrite_method("run",one(
    "                        self._enqueue_flush(cutoff)\n                        self._schedule_closed_frame_repair(now)\n",
    "                        # Emit ordered minute-boundary jobs; never scan SQLite\n"
    "                        # or 200 symbols on the PING/recv loop.\n"
    "                        if cutoff!=last_flush_cutoff:\n"
    "                            self._v071_enqueue_feed('flush',cutoff)\n"
    "                            last_flush_cutoff=cutoff\n"
    "                        if cutoff!=last_repair_cutoff:\n"
    "                            last_repair_cutoff=cutoff\n"
    "                            if last_repair_task is None or last_repair_task.done():\n"
    "                                last_repair_task=asyncio.create_task(asyncio.to_thread(\n"
    "                                    self._schedule_closed_frame_repair,now))\n"))
def change_maintenance(s):
    old="""                        if mono-last_subscription_check>=4.0 or (cutover and cutover!=last_session_cutover):
                            last_subscription_check=mono
                            if cutover:last_session_cutover=cutover
                            await self._switch_venue_subscriptions(ws)
                        if not '1530' <= now.strftime('%H%M') < '1600':
                            await self._refresh_universe_if_due(ws)
                            await self._repair_gmo_depth_on_existing_ws(ws)
"""
    new="""                        if maintenance_task is not None and maintenance_task.done():
                            maintenance_task.result()  # Propagate genuine auth/REG failure
                            maintenance_task=None
                        switch=mono-last_subscription_check>=4.0 or (cutover and cutover!=last_session_cutover)
                        if switch:
                            last_subscription_check=mono
                            if cutover:last_session_cutover=cutover
                        if maintenance_task is None and (switch or mono-last_maintenance_launch>=4.0):
                            last_maintenance_launch=mono
                            maintenance_task=asyncio.create_task(
                                self._v071_subscription_maintenance(ws,switch,
                                    not '1530' <= now.strftime('%H%M') < '1600'))
"""
    assert s.count(old)==1, ("old maintenance count",s.count(old))
    return s.replace(old,new)
rewrite_method("run",change_maintenance)

def move_receive_work(s):
    a="                        # Parallel lightweight observed-data capture for E.\n"
    b="                        if self._confirmed_ws_session:\n"
    assert s.count(a)==1 and s.count(b)==1
    start=s.index(a);end=s.index(b,start)
    new="""                        # One bounded FIFO worker owns real 0D -> GMO -> 0B -> MA.
                        # The recv loop never awaits DB, broker research or strategy IO.
                        self._v071_enqueue_feed('raw',msg)
                        if not self._confirmed_ws_session and fully_registered:
                            self._confirmed_ws_session=True
                            self._health('실시간 구독 승인','키움 REG 확인 · 실제 0B 시세 수신은 별도')
                            self._progress('실시간 구독 승인',1,1,
                                           '구독 승인 · 종목별 실체결 수신 확인 대기',state='waiting')
"""
    s=s[:start]+new+s[end:]
    assert s.count("await asyncio.to_thread(self._new_tick, tick)")==0
    return s
rewrite_method("run",move_receive_work)
rewrite_method("run",one(
    "warmup_task = asyncio.create_task(self._warmup())",
    "warmup_task = asyncio.create_task(self._v071_background_preload())"
))
rewrite_method("run",one(
    "            except Exception as exc:\n                pause = active and '1530' <= now_kst().strftime('%H%M') < '1600'\n",
    "            except Exception as exc:\n"
    "                self._feed_generation += 1\n"
    "                self._confirmed_ws_session=False\n"
    "                if maintenance_task is not None and not maintenance_task.done():\n"
    "                    maintenance_task.cancel()\n"
    "                pause = active and '1530' <= now_kst().strftime('%H%M') < '1600'\n"
))
source=source.replace(
    "        if not self._confirmed_ws_session and (fully_registered or bool(observed_ticks)):", 
    "        if not self._confirmed_ws_session and fully_registered:"
)
# The above line should now live exclusively in the historical substring, not
# in our patched receive path; there must be no old observed_ticks reference.
assert "observed_ticks" not in source[source.index("    async def run(self):"):]
ast.parse(source)
compile(source,"d_live.py","exec")
assert len(source)>len(old_source)
assert source.count("await self._startup_preload()")==1
assert source.count("self._v071_enqueue_feed('raw',msg)")==1
assert "ping_timeout=45" in source
assert "self._v071_background_preload()" in source
assert 'await asyncio.to_thread(self.engine.book.on_message' not in source

out=io.BytesIO()
with zipfile.ZipFile(out,"w") as zout:
    for item in infos:
        zout.writestr(item,source.encode("utf8") if item.filename=="d_live.py" else original[item.filename])
changed=out.getvalue()
with zipfile.ZipFile(io.BytesIO(changed)) as z:
    for name,content in original.items():
        if name!="d_live.py":assert z.read(name)==content, ("Non-live policy changed",name)
    assert z.read("d_live.py").decode("utf8")==source
sha=hashlib.sha256(changed).hexdigest()
encoded=base64.b64encode(changed).decode("ascii")
width=(len(encoded)+21)//22
for i,f in enumerate(parts):f.write_text(encoded[i*width:(i+1)*width],encoding="ascii")
bridge=BRIDGE.read_text("utf8")
assert bridge.count('CORE_SHA256="'+OLD_SHA+'"')==1
BRIDGE.write_text(bridge.replace('CORE_SHA256="'+OLD_SHA+'"','CORE_SHA256="'+sha+'"'),encoding="utf8")
gradle=GRADLE.read_text("utf8")
assert gradle.count("versionCode 76")==1
assert gradle.count("versionName '0.7.70-COIN-TREND-WS-VERIFIED'")==1
GRADLE.write_text(gradle.replace("versionCode 76","versionCode 77")
    .replace("versionName '0.7.70-COIN-TREND-WS-VERIFIED'",
             "versionName '0.7.71-WS-ORDERED-RECV-HISTORY'"),encoding="utf8")
print("V071_CHANGED_D_LIVE_SHA256",hashlib.sha256(source.encode('utf8')).hexdigest())
print("V071_CORE_SHA256",sha)
print("V071_NON_LIVE_MODULES_BYTE_IDENTICAL",len(original)-1)
print("V071_NO_SLOW_CANDLE_HISTORY_BEFORE_SOCKET")
print("V071_ORDERED_RECEIVE_WORKER_WITH_REAL_RECEIVE_TIMESTAMP")
print("V071_NO_RETROACTIVE_STALE_ORDER")
