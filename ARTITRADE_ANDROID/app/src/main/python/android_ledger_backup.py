"""Read-only complete ARTITRADE phone ledger export.

Every SQLite file is copied through sqlite3.backup (WAL-safe), never by copying
the live database bytes. Broker secrets, authentication, and ephemeral files
are excluded. ZIP + per-file SHA-256 are staged and committed atomically.
No trading signal, order, balance or source file is ever modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path

SCHEMA = "ARTITRADE_PHONE_LEDGER_BACKUP_V1"
MAX_FILES = 2000
MAX_EXPANDED = 16 * 1024**3
MIN_FREE = 128 * 1024**2
SUFFIXES = {".sqlite3", ".sqlite", ".db", ".json", ".jsonl", ".csv", ".parquet"}
PRIVATE = ("credential", "secret", "token", "password", "apikey",
           "api_key", "private_key", "access_key", "refresh_key", "auth",
           "oauth", "keyring", "keystore", "dpapi", "cookie", "pairing")
SKIP_PARTS = {"tmp", "temp", "cache", "__pycache__", "logs", "backups"}


def _sha256(file: Path, check_cancel=None) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            if check_cancel:check_cancel()
            digest.update(block)
    return digest.hexdigest()


def _candidate(root: Path, file: Path) -> bool:
    if file.is_symlink() or not file.is_file():
        return False
    rel = file.relative_to(root)
    # Paper trading capital/slot settings are part of the reproducible research.
    # This exact local JSON contains limits, never broker API credentials.
    if rel.as_posix() in ("market_research_v033.sqlite3",
                          "market_trend_v043.sqlite3",
                          "phone_buy_evidence_v035.sqlite3",
                          "POST_EXIT_HIGH_V053.sqlite3",
                          "execution_decisions_v074.sqlite3"):
        return True
    if rel.as_posix() in (
        "ANDROID_OPERATION_V074.json",
        "ARTITRADE_FRESH_EPOCH_V106.json",
        "execution_entry_plans_v107.sqlite3",
        "entry_failure_audit_v075.sqlite3",
        "entry_failure_audit_v075.json",
        "execution_evidence_v074.sqlite3"):
        return True
    parts = rel.parts
    if not parts or parts[0] not in ("stock", "coin"):
        return False
    if file.suffix.lower() not in SUFFIXES:
        return False
    for part in parts:
        lower = part.lower()
        if part.startswith(".") or lower in SKIP_PARTS:
            return False
        if any(key in lower for key in PRIVATE):
            return False
        if ".pre_pc_import_" in lower or ".pre_close_repair_" in lower:
            return False
    return True


def _copy_checked(source: Path, target: Path, check_cancel=None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, target.open("wb") as dst:
        while True:
            if check_cancel:check_cancel()
            block=src.read(1024 * 1024)
            if not block:break
            dst.write(block)
        dst.flush()
        os.fsync(dst.fileno())
    if source.name == "PROJECTGMO_V1.0_account_state.json":
        try:
            state = json.loads(target.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("GMO account-state JSON invalid; export cancelled") from exc
        if not isinstance(state, dict) or not isinstance(state.get("positions", {}), dict):
            raise ValueError("GMO account-state positions malformed; export cancelled")


def _db_snapshot(source: Path, target: Path, check_cancel=None,
                 on_progress=None) -> dict:
    """Bounded, cancellable SQLite online backup of the ORIGINAL WAL-aware DB.

    A busy source used to leave sqlite3.backup() retrying forever while the UI
    displayed 18%.  Only an intermediate file is opened writable.  Fail with
    a specific diagnostic rather than silently returning an incomplete book.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    uri=source.resolve().as_uri()+"?mode=ro"
    failure=None
    # A running WAL writer can cause SQLite backup to internally restart.
    # Keep one denominator and a non-decreasing high-water UI counter even
    # across retries. Only a fully verified output may report N/N.
    visible_total=None
    visible_done=0
    for attempt in range(2):
        if check_cancel:check_cancel()
        if target.exists():target.unlink()
        began=time.monotonic()
        progressed=began
        previous_remaining=None
        def step(status,remaining,total):
            nonlocal progressed,previous_remaining,visible_total,visible_done
            if check_cancel:check_cancel()
            now=time.monotonic()
            if previous_remaining is None or remaining<previous_remaining:
                progressed=now
            previous_remaining=remaining
            pages=max(1,int(total))
            if visible_total is None:visible_total=pages
            # Separate real SQLite status from the UI high-water mark. Some
            # 0B-heavy WAL sources restart their page counter while writing.
            scaled=int(visible_total*max(0,pages-remaining)/pages)
            visible_done=max(visible_done,min(visible_total-1,scaled))
            if on_progress:on_progress(visible_done,visible_total,int(status))
            if now-began>900 or now-progressed>75:
                raise TimeoutError(
                    "SQLITE_BACKUP_STALLED: "+source.name+
                    " status="+str(status)+" remaining="+str(remaining)+
                    "/"+str(total)+" stalled_sec="+str(round(now-progressed,1)))
        try:
            with closing(sqlite3.connect(uri,uri=True,timeout=3)) as read, \
                 closing(sqlite3.connect(str(target),timeout=3)) as write:
                read.execute("PRAGMA busy_timeout=3000")
                read.execute("PRAGMA query_only=ON")
                write.execute("PRAGMA busy_timeout=3000")
                # Pin a stable read snapshot ONLY for WAL. WAL-mode writers
                # continue committing; rollback-journal writes must not be
                # blocked by a prolonged held reader.
                mode=str(read.execute("PRAGMA journal_mode").fetchone()[0]).lower()
                if mode=="wal":
                    read.execute("BEGIN")
                    read.execute("SELECT count(*) FROM sqlite_master").fetchone()
                try:
                    read.backup(write,pages=256,sleep=0.1,progress=step)
                    if check_cancel:check_cancel()
                    write.commit()
                finally:
                    if mode=="wal":read.rollback()
            failure=None
            break
        except (sqlite3.OperationalError,PermissionError,TimeoutError) as error:
            failure=error
            if check_cancel:check_cancel()
            if attempt==0:time.sleep(.35)
    if failure is not None:
        if target.exists():target.unlink()
        raise RuntimeError("SQLITE_BACKUP_UNAVAILABLE_SOURCE_UNCHANGED: "+
                           source.name+": "+str(failure)) from failure
    with closing(sqlite3.connect(target.resolve().as_uri()+"?mode=ro", uri=True,
                                 timeout=3)) as check:
        if check_cancel:check_cancel()
        integrity=check.execute("PRAGMA quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("SQLite online backup failed integrity check: "+source.name)
        tables=[r[0] for r in check.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        counts={}
        for name in tables:
            if check_cancel:check_cancel()
            escaped=name.replace('"','""')
            counts[name]=int(check.execute('SELECT COUNT(*) FROM "'+escaped+'"').fetchone()[0])
    if check_cancel:check_cancel()
    if on_progress:
        complete=visible_total if visible_total is not None else 1
        on_progress(complete,complete,101)
    return counts


def _research_class(table):
    x=table.lower()
    if ("feature_snapshot" in x or "candle_observation" in x or
            "market_score_observation" in x or "analysis_run" in x or
            "position_observation" in x):
        return "ai_analysis_evidence"
    if ("shadow" in x or "forward" in x or "milestone" in x
            or "book_path" in x):
        return "virtual_research"
    if ("execution" in x or "fill" in x or x in
            ("positions","orders","wallets","accounts")):
        # Table-level source contains both paper and broker fills.
        return "unverified_execution_ledger"
    if ("candidate" in x or "signal" in x or "research" in x
            or "scan" in x or "cohort" in x):
        return "signal_and_decision"
    return "other_original_data"


def _source_catalog(manifest):
    """Every SQLite table count, not a UI 3,000-row preview."""
    rows=[]
    for source, info in manifest["strategy_files"].items():
        if info.get("kind")!="sqlite":
            continue
        for table,count in (info.get("tables") or {}).items():
            rows.append({"source":source,"table":table,"records":count,
                         "kind":_research_class(table),
                         "has_original_sqlite_in_zip":True})
    return {"version":1,"read_only":True,"original_records_not_truncated":True,
            "tables":rows,
            "actual_trade_rows":None,
            "actual_trade_missing_reason":"BROKER_PROVENANCE_NOT_PROVEN_BY_TABLE_NAME",
            "unverified_execution_ledger_rows":sum(x["records"] for x in rows if x["kind"]=="unverified_execution_ledger"),
            "virtual_research_rows":sum(x["records"] for x in rows if x["kind"]=="virtual_research"),
            "signal_decision_rows":sum(x["records"] for x in rows if x["kind"]=="signal_and_decision"),
            "note":"Count is per original table, not deduplicated individual trades."}


def _reports_from_json(raw):
    if raw is None:
        return None
    if isinstance(raw,str):
        raw=json.loads(raw)
    if not isinstance(raw,dict) or len(raw)>8:
        raise ValueError("Research export report manifest invalid")
    files={}
    for name,value in raw.items():
        if not isinstance(name,str) or not name.startswith("analysis/") or \
           name.count("/")!=1 or name.endswith((".sqlite3",".db")) or \
           not name.endswith((".json",".txt",".csv")):
            raise ValueError("Research report must remain under analysis/")
        if not isinstance(value,str):
            raise ValueError("Research report must be UTF-8 text")
        content=value.encode("utf-8")
        if len(content)>8*1024*1024:
            raise ValueError("Research report is too large for inline metadata")
        files[name]=content
    return files


def make_backup(root_path: str, output_path: str, reports_json=None,
                progress_path=None, market_scope=None) -> str:
    """Produce a private verified ZIP BEFORE selecting any Android SAF file."""
    progress=Path(progress_path).resolve() if progress_path else None
    def announce(percent,stage,done=0,total=0,detail=""):
        if progress is None:return
        try:
            tmp=progress.with_suffix(progress.suffix+".tmp")
            tmp.write_text(json.dumps({"percent":int(percent),"stage":str(stage),
                "done":int(done),"total":int(total),"detail":str(detail)[:190],
                "updated_ms":int(time.time()*1000)},ensure_ascii=False),
                encoding="utf-8")
            os.replace(tmp,progress)
        except (OSError,TypeError,ValueError):pass
    announce(4,"원본 원장 확인",detail="원장 파일 검색 중")
    reports=_reports_from_json(reports_json)
    root=Path(root_path).resolve()
    output=Path(output_path).resolve()
    if not root.is_dir() or not output.parent.is_dir():
        raise FileNotFoundError("Phone ledger root or cache directory unavailable")
    if market_scope not in (None,"stock","coin"):
        raise ValueError("INVALID_MARKET_SCOPE")
    sources=[p for p in root.rglob("*") if _candidate(root,p)
             and (market_scope is None or
                  p.relative_to(root).parts[0] == market_scope or
                  len(p.relative_to(root).parts)==1)]
    if not sources:
        raise ValueError("No persisted phone ledger files were found")
    if len(sources)>MAX_FILES:
        raise ValueError("Too many phone ledger files; export stopped without truncation")
    sources.sort(key=lambda x:x.relative_to(root).as_posix())
    announce(8,"원본 원장 확인",0,len(sources),
             f"추출 원본 {len(sources)}개 검증 중")
    original_size=sum(p.stat().st_size for p in sources)
    if original_size>MAX_EXPANDED:
        raise ValueError("Phone ledger exceeds the 16GiB verified-backup safety limit")
    # Snapshot copies and ZIP coexist briefly; fail before writing if unsafe.
    free=shutil.disk_usage(output.parent).free
    if free<original_size*2+MIN_FREE:
        raise OSError("Phone free space insufficient for complete WAL-safe backup")
    marker=Path(str(output)+".partial")
    manifest={"schema":SCHEMA,"created_at_epoch_ms":int(time.time()*1000),
              "source":"ANDROID_PHONE_MAIN","strategy_files":{},"report_files":{},
              "sqlite_backup_mode":"sqlite3.Connection.backup",
              "independent_db_snapshots":True,
              "market_scope":market_scope or "all",
              "file_count":len(sources)}
    if market_scope is not None:
        try:
            epoch_marker=json.loads((root/"ARTITRADE_FRESH_EPOCH_V106.json").read_text("utf8"))
            if epoch_marker.get("state")!="COMMITTED":
                raise ValueError("Epoch not committed")
            manifest["epoch_id"]=str(epoch_marker["epoch_id"])
            manifest["captured_at_utc"]=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat()
        except (OSError,ValueError,KeyError,TypeError) as e:
            raise ValueError("DAILY_BACKUP_REQUIRES_COMMITTED_FRESH_EPOCH") from e
    try:
        with tempfile.TemporaryDirectory(prefix="artitrade_ledger_snapshot_",
                                         dir=str(output.parent)) as stage:
            stage_root=Path(stage)
            for idx,original in enumerate(sources):
                rel=original.relative_to(root).as_posix()
                announce(10+int(48*idx/max(1,len(sources))),"SQLite/WAL 스냅샷",
                         idx,len(sources),rel)
                saved=stage_root/rel
                if original.suffix.lower() in (".sqlite3", ".sqlite", ".db"):
                    rows=_db_snapshot(original,saved)
                    kind="sqlite"
                else:
                    _copy_checked(original,saved)
                    rows=None
                    kind="file"
                manifest["strategy_files"][rel]={
                    "sha256":_sha256(saved),"size":saved.stat().st_size,
                    "kind":kind,"tables":rows
                }
            announce(60,"ZIP 원본 준비",len(sources),len(sources),
                     "스냅샷 완료, 압축 파일 작성")
            if reports is not None:
                # Research ZIP includes the full unchanged original PC+phone DBs,
                # plus a readable table index and the current strategy preview.
                reports["analysis/source_table_catalog.json"]=json.dumps(
                    _source_catalog(manifest),ensure_ascii=False,indent=2
                ).encode("utf-8")
                reports["analysis/README_ko.txt"]=(
                    "ARTITRADE 원본 연구 이력 전체 백업\n"
                    "stock/ 및 coin/ 아래의 각 .sqlite3 파일에 원본 후보·점수·"
                    "매수·매도·가상매매 테이블의 모든 행이 포함됨.\n"
                    "analysis/source_table_catalog.json에서 전략별 테이블 건수를 확인할 수 있음.\n"
                    "stock/ANDROID_RESEARCH_OBSERVATIONS.sqlite3는 휴대폰에서"
                    " 감지한 원본 행의 수정 이력임.\n"
                    "analysis/research_summary.json은 화면 요약으로, 원본 전체 행수와 다를 수 있음.\n"
                    "가상 결과는 원본의 가상 체결/관측 근거가 있을 때만 인정해야 함.\n"
                    "자동매매 조건·주문·원장은 백업 중 변경하지 않음.\n"
                ).encode("utf-8")
                for rel,blob in reports.items():
                    manifest["report_files"][rel]={
                        "sha256":hashlib.sha256(blob).hexdigest(),
                        "size":len(blob),"kind":"report"
                    }
            with zipfile.ZipFile(marker,"w",compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6,allowZip64=True) as z:
                z.writestr("manifest.json",json.dumps(
                    manifest,ensure_ascii=False,separators=(",",":")))
                for idx,rel in enumerate(manifest["strategy_files"]):
                    announce(61+int(27*idx/max(1,len(sources))),
                             "ZIP 압축 기록",idx,len(sources),rel)
                    z.write(stage_root/rel,arcname=rel)
                for rel,blob in (reports or {}).items():
                    z.writestr(rel,blob)
                announce(88,"ZIP 압축 기록",len(sources),len(sources),
                         "정상 ZIP 매니페스트 생성")
            with marker.open("rb+") as durable:
                durable.flush()
                os.fsync(durable.fileno())
            os.replace(marker,output)
        announce(92,"원본 ZIP 검증",len(sources),len(sources),
                 "파일 크기·SHA-256 확인")
        return json.dumps({"ok":True,"schema":SCHEMA,
                           "sha256":_sha256(output),
                           "bytes":output.stat().st_size,
                           "file_count":len(sources),
                           "report_count":len(manifest["report_files"]),
                           "sqlite_count":sum(1 for x in manifest["strategy_files"].values()
                                              if x["kind"]=="sqlite")},
                          ensure_ascii=False)
    finally:
        if marker.exists():
            marker.unlink()


def validate_backup(archive_path: str) -> dict:
    """Streaming verification, useful before any manual restore on a PC."""
    with zipfile.ZipFile(archive_path,"r") as z:
        members=z.namelist()
        if len(members)!=len(set(members)) or "manifest.json" not in members:
            raise ValueError("Duplicate or missing backup members")
        manifest=json.loads(z.read("manifest.json"))
        if manifest.get("schema")!=SCHEMA:
            raise ValueError("Unknown phone ledger backup schema")
        files=manifest.get("strategy_files")
        reports=manifest.get("report_files") or {}
        if not isinstance(files,dict) or not isinstance(reports,dict) or \
           set(files).intersection(reports) or \
           set(members)!=(set(files)|set(reports)|{"manifest.json"}):
            raise ValueError("Backup ZIP files do not match the signed manifest")
        for rel,info in {**files,**reports}.items():
            if rel in files:
                # Root-level research/score journals are legitimate entries
                # produced by _candidate(). Match the writer allowlist exactly.
                root_journals=("market_research_v033.sqlite3",
                               "market_trend_v043.sqlite3",
                               "phone_buy_evidence_v035.sqlite3",
                               "POST_EXIT_HIGH_V053.sqlite3",
                               "execution_decisions_v074.sqlite3",
                               "execution_entry_plans_v107.sqlite3",
                               "entry_failure_audit_v075.sqlite3",
                               "entry_failure_audit_v075.json",
                               "execution_evidence_v074.sqlite3",
                               "ARTITRADE_FRESH_EPOCH_V106.json",
                               "ANDROID_OPERATION_V074.json")
                if (not rel.startswith(("stock/","coin/")) and
                        rel not in root_journals) or ".." in Path(rel).parts:
                    raise ValueError("Unexpected ledger backup path")
            elif not rel.startswith("analysis/") or ".." in Path(rel).parts:
                raise ValueError("Unexpected analysis report path")
            digest=hashlib.sha256()
            count=0
            with z.open(rel,"r") as stream:
                for chunk in iter(lambda: stream.read(1024*1024),b""):
                    digest.update(chunk)
                    count+=len(chunk)
            if count!=info.get("size") or digest.hexdigest()!=info.get("sha256"):
                raise ValueError("Backup member size/SHA mismatch: "+rel)
    return {"ok":True,"files":len(files),"reports":len(reports),
            "schema":SCHEMA,"market_scope":manifest.get("market_scope","all"),
            "epoch_id":manifest.get("epoch_id",""),
            "sha256":_sha256(Path(archive_path)),
            "bytes":Path(archive_path).stat().st_size}


def validate_backup_json(archive_path: str) -> str:
    """Stable JSON return contract for Chaquopy Java's JSONObject parser."""
    return json.dumps(validate_backup(archive_path),ensure_ascii=False)

