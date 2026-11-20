"""One-time clearing of INDEPENDENT research / error / AI history files.

Owner instruction 2026-11-20 (phase 2 of the ledger reset, issue #123): after the
PAPER ledger reset the old research, error and AI-analysis history must not stay
mixed with new-policy data. Only data that no engine rebuilds or reads as state
is cleared:

* files that exist purely as AI / research / audit journals (cleared in full,
  schema kept): AI_RESEARCH_V040, AI_COMPREHENSIVE_V052, phone_buy_evidence_v035,
  execution_decisions_v074, execution_entry_plans_v107, forward_high_v041,
  ANDROID_RESEARCH_OBSERVATIONS, the MarketTrend RSI watch file;
* MarketTrend journal logs (events, gate_events, watch_observations) - NOT its
  latch / position / stop-reentry state;
* error counters (issues, runtime_error_summary, issue_lifecycle) of non-GMO DBs;
* post-exit-high tracking rows of non-GMO strategies;
* ``*error*.jsonl`` log files.

NOT touched on purpose (engine-owned, explained to the owner): signals, candidates,
pending orders, candidate_snapshots / forward / shadow tables (the engine back-fills
candidate_snapshots from signals, so clearing only the snapshots would be undone),
bars, market score history, accounts and every GMO file.
Runs once, before engines start; idempotent per database; failure never blocks trading.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
from pathlib import Path

TAG = "NL1120_HISTORY_RESET_V0140"
KST = dt.timezone(dt.timedelta(hours=9))
_LOCK = threading.RLock()
FULL_CLEAR_FILES = frozenset({
    "AI_RESEARCH_V040.sqlite3", "AI_COMPREHENSIVE_V052.sqlite3",
    "phone_buy_evidence_v035.sqlite3", "execution_decisions_v074.sqlite3",
    "execution_entry_plans_v107.sqlite3", "forward_high_v041.sqlite3",
    "ANDROID_RESEARCH_OBSERVATIONS.sqlite3",
    "market_trend_rsi_watch_v1118.sqlite3",
})
FILE_TABLES = {"market_trend_v043.sqlite3": ("events", "gate_events", "watch_observations")}
ERROR_TABLES = ("issues", "runtime_error_summary", "issue_lifecycle")
POST_EXIT_FILE = "POST_EXIT_HIGH_V053.sqlite3"


class UnsafeHistoryReset(RuntimeError):
    pass


def _clock():
    return dt.datetime.now(KST).isoformat()


def _is_gmo(path):
    return "gmo" in str(path).lower()


def _tables(c):
    return {str(r[0]) for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _clear_tables(path, names):
    """Delete every row of ``names`` that exist; one transaction per database."""
    with sqlite3.connect(str(path), timeout=20) as c:
        c.execute("PRAGMA busy_timeout=20000")
        existing = _tables(c)
        targets = [t for t in names if t in existing]
        if not targets:
            return {}
        c.execute("BEGIN IMMEDIATE")
        out = {}
        for t in targets:
            out[t] = c.execute('DELETE FROM "%s"' % t.replace('"', '')).rowcount
        c.commit()
    return out


def _clear_post_exit(path):
    """Non-GMO tracking only; GMO research on its own sells stays."""
    with sqlite3.connect(str(path), timeout=20) as c:
        c.execute("PRAGMA busy_timeout=20000")
        existing = _tables(c)
        if "cases" not in existing:
            return {}
        c.execute("BEGIN IMMEDIATE")
        out = {"cases": c.execute(
            "DELETE FROM cases WHERE LOWER(strategy)!='gmo'").rowcount}
        for child in ("minute_path", "high_history"):
            if child in existing:
                out[child] = c.execute(
                    'DELETE FROM "%s" WHERE case_id NOT IN (SELECT case_id FROM cases)' % child
                ).rowcount
        if "errors" in existing:
            out["errors"] = c.execute("DELETE FROM errors").rowcount
        c.commit()
    return out


def run_once(root):
    root = Path(root)
    if root.name != "ARTITRADE_PHONE_MAIN_V070" or root.parent.name != "files":
        raise UnsafeHistoryReset("EXACT_PHONE_PRIVATE_ROOT_REQUIRED")
    if os.environ.get("ARTITRADE_REAL_MODE") == "1":
        return {"ok": True, "skipped": "REAL_TRADING_CONFIG_NO_AUTORESET"}
    done = root.parent / "ARTITRADE_NL1120_HISTORY_RESET_V0140_DONE.json"
    with _LOCK:
        if done.is_file():
            return {"ok": True, "status": "ALREADY_RESET", "applied_now": False}
        cleared, failed, files = {}, [], 0
        for f in sorted(root.rglob("*")):
            if f.is_symlink() or not f.is_file() or _is_gmo(f.relative_to(root)):
                continue
            rel = f.relative_to(root).as_posix()
            name = f.name
            try:
                if f.suffix.lower() == ".jsonl" and "error" in name.lower():
                    f.write_bytes(b"")
                    cleared[rel] = {"jsonl": 1}
                    files += 1
                    continue
                if f.suffix.lower() not in (".sqlite3", ".sqlite", ".db"):
                    continue
                if name in FULL_CLEAR_FILES:
                    with sqlite3.connect(str(f), timeout=20) as c:
                        names = sorted(t for t in _tables(c) if not t.startswith("sqlite_"))
                    got = _clear_tables(f, names)
                elif name == POST_EXIT_FILE:
                    got = _clear_post_exit(f)
                elif name in FILE_TABLES:
                    got = _clear_tables(f, FILE_TABLES[name])
                elif rel.startswith(("stock/", "coin/")):
                    got = _clear_tables(f, ERROR_TABLES)
                else:
                    continue
                if got:
                    cleared[rel] = got
                    files += 1
            except (sqlite3.Error, OSError) as exc:
                failed.append({"file": rel, "error": type(exc).__name__})
        status = "PARTIAL" if failed else "DONE"
        if not failed:
            tmp = done.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tag": TAG, "status": status, "at": _clock(),
                                       "files": files}, sort_keys=True), encoding="utf8")
            os.replace(tmp, done)
        return {"ok": True, "status": status, "applied_now": True, "files": files,
                "failed": failed, "cleared": {k: v for k, v in list(cleared.items())[:40]}}


def run_json(root):
    return json.dumps(run_once(root), ensure_ascii=False, sort_keys=True)
