"""Deterministic v063 patch of one bundled GMO module, preserving other modules.

Run only on the isolated release branch with the originally SHA-pinned core.
This script never opens or edits phone databases, broker connections, or keys.
"""
from __future__ import annotations
import base64
import hashlib
import io
import math
import re
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DIR=ROOT/"app/src/main/assets/strategy_v070"
JAVA=ROOT/"app/src/main/java/com/artitrade/mobile/StrategyRuntimeBridge.java"
OLD_SHA="e7518cb90a73cf62522aaa8962d872890e9e517ae988f52c22b3d94ed88531e0"
TARGET="projectgmo/engine.py"
PARTS=22

def replacement(text,old,new,count):
    if text.count(old)!=count:
        raise RuntimeError(f"Unexpected GMO source occurrence count for {old!r}: {text.count(old)}")
    return text.replace(old,new)

def patch(source):
    text=source.decode("utf-8")
    text=replacement(text,"entry_profit_floor: float = 0.02","entry_profit_floor: float = 0.01",1)
    text=replacement(text,"'ENTRY_PLUS2_FLOOR'","'ENTRY_PLUS1_FLOOR'",2)
    # Preserve every original execution/position field when restoring old GMO
    # states. Only the owner's updated 1% threshold changes for future quotes.
    text=replacement(text,"obj = cls(Settings(**x['cfg']))",
        "obj = cls(Settings(**{**x['cfg'], 'entry_profit_floor': 0.01}))",1)
    return text.encode("utf-8")

def main():
    parts=sorted(DIR.glob("part_*.txt"))
    assert len(parts)==PARTS,(len(parts),PARTS)
    archive=base64.b64decode("".join(p.read_text(encoding="ascii") for p in parts))
    current=hashlib.sha256(archive).hexdigest()
    java=JAVA.read_text(encoding="utf-8")
    if current!=OLD_SHA:
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            updated=z.read(TARGET)
        if (b"entry_profit_floor: float = 0.01" in updated
            and b"ENTRY_PLUS1_FLOOR" in updated
            and current in java):
            print("ALREADY_PATCHED",current)
            return
        raise RuntimeError("Unexpected original core SHA: "+current)
    assert java.count(OLD_SHA)==1,"Java SHA pin changed unexpectedly"
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        info=source.infolist()
        assert len({x.filename for x in info})==len(info),"Duplicate ZIP entry"
        assert sum(x.filename==TARGET for x in info)==1,TARGET
        original={x.filename:source.read(x.filename) for x in info}
        modified=original.copy()
        modified[TARGET]=patch(original[TARGET])
    new_buf=io.BytesIO()
    with zipfile.ZipFile(new_buf,mode="w") as out:
        for x in info:out.writestr(x,modified[x.filename])
    updated=new_buf.getvalue()
    with zipfile.ZipFile(io.BytesIO(updated)) as verified:
        assert verified.namelist()==[x.filename for x in info],"Module list changed"
        for name,content in modified.items():
            if verified.read(name)!=content:raise RuntimeError("ZIP bytes differ: "+name)
    sha=hashlib.sha256(updated).hexdigest()
    encoded=base64.b64encode(updated).decode("ascii")
    size=math.ceil(len(encoded)/PARTS)
    chunks=[encoded[i*size:(i+1)*size] for i in range(PARTS)]
    assert len(chunks)==PARTS and all(chunks),"Unexpected archive chunk structure"
    for p,chunk in zip(parts,chunks):p.write_text(chunk,encoding="ascii")
    assert hashlib.sha256(base64.b64decode("".join(x.read_text(encoding="ascii") for x in parts))).hexdigest()==sha
    JAVA.write_text(java.replace(OLD_SHA,sha),encoding="utf-8")
    print("V063_CORE_PATCHED",sha,"ZIP_BYTES",len(updated),
          "UNCHANGED_MODULES",len(original)-1,"UPDATED_MODULE",TARGET)

if __name__=="__main__":
    main()
