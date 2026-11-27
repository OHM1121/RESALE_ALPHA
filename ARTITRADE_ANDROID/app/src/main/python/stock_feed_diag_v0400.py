"""0.4.0 (issue #155): WHY the Kiwoom stock WebSocket keeps dropping - measurement only.

Evidence (stock error export 2026-11-27 10:27, app 0.3.6; 211 disconnect episodes in 8 days, median uptime 260 s,
89 `keepalive ping timeout (1011)` with ping_timeout=45 s, 16 `V071_STOCK_WS_QUEUE_BACKPRESSURE`, 21 DNS failures):
* TICK_PIPELINE_METRICS (2,317 rows): the single SQLite ledger gate (`TradePriorityRLock`) is held for >= 5 s in 400 rows
  and >= 20 s in 84 rows (maximum 210 s), `normal_max_wait_ms` >= 20 s in 390 rows. Longest holders:
  `artitrade-fix14-higher-frame-rest` 117 rows, `ARTITRADE_D_HISTORY_INDICATORS` 78 rows, anonymous `Dummy-N` executor threads.
* 34 of 304 disconnects (24 of 89 keepalive ones) follow a >= 10 s hold within the previous 2 minutes.
WHAT the holder is doing during such a hold is UNKNOWN (the gate only records the holder's thread name).

This overlay changes no behaviour. It (1) watches every `TradePriorityRLock` and, when one hold passes 5 s, writes the
holder's stack (top frames, once per hold, at most 100 lines) to `stock/db_gate_long_hold_v0400.jsonl`; (2) samples
Python-thread scheduling lag (a 0.25 s sleep's overshoot - CPU / GIL starvation); (3) appends a short diagnostic suffix
(gate hold/holder, feed queue, overflows, scheduling lag) to the existing 'disconnected' health detail text.
"""
from __future__ import annotations

import collections
import json
import sys
import threading
import time
import traceback
from pathlib import Path

VERSION = "STOCK_FEED_DIAG_V0400"
LONG_HOLD_SECONDS = 5.0
POLL_SECONDS = 1.0
MAX_LINES = 100
SAMPLE_FILE = "db_gate_long_hold_v0400.jsonl"
LAG_SLEEP = 0.25
LAG_WINDOW = 480            # 2 minutes of samples

_LOCK = threading.Lock()
_GATES = []                 # weakly not needed: one or two gates live for the whole process
_REPORTED = {}              # id(gate) -> started value already reported
_LINES = [0]
_LAG = collections.deque(maxlen=LAG_WINDOW)
_STARTED = [False]
_INSTALLED = [False]
_ROOT = [None]


def register(gate):
    with _LOCK:
        if not any(g is gate for g in _GATES):
            _GATES.append(gate)


def lag_stats():
    """(max_ms, p95_ms) of scheduling overshoot over the last ~2 minutes."""
    with _LOCK:
        values = sorted(_LAG)
    if not values:
        return 0.0, 0.0
    return round(values[-1], 1), round(values[min(len(values) - 1, int(len(values) * 0.95))], 1)


def _frames(ident, limit=8):
    frame = sys._current_frames().get(ident)
    if frame is None:
        return []
    out = []
    for item in traceback.extract_stack(frame)[-limit:]:
        out.append("%s:%d:%s" % (Path(item.filename).name, item.lineno, item.name))
    return out


def _gate_state(gate):
    """(owner ident, holder name, held seconds, started token) without taking the gate's condition for long."""
    cv = getattr(gate, "_cv", None)
    got = cv.acquire(timeout=0.2) if cv is not None else True
    try:
        owner = getattr(gate, "_owner", None)
        started = float(getattr(gate, "_started", 0.0) or 0.0)
        name = str(getattr(gate, "_holder_name", "") or "")
    finally:
        if got and cv is not None:
            cv.release()
    held = (time.perf_counter() - started) if owner is not None and started else 0.0
    return owner, name, held, started


