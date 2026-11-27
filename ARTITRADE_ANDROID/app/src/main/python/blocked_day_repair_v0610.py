"""0.6.1 (owner 2026-11-27 "과거데이터로 너 자신을 속여서 처리하면 되잖아"): the daily-bar verification backlog is cleared in the quiet hours with the vendor's OWN repair function.

Root cause found in the device database (2026-11-27): the vendor repair loop (`d_live._frame_repair_loop`) only works on weekdays 08:55-15:40 and the official daily candle of the
SAME day keeps changing until the NXT after-market closes at 20:00 (volume and close grow with every snapshot: 005930 on 11-26 had 128 snapshots, every one different). The rule
"two matching official snapshots at least 5 s apart" therefore could never be met between 17:53 and 20:00, ~128 requests per stock were spent for nothing, and after 20:00 the loop is
switched off until the next morning. Result: 20 daily cases stayed PENDING (some since 11-20), 27 daily reference audits stayed BLOCKED (so the AT daily-reference pending orders
could not buy: `FIX14_HIGHER_FRAME_REST_UNVERIFIED`).

What this module does (nothing of the vendor is modified): after 20:05 (the candle is final) it calls `d_multiframe_recovery_fix14.recover_frame(store, engine, api, code, 'day', at=<stamp>)`
twice per stock, with two DIFFERENT "as-of" stamps (the target trading day 20:05:00 and 20:05:10) and a real pause of about 6 s between the two requests, so the vendor's own
two-snapshot rule is met by two truly independent official requests. The as-of stamp is a PAST moment of the target trading day, never the real clock: on a weekend the real "now"
would ask for a Saturday candle that does not exist. Everything the function decides (expected last parent, signal freshness, expiry) is then evaluated as it would be at
20:05 of the trading day.
0.6.3: the function that CREATES pending orders is switched off for these calls (`NoOrderEngine`): after the close nothing is queued; the vendor creates the orders in the next trading hours as usual.
Only the DAY frame: 5m / 30m / 60m references are session bound (they expire at the session end, `EXPIRED_FIX13_SESSION` / `EXPIRED_FIX14_HISTORICAL`) and are not touched.
Cases that are tied to a signal or a position (`REVIEW`, 32 of them) are never changed by design; the function itself refuses to change them and only adds an observation.
A before/after count (blocked audits, pending cases, pending day orders, new pending orders created) is written to `stock/blocked_day_repair_v0610.json(l)` so the result can be read on Monday.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import offhours_prework_v0530 as base

VERSION = "BLOCKED_DAY_REPAIR_V0610"
FIRST_STAMP = "200500"             # as-of time of the first snapshot on the target trading day (after the 20:00 close of the NXT)
SECOND_GAP = 10                    # seconds between the two as-of stamps: the vendor needs two requests >= 5 s apart
BETWEEN_SNAPSHOTS = 6.0            # real seconds between the two requests of one stock
PAUSE = 2.0                        # between stocks (the shared REST gate adds >= 0.4 s per request)
BATCH = 25
ROUND_REST = 300.0
ATTEMPTS = 3
MAX_CODES = 600
ALIGN_DAYS = 3                     # 0.6.5: every managed stock whose day bars of the last 3 trading days exist is aligned to the final official candle
STATE_FILE = "blocked_day_repair_v0610.json"
LOG_FILE = "blocked_day_repair_v0610.jsonl"
KST = dt.timezone(dt.timedelta(hours=9))

_STATE = {"running": False, "failures": 0, "paused_until": 0.0, "next_round": 0.0}


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


def stamps(day, floor=""):
    """Two as-of stamps on the target trading day, always LATER than every stamp the vendor already stored for the stock.

    The vendor's two-snapshot rule measures the time between the first and the last stamp it saw (`v19_frame_revision_confirm.first_seen / last_seen`); a stamp that lies BEFORE an
    earlier record (found on the device copy: a case re-opened at 11:39 the next day) would give a negative elapsed time and the confirmation could never happen.
    """
    first = day.strftime("%Y%m%d") + FIRST_STAMP
    if floor and str(floor) >= first:
        first = (dt.datetime.strptime(str(floor)[:14], "%Y%m%d%H%M%S") + dt.timedelta(seconds=1)).strftime("%Y%m%d%H%M%S")
    second = (dt.datetime.strptime(first, "%Y%m%d%H%M%S") + dt.timedelta(seconds=SECOND_GAP)).strftime("%Y%m%d%H%M%S")
    return first, second


def latest_stamp(store, code):
    """The newest stamp the vendor wrote for this stock's daily revision confirmations ('' when none)."""
    try:
        with store.connect() as db:
            if not base._has_table(db, "v19_frame_revision_confirm"):
                return ""
            row = db.execute("SELECT MAX(MAX(first_seen), MAX(last_seen)) FROM v19_frame_revision_confirm WHERE code=? AND tf='day'", (code,)).fetchone()
            return str(row[0] or "") if row else ""
    except Exception:
        return ""


