"""ARTITRADE v0.7.37: offline, read-only strategy research diagnosis.

No trading actions, internet calls, model-generated claims or ledger writes.
"AI analysis" is evidence-aware rule discovery + chronological holdout, NOT
a causal proof. Signals before entry are never mixed with post-entry outcomes.
"""
import bisect
import datetime as dt
import json
import math
import random
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

FRAMES = ("5m", "30m", "60m", "day")
LABELS = {"5m": "5분봉", "30m": "30분봉", "60m": "1시간봉", "day": "일봉"}
SOURCE = {
    ("stock", "at"): ("stock/ARTITRADE_D.sqlite3", "candidate_snapshots"),
    ("stock", "ma"): ("stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3", "candidates"),
    ("stock", "close"): ("stock/close/AT_CLOSE_PAPER.sqlite3", "signals"),
    ("stock", "gmo"): ("stock/PROJECTGMO_V1.0_research.sqlite3", "gmo_candidates"),
    ("coin", "general"): ("coin/base/upbit_paper.sqlite3", "candidates"),
    ("coin", "ma"): ("coin/ma/upbit_paper.sqlite3", "candidates"),
}
# All of these are entry-time features, never future highs or final performance.
GATES = (
    ("market_score", "<=", -20., "진입 당시 시장점수 -20점 이하"),
    ("atr_pct", ">=", 3., "진입 당시 ATR/가격 3% 이상"),
    ("volume_ratio", ">=", 2.5, "진입 당시 상대거래량 2.5배 이상"),
    ("spread_pct", ">=", .5, "진입 당시 호가 스프레드 0.5% 이상"),
    ("gap_pct", ">=", 4., "진입 당시 갭 상승 4% 이상"),
    ("rsi", ">=", 78., "진입 당시 RSI 78 이상"),
    ("score", "<=", 30., "진입점수 30점 이하"),
    ("bid_imbalance", "<=", .35, "진입 당시 매수호가 비중 35% 이하"),
)
ALIASES = {
    "score": ("score", "diverse_11_score", "gmo_score_at_signal", "signal_score"),
    "atr_pct": ("atr_pct", "atr_percent", "atr_ratio_pct", "atr_to_price_pct"),
    "atr": ("atr", "atr14", "atr_14", "atr_value"),
    "volume_ratio": ("volume_ratio", "vol_ratio", "relative_volume", "rvol", "volume_multiple"),
    "spread_pct": ("spread_pct", "bid_ask_spread_pct", "spread_percent"),
    "gap_pct": ("gap_pct", "gap_percent", "open_gap_pct"),
    "rsi": ("rsi", "rsi14", "rsi_14"),
    "market_score": ("market_score", "market_regime_score", "market_condition_score"),
    "bid_imbalance": ("bid_imbalance", "buy_orderbook_ratio", "orderbook_bid_ratio"),
}
EXCLUDED_FEATURE_PREFIX = ("future_", "forward_", "observed_", "after_", "post_", "exit_")
ROWS_LIMIT = 50000
FILL_LIMIT = 150000


def _num(value):
    try:
        if value is None or isinstance(value, bool): return None
        v = float(str(value).replace(",", "").replace("%", "").strip())
        return v if math.isfinite(v) else None
    except (TypeError, ValueError): return None


def _time(value):
    if value is None: return None
    if isinstance(value, (int, float)) and value > 1000000000:
        return float(value) / (1000 if value > 100000000000 else 1)
    s = str(value).strip()
    if not s: return None
    try:
        if len(s) >= 14 and s[:14].isdigit():
            t = dt.datetime.strptime(s[:14], "%Y%m%d%H%M%S")
            return t.replace(tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp()
        if len(s) >= 10 and s[:10].count("-") == 2:
            t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            if t.tzinfo is None: t = t.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
            return t.timestamp()
    except (ValueError, OverflowError): pass
    return None


def _frame(value):
    s = str(value or "").strip().lower()
    if s in ("5", "5m", "5min", "5분"): return "5m"
    if s in ("30", "30m", "30min", "30분"): return "30m"
    if s in ("60", "60m", "60min", "1h", "1시간"): return "60m"
    if s in ("d", "1d", "day", "daily", "일봉"): return "day"
    return ""


def _read_table(conn, name, limit):
    if not conn or name not in ("candidate_snapshots", "candidates", "signals",
                                "gmo_candidates", "positions", "executions"):
        return [], 0
    table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    if table is None: return [], 0
    count = int(conn.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0])
    rows = [dict(x) for x in conn.execute(
        'SELECT * FROM "' + name + '" ORDER BY rowid DESC LIMIT ?', (limit,))]
    return rows, count


