"""0.6.5 (owner 2026-11-27, stage 2 of the KRX + NXT integration): how much would the strategy decisions change if they were made on the COMBINED (KRX + NXT) candles?

Stage 1 (0.4.4 / 0.6.0) collects the combined 5m / 30m candles of every managed stock into `stock/all_venue_bars_v0440.sqlite3` (table `bars_all`). The strategies still decide on the
regular-session bars in `bars` (the vendor engine and 17 vendor files assume 09:00-15:30). Switching them blind would change every stock decision silently. This module measures the difference
from the stored data, in the quiet hours, once per trading day, local reads only (no network, no order, no write outside its own report):

per frame (5m, 30m) and managed stock that has both series:
* trend label (`market_regime_v0290.classify`, newest 90 completed closes) on the regular series vs on the combined series -> same / different;
* the side of the last close against its MA20 on both series -> same / different;
* per trading day the combined volume divided by the regular volume (median, max) and the share of combined candles outside 09:00-15:30.
Readiness = stocks with at least READY_DAYS trading days of combined candles / managed stocks. The strategies are switched to the combined series only when the owner-facing numbers say it is safe
(readiness >= 0.9 and the label difference is understood); until then this report is the evidence. Result: `stock/combined_vs_regular_v0650.json` (+ one `.jsonl` line per day).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import statistics
import threading
from pathlib import Path

import offhours_prework_v0530 as base

VERSION = "COMBINED_IMPACT_V0650"
KST = dt.timezone(dt.timedelta(hours=9))
FRAMES = ("5m", "30m")
CLOSES = 90
READY_DAYS = 5
STATE_FILE = "combined_vs_regular_v0650.json"
LOG_FILE = "combined_vs_regular_v0650.jsonl"
_LOCK = threading.Lock()


def _closes(rows):
    return [float(r[0]) for r in rows if r[0] is not None and float(r[0]) > 0]


def _ma20_side(closes):
    if len(closes) < 20:
        return None
    ma = sum(closes[-20:]) / 20.0
    last = closes[-1]
    return "ABOVE" if last > ma else "BELOW" if last < ma else "EQUAL"


def _label(closes):
    try:
        import market_regime_v0290 as regime
        label, _ = regime.classify(closes)
        return label or "UNVERIFIED"
    except Exception:
        return "UNVERIFIED"


def compare(regular_db, combined_path, frame, as_of_stamp):
    """Per-code comparison for one frame. `regular_db` is a sqlite connection with `bars`, `combined_path` the bars_all file."""
    out = {"codes": 0, "trend_same": 0, "trend_diff": 0, "trend_unverified": 0, "ma20_same": 0, "ma20_diff": 0, "ratios": [], "outside_regular": 0, "combined_candles": 0, "examples": []}
    with sqlite3.connect("file:%s?mode=ro" % Path(combined_path).as_posix(), uri=True, timeout=5) as comb:
        codes = [r[0] for r in comb.execute("SELECT DISTINCT code FROM bars_all WHERE frame=? AND ts<=?", (frame, as_of_stamp)).fetchall()]
        for code in codes:
            reg = _closes(reversed(regular_db.execute("SELECT close FROM bars WHERE code=? AND frame=? AND ts<=? AND source<>'' AND source NOT LIKE '%EXT_%' ORDER BY ts DESC LIMIT ?", (code, frame, as_of_stamp, CLOSES)).fetchall()))
            allv = _closes(reversed(comb.execute("SELECT close FROM bars_all WHERE code=? AND frame=? AND ts<=? ORDER BY ts DESC LIMIT ?", (code, frame, as_of_stamp, CLOSES)).fetchall()))
            if not reg or not allv:
                continue
            out["codes"] += 1
            a, b = _label(reg), _label(allv)
            if "UNVERIFIED" in (a, b):
                out["trend_unverified"] += 1
            elif a == b:
                out["trend_same"] += 1
            else:
                out["trend_diff"] += 1
                if len(out["examples"]) < 10:
                    out["examples"].append({"code": code, "regular": a, "combined": b})
            sa, sb = _ma20_side(reg), _ma20_side(allv)
            if sa and sb:
                out["ma20_same" if sa == sb else "ma20_diff"] += 1
        for code, day, vol in comb.execute("SELECT code,substr(ts,1,8),SUM(volume) FROM bars_all WHERE frame=? AND ts<=? GROUP BY code,substr(ts,1,8)", (frame, as_of_stamp)).fetchall():
            row = regular_db.execute("SELECT SUM(volume) FROM bars WHERE code=? AND frame=? AND substr(ts,1,8)=? AND source NOT LIKE '%EXT_%'", (code, frame, day)).fetchone()
            if row and row[0] and float(row[0]) > 0 and vol:
                out["ratios"].append(float(vol) / float(row[0]))
        row = comb.execute("SELECT COUNT(*), SUM(CASE WHEN substr(ts,9,4)<'0900' OR substr(ts,9,4)>='1530' THEN 1 ELSE 0 END) FROM bars_all WHERE frame=? AND ts<=?", (frame, as_of_stamp)).fetchone()
        out["combined_candles"], out["outside_regular"] = int(row[0] or 0), int(row[1] or 0)
    ratios = out.pop("ratios")
    out["volume_ratio_median"] = round(statistics.median(ratios), 4) if ratios else None
    out["volume_ratio_max"] = round(max(ratios), 4) if ratios else None
    out["outside_regular_share"] = round(out["outside_regular"] / out["combined_candles"], 4) if out["combined_candles"] else None
    return out


def readiness(combined_path, managed_codes, as_of_stamp):
    with sqlite3.connect("file:%s?mode=ro" % Path(combined_path).as_posix(), uri=True, timeout=5) as comb:
        rows = comb.execute("SELECT code,COUNT(DISTINCT substr(ts,1,8)) FROM bars_all WHERE frame='30m' AND ts<=? GROUP BY code", (as_of_stamp,)).fetchall()
    ready = {str(c) for c, n in rows if int(n) >= READY_DAYS}
    managed = set(managed_codes)
    return {"managed": len(managed), "with_combined_days": len({str(c) for c, _ in rows} & managed), "ready": len(ready & managed), "ready_share": round(len(ready & managed) / len(managed), 4) if managed else None, "ready_days": READY_DAYS}


def run(runtime, now=None):
    """Once per trading day in the quiet window. Never raises; returns the report."""
    fixed = now
    now = now or dt.datetime.now(KST)
    report = {"version": VERSION, "ran": False}
    try:
        day = base.target_day(now)
        root = getattr(runtime, "root", None)
        runner = getattr(runtime, "stock_runner", None)
        store = getattr(runner, "store", None)
        if day is None or not root or store is None or not base.in_window(now):
            return report
        key = day.isoformat()
        state_path = Path(root) / "stock" / STATE_FILE
        try:
            state = json.loads(state_path.read_text("utf8"))
        except Exception:
            state = {}
        if key in state:
            return report
        combined = Path(root) / "stock" / "all_venue_bars_v0440.sqlite3"
        if not combined.exists():
            return report
        stamp = day.strftime("%Y%m%d") + "235959"
        with _LOCK, store.connect() as db:
            managed = [r[0] for r in db.execute("SELECT DISTINCT code FROM bars WHERE frame='5m' AND ts>=?", ((day - dt.timedelta(days=7)).strftime("%Y%m%d") + "000000",)).fetchall()]
            result = {"at": now.isoformat(timespec="seconds"), "day": key, "readiness": readiness(combined, managed, stamp), "frames": {f: compare(db, combined, f, stamp) for f in FRAMES}}
        state[key] = result
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), "utf8")
        os.replace(tmp, state_path)
        try:
            with open(Path(root) / "stock" / LOG_FILE, "a", encoding="utf8") as handle:
                handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            pass
        report = dict(result, ran=True, version=VERSION)
    except Exception as exc:
        report["error"] = (type(exc).__name__ + ":" + str(exc))[:200]
    return report
