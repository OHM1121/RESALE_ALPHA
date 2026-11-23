"""0.3.2 (owner 2026-11-23, issue #147): supply-demand (GMO) D-day selection is fixed at the D close,
and the pending card shows the D+1 buy price.

Owner: "수급일이 11월 20일이면 11월 20일 장 마감후 떠야하는데 그다음거래일인 오늘 11월 23일에 [떴다]
... 기본 조건이 맞다면 우선 매수대기에 올리고 D+1일에는 ewy고려해서 매수가 조건만 맞추면 되잖아.
매수대기가 부분을 넣어서 얼마까지 내려오면 사는건지 표시해"

Root cause (CONFIRMED with the 2026-11-22 09:55 ledger backup and vendor source):
* `GMOService._maintenance_loop` scans "today" after 15:30 and otherwise the PREVIOUS trading
  day, over `stock_runner.market_map` = the Kiwoom ka10032 trading-value ranking AT THE MOMENT OF
  THE SCAN. The 11-20 scan ran 15:30-23:47 over 253 symbols and accepted none.
* On 11-23 (D+1) after 09:30 the same loop kept scanning D = 11-20, but over the 11-23 INTRADAY
  ranking. Symbols that entered today's ranking (한국가구, 하림지주, JW중외제약 - absent from every
  11-20 ranking after 15:20 and never scanned on 11-20) were judged on 11-20 flows and appeared as
  11-20 candidates on 11-23: selection used D+1 information and appeared a day late.

Fix:
1. D selection universe = symbols in the KRX ranking snapshots recorded on D from 15:20 on
   (`rank_snapshots`, stock/ARTITRADE_D.sqlite3), plus symbols already scanned for D before the
   cutoff. Until the next trading day 07:45 (before the NXT pre-market ranking of D+1) the current
   ranking is still D's ranking and the vendor scan is unchanged. After the cutoff a symbol outside
   the D universe is recorded as COMPLETE / OUTSIDE_D_CLOSE_UNIVERSE_V0320 and is never judged.
2. Candidates already accepted that way (registered after the cutoff, outside the D universe, no
   position and no open BUY order) are dismissed through the vendor's own `dismiss()`.
3. Pending rows carry the D+1 buy price: on D+1 with a verified night value,
   `night_futures_gate_v0260.gmo_ceiling(D close)` (D close x (1 + EWY change), tick floored); before
   that the D close and "EWY 확인 대기". Plus registration time and a correct supply-demand reason.

No order, fill or price is created here; the D+1 buy logic (gmo_d1_preplan_v097) is unchanged.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
CUTOFF = dt.time(7, 45)
CLOSE_FROM = "152000"
SCAN_DETAIL = "OUTSIDE_D_CLOSE_UNIVERSE_V0320"
DISMISS_LOG = "GMO_D_UNIVERSE_EXCLUDED_V0320"
PENDING_STATUSES = ("후보", "대기", "매수대기", "미체결/후보")
CLEANUP_INTERVAL_SECONDS = 600
_INSTALLED = False


def _now():
    return dt.datetime.now(KST)


def _day(event_date):
    return str(event_date).replace("-", "")[:8]


def selection_cutoff(event_date, root):
    """Next trading day 07:45 KST: after this the live ranking is no longer D's ranking."""
    from d_gmo_runtime import _next_day
    nxt = dt.date.fromisoformat(str(_next_day(str(event_date), Path(root)))[:10])
    return dt.datetime.combine(nxt, CUTOFF, KST)


def _stock_db(svc):
    return Path(svc.root) / "ARTITRADE_D.sqlite3"


def close_universe(stock_db, event_date):
    """Symbols of the KRX ranking snapshots recorded on D from 15:20 on (empty set if none)."""
    day = _day(event_date)
    path = Path(stock_db)
    if not path.exists():
        return set()
    with closing(sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=2)) as c:
        rows = c.execute("SELECT DISTINCT code FROM rank_snapshots WHERE ts>=? AND ts<=?",
                         (day + CLOSE_FROM, day + "235959")).fetchall()
    return {str(r[0]) for r in rows}


