"""0.3.4 (owner 2026-11-23, issue #149): reward/risk-based buy size for GEN(AT) / MA / Trend, coin AND stock.

Owner: "고정매수금액은 손익비가 1:0.5 이하인 종목에만 세팅해봐" (after the 2026-11-23 ledger
report: equal buy amounts with stop distances from -0.7 % to -25 % let one wide-stop loss wipe out
several wins).

Interpretation (INFERRED, reported to the owner):
  손익비 = loss to the stop : gain to the FIRST target, both measured from the entry at the BUY.
  "1:0.5 이하" = gain / loss <= 0.5 (the stop is at least twice as far as the first target).
Rule:
  ratio = gain / loss
  ratio >  0.5  -> unchanged fixed amount (factor 1)
  ratio <= 0.5  -> amount x (ratio / 0.5), floor MIN_FACTOR, i.e. the loss at the stop is capped at
                   what a 1:0.5 position of the full fixed amount would lose (2 x first-target gain).
Stops used (the ones the exits really apply):
  GEN (reference) : nl.at_hard_stop(reference low, reference high).
  MA / Trend      : the 11.20(2) lookback-low stop (MA 50 bars, Trend 200/90/90) when below the entry,
                    never farther than the -15 % hard stop.
  Stock AT         : same reference hard stop from the reference candle (D19 parent), entry = the order limit.
  Stock MA / Trend : same lookback stops from the KRX bars table / coordinator bars.
  (0.3.4 first shipped for coin only because the ledger had no stock trades; the owner asked for stock too.)
Anything unverifiable (no bars, busy lock, bad numbers) -> factor 1 (the previous behaviour) and a
reason that is recorded. Pure arithmetic + in-memory reads only: no network, no order, no fill.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

RATIO_LIMIT = 0.5
MIN_FACTOR = 0.10
HARD_STOP_FRACTION = 0.85
VERSION = "RR_SIZE_V0340"


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v not in (float("inf"), float("-inf")) else None


def evaluate(entry, first, stop):
    """dict(factor, ratio, gain_pct, loss_pct, applied, reason). Never raises."""
    e, f, s = _num(entry), _num(first), _num(stop)
    out = {"version": VERSION, "factor": 1.0, "ratio": None, "gain_pct": None, "loss_pct": None,
           "applied": False, "reason": "RR_UNVERIFIED"}
    if e is None or f is None or s is None or e <= 0 or f <= e or s <= 0:
        return out
    gain = f / e - 1.0
    loss = 1.0 - s / e
    out["gain_pct"] = round(gain * 100, 3)
    if loss <= 0:
        out["reason"] = "RR_STOP_NOT_BELOW_ENTRY"
        return out
    ratio = gain / loss
    out.update({"ratio": round(ratio, 4), "loss_pct": round(loss * 100, 3)})
    if ratio > RATIO_LIMIT:
        out["reason"] = "RR_ABOVE_1_TO_0_5_FIXED_AMOUNT"
        return out
    out.update({"factor": round(max(MIN_FACTOR, ratio / RATIO_LIMIT), 4), "applied": True,
                "reason": "RR_AT_OR_BELOW_1_TO_0_5_SIZED"})
    return out


def effective_ma_trend_stop(entry, lookback_low):
    """Stop the MA/Trend exits really hit first: lookback low if below entry, never beyond -15 %."""
    e = _num(entry)
    if e is None or e <= 0:
        return None
    hard = e * HARD_STOP_FRACTION
    low = _num(lookback_low)
    if low is None or low <= 0 or low >= e:
        return hard
    return max(low, hard)


def capped(amount, factor):
    """Integer KRW amount after the factor (never above the amount)."""
    try:
        return int(min(float(amount), float(amount) * float(factor)))
    except (TypeError, ValueError):
        return int(amount)


# ------------------------------------------------------------------ stops before the BUY

def _lookback_low(kind, frame, raw_bars, now, source=None):
    from new_legacy_1120_policy import _completed, lookback_stop
    bars = [dict(b, source=source) if source else dict(b) for b in raw_bars if isinstance(b, dict)]
    status, price, used = lookback_stop(kind, frame, _completed(bars, frame, now), now)
    return (price if status == "CANDIDATE" else None), status, used


def _unverified(status):
    """Lookback window not available (too few bars / unsupported frame): the stop is unknown here, so the
    amount stays as before (factor 1). A stop that exists but lies above the entry is NOT unknown: the
    -15 % hard stop applies (see effective_ma_trend_stop)."""
    return dict(evaluate(None, None, None), reason="RR_LOOKBACK_" + str(status), stop_source=str(status))


def _ma_coin_impl(frame, market, entry, now):
    """Evaluation for a coin MA BUY at `entry` now, from the MA scanner's verified candle cache."""
    try:
        import ma_v061_coin_runtime as native_history
        import ma_v067_policy as policy
        lock = native_history._HISTORY_LOCK
        if not lock.acquire(timeout=0.2):
            return dict(evaluate(None, None, None), reason="RR_HISTORY_LOCK_BUSY")
        try:
            source = native_history._HISTORY.get((str(frame), str(market)))
        finally:
            lock.release()
        if not source:
            return dict(evaluate(None, None, None), reason="RR_NO_MA_HISTORY")
        low, status, used = _lookback_low("ma", frame, source[1], now, "ORIGINAL_UPBIT_MA_VERIFIED")
        if status != "CANDIDATE":
            return _unverified(status)
        first = float(policy.up_targets(entry, frame)[0])
        out = evaluate(entry, first, effective_ma_trend_stop(entry, low))
        out.update({"stop_source": "MA_LOOKBACK_%s_%d" % (status, used), "lookback_low": low})
        return out
    except Exception as exc:
        return dict(evaluate(None, None, None), reason="RR_ERROR_" + type(exc).__name__)


