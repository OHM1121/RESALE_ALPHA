"""MA V062: preserve legacy histories, start isolated zero-PnL stock/coin wallets.

An epoch is the first successful V062 installation/startup timestamp. Only
MA account roots are moved; the original/AT, GMO, CLOSE, and independent
market-trend ledgers remain unchanged. No broker cash/balances/holdings or
external open order is ever reset by local SQLite migration.
"""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import threading

_VERSION="MA_V062_NEW_STRATEGY"
_LOCK=threading.RLock()
_INSTALLED=False
KST=dt.timezone(dt.timedelta(hours=9))

def coin_active(root):
    return Path(root)/"coin"/"ma_epoch_v062"/"upbit_paper.sqlite3"

def stock_active(root):
    return Path(root)/"stock"/"MA_EPOCH_V062"/"STOCK_MA_ENGINE_V1"/"ma_paper.sqlite3"

def legacy_db(root,market):
    base=Path(root)
    return (base/"coin"/"ma"/"upbit_paper.sqlite3" if market=="coin"
            else base/"stock"/"STOCK_MA_ENGINE_V1"/"ma_paper.sqlite3")

def active_db(root,market):
    return coin_active(root) if market=="coin" else stock_active(root)

def epoch_file(root,market):
    return active_db(root,market).parent/"ma_epoch_v062.json"

def _iso():
    return dt.datetime.now(KST).isoformat(timespec="seconds")

def _ensure_epoch(root,market):
    target=epoch_file(root,market)
    with _LOCK:
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.is_file():
            try:
                data=json.loads(target.read_text(encoding="utf-8"))
                if data.get("strategy_version")==_VERSION and data.get("started_at"):
                    return data
            except (ValueError,OSError):
                # Corrupt marker must not start a fresh account silently.
                raise RuntimeError("MA_V062_EPOCH_MARKER_INVALID")
            raise RuntimeError("MA_V062_EPOCH_MARKER_UNSUPPORTED")
        state={"strategy_version":_VERSION,"market":market,
               "started_at":_iso(),
               "active_db_relative":str(active_db(root,market).relative_to(root)),
               "preserved_legacy_db_relative":str(legacy_db(root,market).relative_to(root)),
               "real_broker_balance_reset":False,
               "note":"Prior source database remains read-only and has not been erased"}
        # No second first-start marker on a crash/restart. Record before
        # installing or allowing the new independent MA ledger to trade.
        with open(target,"x",encoding="utf8") as f:
            json.dump(state,f,ensure_ascii=False,indent=2)
            f.flush()
            os.fsync(f.fileno())
        return state

def epoch_state(root,market):
    p=epoch_file(root,market)
    if not p.is_file():return None
    try:
        data=json.loads(p.read_text(encoding="utf8"))
        if data.get("strategy_version")==_VERSION:return data
    except (ValueError,OSError):
        return None
    return None

def epoch_start(root,market):
    data=epoch_state(root,market)
    return data.get("started_at") if data else None

