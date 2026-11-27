"""0.5.4 (#169): the reward / risk of a position AT ITS BUY is shown in the execution history.

Owner 2026-11-27: "매수할 때 손익비 원장에 기록해두고 있어? ... 체결이력에서도 매수한 시점 기준으로 손익비가 어떻게 되었던 종목인지 표시되게해".

The BUY-time value is ALREADY recorded since 0.3.4 / 0.3.7 for AT (reference bar), MA and MarketTrend, coin and stock, in each strategy's own
per-position sidecar (`rr_size` = ratio / gain to the first target / loss to the stop / stop source; 0.3.7 `entry_ctx.rr` the same). Supply-demand
(GMO) has no stop at all, so it has no reward / risk and is excluded (as the owner asked in 0.3.7). What was missing is the DISPLAY: the hub rows did
not carry it. This module reads the sidecars READ-ONLY (never the trading gate) and adds `entry_rr` to the position and execution rows of the hub
snapshot, joined by the native position id. Nothing is written, nothing is estimated: a position without a recorded value simply has no `entry_rr`.

Definition (owner 0.3.4): ratio = gain to the FIRST target / loss to the stop, both from the entry at the BUY; "손익비 1 : ratio" (1 : 0.5 or less = the
stop is at least twice as far as the first target).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

VERSION = "ENTRY_RR_V0540"
CACHE_SECONDS = 20.0
# owner -> (database, table, id column, json column); the databases are the ones `position_uid_v015._DB_PATHS` already names
SOURCES = {
    "stock/at": ("stock/ARTITRADE_D.sqlite3", "nl1118_reference_positions", "position_id", "intent_json"),
    "stock/ma": ("stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3", "ma_v067_positions", "position_id", "state_json"),
    "stock/trend": ("stock/trend/market_trend_wallet_v054.sqlite3", "positions", "id", "payload"),
    "coin/general": ("coin/base/upbit_paper.sqlite3", "nl1118_reference_positions", "position_id", "intent_json"),
    "coin/ma": ("coin/ma_epoch_v067/upbit_paper.sqlite3", "ma_v067_positions", "position_id", "state_json"),
    "coin/trend": ("coin/trend/market_trend_wallet_v054.sqlite3", "positions", "id", "payload"),
}
_CACHE = {}
_LOCK = threading.Lock()


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def extract(payload):
    """{ratio, gain_pct, loss_pct, stop_source} from a sidecar JSON (0.3.7 `entry_ctx.rr` first, else 0.3.4 `rr_size`), or None."""
    if not isinstance(payload, dict):
        return None
    for source in ((payload.get("entry_ctx") or {}).get("rr") if isinstance(payload.get("entry_ctx"), dict) else None, payload.get("rr_size")):
        if isinstance(source, dict) and _num(source.get("ratio")) is not None:
            return {"ratio": _num(source.get("ratio")), "gain_pct": _num(source.get("gain_pct")),
                    "loss_pct": _num(source.get("loss_pct")), "stop_source": source.get("stop_source")}
    return None


def load(root, owner):
    """{position id (text): entry_rr} of one wallet. Read-only connection, cached for CACHE_SECONDS; never raises."""
    spec = SOURCES.get(owner)
    if spec is None or not root:
        return {}
    path = Path(root) / spec[0]
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(str(path))
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
    index = {}
    try:
        if path.exists():
            cx = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=2)
            try:
                cx.execute("PRAGMA query_only=ON")
                for ident, raw in cx.execute("SELECT %s, %s FROM %s" % (spec[2], spec[3], spec[1])):
                    try:
                        found = extract(json.loads(raw or "{}"))
                    except (TypeError, ValueError):
                        found = None
                    if found is not None:
                        index[str(ident)] = found
            finally:
                cx.close()
    except Exception:
        index = {}
    with _LOCK:
        _CACHE[str(path)] = (now, index)
    return index


def annotate_ledger(root, owner, ledger):
    """Adds `entry_rr` to the position and execution rows of one wallet. Returns how many rows got it."""
    index = load(root, owner)
    if not index or not isinstance(ledger, dict):
        return 0
    added = 0
    for key in ("positions", "executions", "fills"):
        for row in ledger.get(key) or ():
            if not isinstance(row, dict) or "entry_rr" in row:
                continue
            ident = row.get("position_id")
            if ident in (None, "", 0, "0") and key == "positions":
                ident = row.get("id")
            found = index.get(str(ident)) if ident not in (None, "", 0, "0") else None
            if found is not None:
                row["entry_rr"] = found
                added += 1
    return added


def annotate_packet(root, packet):
    """Called on the hub packet after the position ids were attached (`position_uid_v015`). Never raises."""
    try:
        from position_uid_v015 import _walk
        total = 0
        for owner, ledger in _walk(packet):
            total += annotate_ledger(root, owner, ledger)
        if isinstance(packet, dict):
            packet["entry_rr_v0540"] = {"schema": VERSION, "rows": total, "read_only": True}
    except Exception:
        pass
    return packet
