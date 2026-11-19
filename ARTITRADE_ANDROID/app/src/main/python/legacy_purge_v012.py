"""User-authorized removal of *PAPER* trades created by pre-11.18 rules.

Scope is strictly stock AT, coin reference, stock/coin MA and stock/coin
MarketTrend. GMO and CLOSE/CW files are NEVER opened for mutation.
Keep new-rule positions, receipts and cash intact. Runs before engine starts.
No transaction is replaced with a synthetic SELL. Any uncertain REAL
broker exposure, malformed journal or unknown schema fails closed for that
position or wallet. Original source is checked by the independent rollback
guard before applying a signed one-time ledger migration.
"""
from __future__ import annotations
from collections import defaultdict,Counter
from decimal import Decimal, ROUND_HALF_UP
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading

KST=dt.timezone(dt.timedelta(hours=9))
TAG="NL1118_LEGACY_PAPER_PURGE_V012"
_LOCK=threading.RLock()
SPECS=(
    ("stock_at","stock/ARTITRADE_D.sqlite3"),
    ("coin_at","coin/base/upbit_paper.sqlite3"),
    ("stock_ma","stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3"),
    ("coin_ma","coin/ma_epoch_v067/upbit_paper.sqlite3"),
    ("stock_trend","stock/trend/market_trend_wallet_v054.sqlite3"),
    ("coin_trend","coin/trend/market_trend_wallet_v054.sqlite3"),
    # Historical pre-v067 MA is not an active strategy, but its prior
    # PAPER records must not reappear in a legacy archive export.
    ("old_stock_ma","stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3"),
    ("old_stock_ma","stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3"),
    ("old_coin_ma","coin/ma/upbit_paper.sqlite3"),
    ("old_coin_ma","coin/ma_epoch_v062/upbit_paper.sqlite3"),
)

class UnsafeLegacyPurge(RuntimeError):
    pass

def _money(v):
    return int(Decimal(str(v)).quantize(Decimal("1"),rounding=ROUND_HALF_UP))

def _dec(v):
    try:
        d=Decimal(str(v))
        return d if d.is_finite() else None
    except (ValueError,TypeError):
        return None

def _tables(c):
    return {str(r[0]) for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}

def _has(c,name):
    return name in _tables(c)

def _rows(c,sql,params=()):
    return [dict(r) for r in c.execute(sql,params).fetchall()]

def _clock():
    return dt.datetime.now(KST).isoformat()

def _marker(c):
    c.execute("""CREATE TABLE IF NOT EXISTS nl1118_legacy_purge_v012(
        tag TEXT PRIMARY KEY, applied_at TEXT NOT NULL,
        deleted_positions INTEGER NOT NULL, deleted_fills INTEGER NOT NULL,
        skipped_positions INTEGER NOT NULL)""")

