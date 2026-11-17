"""v086 immutable archive patch: genuine Upbit ticker age and faster WS resubscribe.

Only d_upbit.py of the 82 bundled EXE modules is modified. The original
coin/stock ledgers, RSI policy and broker accounts are NOT migrated or reset.
"""
import ast,base64,hashlib,io,zipfile
from pathlib import Path

root=Path(__file__).resolve().parents[1]
main=root/'app/src/main'
paths=[main/'assets/strategy_v070'/f'part_{i:02d}.txt' for i in range(22)]
bridge=main/'java/com/artitrade/mobile/StrategyRuntimeBridge.java'
gradle=root/'app/build.gradle'
raw=base64.b64decode(''.join(p.read_text('ascii') for p in paths))
original_sha=hashlib.sha256(raw).hexdigest()
assert original_sha=='28b799d795cb4ba7e9ebf109dc458c48057d4ea623ec36b808cf4bd06048dda0',original_sha
java=bridge.read_text()
assert java.count('CORE_SHA256="'+original_sha+'"')==1
with zipfile.ZipFile(io.BytesIO(raw)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    infos=z.infolist()
    original={entry.filename:z.read(entry.filename) for entry in infos}
src=original['d_upbit.py'].decode('utf-8')

def once(old,new):
    global src
    assert src.count(old)==1,(old[:100],src.count(old))
    src=src.replace(old,new,1)

once('        self._v084_sub_sends=0',
'''        self._v084_sub_sends=0
        # Per-coin exchange event clock, not the newer delivery of an OLD
        # ticker mark. Entries require a newly received genuine trade.
        self._v086_last_trade_by_code={}
        self._v086_tick_replay_rejected=0
        self._v086_tick_exchange_stale_rejected=0
        self._v086_book_exchange_stale_rejected=0''')
once('''            'tick_max_queue_lag_ms':round(self._v083_tick_max_lag_ms,1),
            'top40':symbols}''',
'''            'tick_max_queue_lag_ms':round(self._v083_tick_max_lag_ms,1),
            'ticker_replay_rejected':self._v086_tick_replay_rejected,
            'ticker_exchange_stale_rejected':self._v086_tick_exchange_stale_rejected,
            'book_exchange_stale_rejected':self._v086_book_exchange_stale_rejected,
            'top40':symbols}''')
once('''with connect(WS_URL,open_timeout=10,ping_interval=20,ping_timeout=30,max_size=2**20,max_queue=64,
                             close_timeout=5) as ws:''',
'''with connect(WS_URL,open_timeout=10,ping_interval=20,ping_timeout=30,max_size=2**20,max_queue=128,
                             close_timeout=5) as ws:''')
once('''                        try:raw=ws.recv(timeout=2)''','''                        try:raw=ws.recv(timeout=.75)''')
once('''                        timestamp=item.get('timestamp')
                        if not isinstance(timestamp,(int,float)) or abs(kst_now().timestamp()-timestamp/1000)>MAX_BOOK_AGE_SEC:continue
                        if typ=='ticker' and code in subscribed_codes:
                            price=item.get('trade_price')
                            if price is not None:
                                stamp=time.monotonic()
                                self._v084_accept_tick(code,price,stamp,timestamp)''',
'''                        # Per official Upbit schema, ticker.timestamp is
                        # message timestamp; ticker.trade_timestamp is the
                        # source time of the LAST ACTUAL TRANSACTION.
                        # Never turn a refreshed non-trade ticker into a
                        # fictitiously fresh RSI price. Keep 10s trade / 3s
                        # orderbook rules, with no fabricated REST substitutes.
                        if typ=='ticker' and code in subscribed_codes:
                            trade_stamp=item.get('trade_timestamp')
                            price=item.get('trade_price')
                            if (isinstance(trade_stamp,bool) or
                                not isinstance(trade_stamp,(int,float)) or
                                price is None):
                                self._v086_tick_exchange_stale_rejected+=1
                                continue
                            trade_age=time.time()-trade_stamp/1000
                            if trade_age < -0.5 or trade_age > 10.0:
                                self._v086_tick_exchange_stale_rejected+=1
                                continue
                            previous=self._v086_last_trade_by_code.get(code)
                            if (previous is not None and
                                (trade_stamp<previous[0] or
                                 (trade_stamp==previous[0] and price==previous[1]))):
                                self._v086_tick_replay_rejected+=1
                                continue
                            self._v086_last_trade_by_code[code]=(trade_stamp,price)
                            if price is not None:
                                stamp=time.monotonic()
                                self._v084_accept_tick(code,price,stamp,trade_stamp)''')
once('''                        elif typ=='orderbook' and code in subscribed_bids:
                            units=item.get('orderbook_units') or []''',
'''                        elif typ=='orderbook' and code in subscribed_bids:
                            timestamp=item.get('timestamp')
                            if (isinstance(timestamp,bool) or
                                not isinstance(timestamp,(int,float)) or
                                abs(time.time()-timestamp/1000)>MAX_BOOK_AGE_SEC):
                                self._v086_book_exchange_stale_rejected+=1
                                continue
                            units=item.get('orderbook_units') or []''')
assert 'ping_interval=20,ping_timeout=30' in src
assert "trade_age > 10.0" in src and 'MAX_BOOK_AGE_SEC' in src
assert 'self.latest_prices[code]=str(price)' in src
assert 'if age<0 or age>0.75:' in src # internal stale queue cutoff retained
ast.parse(src)

result=io.BytesIO()
with zipfile.ZipFile(result,'w') as z:
    for item in infos:
        z.writestr(item,src.encode('utf-8') if item.filename=='d_upbit.py' else original[item.filename])
changed=result.getvalue()
with zipfile.ZipFile(io.BytesIO(changed)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    for name,payload in original.items():
        if name!='d_upbit.py':assert z.read(name)==payload,name
new_sha=hashlib.sha256(changed).hexdigest()
encoded=base64.b64encode(changed).decode('ascii')
width=(len(encoded)+21)//22
for index,path in enumerate(paths):
    path.write_text(encoded[index*width:(index+1)*width])
bridge.write_text(java.replace('CORE_SHA256="'+original_sha+'"','CORE_SHA256="'+new_sha+'"'))
g=gradle.read_text()
assert g.count('versionCode 91')==1
assert g.count("versionName '0.7.85-RESEARCH-GMO-PAPER'")==1
gradle.write_text(g.replace('versionCode 91','versionCode 92').replace(
    "versionName '0.7.85-RESEARCH-GMO-PAPER'",
    "versionName '0.7.86-RSI-FIRST-CROSS-GMO-FEED'"))
for name in ('test_v054_ui.py','test_v054_market_trend_audit.py'):
    test=root/'ui_tests'/name
    content=test.read_text()
    assert content.count("versionName '0.7.85-RESEARCH-GMO-PAPER'")==1,name
    test.write_text(content.replace("versionName '0.7.85-RESEARCH-GMO-PAPER'",
       "versionName '0.7.86-RSI-FIRST-CROSS-GMO-FEED'"))
print('V086_UPBIT_OFFICIAL_TRUE_TRADE_TS_10S_BOOK_3S_PASS',
      original_sha,new_sha,'unchanged_modules=81')
