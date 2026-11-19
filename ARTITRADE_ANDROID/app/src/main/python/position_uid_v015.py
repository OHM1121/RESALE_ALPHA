"""Durable, source-scoped identifiers for the eight original trading wallets.

This is a UI/research attribution sidecar. ORIGINAL numeric position_id is
NEVER replaced, no broker order or original trading DB is touched. Missing
historical linkages are flagged; they are not invented as trade receipts.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import threading


_LOCK=threading.RLock()
_NAME="POSITION_UID_V015.sqlite3"
_TAG="ARTITRADE_POSITION_UID_V015"
_WALLETS={
    "stock/at":"SAT","stock/ma":"SMA","stock/gmo":"SGM",
    "stock/cw":"SCW","stock/trend":"STR",
    "coin/general":"CAT","coin/ma":"CMA","coin/trend":"CTR",
}
_OWNER_ALIASES={
    "ma_subledger":"ma","ma":"ma","gmo":"gmo",
    "cw":"cw","close":"cw","trend":"trend","market_trend":"trend",
    "reference":"general","general":"general",
}
_DB_PATHS={
    "stock/at":"stock/ARTITRADE_D.sqlite3",
    "stock/ma":"stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
    "stock/cw":"stock/close/AT_CLOSE_PAPER.sqlite3",
    "stock/trend":"stock/trend/market_trend_wallet_v054.sqlite3",
    "coin/general":"coin/base/upbit_paper.sqlite3",
    "coin/ma":"coin/ma_epoch_v067/upbit_paper.sqlite3",
    "coin/trend":"coin/trend/market_trend_wallet_v054.sqlite3",
}

def _epoch(root):
    from ledger_rollback_guard_v002 import _root,_epoch
    root=_root(root)
    return _epoch(root)[0]

def _text(value):
    if value is None:return ""
    result=str(value).strip()
    return "" if result.lower() in ("","none","null","nan","0") else result

def _owner(path):
    if not path or path[0] not in ("stock","coin"):
        return None
    market=path[0]
    original="at" if market=="stock" else "general"
    for part in path[1:]:
        key=_OWNER_ALIASES.get(part,part)
        if key in ("at","general","ma","gmo","cw","trend"):
            original=key
    if market=="stock" and original=="general":original="at"
    if market=="coin" and original=="at":original="general"
    owner=market+"/"+original
    return owner if owner in _WALLETS else None

def _identity_key(row,kind):
    native=_text(row.get("position_id") if kind=="execution" else row.get("id"))
    if native:return "native:"+native,True
    if kind=="position":
        # Some native GMO paper positions have no numeric id. A genuine entry
        # day and symbol identify that original D+1 trading cohort.
        name=_text(row.get("code") or row.get("market") or row.get("symbol"))
        day=_text(row.get("entry_date") or row.get("buy_at") or
                  row.get("entry_at") or row.get("entry_ts"))
        if name and day:
            return "entry:"+name+"|"+day,False
        return None,False
    # Only a broker/native-stored entry identifier can link a fill lacking
    # position_id. A raw SELL id is not proof of its BUY position.
    code=_text(row.get("code") or row.get("market") or row.get("symbol"))
    day=_text(row.get("entry_date") or row.get("buy_at") or row.get("entry_at"))
    if code and day:return "entry:"+code+"|"+day,False
    rid=_text(row.get("id") or row.get("execution_id") or row.get("fill_id"))
    if rid:return "unlinked_fill:"+rid,False
    return None,False

def _buy_proof(row):
    receipt=_text(row.get("id") or row.get("execution_id") or row.get("event_key")
                  or row.get("order_id"))
    if not receipt:return ""
    return receipt

def _uid(owner,epoch,key,generation):
    digest=hashlib.sha256(json.dumps(
        [str(epoch),owner,key,int(generation)],ensure_ascii=True,
        separators=(",",":")).encode("utf8")).hexdigest()[:22].upper()
    return _WALLETS[owner]+"-"+digest

class Registry:
    def __init__(self,root):
        self.root=Path(root)
        self.epoch=_epoch(self.root)
        self.path=self.root/_NAME
        self.db=sqlite3.connect(str(self.path),timeout=6)
        self.db.execute("PRAGMA busy_timeout=6000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS position_uid_registry(
          epoch TEXT NOT NULL,owner TEXT NOT NULL,source_key TEXT NOT NULL,
          generation INTEGER NOT NULL,uid TEXT NOT NULL UNIQUE,
          buy_receipt TEXT NOT NULL,created_at TEXT NOT NULL,
          PRIMARY KEY(epoch,owner,source_key,generation))""")
        self.cache={}
    def close(self):
        self.db.commit()
        self.db.close()
    def assign(self,owner,key,buy_receipt=""):
        if owner not in _WALLETS or not key:
            raise ValueError("POSITION_UID_PROVENANCE_REQUIRED")
        cache_key=(owner,key)
        current=self.cache.get(cache_key)
        if current is None:
            row=self.db.execute("""SELECT generation,uid,buy_receipt
                FROM position_uid_registry WHERE epoch=? AND owner=?
                AND source_key=? ORDER BY generation DESC LIMIT 1""",
                (self.epoch,owner,key)).fetchone()
        else:row=current
        if row is not None:
            generation,uid,old_receipt=row
            if buy_receipt and old_receipt and buy_receipt!=old_receipt:
                # SQLite INTEGER PRIMARY KEY may reuse an ID after deleting
                # the last row. Different genuine BUY receipts get NEW UIDs.
                generation+=1
                row=None
            elif buy_receipt and not old_receipt:
                self.db.execute("""UPDATE position_uid_registry SET buy_receipt=?
                  WHERE epoch=? AND owner=? AND source_key=? AND generation=?""",
                  (buy_receipt,self.epoch,owner,key,generation))
                old_receipt=buy_receipt
            if row is not None:
                result=(generation,uid,old_receipt)
                self.cache[cache_key]=result
                return uid
        else:
            generation=1
        uid=_uid(owner,self.epoch,key,generation)
        self.db.execute("""INSERT INTO position_uid_registry
          (epoch,owner,source_key,generation,uid,buy_receipt,created_at)
          VALUES (?,?,?,?,?,?,?)""",(self.epoch,owner,key,generation,uid,
          buy_receipt,dt.datetime.now(dt.timezone.utc).isoformat()))
        self.cache[cache_key]=(generation,uid,buy_receipt)
        return uid