def _proven_retired_rule(c,kind,p):
    """Require original BUY policy and each SELL provenance, never just date.

    No NL1118 sidecar is NOT affirmative proof of an old-rule purchase.
    Ambiguous post-upgrade buys and sells must remain in native history.
    """
    def canon(value):
        return str(value or '').upper().replace('-', '_')
    def old(value):
        t=canon(value)
        return bool(t and any(marker in t for marker in
            ('OLD_V26','OLD_V061','OLD_V043','OLD_3LEG31',
             'LEGACY_3LEG31','REVERSAL_BEAR','MA_V065_BEAR')))
    def current(value):
        t=canon(value)
        return bool(t and any(marker in t for marker in
            ('NEW_LEGACY_1118','NEW LEGACY(11.18)', 'NL1118',
             'MA_V1118','REVERSAL_UP')))
    receipts=_rows(c,'SELECT * FROM executions WHERE position_id=? ORDER BY id',
                   (int(p['id']),)) if _has(c,'executions') else []
    buys=[x for x in receipts if canon(x.get('side'))=='BUY']
    sells=[x for x in receipts if canon(x.get('side'))=='SELL']
    if not buys:
        return False
    values=[p.get('exit_policy'),p.get('policy'),p.get('strategy')]
    values.extend(x.get('reason') for x in receipts)
    if any(current(v) for v in values):
        return False
    has_old_entry=old(p.get('exit_policy')) or old(p.get('policy')) or any(
        old(x.get('reason')) for x in buys)
    if kind in ('stock_ma','coin_ma'):
        sidecar=_rows(c,'SELECT kind,state_json FROM ma_v067_positions WHERE position_id=?',
                      (int(p['id']),)) if _has(c,'ma_v067_positions') else []
        if sidecar:
            if any(canon(r['kind'])=='REVERSAL_UP' for r in sidecar):
                return False
            if any(canon(r['kind'])=='REVERSAL_BEAR' for r in sidecar):
                has_old_entry=True
    if kind in ('stock_trend','coin_trend'):
        try:
            payload=json.loads(p.get('payload') or '{}')
        except (ValueError,TypeError):
            payload={}
        if any(current(v) for v in payload.values()):
            return False
        has_old_entry=has_old_entry or old(payload.get('entry_signal_policy'))
    # Each historical SELL must have an explicitly pre-NEW-LEGACY reason;
    # an unlabeled/native SELL cannot be silently attributed by timestamp.
    return bool(has_old_entry and all(old(x.get('reason')) for x in sells))


def _old_positions(c,kind):
    positions=_rows(c,"SELECT * FROM positions ORDER BY id") if _has(c,"positions") else []
    # Even archived MA wallets require original BUY and SELL rule evidence;
    # an archive path alone cannot authorize irreversible ledger deletion.
    new=set()
    if kind in ("stock_at","coin_at") and _has(c,"nl1118_reference_positions"):
        new={int(r["position_id"]) for r in _rows(c,
            "SELECT position_id FROM nl1118_reference_positions WHERE policy='NEW_LEGACY_1118'")}
    elif kind in ("stock_ma","coin_ma") and _has(c,"ma_v067_positions"):
        for r in _rows(c,"SELECT position_id,kind,state_json FROM ma_v067_positions"):
            try:state=json.loads(r["state_json"] or "{}")
            except (TypeError,ValueError):continue
            # A 0.0.11 crash can leave new MA policy metadata incomplete.
            # Do not erase a proven genuine V067 upward-entry as "old"
            # merely because the descriptive source field is absent.
            if (r["kind"]=="REVERSAL_UP" and
                (state.get("policy_source")=="NEW LEGACY(11.18).xlsx"
                 or state.get("policy")=="MA_V1118_REVERSAL_UP")):
                new.add(int(r["position_id"]))
    elif kind in ("stock_trend","coin_trend"):
        for row in positions:
            try:payload=json.loads(row["payload"] or "{}")
            except (TypeError,ValueError):continue
            if (payload.get("nl1118_policy")=="NEW_LEGACY_1118" or
                payload.get("entry_signal_policy")=="NL1118_RSI20_24H_BOLL_20_1_5"
                or payload.get("nl1118_sell_intents")):
                new.add(int(row["id"]))
    # Unknown provenance is not a license to delete. A NEW LEGACY BUY can
    # temporarily lack its sidecar after a crash in _on_real_buy/_mark.
    # Date is intentionally not used to classify an OLD execution.
    old=[];protected=[];uncertain=[]
    for p in positions:
        pid=int(p['id'])
        if pid in new:
            protected.append(p)
        elif _proven_retired_rule(c,kind,p):
            old.append(p)
        else:
            uncertain.append(p)
    return old,protected,uncertain

