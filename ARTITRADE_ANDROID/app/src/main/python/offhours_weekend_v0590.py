"""0.5.9 (owner 2026-11-27): the long quiet window (Friday 20:05 -> Monday 07:45, also holidays) is used for a DEEP AUDIT of the managed stock data.

Owner: "오늘 장마감 후 ~ 월요일 장시작 전까지 누락데이터, 누락봉 채워넣고, 관리중 데이터 전체 확인하고 부족한 것 또 찾고 채워넣고, 처리지연으로 지금까지 처리 안 된 것들 정리".

What it does (stock only, read from / written to the bars table like `offhours_prework_v0530`; no signal, order, position or ledger write):
1. Pass 1 (regular session candles): for every stock that has bars in the last DAYS_BACK trading days and for the frames 5m / 30m / 60m, ask the broker once for a deep window
   (several pages, `base_dt` = today), and INSERT OR IGNORE every finished regular-session candle of those days that the database does not have (same checks as the vendor repair:
   regular KRX reference slot, closed, positive volume, sane OHLC, requested code and day). Frame `day`: the last DAY_BARS finished daily candles.
2. Pass 2 / 3: the (stock, frame) pairs that failed (broker error) are asked again, at most PASSES passes in total. Each pass re-counts the stored candles of the managed stocks
   against the day's expected candle slots; a slot the broker also does not have is a candle that never traded (verified empty), not a hole.
3. Backlog inventory (read only): how many unverified / queued items the vendor diagnostics wrote in the last 7 days (issues whose message says "검증 미완료", "대기 초과",
   "지연"), at the start and at the end, so the owner sees whether the pile has stopped growing. The vendor's own retry queues are not touched.
Budget: MAX_REQUESTS broker requests per window, PAUSE seconds between requests, yields at once to a running live feed, three failures in a row pause the work for 30 minutes.
Result: `stock/offhours_v0590.json` (resume) and `stock/offhours_v0590.jsonl`; one short line on the market feed card (`status_line`).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import offhours_prework_v0530 as base

VERSION = "OFFHOURS_WEEKEND_V0590"
SOURCE = "KIWOOM_REST_OFFHOURS_V0590"
FRAMES = ("5m", "30m", "60m")
DAYS_BACK = 5
DAY_BARS = 20
DEEP_PAGES = 12
NEEDED = {"5m": 5 * 70, "30m": 5 * 14, "60m": 5 * 8}
PAUSE = 0.4
MAX_REQUESTS = 8000
PASSES = 3
STATE_FILE = "offhours_v0590.json"
LOG_FILE = "offhours_v0590.jsonl"
KST = dt.timezone(dt.timedelta(hours=9))
BACKLOG_WORDS = ("검증 미완료", "대기 초과", "지연")

_STATE = {"running": False, "progress": [0, 0], "failures": 0, "paused_until": 0.0}


def _paths(root):
    folder = Path(root) / "stock"
    return folder / STATE_FILE, folder / LOG_FILE


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


def trading_days(last_day, count):
    days, probe = [], last_day
    while len(days) < count:
        if base._is_trading_day(probe):
            days.append(probe)
        probe -= dt.timedelta(days=1)
        if (last_day - probe).days > 40:
            break
    return days


def expected_slots(day, frame):
    """Candle start stamps a stock could have on `day` in the regular session (vendor reference rule), oldest first."""
    from d_engine import bar_end, is_krx_regular_reference
    out, close_stamp = [], day.strftime("%Y%m%d") + "153000"
    for minute in range(9 * 60, 15 * 60 + 30):
        ts = day.strftime("%Y%m%d") + "%02d%02d00" % (minute // 60, minute % 60)
        try:
            if is_krx_regular_reference(ts, frame) and bar_end(ts, frame) <= close_stamp:
                out.append(ts)
        except Exception:
            continue
    return out


def _int(value):
    try:
        return int(abs(float(str(value).replace(",", "").strip())))
    except (TypeError, ValueError, OverflowError):
        return 0


def deep_history(api, code, frame, needed, pages=DEEP_PAGES, now=None):
    """Like the vendor `history_bars` but with a bigger window; returns (code, frame, ts, o, h, l, c, v) rows, oldest first."""
    from d_engine import bar_end, is_krx_regular_reference
    now = now or dt.datetime.now(KST)
    today = now.strftime("%Y%m%d")
    scope = {"5m": "5", "30m": "30", "60m": "60"}[frame]
    body = {"stk_cd": code, "tic_scope": scope, "upd_stkpc_tp": "1", "base_dt": today}
    raw, cont, next_key = [], "N", ""
    for _ in range(pages):
        data = api.post("ka10080", "/api/dostk/chart", body, cont, next_key)
        rows = data.get("stk_min_pole_chart_qry") or data.get("stk_mnt_pole_chart_qry") or []
        raw.extend(rows)
        if len({str(r.get("cntr_tm") or "") for r in raw}) >= needed:
            break
        cont = str(data.get("_page_cont_yn", "N")).upper()
        next_key = str(data.get("_page_next_key", ""))
        if cont != "Y" or not next_key or not rows:
            break
    out = {}
    for row in raw:
        digits = "".join(c for c in str(row.get("cntr_tm") or "") if c.isdigit())
        if len(digits) < 8:
            continue
        ts = digits[:14].ljust(14, "0")
        try:
            if ts[:8] > today or not is_krx_regular_reference(ts, frame) or bar_end(ts, frame) > now.strftime("%Y%m%d%H%M%S"):
                continue
        except Exception:
            continue
        o, h, lo, cl, vol = (_int(row.get(k)) for k in ("open_pric", "high_pric", "low_pric", "cur_prc", "trde_qty"))
        if min(o, h, lo, cl) <= 0 or not (lo <= o <= h and lo <= cl <= h):
            continue
        out[ts] = (code, frame, ts, o, h, lo, cl, vol)
    return [out[k] for k in sorted(out)]


def backlog_inventory(store, now=None):
    """Read-only count of the vendor's 'unverified / queue exceeded / delayed' diagnostics of the last 7 days."""
    now = now or dt.datetime.now(KST)
    since = (now - dt.timedelta(days=7)).isoformat()
    try:
        with store.connect() as db:
            if not base._has_table(db, "issues"):
                return {}
            rows = db.execute("SELECT message,COUNT(*) FROM issues WHERE seen_at>=? GROUP BY message", (since,)).fetchall()
    except Exception:
        return {}
    return {str(m): int(n) for m, n in rows if any(w in str(m) for w in BACKLOG_WORDS)}


