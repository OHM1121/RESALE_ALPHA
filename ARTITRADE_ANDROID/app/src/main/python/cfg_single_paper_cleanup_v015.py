"""One-position-only, user-authorized CFG PAPER exception; never symbol purge.

Matches the 2026-11-19 incident from issue #92 in the ORIGINAL coin MA 5m
wallet. The user explicitly authorized removal even though a matching
historical executable bid was unavailable. This exception is NOT general.
Only before any trading engine starts, under the independent epoch witness.
"""
from __future__ import annotations
from decimal import Decimal
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading

from ledger_rollback_guard_v002 import (
    _root,_epoch,_read_checkpoint,_sources,_measure,_write_checkpoint,
    CHECKPOINT,SCHEMA,verify_and_checkpoint,
)
from legacy_purge_v012 import (
    _select_safe,_delete_associated,_delete_candidates,
    _rows,_tables,_has,_execution_delta,_json_atomic,_digest,
)

TAG="ARTITRADE_CFG_SINGLE_POSITION_PAPER_PURGE_V015"
_REL="coin/ma_epoch_v067/upbit_paper.sqlite3"
_SYMBOL="KRW-CFG"
_TF="5m"
# The exact user incident was reported before issue #92 was opened:
# 2026-11-19 15:11:21 KST. No later same-symbol trade is authorized.
_REPORTED_BEFORE=dt.datetime(2026,11,19,15,11,21,
                            tzinfo=dt.timezone(dt.timedelta(hours=9)))
_LOCK=threading.RLock()

class UnsafeCFGCleanup(RuntimeError):pass

def _timestamp(value):
    try:
        x=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
        if x.tzinfo is None:return None
        return x
    except (ValueError,TypeError,OverflowError):
        return None

def _is_reported_position(c,p):
    if str(p.get("tf"))!=_TF or str(p.get("market"))!=_SYMBOL:
        return False
    if str(p.get("status")).upper()!="OPEN":return False
    if str(p.get("execution_mode") or "PAPER").upper()!="PAPER":
        return False
    entered=_timestamp(p.get("entry_at"))
    if (entered is None or entered.date()!=_REPORTED_BEFORE.date() or
        entered>=_REPORTED_BEFORE):
        return False
    if Decimal(str(p.get("qty0") or "0"))<=0:return False
    if abs(Decimal(str(p.get("qty") or "0"))-
           Decimal(str(p["qty0"])))>Decimal("0.00000001"):
        return False
    sid=int(p["id"])
    meta=_rows(c,"SELECT kind,state_json FROM ma_v067_positions WHERE position_id=?",
               (sid,)) if _has(c,"ma_v067_positions") else []
    if len(meta)!=1 or str(meta[0].get("kind"))!="REVERSAL_UP":
        return False
    try:state=json.loads(meta[0].get("state_json") or "{}")
    except (ValueError,TypeError):return False
    if state.get("first_sold") not in (None,False,0,"0"):
        return False
    # A later ticker may update seen_above_entry after app upgrade.
    # Never require a missing quote to claim that a SELL receipt existed.
    fills=_rows(c,"SELECT id,side,qty FROM executions WHERE position_id=?",(sid,))
    buys=[x for x in fills if str(x.get("side")).upper()=="BUY"]
    sells=[x for x in fills if str(x.get("side")).upper()=="SELL"]
    return bool(buys and not sells)

def _candidates(c):
    if not {"positions","executions"}.issubset(_tables(c)):
        return [],"ORIGINAL_TABLE_MISSING"
    rows=_rows(c,"SELECT * FROM positions WHERE tf=? AND market=?",
               (_TF,_SYMBOL))
    matches=[]
    for item in rows:
        try:
            if _is_reported_position(c,item):matches.append(item)
        except (ValueError,KeyError,TypeError,ArithmeticError):
            # Unknown entry or amount -> not safe to select as report incident.
            continue
    if not matches:return [],"NO_UNIQUE_REPORTED_CFG_POSITION"
    if len(matches)!=1:return matches,"AMBIGUOUS_MULTIPLE_CFG_POSITIONS"
    return matches,"EXACT_SINGLE_POSITION"