def scanned_before(svc, event_date, cutoff):
    day = _day(event_date)
    limit = cutoff.isoformat()
    with svc._db() as c:
        rows = c.execute("SELECT code,checked_at FROM gmo_scan WHERE trade_date=?", (day,)).fetchall()
    return {str(r[0]) for r in rows if str(r[1] or "") < limit}


def allowed(svc, code, event_date, now=None):
    """(allowed, reason) for judging `code` as a D-day (event_date) candidate at `now`."""
    now = now or _now()
    cutoff = selection_cutoff(event_date, svc.root)
    if now < cutoff:
        return True, "D_RANKING_STILL_CURRENT"
    try:
        if str(code) in close_universe(_stock_db(svc), event_date):
            return True, "IN_D_CLOSE_UNIVERSE"
    except Exception:
        pass                                   # unknown universe after the cutoff -> not allowed
    try:
        if str(code) in scanned_before(svc, event_date, cutoff):
            return True, "SCANNED_FOR_D_BEFORE_CUTOFF"
    except Exception:
        pass
    return False, SCAN_DETAIL


def _record_blocked(svc, code, event_date, now):
    with svc._db() as c:
        c.execute("INSERT INTO gmo_scan(trade_date,code,status,detail,checked_at,selection_version)"
                  " VALUES(?,?,?,?,?,?) ON CONFLICT(trade_date,code) DO UPDATE SET"
                  " status=excluded.status,detail=excluded.detail,checked_at=excluded.checked_at,"
                  " selection_version=excluded.selection_version",
                  (_day(event_date), str(code), "COMPLETE", SCAN_DETAIL, now.isoformat(), "PROJECTGMO V1.2"))


def _open_buy(svc, code):
    for order in list(getattr(svc.engine, "orders", {}).values()):
        if (str(getattr(order, "code", "")) == str(code) and getattr(order, "side", "") == "BUY"
                and getattr(order, "state", "") not in ("FILLED", "CANCELED")):
            return True
    return False


def cleanup(svc, now=None):
    """Dismiss candidates that entered after the cutoff from outside the D close universe."""
    now = now or _now()
    removed = []
    with svc._db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT code,event_date,entry_date,status,saved_at FROM gmo_candidates WHERE entry_date>=?",
            (now.date().isoformat(),)).fetchall()]
    for r in rows:
        if r["status"] not in PENDING_STATUSES:
            continue
        code = str(r["code"])
        try:
            cutoff = selection_cutoff(r["event_date"], svc.root)
        except Exception:
            continue
        if str(r["saved_at"] or "") < cutoff.isoformat():
            continue                                   # registered while D's ranking was current
        try:
            if code in close_universe(_stock_db(svc), r["event_date"]):
                continue                               # in D's own ranking: legitimate (app was off on D)
        except Exception:
            continue                                   # cannot prove it is outside: leave it
        if code in svc.engine.positions or _open_buy(svc, code):
            continue
        signal = svc.engine.signals.get(code)
        if signal is None or str(getattr(signal, "event_date", "")) != str(r["event_date"]):
            continue
        try:
            if svc.dismiss(code):
                removed.append(code)
                svc._log(DISMISS_LOG, code, "D=%s registered %s after cutoff %s, outside D close ranking"
                         % (r["event_date"], r["saved_at"], cutoff.isoformat()))
        except Exception:
            continue
    return removed


# ---------------------------------------------------------------- pending card view

def _fmt(won):
    return "{:,}원".format(int(won))