def _gen_coin_impl(c, order, entry):
    """Evaluation for a coin GEN (reference-bar) BUY: hard stop under the reference candle."""
    try:
        import new_legacy_1118_policy as nl
        r = c.execute("SELECT features FROM candidates WHERE tf=? AND market=? AND ref_at=?"
                      " ORDER BY id DESC LIMIT 1", (order["tf"], order["market"], order["ref_at"])).fetchone()
        facts = json.loads(r[0]) if r and r[0] else {}
        plan = nl.targets(entry, order["tf"], "reference", ref_low=facts.get("low"), ref_high=facts.get("high"))
        out = evaluate(entry, plan["first"], plan["stop"])
        out["stop_source"] = "REFERENCE_HARD_STOP"
        return out
    except Exception as exc:
        return dict(evaluate(None, None, None), reason="RR_ERROR_" + type(exc).__name__)


def _trend_impl(rt, market, symbol, frame, entry, now):
    """Evaluation for a MarketTrend BUY from the coordinator's in-memory closed bars (tried lock)."""
    try:
        import new_legacy_1118_policy as nl
        co = getattr(rt, "market_trend", None)
        rows = None
        if co is not None:
            lock = getattr(co, "lock", None)
            got = lock.acquire(blocking=False) if lock is not None else True
            if got:
                try:
                    rows = list(co._history.get((market, str(symbol), frame), ()))
                finally:
                    if lock is not None:
                        lock.release()
        low, status, used = (None, "NO_TREND_HISTORY", 0)
        if rows:
            # The in-memory window can be shorter than 200/90 bars: its minimum is then HIGHER than the
            # full window's, so the risk is never over-estimated here (factor is never too small).
            low, status, used = _lookback_low("trend", frame, rows, now)
        if status != "CANDIDATE":
            return _unverified(status)
        first = float(entry) * (1 + nl.FIRST_GAIN[frame])
        out = evaluate(entry, first, effective_ma_trend_stop(entry, low))
        out.update({"stop_source": "TREND_LOOKBACK_%s_%d" % (status, used), "lookback_low": low})
        return out
    except Exception as exc:
        return dict(evaluate(None, None, None), reason="RR_ERROR_" + type(exc).__name__)


# ------------------------------------------------------------------ stored factor (top-ups)