def _apply_one(path,root):
    """Atomic money/row edit; callable only under crash-safe witness staging."""
    with sqlite3.connect(str(path),timeout=15) as c:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=15000")
        matches,reason=_candidates(c)
        if len(matches)!=1:
            return {"status":"SKIPPED","reason":reason,"removed":0}
        p=matches[0];pid=int(p["id"])
        selected,skipped=_select_safe(c,root,"coin_ma",[p])
        if len(selected)!=1 or skipped:
            return {"status":"SKIPPED","reason":"BROKER_OR_NATIVE_RECEIPT_UNVERIFIED",
                    "removed":0}
        pos,ex,account,refund,realized=selected[0]
        if account!="wallets" or str(pos["tf"])!=_TF or not ex:
            raise UnsafeCFGCleanup("COIN_MA_WALLET_ACCOUNT_MISMATCH")
        if realized!=0 or any(str(x.get("side")).upper()!="BUY" for x in ex):
            raise UnsafeCFGCleanup("CFG_BUY_ONLY_SOURCE_RECEIPT_EXPECTED")
        initial_cash=c.execute(
            "SELECT cash,realized FROM wallets WHERE tf=?",(_TF,)).fetchone()
        if initial_cash is None:raise UnsafeCFGCleanup("CFG_WALLET_MISSING")
        # Never mutate unrelated position rows, including other CFG rounds.
        before_others=_rows(c,
           "SELECT * FROM positions WHERE id<>? ORDER BY id",(pid,))
        # Account-cash_after is a running derived balance and must be rebased
        # after deleting this receipt. Price/side/qty/reason remain intact.
        c.execute("BEGIN IMMEDIATE")
        source_exec=_rows(c,"SELECT * FROM executions ORDER BY id")
        correction=0
        for row in source_exec:
            if int(row.get("position_id") or 0)==pid:
                correction-=_execution_delta("coin_ma",row)
            elif correction and row.get("cash_after") is not None:
                c.execute("UPDATE executions SET cash_after=cash_after+? WHERE id=?",
                          (correction,int(row["id"])))
        c.execute("UPDATE wallets SET cash=cash+?,realized=realized-? WHERE tf=?",
                  (refund,realized,_TF))
        _delete_associated(c,"coin_ma",pos,ex)
        # Source-key specific candidate/order cleanup; never by symbol alone.
        others=_rows(c,"SELECT * FROM positions ORDER BY id")
        _delete_candidates(c,"coin_ma",[pos],others)
        after_others=_rows(c,
           "SELECT * FROM positions WHERE id<>? ORDER BY id",(pid,))
        if before_others!=after_others:
            raise UnsafeCFGCleanup("UNRELATED_CFG_OR_OTHER_POSITION_CHANGED")
        if c.execute("SELECT 1 FROM executions WHERE position_id=?",(pid,)).fetchone():
            raise UnsafeCFGCleanup("ORIGINAL_CFG_FILL_NOT_REMOVED")
        if c.execute("SELECT 1 FROM positions WHERE id=?",(pid,)).fetchone():
            raise UnsafeCFGCleanup("ORIGINAL_CFG_POSITION_NOT_REMOVED")
        final_cash=c.execute(
            "SELECT cash,realized FROM wallets WHERE tf=?",(_TF,)).fetchone()
        if (int(final_cash["cash"])!=int(initial_cash["cash"])+refund or
            int(final_cash["realized"])!=int(initial_cash["realized"])-realized):
            raise UnsafeCFGCleanup("CFG_CASH_OR_REALIZED_REBASE_FAILED")
        # Original receipt detail stays only in the private pre-delete DB
        # backup, never in the public GitHub issue or test fixtures.
        return {"status":"DELETED_ONE","removed":1,"fills":len(ex),
                "position_id":pid,"cash_reversal":refund}

