#!/usr/bin/env python3
"""Fail-closed governance validation; no phone files, broker orders, network."""
from pathlib import Path
import json
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
DOC=ROOT/"docs/artitrade"


def fail(errors,message):
    errors.append(message)


def changed_in_pr():
    if os.environ.get("GITHUB_EVENT_NAME")!="pull_request":
        return [],""
    event_path=os.environ.get("GITHUB_EVENT_PATH","")
    event=json.loads(Path(event_path).read_text("utf8")) if event_path else {}
    body=(event.get("pull_request") or {}).get("body") or ""
    base=os.environ.get("GITHUB_BASE_REF","main")
    result=subprocess.run(["git","diff","--name-only","origin/"+base+"...HEAD"],
                          cwd=ROOT,text=True,capture_output=True,check=False)
    if result.returncode:
        return [],body
    return result.stdout.splitlines(),body


def verify(strict=False):
    errors=[];warnings=[]
    for file in ("AGENTS.md","CLAUDE.md",".github/copilot-instructions.md",
                 ".github/ISSUE_TEMPLATE/artitrade-root-cause.yml",
                 ".github/PULL_REQUEST_TEMPLATE.md",
                 "docs/artitrade/USER_STANDING_RULES.md"):
        if not (ROOT/file).is_file():fail(errors,"CONTRIBUTOR_GOVERNANCE_MISSING:"+file)
    rules=(ROOT/"docs/artitrade/USER_STANDING_RULES.md")
    if rules.is_file():
        text=rules.read_text("utf8")
        for heading in ("## R1 ","## R2 ","## R3 ","## R4 ","## R5 "):
            if heading not in text:fail(errors,"USER_STANDING_RULE_MISSING:"+heading.strip())
        if "numeric only" not in text.lower():fail(errors,"USER_RULE_R1_NUMERIC_VERSION_TEXT_MISSING")
        for needle in ("0.1.0","0.2.0","next_version.py","Hand-over path","artitrade apk","Phone Link"):
            if needle not in text:fail(errors,"USER_RULE_R1_VERSION_SEQUENCE_MISSING:"+needle)
    manual=json.loads((DOC/"dependency_graph.json").read_text("utf8"))
    issues=json.loads((DOC/"version_issues.json").read_text("utf8"))
    if not manual["schema"].startswith("ARTITRADE_CRITICAL_DEPENDENCY_GRAPH_"):
        fail(errors,"MANUAL_GRAPH_SCHEMA_UNKNOWN")
    if not issues["schema"].startswith("ARTITRADE_VERSION_ISSUE_REGISTER_"):
        fail(errors,"ISSUES_SCHEMA_UNKNOWN")
    nodes=manual.get("nodes",[])
    by_id={n.get("id"):n for n in nodes}
    if len(by_id)!=len(nodes):fail(errors,"DUPLICATE_GRAPH_NODE_ID")
    if len(nodes)<20:fail(errors,"DEPENDENCY_GRAPH_MISSING_CRITICAL_FLOWS")
    issue_ids={row.get("issue") for row in issues.get("entries",[])}
    for node in nodes:
        nid=node.get("id")
        if not nid:fail(errors,"UNNAMED_GRAPH_NODE");continue
        for dep in node.get("depends_on",[]):
            if dep not in by_id:fail(errors,"BROKEN_GRAPH_EDGE:"+nid+"->"+str(dep))
        for number in node.get("issues",[]):
            if number not in issue_ids:fail(errors,"GRAPH_ISSUE_NOT_REGISTERED:"+str(number))
        for path in node.get("files",[]):
            if not (ROOT/path).is_file():
                message="SOURCE_FILE_NOT_IN_CHECKOUT:"+str(nid)+":"+path
                if strict:fail(errors,message)
                else:warnings.append(message)
    if len(issue_ids)!=len(issues.get("entries",[])):
        fail(errors,"DUPLICATE_ISSUE_NUMBER")
    required={"AT-005-01","AT-005-02","AT-005-03","AT-005-04",
              "AT-005-05","AT-005-06","AT-005-07","AT-005-08","AT-006-01"}
    if not required.issubset({r.get("id") for r in issues.get("entries",[])}):
        fail(errors,"MISSING_BASELINE_USER_OR_AUTODETECTED_ISSUES")
    for item in issues.get("entries",[]):
        if item.get("status") not in (
            "REPORTED","LOG_CONFIRMED","ROOT_CAUSE_CONFIRMED","SOURCE_FIXED",
            "CI_VERIFIED","DEVICE_VERIFIED","OPEN_UNVERIFIED"):
            fail(errors,"UNRECOGNIZED_STATUS:"+str(item.get("id")))
        if item.get("status")=="DEVICE_VERIFIED" and item.get("device")!="VERIFIED":
            fail(errors,"FAKE_DEVICE_VERIFICATION:"+str(item.get("id")))
        for nid in item.get("nodes",[]):
            if nid not in by_id:fail(errors,"ISSUE_REFERENCES_MISSING_NODE:"+nid)
    for item in issues.get("latent_unobserved",[]):
        if item.get("status")!="LATENT_UNOBSERVED":
            fail(errors,"LATENT_NOT_EXPLICITLY_UNOBSERVED")
        for nid in item.get("nodes",[]):
            if nid not in by_id:fail(errors,"LATENT_REFERENCES_MISSING_NODE:"+nid)
    for label in ("Root cause","Affected dependency nodes","Regression","Remaining unverified"):
        if label.lower() not in (ROOT/".github/PULL_REQUEST_TEMPLATE.md").read_text("utf8").lower():
            fail(errors,"PR_TEMPLATE_LACKS:"+label)
    change_list,body=changed_in_pr()
    code_changes=[name for name in change_list
                  if name.startswith("ARTITRADE_ANDROID/app/src/main/")
                  and name.endswith((".py",".java"))]
    if code_changes:
        for path in ("docs/artitrade/version_issues.json",
                     "docs/artitrade/ISSUE_HISTORY.md"):
            if path not in change_list:
                fail(errors,"CODE_CHANGED_WITHOUT_VERSIONED_ISSUE_LEDGER:"+path)
        for tag in ("root cause","affected dependency nodes","regression",
                    "remaining unverified","issue #"):
            if tag not in body.lower():
                fail(errors,"PR_REQUIRES_ROOT_CAUSE_FORM:"+tag)
    if strict:
        generated_path=ROOT/"artitrade_source_graph.json"
        if not generated_path.exists():fail(errors,"FULL_SOURCE_GRAPH_NOT_GENERATED")
        else:
            inventory=json.loads(generated_path.read_text("utf8"))
            facts=inventory.get("summary",{})
            if facts.get("overlay_python_files",0)<75:
                fail(errors,"OVERLAY_FILES_NOT_FULLY_SCANNED")
            if facts.get("java_files",0)<30:
                fail(errors,"JAVA_CLASSES_NOT_FULLY_SCANNED")
            if inventory.get("core_integrity",{}).get("status")!="NATIVE_HASH_VERIFIED":
                fail(errors,"NATIVE_CORE_INTEGRITY_NOT_PROVEN")
            if any(n.get("parse_status")=="FAILED" for n in inventory.get("nodes",[])):
                fail(errors,"PYTHON_PARSE_FAILURE")
    return errors,warnings,{"critical_nodes":len(nodes),
            "versioned_issues":len(issue_ids),"latent":len(issues.get("latent_unobserved",[])),
            "code_files_in_pr":len(code_changes)}


if __name__=="__main__":
    strict="--strict" in sys.argv
    errors,warnings,summary=verify(strict=strict)
    print("ARTITRADE_GOVERNANCE",json.dumps(summary,sort_keys=True))
    for warning in warnings:print("WARN",warning)
    for error in errors:print("FAIL",error)
    sys.exit(1 if errors else 0)