def _broker_uncertain(root,kind,p):
    # Do not erase actual exchange positions or unresolved broker orders.
    if str(p.get("execution_mode") or "PAPER")!="PAPER":
        return True
    if str(p.get("broker_order_id") or ""):
        return True
    journal=Path(root)/"broker"/"live_order_journal.sqlite3"
    if not journal.is_file():return False
    symbol=str(p.get("code") or p.get("market") or p.get("symbol") or "")
    market="coin" if kind.startswith("coin") or kind=="old_coin_ma" else "stock"
    if not symbol:return True
    try:
        with sqlite3.connect(journal.resolve().as_uri()+"?mode=ro",uri=True,
                             timeout=3) as b:
            b.row_factory=sqlite3.Row
            if not _has(b,"live_orders"):return False
            cols={str(x[1]) for x in b.execute("PRAGMA table_info(live_orders)")}
            if not {"market","symbol","status"}.issubset(cols):return True
            rows=_rows(b,"SELECT status FROM live_orders WHERE market=? AND symbol=?",
                       (market,symbol))
            return any(str(x["status"]).upper() in (
                "PREPARED","SENT","PARTIAL","AMBIGUOUS","FILLED",
                "ACKNOWLEDGED","ACCEPTED","UNKNOWN") for x in rows)
    except (sqlite3.Error,OSError):
        return True

def _execution_delta(kind,row):
    side=str(row.get("side") or "").upper()
    qty=_dec(row.get("qty"))
    price=_dec(row.get("price"))
    fee=int(row.get("fees") or 0)
    tax=int(row.get("tax") or 0)
    if side not in ("BUY","SELL") or qty is None or price is None or qty<=0 or price<=0 or fee<0 or tax<0:
        raise UnsafeLegacyPurge("INVALID_NATIVE_EXECUTION_PROOF")
    if kind=="stock_at":
        gross=int(row.get("gross") or 0)
        if gross<=0 or gross!=_money(qty*price):
            raise UnsafeLegacyPurge("UNVERIFIABLE_STOCK_AT_GROSS")
    else:
        gross=_money(qty*price)
    # Cash effect of the ORIGINAL execution. Inverse is used when deleting.
    return (-gross-fee if side=="BUY" else gross-fee-tax)

def _realized_delta(kind,pos,ex):
    sold=[r for r in ex if str(r.get("side")).upper()=="SELL"]
    if kind=="stock_at":
        sold_qty=sum(int(r["qty"]) for r in sold)
        qty0=int(pos["entry_qty"])
        fee=int(pos["entry_fee"] or 0)
        if qty0<=0 or sold_qty>qty0:
            raise UnsafeLegacyPurge("STOCK_AT_SOLD_QTY_INVALID")
        # Original D19 allocated fee in cumulative-share increments.
        allocated=_money(Decimal(fee)*Decimal(sold_qty)/Decimal(qty0))
        return (int(pos["sell_proceeds"] or 0)
                -sold_qty*int(pos["entry_price"])
                -int(pos["sell_costs"] or 0)-allocated)
    col=("net_profit" if kind in ("stock_ma","old_stock_ma")
         else "net_pnl")
    return sum(int(r.get(col) or 0) for r in sold)

