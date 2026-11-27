"""0.5.3 (owner 2026-11-27, standing rule R16): do in the quiet hours what does not need real time, so the market open is not overloaded.

Owner: after the NXT session ends and before the next trading day opens the app does nothing; the market open is when every quote, repair
and record arrives at once. Work that only needs already-finished information is done in that window.

This module is the off-hours worker for STOCK data. Task `bars_completion` (the first one):
  after 20:05 of a trading day D (resumed before 07:45 of the next morning if the phone was off), for every stock that has live bars of day D,
  ask the broker again for the finished regular-session candles of day D (5m, 30m, 60m, day) and INSERT the candles the live feed missed.
  Why it matters: the MA / trend values of the next morning use yesterday's bars; a hole made by a feed drop made yesterday and today
  "two different flows" (the owner's 제주반도체 / 클로봇 reports). The same data is no longer repaired at the open.
Rules: only candles that pass the SAME checks as the vendor repair (regular KRX reference slot, closed, positive volume, sane OHLC, requested code,
requested day); `INSERT OR IGNORE` (an existing candle is never replaced; conflicts are left to the vendor audit); no signal discovery, no order,
no ledger write; yields at once to the live feed; at most MAX_REQUESTS broker requests per night, PAUSE seconds between requests; three failures in
a row pause the task for 30 minutes; the result of the night is written to `stock/offhours_v0530.json` (resume) and `stock/offhours_v0530.jsonl`.
GMO (supply-demand) next-day setup is done by `gmo_d1_preplan_v097.Preplanner.presetup` (06:00, after the EWY value is known), not here.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from pathlib import Path

VERSION = "OFFHOURS_PREWORK_V0530"
SOURCE = "KIWOOM_REST_OFFHOURS_V0530"
FRAMES = ("5m", "30m", "60m", "day")
START_HHMM = "2005"          # after the NXT after-market close (20:00)
STOP_HHMM = "0745"           # before the NXT preparation (07:50)
MAX_REQUESTS = 1500
PAUSE = 0.4
PAUSE_AFTER_FAILURES = 3
PAUSE_SECONDS = 300.0             # 0.6.5: 5 minutes (was 30): a wrong network / not-allowed IP must not stop the work for half an hour after it is fixed
STATE_FILE = "offhours_v0530.json"
LOG_FILE = "offhours_v0530.jsonl"
KST = dt.timezone(dt.timedelta(hours=9))

_STATE = {"installed": False, "thread": None, "failures": 0, "paused_until": 0.0, "running": False, "progress": [0, 0], "last_error": ""}
ENV_KINDS = ("AUTH", "RATE", "NETWORK")
HINTS = {"AUTH": "키움 허용 IP·인증 확인", "RATE": "요청 제한", "NETWORK": "네트워크 연결", "OTHER": "처리 오류"}


def classify_error(exc):
    """AUTH (not-allowed IP, token), RATE (request limit), NETWORK (no route, timeout) or OTHER. The first three are caused by the environment, not by the stock asked for."""
    text = type(exc).__name__ + ":" + str(exc)
    if any(w in text for w in ("허용", "8050", "8005", "8030", "401", "403", "인증", "토큰")):
        return "AUTH"
    if "429" in text or "제한" in text or "RateLimit" in text:
        return "RATE"
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)) or any(w in text for w in ("Connection", "Timeout", "timed out", "Name or service", "SSL", "Max retries")):
        return "NETWORK"
    return "OTHER"


def note_error(exc):
    """Remember what went wrong (for the card text) and return the kind. A failure of kind AUTH / RATE / NETWORK is not counted against the stock."""
    kind = classify_error(exc)
    _STATE["last_error"] = kind
    return kind


def paused_text(prefix, until):
    minutes = max(1, int((until - time.monotonic() + 59) // 60))
    hint = HINTS.get(_STATE.get("last_error"), "")
    return "%s 잠시 중지 · %s%d분 뒤 재시도" % (prefix, (hint + " · ") if hint else "", minutes)


def _is_trading_day(day):
    try:
        from night_futures_gate_v0260 import is_krx_day
        return bool(is_krx_day(day))
    except Exception:
        return day.weekday() < 5


def previous_trading_day(day):
    probe = day
    for _ in range(15):
        probe = probe - dt.timedelta(days=1)
        if _is_trading_day(probe):
            return probe
    return day - dt.timedelta(days=1)


def target_day(now):
    """The trading day whose bars are completed now.

    Trading day: today after 20:05, the previous trading day before 07:45, else None.
    0.5.9 (owner 2026-11-27): a day without a market (weekend, holiday) belongs to the quiet window ALL DAY, so the work runs from Friday 20:05 until
    Monday 07:45 (or the next trading morning) and refers to the last trading day.
    """
    hhmm = now.strftime("%H%M")
    day = now.date()
    if not _is_trading_day(day):
        return previous_trading_day(day)
    if hhmm >= START_HHMM:
        return day
    if hhmm < STOP_HHMM:
        return previous_trading_day(day)
    return None


def long_window(now):
    """True when the quiet window spans a non-trading day (weekend / holiday): time for the deep multi-day audit."""
    day = target_day(now)
    if day is None:
        return False
    nxt = day + dt.timedelta(days=1)
    for _ in range(15):
        if _is_trading_day(nxt):
            break
        nxt += dt.timedelta(days=1)
    return (nxt - day).days >= 2


def in_window(now):
    return target_day(now) is not None


def _paths(root):
    base = Path(root) / "stock"
    return base / STATE_FILE, base / LOG_FILE


def load_state(root):
    path, _ = _paths(root)
    try:
        return json.loads(path.read_text("utf8"))
    except Exception:
        return {}


def save_state(root, state):
    path, _ = _paths(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True), "utf8")
        os.replace(tmp, path)
    except Exception:
        pass


def log(root, event):
    _, path = _paths(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def codes_with_bars(store, day):
    stamp = day.strftime("%Y%m%d")
    with store.connect() as db:
        rows = db.execute("SELECT DISTINCT code FROM bars WHERE frame='5m' AND ts>=? AND ts<?",
                          (stamp + "000000", stamp + "240000")).fetchall()
    return sorted(str(r[0]) for r in rows)


def _market(db, code):
    row = db.execute("SELECT market FROM stocks WHERE code=?", (code,)).fetchone() if _has_table(db, "stocks") else None
    if row is None:
        row = db.execute("SELECT market FROM bars WHERE code=? AND market IN ('KOSPI','KOSDAQ') LIMIT 1", (code,)).fetchone()
    return None if row is None else row[0]


def _has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def valid_rows(rows, code, frame, day, now):
    """The finished regular-session candles of `day` in a broker response, checked like the vendor repair does."""
    from d_engine import bar_end, is_krx_regular_reference
    stamp = day.strftime("%Y%m%d")
    now_stamp = now.strftime("%Y%m%d%H%M%S")
    out = {}
    for item in rows or ():
        try:
            row_code, tf, ts, op, hi, lo, cl, vol = item
            if (str(row_code) != code or tf != frame or len(str(ts)) != 14 or str(ts)[:8] != stamp
                    or not is_krx_regular_reference(ts, frame) or bar_end(ts, frame) > now_stamp
                    or int(vol) <= 0 or min(int(op), int(hi), int(lo), int(cl)) <= 0
                    or not int(lo) <= min(int(op), int(cl)) <= max(int(op), int(cl)) <= int(hi)):
                continue
            out[str(ts)] = (int(op), int(hi), int(lo), int(cl), int(vol))
        except (TypeError, ValueError, OverflowError):
            continue
    return out


def complete_code(store, api, code, day, now, frames=FRAMES, sleep=time.sleep, budget=None):
    """Insert the finished candles of `day` that are missing in the database. Returns {frame: (found_missing, inserted)} and the request count."""
    stamp = day.strftime("%Y%m%d")
    report, requests = {}, 0
    for frame in frames:
        if budget is not None and requests >= budget:
            break
        rows = api.history_bars(code, frame)                       # one request series; the broker answers outside the network lock
        requests += 1
        official = valid_rows(rows, code, frame, day, now)
        if not official:
            report[frame] = (0, 0)
            sleep(PAUSE)
            continue
        with store.connect() as db:
            have = {str(r[0]) for r in db.execute("SELECT ts FROM bars WHERE code=? AND frame=? AND ts>=? AND ts<?",
                                                  (code, frame, stamp + "000000", stamp + "240000")).fetchall()}
            market = _market(db, code)
        missing = {ts: v for ts, v in official.items() if ts not in have}
        inserted = 0
        if missing and market:
            batch = [(code, market, frame, ts, *v, SOURCE) for ts, v in sorted(missing.items())]
            with store.lock, store.connect() as db:
                before = db.total_changes
                db.executemany("INSERT OR IGNORE INTO bars(code,market,frame,ts,open,high,low,close,volume,source) VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
                inserted = db.total_changes - before
        report[frame] = (len(missing), inserted)
        sleep(PAUSE)
    return report, requests


def _runtime():
    try:
        import android_live_bridge as bridge
        return getattr(bridge, "_RUNTIME", None)
    except Exception:
        return None


def run(runtime, now=None, sleep=time.sleep):
    """One pass of the night's work. Returns the summary dict; never raises."""
    fixed = now                                    # tests pass a fixed moment; the app reads the clock for every code
    now = now or dt.datetime.now(KST)
    summary = {"ran": False}
    try:
        day = target_day(now)
        runner = getattr(runtime, "stock_runner", None)
        root = getattr(runtime, "root", None)
        api = getattr(runner, "api", None)
        store = getattr(runner, "store", None)
        if day is None or not root or api is None or store is None:
            return summary
        if time.monotonic() < _STATE["paused_until"]:
            return summary
        key = day.isoformat()
        state = load_state(root)
        task = state.setdefault(key, {}).setdefault("bars_completion", {"done": False, "codes_done": [], "inserted": 0, "missing_found": 0, "requests": 0})
        if task["done"]:
            return summary
        codes = [c for c in codes_with_bars(store, day) if c not in set(task["codes_done"])]
        summary.update(ran=True, day=key, todo=len(codes))
        _STATE["running"] = True                                          # 0.5.7: shown on the market feed card (status())
        _STATE["progress"] = [len(task["codes_done"]), len(task["codes_done"]) + len(codes)]
        if not task["codes_done"]:
            log(root, {"at": now.isoformat(), "task": "bars_completion", "event": "START", "day": key, "codes": len(codes)})
        for code in codes:
            moment = fixed if fixed is not None else dt.datetime.now(KST)
            if not in_window(moment) or task["requests"] >= MAX_REQUESTS:
                break
            try:
                if runner._tick_queue.qsize() >= 24:               # a live feed is running: it has priority
                    break
            except Exception:
                pass
            try:
                report, used = complete_code(store, api, code, day, moment, sleep=sleep, budget=MAX_REQUESTS - task["requests"])
            except Exception as exc:
                _STATE["failures"] += 1
                kind = note_error(exc)
                log(root, {"at": moment.isoformat(), "task": "bars_completion", "event": "ERROR", "code": code, "kind": kind, "error": (type(exc).__name__ + ":" + str(exc))[:200]})
                if _STATE["failures"] >= PAUSE_AFTER_FAILURES:
                    _STATE["paused_until"] = time.monotonic() + PAUSE_SECONDS
                    _STATE["failures"] = 0
                    break
                continue
            _STATE["failures"] = 0
            task["codes_done"].append(code)
            _STATE["progress"][0] = len(task["codes_done"])
            task["requests"] += used
            task["missing_found"] += sum(v[0] for v in report.values())
            task["inserted"] += sum(v[1] for v in report.values())
            if any(v[0] for v in report.values()):
                log(root, {"at": moment.isoformat(), "task": "bars_completion", "event": "FILLED", "code": code, "day": key,
                           "missing_and_inserted": {f: list(v) for f, v in report.items()}})
            if len(task["codes_done"]) % 25 == 0:
                save_state(root, state)
        remaining = [c for c in codes_with_bars(store, day) if c not in set(task["codes_done"])]
        if not remaining:
            task["done"] = True
            task["finished_at"] = dt.datetime.now(KST).isoformat()
            log(root, {"at": task["finished_at"], "task": "bars_completion", "event": "DONE", "day": key,
                       "codes": len(task["codes_done"]), "missing_found": task["missing_found"], "inserted": task["inserted"], "requests": task["requests"]})
        save_state(root, state)
        summary.update(done=bool(task["done"]), inserted=task["inserted"], missing_found=task["missing_found"])
    except Exception as exc:
        summary["error"] = (type(exc).__name__ + ":" + str(exc))[:200]
    finally:
        _STATE["running"] = False
    return summary


