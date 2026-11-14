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


def _copy_piece(source: Path, offset: int, size: int, out) -> None:
    with source.open("rb") as stream:
        stream.seek(offset)
        left=size
        while left:
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


def make_bundle(root_path, output_dir, scope, max_part_bytes=DEFAULT_PART_BYTES):
    if scope not in ("stock","coin","all"):
        raise ValueError("Research scope must be stock, coin or all")
    max_part_bytes=int(max_part_bytes)
    if not 16_384<=max_part_bytes<=DEFAULT_PART_BYTES:
        raise ValueError("Research ZIP part size must be 16KB..500MB")
    root=Path(root_path).resolve()
    folder=Path(output_dir).resolve()
    if not root.is_dir() or not folder.is_dir():
        raise FileNotFoundError("Phone ledger or export cache missing")
    originals=sorted((p for p in root.rglob("*")
        if _candidate(root,p) and (
            scope=="all" or p.relative_to(root).parts[0]==scope)),
        key=lambda p:p.relative_to(root).as_posix())
    if not originals:raise ValueError("No original research ledger files for "+scope)
    if len(originals)>MAX_FILES:raise ValueError("Too many original research files")
    total=sum(p.stat().st_size for p in originals)
    if total>MAX_EXPANDED:raise ValueError("Research source exceeds 16GiB safety cap")
    if shutil.disk_usage(folder).free < total*2+MIN_FREE:
        raise OSError("Insufficient temporary storage for verified research export")

    prefix="ARTITRADE_"+scope.upper()+"_RESEARCH_"+time.strftime("%Y%m%d_%H%M%S")+"_"+uuid.uuid4().hex[:6]
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
            rel=original.relative_to(root).as_posix()
            target=staged/rel
            if original.suffix.lower() in (".sqlite3",".sqlite",".db"):
                tables=_db_snapshot(original,target)
                kind="sqlite"
            else:
                _copy_checked(original,target)
                tables=None
                kind="file"
            record={"path":rel,"serial":serial,"sha256":_sha256(target),
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
        for name,blob in reports.items():
            path=staged/name
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(blob)
            record={"path":name,"serial":len(all_records),"sha256":_sha256(path),
                    "size":len(blob),"kind":"report","tables":None,"fragments":[]}
            report_records[name]=record
            all_records[name]=record
        for record in all_records.values():
            _put_plan_item(pieces,capacity,used,record,record["size"])
        for record in all_records.values():
            record.pop("serial",None)
        index={"schema":SCHEMA,"scope":scope,"created_at_epoch_ms":int(time.time()*1000),
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
                name=f"{prefix}_PART_{i:03d}_OF_{len(pieces):03d}.zip"
                intermediate=stage/name
                with zipfile.ZipFile(intermediate,"w",compression=zipfile.ZIP_STORED,
                                     allowZip64=True) as z:
                    z.writestr("analysis/bundle_index.json",encoded)
                    for rel,fragment in entries:
                        with z.open(fragment["entry"],"w",force_zip64=True) as output:
                            _copy_piece(staged/rel,fragment["offset"],fragment["size"],output)
                size=intermediate.stat().st_size
                if size>max_part_bytes:
                    raise ValueError("Research part exceeds declared byte limit")
                with zipfile.ZipFile(intermediate,"r") as verify:
                    if verify.testzip() is not None:
                        raise ValueError("Research ZIP member CRC failed")
                results.append({"name":name,"path":str(folder/name),
                                "bytes":size,"sha256":_sha256(intermediate)})
            for item in results:
                os.replace(stage/item["name"],folder/item["name"])
                created.append(folder/item["name"])
        except Exception:
            for path in created:
                try:path.unlink()
                except OSError:pass
            raise
    return json.dumps({"ok":True,"schema":SCHEMA,"scope":scope,
        "parts":len(results),"files":results,
        "source_files":len(source_records),"source_bytes":total,
        "max_part_bytes":max_part_bytes},ensure_ascii=False)
