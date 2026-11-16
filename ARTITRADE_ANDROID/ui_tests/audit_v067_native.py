"""Inspect signed bundled original code; no broker, account or order calls."""
from pathlib import Path
import base64,zipfile,io,ast
base=Path(__file__).resolve().parents[1]/'app/src/main/assets/strategy_v070'
data=base64.b64decode(''.join((base/f'part_{i:02d}.txt').read_text('ascii') for i in range(22)))
names={
'stock_ma_engine.py':['StockMAService.on_tick','StockMAService.scan_completed','StockMAService._sell','StockMAService._exit','StockMAService.snapshot','StockMAService._refresh_watched'],
'coin_ma_engine.py':['MACoinLedger._buy','MACoinLedger.record_candidate','MACoinLedger._sell','MACoinLedger.process_book','MACoinLedger.snapshot'],
'd_live.py':['LivePaperRunner.run','LivePaperRunner._new_tick','LivePaperRunner._refresh_universe_if_due','LivePaperRunner._register_subscriptions','LivePaperRunner._boot_market','LivePaperRunner._warmup','LivePaperRunner._tick_job_loop'],
'd_upbit.py':['UpbitPaperRunner._ma_scan_decision','UpbitPaperRunner._both_watched_codes','UpbitPaperRunner._run','UpbitPaperRunner.run','UpbitPaperRunner._handle_message']
}
with zipfile.ZipFile(io.BytesIO(data)) as z:
 for path,target in names.items():
  if path not in z.namelist():continue
  src=z.read(path).decode()
  lines=src.splitlines()
  tree=ast.parse(src,path)
  funcs=[]
  for node in ast.walk(tree):
   if not isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):continue
   parents=[c for c in ast.walk(tree) if isinstance(c,ast.ClassDef) and node in c.body]
   key=(parents[0].name+'.' if parents else '')+node.name
   if key in target:funcs.append((key,node.lineno,node.end_lineno))
  for k,start,end in funcs:
   print('SOURCE_RANGE',path,k,start,end)
   for i in range(max(0,start-2),min(end+2,len(lines))):
    print('SOURCE',path,i+1,lines[i][:420])

with zipfile.ZipFile(io.BytesIO(data)) as z:
    print("CORE_COIN_MA_FILE_NAMES",[x for x in z.namelist() if 'coin' in x.lower() or 'ma' in x.lower()][:100])
