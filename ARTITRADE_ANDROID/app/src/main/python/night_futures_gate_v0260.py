"""0.2.6 (owner instruction 2026-11-22): overnight Korea signal for STOCK buys.

Night futures window: day D 18:00 -> D+1 06:00 (KST). The owner asked for the
KRX KOSPI200 night-futures value from the Kiwoom API first and Yahoo second.
The Kiwoom REST paths used by this app are domestic-stock only
(/api/dostk/*); no night-futures quote path exists in the app, so the value is
taken from Yahoo: iShares MSCI South Korea ETF (EWY), the US session
(23:30-06:00 KST) open -> close change. This is the same proxy the owner's
-2.6 % threshold was chosen from (bottom ~5 % of nights, 2024-11..2026-11).

Rules (stock strategies only; coins are never touched). Owner 2026-11-22,
final wording (supersedes the same-day "-2.6 % / 3 minutes" draft):
1. SEVERE night (change <= -3.0 %): stock reference bar (AT), stock MA and
   stock MarketTrend make no BUY during the first 30 minutes of the KRX
   regular session. (Close-betting buys only 15:10-15:20 and is gated the
   same way, which never changes its behaviour.) Supply-demand is NOT blocked.
2. Held AT / MA / MarketTrend stock positions: when the night is SEVERE
   (change <= -3.0 %; owner 0.2.8, 2026-11-22: "-3% 이하인 날에만", replacing
   "any down night") and
   current price x (1 + night change) <= the position's nearest stop, the
   whole position is sold at the earliest verified moment: from 08:00 on the
   NXT pre-market book (owner, same day), else from the KRX regular open,
   through the strategy's own wallet (manual-exit path, reason
   AUTO_NIGHT_EWY_PREEMPTIVE_STOP). Decided once at 07:55.
3. Supply-demand (GMO) D+1 BUY price: current price <= D close x (1 + night
   change) (down night: lower; up night: higher), rounded DOWN to a valid KRX
   tick. Replaces "below D+1 open OR below D close". If the night value is
   not verified the former rule stays in force and the reason is recorded.

Read-only network access (public Yahoo chart). No order, fill, wallet or
price is created here. Buy paths only ASK this module whether they may buy.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import threading
import time

KST = dt.timezone(dt.timedelta(hours=9))
SEVERE_PCT = -3.0
BLOCK_SECONDS = 1800
BLOCKED_STRATEGIES = ("at", "ma", "trend", "close")
PREEMPT_DECIDE_BEFORE_OPEN_SECONDS = 300
# 0.2.8: the pre-emptive exit runs only on SEVERE nights (same -3.0 % threshold).
PREEMPT_MAX_PCT = SEVERE_PCT
PREMARKET_OPEN = dt.time(8, 0)
PREEMPT_REASON = "NIGHT_EWY_PREEMPTIVE_STOP"
BLOCK_REASON = "NIGHT_FUTURES_SEVERE_FIRST_30MIN_BUY_BLOCK"
SYMBOL = "EWY"
SOURCE = "YAHOO_EWY_US_SESSION_OPEN_TO_CLOSE"
SOURCE_PRIORITY = ("KIWOOM_REST_NIGHT_FUTURES_NOT_AVAILABLE_DOSTK_ONLY", SOURCE)
US_SESSION_SECONDS = 6 * 3600 + 30 * 60
FETCH_FROM = dt.time(6, 5)
FETCH_UNTIL = dt.time(15, 30)
URL = "https://query1.finance.yahoo.com/v8/finance/chart/EWY"

_LOCK = threading.Lock()
_STATE = {}
_WORKER = None
_ROOT = None
_INSTALLED = False


def _now():
    return dt.datetime.now(KST)


def _session(day):
    from krx_session_v041 import SPECIAL_SESSIONS, START, END
    return SPECIAL_SESSIONS.get(day.isoformat(), (START, END))


def is_krx_day(day):
    try:
        from market_trend_v043 import _krx_day
        return bool(_krx_day(day))
    except Exception:
        return day.weekday() < 5


def previous_krx_close(day):
    prior = day - dt.timedelta(days=1)
    for _ in range(15):
        if is_krx_day(prior):
            closing = _session(prior)[1]
            return dt.datetime.combine(prior, dt.time(closing // 60, closing % 60), tzinfo=KST)
        prior -= dt.timedelta(days=1)
    return None


def regular_open_at(day):
    opening = _session(day)[0]
    return dt.datetime.combine(day, dt.time(opening // 60, opening % 60), tzinfo=KST)


def parse_us_session(obj, now, prev_close):
    """Latest COMPLETE EWY US session that closed after the previous KRX close.

    Yahoo 1d bars are stamped with the regular-session open (09:30 New York);
    the session closes 6.5 h later. Returns a dict or a dict with 'reason'.
    """
    try:
        result = obj["chart"]["result"][0]
        stamps = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        opens, closes = quote["open"], quote["close"]
    except (KeyError, IndexError, TypeError):
        return {"reason": "YAHOO_FORMAT_INVALID"}
    best = None
    for i, start in enumerate(stamps or ()):
        try:
            o = float(opens[i]); c = float(closes[i]); start = int(start)
        except (TypeError, ValueError, IndexError):
            continue
        if not (math.isfinite(o) and math.isfinite(c) and o > 0 and c > 0):
            continue
        closed_at = dt.datetime.fromtimestamp(start + US_SESSION_SECONDS, KST)
        if closed_at > now:
            continue  # unfinished US session
        if prev_close is not None and closed_at <= prev_close:
            continue  # belongs to an earlier Korean day
        best = (start, o, c, closed_at)
    if best is None:
        return {"reason": "NO_US_SESSION_SINCE_PREVIOUS_KRX_CLOSE"}
    start, o, c, closed_at = best
    return {"change_pct": round((c / o - 1.0) * 100.0, 4), "open": o, "close": c,
            "us_session_open_kst": dt.datetime.fromtimestamp(start, KST).isoformat(),
            "us_session_close_kst": closed_at.isoformat()}


def _fetch():
    from market_research_v033 import _public_json
    return _public_json(URL, {"interval": "1d", "range": "5d", "includePrePost": "false"})


def refresh(now=None, fetch=None):
    """Fetch once for today's KRX date. Never raises; returns the state."""
    now = now or _now()
    day = now.date()
    state = {"date": day.isoformat(), "status": "UNKNOWN", "change_pct": None,
             "severe": False, "threshold_pct": SEVERE_PCT, "source": SOURCE,
             "source_priority": list(SOURCE_PRIORITY), "checked_at": now.isoformat()}
    try:
        obj = (fetch or _fetch)()
        parsed = parse_us_session(obj, now, previous_krx_close(day))
        if "change_pct" in parsed:
            state.update(parsed)
            state["status"] = "VERIFIED"
            state["severe"] = parsed["change_pct"] <= SEVERE_PCT
        else:
            state["status"] = parsed.get("reason", "UNKNOWN")
    except Exception as exc:
        state["status"] = "FETCH_FAILED_" + type(exc).__name__
    with _LOCK:
        old = _STATE.get(day.isoformat())
        # Never replace a verified value of the same day with a later failure.
        if not (old and old.get("status") == "VERIFIED" and state["status"] != "VERIFIED"):
            _STATE[day.isoformat()] = state
        current = dict(_STATE[day.isoformat()])
    _persist(current)
    return current


