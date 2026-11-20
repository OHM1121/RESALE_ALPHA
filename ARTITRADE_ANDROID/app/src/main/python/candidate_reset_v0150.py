"""One-time clearing of candidate / signal-linked research and buy-pending rows.

Owner instruction 2026-11-20 (phase 3 of the reset, issue #124): after the ledger
reset (#121) and the independent-history clearing (#123) the remaining old-policy
candidates, signals, candidate-linked research and buy-pending (매수대기) rows must
go too, so everything restarts from the new policy.

Engine code is NOT changed; only rows are deleted, schemas, sequences, bars,
market score, accounts and every GMO file stay. Why this is safe (checked in the
vendor engine source, 2026-11-20):
* normal signal discovery only looks at the bar that has just completed
  (``discover(complete_at, min_event=complete_at, ...)``), it never rebuilds old
  signals; the gap-repair discoveries are triggered per symbol by a NEW repair;
* the engine itself deletes ``pending`` rows in many branches and handles an empty
  table; ``candidate_snapshots`` is back-filled from ``signals`` so both are cleared
  together;
* nothing here touches positions or executions (already handled by #121).
Runs once per phone, before any engine starts; per-database transactions; retried
until every database succeeded; failure never blocks trading.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
from pathlib import Path

TAG = "NL1120_CANDIDATE_RESET_V0150"
KST = dt.timezone(dt.timedelta(hours=9))
_LOCK = threading.RLock()

STOCK_AT = (
    "signals", "pending", "v19_orders", "v19_events", "v19_higher_frame_audit14",
    "v19_pending_5m_audit", "v19_frame_revision_confirm",
    "candidate_snapshots", "candidate_events", "candidate_forward",
    "candidate_milestones", "candidate_score_repair", "candidate_cohort_decisions",
    "stock_shadow_fills", "stock_shadow_minute_path", "stock_shadow_queue_audit",
    "stock_shadow_trials",
)
STOCK_MA = ("candidates", "research")
COIN = (
    "orders", "candidates", "candidate_decisions", "scan_runs", "coin_scan_decisions",
    "coin_shadow_book_path", "coin_shadow_fills", "coin_shadow_trials",
    "coin_shadow_trial_1m", "coin_v24_shadow_exit", "coin_top35_members",
    "coin_top35_snapshots", "strong_bear_shadow_1m", "strong_bear_shadow_ref_1m",
    "strong_bear_shadow_refs",
)
CLOSE = (
    "signals", "research_candidates", "research_orderbook", "research_bar_snapshots",
    "research_position_path", "research_followups", "research_events",
    "research_api_events", "research_scan_skips", "research_market_context",
    "research_universe_snapshots",
)
TREND_JOURNAL = ("virtual_position_state",)


class UnsafeCandidateReset(RuntimeError):
    pass


def _clock():
    return dt.datetime.now(KST).isoformat()


def _plan(root, path):
    """Table list for this database, or None. GMO is never a target."""
    rel = path.relative_to(root).as_posix()
    low = rel.lower()
    if "gmo" in low:
        return None
    name = path.name
    if rel == "stock/ARTITRADE_D.sqlite3":
        return STOCK_AT
    if rel == "stock/close/AT_CLOSE_PAPER.sqlite3":
        return CLOSE
    if rel.startswith("stock/") and name == "ma_paper.sqlite3":
        return STOCK_MA
    if rel.startswith("coin/") and name == "upbit_paper.sqlite3":
        return COIN
    if rel == "market_trend_v043.sqlite3":
        return TREND_JOURNAL
    return None


def _clear(path, names):
    with sqlite3.connect(str(path), timeout=20) as c:
        c.execute("PRAGMA busy_timeout=20000")
        existing = {str(r[0]) for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        targets = [t for t in names if t in existing]
        if not targets:
            return {}
        c.execute("BEGIN IMMEDIATE")
        out = {t: c.execute('DELETE FROM "%s"' % t).rowcount for t in targets}
        c.commit()
    return out


def run_once(root):
    root = Path(root)
    if root.name != "ARTITRADE_PHONE_MAIN_V070" or root.parent.name != "files":
        raise UnsafeCandidateReset("EXACT_PHONE_PRIVATE_ROOT_REQUIRED")
    if os.environ.get("ARTITRADE_REAL_MODE") == "1":
        return {"ok": True, "skipped": "REAL_TRADING_CONFIG_NO_AUTORESET"}
    done = root.parent / "ARTITRADE_NL1120_CANDIDATE_RESET_V0150_DONE.json"
    with _LOCK:
        if done.is_file():
            return {"ok": True, "status": "ALREADY_RESET", "applied_now": False}
        cleared, failed = {}, []
        for f in sorted(root.rglob("*")):
            if f.is_symlink() or not f.is_file() or f.suffix.lower() not in (
                    ".sqlite3", ".sqlite", ".db"):
                continue
            names = _plan(root, f)
            if not names:
                continue
            rel = f.relative_to(root).as_posix()
            try:
                got = _clear(f, names)
                if got:
                    cleared[rel] = got
            except (sqlite3.Error, OSError) as exc:
                failed.append({"file": rel, "error": type(exc).__name__})
        status = "PARTIAL" if failed else "DONE"
        if not failed:
            tmp = done.with_suffix(".tmp")
            tmp.write_text(json.dumps({"tag": TAG, "status": status, "at": _clock(),
                                       "files": len(cleared)}, sort_keys=True),
                           encoding="utf8")
            os.replace(tmp, done)
        return {"ok": True, "status": status, "applied_now": True,
                "files": len(cleared), "failed": failed,
                "cleared": {k: v for k, v in list(cleared.items())[:40]}}


def run_json(root):
    return json.dumps(run_once(root), ensure_ascii=False, sort_keys=True)
