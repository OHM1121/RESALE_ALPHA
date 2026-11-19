#!/usr/bin/env python3
"""Source dependency inventory: read-only, offline, no imports/exec of app code.

Captures every present Python overlay, original packaged core .py and Java
class. Static AST and text references are evidence of coupling, NOT proof
of actual runtime use. Dynamic monkeypatch edges live in manual graph.
"""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile


REPO=Path(__file__).resolve().parents[2]
OVERLAYS=REPO/"ARTITRADE_ANDROID/app/src/main/python"
NATIVE=REPO/"ARTITRADE_ANDROID/app/src/main/assets/strategy_v070"
JAVA=REPO/"ARTITRADE_ANDROID/app/src/main/java/com/artitrade/mobile"
CORE_SHA256="b014a00fd2cc29984650cb7ea24e7ea9672d6480fd68e91a6a1ebbe72754dfbb"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def python_record(path,raw,origin):
    label=path.replace("\\","/")
    out={"id":origin+":"+Path(label).stem,"name":label,
         "origin":origin,"sha256":digest(raw),"imports":[],"dynamic_imports":[],
         "defined_functions":[],"defined_classes":[],"monkeypatches":[],
         "parse_status":"OK"}
    try:
        tree=ast.parse(raw.decode("utf8"),filename=label)
    except (SyntaxError,UnicodeError) as e:
        out["parse_status"]="FAILED:"+type(e).__name__
        return out
    for node in ast.walk(tree):
        if isinstance(node,ast.Import):
            out["imports"].extend(x.name for x in node.names)
        elif isinstance(node,ast.ImportFrom):
            if node.module:out["imports"].append(node.module)
        elif isinstance(node,ast.FunctionDef):
            out["defined_functions"].append(node.name)
        elif isinstance(node,ast.ClassDef):
            out["defined_classes"].append(node.name)
        elif isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
            if node.func.attr in ("import_module",) and node.args and isinstance(node.args[0],ast.Constant):
                out["dynamic_imports"].append(str(node.args[0].value))
        elif isinstance(node,ast.Call) and isinstance(node.func,ast.Name):
            if node.func.id=="__import__" and node.args and isinstance(node.args[0],ast.Constant):
                out["dynamic_imports"].append(str(node.args[0].value))
        elif isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Attribute) and target.attr in (
                    "hub_records","on_tick","process_book","_new_tick","_buy",
                    "_sell","start","stop","snapshot","_connect"):
                    out["monkeypatches"].append({"target":ast.unparse(target),"line":node.lineno})
    for key in ("imports","dynamic_imports","defined_functions","defined_classes"):
        out[key]=sorted(set(out[key]))
    return out


def overlay_nodes():
    return [python_record(str(p.relative_to(REPO)),p.read_bytes(),"overlay")
            for p in sorted(OVERLAYS.rglob("*.py"))] if OVERLAYS.is_dir() else []


def native_nodes():
    if not NATIVE.is_dir():return [],{"status":"NO_BUNDLED_NATIVE_CORE"}
    try:
        parts=[(NATIVE/("part_%02d.txt"%i)).read_text("ascii") for i in range(22)]
        payload=base64.b64decode("".join(parts),validate=True)
        digest_actual=digest(payload)
        if digest_actual!=CORE_SHA256:
            return [],{"status":"PACKAGED_NATIVE_HASH_MISMATCH","sha256":digest_actual}
        output=[]
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            if z.testzip() is not None:raise ValueError("corrupt core zip")
            for entry in sorted(z.namelist()):
                if not entry.endswith(".py") or ".." in Path(entry).parts:continue
                output.append(python_record(entry,z.read(entry),"native"))
        return output,{"status":"NATIVE_HASH_VERIFIED","sha256":digest_actual,
                       "python_files":len(output)}
    except (OSError,ValueError,zipfile.BadZipFile) as e:
        return [],{"status":"NATIVE_READ_FAILED","error_type":type(e).__name__}


def java_nodes():
    sources=list(sorted(JAVA.rglob("*.java"))) if JAVA.is_dir() else []
    all_classes={p.stem for p in sources}
    found=[]
    for path in sources:
        raw=path.read_bytes()
        text=raw.decode("utf8",errors="replace")
        refs=sorted(set(x for x in re.findall(r"\b[A-Z][A-Za-z0-9_]+\b",text)
                        if x in all_classes and x!=path.stem))
        found.append({"id":"java:"+path.stem,
                      "name":str(path.relative_to(REPO)),
                      "origin":"java","sha256":digest(raw),"references":refs,
                      "imports":sorted(set(re.findall(r"(?m)^\s*import\s+([\w.]+)",text))),
                      "reference_semantics":"TEXTUAL_REFERENCE_NOT_RUNTIME_PROOF"})
    return found


def resolve_edges(nodes):
    names={n["id"] for n in nodes}
    edges=set()
    for n in nodes:
        if n["origin"]=="java":
            for target in n["references"]:
                if "java:"+target in names:
                    edges.add((n["id"],"java:"+target,"java_reference"))
        else:
            for imp in n.get("imports",[])+n.get("dynamic_imports",[]):
                root=imp.split(".")[0]
                # A module may be present in both bundled core and overlays.
                for origin in ("overlay","native"):
                    target=origin+":"+root
                    if target in names and target!=n["id"]:
                        edges.add((n["id"],target,"import_or_dynamic_import"))
    return [{"from":a,"to":b,"kind":c} for a,b,c in sorted(edges)]


def build():
    python=overlay_nodes()
    native,integrity=native_nodes()
    java=java_nodes()
    nodes=python+native+java
    return {"schema":"ARTITRADE_FULL_SOURCE_GRAPH_V007",
      "explanation":"Static AST/text inventory; actual dynamically wired dependencies must be reviewed against docs/artitrade/dependency_graph.json.",
      "core_integrity":integrity,
      "summary":{"overlay_python_files":len(python),"native_python_files":len(native),
                 "java_files":len(java),"total_nodes":len(nodes)},
      "nodes":nodes,"edges":resolve_edges(nodes)}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="artitrade_source_graph.json")
    args=parser.parse_args()
    data=build()
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(data,ensure_ascii=False,sort_keys=True,
                                 indent=2)+"\n",encoding="utf8")
    print("SOURCE_GRAPH_COUNTS",json.dumps(data["summary"]))
    print("CORE_INTEGRITY",data["core_integrity"]["status"])