def set_state_for_test(state):
    with _LOCK:
        _STATE[str(state["date"])] = dict(state)


def state(now=None):
    now = now or _now()
    with _LOCK:
        found = _STATE.get(now.date().isoformat())
        return dict(found) if found else {"date": now.date().isoformat(),
            "status": "NOT_CHECKED_YET", "change_pct": None, "severe": False,
            "threshold_pct": SEVERE_PCT, "source": SOURCE}


def first_minutes_block(now=None):
    """(blocked, info). Blocked only on a VERIFIED severe night, first 30 min after open."""
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    now = now.astimezone(KST)
    current = state(now)
    if not (current.get("status") == "VERIFIED" and current.get("severe")):
        return False, current
    begin = regular_open_at(now.date())
    if begin <= now < begin + dt.timedelta(seconds=BLOCK_SECONDS):
        return True, dict(current, block_until=(begin + dt.timedelta(seconds=BLOCK_SECONDS)).isoformat())
    return False, current


def gmo_ceiling(d_close, now=None):
    """Owner rule: buy when price <= D close x (1 + night change), tick floored.

    Returns (ceiling or None, info). None -> night value unverified; the
    caller keeps the former OR rule and records why.
    """
    current = state(now)
    try:
        close = int(d_close)
    except (TypeError, ValueError):
        return None, dict(current, reason="D_CLOSE_INVALID")
    if close <= 0:
        return None, dict(current, reason="D_CLOSE_INVALID")
    if current.get("status") != "VERIFIED" or current.get("change_pct") is None:
        return None, dict(current, reason="NIGHT_VALUE_UNVERIFIED_KEEP_ORIGINAL_RULE")
    from projectgmo.prices import down_tick
    raw = close * (1.0 + float(current["change_pct"]) / 100.0)
    try:
        ceiling = int(down_tick(raw))
    except ValueError:
        return None, dict(current, reason="CEILING_TICK_INVALID")
    return ceiling, dict(current, reason="EWY_ADJUSTED_D_CLOSE", raw_ceiling=round(raw, 4),
                         d_close=close, ceiling=ceiling)