def _select_safe(c,root,kind,old):
    selected=[];skipped=[]
    for p in old:
        if _broker_uncertain(root,kind,p):
            skipped.append((p,"BROKER_OR_REAL_EXPOSURE"))
            continue
        if kind in ("stock_trend","coin_trend"):
            # MT54 places an original BUY, then writes NL1118 metadata.
            # An app crash in that short interval can leave a legitimate
            # NEW-LEGACY buy without its sidecar. Refuse that ambiguous
            # post-workbook purchase rather than deleting real new history.
            when=str(p.get("entry_at") or "")
            if when[:10]>="2026-11-18":
                skipped.append((p,"POTENTIALLY_NEW_TREND_BUY_UNMIGRATED"))
                continue
        ex=_rows(c,"SELECT * FROM executions WHERE position_id=? ORDER BY id",
                 (int(p["id"]),)) if _has(c,"executions") else []
        buys=[x for x in ex if str(x.get("side")).upper()=="BUY"]
        if not buys:
            skipped.append((p,"MISSING_ORIGINAL_BUY_RECEIPT"))
            continue
        # No orphan sale, no handwritten net account correction.
        init=_dec(p.get("entry_qty") or p.get("qty0"))
        total=sum((_dec(x["qty"]) or Decimal("0")) for x in buys)
        if init is None or abs(total-init)>Decimal("0.0000001"):
            skipped.append((p,"INCONSISTENT_ORIGINAL_BUY_QUANTITY"))
            continue
        try:
            reverse_cash=-sum(_execution_delta(kind,x) for x in ex)
            realized=_realized_delta(kind,p,ex)
        except (UnsafeLegacyPurge,ValueError,TypeError,KeyError):
            skipped.append((p,"INCONSISTENT_CASH_OR_PROFIT_EVIDENCE"))
            continue
        if kind=="stock_at":
            # Adopted v1.8 positions borrow V19 virtual capital and can
            # move both books/debt. Do not invent a separate refund.
            legacy=(c.execute(
                "SELECT 1 FROM v19_legacy_members WHERE position_id=?",
                (p["id"],)).fetchone()
                if _has(c,"v19_legacy_members") else None)
            if legacy:
                skipped.append((p,"GRANDFATHERED_DUAL_WALLET_RECONCILIATION"))
                continue
        account="wallets" if kind in ("coin_at","coin_ma","old_coin_ma") else "accounts"
        if kind=="stock_at":
            if (_has(c,"v19_members") and c.execute(
                "SELECT 1 FROM v19_members WHERE position_id=?",
                (p["id"],)).fetchone()):
                account="v19_accounts"
        account_row=c.execute(
            'SELECT cash FROM "'+account+'" WHERE tf=?',(p["tf"],)).fetchone() if _has(c,account) else None
        if account_row is None:
            skipped.append((p,"UNKNOWN_NATIVE_ACCOUNT"))
            continue
        selected.append((p,ex,account,reverse_cash,realized))
    return selected,skipped

def _delete_associated(c,kind,pos,ex):
    pid=int(pos["id"])
    parent_tables=(
        "ma_v061_positions","ma_v067_positions","nl1118_reference_positions",
        "nl1118_resting_sells","nl1118_resting_fill_allocations",
        "coin_trade_followup","coin_trade_price_path",
        "coin_position_policy_events","coin_position_support_stop",
        "coin_trade_entry_evidence","coin_recovery_policy_migrations",
        "coin_accumulate_support_events","position_integrity",
        "v19_members","v19_legacy_members"
    )
    existing=_tables(c)
    for name in parent_tables:
        if name not in existing:continue
        if "position_id" not in {str(x[1]) for x in c.execute(
                'PRAGMA table_info("'+name+'")')}:continue
        c.execute('DELETE FROM "'+name+'" WHERE position_id=?',(pid,))
    if "coin_execution_context" in existing:
        c.execute("DELETE FROM coin_execution_context WHERE position_id=?",(pid,))
    c.execute("DELETE FROM executions WHERE position_id=?",(pid,))
    c.execute("DELETE FROM positions WHERE id=?",(pid,))