def _read_source(root, market, strategy):
    path, table = SOURCE[market, strategy]
    full = Path(root) / path
    data = {"source": "SYNC_SNAPSHOT", "candidates": [], "positions": [],
            "executions": [], "total": 0, "truncated": False, "errors": []}
    if not full.is_file(): return data
    try:
        with sqlite3.connect(full.resolve().as_uri() + "?mode=ro", uri=True, timeout=4) as conn:
            conn.row_factory = sqlite3.Row
            data["candidates"], count = _read_table(conn, table, ROWS_LIMIT)
            data["positions"], _ = _read_table(conn, "positions", ROWS_LIMIT)
            data["executions"], e_count = _read_table(conn, "executions", FILL_LIMIT)
            data["total"] = count
            data["truncated"] = count > ROWS_LIMIT or e_count > FILL_LIMIT
            data["source"] = "EXE_SQLITE_READONLY"
    except (OSError, sqlite3.Error, ValueError) as exc:
        data["errors"].append("원본 SQLite 조회 실패: " + type(exc).__name__)
    return data


def _parse_json(value):
    if isinstance(value, dict): return value
    if isinstance(value, str):
        try:
            x = json.loads(value)
            return x if isinstance(x, dict) else {}
        except (ValueError, TypeError): pass
    return {}


def _entry_features(row, entry_price, market_score=None):
    # A strict allow-list prevents target, exit, post-entry candle and P&L leakage.
    raw = dict(row)
    for field in ("features", "feature_json", "reason_json", "indicators", "signal_features"):
        obj = _parse_json(row.get(field))
        raw.update({k: v for k, v in obj.items() if k not in raw})
    lower = {str(k).lower(): v for k, v in raw.items()
             if not str(k).lower().startswith(EXCLUDED_FEATURE_PREFIX)}
    out = {}
    for name, keys in ALIASES.items():
        for k in keys:
            v = _num(lower.get(k))
            if v is not None:
                out[name] = v
                break
    if "atr_pct" not in out and entry_price and entry_price > 0 and "atr" in out:
        out["atr_pct"] = 100 * out["atr"] / entry_price
    # Market-score source is anchored at the signal timestamp, not report time.
    if market_score is not None: out["market_score"] = market_score
    # Never treat a 0-100 % ratio as 0-1; only documented <=1 ratios convert.
    if "bid_imbalance" in out and out["bid_imbalance"] > 1:
        out["bid_imbalance"] /= 100
    return {k: v for k, v in out.items()
            if k in {z[0] for z in GATES} and math.isfinite(v)}


def _market_events(root, market):
    db = Path(root) / "market_research_v033.sqlite3"
    if not db.is_file(): return [], []
    try:
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True, timeout=3) as conn:
            pairs = sorted((int(row[0]), float(row[1])) for row in conn.execute(
                "SELECT ts,score FROM score_events WHERE market=? ORDER BY ts", (market,)))
        return [x[0] for x in pairs], [x[1] for x in pairs]
    except (sqlite3.Error, ValueError, OSError):
        return [], []


def _point_in_time_market(stamps, scores, when, market):
    if when is None or not stamps: return None
    i = bisect.bisect_right(stamps, int(when)) - 1
    if i < 0 or int(when) - stamps[i] > (3600 if market == "stock" else 900):
        return None
    return scores[i]