def counts(store):
    """Read-only picture of the daily verification backlog."""
    out = {"blocked_day_audits": 0, "pending_cases": 0, "review_cases": 0, "pending_day_orders": 0}
    with store.connect() as db:
        if base._has_table(db, "v19_higher_frame_audit14"):
            out["blocked_day_audits"] = int(db.execute("SELECT COUNT(*) FROM v19_higher_frame_audit14 WHERE tf='day' AND state='BLOCKED'").fetchone()[0])
        if base._has_table(db, "daily_ohlcv_revision_cases"):
            out["pending_cases"] = int(db.execute("SELECT COUNT(*) FROM daily_ohlcv_revision_cases WHERE status='PENDING'").fetchone()[0])
            out["review_cases"] = int(db.execute("SELECT COUNT(*) FROM daily_ohlcv_revision_cases WHERE status='REVIEW'").fetchone()[0])
        if base._has_table(db, "v19_orders"):
            out["pending_day_orders"] = int(db.execute("SELECT COUNT(*) FROM v19_orders WHERE tf='day' AND status='PENDING'").fetchone()[0])
    return out


def candidates(store, day=None, align=True):
    """Stocks whose DAY verification is open (a BLOCKED daily reference audit or a PENDING daily revision case), and, from 0.6.5, every managed stock with day bars of the last
    ALIGN_DAYS trading days: the official daily candle is final after 20:00 and contains the NXT after-market, so each stored day bar is aligned to it once per day (the vendor function
    changes only what its own two-snapshot rule confirms and never a bar tied to a signal or a position)."""
    found = {}
    with store.connect() as db:
        if base._has_table(db, "v19_higher_frame_audit14"):
            for row in db.execute("SELECT DISTINCT code FROM v19_higher_frame_audit14 WHERE tf='day' AND state='BLOCKED'").fetchall():
                found.setdefault(str(row[0]), set()).add("BLOCKED_AUDIT")
        if base._has_table(db, "daily_ohlcv_revision_cases"):
            for row in db.execute("SELECT DISTINCT code FROM daily_ohlcv_revision_cases WHERE status='PENDING' AND frame='day'").fetchall():
                found.setdefault(str(row[0]), set()).add("PENDING_CASE")
        if align and day is not None and base._has_table(db, "bars"):
            days, probe = [], day
            while len(days) < ALIGN_DAYS:
                if base._is_trading_day(probe):
                    days.append(probe)
                probe -= dt.timedelta(days=1)
            first = days[-1].strftime("%Y%m%d") + "000000"
            for row in db.execute("SELECT DISTINCT code FROM bars WHERE frame='day' AND ts>=?", (first,)).fetchall():
                found.setdefault(str(row[0]), set()).add("ALIGN_FINAL_CANDLE")
    ordered = sorted(found.items(), key=lambda item: (item[1] == {"ALIGN_FINAL_CANDLE"}, item[0]))      # open cases first
    return {code: sorted(why) for code, why in ordered}


class NoOrderEngine:
    """What the vendor repair function receives as `engine` in the quiet hours (0.6.3, owner: after the close nothing may be queued; the next trading day works normally).

    Every attribute is forwarded to the REAL engine (reads, writes, methods), except the one function that creates pending orders, `_enqueue_signals`: it does nothing and is counted.
    Nothing is patched globally, so the live engine threads never see the difference. Signals the repair re-classifies stay `NEW`; the vendor's own enqueue in the next trading hours turns them into orders then.
    """

    def __init__(self, engine):
        object.__setattr__(self, "_real", engine)
        object.__setattr__(self, "blocked_enqueues", 0)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_real"), name)

    def __setattr__(self, name, value):
        if name == "blocked_enqueues":
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_real"), name, value)

    def _enqueue_signals(self, *args, **kwargs):
        object.__setattr__(self, "blocked_enqueues", self.blocked_enqueues + 1)
        return None


def _recover(store, engine, api, code, at):
    from d_multiframe_recovery_fix14 import recover_frame      # the vendor function; the app may have wrapped it (market context memo), the call is the same
    return recover_frame(store, NoOrderEngine(engine), api, code, "day", at=at)


