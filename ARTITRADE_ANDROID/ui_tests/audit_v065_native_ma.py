"""Read-only inspect native active MA order/exit points from verified archive."""
from pathlib import Path
import base64,io,zipfile
root=Path(__file__).resolve().parents[1]
base=root/'app/src/main/assets/strategy_v070'
data=base64.b64decode(''.join((base/('part_%02d.txt'%i)).read_text('ascii') for i in range(22)))
targets={
 'stock_ma_engine.py':[('_buy',0),('_exit',0),('on_tick',0),('scan_completed',0),('_record',0)],
 'd_upbit.py':[('process_book',0),('_buy',0),('_sell',0),('_ma_scan_decision',0)],
}
with zipfile.ZipFile(io.BytesIO(data)) as f:
 for path,targets_by_name in targets.items():
  lines=f.read(path).decode().splitlines()
  print('FILE',path,len(lines))
  funcs=[(i,l.strip()) for i,l in enumerate(lines)
         if l.strip().startswith(('def ','async def '))]
  for label,_ in targets_by_name:
   matches=[(i,l) for i,l in funcs if l.startswith('def '+label+'(') or l.startswith('async def '+label+'(')]
   for i,fn in matches:
    end=next((j for j,s in funcs if j>i and len(lines[j])-len(lines[j].lstrip())<=len(lines[i])-len(lines[i].lstrip())),min(i+160,len(lines)))
    print('RANGE',path,label,i+1,min(end+1,len(lines)),end-i)
    for k in range(max(0,i-8),min(len(lines),end+8)):
     print('SOURCE',path,k+1,lines[k][:250])