def _identity(row, kind, market, strategy):
    if strategy == "close":
        v = row.get("signal_id") if kind == "position" else row.get("id")
        return ("signal", str(v)) if v is not None else None
    if strategy == "ma" and market == "stock":
        v = row.get("candidate_id") if kind == "position" else row.get("id")
        return ("candidate", str(v)) if v is not None else None
    if strategy == "gmo":
        return ("gmo", str(row.get("code") or ""), str(row.get("entry_date") or row.get("event_date") or "")[:10])
    frame = _frame(row.get("tf") or row.get("frame"))
    code = str(row.get("code") or row.get("market") or "")
    ref = str(row.get("ref_ts") or row.get("ref_at") or "")
    return ("combo", frame, code, ref) if code and ref else None


def _pnl_return(position, fills):
    # Strictly completed positions; open/PENDING positions cannot be declared failures.
    status = str(position.get("status") or "").upper()
    if status not in ("CLOSED", "DONE", "SOLD", "FILLED_EXIT"): return None, "OPEN"
    net = next((_num(position.get(k)) for k in
                ("realized_net_krw", "realized_net", "net_pnl", "net_profit",
                 "realized_pnl_net", "realized_profit_net")
                if _num(position.get(k)) is not None), None)
    if net is None:
        sells = [x for x in fills if "SELL" in str(x.get("side") or "").upper()]
        amounts = []
        for x in sells:
            amount = next((_num(x.get(k)) for k in
                           ("net_realized", "net_profit", "net_pnl",
                            "realized_net", "profit_after_fee")
                           if _num(x.get(k)) is not None), None)
            if amount is None: return None, "NET_SELL_MISSING"
            amounts.append(amount)
        if amounts: net = sum(amounts)
    if net is None: return None, "NET_PNL_MISSING"
    direct = next((_num(position.get(k)) for k in
                   ("net_return_pct", "return_net_pct", "net_profit_pct")
                   if _num(position.get(k)) is not None), None)
    if direct is not None: return direct, "VERIFIED_NET_RETURN"
    capital = next((_num(position.get(k)) for k in
                    ("entry_notional", "initial_cost", "entry_value",
                     "invested_amount", "cost_basis", "total_buy_value")
                    if _num(position.get(k)) is not None and _num(position.get(k)) > 0), None)
    if capital is None:
        buys = [x for x in fills if "BUY" in str(x.get("side") or "").upper()]
        costs = [(_num(x.get("price")), _num(x.get("qty") or x.get("quantity")))
                 for x in buys]
        if costs and all(p and q and p > 0 and q > 0 for p, q in costs):
            capital = sum(p*q for p, q in costs)
    if capital is None:
        price = _num(position.get("entry_price") or position.get("entry"))
        qty = next((_num(position.get(k)) for k in
                    ("initial_qty", "buy_qty", "entry_qty", "filled_buy_qty")
                    if _num(position.get(k)) is not None and _num(position.get(k)) > 0), None)
        if price and qty and price > 0: capital = price * qty
    if capital is None or capital <= 0: return None, "COST_BASIS_MISSING"
    return 100.0 * net / capital, "VERIFIED_NET_PNL_COST"


def _gate(row, gate):
    feature, op, threshold, _ = gate
    value = row["features"].get(feature)
    if value is None: return None
    return value <= threshold if op == "<=" else value >= threshold


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _date(row):
    when = _time(row.get("at"))
    if when is None: return "unknown"
    return dt.datetime.fromtimestamp(when, dt.timezone(dt.timedelta(hours=9))).date().isoformat()


def _cluster_ci(rows, gate, repetitions=400):
    by_day = defaultdict(list)
    for x in rows: by_day[_date(x)].append(x)
    days = sorted(by_day)
    if len(days) < 5: return None
    rnd = random.Random(37037)
    changes = []
    for _ in range(repetitions):
        chosen = [row for day in (rnd.choice(days) for _ in days)
                  for row in by_day[day]]
        changes.append(sum(-r["return_pct"] for r in chosen if _gate(r, gate)) / len(chosen))
    changes.sort()
    return [round(changes[9], 3), round(changes[390], 3)]


