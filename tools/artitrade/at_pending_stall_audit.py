#!/usr/bin/env python3
"""Read-only, sanitized classifier for "stock AT has PENDING orders but no BUY".

Usage (on a PC, against a COPY of the exported stock AT SQLite, e.g.
ARTITRADE_D.sqlite3 from the phone export):

    python3 tools/artitrade/at_pending_stall_audit.py ARTITRADE_D.sqlite3 [--now YYYYMMDDHHMMSS]

It opens the file with SQLite ``mode=ro`` + ``query_only``; it never writes,
migrates or deletes. The output is aggregate counts only: no symbol codes, no
prices, no quantities, no message text, no absolute timestamps. It is safe to
paste into a public GitHub issue.

Evidence discipline (AGENTS.md): a missing table is reported as
``TABLE_ABSENT`` (not zero); every verdict is a hint labelled INFERRED and is
never proof of a root cause or of a device/broker fact.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
from pathlib import Path

SCHEMA = "ARTITRADE_AT_PENDING_STALL_AUDIT_V1"
TIMEFRAMES = ("5m", "30m", "60m", "day")
AGE_BUCKETS = (("lt_1h", 3600), ("lt_6h", 6 * 3600), ("lt_1d", 86400),
               ("lt_3d", 3 * 86400), ("ge_3d", None))
ABSENT = "TABLE_ABSENT"


def _connect(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=4)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _has(conn, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (table,)).fetchone() is not None


def _stamp(value) -> dt.datetime | None:
    """Accept YYYYMMDDHHMMSS or ISO-like text; return naive datetime or None."""
    digits = re.sub(r"\D", "", str(value or ""))[:14]
    if len(digits) < 8:
        return None
    digits = digits.ljust(14, "0")
    try:
        return dt.datetime.strptime(digits, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _bucket(seconds: float) -> str:
    for name, limit in AGE_BUCKETS:
        if limit is None or seconds < limit:
            return name
    return AGE_BUCKETS[-1][0]


def _scrub(text: str) -> str:
    """Remove anything that could identify a symbol/price/host from a label."""
    return re.sub(r"\d{4,}", "#", str(text or ""))[:80]


def _counter(rows, *keys):
    out: dict = {}
    for row in rows:
        node = out
        for key in keys[:-1]:
            node = node.setdefault(str(row[key]), {})
        leaf = str(row[keys[-1]])
        node[leaf] = node.get(leaf, 0) + 1
    return out


def _latest_reference_time(conn) -> dt.datetime | None:
    best = None
    for table, column in (("v19_events", "at"), ("stream_health", "seen_at"),
                          ("executions", "seen_at")):
        if not _has(conn, table):
            continue
        row = conn.execute("SELECT %s AS t FROM %s ORDER BY rowid DESC LIMIT 1"
                           % (column, table)).fetchone()
        stamp = _stamp(row["t"]) if row else None
        if stamp and (best is None or stamp > best):
            best = stamp
    return best


def audit(path, now: dt.datetime | None = None, window_hours: int = 24) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError("database file not found")
    conn = _connect(path)
    try:
        reference = now or _latest_reference_time(conn)
        report: dict = {"schema": SCHEMA, "read_only": True,
                        "contains_symbols_prices_or_messages": False,
                        "reference_time_known": reference is not None,
                        "window_hours": window_hours}

        # 1. Orders ---------------------------------------------------------
        if _has(conn, "v19_orders"):
            rows = conn.execute("SELECT tf,status,since_ts FROM v19_orders").fetchall()
            report["orders_by_status_tf"] = _counter(rows, "status", "tf")
            pending = [r for r in rows if r["status"] == "PENDING"]
            ages: dict = {}
            unknown_age = 0
            for r in pending:
                since = _stamp(r["since_ts"])
                if since is None or reference is None:
                    unknown_age += 1
                    continue
                bucket = _bucket(max(0.0, (reference - since).total_seconds()))
                ages.setdefault(r["tf"], {}).setdefault(bucket, 0)
                ages[r["tf"]][bucket] += 1
            report["pending_age_buckets_by_tf"] = ages
            report["pending_age_unknown"] = unknown_age
            report["pending_total"] = len(pending)
        else:
            report["orders_by_status_tf"] = ABSENT
            report["pending_total"] = ABSENT

        # 2. Continuity audits ---------------------------------------------
        if _has(conn, "v19_higher_frame_audit14"):
            rows = conn.execute("SELECT tf,state,reason FROM v19_higher_frame_audit14").fetchall()
            report["higher_frame_audit_state_tf"] = _counter(rows, "state", "tf")
            reasons: dict = {}
            for r in rows:
                key = (str(r["state"]), _scrub(r["reason"]))
                reasons[key] = reasons.get(key, 0) + 1
            report["higher_frame_audit_reasons"] = {
                "%s/%s" % k: v for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])[:12]}
        else:
            report["higher_frame_audit_state_tf"] = ABSENT
        if _has(conn, "v19_pending_5m_audit"):
            rows = conn.execute("SELECT state FROM v19_pending_5m_audit").fetchall()
            report["pending_5m_audit_state"] = _counter(rows, "state")
        else:
            report["pending_5m_audit_state"] = ABSENT
        if _has(conn, "v19_gap_holds"):
            rows = conn.execute("SELECT tf,state FROM v19_gap_holds").fetchall()
            report["gap_holds_state_tf"] = _counter(rows, "state", "tf")
        else:
            report["gap_holds_state_tf"] = ABSENT

        # 3. Events (all-time and recent window) ----------------------------
        if _has(conn, "v19_events"):
            rows = conn.execute("SELECT at,event FROM v19_events").fetchall()
            total: dict = {}
            recent: dict = {}
            cutoff = (reference - dt.timedelta(hours=window_hours)) if reference else None
            for r in rows:
                name = _scrub(r["event"])
                total[name] = total.get(name, 0) + 1
                at = _stamp(r["at"])
                if cutoff is not None and at is not None and at >= cutoff:
                    recent[name] = recent.get(name, 0) + 1
            top = lambda d: dict(sorted(d.items(), key=lambda kv: -kv[1])[:20])
            report["events_all_time_top"] = top(total)
            report["events_recent_window_top"] = top(recent) if cutoff else "REFERENCE_TIME_UNKNOWN"
        else:
            report["events_all_time_top"] = ABSENT

        # 4. Fills and positions (BUY evidence) -----------------------------
        if _has(conn, "executions"):
            rows = conn.execute("SELECT tf,side FROM executions").fetchall()
            report["executions_side_tf"] = _counter(rows, "side", "tf")
        else:
            report["executions_side_tf"] = ABSENT
        if _has(conn, "positions"):
            report["positions_rows"] = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        else:
            report["positions_rows"] = ABSENT

        # 5. Feed / runtime errors (labels only) ----------------------------
        if _has(conn, "runtime_error_summary"):
            rows = conn.execute(
                "SELECT context,error_type,occurrences FROM runtime_error_summary "
                "ORDER BY occurrences DESC LIMIT 15").fetchall()
            report["runtime_errors_top"] = [
                {"context": _scrub(r["context"]), "error_type": _scrub(r["error_type"]),
                 "occurrences": r["occurrences"]} for r in rows]
        else:
            report["runtime_errors_top"] = ABSENT
        if _has(conn, "stream_health"):
            rows = conn.execute("SELECT state FROM stream_health").fetchall()
            report["stream_health_states"] = _counter(rows, "state")
        else:
            report["stream_health_states"] = ABSENT

        report["verdict_hints_INFERRED"] = _hints(report)
        return report
    finally:
        conn.close()


def _sum_leaf(node) -> int:
    if isinstance(node, dict):
        return sum(_sum_leaf(v) for v in node.values())
    return int(node) if isinstance(node, (int, float)) else 0


def _hints(report: dict) -> list:
    """Heuristic hints only. Each names the evidence it rests on."""
    hints = []
    pending = report.get("pending_total")
    sides = report.get("executions_side_tf")
    if pending == ABSENT or sides == ABSENT:
        return ["EVIDENCE_TABLES_ABSENT: cannot classify; do not read this as zero orders/fills"]
    buys = _sum_leaf(sides.get("BUY", {})) if isinstance(sides, dict) else 0
    if pending and not buys:
        hints.append("PENDING_WITHOUT_ANY_BUY: pending=%d, BUY executions=0" % pending)
    elif not pending:
        hints.append("NO_PENDING_ORDERS: healthy-empty vs not-ready cannot be told from this file alone")
    hf = report.get("higher_frame_audit_state_tf")
    blocked = _sum_leaf(hf.get("BLOCKED", {})) if isinstance(hf, dict) else 0
    if pending and blocked:
        hints.append("PARENT_CONTINUITY_BLOCKS_PRESENT: higher-frame audit BLOCKED=%d "
                     "(official parent bars unproven; compare with feed errors below)" % blocked)
    events = report.get("events_all_time_top")
    if isinstance(events, dict):
        touch = sum(v for k, v in events.items() if "LIMIT_TOUCH_NOT_BOUGHT" in k)
        guarded = sum(v for k, v in events.items() if "GAP_GUARDED" in k)
        total = sum(events.values()) or 1
        if touch and touch / total >= 0.2:
            hints.append("PRICE_TOUCHED_BUT_NOT_BOUGHT: %d events (venue depth / signed score / "
                         "wallet gate, not a missing-bar problem)" % touch)
        if guarded and guarded / total >= 0.2:
            hints.append("GAP_GUARD_DOMINANT: %d events (continuity guard is the main blocker)" % guarded)
    errors = report.get("runtime_errors_top")
    if isinstance(errors, list):
        dns = [e for e in errors if re.search(r"(?i)dns|name|gaierror|resolution|connection", e["error_type"] + e["context"])]
        if dns and pending and not buys:
            hints.append("FEED_ERRORS_COEXIST: %d network-like runtime error labels in the top 15 "
                         "(correlate times on the phone before concluding)" % len(dns))
    if not hints:
        hints.append("NO_DOMINANT_PATTERN")
    return hints


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("database", help="copy of the exported stock AT SQLite (opened read-only)")
    parser.add_argument("--now", help="reference time YYYYMMDDHHMMSS (default: newest row in the file)")
    parser.add_argument("--window-hours", type=int, default=24)
    args = parser.parse_args(argv)
    now = _stamp(args.now) if args.now else None
    if args.now and now is None:
        parser.error("--now must look like YYYYMMDDHHMMSS")
    try:
        result = audit(args.database, now, args.window_hours)
    except (OSError, sqlite3.DatabaseError) as error:
        print(json.dumps({"schema": SCHEMA, "ok": False, "error": type(error).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
