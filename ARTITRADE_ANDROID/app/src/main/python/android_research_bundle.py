"""Scoped, verifiable, read-only research bundle split into independently valid ZIPs.

The original EXE/phone SQLite files are snapshotted with SQLite backup, so
WAL rows are included and neither orders nor live databases are modified.
Each ZIP part is <=500,000,000 bytes (default). Large originals are stored as
lossless binary fragments with a repeated index for PC reassembly.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from android_ledger_backup import (
    MAX_EXPANDED, MAX_FILES, MIN_FREE, _candidate, _copy_checked,
    _db_snapshot, _sha256, _source_catalog
)

SCHEMA="ARTITRADE_RESEARCH_BUNDLE_V1"
DEFAULT_PART_BYTES=500_000_000


class ExportCancelled(RuntimeError):
    """An explicit user cancellation, never a failed broker trade."""
    pass


def _copy_piece(source: Path, offset: int, size: int, out, check_cancel=None) -> None:
    with source.open("rb") as stream:
        stream.seek(offset)
        left=size
        while left:
            if check_cancel:check_cancel()
            chunk=stream.read(min(left,1024*1024))
            if not chunk:
                raise IOError("Research source changed during snapshot staging")
            out.write(chunk)
            left-=len(chunk)


def _put_plan_item(pieces,capacity,used,record,size):
    """Allocate exact raw bytes; each independent ZIP reserves room for its index."""
    remaining=size
    next_part=len(pieces)-1
    if size<=capacity and used[next_part]+size>capacity:
        pieces.append([]);used.append(0);next_part+=1
    part_index=0
    while remaining>0 or (size==0 and part_index==0):
        if used[next_part]>=capacity and remaining>0:
            pieces.append([]);used.append(0);next_part+=1
        count=min(remaining,capacity-used[next_part]) if remaining else 0
        if count<=0 and remaining>0:
            raise ValueError("Research ZIP part capacity exhausted")
        entry=(record["path"] if size<=capacity else
               f'fragments/{record["serial"]:05d}/{part_index:05d}.bin')
        segment={"part":next_part+1,"entry":entry,
                 "offset":size-remaining,"size":count}
        record["fragments"].append(segment)
        pieces[next_part].append((record["path"],segment))
        used[next_part]+=count
        remaining-=count
        part_index+=1
    return pieces,used



def _deflated_bytes(source: Path, offset: int, size: int,
                    check_cancel=None) -> int:
    """Estimate exactly the same raw DEFLATE level as the final error ZIP.

    Streaming and cancellable: large coin WAL snapshots are never materialized
    in memory. The caller keeps a 4MB per-ZIP allowance for ZIP/index headers.
    """
    import zlib
    obj=zlib.compressobj(level=1,method=zlib.DEFLATED,wbits=-15)
    total=0
    with source.open("rb") as stream:
        stream.seek(offset)
        remaining=size
        while remaining:
            if check_cancel:check_cancel()
            data=stream.read(min(1024*1024,remaining))
            if not data:raise IOError("Snapshot changed during compression planning")
            remaining-=len(data)
            total+=len(obj.compress(data))
    if check_cancel:check_cancel()
    return total+len(obj.flush())


def _put_compressed_error_item(pieces,used,capacity,record,source,limit,check_cancel=None):
    """Pack independently decodable ZIP pieces by measured compressed bytes.

    This avoids splitting a 2GB highly-compressible database into unnecessary
    500MB RAW parts: only the actual resulting compressed part size matters.
    Each source fragment has the same lossless index schema as RESEARCH bundles.
    """
    size=record["size"]
    raw_chunk=min(100_000_000,max(16_384,capacity//2))
    offset=0
    index=0
    while offset<size or (size==0 and index==0):
        amount=min(raw_chunk,size-offset) if size else 0
        predicted=_deflated_bytes(source,offset,amount,check_cancel)
        # Header+descriptor+entry central-dir overhead is covered by reserve.
        if predicted>capacity:
            raise ValueError("Compressed fragment exceeds part capacity")
        if used[-1]+predicted>capacity:
            pieces.append([]);used.append(0)
        entry=(record["path"] if size<=raw_chunk else
               f'fragments/{record["serial"]:05d}/{index:05d}.bin')
        fragment={"part":len(pieces),"entry":entry,"offset":offset,
                  "size":amount}
        record["fragments"].append(fragment)
        pieces[-1].append((record["path"],fragment))
        used[-1]+=predicted
        offset+=amount
        index+=1
    return pieces,used


def make_bundle(root_path, output_dir, scope, max_part_bytes=DEFAULT_PART_BYTES,
                progress_path=None, bundle_kind="research", native_errors_json=None,
                cancel_path=None):
    progress=Path(progress_path).resolve() if progress_path else None
    cancel=Path(cancel_path).resolve() if cancel_path else None
    def check_cancel():
        if cancel is not None and cancel.is_file():
            raise ExportCancelled("ARTITRADE_ERROR_EXPORT_CANCELLED")
    def announce(pct,stage,done=0,total=0,detail=''):
        check_cancel()
        if progress is None:return
        try:
            tmp=progress.with_suffix(progress.suffix+'.tmp')
            tmp.write_text(json.dumps({'percent':int(pct),'stage':stage,
                'done':int(done),'total':int(total),
                'detail':str(detail)[:170],
                'updated_ms':int(time.time()*1000)},ensure_ascii=False),
                encoding='utf8')
            os.replace(tmp,progress)
        except (OSError,TypeError,ValueError):pass
    if bundle_kind not in ("research","errors"):
        raise ValueError("Bundle kind must be research or errors")
    announce(4,'오류원장 검사' if bundle_kind=="errors" else '연구원장 검사',
             detail='스냅샷 대상 조사')
    if scope not in ("stock","coin","all"):
        raise ValueError("Research scope must be stock, coin or all")
    max_part_bytes=int(max_part_bytes)
    if not 16_384<=max_part_bytes<=DEFAULT_PART_BYTES:
        raise ValueError("Research ZIP part size must be 16KB..500MB")
    root=Path(root_path).resolve()
    folder=Path(output_dir).resolve()
    if not root.is_dir() or not folder.is_dir():
        raise FileNotFoundError("Phone ledger or export cache missing")
    # Both market scopes retain their own AI journal plus full market-score
    # history, needed to reconstruct buy-to-sell interval averages.
    # The RSI20 coordinator journal is shared by both markets and contains
    # manual mode, stop-day blocks and real-vs-shadow signal decisions.
    shared_scores={"market_research_v033.sqlite3",
                   "market_trend_v043.sqlite3",
                   "execution_decisions_v074.sqlite3"}
    if scope in ("stock","all"):
        shared_scores.add("phone_buy_evidence_v035.sqlite3")
    originals=[]
    for source in root.rglob("*"):
        check_cancel()
        if ((_candidate(root,source) and (
                scope=="all" or source.relative_to(root).parts[0]==scope)) or
                (source.is_file() and source.parent==root and source.name in shared_scores)):
            originals.append(source)
    originals.sort(key=lambda p:p.relative_to(root).as_posix())
    if not originals:raise ValueError("No original research ledger files for "+scope)
    announce(8,'연구원장 검사',0,len(originals),'원본 '+str(len(originals))+'개')
    if len(originals)>MAX_FILES:raise ValueError("Too many original research files")
    total=sum(p.stat().st_size for p in originals)
    if total>MAX_EXPANDED:raise ValueError("Research source exceeds 16GiB safety cap")
    # All source snapshots must fit; DEFLATE output may occupy only a fraction
    # of raw SQLite bytes. The old 2x-raw preflight falsely rejected large
    # COIN/ALL exports even when their compressed archives fit on the phone.
    # Actual ZIP streaming still fails closed if available space runs out.
    if shutil.disk_usage(folder).free < total+MIN_FREE:
        raise OSError("EXPORT_SNAPSHOT_SPACE: need at least "+
                      str(total+MIN_FREE)+" free bytes for original snapshots")

    prefix=("ARTITRADE_"+scope.upper()+
            ("_ERROR_HISTORY_" if bundle_kind=="errors" else "_RESEARCH_")+
            time.strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:6])
    reserve=max(8192,min(4_000_000,max_part_bytes//20))
    capacity=max_part_bytes-reserve
    pieces=[[]]
    used=[0]
    all_records={}
    source_records={}
    report_records={}
    created=[]
    with tempfile.TemporaryDirectory(prefix="artitrade_research_stage_",dir=str(folder)) as temp:
        stage=Path(temp)
        staged=stage/"snapshots"
        staged.mkdir()
        for serial,original in enumerate(originals):
            check_cancel()
            rel=original.relative_to(root).as_posix()
            announce(10+int(45*serial/max(1,len(originals))),
                     '원본 SQLite/WAL 스냅샷',serial,len(originals),rel)
            target=staged/rel
            if original.suffix.lower() in (".sqlite3",".sqlite",".db"):
                # sqlite3.backup may need many WAL-safe page batches while the
                # exchange is writing. Show real copied pages, and surface
                # bounded BUSY/LOCKED failure instead of an infinite 18%.
                last_page_report=[0.0]
                def report_page(copied,pages,status):
                    moment=time.monotonic()
                    if moment-last_page_report[0]<.7 and copied<pages:return
                    last_page_report[0]=moment
                    percent=10+int(45*(serial+min(1.0,copied/max(1,pages)))/
                                   max(1,len(originals)))
                    announce(percent,"원본 SQLite/WAL 스냅샷",
                             serial,len(originals),
                             rel+" · "+str(copied)+"/"+str(pages)+
                             " 페이지 · SQLite 상태 "+str(status))
                tables=_db_snapshot(original,target,check_cancel=check_cancel,
                                    on_progress=report_page)
                kind="sqlite"
            else:
                _copy_checked(original,target,check_cancel=check_cancel)
                tables=None
                kind="file"
            record={"path":rel,"serial":serial,"sha256":_sha256(target,check_cancel),
                    "size":target.stat().st_size,"kind":kind,
                    "tables":tables,"fragments":[]}
            source_records[rel]=record
            all_records[rel]=record
        catalog=_source_catalog({"strategy_files":source_records})
        reports={
            "analysis/source_table_catalog.json":json.dumps(
                catalog,ensure_ascii=False,indent=2).encode("utf8"),
            "analysis/README_ko.txt":(
                "ARTITRADE 통합 리서치 원장 — "+scope+"\n"
                "모든 원본 SQLite와 JSON을 백업 시점의 무손실 스냅샷으로 포함함.\n"
                "각 ZIP은 독립적으로 열 수 있음. fragments/ 항목은 원본의 일부이므로 "
                "같은 묶음의 모든 ZIP과 artitrade_research_restore.py로 결합할 것.\n"
                "analysis/bundle_index.json은 모든 조각의 원본경로, 순서, SHA256을 기록함.\n"
                "실제 체결과 가상관측은 각 원본 테이블에서 구분할 것.\n"
                "암호·API 키는 제외하며 원본 DB와 자동매매 엔진은 변경하지 않음.\n"
            ).encode("utf8")
        }
        if bundle_kind=="errors":
            # Capture Java/Android system process-exit diagnoses in addition
            # to original source SQLite issues. Never expose saved API keys.
            if native_errors_json is not None:
                native=str(native_errors_json)
                if len(native)>250_000:
                    raise ValueError("Android error report exceeds safe bound")
                report=json.loads(native)
                if not isinstance(report,dict):
                    raise ValueError("Android error report must be an object")
                reports["analysis/error_history_summary.json"]=json.dumps(
                    report,ensure_ascii=False,indent=2).encode("utf8")
            reports["analysis/README_ko.txt"]=(
                "ARTITRADE 주식·코인 오류 원본 — "+scope+"\n"
                "오류화면은 표시하지 않지만 원본 SQLite/JSON/JSONL은 유지됨.\n"
                "각 ZIP은 독립적으로 열 수 있음. 원본 fragment 복원 시 모든 PART 필요.\n"
                "각 파트 500,000,000바이트 이하, bundle_index.json에 원본 해시 수록.\n"
                "Android OS 종료 이유는 수집된 경우 error_history_summary.json에 포함.\n"
                "코인·주식 원본 계좌·주문·체결은 수정하지 않음.\n"
            ).encode("utf8")
        for name,blob in reports.items():
            path=staged/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(blob)
            record={"path":name,"serial":len(all_records),"sha256":_sha256(path),
                    "size":len(blob),"kind":"report","tables":None,"fragments":[]}
            report_records[name]=record
            all_records[name]=record
        announce(56,'분할 ZIP 구성',len(originals),len(originals),
                 '원본 SHA 및 분할계획 계산')
        for record in all_records.values():
            check_cancel()
            if bundle_kind=="errors":
                # Plan by real level-1 compressed bytes, not uncompressed DB
                # page count. 300MB of compressed evidence must be ONE ZIP,
                # regardless of how many GB its live WAL snapshots occupy.
                # Cancellation is checked on each 1 MiB planning block.
                _put_compressed_error_item(pieces,used,capacity,record,
                                           staged/record["path"],max_part_bytes,
                                           check_cancel)
            else:
                _put_plan_item(pieces,capacity,used,record,record["size"])
        for record in all_records.values():
            record.pop("serial",None)
        index={"schema":SCHEMA,"scope":scope,"bundle_kind":bundle_kind,
               "created_at_epoch_ms":int(time.time()*1000),
               "part_count":len(pieces),"max_part_bytes":max_part_bytes,
               "sources":source_records,"reports":report_records,
               "all_original_rows_preserved":True,
               "single_zip_is_not_sufficient_if_part_count_gt_1":len(pieces)>1}
        encoded=json.dumps(index,ensure_ascii=False,separators=(",",":")).encode("utf8")
        if len(encoded)>reserve-2048:
            raise ValueError("Research bundle index exceeds reserved part metadata space")
        results=[]
        try:
            for i,entries in enumerate(pieces,1):
                check_cancel()
                announce(60+int(25*(i-1)/max(1,len(pieces))),
                         '분할 ZIP 기록',i-1,len(pieces),
                         '파트 '+str(i)+'/'+str(len(pieces)))
                name=f"{prefix}_PART_{i:03d}_OF_{len(pieces):03d}.zip"
                intermediate=stage/name
                compression=(zipfile.ZIP_DEFLATED if bundle_kind=="errors"
                             else zipfile.ZIP_STORED)
                with zipfile.ZipFile(intermediate,"w",compression=compression,
                                     compresslevel=1 if bundle_kind=="errors" else None,
                                     allowZip64=True) as z:
                    z.writestr("analysis/bundle_index.json",encoded)
                    for rel,fragment in entries:
                        check_cancel()
                        with z.open(fragment["entry"],"w",force_zip64=True) as output:
                            _copy_piece(staged/rel,fragment["offset"],
                                        fragment["size"],output,check_cancel)
                size=intermediate.stat().st_size
                if size>max_part_bytes:
                    raise ValueError("ERROR_ZIP_OVER_500MB: actual ZIP exceeded "+
                                     str(max_part_bytes)+" bytes; no partial export")
                with zipfile.ZipFile(intermediate,"r") as verify:
                    # testzip() blocks until a whole multi-hundred-MB ZIP is
                    # reread. Stream verification with per-MiB cancel checks.
                    for member in verify.infolist():
                        check_cancel()
                        with verify.open(member) as stream:
                            while True:
                                check_cancel()
                                if not stream.read(1024*1024):break
                results.append({"name":name,"path":str(folder/name),
                                "bytes":size,"sha256":_sha256(intermediate,check_cancel)})
            announce(88,'분할 ZIP 완료',len(results),len(results),'전체 파트 검증 후 저장')
            for item in results:
                check_cancel()
                os.replace(stage/item["name"],folder/item["name"])
                created.append(folder/item["name"])
        except Exception:
            for path in created:
                try:path.unlink()
                except OSError:pass
            raise
    check_cancel()
    announce(92,'분할 ZIP 검증 완료',len(results),len(results),
             'Android 저장소 전송 준비')
    return json.dumps({"ok":True,"schema":SCHEMA,"scope":scope,"bundle_kind":bundle_kind,
        "parts":len(results),"files":results,
        "source_files":len(source_records),"source_bytes":total,
        "max_part_bytes":max_part_bytes},ensure_ascii=False)