def _persist(current):
    root = _root_dir()
    if not root:
        return
    try:
        path = os.path.join(str(root), "stock", "night_futures_gate_v0260.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        pass


def _load(root):
    try:
        path = os.path.join(str(root), "stock", "night_futures_gate_v0260.json")
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict) and saved.get("date"):
            with _LOCK:
                _STATE.setdefault(str(saved["date"]), saved)
    except (OSError, ValueError):
        pass


def record_block(strategy, code, frame, info):
    """Decision evidence through the existing execution-decision journal."""
    try:
        import android_live_bridge as bridge
        rt = getattr(bridge, "_RUNTIME", None)
        co = getattr(rt, "market_trend", None) if rt is not None else None
        if co is not None:
            co._record_entry_block("stock", str(code), str(frame or "all"),
                str(info.get("date")), BLOCK_REASON,
                strategy=str(strategy),
                ma_details={"change_pct": info.get("change_pct"),
                            "threshold_pct": SEVERE_PCT, "source": info.get("source"),
                            "block_until": info.get("block_until"), "not_an_order": True})
    except Exception:
        pass


# ------------------------------------------------ pre-emptive stop exits
_PREEMPT = {}


def _runtime():
    try:
        import android_live_bridge as bridge
        return getattr(bridge, "_RUNTIME", None)
    except Exception:
        return None


def _root_dir():
    if _ROOT:
        return _ROOT
    rt = _runtime()
    return getattr(rt, "root", None) if rt is not None else None


def _latest_mark(rt, code, fallback):
    try:
        tick = rt.stock_runner.latest_krx_trade(code)
        if tick and len(tick) >= 5 and str(tick[0]) == str(code) and float(tick[4]) > 0:
            return float(tick[4])
    except Exception:
        pass
    try:
        value = float(fallback)
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


def _rows(db, sql):
    import sqlite3
    from pathlib import Path
    path = Path(db)
    if not path.is_file():
        return []
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql)]