def run_once(root):
    root=_root(root)
    stage=root.parent/".ARTITRADE_CFG_V015_PRIVATE_STAGE"
    manifest=stage/"manifest.json"
    finished=root.parent/"ARTITRADE_CFG_V015_DONE.json"
    checkpoint=root.parent/CHECKPOINT
    db=root/_REL
    with _LOCK:
        if finished.is_file():
            obj=json.loads(finished.read_text(encoding="utf8"))
            if obj.get("tag")!=TAG or obj.get("status")!="DELETED_ONE":
                raise UnsafeCFGCleanup("UNKNOWN_CFG_CLEANUP_FINISHED_MARKER")
            return {"status":"ALREADY_DELETED","removed":0}
        if not checkpoint.is_file():
            # Never create a new ledger baseline merely to make a deletion.
            return {"status":"SKIPPED","reason":"ANTI_ROLLBACK_WITNESS_MISSING"}
        if stage.exists():
            if not manifest.is_file():
                raise UnsafeCFGCleanup("CFG_STAGE_UNRECOGNIZED_NO_DELETE")
            old=json.loads(manifest.read_text(encoding="utf8"))
            if old.get("tag")!=TAG:raise UnsafeCFGCleanup("CFG_STAGE_TAG_MISMATCH")
            current=_digest(checkpoint)
            if current==old.get("post_sha") and old.get("post_sha"):
                _json_atomic(finished,{"tag":TAG,"status":"DELETED_ONE",
                                       "at":dt.datetime.now(dt.timezone.utc).isoformat(),
                                       "position_id":old.get("position_id")})
                shutil.rmtree(stage)
                return {"status":"RECOVERED_DELETED_ONE","removed":0}
            if current!=old.get("before_sha"):
                raise UnsafeCFGCleanup("CFG_INDEPENDENT_WITNESS_CHANGED")
            backup=stage/"original.sqlite3"
            if not backup.is_file() or _digest(backup)!=old.get("backup_sha"):
                raise UnsafeCFGCleanup("CFG_PRIVATE_BACKUP_INTEGRITY_FAILED")
            with sqlite3.connect(str(backup)) as src,sqlite3.connect(str(db)) as dst:
                src.backup(dst)
            shutil.rmtree(stage)
        verify_and_checkpoint(str(root),require_existing=True)
        if not db.is_file():
            return {"status":"SKIPPED","reason":"COIN_MA_NATIVE_DB_NOT_FOUND"}
        # Read-only qualification BEFORE any backup or SQL mutation.
        with sqlite3.connect("file:"+str(db.resolve())+"?mode=ro",uri=True) as ro:
            ro.row_factory=sqlite3.Row
            matches,reason=_candidates(ro)
        if len(matches)!=1:
            return {"status":"SKIPPED","reason":reason,"removed":0}
        if db.is_symlink():
            raise UnsafeCFGCleanup("CFG_SOURCE_DB_SYMLINK")
        stage.mkdir(mode=0o700,exist_ok=False)
        backup=stage/"original.sqlite3"
        try:
            with sqlite3.connect(str(db),timeout=15) as src,\
                 sqlite3.connect(str(backup)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                    raise UnsafeCFGCleanup("CFG_ORIGINAL_DB_BACKUP_BAD")
            prior=_digest(checkpoint)
            _json_atomic(manifest,{"tag":TAG,"before_sha":prior,
                                   "backup_sha":_digest(backup),"post_sha":None})
            result=_apply_one(db,root)
            if result.get("status")!="DELETED_ONE":
                # Return source exactly, without checkpoint rebase.
                with sqlite3.connect(str(backup)) as src,\
                     sqlite3.connect(str(db)) as dst:src.backup(dst)
                shutil.rmtree(stage)
                return result
            epoch,started=_epoch(root)
            doc=_read_checkpoint(checkpoint,epoch)
            if doc is None:raise UnsafeCFGCleanup("CFG_CHECKPOINT_NOT_READABLE")
            new=dict(doc["tables"])
            for rel,source in sorted(_sources(root).items()):
                current=_measure(source,started)
                for table,after in current.items():
                    key=rel+"#"+table
                    before=doc["tables"].get(key)
                    if rel==_REL:
                        new[key]=after
                    elif before is not None and before!=after:
                        raise UnsafeCFGCleanup("CFG_UNRELATED_WALLET_CHANGED")
            witness={"schema":SCHEMA,"epoch_id":epoch,"tables":new,
                "checked_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
                "authorized_cleanup":TAG,"cleanup_targets":[_REL]}
            expected=hashlib.sha256(json.dumps(
                witness,sort_keys=True,ensure_ascii=False,
                separators=(",",":")).encode("utf8")).hexdigest()
            state=json.loads(manifest.read_text(encoding="utf8"))
            state.update(post_sha=expected,position_id=result["position_id"])
            _json_atomic(manifest,state)
            _write_checkpoint(checkpoint,witness)
            if _digest(checkpoint)!=expected:
                raise UnsafeCFGCleanup("CFG_CHECKPOINT_POST_WRITE_HASH_MISMATCH")
            _json_atomic(finished,{"tag":TAG,"status":"DELETED_ONE",
                "at":dt.datetime.now(dt.timezone.utc).isoformat(),
                "position_id":result["position_id"],"removed":1,
                "cash_reversal":result["cash_reversal"]})
            shutil.rmtree(stage)
            return result
        except Exception:
            if checkpoint.is_file() and manifest.is_file():
                try:
                    pre=json.loads(manifest.read_text(encoding="utf8"))
                    if _digest(checkpoint)==pre.get("before_sha") and backup.is_file():
                        with sqlite3.connect(str(backup)) as src,\
                             sqlite3.connect(str(db)) as dst:src.backup(dst)
                        shutil.rmtree(stage)
                except (OSError,sqlite3.DatabaseError,ValueError):
                    pass
            raise
