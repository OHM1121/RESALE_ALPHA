"""ARTITRADE v0.7.84: preserve 0.75s cutoff, coalesce genuine WS ticks per symbol.
Patch ONLY original d_upbit.py inside the pinned 82-file archive, and read-only
native diagnostics in android_runtime_v078.py. Do not change order policy/DB.
"""
import ast,base64,hashlib,io,zipfile
from pathlib import Path

root=Path(__file__).resolve().parents[1]
main=root/'app/src/main'
parts=[main/'assets/strategy_v070'/f'part_{i:02d}.txt' for i in range(22)]
bridge=main/'java/com/artitrade/mobile/StrategyRuntimeBridge.java'
gradle=root/'app/build.gradle'
overlay=main/'python/android_runtime_v078.py'
old=base64.b64decode(''.join(x.read_text('ascii') for x in parts))
old_sha=hashlib.sha256(old).hexdigest()
assert old_sha=='a9e98920206e36aef40e05b20aa66f49ce07cbd4df8cc4119d27928e144e1ffd',old_sha
java=bridge.read_text()
assert java.count('CORE_SHA256="'+old_sha+'"')==1
with zipfile.ZipFile(io.BytesIO(old)) as z:
    assert z.testzip() is None
    infos=z.infolist()
    files={x.filename:z.read(x.filename) for x in infos}
assert len(files)==82
src=files['d_upbit.py'].decode('utf-8')

def once(a,b):
    global src
    assert src.count(a)==1,(src.count(a),a[:100])
    src=src.replace(a,b,1)

once('        self._v083_tick_processed=0;self._v083_tick_max_lag_ms=0.0',
'''        self._v083_tick_processed=0;self._v083_tick_max_lag_ms=0.0
        # Genuine WS tick mailbox: at most ONE unconsumed queue token per symbol.
        # A newer frame replaces pending old frames; it is never a fabricated price.
        self._v084_tick_lock=threading.Lock()
        self._v084_tick_pending={}
        self._v084_tick_delivered={}
        self._v084_tick_counts={}
        self._v084_book_counts={}
        self._v084_tick_received=0
        self._v084_tick_coalesced=0
        self._v084_book_received=0
        self._v084_sub_sends=0''')