def stock_holdings(rt):
    """Open stock AT / MA / MarketTrend positions with every stop candidate.

    Uses the same stop definitions as the holding card (position_view_v0200).
    A position whose stop plan is unknown is skipped, never guessed.
    """
    import position_view_v0200 as view
    from pathlib import Path
    out = []
    root = getattr(rt, "root", None)
    if root:
        db = Path(root) / "stock" / "ARTITRADE_D.sqlite3"
        for r in _rows(db, "SELECT id,code,tf,entry_price,qty,last_price FROM positions "
                           "WHERE status='OPEN' AND qty>0"):
            mark = _latest_mark(rt, r["code"], r.get("last_price"))
            pos = {"id": r["id"], "entry_price": r["entry_price"], "last_price": mark}
            view.enrich_reference([pos], db)
            out.append({"strategy": "at", "code": str(r["code"]), "position_id": int(r["id"]),
                        "entry": float(r["entry_price"] or 0), "mark": mark,
                        "stops": pos.get("stop_loss_all") or []})
    ma = getattr(getattr(rt, "stock_runner", None), "ma", None)
    if ma is not None:
        db = Path(ma.db)
        for r in _rows(db, "SELECT id,code,tf,entry,qty,last_price FROM positions "
                           "WHERE status='OPEN' AND qty>0"):
            mark = _latest_mark(rt, r["code"], r.get("last_price"))
            pos = {"id": r["id"], "tf": r["tf"], "entry": r["entry"], "last_price": mark}
            view.enrich_ma([pos], db)
            out.append({"strategy": "ma", "code": str(r["code"]), "position_id": int(r["id"]),
                        "entry": float(r["entry"] or 0), "mark": mark,
                        "stops": pos.get("stop_loss_all") or []})
    co = getattr(rt, "market_trend", None)
    if co is not None:
        with co.lock:
            current = [p for p in co._positions.values()
                       if p.market == "stock" and float(p.qty or 0) > 0]
        for p in current:
            mark = _latest_mark(rt, p.symbol, getattr(p, "mark", None))
            payload = dict(p.__dict__)
            pos = {"entry": float(p.entry), "mark": mark}
            view.enrich_trend_row(pos, payload)
            out.append({"strategy": "trend", "code": str(p.symbol),
                        "position_id": int(getattr(p, "position_id", -1)),
                        "entry": float(p.entry), "mark": mark,
                        "stops": pos.get("stop_loss_all") or []})
    return out


def preemptive_plan(holdings, change_pct):
    """Positions whose stop would be touched by a SEVERE (<= -3 %) night decline."""
    import position_view_v0200 as view
    plan = []
    if change_pct is None or float(change_pct) > PREEMPT_MAX_PCT:
        return plan
    for h in holdings:
        if not h.get("mark") or not h.get("entry") or not h.get("stops") or h["position_id"] < 0:
            continue
        projected = h["mark"] * (1.0 + change_pct / 100.0)
        pick = view.nearest_stop(h["stops"], h["mark"], h["entry"])
        if pick and projected <= float(pick["price"]):
            plan.append(dict(h, projected=round(projected, 4), stop=float(pick["price"]),
                             stop_kind=pick.get("label"), done=False, attempts=0,
                             last_error=""))
    return plan


def _current_qty(rt, item):
    from pathlib import Path
    if item["strategy"] == "at":
        rows = _rows(Path(rt.root) / "stock" / "ARTITRADE_D.sqlite3",
                     "SELECT qty FROM positions WHERE id=%d AND status='OPEN'" % int(item["position_id"]))
        return int(rows[0]["qty"]) if rows else 0
    if item["strategy"] == "ma":
        rows = _rows(Path(rt.stock_runner.ma.db),
                     "SELECT qty FROM positions WHERE id=%d AND status='OPEN'" % int(item["position_id"]))
        return int(float(rows[0]["qty"])) if rows else 0
    co = rt.market_trend
    with co.lock:
        for p in co._positions.values():
            if (p.market == "stock" and str(p.symbol) == item["code"] and
                    int(getattr(p, "position_id", -1)) == item["position_id"]):
                return int(round(float(p.qty or 0)))
    return 0