def _write(line):
    root = _ROOT[0]
    if root is None:
        try:
            import android_live_bridge as bridge
            root = getattr(getattr(bridge, "_RUNTIME", None), "root", None)
        except Exception:
            root = None
    if root is None:
        return False
    try:
        with (Path(root) / "stock" / SAMPLE_FILE).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception:
        return False


def check_gates():
    """One watchdog pass. Returns the number of reports written."""
    with _LOCK:
        gates = list(_GATES)
    written = 0
    for gate in gates:
        try:
            owner, name, held, started = _gate_state(gate)
            if owner is None or held < LONG_HOLD_SECONDS:
                continue
            with _LOCK:
                if _REPORTED.get(id(gate)) == started or _LINES[0] >= MAX_LINES:
                    continue
                _REPORTED[id(gate)] = started
                _LINES[0] += 1
            record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "holder": name[:60], "held_s": round(held, 1),
                      "stack": _frames(owner), "lag_max_ms": lag_stats()[0]}
            if _write(json.dumps(record, ensure_ascii=False)):
                written += 1
        except Exception:
            continue
    return written


def _lag_probe():
    while True:
        started = time.perf_counter()
        time.sleep(LAG_SLEEP)
        over = (time.perf_counter() - started - LAG_SLEEP) * 1000.0
        with _LOCK:
            _LAG.append(max(0.0, over))


def _watchdog():
    while True:
        time.sleep(POLL_SECONDS)
        try:
            check_gates()
        except Exception:
            pass


def start_threads():
    with _LOCK:
        if _STARTED[0]:
            return
        _STARTED[0] = True
    for target, name in ((_lag_probe, "artitrade-v0400-lag-probe"), (_watchdog, "artitrade-v0400-gate-watchdog")):
        threading.Thread(target=target, name=name, daemon=True).start()


def disconnect_suffix(runner):
    """Short text appended to a 'disconnected' health detail. Never raises."""
    try:
        parts = []
        gate = getattr(getattr(runner, "store", None), "lock", None)
        if gate is not None and hasattr(gate, "snapshot_metrics"):
            m = gate.snapshot_metrics()
            parts.append("gate_now=%s long_holds=%s max_hold_ms=%s max_wait_ms=%s" % (
                (m.get("current_holder") or "-")[:30], m.get("long_holds"), m.get("longest_holder_ms"),
                m.get("normal_max_wait_ms")))
        q = getattr(runner, "_feed_message_queue", None)
        if q is not None:
            parts.append("feedq=%s hi=%s overflow=%s" % (q.qsize(), getattr(runner, "_feed_high_water", "?"),
                                                           getattr(runner, "_feed_overflows", "?")))
        tq = getattr(runner, "_tick_queue", None)
        if tq is not None:
            parts.append("tickq=%s" % tq.qsize())
        mx, p95 = lag_stats()
        parts.append("sched_lag_max_ms=%s p95=%s" % (mx, p95))
        return " | DIAG " + " ".join(parts)
    except Exception:
        return ""


def install(root=None, d_storage=None, d_live=None, threads=True):
    """Wrap the gate constructor and the health callback once. Idempotent; no behaviour change."""
    if root is not None:
        _ROOT[0] = root
    if _INSTALLED[0]:
        return
    if d_storage is None:
        import d_storage
    if d_live is None:
        import d_live
    gate_type = d_storage.TradePriorityRLock
    native_init = gate_type.__init__

    def init(self, *a, **kw):
        native_init(self, *a, **kw)
        register(self)

    gate_type.__init__ = init
    try:
        import gc
        for obj in gc.get_objects():
            if isinstance(obj, gate_type):
                register(obj)
    except Exception:
        pass
    runner_type = d_live.LivePaperRunner
    native_health = runner_type._health

    def health(self, state, details=""):
        if "연결 끊김" in str(state):
            details = str(details) + disconnect_suffix(self)
        return native_health(self, state, details)

    runner_type._health = health
    if threads:
        start_threads()
    _INSTALLED[0] = True
