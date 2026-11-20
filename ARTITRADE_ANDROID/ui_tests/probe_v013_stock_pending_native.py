"""Read-only extracted ORIGINAL source probe to diagnose stock AT missed BUY.

No broker credentials, original user data or production DBs are touched.
"""
from pathlib import Path
import re
native=Path("/tmp/artitrade012_native")

def scan(file,keys,before=12,after=20,max_hits=40):
    src=(native/file).read_text("utf8").splitlines()
    hit=[]
    for i,l in enumerate(src):
        if any(k in l for k in keys): hit.append(i)
    print("V013_SOURCE_FILE",file,"TOTAL_LINES",len(src),"TOTAL_MATCH",len(hit),flush=True)
    for i in hit[:max_hits]:
        print("V013_SOURCE",file,i+1,"\n".join(
            "%04d %s"%(j+1,src[j]) for j in
            range(max(0,i-before),min(len(src),i+after+1))),flush=True)

scan('d_live.py',[
    "ARTITRADE_D_HISTORY_INDICATORS",
    "def _tick_job_loop",
    "def _new_tick",
    "_tick_queue.get",
    "def _frame_repair_worker",
    "def _higher_frame",
    "FIX8_DB_GATE_CONTENTION",
    "FIX14_LIVE_HIGHER_FRAME_GAP_GUARDED",
],before=6,after=26,max_hits=35)
scan('d_v19_portfolio.py',[
    "def _buy_limit_depth",
    "LIMIT_TOUCH_NOT_BOUGHT_FIX6",
    "def process_realtime_tick",
    "LIVE_PARENT_GAP",
    "def _on_tick",
    "def _refresh_watched",
],before=5,after=42,max_hits=18)
scan('d_storage.py',[
    "class Priority",
    "class DBGate",
    "HISTORY_INDICATORS",
    "def _connect",
    "def connect",
    "def transaction",
    "def __enter__",
],before=3,after=28,max_hits=17)

def focus(file,start,end):
    lines=(native/file).read_text('utf8').splitlines()
    print('V013_FOCUS',file,start,end,'BEGIN',flush=True)
    for i in range(start-1,min(len(lines),end)):
        print('V013_DETAIL %04d %s'%(i+1,lines[i]),flush=True)
    print('V013_FOCUS',file,start,end,'END',flush=True)

focus('d_live.py',623,680)
focus('d_live.py',911,1005)
focus('d_v19_portfolio.py',1089,1265)
focus('d_v19_portfolio.py',1438,1520)
focus('d_storage.py',110,176)
