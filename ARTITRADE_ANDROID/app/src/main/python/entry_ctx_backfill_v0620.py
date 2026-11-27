"""0.6.2 (owner 2026-11-27 "왜 이렇게 누락된 데이터가 많아 ... 장중에 즉시 기록 안 해도 되는 것도 장 마감 후 철저히 기록"): the BUY-time context of every COIN position is completed in the quiet hours.

What was wrong (device export 2026-11-27, ledger workbook): the per-position sidecars of the coin strategies carry `rr_size` (46 / 45 / 9 of 61 / 79 / 23 positions) but NO `entry_ctx` (MA 5/10/20/60/120 above / below and slope, trend)
and only the MA / MarketTrend sidecars carry `market_score_at_buy`. The workbook therefore rebuilt trend and score afterwards from the AI research observation tables, which hold only 0-55 coin candles per frame
(47 of 79 coin MA and 20 of 23 coin MarketTrend positions showed "추세 미확인"). 0.6.0 completed this for stock positions only.

What this does (same rules as 0.6.0, source = the official Upbit candles, public read-only requests):
* For every coin position of the three owners (reference `coin/base`, MA `coin/ma_epoch_v067`, MarketTrend `coin/trend`) whose sidecar has no `entry_ctx` written by this backfill, ONE candle request (200 candles ending at the
  frame boundary at or before the BUY; `/v1/candles/minutes/{5,30,60}` or `/v1/candles/days`) gives the COMPLETED closes before the BUY. The newest 140 are used. A candle still forming at the BUY is never used.
* `entry_ctx` = MA 5/10/20/60/120 (value, slope %, entry above / below), trend (market_regime rule, needs 65 bars), the copy of the stored `rr_size`, and the market score: the value the strategy stored at the BUY
  (`market_score_at_buy`), else the last score the app recorded before the BUY with its age (`LAST_RECORDED_BEFORE_BUY`, at most 1 h for coins). Nothing is guessed; what cannot be found is None with a reason.
* A context written by this backfill is not requested again (candles of the past do not change); a context of the real-time recorder is replaced only when the backfill rests on MORE completed bars or adds the score.
* Pace: 0.5 s between requests plus the shared candle limiter of the Upbit client; yields to nothing else (it only reads); at most 400 requests per round; the quiet window only.
* Every position that could not be completed is listed with its reason in `coin/entry_ctx_backfill_v0620.json` (NO_SIDECAR_ROW, NO_ENTRY_TIME, NO_CANDLES_BEFORE_BUY, UPBIT_ERROR, PARTIAL_n_OF_121_BARS); one line per written position in `.jsonl`.
Only the sidecar changes. No order, no fill, no price, no wallet.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path

import entry_ctx_backfill_v0600 as base

VERSION = "ENTRY_CTX_BACKFILL_V0620"
KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc
PAUSE = 0.5
MAX_REQUESTS = 400
COUNT = 200
STATE_FILE = "entry_ctx_backfill_v0620.json"
LOG_FILE = "entry_ctx_backfill_v0620.jsonl"
STEP = {"5m": 300, "30m": 1800, "60m": 3600, "1h": 3600, "day": 86400}
PATH = {"5m": "/v1/candles/minutes/5", "30m": "/v1/candles/minutes/30", "60m": "/v1/candles/minutes/60", "1h": "/v1/candles/minutes/60", "day": "/v1/candles/days"}
OWNERS = {
    "BASE": ("coin", "base", "upbit_paper.sqlite3"),
    "MA": ("coin", "ma_epoch_v067", "upbit_paper.sqlite3"),
    "TREND": ("coin", "trend", "market_trend_wallet_v054.sqlite3"),
}


def boundary(moment, frame):
    """The frame boundary (UTC) at or before the BUY: candles that START before it have ENDED at or before the BUY."""
    step = STEP[frame]
    epoch = int(moment.timestamp())
    return dt.datetime.fromtimestamp(epoch - epoch % step, UTC)


def completed_closes(client, market, frame, moment, cache=None, sleep=time.sleep, counter=None):
    """Completed closes (oldest first, at most 140) of an Upbit market before the BUY. Raises on a request error."""
    if frame not in PATH:
        raise ValueError("FRAME_" + str(frame))
    end = boundary(moment, frame)
    key = (market, frame, end.isoformat())
    if cache is not None and key in cache:
        return cache[key]
    sleep(PAUSE)
    if counter is not None:
        counter[0] += 1
    rows = client.get(PATH[frame], {"market": market, "count": COUNT, "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
    closes = []
    for row in sorted((r for r in rows or [] if isinstance(r, dict)), key=lambda r: str(r.get("candle_date_time_utc") or "")):
        try:
            start = dt.datetime.strptime(str(row["candle_date_time_utc"])[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
            close = float(row["trade_price"])
        except (KeyError, ValueError, TypeError):
            continue
        if close > 0 and start + dt.timedelta(seconds=STEP[frame]) <= moment:
            closes.append(close)
    closes = closes[-base.BAR_LIMIT:]
    if cache is not None:
        cache[key] = closes
    return closes


def plan_rr(sidecar, entry):
    """BUY-time RR of a reference-bar (base) position that has no `rr_size`: the plan stored at the BUY (`entry`, `stop`, `first`) gives the same numbers rr_size recorded (checked on 46 recorded positions: ratio, gain and loss identical).

    Only the reference-bar owner stores its hard stop in the sidecar; MA and MarketTrend use a lookback low of the candles at the BUY (not stored): they stay without RR and say why.
    """
    try:
        e, stop, first = float(sidecar["entry"]), float(sidecar["stop"]), float(sidecar["first"])
    except (KeyError, TypeError, ValueError):
        return {"reason": "NO_RR_RECORDED_AT_BUY"}
    if not (0 < stop < e < first):
        return {"reason": "NO_RR_PLAN_INCONSISTENT"}
    gain, loss = (first - e) / e * 100.0, (e - stop) / e * 100.0
    return {"ratio": round(gain / loss, 4), "gain_pct": round(gain, 4), "loss_pct": round(loss, 4), "stop_source": "REFERENCE_HARD_STOP", "reason": "RECOMPUTED_FROM_STORED_PLAN"}


def rr_fn_for(owner):
    if owner == "BASE":
        return plan_rr
    return lambda sidecar, entry: {"reason": "NO_RR_RECORDED_AT_BUY"}


def skip_once(old):
    """A context written by this backfill is final: the candles of the past do not change."""
    return isinstance(old, dict) and (old.get("backfill") or {}).get("version") == VERSION


def _rows(db, owner):
    if owner == "BASE":
        found = db.execute("SELECT p.id,p.market,p.tf,p.entry_at,p.entry,n.intent_json FROM positions p LEFT JOIN nl1118_reference_positions n ON n.position_id=p.id ORDER BY p.id").fetchall()
    elif owner == "MA":
        found = db.execute("SELECT p.id,p.market,p.tf,p.entry_at,p.entry,v.state_json FROM positions p LEFT JOIN ma_v067_positions v ON v.position_id=p.id ORDER BY p.id").fetchall()
    else:
        found = db.execute("SELECT id,symbol,tf,entry_at,entry,payload FROM positions ORDER BY id").fetchall()
    return [{"id": r[0], "code": r[1], "frame": r[2], "at": r[3], "entry": r[4], "sidecar": r[5]} for r in found]


def _writer(path, owner, now):
    stamp = now.isoformat(timespec="seconds")

    def write(pid, text):
        with base._connect(path) as db:
            if owner == "BASE":
                db.execute("UPDATE nl1118_reference_positions SET intent_json=?,updated_at=? WHERE position_id=?", (text, stamp, pid))
            elif owner == "MA":
                db.execute("UPDATE ma_v067_positions SET state_json=?,updated_at=? WHERE position_id=?", (text, stamp, pid))
            else:
                db.execute("UPDATE positions SET payload=?,updated_at=? WHERE id=?", (text, stamp, pid))
    return write


def _log(root, event):
    try:
        path = Path(root) / "coin" / LOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def _save(root, payload):
    import os
    try:
        path = Path(root) / "coin" / STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), "utf8")
        os.replace(tmp, path)
    except Exception:
        pass


def run(runtime, now=None, client=None, sleep=time.sleep):
    """Complete the BUY-time context of all coin positions. Returns the report dict; never raises."""
    now = now or dt.datetime.now(KST)
    report = {"version": VERSION, "at": now.isoformat(timespec="seconds"), "sources": {}, "requests": 0}
    try:
        root = getattr(runtime, "root", None)
        if not root:
            return report
        if client is None:
            from d_upbit import UpbitPublicClient
            client = UpbitPublicClient(min_interval=PAUSE)
        book = base.ScoreBook(Path(root, *base.COIN_SCORE_DB), base.COIN_SCORE_SQL, base.COIN_SCORE_MAX_AGE)
        score_fn = base.score_fn_for(book)
        cache, counter = {}, [0]
        with base._LOCK:
            for owner, parts in OWNERS.items():
                part = report["sources"][owner] = base._blank()
                path = Path(root, *parts)
                if not path.exists():
                    continue
                if counter[0] >= MAX_REQUESTS:
                    part["deferred"] = True
                    continue
                try:
                    with base._connect(path) as db:
                        rows = _rows(db, owner)

                    def closes_fn(code, frame, moment, _client=client, _cache=cache, _counter=counter):
                        if _counter[0] >= MAX_REQUESTS:
                            raise RuntimeError("REQUEST_BUDGET_REACHED")
                        return completed_closes(_client, code, frame, moment, cache=_cache, sleep=sleep, counter=_counter)
                    base._process("COIN_" + owner, rows, None, _writer(path, owner, now), now, root, part, closes_fn=closes_fn, score_fn=score_fn,
                                  skip_fn=skip_once, source_label="UPBIT_PUBLIC_CANDLES", version=VERSION, log_fn=_log, rr_fn=rr_fn_for(owner))
                except Exception as exc:
                    part["error"] = (type(exc).__name__ + ":" + str(exc))[:160]
        report["requests"] = counter[0]
        _save(root, report)
    except Exception as exc:
        report["error"] = (type(exc).__name__ + ":" + str(exc))[:200]
    return report