def _select_gate(train):
    candidates = []
    for gate in GATES:
        available = [r for r in train if _gate(r, gate) is not None]
        risk = [r for r in available if _gate(r, gate)]
        safe = [r for r in available if not _gate(r, gate)]
        if len(available) < 24 or len(risk) < 8 or len(safe) < 8: continue
        risk_avg = _mean([r["return_pct"] for r in risk])
        safe_avg = _mean([r["return_pct"] for r in safe])
        if risk_avg >= safe_avg - .2: continue
        # Avoiding historically negative trades, not a hypothetical rewritten exit.
        delta = -sum(x["return_pct"] for x in risk) / len(train)
        if delta > 0:
            candidates.append((delta, gate, len(risk), risk_avg, safe_avg, len(available)))
    return max(candidates, key=lambda x: x[0]) if candidates else None


def _summarize_frame(frame, candidates, trades, source, truncated, error_notes):
    f_cand = [c for c in candidates if c["frame"] == frame]
    f_trades = [x for x in trades if x["frame"] == frame]
    valid = sorted([r for r in f_trades if r["return_pct"] is not None],
                   key=lambda x: (x["timestamp"] if x["timestamp"] is not None else -1, x["key"]))
    missing = Counter(x["pnl_state"] for x in f_trades if x["return_pct"] is None)
    base = {
        "frame": frame, "label": LABELS[frame], "signals": len(f_cand),
        "entries": len(f_trades), "evaluated": len(valid),
        "pending": sum(1 for x in f_trades if x["pnl_state"] == "OPEN"),
        "missing_net": sum(missing.values()) - missing.get("OPEN", 0),
        "source": source, "truncated": truncated,
        "status": "INSUFFICIENT", "patterns": [], "causes": [],
        "improvement": "원본 기록 보강 후 진입조건을 검증할 것",
        "quantitative": None, "coverage": {},
        "warnings": list(error_notes),
    }
    if not f_cand and not f_trades:
        base["causes"] = ["해당 시간봉의 연구 기록이 없음. 손익 개선 효과 산출 불가."]
        return base
    if f_trades and not valid:
        base["causes"] = ["손실 원인을 판단하기 위한 종료 체결의 순손익·원금 정보가 부족함.",
                          "가격 하락 또는 목표 고가 관측을 손실 원인·매도 체결로 취급하지 않음."]
        base["warnings"].append("원본 매도 체결 순손익 및 매수원금(또는 순수익률) 기록 필요")
        return base
    for name, _, _, label in GATES:
        present = sum(name in x["features"] for x in valid)
        base["coverage"][name] = {"n": present, "pct": round(100*present/len(valid), 1)}
    base["patterns"].append(
        "실제 진입 {}건 / 순손익 검증 {}건 / 미종료 {}건 · 순수익률 평균 {:+.2f}%".format(
            len(f_trades), len(valid), base["pending"], _mean([r["return_pct"] for r in valid])))
    if len(valid) < 40:
        base["causes"] = ["검증 가능한 종료거래 40건 미만: 원인 후보를 추정할 표본이 부족함.",
                          "ATR·거래량·시장점수는 진입 당시 증거가 기록된 경우에만 분석함."]
        base["warnings"].append("최소 40건, 별도 최근 검증 12건 확보 전 정량효과 확정 금지")
        return base
    train_n = max(28, int(len(valid) * .7))
    train, holdout = valid[:train_n], valid[train_n:]
    if len(holdout) < 12:
        base["warnings"].append("최근 검증구간 12건 미만")
        return base
    selected = _select_gate(train)
    if selected is None:
        base["causes"] = ["진입 당시 지표에서 반복적으로 손실을 구별하는 유의한 조건을 찾지 못함.",
                          "실제 매도사유·호가 흐름·뉴스 촉발요인 자료를 추가 확보할 필요가 있음."]
        base["improvement"] = "현행 매수 기준 유지 · 사후 급락을 원인으로 단정하지 말고 선행 데이터 확보"
        return base
    _, gate, bad_n, risk_avg, safe_avg, observed = selected
    selected_feature = gate[0]
    eligible_val = [x for x in holdout if _gate(x, gate) is not None]
    excluded = [x for x in holdout if _gate(x, gate)]
    applied = len(eligible_val) == len(holdout) and len(excluded) >= 3
    # Missing feature values remain traded; no retroactive filtering or invented value.
    baseline = _mean([x["return_pct"] for x in holdout])
    revised = sum(x["return_pct"] for x in holdout if not _gate(x, gate)) / len(holdout)
    delta = revised - baseline
    ci = _cluster_ci(holdout, gate) if applied else None
    confirmed = bool(ci and ci[0] > 0 and delta > 0)
    gate_text = gate[3]
    base["patterns"].append(
        "{}: 학습구간 관측 {}/{}건, 위험군 {}건 평균 {:+.2f}%, 비교군 {:+.2f}%".format(
            gate_text, observed, len(train), bad_n, risk_avg, safe_avg))
    base["causes"] = [
        "진입 이전 조건 '{}'과 후속 손익의 연관성 확인. 주가 하락·거래량 급등 자체는 원인으로 간주하지 않음.".format(gate_text),
        "가격 충격의 근본 촉발원인(뉴스·대량 매도 주체·호가 소진)은 관련 시계열이 없으면 확정할 수 없음.",
        "진입 판단 → 시장/변동성 환경 → 실제 매도사유 → 순손익 순서로 추가 교차검증 필요.",
    ]
    base["improvement"] = ("{} 조건에서 신규 매수를 보류하는 필터를 PAPER 전용으로 비교."
                            " 매도·익절 기준과 실거래 설정은 변경하지 않음.").format(gate_text)
    base["status"] = "VALIDATED_CANDIDATE" if confirmed else "HYPOTHESIS"
    base["quantitative"] = {
        "method": "과거 실제 종료거래 70% 학습/최근 30% 검증 · 미매수 시 수익률 0% · 동일 기회당 고정 투자단위",
        "train_n": len(train), "test_n": len(holdout), "screened_n": len(excluded),
        "unavailable_test_n": len(holdout) - len(eligible_val),
        "test_period": [_date(holdout[0]), _date(holdout[-1])],
        "baseline_pct": round(baseline, 3), "screened_pct": round(revised, 3),
        "delta_pp": round(delta, 3), "delta_ci95_pp": ci,
        "baseline_win_pct": round(100 * sum(x["return_pct"] > 0 for x in holdout)/len(holdout), 1),
        "screened_win_pct": round(100 * sum(x["return_pct"] > 0 for x in holdout if not _gate(x, gate))/
                                  max(1, sum(not _gate(x, gate) for x in holdout)), 1),
        "validated": confirmed, "screened": selected_feature,
    }
    if not confirmed:
        base["warnings"].append("최근 표본 재현 또는 날짜별 95% 구간 확인 실패: 예상 수익 개선 확정 불가")
    else:
        base["warnings"].append("95% 구간은 날짜별 재표본화 추정이며 미래 수익·인과관계를 보장하지 않음")
    if len(eligible_val) != len(holdout):
        base["warnings"].append("최근 구간 일부 진입지표 누락: 미기록 건은 원래대로 진입 처리")
    return base