def audit_code(store, api, code, days, now, sleep=time.sleep, budget=None):
    """Deep-fetch the frames of one stock and insert the missing finished candles of `days`. Returns ({frame: (missing, inserted)}, requests)."""
    report, requests = {}, 0
    for frame in FRAMES:
        if budget is not None and requests >= budget:
            break
        rows = deep_history(api, code, frame, NEEDED[frame], now=now)
        requests += max(1, 1 + len(rows) // 400)
        missing_total = inserted_total = 0
        for day in days:
            official = base.valid_rows(rows, code, frame, day, now)
            if not official:
                continue
            stamp = day.strftime("%Y%m%d")
            with store.connect() as db:
                have = {str(r[0]) for r in db.execute("SELECT ts FROM bars WHERE code=? AND frame=? AND ts>=? AND ts<?", (code, frame, stamp + "000000", stamp + "240000")).fetchall()}
                market = base._market(db, code)
            missing = {ts: v for ts, v in official.items() if ts not in have}
            missing_total += len(missing)
            if missing and market:
                batch = [(code, market, frame, ts, *v, SOURCE) for ts, v in sorted(missing.items())]
                with store.lock, store.connect() as db:
                    before = db.total_changes
                    db.executemany("INSERT OR IGNORE INTO bars(code,market,frame,ts,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
                    inserted_total += db.total_changes - before
        report[frame] = (missing_total, inserted_total)
        sleep(PAUSE)
    return report, requests


def managed_codes(store, days):
    first = days[-1].strftime("%Y%m%d") + "000000"
    with store.connect() as db:
        rows = db.execute("SELECT DISTINCT code FROM bars WHERE frame='5m' AND ts>=?", (first,)).fetchall()
    return sorted(str(r[0]) for r in rows)


def coverage(store, codes, days):
    """(stored, expected) candle counts of the managed stocks that traded that day (a stock with no candle that day is not counted as expected)."""
    stored = expected = 0
    with store.connect() as db:
        for day in days:
            stamp = day.strftime("%Y%m%d")
            for frame in FRAMES:
                slots = len(expected_slots(day, frame))
                rows = db.execute("SELECT code,COUNT(*) FROM bars WHERE frame=? AND ts>=? AND ts<? GROUP BY code", (frame, stamp + "000000", stamp + "240000")).fetchall()
                stored += sum(int(n) for c, n in rows if c in set(codes))
                expected += slots * sum(1 for c, n in rows if c in set(codes))
    return stored, expected


def run_audit(runtime, now=None, sleep=time.sleep):
    """One round of the audit; resumes from stock/offhours_v0590.json. Never raises; returns a summary dict."""
    fixed = now                                    # tests pass a fixed moment; the app reads the clock for every stock
    now = now or dt.datetime.now(KST)
    summary = {"ran": False}
    try:
        last = base.target_day(now)
        runner = getattr(runtime, "stock_runner", None)
        root = getattr(runtime, "root", None)
        api, store = getattr(runner, "api", None), getattr(runner, "store", None)
        if last is None or not base.long_window(now) or not root or api is None or store is None:
            return summary
        if time.monotonic() < _STATE["paused_until"]:
            return summary
        key = last.isoformat()
        state = load_state(root)
        task = state.setdefault(key, {"done_codes": [], "attempts": {}, "unresolved": [], "filled": 0, "missing_found": 0, "requests": 0, "finished": False})
        if task["finished"]:
            return summary
        days = trading_days(last, DAYS_BACK)
        codes = managed_codes(store, days)
        if "backlog_start" not in task:
            task["backlog_start"] = backlog_inventory(store, now)
            task["coverage_start"] = list(coverage(store, codes, days))
            log(root, {"at": now.isoformat(), "event": "START", "day": key, "codes": len(codes), "backlog": task["backlog_start"], "coverage": task["coverage_start"]})
        done = set(task["done_codes"])

        def pending():
            return [c for c in codes if c not in done and int(task["attempts"].get(c, 0)) < PASSES]

        todo = pending()
        summary.update(ran=True, day=key, todo=len(todo))
        _STATE["running"] = True
        _STATE["progress"] = [len(done), len(codes)]
        for code in todo:
            moment = fixed if fixed is not None else dt.datetime.now(KST)
            if not base.in_window(moment) or task["requests"] >= MAX_REQUESTS:
                break
            try:
                if runner._tick_queue.qsize() >= 24:
                    break                                          # the live feed has priority
            except Exception:
                pass
            try:
                report, used = audit_code(store, api, code, days, moment, sleep=sleep, budget=MAX_REQUESTS - task["requests"])
                _STATE["failures"] = 0
            except Exception as exc:
                _STATE["failures"] += 1
                task["attempts"][code] = int(task["attempts"].get(code, 0)) + 1       # asked again in a later round, at most PASSES times in all
                if task["attempts"][code] >= PASSES and code not in task["unresolved"]:
                    task["unresolved"].append(code)
                log(root, {"at": moment.isoformat(), "event": "ERROR", "code": code, "attempt": task["attempts"][code], "error": (type(exc).__name__ + ":" + str(exc))[:160]})
                if _STATE["failures"] >= 3:
                    _STATE["paused_until"] = time.monotonic() + 1800.0
                    _STATE["failures"] = 0
                    break
                continue
            task["done_codes"].append(code)
            done.add(code)
            task["requests"] += used
            task["missing_found"] += sum(v[0] for v in report.values())
            task["filled"] += sum(v[1] for v in report.values())
            _STATE["progress"][0] = len(done)
            if any(v[0] for v in report.values()):
                log(root, {"at": moment.isoformat(), "event": "FILLED", "code": code, "missing_and_inserted": {f: list(v) for f, v in report.items()}})
            if len(done) % 25 == 0:
                save_state(root, state)
        if not pending():
            task["finished"] = True
            task["finished_at"] = dt.datetime.now(KST).isoformat()
            task["backlog_end"] = backlog_inventory(store, now)
            task["coverage_end"] = list(coverage(store, codes, days))
            log(root, {"at": task["finished_at"], "event": "DONE", "day": key, "codes": len(codes), "missing_found": task["missing_found"], "filled": task["filled"],
                       "requests": task["requests"], "unresolved": task["unresolved"], "backlog_end": task["backlog_end"], "coverage_end": task["coverage_end"]})
        save_state(root, state)
        summary.update(finished=bool(task["finished"]), filled=task["filled"], missing_found=task["missing_found"])
    except Exception as exc:
        summary["error"] = (type(exc).__name__ + ":" + str(exc))[:200]
    finally:
        _STATE["running"] = False
    return summary


def status_line(root, day, now=None):
    """Very short text for the market feed card: running / paused / done of the weekend audit ({} when it has nothing to say)."""
    try:
        if not base.long_window(now or dt.datetime.now(KST)):
            return {}
        if _STATE["running"]:
            done, total = _STATE["progress"]
            return {"phase": "running", "label": "주말 데이터 점검·보충 중" + (" %d/%d" % (done, total) if total else "")}
        if time.monotonic() < _STATE["paused_until"]:
            return {"phase": "paused", "label": "주말 데이터 점검 잠시 중지 · 재시도 대기"}
        task = (load_state(root).get(day.isoformat()) or {})
        if task.get("finished"):
            return {"phase": "done", "label": "주말 데이터 점검·보충 완료 · %d개 보충" % int(task.get("filled", 0))}
        if task:
            return {"phase": "waiting", "label": "주말 데이터 점검 대기"}
        return {}
    except Exception:
        return {}
