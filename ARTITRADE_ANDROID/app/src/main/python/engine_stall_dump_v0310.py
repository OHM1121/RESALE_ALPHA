"""0.3.1: read-only Python thread dump for a frozen engine (issue #146).

Evidence (coin error export 2026-11-22 20:35, app 0.2.9): at 19:39 the Python engine stopped
making progress while the Android process, the UI thread and the market-score worker stayed
alive. Scan, order-book, price-path and best-book recorders all went silent within ~40 s and
stayed silent for 55 minutes until the app was restarted. The same signature occurred in
issue #136 (0.2.1, 22:49) and its cause was never proven because nothing recorded WHERE the
threads were blocked.

This module answers exactly that question. `dump_threads()` formats every Python thread's
current stack (module:line function) so the next export names the blocked frames and the lock
they wait on. It only reads `sys._current_frames()`; it takes NO engine lock, calls no network,
writes nothing and creates no order, fill or price, so it is safe to call while the engine is
frozen. Local variables are never included.
"""
from __future__ import annotations

import linecache
import os
import re
import sys
import threading
import time

MAX_FRAMES_PER_THREAD = 7
MAX_CHARS = 8500
# Function names that mean "this thread is parked, not working".
_IDLE_WAIT = {"wait", "get", "acquire", "_wait_for_tstate_lock", "sleep", "select", "recv",
              "accept", "_worker", "run_forever", "poll"}
_LOCK_WAIT = {"acquire", "_acquire_restore", "__enter__", "wait", "_wait_for_tstate_lock"}
# `lock.acquire()` / `with lock:` are C calls: the blocked thread has NO Python frame for them,
# so its top Python frame is the line that called them. Recognise that line by its text.
_LOCK_LINE = re.compile(r"\.acquire\(|^\s*with\s+[\w.\[\]()'\", ]*(lock|Lock|_lock|mutex|cond|not_empty|not_full|"
                        r"order_lock|rlock)\b|\.(join|wait)\(")
_QUEUE_LINE = re.compile(r"\.get\(|\.get_nowait\(|queue|Queue")


def _short(path):
    path = str(path or "")
    base = os.path.basename(path)
    return base or path


def _source_line(filename, lineno):
    try:
        return linecache.getline(filename, lineno).strip()[:90]
    except Exception:
        return ""


def _frames(frame, limit):
    """[(file, line, function)] innermost first, plus the top frame's source text."""
    out, text = [], ""
    while frame is not None and len(out) < limit:
        code = frame.f_code
        if not out:
            text = _source_line(code.co_filename, frame.f_lineno)
        out.append((_short(code.co_filename), frame.f_lineno, code.co_name))
        frame = frame.f_back
    return out, text


def snapshot_threads(limit=MAX_FRAMES_PER_THREAD):
    """List of dicts: name, daemon, ident, frames (innermost first), lock_wait (bool)."""
    names = {}
    try:
        for t in threading.enumerate():
            names[t.ident] = (t.name, bool(t.daemon))
    except Exception:
        pass
    rows = []
    for ident, frame in sys._current_frames().items():
        name, daemon = names.get(ident, ("thread-%s" % ident, True))
        frames, text = _frames(frame, limit)
        top = frames[0][2] if frames else ""
        # A worker idling in queue.get() is normal; only a thread parked on a lock is suspect.
        queue_idle = any(f[0] == "queue.py" for f in frames[:3])
        lock_wait = (top in _LOCK_WAIT or bool(_LOCK_LINE.search(text))) and not queue_idle
        idle = lock_wait or queue_idle or top in _IDLE_WAIT or bool(_QUEUE_LINE.search(text))
        rows.append({"name": name, "daemon": daemon, "ident": ident, "frames": frames,
                     "src": text, "lock_wait": lock_wait, "idle": idle})
    return rows


def format_dump(rows, max_chars=MAX_CHARS, header=""):
    # Busy / lock-waiting threads first: the blocked chain is what matters.
    ordered = sorted(rows, key=lambda r: (r["idle"] and not r["lock_wait"], not r["lock_wait"], r["name"]))
    lines = [header] if header else []
    waiting = [r["name"] for r in ordered if r["lock_wait"]]
    lines.append("threads=%d lock_waiting=%d [%s]" % (len(rows), len(waiting), ",".join(waiting)[:600]))
    for r in ordered:
        chain = " < ".join("%s:%d %s" % f for f in r["frames"])
        src = r.get("src") or ""
        lines.append("%s%s%s | %s%s" % (r["name"], " d" if r["daemon"] else "",
                                        " LOCK" if r["lock_wait"] else "", chain,
                                        (" >> " + src) if src else ""))
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars - 12] + "\n...[cut]"


def dump_threads(max_chars=MAX_CHARS):
    """Formatted dump of all Python threads. Never raises."""
    try:
        started = time.monotonic()
        text = format_dump(snapshot_threads(), max_chars,
                           header="py-thread-dump at=%d" % int(time.time()))
        return text + "\ntook_ms=%d" % int((time.monotonic() - started) * 1000)
    except Exception as exc:                      # a diagnostic must never be the next fault
        return "py-thread-dump-failed %s: %s" % (type(exc).__name__, str(exc)[:200])
