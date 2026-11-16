"""Add narrowly scoped original GMO no-order diagnostics, not strategy changes.

The existing 82-module archive remains byte-identical except for
d_gmo_runtime.py, whose original buy condition and paper matcher are unchanged.
No broker contact, prices, historical trades or original user DB writes in CI.
"""
from __future__ import annotations
import ast
import base64
import hashlib
import io
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ASSET=ROOT/'app/src/main/assets/strategy_v070'
PARTS=[ASSET/f'part_{i:02d}.txt' for i in range(22)]
BRIDGE=ROOT/'app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java'
BASE='48a3fbd833de7c42ae44988dbaccdd70b28594bad6567f22e91dd93acd8bf7fe'
raw=base64.b64decode(''.join(x.read_text('ascii') for x in PARTS))
digest=hashlib.sha256(raw).hexdigest()
if digest!=BASE:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        module=z.read('d_gmo_runtime.py').decode('utf-8')
    assert 'def _audit_d1_nonorder(' in module
    assert 'CORE_SHA256="'+digest+'"' in BRIDGE.read_text('utf-8')
    print('V075_GMO_DIAGNOSTICS_ALREADY_APPLIED',digest)
    raise SystemExit(0)
with zipfile.ZipFile(io.BytesIO(raw)) as src:
    infos=src.infolist()
    data={x.filename:src.read(x.filename) for x in infos}
    assert len(data)==82
d=data['d_gmo_runtime.py'].decode('utf-8')
assert 'def _audit_d1_nonorder(' not in d
def once(before,after):
    global d
    n=d.count(before)
    if n!=1:raise AssertionError('GMO diagnostic anchor expected 1, got '+str(n)+': '+before[:80])
    d=d.replace(before,after,1)

once("""    def _persist(self, force=False):
""", """    def _audit_d1_nonorder(self, code, reason, detail, at):
        \"\"\"Durable GMO PAPER candidate rejection, never an execution or fill.

        Only append to the existing in-memory original engine.audit, which
        the normal _persist writes on its already-scheduled cadence.
        No SQLite or new network I/O in the 09:00 hot path.
        \"\"\"
        key=('V075_D1_NO_ORDER',str(code),str(at)[:10],str(reason))
        now=systime.monotonic()
        if now-self._gmo_last_block_log.get(key,-1e9)<300.0:
            return
        self._gmo_last_block_log[key]=now
        self.engine._log(code, 'GMO_D1_NO_ORDER_'+str(reason),
                         str(detail)[:400], str(at))

    def _persist(self, force=False):
""")
once("""                ceiling = d1_allowed_ceiling(opener, event.event_close)
                if q.last <= ceiling and entry_ask <= ceiling and paper:
""", """                ceiling = d1_allowed_ceiling(opener, event.event_close)
                # Facts about an intentionally UNPLACED order, not synthetic
                # attempts. Preserve the ORIGINAL exact buy condition below.
                if not paper:
                    self._audit_d1_nonorder(code,'LIVE_MODE_PAPER_ONLY',
                        'original GMO live broker orders are not enabled',q.at)
                elif q.last > ceiling or entry_ask > ceiling:
                    self._audit_d1_nonorder(code,'PRICE_ABOVE_ALLOWED_CEILING',
                        f'last={q.last} ask={entry_ask} ceiling={ceiling} '
                        f'official_open={opener} D_close={event.event_close}',q.at)
                if q.last <= ceiling and entry_ask <= ceiling and paper:
""")
once("""                    except ValueError as exc:
                        if '4 positions' not in str(exc) and '25% account' not in str(exc):
                            self._log('ENTRY_SKIPPED',code,str(exc))
""", """                    except ValueError as exc:
                        # Existing code suppressed the two most common
                        # non-order causes (four slots / unavailable budget)
                        # from gmo_issues. Keep all original behavior, but
                        # persist the exact cause to engine.audit.
                        self._audit_d1_nonorder(code,'ENGINE_ENTRY_REJECTED',
                                                str(exc),q.at)
                        if '4 positions' not in str(exc) and '25% account' not in str(exc):
                            self._log('ENTRY_SKIPPED',code,str(exc))
""")
ast.parse(d)
assert d.count("if q.last <= ceiling and entry_ask <= ceiling and paper:")==1
assert d.count("intent = self.engine.open_entry(")==1
assert d.count("self.engine.paper_match(q)")==1
assert d.count("self.engine.on_quote(q)")==1
data['d_gmo_runtime.py']=d.encode('utf8')
buf=io.BytesIO()
with zipfile.ZipFile(buf,'w') as out:
    for item in infos:out.writestr(item,data[item.filename])
patched=buf.getvalue()
with zipfile.ZipFile(io.BytesIO(patched)) as ver, zipfile.ZipFile(io.BytesIO(raw)) as orig:
    assert ver.namelist()==orig.namelist()
    for name in orig.namelist():
        if name!='d_gmo_runtime.py':assert ver.read(name)==orig.read(name),name
    assert ver.read('d_gmo_runtime.py')==d.encode('utf8')
encoded=base64.b64encode(patched).decode('ascii')
stride=(len(encoded)+21)//22
for i,part in enumerate(PARTS):
    part.write_text(encoded[i*stride:(i+1)*stride],encoding='ascii')
newsha=hashlib.sha256(patched).hexdigest()
bridge=BRIDGE.read_text('utf8')
assert bridge.count('CORE_SHA256="'+BASE+'"')==1
BRIDGE.write_text(bridge.replace('CORE_SHA256="'+BASE+'"',
                                 'CORE_SHA256="'+newsha+'"'),encoding='utf8')
print('V075_GMO_MISSING_REJECTION_EVIDENCE_PERSISTED_NO_STRATEGY_CHANGE')
print('V075_CORE_SHA256',newsha)
print('V075_81_UNMODIFIED_EMBEDDED_MODULES')