def _delete_candidates(c,kind,removed,new):
    old_ids={int(p["id"]) for p in removed}
    kept_ids={int(p["id"]) for p in new}
    current=_tables(c)
    if kind=="stock_at" and "signals" in current:
        # Id-based child deletion, never by symbol alone. Leave overlapping
        # signal/reference if another NEW policy position still owns it.
        for p in removed:
            linked=[]
            if "v19_members" in current:
                linked=_rows(c,"SELECT signal_id FROM v19_members WHERE position_id=?",
                             (p["id"],))
            if not linked:
                linked=_rows(c,"SELECT id AS signal_id FROM signals WHERE tf=? AND code=? AND ref_ts=?",
                             (p["tf"],p["code"],p["ref_ts"]))
            for row in linked:
                sid=int(row["signal_id"])
                if "v19_members" in current and c.execute(
                    "SELECT 1 FROM v19_members WHERE signal_id=? LIMIT 1",
                    (sid,)).fetchone():continue
                if c.execute("""SELECT 1 FROM positions p
                    JOIN signals s ON s.tf=p.tf AND s.code=p.code
                       AND s.ref_ts=p.ref_ts
                    WHERE s.id=? LIMIT 1""",(sid,)).fetchone():continue
                for table in (
                    "v19_orders","candidate_snapshots","candidate_events",
                    "candidate_forward","candidate_milestones","candidate_score_repair",
                    "candidate_cohort_decisions","stock_shadow_fills",
                    "stock_shadow_minute_path","stock_shadow_queue_audit",
                    "stock_shadow_trials"):
                    if table not in current:continue
                    col="signal_id"
                    cols={str(x[1]) for x in c.execute(
                        'PRAGMA table_info("'+table+'")')}
                    if col in cols:
                        c.execute('DELETE FROM "'+table+'" WHERE signal_id=?',(sid,))
                c.execute("DELETE FROM signals WHERE id=?",(sid,))
    elif kind in ("coin_at","coin_ma") and "candidates" in current:
        for p in removed:
            if c.execute("""SELECT 1 FROM positions WHERE tf=? AND market=?
                AND ref_at=? LIMIT 1""",(p["tf"],p["market"],p["ref_at"])).fetchone():
                continue
            cand=_rows(c,"SELECT id FROM candidates WHERE tf=? AND market=? AND ref_at=?",
                       (p["tf"],p["market"],p["ref_at"]))
            for row in cand:
                cid=int(row["id"])
                for table in (
                    "candidate_decisions","coin_shadow_book_path",
                    "coin_shadow_fills","coin_shadow_trials","coin_shadow_trial_1m",
                    "coin_scan_decisions","coin_v24_shadow_exit"):
                    if table not in current:continue
                    cols={str(x[1]) for x in c.execute(
                        'PRAGMA table_info("'+table+'")')}
                    if "candidate_id" in cols:
                        c.execute('DELETE FROM "'+table+'" WHERE candidate_id=?',(cid,))
                c.execute("DELETE FROM candidates WHERE id=?",(cid,))
            c.execute("DELETE FROM orders WHERE tf=? AND market=? AND ref_at=?",
                      (p["tf"],p["market"],p["ref_at"]))
    elif kind=="stock_ma" and "candidates" in current:
        for p in removed:
            cid=int(p.get("candidate_id") or 0)
            if not cid or c.execute(
                "SELECT 1 FROM positions WHERE candidate_id=?",(cid,)).fetchone():
                continue
            c.execute("DELETE FROM candidates WHERE id=?",(cid,))
            if "research" in current:
                row=p
                # Research keys use the verified source candidate reference.
                c.execute("""DELETE FROM research WHERE tf=? AND code=?
                    AND ref_ts NOT IN (SELECT ref_ts FROM candidates WHERE code=? AND tf=?)""",
                    (p["tf"],p["code"],p["code"],p["tf"]))

