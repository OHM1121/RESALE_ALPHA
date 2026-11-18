"""ARTITRADE v106: deliberately offline fresh PAPER-ledger epoch.

Source-only preparation. Nothing imports or invokes this automatically.
Must be called once, before Python/Java engines or sockets start, by a
separately approved future app action. NEVER run on active phone SQLite/WAL.
Existing eight strategy modules, policies, credentials and capital settings
are not edited. On success the marker blocks old PC-import archives.

No APK, network calls, broker orders or automatic deletion on import.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import uuid
from pathlib import Path

ROOT_NAME = "ARTITRADE_PHONE_MAIN_V070"
MARKER = "ARTITRADE_FRESH_EPOCH_V106.json"
SCHEMA = "ARTITRADE_FRESH_PAPER_EPOCH_V106"
CONFIRM = "DELETE_PREVIOUS_PAPER_RECORDS_AND_RESEARCH"
PRESERVED = frozenset({"ANDROID_OPERATION_V074.json", MARKER})
SENSITIVE = ("secret", "credential", "token", "password", "apikey", "api_key",
             "private_key", "access_key", "refresh_key", "auth", "oauth",
             "keyring", "keystore", "dpapi", "cookie", "pairing")
DATA_EXTS = (".sqlite3", ".sqlite", ".db", ".json", ".jsonl",
             ".csv", ".parquet", ".sqlite3-wal", ".sqlite3-shm",
             ".sqlite-wal", ".sqlite-shm", ".db-wal", ".db-shm",
             ".db-journal", ".sqlite3-journal")


class ResetBlocked(RuntimeError):
    pass


def _root(value):
    path = Path(value).expanduser()
    # Refuse arbitrary disk paths, repository trees, relative paths or symlinks.
    if (not path.is_absolute() or path.name != ROOT_NAME or
            path.parent.name != "files" or path.is_symlink() or
            not path.is_dir()):
        raise ResetBlocked("EXACT_ANDROID_PRIVATE_FILES_ROOT_REQUIRED")
    return path


def _is_sensitive(rel):
    return any(word in part.lower() for part in rel.parts for word in SENSITIVE)


def _history(rel):
    if rel.as_posix() in PRESERVED or _is_sensitive(rel):
        return False
    name = rel.name.lower()
    if ".pre_pc_import_" in name or ".pre_close_repair_" in name:
        return True
    if not name.endswith(DATA_EXTS):
        return False
    if rel.parts[0] in ("stock", "coin"):
        return True
    # The root contains cross-market execution/research databases and
    # operational snapshots, but may also contain the user's settings.
    if len(rel.parts) == 1:
        return name.startswith((
            "market_", "phone_buy_", "post_exit_", "execution_", "recovery_",
            "entry_", "gmo_", "artitrade_", "android_error_", "ai_",
            "stock_", "coin_"))
    return False


def _scan(path):
    delete = []
    preserve = []
    for current in path.rglob("*"):
        if current.is_symlink():
            raise ResetBlocked("SYMLINK_IN_PHONE_LEDGER_TREE")
        if not current.is_file():
            continue
        rel = current.relative_to(path)
        if rel.as_posix() in PRESERVED:
            preserve.append(rel.as_posix())
        elif _history(rel):
            delete.append((rel.as_posix(), current.stat().st_size))
        else:
            preserve.append(rel.as_posix())
    delete.sort()
    preserve.sort()
    return delete, preserve


def _sqlite_ro(db):
    # An unreadable, busy or corrupt execution database is NOT authorization
    # to erase it. Any such case blocks the reset for offline inspection.
    from urllib.parse import quote
    uri = "file:" + quote(str(db), safe="/") + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2)


def _check_real_order_proof(root, plan):
    names = {item for item, _ in plan}
    possible = [root / name for name in names if name.endswith((".sqlite3", ".sqlite", ".db"))]
    journal = root / "broker" / "live_order_journal.sqlite3"
    if journal.is_file() and journal not in possible:
        possible.append(journal)
    for db in possible:
        try:
            with _sqlite_ro(db) as con:
                tables = {row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if db == journal:
                    for name in tables:
                        if name.lower() in ("orders", "fills", "executions",
                                            "broker_orders", "live_orders"):
                            row = con.execute('SELECT 1 FROM "' + name + '" LIMIT 1').fetchone()
                            if row:
                                raise ResetBlocked("LIVE_BROKER_JOURNAL_NOT_EMPTY")
                for table in tables.intersection(
                    ("positions", "executions", "orders", "fills", "broker_orders")):
                    cols = {r[1] for r in con.execute(
                        'PRAGMA table_info("' + table + '")')}
                    for column in ("execution_mode", "fill_mode", "mode"):
                        if column in cols and con.execute(
                            'SELECT 1 FROM "' + table + '" WHERE UPPER(CAST("' +
                            column + '" AS TEXT)) LIKE ? LIMIT 1', ("%LIVE%",)
                        ).fetchone():
                            raise ResetBlocked("REAL_EXECUTION_MODE_PRESENT:" + db.name)
                    for column in ("broker_order_id", "exchange_order_id"):
                        if column in cols and con.execute(
                            'SELECT 1 FROM "' + table + '" WHERE TRIM(CAST("' +
                            column + '" AS TEXT)) NOT IN (?,?) LIMIT 1', ("", "None")
                        ).fetchone():
                            raise ResetBlocked("BROKER_ORDER_PROVENANCE_PRESENT:" + db.name)
        except ResetBlocked:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise ResetBlocked(
                "UNVERIFIABLE_SQLITE_BLOCKS_RESET:" + db.name + ":" +
                type(exc).__name__) from exc


def _marker(path):
    name = path / MARKER
    if not name.exists():
        return None
    try:
        value = json.loads(name.read_text(encoding="utf8"))
        if (value["schema"] != SCHEMA or value["state"] not in
                ("PREPARING", "COMMITTED") or not value["epoch_id"]):
            raise ValueError("invalid marker")
        return value
    except (KeyError, ValueError, OSError, TypeError) as exc:
        raise ResetBlocked("FRESH_EPOCH_MARKER_INVALID") from exc


def _save_marker(path, data):
    target = path / MARKER
    tmp = path / (MARKER + ".tmp")
    with tmp.open("w", encoding="utf8") as stream:
        json.dump(data, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, target)


def preview(root):
    """Read-only inventory. No account, DB, secret or archive changes."""
    path = _root(root)
    existing = _marker(path)
    if existing and existing["state"] == "COMMITTED":
        return {"status": "ALREADY_COMMITTED", "epoch_id": existing["epoch_id"],
                "delete_count": 0, "delete_bytes": 0}
    candidates, retained = _scan(path)
    return {"status": "PREVIEW_ONLY", "delete_count": len(candidates),
            "delete_bytes": sum(size for _, size in candidates),
            "files_to_delete": [name for name, _ in candidates],
            "files_preserved": retained,
            "requires_engine_stopped": True, "requires_live_disarmed": True,
            "automatic_reset": False}


def reset(root, *, confirmation, engine_stopped=False,
          live_orders_disarmed=False):
    """Explicit, one-shot, offline PAPER reset; NEVER called at startup.

    A PREPARING marker makes interrupted resets fail closed at Android startup.
    Rerun with the same explicit approval to finish; COMMITTED never erases
    newly accumulated records on a second invocation.
    """
    path = _root(root)
    if (confirmation != CONFIRM or engine_stopped is not True or
            live_orders_disarmed is not True):
        raise ResetBlocked("EXPLICIT_OFFLINE_PAPER_RESET_CONFIRMATION_REQUIRED")
    existing = _marker(path)
    if existing and existing["state"] == "COMMITTED":
        return {"status": "ALREADY_COMMITTED", "epoch_id": existing["epoch_id"]}
    candidates, _ = _scan(path)
    _check_real_order_proof(path, candidates)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    record = existing or {"schema": SCHEMA, "epoch_id": str(uuid.uuid4()),
                          "created_at_utc": now, "mode": "PAPER_ONLY"}
    record["state"] = "PREPARING"
    record["last_prepare_at_utc"] = now
    record["planned_files"] = len(candidates)
    _save_marker(path, record)
    for rel, _size in candidates:
        target = path / rel
        if target.is_symlink():
            raise ResetBlocked("FILE_CHANGED_TO_SYMLINK_DURING_RESET")
        target.unlink()
    for dirname in ("stock", "coin"):
        base = path / dirname
        if base.is_dir():
            for directory in sorted(
                (v for v in base.rglob("*") if v.is_dir()),
                key=lambda v: len(v.parts), reverse=True):
                if not any(directory.iterdir()):
                    directory.rmdir()
            if not any(base.iterdir()):
                base.rmdir()
    remaining, _ = _scan(path)
    if remaining:
        raise ResetBlocked("HISTORICAL_FILES_REMAIN_RESET_INCOMPLETE")
    record.update(state="COMMITTED", completed_at_utc=dt.datetime.now(
        dt.timezone.utc).isoformat(timespec="seconds"),
        removed_files=len(candidates),
        removed_bytes=sum(size for _, size in candidates),
        pc_auto_import_blocked=True)
    _save_marker(path, record)
    return {"status": "COMMITTED", "epoch_id": record["epoch_id"],
            "removed_files": record["removed_files"],
            "removed_bytes": record["removed_bytes"],
            "pc_auto_import_blocked": True}