def _walk(value,path=()):
    if not isinstance(value,dict):return
    if ("positions" in value or "executions" in value or
        "fills" in value) and _owner(path):
        yield _owner(path),value
    for key,sub in value.items():
        if isinstance(sub,dict):
            yield from _walk(sub,path+(str(key),))

def _annotate_ledger(reg,owner,ledger):
    positions=[x for x in (ledger.get("positions") or [])
               if isinstance(x,dict)]
    fills=[x for x in (ledger.get("executions") or ledger.get("fills") or [])
           if isinstance(x,dict)]
    proof={}
    for row in fills:
        if str(row.get("side") or row.get("type") or "").upper()!="BUY":continue
        key,_=_identity_key(row,"execution")
        if key and not key.startswith("unlinked_fill:"):
            proof.setdefault(key,_buy_proof(row))
    source_positions={}
    for row in positions:
        key,native=_identity_key(row,"position")
        if key:
            source_positions[key]=row
            value=reg.assign(owner,key,proof.get(key,""))
            row["position_uid"]=value
            row["position_uid_status"]="NATIVE_POSITION" if native else "ENTRY_COHORT"
            if native and not _text(row.get("position_id")):
                row["position_id"]=row["id"]
        else:
            # Cannot invent a native position relationship. Keep a stable
            # individual UI record keyed by all ORIGINAL entry-identifying fields.
            fp=json.dumps([_text(row.get(k)) for k in
                ("code","market","symbol","entry_at","entry_date","ref_at",
                 "tf","frame","buy_at","candidate_id")],ensure_ascii=True)
            if not any(_text(row.get(k)) for k in
                ("entry_at","entry_date","buy_at","candidate_id","ref_at")):
                row["position_uid"]=""
                row["position_uid_status"]="ORIGINAL_POSITION_ENTRY_ID_MISSING"
                continue
            key="unlinked_position:"+hashlib.sha256(fp.encode()).hexdigest()
            row["position_uid"]=reg.assign(owner,key)
            row["position_uid_status"]="NATIVE_ID_MISSING_UNLINKED"
    for row in fills:
        key,native=_identity_key(row,"execution")
        if key is None:
            # No stable original event ID: never manufacture a trade linkage.
            row["position_uid"]=""
            row["position_uid_status"]="UNLINKED_EXECUTION_NO_SOURCE_EVENT"
            continue
        if key in source_positions and not native:
            identity_status="ENTRY_COHORT"
        elif key.startswith("unlinked_fill:"):
            identity_status="UNLINKED_EXECUTION"
        else:
            identity_status="NATIVE_POSITION" if native else "ENTRY_COHORT"
        row["position_uid"]=reg.assign(owner,key,proof.get(key,""))
        row["position_uid_status"]=identity_status
    return len(positions),len(fills)