def _sell_once(rt, item, venue="KRX"):
    import uuid
    import android_manual_paper_sell_v085 as manual
    qty = _current_qty(rt, item)
    if qty <= 0:
        item["done"] = True
        return {"ok": True, "status": "CLOSED"}
    request = {"market": "stock", "strategy": item["strategy"], "code": item["code"],
               "request_id": str(uuid.uuid4()), "percent": 100,
               "position_id": item["position_id"], "expected_qty": str(qty),
               "auto_reason": PREEMPT_REASON, "venue": venue}
    result = json.loads(manual.perform(request))
    item["attempts"] += 1
    item["last_venue"] = venue
    if result.get("ok"):
        if _current_qty(rt, item) <= 0:
            item["done"] = True
    else:
        item["last_error"] = str(result.get("error"))[:80]
    return result


def _record_preempt(item, reason, info):
    try:
        rt = _runtime()
        co = getattr(rt, "market_trend", None) if rt is not None else None
        if co is not None:
            co._record_entry_block("stock", item["code"], item["strategy"],
                str(info.get("date")), reason, strategy=item["strategy"],
                ma_details={"position_id": item["position_id"], "mark": item.get("mark"),
                            "projected": item.get("projected"), "stop": item.get("stop"),
                            "stop_kind": item.get("stop_kind"),
                            "change_pct": info.get("change_pct"),
                            "attempts": item.get("attempts"),
                            "last_error": item.get("last_error"), "paper_only": True})
    except Exception:
        pass


def exit_venue(now):
    """Venue for the automatic exit at `now`: NXT pre-market first, then KRX.

    The engine's NXT window is 08:00-08:30 (d_v19_portfolio.live_session);
    days with a closed NXT pre-market (e.g. CSAT) go straight to KRX.
    """
    from d_v19_portfolio import live_session
    try:
        from gmo_d1_preplan_v097 import NXT_PREMARKET_CLOSED
    except Exception:
        NXT_PREMARKET_CLOSED = ()
    if now.date().isoformat() not in NXT_PREMARKET_CLOSED and live_session(now, "NXT"):
        return "NXT"
    begin = regular_open_at(now.date())
    if begin <= now < begin + dt.timedelta(seconds=BLOCK_SECONDS) and live_session(now, "KRX"):
        return "KRX"
    return None


def preemptive_step(now=None):
    """Decide at 07:55, sell from 08:00 (NXT) and from the KRX open. Returns delay (s)."""
    now = now or _now()
    if not is_krx_day(now.date()):
        return 60
    premarket = dt.datetime.combine(now.date(), PREMARKET_OPEN, tzinfo=KST)
    begin = regular_open_at(now.date())
    if now < premarket - dt.timedelta(seconds=PREEMPT_DECIDE_BEFORE_OPEN_SECONDS):
        return 30 if now >= premarket - dt.timedelta(minutes=15) else 60
    if now >= begin + dt.timedelta(seconds=BLOCK_SECONDS):
        return 60
    info = state(now)
    if (info.get("status") != "VERIFIED" or info.get("change_pct") is None or
            float(info["change_pct"]) > PREEMPT_MAX_PCT):
        return 10
    rt = _runtime()
    if rt is None or not getattr(rt, "started", False):
        return 5
    key = now.date().isoformat()
    plan = _PREEMPT.get(key)
    if plan is None:
        try:
            plan = preemptive_plan(stock_holdings(rt), float(info["change_pct"]))
        except Exception:
            return 5
        _PREEMPT[key] = plan
        for item in plan:
            _record_preempt(item, "NIGHT_EWY_PREEMPTIVE_STOP_PLANNED", info)
    venue = exit_venue(now)
    if venue is None:
        return 1
    for item in plan:
        if item["done"]:
            continue
        try:
            _sell_once(rt, item, venue)
        except Exception as exc:
            item["last_error"] = type(exc).__name__
        if item["done"]:
            _record_preempt(item, "NIGHT_EWY_PREEMPTIVE_STOP_SOLD", info)
        elif item["attempts"] in (1, 30, 300, 1200):
            _record_preempt(item, "NIGHT_EWY_PREEMPTIVE_STOP_RETRYING", info)
    return 1