def status(root, now=None):
    """0.5.7 (owner 2026-11-27): one short line for the market feed card about the after-close data collection and storage.

    {} outside the quiet window; else {"phase": running | done | paused | waiting, "label": <very short Korean text>}.
    The text only says whether the collect-and-store work is running well, not what it does in detail.
    """
    try:
        now = now or dt.datetime.now(KST)
        day = target_day(now)
        if day is None:
            hhmm = now.strftime("%H%M")
            if _is_trading_day(now.date()) and "1530" <= hhmm < START_HHMM:
                return {"phase": "waiting", "label": "장마감 후 작업 대기 · 시간외 거래가 끝나는 20:05에 시작"}
            return {}
        try:
            import offhours_weekend_v0590 as weekend
            audit = weekend.status_line(root, day, now)
        except Exception:
            audit = {}
        if audit and audit.get("phase") == "running":
            return audit
        try:
            import all_venue_complete_v0600 as combined
            if combined._STATE["running"]:
                done_n, total_n = combined._STATE["progress"]
                return {"phase": "running", "label": "장마감 후 통합봉 수집·저장 중" + (" %d/%d" % (done_n, total_n) if total_n else "")}
        except Exception:
            pass
        if _STATE["running"]:
            done, total = _STATE["progress"]
            return {"phase": "running", "label": "장마감 후 데이터 수집·저장 중" + (" %d/%d" % (done, total) if total else "")}
        if time.monotonic() < _STATE["paused_until"]:
            return {"phase": "paused", "label": paused_text("장마감 후 데이터 수집", _STATE["paused_until"])}
        task = (load_state(root).get(day.isoformat()) or {}).get("bars_completion") or {}
        if audit and task.get("done"):
            return audit
        if task.get("done"):
            return {"phase": "done", "label": "장마감 후 데이터 수집·저장 완료 · %d종목" % len(task.get("codes_done") or [])}
        return {"phase": "waiting", "label": "장마감 후 데이터 수집 대기"}
    except Exception:
        return {}


