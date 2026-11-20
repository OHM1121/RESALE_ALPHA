"""One-time PAPER ledger reset for everything EXCEPT the GMO (수급매매) ledger.

Owner instruction 2026-11-20: delete every ledger except the GMO one so the
effect of the new 11.20(2) policy can be measured from zero. The trading
engine, strategy code and processes are NOT changed; only ledger rows of the
stock/coin reference-bar (AT), MA, MarketTrend wallets and the close-betting
(CW) wallet are removed, with exact cash / realized-P&L reversal.

Safety contract (same proven machinery as ``legacy_purge_v012``):
* runs BEFORE any engine starts, after the independent anti-rollback witness
  was verified, and only once (a DONE marker prevents any second run);
* GMO files (``stock/PROJECTGMO_*``, ``gmo_*``) are never opened;
* REAL / broker-linked rows are never deleted (fail closed, reported);
* every touched wallet is backed up first and restored if anything fails
  before the witness is rebased; the witness is rebased ONLY for the touched
  wallets, any other ledger change aborts;
* no synthetic BUY/SELL is created, schemas and engine tables are untouched.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import threading
from collections import Counter, defaultdict
from pathlib import Path

import legacy_purge_v012 as lp

TAG = "NL1120_LEDGER_RESET_EXCEPT_GMO_V0130"
KST = dt.timezone(dt.timedelta(hours=9))
_LOCK = threading.RLock()
# Active strategy wallets (kinds understood by legacy_purge_v012) + close-betting.
SPECS = (
    ("stock_at", "stock/ARTITRADE_D.sqlite3"),
    ("coin_at", "coin/base/upbit_paper.sqlite3"),
    ("stock_ma", "stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3"),
    ("coin_ma", "coin/ma_epoch_v067/upbit_paper.sqlite3"),
    ("stock_trend", "stock/trend/market_trend_wallet_v054.sqlite3"),
    ("coin_trend", "coin/trend/market_trend_wallet_v054.sqlite3"),
    ("stock_close", "stock/close/AT_CLOSE_PAPER.sqlite3"),
)
# GMO must never be a wallet target. Asserted in tests and at runtime.
FORBIDDEN_MARKERS = ("gmo",)


class UnsafeLedgerReset(RuntimeError):
    pass


def _clock():
    return dt.datetime.now(KST).isoformat()


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_atomic(path, obj):
    path = Path(path)
    data = json.dumps(obj, ensure_ascii=True, sort_keys=True).encode("utf8")
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _marker(c):
    c.execute("""CREATE TABLE IF NOT EXISTS nl1120_ledger_reset_v0130(
        tag TEXT PRIMARY KEY, applied_at TEXT NOT NULL,
        deleted_positions INTEGER NOT NULL, deleted_fills INTEGER NOT NULL,
        skipped_positions INTEGER NOT NULL)""")


def _paper_only(c):
    """Any non-PAPER execution or position keeps the whole wallet untouched."""
    for table in ("executions", "positions"):
        if not lp._has(c, table):
            continue
        columns = {str(x[1]) for x in c.execute('PRAGMA table_info("%s")' % table)}
        for col in ("execution_mode", "fill_mode", "mode"):
            if col in columns and c.execute(
                    'SELECT 1 FROM "%s" WHERE UPPER(COALESCE(CAST("%s" AS TEXT),\'\'))'
                    " NOT IN ('PAPER','') LIMIT 1" % (table, col)).fetchone():
                return False
        for col in ("broker_order_id", "exchange_order_id"):
            if col in columns and c.execute(
                    'SELECT 1 FROM "%s" WHERE TRIM(COALESCE(CAST("%s" AS TEXT),\'\'))'
                    " NOT IN ('','None') LIMIT 1" % (table, col)).fetchone():
                return False
    return True


def _select_all(c, root, kind, positions):
    """Every position with provable PAPER cash/P&L evidence; skip the rest."""
    selected, skipped = [], []
    for p in positions:
        if lp._broker_uncertain(root, kind, p):
            skipped.append((p, "BROKER_OR_REAL_EXPOSURE"))
            continue
        ex = lp._rows(c, "SELECT * FROM executions WHERE position_id=? ORDER BY id",
                      (int(p["id"]),)) if lp._has(c, "executions") else []
        buys = [x for x in ex if str(x.get("side")).upper() == "BUY"]
        if not buys:
            skipped.append((p, "MISSING_ORIGINAL_BUY_RECEIPT"))
            continue
        init = lp._dec(p.get("entry_qty") or p.get("qty0"))
        total = sum((lp._dec(x["qty"]) or lp.Decimal("0")) for x in buys)
        if init is None or abs(total - init) > lp.Decimal("0.0000001"):
            skipped.append((p, "INCONSISTENT_ORIGINAL_BUY_QUANTITY"))
            continue
        try:
            reverse_cash = -sum(lp._execution_delta(kind, x) for x in ex)
            realized = lp._realized_delta(kind, p, ex)
        except (lp.UnsafeLegacyPurge, ValueError, TypeError, KeyError):
            skipped.append((p, "INCONSISTENT_CASH_OR_PROFIT_EVIDENCE"))
            continue
        if kind == "stock_at" and lp._has(c, "v19_legacy_members") and c.execute(
                "SELECT 1 FROM v19_legacy_members WHERE position_id=?",
                (p["id"],)).fetchone():
            skipped.append((p, "GRANDFATHERED_DUAL_WALLET_RECONCILIATION"))
            continue
        account = "wallets" if kind in ("coin_at", "coin_ma") else "accounts"
        if kind == "stock_at" and lp._has(c, "v19_members") and c.execute(
                "SELECT 1 FROM v19_members WHERE position_id=?", (p["id"],)).fetchone():
            account = "v19_accounts"
        row = (c.execute('SELECT cash FROM "%s" WHERE tf=?' % account, (p["tf"],)).fetchone()
               if lp._has(c, account) else None)
        if row is None:
            skipped.append((p, "UNKNOWN_NATIVE_ACCOUNT"))
            continue
        selected.append((p, ex, account, reverse_cash, realized))
    return selected, skipped


def _apply_wallet(path, kind, root):
    """Reset one strategy wallet in ONE transaction (PAPER only, exact reversal)."""
    with sqlite3.connect(str(path), timeout=20) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=20000")
        c.execute("PRAGMA secure_delete=ON")
        _marker(c)
        if c.execute("SELECT 1 FROM nl1120_ledger_reset_v0130 WHERE tag=?", (TAG,)).fetchone():
            return {"kind": kind, "removed": 0, "skipped": 0, "already": True}
        if not lp._has(c, "positions") or not lp._has(c, "executions"):
            return {"kind": kind, "removed": 0, "skipped": 0, "not_ready": True}
        if not _paper_only(c):
            return {"kind": kind, "removed": 0, "skipped": 0,
                    "reason": "LIVE_OR_BROKER_EVIDENCE_FAIL_CLOSED"}
        positions = lp._rows(c, "SELECT * FROM positions ORDER BY id")
        selected, skipped = _select_all(c, root, kind, positions)
        orphans = c.execute("""SELECT COUNT(*) FROM executions
            WHERE position_id IS NOT NULL AND
            position_id NOT IN (SELECT id FROM positions)""").fetchone()[0]
        c.execute("BEGIN IMMEDIATE")
        adjustment = defaultdict(int)
        profits = defaultdict(int)
        fills = 0
        for p, ex, account, refund, realized in selected:
            adjustment[(account, str(p["tf"]))] += refund
            profits[(account, str(p["tf"]))] -= realized
            fills += len(ex)
        for (account, tf), diff in adjustment.items():
            target = ("realized_pnl" if account in ("accounts", "v19_accounts")
                      and kind == "stock_at" else "realized")
            row = c.execute('SELECT cash FROM "%s" WHERE tf=?' % account, (tf,)).fetchone()
            if row is None or int(row["cash"]) + diff < 0:
                raise UnsafeLedgerReset("NATIVE_CASH_REBASE_NEGATIVE_OR_MISSING")
            c.execute('UPDATE "%s" SET cash=cash+?,"%s"="%s"+? WHERE tf=?'
                      % (account, target, target), (diff, profits[(account, tf)], tf))
        for p, ex, account, refund, realized in selected:
            lp._delete_associated(c, kind, p, ex)
        removed = [p for p, _, _, _, _ in selected]
        lp._delete_candidates(c, kind, removed, [])
        gone = {int(p["id"]) for p in removed}
        if gone and c.execute("SELECT COUNT(*) FROM positions WHERE id IN (%s)"
                              % ",".join("?" for _ in gone), tuple(gone)).fetchone()[0]:
            raise UnsafeLedgerReset("REMOVED_POSITION_REAPPEARED")
        if not skipped and not orphans:
            c.execute("INSERT INTO nl1120_ledger_reset_v0130 VALUES(?,?,?,?,?)",
                      (TAG, _clock(), len(removed), fills, 0))
        return {"kind": kind, "removed": len(removed), "fills": fills,
                "skipped": len(skipped) + int(orphans),
                "skip_reasons": dict(Counter(r for _, r in skipped)),
                "orphan_fills": int(orphans),
                "fully_done": not bool(skipped or orphans)}


def _apply_close(path, root):
    """Close-betting wallet: strategy-keyed wallets with a seed column."""
    with sqlite3.connect(str(path), timeout=20) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=20000")
        c.execute("PRAGMA secure_delete=ON")
        _marker(c)
        if c.execute("SELECT 1 FROM nl1120_ledger_reset_v0130 WHERE tag=?", (TAG,)).fetchone():
            return {"kind": "stock_close", "removed": 0, "skipped": 0, "already": True}
        for table in ("wallets", "positions", "executions"):
            if not lp._has(c, table):
                return {"kind": "stock_close", "removed": 0, "skipped": 0, "not_ready": True}
        wallets = lp._rows(c, "SELECT * FROM wallets")
        execs = lp._rows(c, "SELECT * FROM executions ORDER BY id")
        # Reverse every cash movement; the result must equal the wallet seed,
        # otherwise the history is not reconcilable and nothing is touched.
        net = defaultdict(int)
        try:
            for e in execs:
                gross = lp._money(lp._dec(e["qty"]) * lp._dec(e["price"]))
                fee, tax = int(e.get("fee") or 0), int(e.get("tax") or 0)
                side = str(e["side"]).upper()
                if side not in ("BUY", "SELL") or gross <= 0 or fee < 0 or tax < 0:
                    raise ValueError("bad execution")
                net[str(e["strategy"])] += (-gross - fee if side == "BUY" else gross - fee - tax)
        except (ValueError, TypeError, KeyError):
            return {"kind": "stock_close", "removed": 0, "skipped": len(execs),
                    "reason": "INVALID_CLOSE_EXECUTION_PROOF"}
        for w in wallets:
            if int(w["cash"]) - net[str(w["strategy"])] != int(w["seed"]):
                return {"kind": "stock_close", "removed": 0, "skipped": len(execs),
                        "reason": "CLOSE_CASH_NOT_RECONCILABLE_TO_SEED"}
        pos_ids = [int(r["id"]) for r in c.execute("SELECT id FROM positions")]
        c.execute("BEGIN IMMEDIATE")
        if lp._has(c, "research_position_path"):
            c.execute("DELETE FROM research_position_path")
        c.execute("DELETE FROM executions")
        c.execute("DELETE FROM positions")
        c.execute("UPDATE wallets SET cash=seed, realized=0, updated_at=?", (_clock(),))
        c.execute("INSERT INTO nl1120_ledger_reset_v0130 VALUES(?,?,?,?,?)",
                  (TAG, _clock(), len(pos_ids), len(execs), 0))
        return {"kind": "stock_close", "removed": len(pos_ids), "fills": len(execs),
                "skipped": 0, "fully_done": True}


def _active(root):
    out = []
    for kind, rel in SPECS:
        if any(m in rel.lower() for m in FORBIDDEN_MARKERS):
            raise UnsafeLedgerReset("GMO_NEVER_A_RESET_TARGET")
        if (root / rel).is_file():
            out.append((kind, rel))
    return out


def run_once(root):
    """One-time reset BEFORE engine startup; crash-safe, witness-rebasing."""
    root = Path(root)
    if root.name != "ARTITRADE_PHONE_MAIN_V070" or root.parent.name != "files":
        raise UnsafeLedgerReset("EXACT_PHONE_PRIVATE_ROOT_REQUIRED")
    if os.environ.get("ARTITRADE_REAL_MODE") == "1":
        return {"ok": True, "skipped": "REAL_TRADING_CONFIG_NO_AUTORESET"}
    from ledger_rollback_guard_v002 import (
        _root, verify_and_checkpoint, _read_checkpoint, _epoch,
        _sources, _measure, _write_checkpoint, CHECKPOINT, SCHEMA)
    _root(root)
    with _LOCK:
        stage = root.parent / ".ARTITRADE_NL1120_LEDGER_RESET_V0130_STAGE"
        manifest = stage / "manifest.json"
        finished = root.parent / "ARTITRADE_NL1120_LEDGER_RESET_V0130_DONE.json"
        checkpoint = root.parent / CHECKPOINT
        if finished.is_file():
            return {"ok": True, "status": "ALREADY_RESET"}
        if stage.exists() and manifest.exists():
            state = json.loads(manifest.read_text("utf8"))
            if state.get("tag") != TAG:
                raise UnsafeLedgerReset("UNKNOWN_RESET_STAGE")
            post = state.get("post_witness_sha")
            if post and checkpoint.is_file() and _digest(checkpoint) == post:
                _json_atomic(finished, {"tag": TAG, "status": state.get("status") or "DONE",
                                        "at": _clock(), "result": state.get("result")})
                shutil.rmtree(stage)
                return {"ok": True, "status": "RECOVERED_FINISHED"}
            if not checkpoint.is_file() or _digest(checkpoint) != state.get("before_witness_sha"):
                raise UnsafeLedgerReset("RESET_WITNESS_CHANGED_UNEXPECTEDLY")
            for item in state.get("backups", []):
                backup = stage / item["name"]
                if not backup.is_file() or _digest(backup) != item["sha"]:
                    raise UnsafeLedgerReset("RESET_RECOVERY_BACKUP_INVALID")
                with sqlite3.connect(str(backup)) as src, sqlite3.connect(str(root / item["rel"])) as dst:
                    src.backup(dst)
            shutil.rmtree(stage)
        verify_and_checkpoint(str(root), require_existing=checkpoint.exists())
        active = _active(root)
        if not active:
            _json_atomic(finished, {"tag": TAG, "status": "NO_SOURCE_WALLETS", "at": _clock()})
            return {"ok": True, "status": "NO_SOURCE_WALLETS"}
        stage.mkdir(mode=0o700, exist_ok=True)
        backups = []
        for idx, (kind, rel) in enumerate(active):
            source = root / rel
            if source.is_symlink():
                raise UnsafeLedgerReset("SOURCE_WALLET_SYMLINK")
            name = "wal_%d.sqlite3" % idx
            with sqlite3.connect(str(source), timeout=15) as src, \
                    sqlite3.connect(str(stage / name)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise UnsafeLedgerReset("BEFORE_RESET_BACKUP_CORRUPT")
            backups.append({"name": name, "rel": rel, "sha": _digest(stage / name)})
        before = _digest(checkpoint)
        _json_atomic(manifest, {"tag": TAG, "before_witness_sha": before,
                                "backups": backups, "post_witness_sha": None})
        results = []
        try:
            for kind, rel in active:
                path = root / rel
                results.append(_apply_close(path, root) if kind == "stock_close"
                               else _apply_wallet(path, kind, root))
            epoch, started = _epoch(root)
            doc = _read_checkpoint(checkpoint, epoch)
            if doc is None:
                raise UnsafeLedgerReset("GUARD_WITNESS_MISSING")
            new = dict(doc["tables"])
            touched = {rel for _, rel in active}
            for rel, db in sorted(_sources(root).items()):
                for table, after in _measure(db, started).items():
                    key = rel + "#" + table
                    old = doc["tables"].get(key)
                    if rel in touched:
                        new[key] = after
                    elif old is not None and old != after:
                        raise UnsafeLedgerReset("UNSCOPED_LEDGER_CHANGED")
            status = ("PARTIAL" if any(int(r.get("skipped") or 0) > 0 or r.get("reason")
                                       for r in results) else "DONE")
            witness = {"schema": SCHEMA, "epoch_id": epoch, "tables": new,
                       "checked_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "authorized_cleanup": TAG,
                       "cleanup_targets": [rel for _, rel in active]}
            expected = hashlib.sha256(json.dumps(
                witness, sort_keys=True, ensure_ascii=False,
                separators=(",", ":")).encode("utf8")).hexdigest()
            state = json.loads(manifest.read_text("utf8"))
            state.update(post_witness_sha=expected, result=results, status=status)
            _json_atomic(manifest, state)
            _write_checkpoint(checkpoint, witness)
            if _digest(checkpoint) != expected:
                raise UnsafeLedgerReset("GUARD_POST_WITNESS_HASH_MISMATCH")
            _json_atomic(finished, {"tag": TAG, "status": status, "at": _clock(),
                                    "result": results})
            shutil.rmtree(stage)
            return {"ok": True, "status": status, "results": results}
        except Exception:
            # Before the witness swap restore every wallet exactly as it was.
            if _digest(checkpoint) == before:
                for item in backups:
                    with sqlite3.connect(str(stage / item["name"])) as source, \
                            sqlite3.connect(str(root / item["rel"])) as target:
                        source.backup(target)
                shutil.rmtree(stage)
            raise


def run_json(root):
    return json.dumps(run_once(root), ensure_ascii=False, sort_keys=True)
