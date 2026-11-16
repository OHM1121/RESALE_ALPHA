"""Read only verified bundled source and list quote/display/snapshot provenance.
Does not connect to any broker, start a runtime, or change ledgers.
"""
from pathlib import Path
import base64,hashlib,io,zipfile,re
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'app/src/main/assets/strategy_v070'
raw=base64.b64decode(''.join((P/('part_%02d.txt'%i)).read_text('ascii') for i in range(22)))
print('BUNDLE_SHA256',hashlib.sha256(raw).hexdigest())
rules={
'd_live.py':[
 '_new_tick','_tick_job_loop','_ws_loop','_on_tick','_ws','_refresh_universe_if_due',
 '_refresh_tracked_codes','_run','_preload','_update','_subscribe',
 'latest_price','last_tick','last_price','_watch_feed_last_epoch',
 'self.last','tick_at','tick_time','_handle_price','_live_loop',
 'last_quote','gmo',
],
'd_gmo_runtime.py':[
 'class','def on_live_tick','def on_priority_tick','def on_book_message',
 'def snapshot','def _','last_price','tick','bid','ask','_last_quote','quote',
 'on_tick','_on_print',
],
'd_gmo_score_research_paper.py':['last_price','def snapshot'],
'stock_ma_engine.py':['def snapshot','last_price','positions','mark','_refresh_watched'],
'd_v19_portfolio.py':['last_price','def snapshot','def on_tick'],
'd_engine.py':['last_price','def snapshot','def on_tick'],
'd_storage.py':['stock_quotes','last_price','last_tick','def connect'],
'at_close_bridge.py':['last_price','last_tick','quote','def snapshot','def broker_transport_snapshot']
}
with zipfile.ZipFile(io.BytesIO(raw)) as z:
 print('RELEVANT',*[n for n in z.namelist() if any(k in n.lower() for k in ('gmo','live','stock','close','v19','storage'))][:100],sep=' | ')
 for name,keys in rules.items():
  if name not in z.namelist():continue
  src=z.read(name).decode('utf8').splitlines()
  print('MODULE',name,'LINES',len(src))
  indices=[i for i,line in enumerate(src) if any(key in line for key in keys)]
  print('MATCHES',len(indices))
  for i in indices[:95]:print('MATCH',name,i+1,src[i][:260])

print('TARGETED_EXCERPTS')
ranges={
 'd_live.py':[(285,370),(455,545),(885,966),(1435,1555),(1840,1930),(1930,2040),(2040,2195),(2195,2290)],
 'd_gmo_runtime.py':[(80,132),(478,563),(565,736),(1075,1195)],
 'stock_ma_engine.py':[(350,398)],
 'at_close_bridge.py':[(85,122)],
}
with zipfile.ZipFile(io.BytesIO(raw)) as z:
 for name,parts in ranges.items():
  if name not in z.namelist():continue
  lines=z.read(name).decode('utf8').splitlines()
  for lo,hi in parts:
   print('TARGET_RANGE',name,lo,hi)
   for idx in range(lo-1,min(hi,len(lines))):
    print('TARGET_SOURCE',name,idx+1,lines[idx][:280])