def _loop():
    time.sleep(60.0)               # 0.6.5: was 300 s; the app is restarted often and the work must not wait five minutes after every restart
    while True:
        try:
            if in_window(dt.datetime.now(KST)):
                run(_runtime())
                if long_window(dt.datetime.now(KST)):
                    import offhours_weekend_v0590 as weekend
                    weekend.run_audit(_runtime())          # 0.5.9: the deep multi-day audit of the managed data, only in a long window
                import entry_ctx_backfill_v0600 as backfill
                backfill.run(_runtime())                  # 0.6.0: BUY-time MA / trend context of the stock positions, from the stored bars
                import after_hours_bars_v0660 as ext_bars
                ext_bars.run(_runtime())                  # 0.6.6: the past NXT pre-market / after-hours bars join the continuous bar series
                import entry_ctx_backfill_v0620 as coin_backfill
                coin_backfill.run(_runtime())             # 0.6.2: BUY-time MA / trend / market score of the COIN positions, from the official Upbit candles
                import blocked_day_repair_v0610 as day_repair
                day_repair.run(_runtime())                # 0.6.1: the daily-bar verification backlog, with the vendor's own repair function as of the trading day
        except Exception:
            pass
        time.sleep(300.0)


def _combined_loop():
    """0.6.7 (owner 2026-11-28 "통합봉은 왜 완성을 못하고 있는거야"): the combined (KRX + NXT) candles have their OWN thread.

    Before, the fill waited behind the whole bar-completion pass (up to 1500 requests), the weekend audit and the other work in one loop that slept 5 minutes per turn, and the readiness
    report was taken once at the first turn of the night (so it stayed at the start-of-night number even after the fill finished). Now: the fill runs on its own every minute (its own round rest),
    and the impact / readiness report follows it and repeats until the fill of the day is finished.
    """
    time.sleep(90.0)
    while True:
        try:
            if in_window(dt.datetime.now(KST)):
                import all_venue_complete_v0600 as combined
                combined.run(_runtime())                  # 0.6.0: the combined (KRX + NXT) candles of the managed stocks, separate store
                import combined_impact_v0650 as impact
                impact.run(_runtime())                    # 0.6.5: what would change if the strategies decided on the combined candles; local reads only
        except Exception:
            pass
        time.sleep(60.0)


def install(threads=True):
    if _STATE["installed"]:
        return
    if threads and _STATE["thread"] is None:
        thread = threading.Thread(target=_loop, name="ARTITRADE_V0530_OFFHOURS", daemon=True)
        _STATE["thread"] = thread
        thread.start()
    if threads and _STATE.get("thread_combined") is None:
        thread = threading.Thread(target=_combined_loop, name="ARTITRADE_V0670_COMBINED", daemon=True)
        _STATE["thread_combined"] = thread
        thread.start()
    _STATE["installed"] = True
