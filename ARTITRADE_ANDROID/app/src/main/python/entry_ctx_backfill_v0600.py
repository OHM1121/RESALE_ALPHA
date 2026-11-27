"""0.6.0 (owner 2026-11-27): after the close, the BUY-time context (MA 5/10/20/60/120 above / below and slope, trend) of every STOCK position is completed from the stored bars.

Owner: "매도 체결된 종목의 매수 당시 MA 위/아래와 추세를 장 마감 후 채우기 ... 꼼꼼하고 detail하게 누락된 건 없는지 잘 체크하면서" (stock only; the market score stays real time).

Why it is needed: `entry_context_v0370` writes `entry_ctx` into the per-position sidecar AT THE BUY. Positions bought before 0.3.7 (all 34 stock AT positions of the device export: none has `entry_ctx`)
and positions bought while the bars were short or missing have no or partial context. This module recomputes it from the COMPLETED bars that existed before the BUY and writes it into the same sidecar.

Sources (all three stock owners; supply-demand (GMO) has no sidecar and is not touched):
  AT         stock `nl1118_reference_positions.intent_json`   (positions p: code, tf, entry_ts, entry_price)
  MA         `ma_v067_positions.state_json` in the MA database (positions: code, tf, entry_at, entry)
  MarketTrend `positions.payload` of `stock/trend/market_trend_wallet_v054.sqlite3` (symbol, tf, entry_at, entry)
Rules:
* Bars: `bars` rows of the position's frame whose candle END is at or before the BUY time (a candle still forming at the BUY is never used), source not empty, newest 140.
* `entry_vs_ma` uses the entry price of the sidecar (the planned entry of the BUY), else the stored entry. Slope = MA_n of the newest completed bar / MA_n one bar earlier - 1.
* Trend = `market_regime_v0290.classify` (needs 65 bars). Market score is NOT recorded: `{"score": None, "basis": "NOT_RECORDED_AT_BUY_BACKFILL"}` (it is real time and was not stored).
* An existing context is replaced only when the new one rests on MORE completed bars (the weekend candle audit may have added bars); a context with the same or more bars is kept as it is. The BUY-time `rr` of
  the sidecar (`rr_size`) is copied, never recomputed. Nothing else in the sidecar changes. The context carries `backfill = {version, at, source}`.
* Missing evidence stays None with a reason; nothing is guessed. A position without a sidecar row, without an entry time or with no bars is listed, not skipped silently.
No network, no order, no fill, no price. A locked database is retried in the next round.
Result: `stock/entry_ctx_backfill_v0600.json` (per source: scanned / already complete / filled / improved / no sidecar / no entry time / no bars / still partial, with the list of every unresolved
position id and its reason) and `stock/entry_ctx_backfill_v0600.jsonl` (one line per written position).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading
from pathlib import Path

VERSION = "ENTRY_CTX_BACKFILL_V0600"
KST = dt.timezone(dt.timedelta(hours=9))
BAR_LIMIT = 140
FULL_BARS = 121                      # MA120 and its slope need 121 completed closes
STATE_FILE = "entry_ctx_backfill_v0600.json"
LOG_FILE = "entry_ctx_backfill_v0600.jsonl"
MA_DB = ("MA_EPOCH_V067", "STOCK_MA_ENGINE_V1", "ma_paper.sqlite3")
TREND_DB = ("trend", "market_trend_wallet_v054.sqlite3")
_LOCK = threading.Lock()


def entry_moment(value):
    """KST aware datetime from a 14-digit stamp or an ISO string, else None."""
    text = str(value or "").strip()
    digits = "".join(c for c in text if c.isdigit())
    try:
        if len(text) == 14 and text.isdigit():
            return dt.datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=KST)
        moment = dt.datetime.fromisoformat(text)
        return moment if moment.tzinfo else moment.replace(tzinfo=KST)
    except (ValueError, TypeError):
        if len(digits) == 14:
            try:
                return dt.datetime.strptime(digits, "%Y%m%d%H%M%S").replace(tzinfo=KST)
            except ValueError:
                return None
        return None


def closes_before(store, code, frame, moment):
    """Completed closes of the frame whose candle ended at or before `moment`, oldest first."""
    import rr_size_v0340 as rr
    rows = rr.stock_bars_before(store, code, frame, moment, limit=BAR_LIMIT + 5)
    return [r["close"] for r in rows if r.get("source") and r["end"] <= moment][-BAR_LIMIT:]


def build_ctx(entry, closes, sidecar_rr, now, score=None, source="LOCAL_COMPLETED_BARS", version=None):
    import entry_context_v0370 as ctx
    out = {"version": ctx.VERSION, "basis": "COMPLETED_BARS_BEFORE_BUY_BACKFILL"}
    out.update(ctx.ma_context(closes, entry))
    try:
        import market_regime_v0290 as regime
        label, metrics = regime.classify(closes or [])
        out["trend"] = label or "UNVERIFIED"
        out["trend_bars"] = metrics.get("bars")
        out["trend_rule"] = "close>MA20>MA60 and MA20 above 5 bars ago = UP; mirror = DOWN; else SIDEWAYS; needs 65 bars"
    except Exception as exc:
        out["trend"] = "UNVERIFIED"
        out["trend_error"] = type(exc).__name__
    out["market_score"] = score if isinstance(score, dict) else {"score": None, "basis": "NOT_RECORDED_AT_BUY_BACKFILL"}
    if out["bars"] == 0:
        out["reason"] = "ENTRY_CTX_NO_COMPLETED_BARS"
    r = sidecar_rr if isinstance(sidecar_rr, dict) else {}
    out["rr"] = {"ratio": r.get("ratio"), "gain_pct": r.get("gain_pct"), "loss_pct": r.get("loss_pct"), "stop_source": r.get("stop_source"), "reason": r.get("reason")}
    out["backfill"] = {"version": version or VERSION, "at": now.isoformat(timespec="seconds"), "source": source}
    return out


STOCK_SCORE_DB = ("market_research_v033.sqlite3",)
STOCK_SCORE_SQL = "SELECT ts,score FROM score_events WHERE market='stock' ORDER BY ts"
COIN_SCORE_DB = ("coin", "ai", "AI_RESEARCH_V040.sqlite3")
COIN_SCORE_SQL = "SELECT score_ts,score FROM market_score_observations ORDER BY score_ts"
STOCK_SCORE_MAX_AGE = 20 * 3600     # the last score of the same trading day; a buy after the close (16:00-20:00) has no newer one
COIN_SCORE_MAX_AGE = 3600


class ScoreBook:
    """The market scores the app recorded (epoch seconds, score), read once per round. `at(moment)` = the last score at or before the BUY, with its age."""

    def __init__(self, path, sql, max_age):
        self.points, self.max_age = [], int(max_age)
        try:
            if Path(path).exists():
                with sqlite3.connect("file:%s?mode=ro" % Path(path).as_posix(), uri=True, timeout=5) as db:
                    self.points = sorted((int(float(t)), float(v)) for t, v in db.execute(sql).fetchall())
        except Exception:
            self.points = []
        self.times = [t for t, _ in self.points]

    def at(self, moment):
        import bisect
        epoch = int(moment.timestamp())
        index = bisect.bisect_right(self.times, epoch) - 1
        if index < 0:
            return None
        age = epoch - self.times[index]
        if age > self.max_age:
            return None
        return {"score": self.points[index][1], "at": dt.datetime.fromtimestamp(self.times[index], KST).isoformat(timespec="seconds"), "age_s": age,
                "basis": "LAST_RECORDED_BEFORE_BUY" if age > 0 else "RECORDED_AT_BUY"}


def score_fn_for(book):
    """score_fn(moment, sidecar): the score the strategy itself stored at the BUY (`market_score_at_buy`), else the last recorded one (with its age)."""
    def score(moment, sidecar):
        own = (sidecar or {}).get("market_score_at_buy")
        if isinstance(own, (int, float)) and not isinstance(own, bool):
            return {"score": float(own), "basis": "STORED_AT_BUY_BY_STRATEGY", "age_s": 0}
        return book.at(moment) if book is not None else None
    return score


def has_score(ctx):
    score = (ctx or {}).get("market_score") if isinstance(ctx, dict) else None
    return isinstance(score, dict) and score.get("score") is not None


def better(old, new):
    """Replace only when there is no context yet, the new one rests on MORE completed bars, or it adds a market score the old one does not have (never fewer bars)."""
    if not isinstance(old, dict):
        return True
    try:
        if int(new.get("bars") or 0) > int(old.get("bars") or 0):
            return True
        return has_score(new) and not has_score(old) and int(new.get("bars") or 0) >= int(old.get("bars") or 0)
    except (TypeError, ValueError):
        return True


def _connect(path):
    cx = sqlite3.connect(str(path), timeout=5)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=5000")
    return cx


def _blank():
    return {"scanned": 0, "already_complete": 0, "filled": 0, "improved": 0, "no_sidecar": 0, "no_entry_time": 0, "no_bars": 0, "still_partial": 0, "locked": 0, "unresolved": []}


def _process(source, rows, store, write, now, root, report, closes_fn=None, score_fn=None, skip_fn=None, source_label="LOCAL_COMPLETED_BARS", version=None, log_fn=None, rr_fn=None):
    """rows: dicts {id, code, frame, entry, at, sidecar (str or None)}.

    closes_fn(code, frame, moment) -> completed closes (default: the local bars); score_fn(moment, sidecar) -> market score dict or None;
    skip_fn(old_ctx) -> True when the stored context must not be fetched again (a network source asks only once per position).
    """
    for row in rows:
        report["scanned"] += 1
        pid, code = row["id"], str(row["code"])
        if row["sidecar"] is None:
            report["no_sidecar"] += 1
            report["unresolved"].append({"id": pid, "code": code, "reason": "NO_SIDECAR_ROW"})
            continue
        try:
            sidecar = json.loads(row["sidecar"] or "{}")
        except ValueError:
            report["unresolved"].append({"id": pid, "code": code, "reason": "SIDECAR_JSON_INVALID"})
            continue
        moment = entry_moment(row["at"])
        if moment is None:
            report["no_entry_time"] += 1
            report["unresolved"].append({"id": pid, "code": code, "reason": "NO_ENTRY_TIME"})
            continue
        entry = sidecar.get("entry") if sidecar.get("entry") not in (None, "") else row["entry"]
        old = sidecar.get("entry_ctx")
        if skip_fn is not None and skip_fn(old):
            report["already_complete"] += 1
            if isinstance(old, dict) and int(old.get("bars") or 0) < FULL_BARS:
                report["still_partial"] += 1
                report["unresolved"].append({"id": pid, "code": code, "reason": "PARTIAL_%d_OF_%d_BARS" % (int(old.get("bars") or 0), FULL_BARS)})
            continue
        try:
            closes = closes_fn(code, str(row["frame"]), moment) if closes_fn is not None else closes_before(store, code, str(row["frame"]), moment)
        except Exception as exc:
            report["errors"] = report.get("errors", 0) + 1
            report["unresolved"].append({"id": pid, "code": code, "reason": "BARS_UNAVAILABLE_" + type(exc).__name__ + (":" + str(exc)[:40] if str(exc) else "")})
            continue
        score = score_fn(moment, sidecar) if score_fn is not None else None
        rr = sidecar.get("rr_size") or ((old or {}).get("rr") if isinstance(old, dict) and (old.get("rr") or {}).get("ratio") is not None else None)
        if rr is None and rr_fn is not None:
            rr = rr_fn(sidecar, entry)
        new = build_ctx(entry, closes, rr, now, score=score, source=source_label, version=version)
        if not better(old, new):
            report["already_complete"] += 1
            if int(old.get("bars") or 0) < FULL_BARS:
                report["still_partial"] += 1
                report["unresolved"].append({"id": pid, "code": code, "reason": "PARTIAL_%d_OF_%d_BARS" % (int(old.get("bars") or 0), FULL_BARS)})
            continue
        if new["bars"] == 0:
            report["no_bars"] += 1
            report["unresolved"].append({"id": pid, "code": code, "reason": "NO_COMPLETED_BARS_BEFORE_BUY"})
            continue
        sidecar["entry_ctx"] = new
        try:
            write(pid, json.dumps(sidecar, ensure_ascii=False, separators=(",", ":")))
        except sqlite3.OperationalError:
            report["locked"] += 1
            continue
        report["improved" if isinstance(old, dict) else "filled"] += 1
        if new["bars"] < FULL_BARS:
            report["still_partial"] += 1
            report["unresolved"].append({"id": pid, "code": code, "reason": "PARTIAL_%d_OF_%d_BARS" % (new["bars"], FULL_BARS)})
        (log_fn or log)(root, {"at": now.isoformat(timespec="seconds"), "source": source, "position_id": pid, "code": code, "frame": row["frame"], "bars": new["bars"],
                   "trend": new.get("trend"), "ma_vs_entry": {k: v.get("entry_vs_ma") for k, v in new["ma"].items()}, "replaced_bars": (old or {}).get("bars") if isinstance(old, dict) else None})


def log(root, event):
    try:
        path = Path(root) / "stock" / LOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def save(root, payload):
    try:
        path = Path(root) / "stock" / STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), "utf8")
        os.replace(tmp, path)
    except Exception:
        pass


def _at_rows(store):
    with store.connect() as db:
        rows = db.execute("SELECT p.id,p.code,p.tf,p.entry_ts,p.entry_price,n.intent_json FROM positions p LEFT JOIN nl1118_reference_positions n ON n.position_id=p.id ORDER BY p.id").fetchall()
    return [{"id": r[0], "code": r[1], "frame": r[2], "at": r[3], "entry": r[4], "sidecar": r[5]} for r in rows]


def run(runtime, now=None):
    """Complete the BUY-time context of all stock positions. Returns the report dict; never raises."""
    now = now or dt.datetime.now(KST)
    report = {"version": VERSION, "at": now.isoformat(timespec="seconds"), "sources": {}}
    try:
        runner = getattr(runtime, "stock_runner", None)
        root = getattr(runtime, "root", None)
        store = getattr(runner, "store", None)
        if not root or store is None:
            return report
        book = ScoreBook(Path(root, *STOCK_SCORE_DB), STOCK_SCORE_SQL, STOCK_SCORE_MAX_AGE)
        score_fn = score_fn_for(book)
        with _LOCK:
            # ---- AT
            part = report["sources"]["AT"] = _blank()
            try:
                def write_at(pid, text):
                    with store.lock, store.connect() as db:
                        db.execute("UPDATE nl1118_reference_positions SET intent_json=?,updated_at=? WHERE position_id=?", (text, now.isoformat(timespec="seconds"), pid))
                _process("AT", _at_rows(store), store, write_at, now, root, part, score_fn=score_fn)
            except sqlite3.Error as exc:
                part["error"] = type(exc).__name__
            # ---- MA
            part = report["sources"]["MA"] = _blank()
            path = Path(root, "stock", *MA_DB)
            if path.exists():
                try:
                    with _connect(path) as db:
                        rows = db.execute("SELECT p.id,p.code,p.tf,p.entry_at,p.entry,v.state_json FROM positions p LEFT JOIN ma_v067_positions v ON v.position_id=p.id ORDER BY p.id").fetchall()

                    def write_ma(pid, text):
                        with _connect(path) as db:
                            db.execute("UPDATE ma_v067_positions SET state_json=?,updated_at=? WHERE position_id=?", (text, now.isoformat(timespec="seconds"), pid))
                    _process("MA", [{"id": r[0], "code": r[1], "frame": r[2], "at": r[3], "entry": r[4], "sidecar": r[5]} for r in rows], store, write_ma, now, root, part, score_fn=score_fn)
                except sqlite3.Error as exc:
                    part["error"] = type(exc).__name__
            # ---- MarketTrend
            part = report["sources"]["TREND"] = _blank()
            path = Path(root, "stock", *TREND_DB)
            if path.exists():
                try:
                    with _connect(path) as db:
                        rows = db.execute("SELECT id,symbol,tf,entry_at,entry,payload FROM positions ORDER BY id").fetchall()

                    def write_trend(pid, text):
                        with _connect(path) as db:
                            db.execute("UPDATE positions SET payload=?,updated_at=? WHERE id=?", (text, now.isoformat(timespec="seconds"), pid))
                    _process("TREND", [{"id": r[0], "code": r[1], "frame": r[2], "at": r[3], "entry": r[4], "sidecar": r[5]} for r in rows], store, write_trend, now, root, part, score_fn=score_fn)
                except sqlite3.Error as exc:
                    part["error"] = type(exc).__name__
        save(root, report)
    except Exception as exc:
        report["error"] = (type(exc).__name__ + ":" + str(exc))[:200]
    return report