def annotate_packet(root,packet):
    """Called after existing source snapshots; original order/ledger untouched."""
    if not isinstance(packet,dict):return packet
    with _LOCK:
        reg=Registry(root)
        try:
            seen=set()
            counts={"position_rows":0,"execution_rows":0,"unlinked_rows":0}
            for owner,ledger in _walk(packet):
                pair=(owner,id(ledger))
                if pair in seen:continue
                seen.add(pair)
                p,e=_annotate_ledger(reg,owner,ledger)
                counts["position_rows"]+=p
                counts["execution_rows"]+=e
                for row in (ledger.get("executions") or []):
                    if isinstance(row,dict) and str(row.get("position_uid_status","")).startswith("UNLINKED"):
                        counts["unlinked_rows"]+=1
            packet["position_uid_v015"]={"schema":_TAG,
                "status":"COMPLETE","count":counts,
                "original_wallet_write":False}
            return packet
        finally:
            reg.close()

def backfill_native(root):
    """Off-feed one-time recovery of ALL old OPEN/CLOSED native position IDs.

    Read original ledgers in SQLite read-only WAL mode, including historical
    records no longer present in capped phone UI lists. No original SQL write.
    """
    from urllib.parse import quote
    totals={}
    with _LOCK:
        reg=Registry(root)
        try:
            for owner,rel in _DB_PATHS.items():
                db=Path(root)/rel
                if not db.is_file():continue
                with sqlite3.connect("file:"+quote(str(db.resolve()),safe="/")+"?mode=ro",
                                     uri=True,timeout=4) as native:
                    native.row_factory=sqlite3.Row
                    native.execute("PRAGMA query_only=ON")
                    found={str(x[0]) for x in native.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    if "positions" not in found:continue
                    receipts={}
                    if "executions" in found:
                        columns={r[1] for r in native.execute("PRAGMA table_info(executions)")}
                        if {"position_id","side","id"}.issubset(columns):
                            for row in native.execute(
                                "SELECT position_id,id FROM executions WHERE UPPER(side)='BUY' ORDER BY id"):
                                if row["position_id"] is not None:
                                    receipts.setdefault("native:"+str(row["position_id"]),str(row["id"]))
                    count=0
                    for row in native.execute("SELECT id FROM positions ORDER BY id"):
                        pid=row["id"]
                        if pid is None:continue
                        key="native:"+str(pid)
                        reg.assign(owner,key,receipts.get(key,""))
                        count+=1
                    totals[owner]=count
            return {"status":"COMPLETE","wallet_position_counts":totals}
        finally:
            reg.close()

_INSTALLED=False
def install(core):
    global _INSTALLED
    if _INSTALLED:return
    import json as _json
    old_hub=core.Runtime.hub_records
    def with_position_uid(self,for_ui=False):
        original=old_hub(self,for_ui=for_ui)
        try:
            source=_json.loads(original) if isinstance(original,str) else original
            mapped=annotate_packet(self.root,source)
            return _json.dumps(mapped,ensure_ascii=False,default=str,
                               separators=(",",":"))
        except Exception as exc:
            self._err("position_uid_v015_attribution",exc)
            # Fail closed for the identity display, not for trading.
            # Losing an optional UID must NEVER hide the original holdings,
            # fills, cash or error history from the dashboard.
            return original if isinstance(original,str) else _json.dumps(
                original,ensure_ascii=False,default=str,separators=(",",":"))
    core.Runtime.hub_records=with_position_uid
    old_start=core.Runtime.start
    def start(self):
        result=old_start(self)
        if getattr(self,"started",False) and not getattr(
                self,"_position_uid_v015_backfill_started",False):
            self._position_uid_v015_backfill_started=True
            def background():
                try:
                    backfill_native(self.root)
                except Exception as exc:
                    self._err("position_uid_v015_backfill",exc)
            threading.Thread(target=background,daemon=True,
                name="artitrade-position-uid-backfill-v015").start()
        return result
    core.Runtime.start=start
    _INSTALLED=True