def read_archive(root,market):
    """Read-only preview; no old candidate is requeued or made a position."""
    path=legacy_db(root,market)
    empty={"executions":[],"research":[],"positions":[],"path":str(path),
           "source":"MA_LEGACY_BEFORE_V062","read_only":True}
    if not path.is_file():return empty
    try:
        with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,
                             timeout=3) as c:
            c.row_factory=sqlite3.Row
            tables={x[0] for x in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for name,limit in (("executions",2500),("research",1500),
                               ("positions",500)):
                if name not in tables:continue
                rows=[dict(x) for x in c.execute(
                    'SELECT * FROM "'+name+'" ORDER BY id DESC LIMIT ?',(limit,))]
                for row in rows:
                    row.update(ma_epoch="MA_LEGACY_PRE_V062",
                               historical_only=True,strategy="ma")
                    if "frame" not in row:
                        row["frame"]=row.get("tf") or ""
                    if market=="coin":
                        row.setdefault("code",row.get("market"))
                        row.setdefault("ts",row.get("at"))
                    if name=="executions":
                        row["legacy_ma_id"]=row.get("id")
                        row["id"]="MA_LEGACY_"+market+"_"+str(row.get("id"))
                        row["source_position_id"]=(
                            "MA_LEGACY_"+market+"_"+str(row.get("position_id")))
                        row["position_id"]=row["source_position_id"]
                    elif name=="positions":
                        row["legacy_ma_id"]=row.get("id")
                        row["id"]="MA_LEGACY_"+market+"_"+str(row.get("id"))
                    elif name=="research":
                        row["at"]=row.get("ts") or row.get("at")
                        row["reason"]=row.get("verdict") or row.get("reason")
                empty[name]=rows
        return empty
    except (OSError,sqlite3.Error,ValueError):
        return empty

def _prior_live_conflict(root,market,symbol):
    """Fail closed on legacy holdings with independently broker-proven MA risk.

    If the old wallet has an OPEN historical position and proof of its LIVE
    MA buy exists, resetting a phone-only MA wallet must not duplicate a
    real broker position or hide an unresolved live order.
    """
    path=legacy_db(root,market)
    try:
        count=0
        if path.is_file():
            with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                 uri=True,timeout=2) as c:
                symcolumn="market" if market=="coin" else "code"
                count=c.execute("SELECT COUNT(*) FROM positions WHERE status='OPEN' AND "+
                                symcolumn+"=?",(symbol,)).fetchone()[0]
        journal=Path(root)/"broker"/"live_order_journal.sqlite3"
        if not journal.is_file():
            return "OLD_MA_POSITION_UNPROVEN_BROKER_STATE" if count else None
        with sqlite3.connect(journal.resolve().as_uri()+"?mode=ro",
                             uri=True,timeout=2) as c:
            rows=c.execute("""SELECT side,status,reason FROM live_orders
                WHERE market=? AND symbol=? ORDER BY updated_at DESC LIMIT 250""",
                (market,symbol)).fetchall()
            for side,status,reason in rows:
                if side in ("BUY","SELL") and status in (
                        "PREPARED","SENT","AMBIGUOUS","PARTIAL"):
                    # A not-yet-resolved broker instruction may create or
                    # consume actual holdings even when old SQLite says zero.
                    return "UNRESOLVED_BROKER_ORDER_RECONCILIATION_REQUIRED"
                if count and side=="BUY" and status=="FILLED" and (
                        "MA" in str(reason).upper() or market=="coin"):
                    return "OLD_MA_LIVE_POSITION_RECONCILIATION_REQUIRED"
        return None
    except (OSError,sqlite3.Error,ValueError):
        return "OLD_MA_LIVE_AUDIT_FAILED"

def _guard_before(original,self,sql,params=()):
    """No reset can authorize trading against unresolved legacy real fills."""
    import re
    pattern=r"INSERT(?:\s+OR\s+IGNORE|\s+OR\s+REPLACE)?\s+INTO\s+executions\s*\((.*?)\)"
    matched=re.search(pattern," ".join(str(sql).split()),re.I)
    source=str(getattr(self,"_source",""))
    if matched and source in ("stock_ma","coin_ma") and self._rt.live_enabled(
            "stock" if source=="stock_ma" else "coin"):
        keys=[x.strip().strip('"\u0060[]').lower() for x in matched.group(1).split(",")]
        if isinstance(params,(list,tuple)) and len(keys)<=len(params):
            row=dict(zip(keys,params))
            if str(row.get("side") or "").upper()=="BUY":
                symbol=str(row.get("market") or row.get("code") or "")
                market="coin" if source=="coin_ma" else "stock"
                conflict=_prior_live_conflict(self._rt.root,market,symbol)
                if conflict:
                    # Raising BEFORE broker FOK preserves both account and
                    # candidate states; no fake fill is entered.
                    from android_live_bridge import LiveOrderError
                    raise LiveOrderError("MA_V062_"+conflict)
    return original(self,sql,params)

def install():
    global _INSTALLED
    if _INSTALLED:return
    from coin_ma_engine import MACoinLedger
    from stock_ma_engine import StockMAService
    import android_live_bridge as bridge
    previous_coin=MACoinLedger.__init__
    previous_stock=StockMAService.__init__

    def new_coin(self,root):
        p=Path(root)
        if p.name!="ma":
            # Explicit unit-test roots are not silently redirected.
            return previous_coin(self,root)
        parent=p.parent.parent
        _ensure_epoch(parent,"coin")
        return previous_coin(self,p.parent/"ma_epoch_v062")

    def new_stock(self,root,market_store):
        p=Path(root)
        if p.name!="stock":
            return previous_stock(self,root,market_store)
        parent=p.parent
        _ensure_epoch(parent,"stock")
        return previous_stock(self,p/"MA_EPOCH_V062",market_store)

    MACoinLedger.__init__=new_coin
    StockMAService.__init__=new_stock

    previous_before=bridge.LiveConnectionProxy._before
    def source_guard(self,sql,params=()):
        return _guard_before(previous_before,self,sql,params)
    bridge.LiveConnectionProxy._before=source_guard
    _INSTALLED=True
