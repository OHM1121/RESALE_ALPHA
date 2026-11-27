"""0.4.4 (#159): combined KRX+NXT ("통합") stock bars - stage 1: read-only measurement, no behaviour change.

Owner decision 2026-11-27: stock bars for MA / MarketTrend / reference bar must follow the combined (통합) flow, because the owner's
chart is the combined view (NXT pre-market 08:00-09:00 and after-market 15:30-20:00 included) and the app only builds the KRX
regular session (09:00-15:30). Evidence: the 5m RSI dip of one stock (about 19:40) and the MA alignment of another are visible in
the combined chart only.

Why this is staged: (1) the combined chart request (`stk_cd` + `_AL`) has never been issued by this app; whether the broker
accepts it, how far back it goes and whether it returns every session cannot be tested outside the phone; (2) the stock engine has
about 15 places that assume 09:00-15:30 (bar validity, session coverage, repair windows, reference discovery, close windows); a blind
switch could silently corrupt the shared `bars` table. Stage 1 therefore only READS combined 5m / 30m candles for a few symbols,
stores them in its OWN database file (`stock/all_venue_bars_v0440.sqlite3`, no ledger gate, no change to `bars`), and writes for each
cycle how they differ from the regular-session bars the strategies use now, and whether the MA entry alignment would have been seen
on either series. The next error export answers the open questions; stage 2 then switches the strategies.

No order, fill, quote or ledger value is created. A failure here never stops the app: 3 failures in a row pause the probe for 30
minutes. The probe skips a cycle while the tick queue is backed up.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

VERSION = "STOCK_ALL_VENUE_BARS_V0440"
FILE = "all_venue_bars_v0440.sqlite3"
SUFFIX = "_AL"
FRAMES = {"5m": 5, "30m": 30}
CYCLE_SECONDS = 900.0
MAX_CODES = 10
DEFAULT_CODES = ("080220", "466100")        # the two symbols the owner reported (chart screenshots 2026-11-27)
PAUSE_AFTER_FAILURES = 3
PAUSE_SECONDS = 1800.0
KST = dt.timezone(dt.timedelta(hours=9))
_FRAME_SECS = {"5m": 300, "30m": 1800}

DDL = """
CREATE TABLE IF NOT EXISTS bars_all(code TEXT NOT NULL, frame TEXT NOT NULL, ts TEXT NOT NULL,
  open INTEGER NOT NULL, high INTEGER NOT NULL, low INTEGER NOT NULL, close INTEGER NOT NULL, volume INTEGER NOT NULL,
  fetched_at TEXT NOT NULL, PRIMARY KEY(code, frame, ts)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS probe_log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, code TEXT NOT NULL,
  frame TEXT NOT NULL, stk_cd TEXT NOT NULL, ok INTEGER NOT NULL, rows INTEGER, first_ts TEXT, last_ts TEXT,
  hours TEXT, session_bars TEXT, regular_same INTEGER, regular_diff INTEGER, regular_volume_ratio REAL,
  ma_all TEXT, ma_krx TEXT, error TEXT);
