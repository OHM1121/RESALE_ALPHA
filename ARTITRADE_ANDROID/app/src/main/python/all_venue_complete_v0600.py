"""0.6.0 (owner 2026-11-27 "통합(NXT 포함) 봉 보충: 이것도 진행해"): the quiet hours complete the combined (통합 = KRX + NXT) 5m / 30m candles of the managed stocks.

Stage 1 of the combined flow (`stock_all_venue_bars_v0440`) only probed ~10 stocks every 15 minutes during the day. The device data shows the question is answered: all 160 probes of
2026-11-27 succeeded; one request (`stk_cd` + `_AL`, `ka10080`) returns about 900 candles (5m: ~2 weeks, 30m: ~2 months), including the NXT pre-market (08:xx) and after-market (16-19:xx).
This module fills the SAME separate store (`stock/all_venue_bars_v0440.sqlite3`, table `bars_all`; `INSERT OR IGNORE`, an existing candle is never replaced) for EVERY managed stock
(stocks that have bars in the last 5 trading days), in the quiet window (after the NXT close, before the next open; on a weekend / holiday also in deeper pages), at a gentle pace.
Nothing else changes: the shared `bars` table, the strategies, the wallets and the ledger are not touched (stage 2, the switch of MA / MarketTrend / reference-bar decisions to the combined
series, is a separate owner decision that needs this data first).
Rules: only COMPLETED candles (the forming one is never stored), positive prices and volume, sane OHLC; the code asked is the code stored; at most PAGES pages per stock and frame (weekend:
WEEKEND_PAGES) and never older than HORIZON_DAYS; BATCH stocks per round and ROUND_REST seconds of rest between rounds; PAUSE seconds between requests; yields at once to a busy tick queue;
three failures in a row pause the work for 30 minutes; each stock is attempted at most ATTEMPTS times.
Result: `stock/offhours_al_v0600.json` (resume + coverage: stocks done, candles added, stocks whose combined series shows NXT hours) and `.jsonl`.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import time
from pathlib import Path

import offhours_prework_v0530 as base
import stock_all_venue_bars_v0440 as stage1

VERSION = "ALL_VENUE_COMPLETE_V0600"
FRAMES = ("5m", "30m")
PAGES = 2
WEEKEND_PAGES = 6
HORIZON_DAYS = 45
PAUSE = 1.0
BATCH = 60                                                   # 0.6.7: was 40
ROUND_REST = 120.0                                           # 0.6.7: was 600 (a whole night held about 13 rounds of 40 with ten minutes rest each, behind the other work)
ATTEMPTS = 3
MAX_REQUESTS = 6000
STATE_FILE = "offhours_al_v0600.json"
LOG_FILE = "offhours_al_v0600.jsonl"
KST = dt.timezone(dt.timedelta(hours=9))

_STATE = {"running": False, "progress": [0, 0], "failures": 0, "paused_until": 0.0, "next_round": 0.0}


def _paths(root):
    folder = Path(root) / "stock"
    return folder / STATE_FILE, folder / LOG_FILE, folder / stage1.FILE


def load_state(root):
    try:
        return json.loads(_paths(root)[0].read_text("utf8"))
    except Exception:
        return {}


def save_state(root, state):
    try:
        path = _paths(root)[0]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), "utf8")
        os.replace(tmp, path)
    except Exception:
        pass


def log(root, event):
    try:
        path = _paths(root)[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def fetch_pages(api, code, frame, now, pages, horizon_stamp, sleep=time.sleep):
    """Combined candles of one stock and frame over several pages: {ts: (o, h, l, c, v)} (completed only). Raises on a request error of the first page."""
    body = {"stk_cd": code + stage1.SUFFIX, "tic_scope": str(stage1.FRAMES[frame]), "upd_stkpc_tp": "1", "base_dt": now.strftime("%Y%m%d")}
    out, cont, next_key = {}, "N", ""
    for page in range(pages):
        try:
            packet = api.post("ka10080", "/api/dostk/chart", body, cont, next_key)
        except Exception:
            if page == 0:
                raise
            break                                              # an older page that fails is simply not stored now
        rows = stage1.parse_rows(packet, frame, now)
        out.update(rows)
        oldest = min(rows) if rows else None
        cont = str((packet or {}).get("_page_cont_yn", "N")).upper()
        next_key = str((packet or {}).get("_page_next_key", ""))
        if cont != "Y" or not next_key or not rows or (oldest is not None and oldest[:8] <= horizon_stamp):
            break
        sleep(PAUSE)                                           # one request per PAUSE seconds, also between the pages of one stock
    return out


def store_rows(db_path, code, frame, rows, now):
    """INSERT OR IGNORE into bars_all. Returns the number of new candles."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cx = sqlite3.connect(str(path), timeout=10)
    try:
        cx.executescript(stage1.DDL)
        before = cx.total_changes
        cx.executemany("INSERT OR IGNORE INTO bars_all(code,frame,ts,open,high,low,close,volume,fetched_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       [(code, frame, ts, *v, now.isoformat(timespec="seconds")) for ts, v in sorted(rows.items())])
        cx.commit()
        return cx.total_changes - before
    finally:
        cx.close()


def has_nxt_hours(rows):
    """True when the combined series shows the NXT pre-market (08) or after-market (16-19) hours: a hint that the stock trades on NXT."""
    return any(ts[8:10] == "08" or "16" <= ts[8:10] <= "19" for ts in rows)


def managed(store, last_day):
    days = []
    probe = last_day
    while len(days) < 5:
        if base._is_trading_day(probe):
            days.append(probe)
        probe -= dt.timedelta(days=1)
    first = days[-1].strftime("%Y%m%d") + "000000"
    with store.connect() as db:
        rows = db.execute("SELECT DISTINCT code FROM bars WHERE frame='5m' AND ts>=?", (first,)).fetchall()
    return sorted(str(r[0]) for r in rows)


def run(runtime, now=None, sleep=time.sleep):
    """One round (at most BATCH stocks); resumes from stock/offhours_al_v0600.json. Never raises; returns a summary dict."""
    fixed = now
    now = now or dt.datetime.now(KST)
    summary = {"ran": False}
    try:
        last = base.target_day(now)
        runner = getattr(runtime, "stock_runner", None)
        root = getattr(runtime, "root", None)
        api, store = getattr(runner, "api", None), getattr(runner, "store", None)
        if last is None or not root or api is None or store is None:
            return summary
        if time.monotonic() < _STATE["paused_until"] or time.monotonic() < _STATE["next_round"]:
            return summary
        key = last.isoformat()
        state = load_state(root)
        task = state.setdefault(key, {"done": [], "attempts": {}, "unresolved": [], "added": 0, "requests": 0, "nxt_hours": [], "finished": False})
        if task["finished"]:
            return summary
        codes = managed(store, last)
        done = set(task["done"])
        pages = WEEKEND_PAGES if base.long_window(now) else PAGES
        horizon = (last - dt.timedelta(days=HORIZON_DAYS)).strftime("%Y%m%d")
        db_path = _paths(root)[2]
        todo = [c for c in codes if c not in done and int(task["attempts"].get(c, 0)) < ATTEMPTS][:BATCH]
        summary.update(ran=True, day=key, todo=len(todo), pages=pages)
        if "started_at" not in task:
            task["started_at"] = now.isoformat(timespec="seconds")
            task["codes_total"] = len(codes)
            log(root, {"at": task["started_at"], "event": "START", "day": key, "codes": len(codes), "pages": pages})
        _STATE["running"] = True
        _STATE["progress"] = [len(done), len(codes)]
        for code in todo:
            moment = fixed if fixed is not None else dt.datetime.now(KST)
            if not base.in_window(moment) or task["requests"] >= MAX_REQUESTS:
                break
            try:
                if runner._tick_queue.qsize() >= 24:
                    break
            except Exception:
                pass
            try:
                added, seen_nxt = 0, False
                for frame in FRAMES:
                    rows = fetch_pages(api, code, frame, moment, pages, horizon, sleep=sleep)
                    task["requests"] += pages
                    added += store_rows(db_path, code, frame, rows, moment)
                    seen_nxt = seen_nxt or has_nxt_hours(rows)
                _STATE["failures"] = 0
            except Exception as exc:
                _STATE["failures"] += 1
                kind = base.note_error(exc)
                if kind not in base.ENV_KINDS:                                      # 0.6.5: environment errors do not burn an attempt
                    task["attempts"][code] = int(task["attempts"].get(code, 0)) + 1
                    if task["attempts"][code] >= ATTEMPTS and code not in task["unresolved"]:
                        task["unresolved"].append(code)
                log(root, {"at": moment.isoformat(timespec="seconds"), "event": "ERROR", "code": code, "kind": kind, "attempt": task["attempts"].get(code, 0), "error": (type(exc).__name__ + ":" + str(exc))[:160]})
                if _STATE["failures"] >= 3:
                    _STATE["paused_until"] = time.monotonic() + base.PAUSE_SECONDS
                    _STATE["failures"] = 0
                    break
                continue
            task["done"].append(code)
            done.add(code)
            task["added"] += added
            if seen_nxt:
                task["nxt_hours"].append(code)
            _STATE["progress"][0] = len(done)
        remaining = [c for c in codes if c not in done and int(task["attempts"].get(c, 0)) < ATTEMPTS]
        if not remaining:
            task["finished"] = True
            task["finished_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
            log(root, {"at": task["finished_at"], "event": "DONE", "day": key, "codes": len(codes), "added": task["added"], "requests": task["requests"],
                       "stocks_with_nxt_hours": len(task["nxt_hours"]), "unresolved": task["unresolved"]})
        else:
            _STATE["next_round"] = time.monotonic() + ROUND_REST          # rest between rounds: no hurry in the quiet window
        save_state(root, state)
        summary.update(finished=bool(task["finished"]), added=task["added"], remaining=len(remaining))
    except Exception as exc:
        summary["error"] = (type(exc).__name__ + ":" + str(exc))[:200]
    finally:
        _STATE["running"] = False
    return summary
