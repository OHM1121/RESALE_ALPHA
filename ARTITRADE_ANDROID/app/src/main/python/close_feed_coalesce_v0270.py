"""0.2.7: stock close-betting (종가베팅) realtime feed keeps only the LATEST row per symbol/type.

Root cause (issue #142; same mechanism as coin #137, CONFIRMED in vendor code,
device prevalence UNVERIFIED because the weekday rows were erased by the
2026-11-20 resets):
* `at_close_bridge.publish` puts EVERY message of the app's single Kiwoom
  websocket (all ~200 subscribed symbols, 0B and 0D) into ONE 4,096-slot queue
  that ONE close-betting thread drains, with per-message bookkeeping and
  research-DB writes.
* The consumer discards every frame older than 3 s (`valid_broker_frame`).
  Once it falls behind near the close, most frames are thrown away, the
  close-betting order books stop updating and its unchanged 3 s gate rejects
  candidates as NO_FRESH_ORDERBOOK (0.0.18 history: 70 of 90 evaluations,
  observed book age 3-300 s). The 0.1.0 subscription pin cannot help when
  the frames are dropped after arrival.
* Rule replay on the stored daily bars: 11-18 and 11-19 had 11 and 12 symbols
  meeting the daily close-betting shape (market-cap test not applied), while
  the app bought nothing.

Fix: the close-betting queue is replaced by a coalescing queue. Each
(symbol, row type) keeps only its newest row; one token per key is queued, so
the backlog is bounded by the number of keys and the consumer always reads
current data. Freshness is still the arrival time of that newest row and the
vendor's own 3 s checks are unchanged. No quote, order or fill is created.
The vendor engine file is not modified.
"""
from __future__ import annotations

import queue
import threading
import time

_INSTALLED = False
_KEY = "__close_feed_key_v0270__"
_MSG = "__close_feed_msg_v0270__"
STATS_INTERVAL_SECONDS = 300.0


class CoalescingCloseQueue(queue.Queue):
    """Queue of (arrived_monotonic, message) whose REAL rows are coalesced."""

    def __init__(self, maxsize=4096):
        super().__init__(maxsize)
        self._latest_lock = threading.Lock()
        self._latest = {}
        self._pending = set()
        self.owner = None
        self.stats = {"rows": 0, "coalesced": 0, "dispatched": 0, "queue_full": 0,
                      "control": 0}
        self._stats_at = time.monotonic()

    @staticmethod
    def _key(row):
        return (str(row.get("item") or ""), str(row.get("type") or ""))

    def put(self, item, block=True, timeout=None):
        self.put_nowait(item)

    def put_nowait(self, item):
        try:
            arrived, msg = item
        except (TypeError, ValueError):
            return queue.Queue.put(self, (_MSG, item), False)
        if not (isinstance(msg, dict) and msg.get("trnm") == "REAL"
                and isinstance(msg.get("data"), list)):
            self.stats["control"] += 1
            return queue.Queue.put(self, (_MSG, item), False)
        for row in msg["data"]:
            if not isinstance(row, dict):
                continue
            key = self._key(row)
            with self._latest_lock:
                self.stats["rows"] += 1
                self._latest[key] = (arrived, row)
                if key in self._pending:
                    self.stats["coalesced"] += 1
                    continue
                self._pending.add(key)
            try:
                queue.Queue.put(self, (_KEY, key), False)
            except queue.Full:
                with self._latest_lock:
                    self._pending.discard(key)
                    self._latest.pop(key, None)
                    self.stats["queue_full"] += 1
        return None

    def get(self, block=True, timeout=None):
        tag, value = super().get(block, timeout)
        self._maybe_report()
        if tag == _KEY:
            with self._latest_lock:
                self._pending.discard(value)
                found = self._latest.pop(value, None)
                if found is not None:
                    self.stats["dispatched"] += 1
            if found is None:
                # Never invent a frame: an expired token is an invalid frame.
                return 0.0, {"trnm": "COALESCED_EMPTY"}
            arrived, row = found
            return arrived, {"trnm": "REAL", "data": [row]}
        return value

    def _maybe_report(self):
        now = time.monotonic()
        if now - self._stats_at < STATS_INTERVAL_SECONDS or self.owner is None:
            return
        self._stats_at = now
        try:
            self.owner._safe_event("CW_FEED_COALESCE_V0270",
                                   dict(self.stats, depth=self.qsize()), "INFO")
        except Exception:
            pass


def install(core=None):
    global _INSTALLED
    if _INSTALLED:
        return
    import at_close_runner
    native_init = at_close_runner.AtCloseRunner.__init__

    def init(self, *args, **kwargs):
        native_init(self, *args, **kwargs)
        coalescing = CoalescingCloseQueue(maxsize=4096)
        coalescing.owner = self
        self.shared_queue = coalescing

    at_close_runner.AtCloseRunner.__init__ = init
    _INSTALLED = True