def _worker_loop():
    last_fetch = 0.0
    while True:
        delay = 60
        try:
            now = _now()
            rt = _runtime()
            if rt is None or not getattr(rt, "started", False):
                # Unit harnesses import the runtime without starting it: no
                # network fetch and no exit attempt outside a running engine.
                time.sleep(30)
                continue
            if (is_krx_day(now.date()) and FETCH_FROM <= now.time() < FETCH_UNTIL and
                    state(now).get("status") != "VERIFIED" and
                    time.monotonic() - last_fetch >= 60):
                last_fetch = time.monotonic()
                refresh(now)
            delay = min(delay, preemptive_step(now))
        except Exception:
            delay = 5
        time.sleep(max(1, delay))


def start_worker(root=None):
    global _WORKER, _ROOT
    if root:
        _ROOT = root
        _load(root)
    if _WORKER is not None and _WORKER.is_alive():
        return _WORKER
    _WORKER = threading.Thread(target=_worker_loop, daemon=True,
                               name="artitrade-night-futures-v0260")
    _WORKER.start()
    return _WORKER


def blocked_now(strategy, code="", frame="", now=None):
    if strategy not in BLOCKED_STRATEGIES:
        return False
    blocked, info = first_minutes_block(now)
    if blocked:
        record_block(strategy, code, frame, info)
    return blocked


def install(core=None):
    """Wrap only native STOCK BUY entry functions; SELL paths are untouched."""
    global _INSTALLED
    if _INSTALLED:
        return
    import d_engine
    import d_v19_portfolio
    from market_trend_wallet_v054 import TrendWalletV054

    native_legacy = d_engine.PaperEngine._buy_if_touch
    native_d19 = d_v19_portfolio.D19PaperEngine._buy_limit_depth
    native_trend_buy = TrendWalletV054.buy

    def legacy_stock(self, c, tf, minute, at):
        if blocked_now("at", "", tf):
            return None
        return native_legacy(self, c, tf, minute, at)

    def d19_stock(self, c, row, when, venue, observed_trade_price, observed_trade_qty=0):
        try:
            code = dict(row).get("code", "")
            frame = dict(row).get("tf", "")
        except Exception:
            code, frame = "", ""
        if blocked_now("at", code, frame):
            return False, BLOCK_REASON
        return native_d19(self, c, row, when, venue, observed_trade_price, observed_trade_qty)

    def trend_buy(self, market, frame, symbol, name, ask, ask_size, at, bar_key, rsi20_live):
        if market == "stock" and blocked_now("trend", symbol, frame):
            return None, BLOCK_REASON
        return native_trend_buy(self, market, frame, symbol, name, ask, ask_size,
                                at, bar_key, rsi20_live)

    d_engine.PaperEngine._buy_if_touch = legacy_stock
    d_v19_portfolio.D19PaperEngine._buy_limit_depth = d19_stock
    TrendWalletV054.buy = trend_buy
    try:
        _close_buy_gate()
    except Exception:
        pass
    root = None
    try:
        rt = getattr(core, "_RUNTIME", None) if core is not None else None
        root = getattr(rt, "root", None)
    except Exception:
        root = None
    start_worker(root)
    _INSTALLED = True


def _close_buy_gate():
    """Close-betting buys only 15:10-15:20, never inside 09:00-09:30.

    Its single native BUY entry is gated the same way; this never changes its
    behaviour (a no-op outside the first 30 minutes).
    """
    import at_close_engine
    engine = getattr(at_close_engine, "PaperEngine", None)
    if engine is None or not hasattr(engine, "open"):
        return
    native = engine.open

    def gated(self, strategy, rank, signal_id, ask, ask_qty, feature):
        try:
            code = rank.get("code", "")
        except AttributeError:
            code = ""
        if blocked_now("close", code, "close"):
            return False, BLOCK_REASON
        return native(self, strategy, rank, signal_id, ask, ask_qty, feature)
    engine.open = gated
