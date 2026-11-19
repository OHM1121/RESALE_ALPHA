#!/usr/bin/env python3
"""Portable PR control for any AI/human contributor to ARTITRADE.

Use GitHub branch rules to mark this workflow REQUIRED. This script checks
documentation contract and change traceability; it cannot judge or force
real-world brokerage correctness.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPO=Path(__file__).resolve().parents[2]
REQUIRED=("AGENTS.md","CLAUDE.md",".github/copilot-instructions.md",
          "docs/artitrade/ISSUE_HISTORY.md","docs/artitrade/dependency_graph.json",
          "docs/artitrade/version_issues.json",
          ".github/ISSUE_TEMPLATE/artitrade-root-cause.yml",
          ".github/PULL_REQUEST_TEMPLATE.md")
for rel in REQUIRED:
    if not (REPO/rel).is_file():
        raise SystemExit("MISSING_ARTITRADE_GOVERNANCE_FILE: "+rel)
graph=json.loads((REPO/"docs/artitrade/dependency_graph.json").read_text(encoding="utf8"))
history=json.loads((REPO/"docs/artitrade/version_issues.json").read_text(encoding="utf8"))
ids={x["id"] for x in graph.get("nodes",[])}
if not ids or len(ids)!=len(graph["nodes"]):
    raise SystemExit("INVALID_GRAPH_IDS")
if not history.get("entries"):
    raise SystemExit("NO_VERSIONED_ISSUES")
for row in history["entries"]:
    if not row.get("issue") or not row.get("found") or not row.get("status"):
        raise SystemExit("INCOMPLETE_VERSION_ISSUE "+str(row))
    if any(n not in ids for n in row.get("nodes",[])):
        raise SystemExit("UNKNOWN_DEPENDENCY_NODE "+str(row.get("id")))
    if row.get("device")=="VERIFIED" and row.get("status")!="DEVICE_VERIFIED":
        raise SystemExit("UNPROVEN_DEVICE_VERIFICATION "+str(row.get("id")))

event_path=os.getenv("GITHUB_EVENT_PATH","")
if os.getenv("GITHUB_EVENT_NAME")=="pull_request" and event_path:
    event=json.loads(Path(event_path).read_text(encoding="utf8"))
    pr=event.get("pull_request") or {}
    base=(pr.get("base") or {}).get("sha")
    if not base:raise SystemExit("MISSING_PR_BASE_SHA")
    diff=subprocess.run(["git","diff","--name-only",base+"...HEAD"],
      cwd=REPO,encoding="utf8",capture_output=True)
    if diff.returncode!=0:raise SystemExit("GIT_DIFF_FAILED "+diff.stderr[:150])
    changed=set(diff.stdout.splitlines())
    code_changed=any(x.startswith("ARTITRADE_ANDROID/app/src/main/") for x in changed)
    if code_changed:
        for rel in ("docs/artitrade/dependency_graph.json",
                    "docs/artitrade/version_issues.json"):
            if rel not in changed:
                raise SystemExit("ROOT_CAUSE_HISTORY_NOT_UPDATED: "+rel)
        body=str(pr.get("body") or "")+" "+str(pr.get("title") or "")
        if not re.search(r"(?:#\d+|https://github\.com/OHM1121/RESALE_ALPHA/issues/\d+)",body):
            raise SystemExit("NO_GITHUB_ISSUE_LINK_IN_PR")
print("ARTITRADE_MAIN_GOVERNANCE_OK",len(ids),len(history["entries"]))