def stored_coin_factor(c, position_id, variant):
    """Factor recorded at the BUY for a coin position (1.0 when none): top-ups must not refill it."""
    try:
        if variant == "ma":
            row = c.execute("SELECT state_json FROM ma_v067_positions WHERE position_id=?",
                            (position_id,)).fetchone()
        else:
            row = c.execute("SELECT intent_json FROM nl1118_reference_positions WHERE position_id=?",
                            (position_id,)).fetchone()
        if row is None:
            return 1.0
        f = _num(json.loads(row[0] or "{}").get("rr_size_factor"))
        return f if f is not None and 0 < f <= 1 else 1.0
    except (sqlite3.Error, TypeError, ValueError, KeyError, IndexError):
        return 1.0


def record(target, info):
    """Write the evaluation into a sidecar dict (state/plan/payload)."""
    target["rr_size_factor"] = str(info.get("factor", 1.0))
    target["rr_size"] = {k: info.get(k) for k in ("version", "ratio", "gain_pct", "loss_pct", "applied",
                                                  "reason", "stop_source")}
    if isinstance(info.get("entry_ctx"), dict):
        target["entry_ctx"] = info["entry_ctx"]      # 0.3.7: BUY-time MA slopes + RR per position id
    return target


# ------------------------------------------------------------------ stock

def stock_bars_before(store, code, frame, now, limit=60):
    """Completed KRX bars (dicts for new_legacy_1120_policy) ending at/before `now`, read-only."""
    import d_engine
    stamp = now.strftime("%Y%m%d%H%M%S")
    with store.connect() as db:
        older = db.execute("SELECT ts,low,high,close,source FROM bars WHERE code=? AND frame=? AND ts<?"
                           " ORDER BY ts DESC LIMIT ?", (code, frame, stamp, int(limit))).fetchall()
    out = []
    for row in reversed(older):
        s = str(row["ts"])[:14]
        if len(s) != 14:
            continue
        begins = dt.datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=now.tzinfo)
        ends = dt.datetime.strptime(str(d_engine.bar_end(s, frame))[:14], "%Y%m%d%H%M%S").replace(tzinfo=now.tzinfo)
        out.append({"start": begins, "end": ends, "low": row["low"], "high": row["high"],
                    "close": row["close"], "source": str(row["source"] or "")})
    return out


def _ma_stock_impl(store, code, frame, entry, now):
    """Evaluation for a stock MA REVERSAL_UP BUY at `entry` (KRW) at aware time `now`."""
    try:
        import ma_v067_policy as policy
        low, status, used = _lookback_low("ma", frame, stock_bars_before(store, code, frame, now), now)
        if status != "CANDIDATE":
            return _unverified(status)
        first = float(policy.up_targets(entry, frame)[0])
        out = evaluate(entry, first, effective_ma_trend_stop(entry, low))
        out.update({"stop_source": "MA_STOCK_LOOKBACK_%s_%d" % (status, used), "lookback_low": low})
        return out
    except Exception as exc:
        return dict(evaluate(None, None, None), reason="RR_ERROR_" + type(exc).__name__)


def _at_stock_impl(engine, c, row):
    """Evaluation for a stock AT (D19) BUY: reference-candle hard stop; entry = the order's limit."""
    try:
        import new_legacy_1118_policy as nl
        signal = c.execute("SELECT * FROM signals WHERE id=?", (row["signal_id"],)).fetchone()
        if signal is None:
            return dict(evaluate(None, None, None), reason="RR_NO_SIGNAL")
        parent = engine._reference_parent(c, signal, json.loads(signal["features"]))
        if parent is None:
            return dict(evaluate(None, None, None), reason="RR_NO_REFERENCE_PARENT")
        entry = float(row["limit_won"])
        plan = nl.targets(entry, row["tf"], "reference", ref_low=int(parent["low"]), ref_high=int(parent["high"]))
        out = evaluate(entry, plan["first"], plan["stop"])
        out["stop_source"] = "AT_REFERENCE_HARD_STOP"
        return out
    except Exception as exc:
        return dict(evaluate(None, None, None), reason="RR_ERROR_" + type(exc).__name__)


