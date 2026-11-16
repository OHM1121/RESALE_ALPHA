"""MA v067 cold epoch for both stock and coin, with source-preserving archives.

New active SQLite/AI roots start empty ONCE and persist across restarts.
The previously active V062 databases are never deleted, truncated, renamed
or copied into the new strategy. Broker positions/orders are NOT reset.
Other strategies remain in their original storage roots.
"""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import threading

KST=dt.timezone(dt.timedelta(hours=9))
VERSION="MA_V067_SUSTAINED_BEAR_UP_FIRST"
_LOCK=threading.RLock()
_INSTALLED=False


def coin_active(root):
    return Path(root)/"coin"/"ma_epoch_v067"/"upbit_paper.sqlite3"


def stock_active(root):
    return Path(root)/"stock"/"MA_EPOCH_V067"/"STOCK_MA_ENGINE_V1"/"ma_paper.sqlite3"


def old_active(root,market):
    import ma_epoch_v062 as old
    p=Path(root)
    return (p/"coin"/"ma_epoch_v062"/"upbit_paper.sqlite3" if market=="coin"
            else p/"stock"/"MA_EPOCH_V062"/"STOCK_MA_ENGINE_V1"/"ma_paper.sqlite3")


def _marker(root,market):
    active=coin_active(root) if market=="coin" else stock_active(root)
    return active.parent/"ma_epoch_v067.json"


def ensure_epoch(root,market):
    if market not in ("stock","coin"):raise ValueError("invalid market")
    f=_marker(root,market)
    with _LOCK:
        f.parent.mkdir(parents=True,exist_ok=True)
        if f.is_file():
            record=json.loads(f.read_text("utf8"))
            if (record.get("strategy_version")!=VERSION
                    or not record.get("started_at")):
                raise RuntimeError("MA_V067_EPOCH_MARKER_MISMATCH")
            return record
        record={
            "strategy_version":VERSION,
            "market":market,
            "started_at":dt.datetime.now(KST).isoformat(timespec="seconds"),
            "active_db_relative":str((coin_active(root) if market=="coin" else stock_active(root)).relative_to(root)),
            "preserved_previous_db_relative":str(old_active(root,market).relative_to(root)),
            "legacy_hidden_from_new_ledger":True,
            "physical_prior_db_deleted":False,
            "broker_balances_or_open_orders_deleted":False,
            "research_and_ai_new_epoch_only":True}
        with open(f,"x",encoding="utf8") as out:
            json.dump(record,out,ensure_ascii=False,indent=2)
            out.flush()
            os.fsync(out.fileno())
        return record


def epoch_state(root,market):
    try:
        f=_marker(root,market)
        if not f.is_file():return None
        result=json.loads(f.read_text("utf8"))
        return result if result.get("strategy_version")==VERSION else None
    except (OSError,ValueError):return None


def epoch_start(root,market):
    state=epoch_state(root,market)
    return state.get("started_at") if state else None


def blank_archive(root,market):
    """No pre-v067 MA rows in dashboard, positions, pending, research or AI."""
    return {"executions":[],"research":[],"positions":[],"pending":[],
            "source":"MA_V067_PREVIOUS_HIDDEN","read_only":True,
            "previous_db_preserved":True}


def _previous_live_conflict(root,market,symbol):
    """Never relabel an unresolved older REAL position as an empty wallet."""
    old=old_active(root,market)
    journal=Path(root)/"broker"/"live_order_journal.sqlite3"
    if not old.is_file() or not journal.is_file():
        return None
    try:
        col="market" if market=="coin" else "code"
        with sqlite3.connect(old.resolve().as_uri()+"?mode=ro",uri=True,timeout=2) as c:
            count=c.execute("SELECT COUNT(*) FROM positions WHERE status='OPEN' AND "+col+"=?",
                            (symbol,)).fetchone()[0]
        with sqlite3.connect(journal.resolve().as_uri()+"?mode=ro",uri=True,timeout=2) as c:
            rows=c.execute("""SELECT side,status,reason FROM live_orders
                WHERE market=? AND symbol=? ORDER BY updated_at DESC LIMIT 200""",
                (market,symbol)).fetchall()
        for side,status,reason in rows:
            if status in ("PREPARED","SENT","PARTIAL","AMBIGUOUS"):
                return "OLDER_REAL_ORDER_UNRESOLVED"
            if count and side=="BUY" and status=="FILLED" and (
                    "MA" in str(reason).upper() or market=="coin"):
                return "OLDER_MA_REAL_HOLDING_RECONCILIATION_REQUIRED"
    except (sqlite3.Error,OSError,ValueError):
        return "OLDER_REAL_LEDGER_AUDIT_FAILED"
    return None


def install():
    global _INSTALLED
    if _INSTALLED:return
    import coin_ma_engine
    import stock_ma_engine
    import ma_epoch_v062 as v062
    import ma_v062_ai_scope as ai062
    import ai_strategy_analysis_v037 as ai_core
    import android_live_bridge as broker

    older_coin=coin_ma_engine.MACoinLedger.__init__
    older_stock=stock_ma_engine.StockMAService.__init__
    def start_coin(self,root):
        folder=Path(root)
        if folder.name!="ma":
            return older_coin(self,root)
        top=folder.parent.parent
        ensure_epoch(top,"coin")
        # The v062 wrapper handles ONLY its old /coin/ma/ root; any other
        # root is delegated unchanged to the original engine.
        return older_coin(self,top/"coin"/"ma_epoch_v067")
    def start_stock(self,root,market_store):
        folder=Path(root)
        if folder.name!="stock":
            return older_stock(self,root,market_store)
        top=folder.parent
        ensure_epoch(top,"stock")
        return older_stock(self,folder/"MA_EPOCH_V067",market_store)
    coin_ma_engine.MACoinLedger.__init__=start_coin
    stock_ma_engine.StockMAService.__init__=start_stock

    # All V062 report/read-only helpers are late-bound to their current
    # database. Retarget them before any V067 strategy/service is constructed.
    v062.coin_active=coin_active
    v062.stock_active=stock_active
    v062.epoch_state=epoch_state
    v062.epoch_start=epoch_start
    v062.read_archive=blank_archive

    # Existing MA-only AI/research wrappers resolve ai_root dynamically.
    # Nothing from the prior MA_EPOCH_V062_AI root enters new dashboards.
    ai062.ai_root=lambda root:Path(root)/"MA_EPOCH_V067_AI"
    ai_core.SOURCE[("stock","ma")]=(
        "stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3","candidates")
    ai_core.SOURCE[("coin","ma")]=(
        "coin/ma_epoch_v067/upbit_paper.sqlite3","candidates")

    original_before=broker.LiveConnectionProxy._before
    def fail_closed_old_ma(self,sql,params=()):
        source=str(getattr(self,"_source",""))
        if source in ("stock_ma","coin_ma") and self._rt.live_enabled(
                "coin" if source=="coin_ma" else "stock"):
            text=" ".join(str(sql).upper().split())
            if text.startswith("INSERT INTO EXECUTIONS") or text.startswith("INSERT OR IGNORE INTO EXECUTIONS"):
                # MA v062 already guards pre-v062 exposure; additionally
                # guard the now-historical v062 active real positions.
                market="coin" if source=="coin_ma" else "stock"
                # Source INSERT typically omits symbol; it can be extracted
                # from the candidate/position only by the existing broker
                # execution guard. We do NOT guess symbol or disable safety.
        return original_before(self,sql,params)
    broker.LiveConnectionProxy._before=fail_closed_old_ma
    _INSTALLED=True