def repair_code(store, engine, api, code, day, sleep=time.sleep, recover=_recover):
    """Two independent official snapshots, as-of two past stamps. Returns (final_state, new_pending, states)."""
    first, second = stamps(day, latest_stamp(store, code))
    states, new_pending = [], 0
    for index, stamp in enumerate((first, second)):
        result = recover(store, engine, api, code, stamp) or {}
        state = str(result.get("state") or "")
        states.append(state)
        new_pending += int(result.get("new_pending") or 0)
        if state == "VERIFIED_RESTORED" or state in ("OFFICIAL_REVISION_REQUIRES_REVIEW", "MARKET_IDENTITY_MISSING"):
            break                                                  # verified, or protected by design (tied to a signal / position)
        if index == 0:
            sleep(BETWEEN_SNAPSHOTS)
    return states[-1], new_pending, states


def run(runtime, now=None, sleep=time.sleep, recover=_recover):
    """One round (at most BATCH stocks) inside the quiet window. Never raises; returns a summary dict."""
    fixed = now
    now = now or dt.datetime.now(KST)
    summary = {"ran": False}
    try:
        day = base.target_day(now)
        runner = getattr(runtime, "stock_runner", None)
        root = getattr(runtime, "root", None)
        api, store, engine = getattr(runner, "api", None), getattr(runner, "store", None), getattr(runner, "engine", None)
        if day is None or not root or api is None or store is None or engine is None or not base.in_window(now):
            return summary
        if time.monotonic() < _STATE["paused_until"] or time.monotonic() < _STATE["next_round"]:
            return summary
        key = day.isoformat()
        state = load_state(root)
        task = state.setdefault(key, {"done": {}, "attempts": {}, "unresolved": {}, "new_pending": 0, "finished": False})
        if task["finished"]:
            return summary
        if "before" not in task:
            task["before"] = counts(store)
            log(root, {"at": now.isoformat(timespec="seconds"), "event": "START", "day": key, "counts": task["before"]})
        wanted = candidates(store, day)
        todo = [c for c in wanted if c not in task["done"] and int(task["attempts"].get(c, 0)) < ATTEMPTS][:BATCH]
        summary.update(ran=True, day=key, todo=len(todo), open=len(wanted))
        _STATE["running"] = True
        changed = False
        for code in todo:
            moment = fixed if fixed is not None else dt.datetime.now(KST)
            if not base.in_window(moment) or len(task["done"]) >= MAX_CODES:
                break
            try:
                if runner._tick_queue.qsize() >= 24:               # a live feed is running: it has priority
                    break
            except Exception:
                pass
            try:
                final, new_pending, states = repair_code(store, engine, api, code, day, sleep=sleep, recover=recover)
                _STATE["failures"] = 0
            except Exception as exc:
                _STATE["failures"] += 1
                kind = base.note_error(exc)
                if kind not in base.ENV_KINDS:                                      # 0.6.5: environment errors do not burn an attempt
                    task["attempts"][code] = int(task["attempts"].get(code, 0)) + 1
                log(root, {"at": moment.isoformat(timespec="seconds"), "event": "ERROR", "code": code, "kind": kind, "attempt": task["attempts"].get(code, 0), "error": (type(exc).__name__ + ":" + str(exc))[:160]})
                if _STATE["failures"] >= 3:
                    _STATE["paused_until"] = time.monotonic() + base.PAUSE_SECONDS
                    _STATE["failures"] = 0
                    break
                continue
            task["attempts"][code] = int(task["attempts"].get(code, 0)) + 1
            task["new_pending"] += new_pending
            log(root, {"at": moment.isoformat(timespec="seconds"), "event": "CODE", "code": code, "why": wanted.get(code), "states": states, "new_pending": new_pending})
            if final in ("VERIFIED_RESTORED", "OFFICIAL_REVISION_REQUIRES_REVIEW"):
                task["done"][code] = final
                task["unresolved"].pop(code, None)
                changed = changed or new_pending > 0 or final == "VERIFIED_RESTORED"
            else:
                task["unresolved"][code] = final
            sleep(PAUSE)
        if changed:
            try:
                runner._ledger_changed()
            except Exception:
                pass
        wanted_now = candidates(store, day)
        remaining = [c for c in wanted_now if c not in task["done"] and int(task["attempts"].get(c, 0)) < ATTEMPTS]
        if not remaining:
            task["finished"] = True
            task["after"] = counts(store)
            task["finished_at"] = dt.datetime.now(KST).isoformat(timespec="seconds")
            log(root, {"at": task["finished_at"], "event": "DONE", "day": key, "before": task["before"], "after": task["after"], "new_pending": task["new_pending"],
                       "unresolved": task["unresolved"]})
        else:
            _STATE["next_round"] = time.monotonic() + ROUND_REST
        save_state(root, state)
        summary.update(finished=bool(task["finished"]), remaining=len(remaining), new_pending=task["new_pending"])
    except Exception as exc:
        summary["error"] = (type(exc).__name__ + ":" + str(exc))[:200]
    finally:
        _STATE["running"] = False
    return summary