"""

_STATE = {"installed": False, "thread": None, "failures": 0, "paused_until": 0.0}


def _int(value):
    try:
        return int(float(str(value).replace(",", "").replace("+", "").replace("-", "")))
    except (TypeError, ValueError):
        return 0


def parse_rows(packet, frame, now):
    """Completed combined candles of a ka10080 packet, ALL sessions kept: {ts: (o, h, l, c, v)}."""
    raw = (packet or {}).get("stk_min_pole_chart_qry") or []
    secs = _FRAME_SECS[frame]
    cutoff = now.replace(tzinfo=None) if now.tzinfo is None else now.astimezone(KST).replace(tzinfo=None)
    out = {}
    for row in raw:
        digits = "".join(ch for ch in str(row.get("cntr_tm") or "") if ch.isdigit())
        if len(digits) < 12:
            continue
        ts = digits[:14].ljust(14, "0")
        try:
            end = dt.datetime.strptime(ts, "%Y%m%d%H%M%S") + dt.timedelta(seconds=secs)
        except ValueError:
            continue
        if end > cutoff:                                   # the forming candle is never stored
            continue
        o = _int(row.get("open_pric"))
        h = _int(row.get("high_pric"))
        lo = _int(row.get("low_pric"))
        c = _int(row.get("cur_prc"))
        v = _int(row.get("trde_qty"))
        if min(o, h, lo, c) <= 0 or v <= 0 or lo > min(o, c) or h < max(o, c):
            continue
        out[ts] = (o, h, lo, c, v)
    return out


def fetch(api, code, frame, now):
    """One combined-chart request (first page only). Returns (rows or None, error text)."""
    body = {"stk_cd": code + SUFFIX, "tic_scope": str(FRAMES[frame]), "upd_stkpc_tp": "1", "base_dt": now.strftime("%Y%m%d")}
    try:
        packet = api.post("ka10080", "/api/dostk/chart", body)
    except Exception as exc:
        return None, (type(exc).__name__ + ":" + str(exc))[:240]
    rows = parse_rows(packet, frame, now)
    if not rows:
        return None, "NO_USABLE_ROWS"
    return rows, None


def _hours(rows):
    """Distinct hours that have candles on the newest day, e.g. '08,09,10,...,19'."""
    if not rows:
        return "", ""
    last_day = max(ts[:8] for ts in rows)
    hours = sorted({ts[8:10] for ts in rows if ts[:8] == last_day})
    bars = sum(1 for ts in rows if ts[:8] == last_day)
    return ",".join(hours), "%s:%d" % (last_day, bars)


def compare_regular(rows, krx_rows):
    """How the combined candles inside 09:00-15:30 differ from the regular-session bars the strategies use now."""
    same = diff = 0
    ratios = []
    for ts, (o, h, lo, c, v) in rows.items():
        if not ("0900" <= ts[8:12] < "1530") or ts not in krx_rows:
            continue
        ko, kh, kl, kc, kv = krx_rows[ts]
        if (o, h, lo, c) == (ko, kh, kl, kc):
            same += 1
        else:
            diff += 1
        if kv > 0:
            ratios.append(v / kv)
    ratio = round(sum(ratios) / len(ratios), 4) if ratios else None
    return same, diff, ratio


def ma_verdict(closes):
    """MA entry alignment verdict of the LAST completed bar of a close series (the owner's own rule)."""
    try:
        import ma_v067_policy as policy
        if len(closes) < 122:
            return "SMA_HISTORY_REQUIRED(%d)" % len(closes)
        verdict = policy.prewatch_upward_alignment(list(closes[:-1]), closes[-1])
        return str(verdict.get("kind") or verdict.get("reason"))
    except Exception as exc:
        return "ERR_" + type(exc).__name__


def _krx_rows(main_db, code, frame, limit=400):
    out = {}
    try:
        with closing(sqlite3.connect("file:%s?mode=ro" % main_db, uri=True, timeout=2)) as cx:
            for ts, o, h, lo, c, v in cx.execute(
                    "SELECT ts,open,high,low,close,volume FROM bars WHERE code=? AND frame=? ORDER BY ts DESC LIMIT ?",
                    (code, frame, limit)):
                out[ts] = (o, h, lo, c, v)
    except Exception:
        pass
    return out


def record(db_path, main_db, code, frame, rows, error, now):
    stamp = now.isoformat()
    with closing(sqlite3.connect(str(db_path), timeout=5)) as cx:
        cx.executescript(DDL)
        if rows is None:
            cx.execute("INSERT INTO probe_log(at,code,frame,stk_cd,ok,error) VALUES(?,?,?,?,0,?)",
                       (stamp, code, frame, code + SUFFIX, error))
            cx.commit()
            return
        cx.executemany("INSERT OR REPLACE INTO bars_all(code,frame,ts,open,high,low,close,volume,fetched_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       [(code, frame, ts, *v, stamp) for ts, v in rows.items()])
        krx = _krx_rows(main_db, code, frame)
        same, diff, ratio = compare_regular(rows, krx)
        hours, session_bars = _hours(rows)
        ordered = sorted(rows)
        all_closes = [rows[ts][3] for ts in ordered]
        krx_closes = [krx[ts][3] for ts in sorted(krx)]
        cx.execute("""INSERT INTO probe_log(at,code,frame,stk_cd,ok,rows,first_ts,last_ts,hours,session_bars,regular_same,
                      regular_diff,regular_volume_ratio,ma_all,ma_krx) VALUES(?,?,?,?,1,?,?,?,?,?,?,?,?,?,?)""",
                   (stamp, code, frame, code + SUFFIX, len(rows), ordered[0], ordered[-1], hours, session_bars, same, diff,
                    ratio, ma_verdict(all_closes), ma_verdict(krx_closes)))
        cx.commit()


def _runtime():
    try:
        import android_live_bridge as bridge
        return getattr(bridge, "_RUNTIME", None)
    except Exception:
        return None


def pick_codes(runtime, extra=()):
    codes = list(DEFAULT_CODES)
    try:
        coordinator = getattr(runtime, "market_trend", None)
        scope = list((getattr(coordinator, "_candidates", {}) or {}).get("stock", {}))
        codes += [str(c) for c in scope]
    except Exception:
        pass
    codes += [str(c) for c in extra]
    seen, out = set(), []
    for code in codes:
        if len(code) == 6 and code.isdigit() and code not in seen:
            seen.add(code)
            out.append(code)
    return out[:MAX_CODES]


def in_window(now):
    return now.weekday() < 5 and "0755" <= now.strftime("%H%M") <= "2005"


def cycle(runtime, now=None, sleep=time.sleep):
    """One probe pass. Returns the number of successful requests; never raises."""
    now = now or dt.datetime.now(KST)
    if time.monotonic() < _STATE["paused_until"] or not in_window(now):
        return 0
    runner = getattr(runtime, "stock_runner", None)
    api = getattr(runner, "api", None)
    root = getattr(runtime, "root", None)
    if api is None or not root:
        return 0
    try:
        if runner._tick_queue.qsize() >= 24:                # the live feed has priority
            return 0
    except Exception:
        pass
    stock_dir = Path(root) / "stock"
    db_path = stock_dir / FILE
    main_db = stock_dir / "ARTITRADE_D.sqlite3"
    ok = 0
    for code in pick_codes(runtime):
        for frame in FRAMES:
            rows, error = fetch(api, code, frame, now)
            try:
                record(db_path, main_db, code, frame, rows, error, now)
            except Exception:
                pass
            if rows is None:
                _STATE["failures"] += 1
                if _STATE["failures"] >= PAUSE_AFTER_FAILURES:
                    _STATE["paused_until"] = time.monotonic() + PAUSE_SECONDS
                    _STATE["failures"] = 0
                    return ok
            else:
                _STATE["failures"] = 0
                ok += 1
            sleep(0.5)
    return ok


def _loop():
    time.sleep(120.0)
    while True:
        try:
            cycle(_runtime())
        except Exception:
            pass
        time.sleep(CYCLE_SECONDS)


def install(threads=True):
    if _STATE["installed"]:
        return
    if threads and _STATE["thread"] is None:
        thread = threading.Thread(target=_loop, name="ARTITRADE_V0440_ALL_VENUE_PROBE", daemon=True)
        _STATE["thread"] = thread
        thread.start()
    _STATE["installed"] = True
