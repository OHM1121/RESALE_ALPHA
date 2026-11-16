"""v073 surgical repair of the production GMO exit callback and held-risk lane.

Run in a CI job on an immutable v0.7.72 base. Never contacts brokers,
reads actual account state or modifies customer databases. Preserves every
original trading threshold and unrelated embedded module.
"""
from __future__ import annotations
import ast,base64,hashlib,io,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ASSET=ROOT/'app/src/main/assets/strategy_v070'
PARTS=[ASSET/f'part_{i:02d}.txt' for i in range(22)]
BRIDGE=ROOT/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
GRADLE=ROOT/'app/build.gradle'
ORIGINAL='2ee2dfd6e743800c4246e7ea7ac9f9049686935500b3e87a2ac9775fb3466f10'
raw=base64.b64decode(''.join(p.read_text('ascii') for p in PARTS))
assert hashlib.sha256(raw).hexdigest()==ORIGINAL,'v072 signed source changed: abort'
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
    meta=archive.infolist()
    src={x.filename:archive.read(x.filename) for x in meta}
assert len(src)==82
gmo=src['d_gmo_runtime.py'].decode('utf-8')
d=src['d_live.py'].decode('utf-8')

def once(text,before,after):
    n=text.count(before)
    if n!=1:raise AssertionError('expected one matched anchor, got '+str(n)+': '+before[:100])
    return text.replace(before,after,1)

# Completely missing from production GMOService.on_live_tick: the existing
# Engine.on_quote() stop/take-profit decision routine. Unit tests exercised
# the pure engine directly but never checked service-to-engine dispatch.
assert 'self.engine.on_quote(q)' not in gmo,'GMO quote handler already wired; review manually'
gmo=once(gmo,
"""            prior_buys = len(self.engine.buy_fills)
            # If continuously pre-armed before 09:00, first fresh KRX 09:00 print may seed opener.""",
"""            prior_buys = len(self.engine.buy_fills)
            # Every fresh held 0B must enter the ORIGINAL approved GMO exit
            # state machine. Without this call even a crossed profit-protect
            # threshold did not create a SELL intent or a pending order.
            # Paper matching/broker acknowledgement remains independently
            # verified; no replay, REST price, or invented historical fill.
            if code in self.engine.positions:
                self.engine.on_quote(q)
            # If continuously pre-armed before 09:00, first fresh KRX 09:00 print may seed opener."""
)
# Make existing holdings a priority regardless of D+1 entry window.
gmo=once(gmo,
"""    def overdue_watchlist(self):
        \"\"\"Held GMO names with expired holding periods, evaluated ahead of D ticks.\"\"\"""",
"""    def holding_codes(self):
        \"\"\"All still-held GMO positions, not just overdue or pending buys.\"\"\"
        with self._lock:
            return frozenset(code for code,pos in self.engine.positions.items()
                             if getattr(pos,'qty',0)>0)

    def overdue_watchlist(self):
        \"\"\"Held GMO names with expired holding periods, evaluated ahead of D ticks.\"\"\""""
)
d=once(d,
"""            (venue == 'KRX' and code in self.gmo.active_buy_watchlist(gmo_now))
        )""",
"""            (venue == 'KRX' and code in self.gmo.active_buy_watchlist(gmo_now)) or
            (venue == 'KRX' and code in self.gmo.holding_codes())
        )"""
)
d=once(d,
"""            try:
                venue = tick[7]
                now = now_kst()
                if venue == 'NXT':""",
"""            try:
                venue = tick[7]
                now = now_kst()
                # Priority processing must NOT weaken the original 3s
                # executable exchange+receive-time gate.
                from d_minute_repair_fast import exchange_stamp_is_fresh
                if (len(tick)<9 or
                    not exchange_stamp_is_fresh(tick[8],tick[6])):
                    self._queue_pipeline_event('HELD_RISK_PRIORITY_STALE_TICK',
                        {'code':str(tick[0]),'venue':str(venue)})
                    continue
                if venue == 'NXT':"""
)
ast.parse(gmo);ast.parse(d)
assert 'self.engine.on_quote(q)' in gmo
assert 'self.gmo.holding_codes()' in d
assert 'V071_KRX_0B_SILENT_45S_RECONNECT' not in d
assert "self._v071_enqueue_feed('raw',msg)" in d
assert "entry_profit_floor=0.02" not in d  # policy belongs only to original engine
src['d_gmo_runtime.py']=gmo.encode('utf-8')
src['d_live.py']=d.encode('utf-8')
out=io.BytesIO()
with zipfile.ZipFile(out,'w') as z:
    for info in meta:z.writestr(info,src[info.filename])
patched=out.getvalue()
with zipfile.ZipFile(io.BytesIO(patched)) as z:
    for name,old in src.items():
        assert z.read(name)==old,name
    with zipfile.ZipFile(io.BytesIO(raw)) as original:
        for name in original.namelist():
            if name not in ('d_live.py','d_gmo_runtime.py'):
                assert original.read(name)==z.read(name),'unapproved module change: '+name
encoded=base64.b64encode(patched).decode('ascii')
stride=(len(encoded)+21)//22
for i,p in enumerate(PARTS):p.write_text(encoded[i*stride:(i+1)*stride],encoding='ascii')
newsha=hashlib.sha256(patched).hexdigest()
j=BRIDGE.read_text('utf-8')
assert j.count('CORE_SHA256="'+ORIGINAL+'"')==1
BRIDGE.write_text(j.replace('CORE_SHA256="'+ORIGINAL+'"','CORE_SHA256="'+newsha+'"'),encoding='utf-8')
gradle=GRADLE.read_text('utf-8')
assert gradle.count('versionCode 78')==1
assert gradle.count("versionName '0.7.72-VERIFIED-STOCK-QUOTE'")==1
GRADLE.write_text(gradle.replace('versionCode 78','versionCode 79')
                   .replace("versionName '0.7.72-VERIFIED-STOCK-QUOTE'",
                            "versionName '0.7.73-GMO-EXIT-WATCH-QUOTE'"),encoding='utf-8')
print('V073_GMO_EXIT_DISPATCH_RESTORED')
print('V073_HELD_GMO_PRIORITY_RISK_LANE_ENABLED')
print('V073_BROKER_FRESHNESS_UNCHANGED')
print('V073_UNMODIFIED_EMBEDDED_MODULES',len(src)-2)
print('CORE_SHA256',newsha)