def stock_limit_won(base_limit_won, factor, buy_limit_won, fee_rate):
    """Per-symbol KRW cap after the factor, never below ONE share (else the order could never fill)."""
    reduced = int(base_limit_won * factor)
    one_share = int(buy_limit_won * (1 + fee_rate)) + 2
    return max(reduced, min(int(base_limit_won), one_share))


# ------------------------------------------------------------------ 0.3.7 BUY context (RR + MA 5/10/20/60/120)
# Each public evaluator keeps its old result and additionally carries `entry_ctx`
# (entry_context_v0370): the RR at the BUY and the MA slopes from the COMPLETED bars before it.

def _closes_from_bars(bars, frame, now, source=None):
    from new_legacy_1120_policy import _completed
    rows = [dict(b, source=source) if source else dict(b) for b in bars if isinstance(b, dict)]
    return [c for (_end, _low, c) in _completed(rows, frame, now)]


def _ctx_coin_native(frame, market, now):
    import ma_v061_coin_runtime as native_history
    lock = native_history._HISTORY_LOCK
    if not lock.acquire(timeout=0.2):
        return None, "ENTRY_CTX_HISTORY_LOCK_BUSY"
    try:
        source = native_history._HISTORY.get((str(frame), str(market)))
    finally:
        lock.release()
    if not source:
        return None, "ENTRY_CTX_NO_NATIVE_HISTORY"
    return _closes_from_bars(source[1], frame, now, "ORIGINAL_UPBIT_MA_VERIFIED"), None


def _ctx_trend_rows(rt, market, symbol, frame, now):
    co = getattr(rt, "market_trend", None)
    if co is None:
        return None, "ENTRY_CTX_NO_TREND_COORDINATOR"
    lock = getattr(co, "lock", None)
    got = lock.acquire(blocking=False) if lock is not None else True
    if not got:
        return None, "ENTRY_CTX_TREND_LOCK_BUSY"
    try:
        rows = list(co._history.get((market, str(symbol), frame), ()))
    finally:
        if lock is not None:
            lock.release()
    return _closes_from_bars(rows, frame, now), None


def _ctx_stock_bars(store, code, frame, now):
    if store is None:
        return None, "ENTRY_CTX_NO_STOCK_STORE"
    rows = stock_bars_before(store, code, frame, now, limit=140)
    return [r["close"] for r in rows if r.get("source")], None


def _finish(out, entry, getter):
    import entry_context_v0370 as ctx
    try:
        closes, why = getter()
    except Exception as exc:
        closes, why = None, "ENTRY_CTX_ERROR_" + type(exc).__name__
    return ctx.attach(out, entry, closes or [], why)


def ma_coin(frame, market, entry, now):
    return _finish(_ma_coin_impl(frame, market, entry, now), entry,
                   lambda: _ctx_coin_native(frame, market, now))


def gen_coin(c, order, entry):
    now = dt.datetime.now(dt.timezone.utc)
    return _finish(_gen_coin_impl(c, order, entry), entry,
                   lambda: _ctx_coin_native(order["tf"], order["market"], now))


def trend(rt, market, symbol, frame, entry, now):
    return _finish(_trend_impl(rt, market, symbol, frame, entry, now), entry,
                   lambda: _ctx_trend_rows(rt, market, symbol, frame, now))


def ma_stock(store, code, frame, entry, now):
    return _finish(_ma_stock_impl(store, code, frame, entry, now), entry,
                   lambda: _ctx_stock_bars(store, code, frame, now))


def at_stock(engine, c, row):
    out = _at_stock_impl(engine, c, row)
    now = dt.datetime.now(dt.timezone.utc)
    store = getattr(engine, "store", None) or getattr(engine, "market_store", None)
    try:
        entry = float(row["limit_won"])
    except Exception:
        entry = None
    return _finish(out, entry, lambda: _ctx_stock_bars(store, str(row["code"]) if "code" in row.keys() else "", row["tf"], now))
