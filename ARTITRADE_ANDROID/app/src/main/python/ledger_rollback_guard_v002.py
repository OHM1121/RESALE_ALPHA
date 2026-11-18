"""ARTITRADE PAPER epoch 0.0.2: read-only anti-rollback witness.

Never repairs, migrates, deletes or rewrites any original SQLite ledger.
An independent files/-level checkpoint survives replacing stock/coin DB trees.
Regression or loss of committed fills blocks new engine execution. The sole
allowed checkpoint operation is monotonic advancement after verified reads.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote

ROOT_NAME = "ARTITRADE_PHONE_MAIN_V070"
MARKER = "ARTITRADE_FRESH_EPOCH_V106.json"
CHECKPOINT = "ARTITRADE_LEDGER_GUARD_V002.json"
SCHEMA = "ARTITRADE_MONOTONIC_PAPER_V002"
EPOCH_SCHEMA = "ARTITRADE_FRESH_PAPER_EPOCH_V106"
EXECUTION_TABLES = frozenset({
    "executions", "fills", "paper_fills", "buy_fills", "sell_fills",
    "order_fills", "trade_fills", "paper_executions",
})
IDENTITY = ("id", "execution_id", "fill_id", "order_id", "trade_id",
            "ts", "timestamp", "filled_at", "executed_at", "created_at",
            "entry_at", "buy_at", "sell_at", "side", "code", "symbol",
            "market", "quantity", "qty", "price", "fill_price")
TIME_FIELDS = ("filled_at", "executed_at", "ts", "timestamp", "entry_at",
               "buy_at", "sell_at", "created_at")

class RollbackBlocked(RuntimeError):
    pass

def _root(value):
    root=Path(value)
    if (not root.is_absolute() or not root.is_dir() or root.is_symlink() or
        root.name!=ROOT_NAME or root.parent.name!="files"):
        raise RollbackBlocked("EXACT_PRIVATE_PAPER_ROOT_REQUIRED")
    return root

def _epoch(root):
    try:
        obj=json.loads((root/MARKER).read_text(encoding="utf8"))
        if (obj.get("schema")!=EPOCH_SCHEMA or
            obj.get("state")!="COMMITTED" or not obj.get("epoch_id") or
            not obj.get("completed_at_utc")):
            raise ValueError("invalid epoch")
        started=dt.datetime.fromisoformat(obj["completed_at_utc"].replace("Z","+00:00"))
        if started.tzinfo is None:raise ValueError("epoch timezone missing")
        return str(obj["epoch_id"]),started
    except (OSError,ValueError,TypeError,KeyError) as exc:
        raise RollbackBlocked("FRESH_EPOCH_WITNESS_UNVERIFIABLE") from exc

def _read_checkpoint(path,epoch):
    if not path.exists():return None
    try:
        if path.is_symlink():raise ValueError("checkpoint symlink")
        raw=path.read_bytes()
        if len(raw)>16_000_000:raise ValueError("oversize")
        doc=json.loads(raw.decode("utf8"))
        if (doc.get("schema")!=SCHEMA or doc.get("epoch_id")!=epoch
                or not isinstance(doc.get("tables"),dict)):
            raise ValueError("different epoch/schema")
        return doc
    except (OSError,ValueError,TypeError,UnicodeError) as exc:
        raise RollbackBlocked("ANTI_ROLLBACK_CHECKPOINT_INVALID_OR_REPLACED") from exc

def _datetime(value):
    try:
        if value is None or value=="":return None
        if isinstance(value,(int,float)):
            value=float(value)
            if value>1e12:value/=1000.0
            if value<946684800 or value>4102444800:return None
            return dt.datetime.fromtimestamp(value,dt.timezone.utc)
        val=str(value).strip()
        if val.isdigit():
            if len(val)==14:
                parsed=dt.datetime.strptime(val,"%Y%m%d%H%M%S")
                return parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
            if len(val)>=10:
                return _datetime(int(val))
            return None
        if len(val)==10 and val[4]=="-" and val[7]=="-":
            return None # Original signal date is NOT an execution timestamp.
        date=dt.datetime.fromisoformat(val.replace("Z","+00:00"))
        return date if date.tzinfo else date.replace(
            tzinfo=dt.timezone(dt.timedelta(hours=9)))
    except (OverflowError,ValueError,TypeError):
        return None

def _identity(row):
    if not row:return ""
    result={k:row[k] for k in row.keys()
            if k.lower() in IDENTITY and row[k] is not None}
    if not result:result={"rowid":row["__guard_rowid"]}
    return hashlib.sha256(json.dumps(result,ensure_ascii=False,
        default=str,sort_keys=True,separators=(",",":")).encode("utf8")).hexdigest()

def _measure(db,epoch_time):
    # SQLite mode=ro participates in WAL snapshots. NEVER use immutable=1:
    # that option ignores fresh committed WAL transactions.
    uri="file:"+quote(str(db),safe="/")+"?mode=ro"
    try:
        with sqlite3.connect(uri,uri=True,timeout=1.0) as con:
            con.row_factory=sqlite3.Row
            con.execute("PRAGMA query_only=ON")
            table_names=[str(r[0]) for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            found={}
            for table in table_names:
                if table.lower() not in EXECUTION_TABLES:continue
                if '"' in table:raise RollbackBlocked("UNSAFE_EXECUTION_TABLE_NAME")
                escaped='"'+table+'"'
                count,largest=con.execute(
                    "SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM "+escaped
                ).fetchone()
                info={str(r[1]).lower():str(r[1]) for r in con.execute(
                    "PRAGMA table_info("+escaped+")")}
                keys=[actual for key in IDENTITY if
                      (actual:=info.get(key)) is not None]
                timestamps=[info[x] for x in TIME_FIELDS if x in info]
                cols=(['rowid AS __guard_rowid']+
                      ['"'+c+'"' for c in keys])
                sql="SELECT "+",".join(cols)+" FROM "+escaped
                oldest=(con.execute(sql+" ORDER BY rowid ASC LIMIT 1").fetchone()
                        if count else None)
                newest=(con.execute(sql+" ORDER BY rowid DESC LIMIT 1").fetchone()
                        if count else None)
                # Check oldest AND newest. This flags pre-epoch PAPER fills
                # repopulated from an old ZIP, without classifying past KRX
                # price candles or candidate signal dates as executions.
                for row in (oldest,newest):
                    if row is None:continue
                    for field in timestamps:
                        stamp=_datetime(row[field])
                        if stamp and stamp<epoch_time-dt.timedelta(minutes=2):
                            raise RollbackBlocked(
                                "PREVIOUS_EPOCH_EXECUTION_REAPPEARED")
                found[table.lower()]={
                    "count":int(count),"max_rowid":int(largest),
                    "first_identity":_identity(oldest),
                    "last_identity":_identity(newest),
                }
            return found
    except RollbackBlocked:raise
    except (OSError,sqlite3.DatabaseError) as exc:
        raise RollbackBlocked(
            "EXECUTION_DB_UNREADABLE_STOP_NO_RESTORE:"+type(exc).__name__) from exc

def _sources(root):
    result={}
    for f in root.rglob("*"):
        if f.is_symlink():raise RollbackBlocked("LEDGER_SYMLINK_NO_RESTORE")
        if not f.is_file() or f.suffix.lower() not in (".sqlite3",".sqlite",".db"):
            continue
        rel=f.relative_to(root).as_posix()
        lower=rel.lower()
        if (rel.startswith(("stock/","coin/")) or
            lower.startswith(("execution_","entry_","gmo_"))):
            if "research" in lower or "audit" in lower or "cache" in lower:
                continue
            result[rel]=f
    return result

def _write_checkpoint(path,obj):
    tmp=path.with_name(path.name+".tmp")
    data=json.dumps(obj,sort_keys=True,ensure_ascii=False,
                    separators=(",",":")).encode("utf8")
    try:
        with tmp.open("wb") as out:
            os.chmod(tmp,0o600)
            out.write(data);out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
        # Durable parent directory entry avoids losing the witness after
        # crash/power loss while SQLite WAL remains committed.
        try:
            fd=os.open(str(path.parent),os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
        except OSError:pass
    except OSError as exc:
        raise RollbackBlocked("ANTI_ROLLBACK_WITNESS_NOT_DURABLE") from exc

def verify_and_checkpoint(root,require_existing=False):
    root=_root(root)
    epoch,started=_epoch(root)
    proof=root.parent/CHECKPOINT
    prior=_read_checkpoint(proof,epoch)
    if prior is None and require_existing:
        raise RollbackBlocked("ANTI_ROLLBACK_WITNESS_REMOVED_NO_RESET_ALLOWED")
    old=prior["tables"] if prior else {}
    new=dict(old)
    tracked=0
    sources=_sources(root)
    for rel,path in sorted(sources.items()):
        found=_measure(path,started)
        for table,current in found.items():
            key=rel+"#"+table
            before=old.get(key)
            if before:
                if (current["count"]<before["count"] or
                    current["max_rowid"]<before["max_rowid"]):
                    raise RollbackBlocked("LEDGER_EXECUTIONS_ROLLED_BACK_BLOCK_TRADING")
                if (current["count"]==before["count"] and
                    current["max_rowid"]==before["max_rowid"] and
                    (current["first_identity"]!=before["first_identity"] or
                     current["last_identity"]!=before["last_identity"])):
                    raise RollbackBlocked("LEDGER_SAME_SIZE_DIFFERENT_FILLS")
                if (current["count"]>before["count"] and
                    before["count"]>0 and
                    current["first_identity"]!=before["first_identity"]):
                    raise RollbackBlocked("LEDGER_ORIGINAL_FILL_PREFIX_REPLACED")
            new[key]=current
            tracked+=1
    present=set()
    for rel,path in sorted(sources.items()):
        for tab in _measure(path,started):
            present.add(rel+"#"+tab)
    for key,prev in old.items():
        if prev.get("count",0)>0 and key not in present:
            raise RollbackBlocked("PREVIOUS_EXECUTION_DB_OR_TABLE_DISAPPEARED")
    advanced=(prior is None or new!=old)
    if advanced:
        _write_checkpoint(proof,{
            "schema":SCHEMA,"epoch_id":epoch,
            "tables":new,
            "checked_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        })
    return {"ok":True,"status":"MONOTONIC_PAPER_EPOCH_OK",
            "epoch_id":epoch,"tables_checked":tracked,
            "checkpoint_advanced":advanced}

def verify_json(root,require_existing=False):
    return json.dumps(verify_and_checkpoint(root,require_existing),
                      ensure_ascii=False,sort_keys=True)