def _apply_one(path,kind,root):
    with sqlite3.connect(str(path),timeout=20) as c:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA busy_timeout=20000")
        c.execute("PRAGMA secure_delete=ON")
        _marker(c)
        if c.execute("SELECT 1 FROM nl1118_legacy_purge_v012 WHERE tag=?",
                     (TAG,)).fetchone():
            return {"kind":kind,"removed":0,"skipped":0,"already":True}
        if not _has(c,"positions") or not _has(c,"executions"):
            return {"kind":kind,"removed":0,"skipped":0,"not_ready":True}
        old,new,uncertain=_old_positions(c,kind)
        # No partial account rebasing that would rewrite real broker receipts
        # co-resident with PAPER records in the same wallet/timeframe.
        columns={str(x[1]) for x in c.execute("PRAGMA table_info(executions)")}
        if "execution_mode" in columns and c.execute("""SELECT 1 FROM executions
            WHERE UPPER(COALESCE(execution_mode,'')) NOT IN ('PAPER','')
            LIMIT 1""").fetchone():
            return {"kind":kind,"removed":0,"skipped":len(old),
                    "reason":"LIVE_EXECUTION_CORESIDENT_FAIL_CLOSED"}
        selected,skipped=_select_safe(c,root,kind,old)
        skipped.extend((p,'BUY_SELL_POLICY_PROVENANCE_UNVERIFIED') for p in uncertain)
        # Original broker or imported wallet evidence is never deleted.
        # We can still remove separately proven PAPER trades in this file.
        removed=[p for p,_,_,_,_ in selected]
        # If an older executable fill has no matching position, there is
        # no provable source cost basis. Never synthesize a cash refund
        # or claim the ledger is fully cleared. Keep it for explicit review.
        ids={int(p["id"]) for p in old+new}
        orphan_count=c.execute("""SELECT COUNT(*) FROM executions
            WHERE position_id IS NOT NULL AND
            position_id NOT IN (SELECT id FROM positions)""").fetchone()[0]
        if kind.startswith("old_") and orphan_count:
            return {"kind":kind,"removed":0,"skipped":int(orphan_count),
                    "reason":"ARCHIVE_ORPHAN_NATIVE_FILL_UNVERIFIED"}
        if not selected and not old and not uncertain and not kind.startswith("old_"):
            if orphan_count:
                return {"kind":kind,"removed":0,"skipped":int(orphan_count),
                        "reason":"ORPHAN_NATIVE_EXECUTION_UNRECONCILABLE"}
            c.execute("INSERT INTO nl1118_legacy_purge_v012 VALUES(?,?,?,?,?)",
                      (TAG,_clock(),0,0,0))
            return {"kind":kind,"removed":0,"skipped":0}
        # Do not silently mark a wallet finished while undecidable old
        # holdings remain. Later launches can retry after reconciliation.
        c.execute("BEGIN IMMEDIATE")
        total_fills=0
        all_execs=_rows(c,"SELECT * FROM executions ORDER BY id")
        adjustment=defaultdict(int)
        profits=defaultdict(int)
        doomed_ids={int(p["id"]) for p in removed}
        for p,ex,account,refund,realized in selected:
            adjustment[(account,str(p["tf"]))]+=refund
            profits[(account,str(p["tf"]))]-=realized
            total_fills+=len(ex)
        # Native cash_after is an audit of account state at each event.
        # Rebase surviving same-account timestamps by removed old cash
        # movements, not by a fabricated BUY/SELL.
        if kind in ("coin_at","coin_ma","stock_trend","coin_trend"):
            offset=defaultdict(int)
            for e in all_execs:
                t=str(e["tf"]);pid=int(e.get("position_id") or 0)
                if pid in doomed_ids:
                    offset[t]-=_execution_delta(kind,e)
                elif "cash_after" in e and offset[t]:
                    c.execute("UPDATE executions SET cash_after=cash_after+? WHERE id=?",
                              (offset[t],int(e["id"])))
        for (account,tf),diff in adjustment.items():
            pnl=profits[(account,tf)]
            target="realized_pnl" if account in ("accounts","v19_accounts") and kind=="stock_at" else "realized"
            if kind in ("stock_trend","coin_trend"):target="realized"
            row=c.execute('SELECT cash,"'+target+'" AS pnl FROM "'+account+'" WHERE tf=?',
                          (tf,)).fetchone()
            if row is None or int(row["cash"])+diff<0:
                raise UnsafeLegacyPurge("NATIVE_CASH_REBASE_NEGATIVE_OR_MISSING")
            c.execute('UPDATE "'+account+'" SET cash=cash+?,"'+target+'"="'+target+'"+? WHERE tf=?',
                      (diff,pnl,tf))
        for p,ex,account,refund,realized in selected:
            _delete_associated(c,kind,p,ex)
        _delete_candidates(c,kind,removed,new)
        if kind.startswith("old_") and not skipped:
            # Fully retired MA archives: remove their historic signal/AI
            # payload as well as actual old PAPER trades, but not settings,
            # schema or independent account seed.
            for t in _tables(c):
                if t.startswith("sqlite_") or t in (
                    "accounts","wallets","meta","nl1118_legacy_purge_v012"):
                    continue
                c.execute('DELETE FROM "'+t+'"')
            account="wallets" if kind=="old_coin_ma" else "accounts"
            if _has(c,account):
                c.execute('UPDATE "'+account+'" SET cash=seed,realized=0')
        if c.execute("SELECT COUNT(*) FROM positions WHERE id IN ("+
                     ",".join("?" for _ in doomed_ids)+")",
                     tuple(doomed_ids)).fetchone()[0] if doomed_ids else 0:
            raise UnsafeLegacyPurge("REMOVED_POSITION_REAPPEARED")
        for row in new+uncertain:
            if c.execute("SELECT 1 FROM positions WHERE id=?",(row["id"],)).fetchone() is None:
                raise UnsafeLegacyPurge("PROTECTED_NL1118_POSITION_REMOVED")
        if not skipped and not orphan_count:
            c.execute("INSERT INTO nl1118_legacy_purge_v012 VALUES(?,?,?,?,?)",
                      (TAG,_clock(),len(removed),total_fills,0))
        return {"kind":kind,"removed":len(removed),
                "fills":total_fills,"skipped":len(skipped)+int(orphan_count),
                "skip_reasons":dict(Counter(reason for _,reason in skipped)),
                "orphan_fills":int(orphan_count),
                "fully_done":not bool(skipped or orphan_count)}

