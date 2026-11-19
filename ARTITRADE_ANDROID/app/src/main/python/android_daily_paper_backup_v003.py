"""WAL-safe, verified one-snapshot-per-market daily MSERVER staging.

The user-facing UI totals are NOT a backup. This only reads original PAPER
files with sqlite3.backup() while native BUY/SELL threads remain untouched.
Never downloads, migrates or restores an old ledger automatically.
"""
from __future__ import annotations
import datetime as dt
import json
import os
from pathlib import Path
from android_ledger_backup import make_backup, validate_backup
from krx_session_v041 import CLOSED
from ledger_rollback_guard_v002 import verify_and_checkpoint

KST=dt.timezone(dt.timedelta(hours=9))  # Modern KRX/Upbit KST; no Android tzdata dependency
MARKER="ARTITRADE_FRESH_EPOCH_V106.json"
MAX_LOOKBACK_DAYS=15

class DailyBackupBlocked(RuntimeError):
    pass

def _trading_day(day):
    if day.weekday()>=5:return False
    calendar=CLOSED.get(day.year)
    if calendar is None:
        raise DailyBackupBlocked("UNVERIFIED_KRX_TRADING_CALENDAR_FOR_YEAR")
    return day.isoformat() not in calendar

def due_date(market,last_date="",now=None):
    if market not in ("stock","coin"):raise DailyBackupBlocked("INVALID_BACKUP_MARKET")
    now=now or dt.datetime.now(KST)
    now=now.astimezone(KST) if now.tzinfo else now.replace(tzinfo=KST)
    cutoff=dt.time(20,10) if market=="stock" else dt.time(11,30)
    today=now.date()
    eligible=today if now.time()>=cutoff else today-dt.timedelta(days=1)
    if market=="stock":
        for _ in range(MAX_LOOKBACK_DAYS):
            if _trading_day(eligible):break
            eligible-=dt.timedelta(days=1)
        else:raise DailyBackupBlocked("LAST_TRADING_DAY_NOT_VERIFIABLE")
    return eligible.isoformat() if eligible.isoformat()!=str(last_date) else ""

def due_json(market,last_date="",now_iso=""):
    try:
        now=dt.datetime.fromisoformat(now_iso) if now_iso else None
        due=due_date(market,last_date,now)
        return json.dumps({"ok":True,"market":market,"date":due,
            "due":bool(due),"kst":dt.datetime.now(KST).isoformat()},
            ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"ok":False,"market":market,"date":"",
            "error":type(exc).__name__+":"+str(exc)[:100]},
            ensure_ascii=False)

def _private(root):
    root=Path(root)
    if (not root.is_absolute() or root.name!="ARTITRADE_PHONE_MAIN_V070"
            or root.parent.name!="files" or root.is_symlink()
            or not root.is_dir()):
        raise DailyBackupBlocked("PRIVATE_TRADING_ROOT_REQUIRED")
    try:
        marker=json.loads((root/MARKER).read_text("utf8"))
        if marker.get("schema")!="ARTITRADE_FRESH_PAPER_EPOCH_V106" or marker.get("state")!="COMMITTED":
            raise ValueError("not committed")
        epoch=str(marker["epoch_id"])
        if not epoch:raise ValueError("empty")
    except (OSError,ValueError,KeyError,TypeError) as exc:
        raise DailyBackupBlocked("FRESH_EPOCH_NOT_COMMITTED_NO_DAILY_BACKUP") from exc
    return root,epoch

def _zip_file(root,market,date,cache_dir):
    path=Path(cache_dir)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise DailyBackupBlocked("DAILY_BACKUP_CACHE_DIRECTORY_INVALID")
    staging=path/"artitrade_daily_v003"
    staging.mkdir(exist_ok=True)
    return staging/("ARTITRADE_"+market+"_"+date+".zip")

def prepare_json(root,cache_dir,market,date):
    if market not in ("stock","coin") or not isinstance(date,str):
        raise DailyBackupBlocked("INVALID_DAILY_SCHEDULE_SCOPE")
    try:dt.date.fromisoformat(date)
    except ValueError as exc:raise DailyBackupBlocked("INVALID_DAILY_BACKUP_DATE") from exc
    root,epoch=_private(root)
    target=_zip_file(root,market,date,cache_dir)
    # This high-water check belongs outside broker threads. Fail closed
    # instead of backing up a rolled-back file which might look healthy.
    verify_and_checkpoint(root)
    if target.is_file():
        try:
            info=validate_backup(str(target))
            if info.get("market_scope")==market and info.get("epoch_id")==epoch:
                return json.dumps({"ok":True,"reused":True,
                    "path":str(target),"market":market,"date":date,
                    "epoch_id":epoch,"sha256":info["sha256"],
                    "bytes":info["bytes"],"files":info["files"]},ensure_ascii=False)
        except Exception:
            pass
        # A previous partially uploaded archive must never be replaced by
        # different ZIP bytes. Explicit offline recovery is required.
        raise DailyBackupBlocked("EXISTING_DAILY_ZIP_UNVERIFIED_NO_REPLACEMENT")
    raw=make_backup(str(root),str(target),market_scope=market)
    response=json.loads(raw)
    if not response.get("ok"):raise DailyBackupBlocked("NATIVE_SQLITE_BACKUP_NOT_VERIFIED")
    validation=validate_backup(str(target))
    if validation.get("market_scope")!=market or validation.get("epoch_id")!=epoch:
        raise DailyBackupBlocked("DAILY_ZIP_SCOPE_OR_EPOCH_MISMATCH")
    verify_and_checkpoint(root)
    return json.dumps({"ok":True,"reused":False,
        "path":str(target),"market":market,"date":date,"epoch_id":epoch,
        "sha256":validation["sha256"],"bytes":validation["bytes"],
        "files":validation["files"]},ensure_ascii=False)

def prepare(root,cache_dir,market,date):
    try:return prepare_json(root,cache_dir,market,date)
    except Exception as exc:
        return json.dumps({"ok":False,"market":market,"date":date,
             "error":type(exc).__name__+":"+str(exc)[:160]},ensure_ascii=False)