def _combined_dict(row, other):
    merged = dict(row)
    for key, value in other.items():
        if key not in merged or merged[key] is None:
            merged[key] = value
    return merged


def _build_samples(market, strategy, raw, research_json, ledger_json, events):
    research = _parse_json(research_json)
    ledger = _parse_json(ledger_json)
    snap = [r for r in research.get("items", []) if isinstance(r, dict)]
    source_candidates = raw["candidates"] or snap
    source_positions = raw["positions"] or [
        p for p in ledger.get("positions", []) if isinstance(p, dict)]
    source_exec = raw["executions"] or [
        e for e in ledger.get("executions", []) if isinstance(e, dict)]
    if not source_candidates and snap: source_candidates = snap
    # Snapshot may be rich in outcome/entry evidence absent from raw candidate rows.
    snap_by_key = {}
    for item in snap:
        key = _identity(item, "candidate", market, strategy)
        if key is not None: snap_by_key[key] = item
    candidates, lookup = [], {}
    stamps, scores = events
    for idx, candidate in enumerate(source_candidates):
        if not isinstance(candidate, dict): continue
        key = _identity(candidate, "candidate", market, strategy)
        if key is None: key = ("research", str(idx))
        row = _combined_dict(candidate, snap_by_key.get(key, {}))
        frame = _frame(row.get("tf") or row.get("frame"))
        if not frame and strategy in ("close", "gmo"): frame = "day"
        when = _time(row.get("at") or row.get("signal_ts") or row.get("created_at") or
                     row.get("ts") or row.get("event_date") or row.get("ref_ts") or row.get("ref_at"))
        entry_price = _num(row.get("limit_price") or row.get("entry_price") or row.get("entry"))
        score = _point_in_time_market(stamps, scores, when, market)
        info = {"key": str(key), "frame": frame, "timestamp": when,
                "at": row.get("at") or row.get("signal_ts") or row.get("created_at") or
                      row.get("ts") or row.get("ref_ts") or row.get("ref_at") or row.get("event_date"),
                "features": _entry_features(row, entry_price, score), "raw": row}
        candidates.append(info)
        lookup.setdefault(key, info)
    fills_by_position = defaultdict(list)
    for fill in source_exec:
        pid = fill.get("position_id")
        if pid is not None: fills_by_position[str(pid)].append(fill)
    trades = []
    for position in source_positions:
        if not isinstance(position, dict): continue
        key = _identity(position, "position", market, strategy)
        candidate = lookup.get(key)
        if candidate is None: continue  # Do not assign a different position's features.
        frame = candidate["frame"]
        value, state = _pnl_return(position, fills_by_position.get(str(position.get("id")), []))
        trades.append({"key": candidate["key"], "frame": frame,
                       "timestamp": candidate["timestamp"], "at": candidate["at"],
                       "features": candidate["features"], "return_pct": value,
                       "pnl_state": state})
    # If original DB is absent, do not upgrade research virtual "HIT" to a fill.
    # Every quantified outcome must still be linked to a closed actual position.
    return candidates, trades


