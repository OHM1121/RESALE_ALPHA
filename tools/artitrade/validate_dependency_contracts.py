#!/usr/bin/env python3
"""Fail-closed version issue and dependency map validator (no broker access).

The generated graph is the source inventory; the curated graph is authoritative
for important runtime-coupling contracts. This validator does NOT prove that
real mobile execution works or enforce external AI compliance on its own.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import subprocess
import sys

REPO=Path(__file__).resolve().parents[2]
DIR=REPO/"docs/artitrade"
VALID={"REPORTED","LOG_CONFIRMED","ROOT_CAUSE_CONFIRMED","SOURCE_FIXED",
       "CI_VERIFIED","DEVICE_VERIFIED","OPEN_UNVERIFIED","LATENT_UNOBSERVED",
       "NON_ERROR_POLICY_BLOCK"}
HUB_FILES=("market_trend_runtime_v043.py","post_exit_runtime_v053.py",
           "phone_policy_reference_v059.py","ma_v061_packet.py",
           "ma_v062_packet.py","stock_quote_truth_v066.py")
HUB_WRAPPERS=("runtime_hub","tracking_hub","chronologized","scoped",
              "records","hub_with_verified_prices")


def fail(text):
    raise ValueError(text)


def read_json(path):
    try:return json.loads(path.read_text(encoding="utf8"))
    except Exception as exc:fail("Cannot parse "+str(path)+": "+type(exc).__name__)


def validate_generated(path):
    data=read_json(path)
    if data.get("schema")!="ARTITRADE_FULL_SOURCE_GRAPH_V007":
        fail("Generated full graph schema mismatch")
    if data.get("core_integrity",{}).get("status") not in (
        "NATIVE_HASH_VERIFIED","APPROVED_VENDOR_PATCH_HASH_VERIFIED"):
        fail("Original/explicitly approved vendor-patched core SHA verification failed")
    summary=data["summary"]
    if min(summary["overlay_python_files"],summary["native_python_files"],
           summary["java_files"])<1:
        fail("Empty source graph")
    errors=[n["name"] for n in data["nodes"] if n.get("parse_status","OK")!="OK"]
    if errors:fail("Failed Python syntax: "+", ".join(errors[:10]))
    names={n["id"] for n in data["nodes"]}
    for edge in data["edges"]:
        if edge["from"] not in names or edge["to"] not in names:
            fail("Dangling static edge: "+str(edge))
    return summary,len(data["edges"])


def validate_hub_contract():
    # v005 was broken by a new display-only monkeypatch which silently
    # changed the existing hub_records(for_ui=...) method signature.
    root=REPO/"ARTITRADE_ANDROID/app/src/main/python"
    for filename,fnname in zip(HUB_FILES,HUB_WRAPPERS):
        tree=ast.parse((root/filename).read_text(encoding="utf8"))
        defs=[n for n in ast.walk(tree)
              if isinstance(n,ast.FunctionDef) and n.name==fnname]
        if len(defs)!=1:fail(filename+": missing unique hub wrapper")
        fn=defs[0]
        if "for_ui" not in [a.arg for a in fn.args.args]:
            fail(filename+"."+fnname+" lost the for_ui interface")
        forward=any(any(k.arg=="for_ui" and isinstance(k.value,ast.Name)
                        and k.value.id=="for_ui" for k in call.keywords)
                    for call in ast.walk(fn) if isinstance(call,ast.Call))
        if not forward:fail(filename+"."+fnname+" doesn't forward for_ui")


def validate_registry():
    graph=read_json(DIR/"dependency_graph.json")
    journal=read_json(DIR/"version_issues.json")
    if graph.get("schema")!="ARTITRADE_CRITICAL_DEPENDENCY_GRAPH_V007":
        fail("Critical dependency graph schema mismatch")
    if journal.get("schema")!="ARTITRADE_VERSION_ISSUE_REGISTER_V007":
        fail("Version issue journal schema mismatch")
    nodes=graph["nodes"]
    ids=[n["id"] for n in nodes]
    if len(ids)!=len(set(ids)):fail("Duplicate graph node IDs")
    known=set(ids)
    issue_ids={e["issue"] for e in journal["entries"]}
    if len(issue_ids)!=len(journal["entries"]):fail("Duplicate GitHub issue linkage")
    if len(journal["entries"])<9:fail("Missing the previously identified v005/v006 issue history")
    for n in nodes:
        for dep in n["depends_on"]:
            if dep not in known:fail(n["id"]+" depends on missing node "+dep)
        for path in n.get("files",[]):
            if not (REPO/path).is_file():
                fail(n["id"]+" references missing source: "+path)
        for issue in n.get("issues",[]):
            if issue not in issue_ids:fail(n["id"]+" references missing issue "+str(issue))
    for e in journal["entries"]:
        if e["status"] not in VALID:
            fail("Unexpected issue status "+str(e["status"]))
        if e.get("device")=="VERIFIED" and e["status"]!="DEVICE_VERIFIED":
            fail("Device verification falsely claimed for "+e["id"])
        if e["status"] in ("SOURCE_FIXED","CI_VERIFIED"):
            if not e.get("fix_version") or not e.get("tests"):
                fail("Missing code fix/regression evidence for "+e["id"])
        for n in e["nodes"]:
            if n not in known:fail(e["id"]+" points at absent graph node "+n)
    for name in ("AGENTS.md","CLAUDE.md",".github/copilot-instructions.md",
                 ".github/ISSUE_TEMPLATE/artitrade-root-cause.yml",
                 ".github/PULL_REQUEST_TEMPLATE.md","docs/artitrade/ISSUE_HISTORY.md"):
        if not (REPO/name).is_file():fail("Missing cross-AI governance file "+name)
    required=("root cause","dependency","GitHub Issue")
    policy=(REPO/"AGENTS.md").read_text(encoding="utf8").lower()
    if any(s.lower() not in policy for s in required):
        fail("Cross-AI root-cause policy is incomplete")
    return len(nodes),len(journal["entries"])


def optional_branch_impact(base):
    # For PR builds from this source branch, require BOTH version log and
    # dependency map updates whenever application source changes. This catches
    # future symptom-only edits; it cannot prevent a deliberate bypass.
    if not base:return
    proc=subprocess.run(["git","diff","--name-only",base+"...HEAD"],
             cwd=REPO,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if proc.returncode:fail("Unable to diff branch against "+base)
    names=set(proc.stdout.splitlines())
    changed=any(p.startswith("ARTITRADE_ANDROID/app/src/main/") for p in names)
    if changed:
        for needed in ("docs/artitrade/version_issues.json",
                       "docs/artitrade/dependency_graph.json"):
            if needed not in names:
                fail("Changed production source without "+needed+" in PR")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--generated",default="artitrade_source_graph.json")
    parser.add_argument("--base",default="")
    args=parser.parse_args()
    n,e=validate_generated(REPO/args.generated)
    validate_hub_contract()
    graph_nodes,entries=validate_registry()
    optional_branch_impact(args.base)
    print("ARTITRADE_GOVERNANCE_OK",
          json.dumps({"source_files":n,"static_edges":e,
                      "critical_nodes":graph_nodes,"version_issues":entries}))
