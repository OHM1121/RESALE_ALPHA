"""Restore only the packaged GMO engine from exact v0.7.62 commit.
Never changes other strategies or existing customer SQLite/credentials.
"""
import base64,hashlib,io,re,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PARTS=ROOT/'app/src/main/assets/strategy_v070'
JAVA=ROOT/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
REFERENCE='61cea5ad1b745da6a88c9a2c0a79d59013e5e991'
BEFORE='88cd3f30d6a327ce7d2dbffb275de16ad0d47d04e9530cc737a714d4fdb18d87'
ORIGINAL='e7518cb90a73cf62522aaa8962d872890e9e517ae988f52c22b3d94ed88531e0'
TARGET='projectgmo/engine.py'
def chunks():
 return [PARTS/f'part_{i:02d}.txt' for i in range(22)]
def decode(parts):
 return base64.b64decode(''.join(parts))
def contents(archive):
 with zipfile.ZipFile(io.BytesIO(archive)) as z:
  return {n:z.read(n) for n in z.namelist()}
def main():
 before=decode([p.read_text(encoding='ascii') for p in chunks()])
 oldtexts=[subprocess.check_output(['git','show',REFERENCE+':'+str(p.relative_to(ROOT))]).decode('ascii') for p in chunks()]
 old=decode(oldtexts)
 before_hash=hashlib.sha256(before).hexdigest()
 old_hash=hashlib.sha256(old).hexdigest()
 assert old_hash==ORIGINAL,('original v062 core SHA unexpectedly changed',old_hash)
 assert before_hash in (BEFORE,ORIGINAL),('unexpected v063 core SHA',before_hash)
 prev=contents(old)
 current=contents(before)
 assert set(prev)==set(current)
 assert all(prev[k]==current[k] for k in current if k!=TARGET),'OTHER_STRATEGY_CONTENT_DIFFERED'
 assert b'entry_profit_floor: float = 0.02' in prev[TARGET]
 assert b'ENTRY_PLUS2_FLOOR' in prev[TARGET]
 if before_hash==ORIGINAL:
  print('GMO_ALREADY_V062',old_hash);return
 assert b'entry_profit_floor: float = 0.01' in current[TARGET]
 java=JAVA.read_text(encoding='utf-8')
 assert java.count(BEFORE)==1
 archive=io.BytesIO()
 with zipfile.ZipFile(io.BytesIO(before)) as source,zipfile.ZipFile(archive,'w') as output:
  for item in source.infolist():
   output.writestr(item,prev[TARGET] if item.filename==TARGET else current[item.filename])
 result=archive.getvalue()
 sha=hashlib.sha256(result).hexdigest()
 assert all(prev[k]==contents(result)[k] for k in current)
 data=base64.b64encode(result).decode('ascii')
 length=(len(data)+21)//22
 for i,p in enumerate(chunks()):
  p.write_text(data[i*length:(i+1)*length],encoding='ascii')
 JAVA.write_text(java.replace(BEFORE,sha),encoding='utf-8')
 assert hashlib.sha256(decode([p.read_text(encoding='ascii') for p in chunks()])).hexdigest()==sha
 print('GMO_V062_POLICY_RESTORED',sha,'ORIGINAL_POLICY=2PCT','UNCHANGED_OTHER_MODULES',len(current)-1)
if __name__=='__main__':main()