old_worker='''    def _v083_consume_ticks(self):
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
'''
new_worker='''    def _v084_accept_tick(self,code,price,received,exchange_timestamp):
        # Called ONLY for an authenticated, timestamp-checked WS ticker frame.
        # Coalescing removes obsolete queue backlog, not the 10s RSI or 3s book gate.
        with self._v084_tick_lock:
            self._v083_tick_seen[code]=(received,exchange_timestamp)
            self._v084_tick_counts[code]=self._v084_tick_counts.get(code,0)+1
            self._v084_tick_received+=1
            pending=code in self._v084_tick_pending
            self._v084_tick_pending[code]=(price,received)
            if pending:
                self._v084_tick_coalesced+=1
            else:
                try:self._v083_tick_queue.put_nowait(code)
                except queue.Full:
                    # Never leave a code permanently pending without a token.
                    self._v084_tick_pending.pop(code,None)
                    self._v083_tick_drops+=1

    def _v083_consume_ticks(self):
        # Dedicated MA/Trend callback worker. Only most recent genuine frame
        # per code can occupy the queue; stale data remain prohibited.
        while not self.stop_event.is_set():
            try:code=self._v083_tick_queue.get(timeout=.2)
            except queue.Empty:continue
            try:
                with self._v084_tick_lock:
                    payload=self._v084_tick_pending.pop(code,None)
                if payload is None:continue
                price,received=payload
                age=time.monotonic()-received
                self._v083_tick_max_lag_ms=max(self._v083_tick_max_lag_ms,age*1000)
                if age<0 or age>0.75:
                    self._v083_tick_late+=1
                    continue
                # Existing _PriceMemory fires the ORIGINAL strategy observer.
                self.latest_prices[code]=str(price)
                self._v083_tick_processed+=1
                with self._v084_tick_lock:
                    self._v084_tick_delivered[code]=(received,time.monotonic())
            except Exception as exc:
                self._ws_last_reason="TICK_CALLBACK:"+type(exc).__name__
            finally:
                self._v083_tick_queue.task_done()

    def _v084_ws_health(self,trend_codes=()):
        # Read-only in-memory diagnostic, no SQLite and no price substitution.
        now=time.monotonic()
        with self._v083_lock:
            requested=set(self._v083_codes)
            requested_books=set(self._v083_bids)
            refresh_age=(now-self._v083_sub_updated if self._v083_sub_updated else None)
        active=set(self._v083_active_codes)
        active_books=set(self._v083_active_bids)
        with self._v084_tick_lock:
            ticks=dict(self._v083_tick_seen)
            books=dict(self._v083_book_seen)
            delivered=dict(self._v084_tick_delivered)
            tick_counts=dict(self._v084_tick_counts)
            book_counts=dict(self._v084_book_counts)
            pending=len(self._v084_tick_pending)
            received=self._v084_tick_received
            coalesced=self._v084_tick_coalesced
            book_total=self._v084_book_received
        codes=sorted(set(trend_codes or ()))
        def age(record):
            if not record:return None
            return round(max(0.0,now-record[0]),3)
        symbols=[]
        for code in codes:
            symbols.append({
                'code':code,
                'ticker_requested':code in requested,
                'book_requested':code in requested_books,
                'ticker_submitted':code in active,
                'book_submitted':code in active_books,
                'ticker_age_sec':age(ticks.get(code)),
                'book_age_sec':age(books.get(code)),
                'strategy_tick_source_age_sec':age(delivered.get(code)),
                'ticker_frames':tick_counts.get(code,0),
                'book_frames':book_counts.get(code,0)})
        return {
            'scope':'TOP40_REAL_WS_ONLY_NOT_FILL_PROOF',
            'expected_top40':len(codes),
            'ticker_requested_total':len(requested),
            'book_requested_total':len(requested_books),
            'ticker_submitted_total':len(active),
            'book_submitted_total':len(active_books),
            'top40_ticker_submitted':sum(x['ticker_submitted'] for x in symbols),
            'top40_book_submitted':sum(x['book_submitted'] for x in symbols),
            'top40_ticker_seen_10s':sum(x['ticker_age_sec'] is not None and x['ticker_age_sec']<=10 for x in symbols),
            'top40_book_seen_3s':sum(x['book_age_sec'] is not None and x['book_age_sec']<=3 for x in symbols),
            'top40_strategy_delivered_10s':sum(x['strategy_tick_source_age_sec'] is not None and x['strategy_tick_source_age_sec']<=10 for x in symbols),
            'subscription_refresh_age_sec':round(refresh_age,3) if refresh_age is not None else None,
            'ws_connected':bool(self._ws_connected),
            'ws_reconnects':self._ws_reconnects,
            'ws_last_reason':self._ws_last_reason,
            'subscriptions_sent_not_acknowledged':self._v084_sub_sends,
            'tick_frames_received':received,
            'book_frames_received':book_total,
            'tick_frames_coalesced':coalesced,
            'tick_queue_depth':self._v083_tick_queue.qsize(),
            'tick_pending_codes':pending,
            'tick_queue_drops':self._v083_tick_drops,
            'tick_late_drops_0_75sec':self._v083_tick_late,
            'tick_frames_delivered':self._v083_tick_processed,
            'tick_max_queue_lag_ms':round(self._v083_tick_max_lag_ms,1),
            'top40':symbols}
'''
once(old_worker,new_worker)
once('''                                self._v083_tick_seen[code]=(stamp,timestamp)
                                try:self._v083_tick_queue.put_nowait((code,price,stamp))
                                except queue.Full:self._v083_tick_drops+=1''',
'''                                self._v084_accept_tick(code,price,stamp,timestamp)''')
once('''                            self._v083_book_seen[code]=(time.monotonic(),timestamp)
                            self._enqueue_ws_event('book',book);self._ws_connected=True''',
'''                            with self._v084_tick_lock:
                                self._v083_book_seen[code]=(time.monotonic(),timestamp)
                                self._v084_book_received+=1
                                self._v084_book_counts[code]=self._v084_book_counts.get(code,0)+1
                            self._enqueue_ws_event('book',book);self._ws_connected=True''')
