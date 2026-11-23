"""0.3.6 (owner 2026-11-23, issue #151): KRX best-ask/bid quantity read from the book that is really there.

Owner: "가격이 변동이 계속있었고 변동이 있다는건 호가와 수량의 변동이 있다는건데 왜 너만 그 변동 속에서
물량을 못가져오냐" (지슨 AT: 1 share; 하림지주 GMO: 3 of 39 shares, order left NEW for hours).

Evidence (stock error export 2026-11-23 15:13):
* `gmo_issues` GMO_BOOK_INVALID 476x, e.g. `0D_KRX_ASK_DEPTH_ZERO KRX ask=9900 bid=9880 ask_qty=0 bid_qty=0
  specific_depth_present=True combined_depth_present=True` - a real best ask/bid price pair with ZERO quantity
  although the combined quantity FID is present.
* Both vendor readers (`d_v19_portfolio.Book.on_message` for AT, `GMOService.on_book_message` for GMO) read the
  quantity from FID 6044 / 6054 for a KRX 0D row and fall back to FID 61 / 71 ONLY when 6044 / 6054 is absent
  (None or ''). A placeholder "0" counts as present, so the update is dropped (`qty<=0: continue`) and the
  book stays empty/stale, every following price change is lost the same way.
* A KRX 0D row (item = plain 6-digit code, no `_NX` / `_AL` suffix) IS the KRX-only book: its FID 61 / 71 are the
  KRX best ask / bid quantities. NXT keeps its strict own-FID rule (6066 / 6076) - not touched here.

Fix (overlay, vendor engine untouched): before either reader sees a message, every KRX 0D row whose 6044 (6054)
is zero / blank while 61 (71) is a positive number gets 6044 (6054) := 61 (71). Nothing else is changed: prices,
freshness, 50 % participation, reservations and every other guard stay with the vendor code. Numeric samples of the
FIDs involved (no account, order or key data) are kept in `stock/depth_fid_samples_v0360.jsonl` (<= 300 lines) so the
next export can prove or refute the reading.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

SAMPLE_FILE = "depth_fid_samples_v0360.jsonl"
SAMPLE_MAX_LINES = 300
SAMPLE_GAP_SECONDS = 30.0
FIDS = ("41", "51", "61", "71", "6044", "6054", "6066", "6076")

_LOCK = threading.Lock()
_STATS = {"rows": 0, "krx_rows": 0, "fallback_ask": 0, "fallback_bid": 0, "both_zero": 0}
_LAST_SAMPLE = {}
_SAMPLE_LINES = [0]
_INSTALLED = False
_ROOT = [None]


def stats():
    with _LOCK:
        return dict(_STATS)


def _number(raw):
    try:
        v = abs(float(str(raw).replace(",", "").replace("+", "").strip()))
    except (TypeError, ValueError):
        return 0.0
    return v if v == v and v != float("inf") else 0.0


def _blank_or_zero(raw):
    return raw is None or str(raw).strip() == "" or _number(raw) == 0.0


def normalize_row(vals, venue):
    """In place. Returns (ask_filled, bid_filled). Only a KRX row, only zero/blank -> positive combined."""
    if venue != "KRX" or not isinstance(vals, dict):
        return False, False
    ask = bid = False
    if _blank_or_zero(vals.get("6044")) and _number(vals.get("61")) > 0:
        vals["6044"] = vals.get("61")
        ask = True
    if _blank_or_zero(vals.get("6054")) and _number(vals.get("71")) > 0:
        vals["6054"] = vals.get("71")
        bid = True
    return ask, bid


def normalize_message(message):
    """Apply to every 0D row of a REAL message. Never raises."""
    try:
        if not isinstance(message, dict) or message.get("trnm") != "REAL":
            return 0
        from nxt_eligibility import venue_from_realtime
        changed = 0
        for row in message.get("data") or []:
            if not isinstance(row, dict) or row.get("type") != "0D":
                continue
            vals = row.get("values")
            if not isinstance(vals, dict):
                continue
            venue, code = venue_from_realtime(row.get("item") or vals.get("9001"), vals.get("9081"))
            if venue != "KRX":
                continue
            raw = {k: vals.get(k) for k in FIDS}
            ask, bid = normalize_row(vals, venue)
            with _LOCK:
                _STATS["rows"] += 1
                _STATS["krx_rows"] += 1
                _STATS["fallback_ask"] += int(ask)
                _STATS["fallback_bid"] += int(bid)
                zero = _number(vals.get("6044")) == 0 and _number(vals.get("61")) == 0
                _STATS["both_zero"] += int(zero)
            if ask or bid or (_number(raw.get("41")) > 0 and _number(vals.get("6044")) == 0):
                _sample(code, raw, ask, bid)
            changed += int(ask or bid)
        return changed
    except Exception:
        return 0


def _sample(code, raw, ask, bid):
    now = time.monotonic()
    with _LOCK:
        if now - _LAST_SAMPLE.get(code, -1e9) < SAMPLE_GAP_SECONDS or _SAMPLE_LINES[0] >= SAMPLE_MAX_LINES:
            return
        _LAST_SAMPLE[code] = now
        _SAMPLE_LINES[0] += 1
    root = _ROOT[0]
    if root is None:
        try:
            import android_live_bridge as bridge
            root = getattr(getattr(bridge, "_RUNTIME", None), "root", None)
        except Exception:
            root = None
    if root is None:
        return
    try:
        line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "code": code, "venue": "KRX",
                           "fids": {k: (None if v is None else str(v)[:16]) for k, v in raw.items()},
                           "fallback_ask": ask, "fallback_bid": bid}, ensure_ascii=False)
        with (Path(root) / "stock" / SAMPLE_FILE).open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def install(root=None):
    """Wrap the two vendor readers once. `root` is the app root for the numeric samples."""
    global _INSTALLED
    _ROOT[0] = root if root is not None else _ROOT[0]
    if _INSTALLED:
        return
    import d_v19_portfolio
    from d_gmo_runtime import GMOService

    native_book = d_v19_portfolio.Book.on_message
    native_gmo = GMOService.on_book_message

    def book_on_message(self, message, nxt_snapshot, market_map, received=None):
        normalize_message(message)
        return native_book(self, message, nxt_snapshot, market_map, received)

    def gmo_on_book_message(self, message, market_map):
        normalize_message(message)
        return native_gmo(self, message, market_map)

    d_v19_portfolio.Book.on_message = book_on_message
    GMOService.on_book_message = gmo_on_book_message
    _INSTALLED = True