def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def _json_atomic(path,obj):
    path=Path(path)
    data=json.dumps(obj,ensure_ascii=True,sort_keys=True).encode("utf8")
    tmp=path.with_suffix(".tmp")
    with tmp.open("wb") as f:
        f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def run_once(root):
    """One-time signed PAPER cleanup BEFORE engine startup and AFTER epoch is committed.

    The original witness is verified, every touched file is backed up in a
    private transient staging directory, and the anti-rollback witness is
    updated ONLY for listed strategy wallets after exact SQLite validation.
    A crash before checkpoint rebasing restores backups on next boot.
    """
    root=Path(root)
    if root.name!="ARTITRADE_PHONE_MAIN_V070" or root.parent.name!="files":
        raise UnsafeLegacyPurge("EXACT_PHONE_PRIVATE_ROOT_REQUIRED")
    if os.environ.get("ARTITRADE_REAL_MODE")=="1":
        return {"ok":True,"skipped":"REAL_TRADING_CONFIG_NO_AUTOPURGE"}
    from ledger_rollback_guard_v002 import (
        _root,verify_and_checkpoint,_read_checkpoint,_epoch,
        _sources,_measure,_write_checkpoint,CHECKPOINT,SCHEMA)
    _root(root)
    with _LOCK:
        stage=root.parent/".ARTITRADE_NL1118_PURGE_V012_STAGE"
        manifest=stage/"manifest.json"
        finished=root.parent/"ARTITRADE_NL1118_PURGE_V012_DONE.json"
        if finished.is_file():
            return {"ok":True,"status":"ALREADY_CLEANED"}
        if stage.exists() and manifest.exists():
            state=json.loads(manifest.read_text("utf8"))
            if state.get("tag")!=TAG:raise UnsafeLegacyPurge("UNKNOWN_PURGE_STAGE")
            checkpoint=root.parent/CHECKPOINT
            post=state.get("post_witness_sha")
            if post and checkpoint.is_file() and _digest(checkpoint)==post:
                _json_atomic(finished,{"tag":TAG,
                                       "status":state.get("status") or "DONE",
                                       "at":_clock(),"result":state.get("result")})
                shutil.rmtree(stage)
                return {"ok":True,"status":"RECOVERED_FINISHED"}
            # If witness was NOT rebased yet, restore all exact original
            # SQLite snapshot backups before trying the migration again.
            prior=state.get("before_witness_sha")
            if not checkpoint.is_file() or _digest(checkpoint)!=prior:
                raise UnsafeLegacyPurge("PURGE_WITNESS_CHANGED_UNEXPECTEDLY")
            for item in state.get("backups",[]):
                target=root/item["rel"]
                backup=stage/item["name"]
                if not backup.is_file() or _digest(backup)!=item["sha"]:
                    raise UnsafeLegacyPurge("PURGE_RECOVERY_BACKUP_INVALID")
                with sqlite3.connect(str(backup)) as src,sqlite3.connect(str(target)) as dst:
                    src.backup(dst)
            shutil.rmtree(stage)
        verify_and_checkpoint(str(root),require_existing=(root.parent/CHECKPOINT).exists())
        active=[(kind,rel) for kind,rel in SPECS if (root/rel).is_file()]
        if not active:
            _json_atomic(finished,{"tag":TAG,"status":"NO_SOURCE_WALLETS","at":_clock()})
            return {"ok":True,"status":"NO_SOURCE_WALLETS"}
        stage.mkdir(mode=0o700,exist_ok=True)
        backups=[]
        for idx,(kind,rel) in enumerate(active):
            source=root/rel
            if source.is_symlink():raise UnsafeLegacyPurge("SOURCE_WALLET_SYMLINK")
            bname=("wal_"+str(idx)+".sqlite3")
            dest=stage/bname
            with sqlite3.connect(str(source),timeout=15) as src,sqlite3.connect(str(dest)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA quick_check").fetchone()[0]!="ok":
                    raise UnsafeLegacyPurge("BEFORE_PURGE_BACKUP_CORRUPT")
            backups.append({"name":bname,"rel":rel,"sha":_digest(dest)})
        checkpoint=root.parent/CHECKPOINT
        before=_digest(checkpoint)
        _json_atomic(manifest,{"tag":TAG,"before_witness_sha":before,
                               "backups":backups,"post_witness_sha":None})
        results=[]
        try:
            for kind,rel in active:
                results.append(_apply_one(root/rel,kind,root))
            # Independent witness is exact-source baseline for ALL other
            # ledgers. No GMO or CW table/file can be modified or rebased.
            epoch,started=_epoch(root)
            doc=_read_checkpoint(checkpoint,epoch)
            if doc is None:raise UnsafeLegacyPurge("GUARD_WITNESS_MISSING")
            new=dict(doc["tables"])
            touched={rel for _,rel in active}
            for rel,db in sorted(_sources(root).items()):
                for table,after in _measure(db,started).items():
                    key=rel+"#"+table
                    old=doc["tables"].get(key)
                    if rel in touched:
                        new[key]=after
                    elif old is not None and old!=after:
                        raise UnsafeLegacyPurge("UNSCOPED_LEDGER_CHANGED")
            status=("PARTIAL" if any(int(r.get("skipped") or 0)>0
                                  for r in results) else "DONE")
            witness={"schema":SCHEMA,"epoch_id":epoch,
                "tables":new,
                "checked_at_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
                "authorized_cleanup":TAG,
                "cleanup_targets":[rel for _,rel in active]}
            # Publish expected next checkpoint digest DURABLY before the
            # witness swap; a crash between os.replace and status writing
            # can then be identified as a COMPLETE signed migration.
            next_raw=json.dumps(witness,sort_keys=True,ensure_ascii=False,
                separators=(",",":")).encode("utf8")
            expected=hashlib.sha256(next_raw).hexdigest()
            state=json.loads(manifest.read_text("utf8"))
            state.update(post_witness_sha=expected,result=results,status=status)
            _json_atomic(manifest,state)
            _write_checkpoint(checkpoint,witness)
            if _digest(checkpoint)!=expected:
                raise UnsafeLegacyPurge("GUARD_POST_WITNESS_HASH_MISMATCH")
            _json_atomic(finished,{"tag":TAG,"status":status,
                "at":_clock(),"result":results})
            shutil.rmtree(stage)
            return {"ok":True,"status":status,"results":results}
        except Exception:
            # Before independent checkpoint rebase, restore every original
            # wallet backup, including WAL-visible data, atomically per file.
            if _digest(checkpoint)==before:
                for item in backups:
                    with sqlite3.connect(str(stage/item["name"])) as source,\
                         sqlite3.connect(str(root/item["rel"])) as target:
                        source.backup(target)
                shutil.rmtree(stage)
            raise
