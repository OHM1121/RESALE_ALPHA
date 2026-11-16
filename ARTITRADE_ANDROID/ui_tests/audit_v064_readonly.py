"""Read-only v064 root-cause inspection of packaged source. No broker access."""
import ast, base64, io, zipfile, re, hashlib
from pathlib import Path
root=Path(__file__).resolve().parents[1]
parts=root/'app/src/main/assets/strategy_v070'
archive=base64.b64decode(''.join((parts/f'part_{i:02d}.txt').read_text(encoding='ascii') for i in range(22)))
print('BUNDLE_SHA256',hashlib.sha256(archive).hexdigest())
with zipfile.ZipFile(io.BytesIO(archive)) as z:
 for name in ('d_upbit.py','coin_ma_engine.py','d_live.py','projectgmo/engine.py'):
  if name not in z.namelist():
   print('MISSING',name);continue
  lines=z.read(name).decode('utf-8').splitlines()
  print('FILE',name,len(lines))
  if name=='d_upbit.py':
   keywords=('latest_prices','last_book','_both_watched_codes','orderbook','websockets.connect','ping_interval','ping_timeout','tracked','async def','def _','candles(', 'timeout=')
  elif name=='coin_ma_engine.py':
   keywords=('def process_book','def _buy','def record_candidate','def _sell','FROM orders','status=','expires_at','ma_reference_decision')
  elif name=='d_live.py':
   keywords=('def _new_tick','def _tick_job_loop','def _startup_preload','def _refresh_universe_if_due','def _refresh_tracked_codes','async def _warmup','async def _switch_venue_subscriptions','ping_interval','ping_timeout','_register_subscriptions','_preload')
  else:
   keywords=('entry_profit_floor','ENTRY_PLUS1_FLOOR','ENTRY_PLUS2_FLOOR','def from_state')
  match=[i for i,line in enumerate(lines) if any(k in line for k in keywords)]
  print('MATCHES',len(match))
  out=set()
  for i in match[:105]:
   for j in range(max(0,i-3),min(len(lines),i+6)):
    out.add(j)
  for i in sorted(out):
   print(f'SOURCE {name}:{i+1:04d}',lines[i][:240])
