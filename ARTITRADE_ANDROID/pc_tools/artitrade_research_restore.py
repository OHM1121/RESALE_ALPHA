#!/usr/bin/env python3
"""Verify and reassemble ARTITRADE research ZIP parts into a NEW directory only."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

SCHEMA="ARTITRADE_RESEARCH_BUNDLE_V1"
PART_PATTERN=re.compile(r"_PART_(\d+)_OF_(\d+)\.zip$",re.IGNORECASE)


def _safe_path(root,rel):
    candidate=PurePosixPath(rel)
    if (candidate.is_absolute() or '..' in candidate.parts or
        not candidate.parts or candidate.parts[0] not in ('stock','coin','analysis') or
        any(p in ('','.','..') for p in candidate.parts)):
        raise ValueError("Unsafe research member path")
    return root.joinpath(*candidate.parts)


def restore_bundle(parts,output_dir):
    files=[Path(p).resolve() for p in parts]
    if not files:
        raise ValueError("No research ZIP parts supplied")
    target=Path(output_dir).resolve()
    archives={}
    with ExitStack() as cleanup:
        original_index=None
        for file in files:
            found=PART_PATTERN.search(file.name)
            if not found:
                raise ValueError("Research part filename missing index")
            number,total=map(int,found.groups())
            if number<1 or total<1 or number>total or number in archives:
                raise ValueError("Duplicate or invalid research part number")
            archive=cleanup.enter_context(zipfile.ZipFile(file,"r",allowZip64=True))
            current=archive.read("analysis/bundle_index.json")
            if original_index is None:
                original_index=current
            elif current!=original_index:
                raise ValueError("Research part manifests disagree")
            archives[number]=archive
        index=json.loads(original_index)
        if index.get("schema")!=SCHEMA or index.get("part_count")!=len(files) or (
                set(archives)!=set(range(1,int(index.get("part_count") or 0)+1))):
            raise ValueError("Missing research ZIP part: restore requires every part")
        records={**(index.get("sources") or {}),**(index.get("reports") or {})}
        if not records:
            raise ValueError("Research bundle has no original files")
        for rel,record in records.items():
            _safe_path(target,rel)
            if not isinstance(record,dict) or record.get("path")!=rel:
                raise ValueError("Invalid source manifest")
            if _safe_path(target,rel).exists():
                raise FileExistsError("Refusing to replace existing research file: "+rel)
            fragments=record.get("fragments") or []
            offset=0
            for f in fragments:
                if not isinstance(f,dict) or f.get("offset")!=offset:
                    raise ValueError("Research fragment offsets are not contiguous")
                size=f.get("size");part=f.get("part");entry=f.get("entry")
                if type(size) is not int or size<0 or part not in archives or not isinstance(entry,str):
                    raise ValueError("Invalid research fragment metadata")
                if entry not in archives[part].namelist():
                    raise ValueError("Research fragment missing from ZIP")
                if size!=archives[part].getinfo(entry).file_size:
                    raise ValueError("Research fragment size mismatch")
                offset+=size
            if offset!=record.get("size"):
                raise ValueError("Research source is not complete")
        # No files are installed before every manifest is proven complete.
        target.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="artitrade_research_restore_",
                                         dir=str(target.parent)) as staging:
            stage=Path(staging)
            for rel,record in records.items():
                path=_safe_path(stage,rel)
                path.parent.mkdir(parents=True,exist_ok=True)
                digest=hashlib.sha256()
                count=0
                with path.open("xb") as output:
                    for frag in record["fragments"]:
                        with archives[frag["part"]].open(frag["entry"]) as stream:
                            while True:
                                block=stream.read(1024*1024)
                                if not block:break
                                output.write(block)
                                digest.update(block)
                                count+=len(block)
                    output.flush()
                    os.fsync(output.fileno())
                if count!=record["size"] or digest.hexdigest()!=record["sha256"]:
                    raise ValueError("Research source SHA256 mismatch: "+rel)
            target.mkdir(parents=True,exist_ok=True)
            for rel in records:
                src=_safe_path(stage,rel);dst=_safe_path(target,rel)
                if dst.exists():raise FileExistsError("Research path already exists: "+rel)
                dst.parent.mkdir(parents=True,exist_ok=True)
                os.rename(src,dst)
    return {"ok":True,"files":len(records),"scope":index["scope"],
            "parts":len(files),"output":str(target)}


def main():
    parser=argparse.ArgumentParser(description="Restore original research data from all ZIP parts")
    parser.add_argument("parts",nargs="+",help="All *_PART_NNN_OF_NNN.zip files")
    parser.add_argument("--output",required=True,help="A NEW folder, not an original EXE ledger folder")
    args=parser.parse_args()
    print(json.dumps(restore_bundle(args.parts,args.output),ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