def buy_rule(signal, now=None):
    """Dict with the D+1 buy price for the pending card. Never raises."""
    now = now or _now()
    close = int(getattr(signal, "event_close", 0) or 0)
    entry = str(getattr(signal, "entry_date", "") or "")
    out = {"gmo_d_close": close or None, "gmo_entry_date": entry or None,
           "gmo_buy_ceiling": None, "gmo_buy_wait_label": None, "gmo_buy_rule": None}
    if close <= 0:
        out["gmo_buy_wait_label"] = "종가 확인 중"
        out["gmo_buy_rule"] = "매수 조건 · D일 종가 확인 중"
        return out
    if entry and now.date().isoformat() == entry:
        try:
            import night_futures_gate_v0260 as night
            ceiling, info = night.gmo_ceiling(close, now)
        except Exception:
            ceiling, info = None, {}
        if ceiling:
            change = float(info.get("change_pct") or 0.0)
            out["gmo_buy_ceiling"] = int(ceiling)
            out["gmo_night_change_pct"] = round(change, 2)
            out["gmo_buy_rule"] = ("매수 조건 · 현재가 %s 이하 (D일 종가 %s × EWY %+.2f%%)"
                                   % (_fmt(ceiling), _fmt(close), change))
            return out
    out["gmo_buy_wait_label"] = "EWY 확인 대기"
    out["gmo_buy_rule"] = ("매수 조건 · D일 종가 %s × (1 + 미국장 EWY 등락률) 이하 · "
                           "%s 아침 EWY 확정 후 가격 표시" % (_fmt(close), entry or "D+1"))
    return out


def enrich_pending(svc, out, now=None):
    now = now or _now()
    rows = out.get("pending") or []
    if not rows:
        return out
    saved = {}
    try:
        with svc._db() as c:
            for r in c.execute("SELECT code,event_date,saved_at FROM gmo_candidates"
                               " WHERE entry_date>=?", (now.date().isoformat(),)):
                saved[(str(r[0]), str(r[1]))] = r[2]
    except Exception:
        pass
    for row in rows:
        if not row.get("gmo"):
            continue
        signal = svc.engine.signals.get(str(row.get("code")))
        if signal is None:
            continue
        rule = buy_rule(signal, now)
        row.update(rule)
        if rule["gmo_buy_ceiling"] and not row.get("limit_price"):
            row["limit_price"] = rule["gmo_buy_ceiling"]
        row.setdefault("target_price", row.get("first_target_won"))
        at = saved.get((str(row.get("code")), str(row.get("gmo_event_date"))))
        if at and not row.get("at"):
            row["at"] = at
        if not row.get("status"):
            row["status"] = "D+1 매수가 대기" if rule["gmo_buy_ceiling"] else (
                rule["gmo_buy_wait_label"] or "확인 중")
        # The vendor text "기준봉 조건 미적용 · 수급매매 후보" was shown as "기준봉 매수 조건 충족".
        row["reason"] = "수급매매 후보 · D일 3주체 수급 조건 충족"
    return out


def install(core=None):
    global _INSTALLED
    if _INSTALLED:
        return
    from d_gmo_runtime import GMOService

    native_scan = GMOService._scan_one
    native_start = GMOService.start
    native_snapshot = GMOService.snapshot

    def _scan_one(self, api, code, name, market, event_date):
        now = _now()
        try:
            ok, _why = allowed(self, code, event_date, now)
        except Exception:
            ok = True                                  # calendar unknown: keep the vendor behaviour
        if not ok:
            try:
                _record_blocked(self, code, event_date, now)
            except Exception:
                pass
            return None
        return native_scan(self, api, code, name, market, event_date)

    def start(self, *args, **kwargs):
        result = native_start(self, *args, **kwargs)

        def loop():
            stop = getattr(self, "_stop", None)
            wait = (stop.wait if stop is not None else (lambda s: threading.Event().wait(s)))
            if wait(20):
                return
            while True:
                try:
                    cleanup(self)
                except Exception:
                    pass
                if wait(CLEANUP_INTERVAL_SECONDS):
                    return
        threading.Thread(target=loop, daemon=True, name="artitrade-v0320-gmo-d-universe").start()
        return result

    def snapshot(self):
        out = native_snapshot(self)
        try:
            enrich_pending(self, out)
        except Exception:
            pass
        return out

    GMOService._scan_one = _scan_one
    GMOService.start = start
    GMOService.snapshot = snapshot
    _INSTALLED = True