once('''                    ws.send(json.dumps(make_sub('ARTITRADE_COIN_'+uuid4().hex,codes,bids)))
                    subscribed_codes=codes;subscribed_bids=bids''',
'''                    ws.send(json.dumps(make_sub('ARTITRADE_COIN_'+uuid4().hex,codes,bids)))
                    self._v084_sub_sends+=1
                    subscribed_codes=codes;subscribed_bids=bids''')
once('''                            ws.send(json.dumps(make_sub('ARTITRADE_RESUB_'+uuid4().hex,next_codes,next_bids)))
                            subscribed_codes=next_codes;subscribed_bids=next_bids''',
'''                            ws.send(json.dumps(make_sub('ARTITRADE_RESUB_'+uuid4().hex,next_codes,next_bids)))
                            self._v084_sub_sends+=1
                            subscribed_codes=next_codes;subscribed_bids=next_bids''')
ast.parse(src)
assert '0.75' in src
assert 'ping_interval=20,ping_timeout=30' in src
with zipfile.ZipFile(io.BytesIO(old)) as z:
    assert len(z.namelist())==82
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for info in infos:
        z.writestr(info,src.encode() if info.filename=='d_upbit.py' else files[info.filename])
new=out.getvalue()
with zipfile.ZipFile(io.BytesIO(new)) as z:
    assert z.testzip() is None
    assert len(z.namelist())==82
    for name in files:
        if name!='d_upbit.py':assert z.read(name)==files[name]
new_sha=hashlib.sha256(new).hexdigest()
b64=base64.b64encode(new).decode('ascii')
width=(len(b64)+21)//22
for i,p in enumerate(parts):p.write_text(b64[i*width:(i+1)*width])
bridge.write_text(java.replace('CORE_SHA256="'+old_sha+'"','CORE_SHA256="'+new_sha+'"'))
g=gradle.read_text()
assert g.count('versionCode 89')==1
assert g.count("versionName '0.7.83-WS-RECEIVE-ISOLATION'")==1
gradle.write_text(g.replace('versionCode 89','versionCode 90').replace(
    "versionName '0.7.83-WS-RECEIVE-ISOLATION'",
    "versionName '0.7.84-WS-TOP40-TICK-COALESCE'"))
o=overlay.read_text()
anchor="        trend=getattr(self,'market_trend',None)\n        warm={}"
assert o.count(anchor)==1,o.count(anchor)
o=o.replace(anchor,'''        trend=getattr(self,'market_trend',None)
        # Read-only TOP40 comparison: requested vs submitted vs genuine frames.
        # This is diagnostic evidence, not a synthetic quote or execution gate.
        try:
            trend_codes=()
            if trend is not None:
                with trend.lock:
                    trend_codes=tuple(trend._candidates.get('coin',{}))
            if cr is not None and hasattr(cr,'_v084_ws_health'):
                snapshot['upbit_ws_top40_v084']=cr._v084_ws_health(trend_codes)
        except Exception as exc:
            snapshot['upbit_ws_top40_v084']={'diagnostic_error':type(exc).__name__}
        warm={}''',1)
ast.parse(o)
overlay.write_text(o)
test=root/'ui_tests/test_v080_ma_trend_data.py'
t=test.read_text()
assert t.count("self.assertIn('self._v083_tick_queue.put_nowait((code,price,stamp))',src)")==1
t=t.replace("self.assertIn('self._v083_tick_queue.put_nowait((code,price,stamp))',src)",
            "self.assertIn('self._v084_accept_tick(code,price,stamp,timestamp)',src)")
test.write_text(t)
for test_name in ('test_v054_market_trend_audit.py','test_v054_ui.py'):
    file=root/'ui_tests'/test_name
    data=file.read_text()
    legacy="versionName '0.7.83-WS-RECEIVE-ISOLATION'"
    assert data.count(legacy)==1,(test_name,data.count(legacy))
    file.write_text(data.replace(legacy,"versionName '0.7.84-WS-TOP40-TICK-COALESCE'"))
print('V084_SOURCE_PATCH_PASS',old_sha,new_sha,'modules=',len(files),
      'ticker cutoff remains 0.75 sec; RSI and book policies unchanged')
