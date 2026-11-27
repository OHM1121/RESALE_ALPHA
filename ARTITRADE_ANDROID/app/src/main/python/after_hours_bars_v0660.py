"""0.6.6 (owner 2026-11-27: KRX and NXT are one market): the stock's bar series is continuous across the sessions.

From 0.6.6 the engine writes every completed NXT pre-market (08:00-08:30) and KRX after-hours (16:00-20:00) frame to `bars` the moment it is built (source LIVE_EXT_<venue>_<segment>).
This quiet-hours job brings the PAST into the same shape, so MA / MarketTrend / reference-bar decisions see the same continuous series for the days before the update:
1. completed frames of `v19_venue_frames` (live, built by the engine since 2026-11-19) -> `bars`, source LIVE_EXT_<venue>_<segment>;
2. the official combined candles of `stock/all_venue_bars_v0440.sqlite3` (5m / 30m) whose start lies in the extended sessions (08:00-08:30, 16:00-20:00) -> `bars`, source AL_EXT_OFFICIAL, only where no live frame exists.
`INSERT OR IGNORE`: a bar that exists is never replaced. Regular-session bars are not touched. Local reads and one local write; no network, no order. Once per trading day; report `stock/after_hours_bars_v0660.json`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

import offhours_prework_v0530 as base

VERSION = "AFTER_HOURS_BARS_V0660"
KST = dt.timezone(dt.timedelta(hours=9))
STATE_FILE = "after_hours_bars_v0660.json"


def _extended(ts):
    hhmm = str(ts)[8:12]
    return "0800" <= hhmm < "0830" or "1600" <= hhmm < "2000"


def copy_frames(db):
    """completed engine frames -> bars. Returns the number of new rows."""
    if not (base._has_table(db, "v19_venue_frames") and base._has_table(db, "bars")):
        return 0
    before = db.total_changes
    db.execute("""INSERT OR IGNORE INTO bars(code,market,frame,ts,open,high,low,close,volume,source)
        SELECT f.code, COALESCE((SELECT market FROM stocks s WHERE s.code=f.code),(SELECT market FROM bars b WHERE b.code=f.code AND b.market IN ('KOSPI','KOSDAQ') LIMIT 1)),
               f.frame, f.ts, f.open, f.high, f.low, f.close, f.volume, 'LIVE_EXT_'||f.venue||'_'||f.segment
        FROM v19_venue_frames f
        WHERE f.frame IN ('5m','30m','60m') AND COALESCE((SELECT market FROM stocks s WHERE s.code=f.code),(SELECT market FROM bars b WHERE b.code=f.code AND b.market IN ('KOSPI','KOSDAQ') LIMIT 1)) IS NOT NULL""")
    return db.total_changes - before


def copy_official(db, combined_path):
    """official combined candles of the extended sessions -> bars (where nothing exists). Returns the number of new rows."""
    if not Path(combined_path).exists() or not base._has_table(db, "bars"):
        return 0
    added = 0
    with sqlite3.connect("file:%s?mode=ro" % Path(combined_path).as_posix(), uri=True, timeout=5) as comb:
        rows = comb.execute("SELECT code,frame,ts,open,high,low,close,volume FROM bars_all WHERE frame IN ('5m','30m') AND ((substr(ts,9,4)>='0800' AND substr(ts,9,4)<'0830') OR (substr(ts,9,4)>='1600' AND substr(ts,9,4)<'2000'))").fetchall()
    markets = {}
    for code, frame, ts, o, h, l, c, v in rows:
        if code not in markets:
            row = db.execute("SELECT market FROM stocks WHERE code=?", (code,)).fetchone() or db.execute("SELECT market FROM bars WHERE code=? AND market IN ('KOSPI','KOSDAQ') LIMIT 1", (code,)).fetchone()
            markets[code] = row[0] if row else None
        if markets[code] is None or not (o and h and l and c) or float(v or 0) <= 0:
            continue
        before = db.total_changes
        db.execute("INSERT OR IGNORE INTO bars(code,market,frame,ts,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?,?,?)", (code, markets[code], frame, ts, o, h, l, c, v, "AL_EXT_OFFICIAL"))
        added += db.total_changes - before
    return added


def run(runtime, now=None):
    """Once per trading day in the quiet window. Never raises; returns the report."""
    now = now or dt.datetime.now(KST)
    report = {"version": VERSION, "ran": False}
    try:
        day = base.target_day(now)
        root = getattr(runtime, "root", None)
        store = getattr(getattr(runtime, "stock_runner", None), "store", None)
        if day is None or not root or store is None or not base.in_window(now):
            return report
        key = day.isoformat()
        path = Path(root) / "stock" / STATE_FILE
        try:
            state = json.loads(path.read_text("utf8"))
        except Exception:
            state = {}
        if key in state:
            return report
        with store.lock, store.connect() as db:
            frames = copy_frames(db)
            official = copy_official(db, Path(root) / "stock" / "all_venue_bars_v0440.sqlite3")
            db.commit()
        state[key] = {"at": now.isoformat(timespec="seconds"), "frames_copied": frames, "official_copied": official}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), "utf8")
        os.replace(tmp, path)
        report = dict(state[key], ran=True, version=VERSION)
    except Exception as exc:
        report["error"] = (type(exc).__name__ + ":" + str(exc))[:200]
    return report