def analyze(root, market, strategy, research_json="{}", ledger_json="{}"):
    """Returns JSON for the Android native screen; never edits a strategy or source."""
    if (market, strategy) not in SOURCE:
        return json.dumps({"ok": False, "message": "지원하지 않는 시장/전략"}, ensure_ascii=False)
    raw = _read_source(root, market, strategy)
    stamps = _market_events(root, market)
    candidates, trades = _build_samples(market, strategy, raw, research_json,
                                         ledger_json, stamps)
    frames = [_summarize_frame(frame, candidates, trades, raw["source"],
                               raw["truncated"], raw["errors"]) for frame in FRAMES]
    missing = sum(1 for x in frames if x["evaluated"] < 40)
    return json.dumps({
        "ok": True, "market": market, "strategy": strategy,
        "schema": "ARTITRADE_AI_ANALYSIS_v037",
        "title": ("주식" if market == "stock" else "코인") + " · " +
                 {"at": "기준봉", "general": "기준봉", "ma": "MA",
                  "close": "종가베팅", "gmo": "수급매매"}[strategy],
        "generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(timespec="seconds"),
        "source": raw["source"], "source_total": raw["total"],
        "frames": frames, "weak_frames": missing,
        "method_note": "원인 가설은 진입 전 기록과 실제 종료손익을 시계열로 대조. 확정적 인과관계는 주장하지 않음.",
        "effect_note": "정량 개선폭은 거래 건너뛰기 가정의 과거 검증구간 재현값(퍼센트포인트/진입기회)이며 미래 성과 예측·자동매매 적용값이 아님.",
        "data_note": "원본 SQLite가 없으면 수신 스냅샷으로만 분석하며, 순손익·매수원금이 없는 거래는 정량효과에서 제외함.",
    }, ensure_ascii=False)
