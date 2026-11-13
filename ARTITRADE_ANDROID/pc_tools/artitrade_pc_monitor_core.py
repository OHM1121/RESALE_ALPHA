"""ARTITRADE PC read-only mobile ledger model and MSERVER client.

No broker modules are imported. Every remote request is GET, only via Tailscale.
Authoritative source: phone MSERVER current record revisions, never a PC trading DB.
"""
from __future__ import annotations

import ipaddress
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Callable

TAILNET = ipaddress.ip_network("100.64.0.0/10")
KST = timezone(timedelta(hours=9))
SOURCES = [
    ("stock", "at"), ("stock", "cw"), ("stock", "shared"),
    ("coin", "general"), ("coin", "ma"), ("coin", "shared"),
]
MARKETS = (
    ("stock/at", "주식 · 기준봉"),
    ("stock/ma", "주식 · MA"),
    ("stock/gmo", "주식 · 수급매매"),
    ("stock/close", "주식 · 종가베팅"),
    ("coin/general", "코인 · 기준봉"),
    ("coin/ma", "코인 · MA"),
)
LABELS = dict(MARKETS)
FRAMES = ("5m", "30m", "60m", "day")
BAND_NAMES = ("0~20점", "21~40점", "41~60점", "61~80점", "81~100점")

def validate_hub_url(raw: str) -> str:
    try:
        addr = urllib.parse.urlsplit((raw or "").strip().rstrip("/"))
        if addr.scheme != "http" or not addr.hostname or addr.username or addr.password:
            raise ValueError("Tailscale HTTP 주소 필요")
        host = ipaddress.ip_address(addr.hostname)
        if host not in TAILNET and not host.is_loopback:
            raise ValueError("Tailscale 100.64/10 주소만 허용")
        if addr.port != 8765 or addr.path or addr.query or addr.fragment:
            raise ValueError("주소는 http://100.x.y.z:8765 형식")
        return f"http://{addr.hostname}:8765"
    except (ValueError, TypeError) as exc:
        raise ValueError("MSERVER 주소 오류: " + str(exc)) from exc

def normalize_frame(raw) -> str:
    val = str(raw or "").strip().lower()
    mapping = {
        "5": "5m", "5m": "5m", "5min": "5m", "5분": "5m",
        "30": "30m", "30m": "30m", "30min": "30m", "30분": "30m",
        "60": "60m", "60m": "60m", "60min": "60m", "1h": "60m", "1시간": "60m",
        "day": "day", "1d": "day", "daily": "day", "d": "day", "일봉": "day",
    }
    return mapping.get(val, "")

def score_band(value) -> str:
    try:
        if value is None or str(value).strip() == "":
            return "점수 미기록"
        number = float(str(value).replace(",", ""))
        if not math.isfinite(number):
            return "점수 미기록"
    except (TypeError, ValueError):
        return "점수 미기록"
    if 0 <= number <= 20: return BAND_NAMES[0]
    if 20 < number <= 40: return BAND_NAMES[1]
    if 40 < number <= 60: return BAND_NAMES[2]
    if 60 < number <= 80: return BAND_NAMES[3]
    if 80 < number <= 100: return BAND_NAMES[4]
    return "기타 점수"

def bool_value(value) -> bool:
    if isinstance(value, bool): return value
    if isinstance(value, (int, float)): return bool(value)
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "filled", "bought", "매수")

def outcome_of(row: dict) -> str:
    val = str(row.get("outcome") or "").strip().upper()
    if val in ("HIT", "MISS", "PENDING"): return val
    target = row.get("target_hit")
    if target is True: return "HIT"
    if target is False: return "MISS"
    return "PENDING"

def event_time(value) -> str:
    if value is None or str(value).strip() == "": return "미기록"
    s = str(value).strip()
    try:
        if re.fullmatch(r"\d{13}", s): return datetime.fromtimestamp(int(s) / 1000, KST).strftime("%Y-%m-%d %H:%M")
        if re.fullmatch(r"\d{10}", s): return datetime.fromtimestamp(int(s), KST).strftime("%Y-%m-%d %H:%M")
        if re.fullmatch(r"\d{14}", s): return datetime.strptime(s, "%Y%m%d%H%M%S").strftime("%Y-%m-%d %H:%M")
        if re.fullmatch(r"\d{8}", s): return datetime.strptime(s, "%Y%m%d").strftime("%Y-%m-%d")
        if s.endswith("Z") or "T" in s and ("+" in s[10:] or "-" in s[10:]):
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return parsed.astimezone(KST).strftime("%Y-%m-%d %H:%M")
        return s.replace("T", " ")[:16]
    except (OverflowError, ValueError, OSError):
        return s[:30]

def time_from_row(row: dict) -> str:
    for k in ("at", "ts", "executed_at", "filled_at", "created_at", "ref_ts", "ref_at",
              "event_date", "gmo_event_date", "entry_ts", "buy_at"):
        if row.get(k): return event_time(row[k])
    return "미기록"

def record_definitions():
    for market, strategy in SOURCES:
        for bucket in (("ledger",) if strategy == "shared" else ("ledger", "research", "errors")):
            yield market, strategy, bucket

def http_get(base: str, token: str, method: str, path: str, params=None) -> dict:
    if method != "GET" or path not in ("/v1/status", "/v1/record", "/v1/record/meta", "/health"):
        raise ValueError("조회 전용: GET 상태/원장 API 외 명령은 모두 차단됨")
    url = validate_hub_url(base) + path
    if params:
        if path not in ("/v1/record", "/v1/record/meta"):
            raise ValueError("Unexpected query")
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET", headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "Cache-Control": "no-store",
    })
    # Avoid Windows corporate proxies intercepting the private Tailscale address.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=12) as response:
            data = response.read(36 * 1024 * 1024 + 1)
            if len(data) > 36 * 1024 * 1024:
                raise ValueError("MSERVER 응답 크기 초과")
            return json.loads(data.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try: error = json.loads(exc.read(16384).decode("utf-8"))
        except Exception: error = {"error": "HTTP " + str(exc.code)}
        raise ConnectionError("MSERVER HTTP " + str(exc.code) + " · " + str(error.get("error", ""))) from exc

def dict_or_empty(value) -> dict:
    return value if isinstance(value, dict) else {}

def extract_segments(raw: dict) -> dict:
    """Map the MSERVER storage keys back into six independent phone strategies."""
    stock_at = dict_or_empty(raw.get(("stock", "at", "ledger")))
    at_research = dict_or_empty(raw.get(("stock", "at", "research")))
    stock_cw = dict_or_empty(raw.get(("stock", "cw", "ledger")))
    cw_research = dict_or_empty(raw.get(("stock", "cw", "research")))
    source = {
        "stock/at": (stock_at, at_research),
        "stock/ma": (dict_or_empty(stock_at.get("ma_subledger") or stock_at.get("ma")),
                     dict_or_empty(at_research.get("ma"))),
        "stock/gmo": (dict_or_empty(stock_at.get("gmo")),
                      dict_or_empty(at_research.get("gmo"))),
        "stock/close": (stock_cw, cw_research),
        "coin/general": (dict_or_empty(raw.get(("coin", "general", "ledger"))),
                         dict_or_empty(raw.get(("coin", "general", "research")))),
        "coin/ma": (dict_or_empty(raw.get(("coin", "ma", "ledger"))),
                    dict_or_empty(raw.get(("coin", "ma", "research")))),
    }
    return {k: {"ledger": v[0], "research": v[1]} for k, v in source.items()}

def research_rows(part: dict) -> list[dict]:
    research = dict_or_empty(part.get("research"))
    return [row for row in research.get("items", []) if isinstance(row, dict)] if isinstance(research.get("items", []), list) else []

def flatten_items(segments: dict, bucket: str) -> list[dict]:
    rows = []
    for key, part in segments.items():
        source = dict_or_empty(part.get("ledger" if bucket != "research" else "research"))
        data = source.get(bucket if bucket != "research" else "items") or []
        if not isinstance(data, list): continue
        for item in data:
            if not isinstance(item, dict): continue
            row = dict(item)
            row["_source"] = key
            row["_frame"] = normalize_frame(item.get("tf") or item.get("frame"))
            rows.append(row)
    return rows

def summarize_research(items: list[dict]) -> list[dict]:
    """Per timeframe and score band: separate actual fills from entered virtual trials.

    Hit rate uses only concluded HIT+MISS; PENDING and virtual not entered are excluded.
    """
    groups = {}
    for row in items:
        if not isinstance(row, dict): continue
        frame = normalize_frame(row.get("tf") or row.get("frame")) or "미분류"
        band = score_band(row.get("score"))
        key = (frame, band)
        d = groups.setdefault(key, {
            "frame": frame, "band": band, "signals": 0, "actual_count": 0,
            "actual_hit": 0, "actual_miss": 0, "actual_pending": 0,
            "virtual_entries": 0, "virtual_hit": 0, "virtual_miss": 0,
            "virtual_pending": 0, "virtual_not_entered": 0,
            "actual_rate": None, "virtual_rate": None,
        })
        d["signals"] += 1
        actual = bool_value(row.get("bought"))
        virtual_entered = bool_value(row.get("entry_observed"))
        outcome = outcome_of(row)
        if actual:
            d["actual_count"] += 1
            d["actual_" + outcome.lower()] += 1
        elif virtual_entered:
            d["virtual_entries"] += 1
            d["virtual_" + outcome.lower()] += 1
        else:
            d["virtual_not_entered"] += 1
    for d in groups.values():
        for prefix in ("actual", "virtual"):
            evaluated = d[prefix + "_hit"] + d[prefix + "_miss"]
            d[prefix + "_rate"] = round(d[prefix + "_hit"] * 100 / evaluated, 1) if evaluated else None
    frame_order = {x: i for i, x in enumerate(FRAMES)}
    band_order = {x: i for i, x in enumerate(BAND_NAMES)}
    return sorted(groups.values(), key=lambda x: (
        frame_order.get(x["frame"], 99), band_order.get(x["band"], 99), x["band"]
    ))

class ReadOnlyHubClient:
    """Meta-first polling. No PUT/POST, no remote control, no trading imports."""
    def __init__(self, base: str, token: str, requester: Callable | None = None):
        self.base = validate_hub_url(base)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", (token or "").strip()):
            raise ValueError("MSERVER 토큰은 64자리 16진수이어야 함")
        self.token = token.strip()
        self.requester = requester or (lambda method, path, params: http_get(
            self.base, self.token, method, path, params))
        self.cache = {}
        self.revisions = {}
        self.metas = {}

    def fetch(self) -> dict:
        state = self.requester("GET", "/v1/status", None)
        if not state.get("ok", False):
            raise ConnectionError("MSERVER 인증 또는 상태 조회 실패: " + str(state.get("error", "UNKNOWN")))
        warnings = []
        updated_cache = dict(self.cache)
        updated_revisions = dict(self.revisions)
        updated_metas = dict(self.metas)
        for market, strategy, bucket in record_definitions():
            key = (market, strategy, bucket)
            args = {"market": market, "strategy": strategy, "bucket": bucket}
            try:
                meta = self.requester("GET", "/v1/record/meta", args)
                if not meta.get("ok"):
                    raise ConnectionError(str(meta.get("error", "RECORD_META_ERROR")))
                updated_metas[key] = meta
                if not meta.get("exists", False):
                    updated_cache.pop(key, None)
                    updated_revisions.pop(key, None)
                    continue
                rev = int(meta.get("revision", 0))
                if updated_revisions.get(key) == rev and key in updated_cache:
                    continue
                reply = self.requester("GET", "/v1/record", args)
                if not reply.get("ok", False):
                    raise ConnectionError(str(reply.get("error", "RECORD_READ_ERROR")))
                data = reply.get("data")
                if not isinstance(data, dict):
                    raise ValueError("원장 JSON 형식 오류")
                updated_cache[key] = data
                updated_revisions[key] = int(reply.get("revision", rev))
            except Exception as exc:
                warnings.append(f"{market}/{strategy}/{bucket}: {type(exc).__name__} {str(exc)[:95]}")
        self.cache = updated_cache
        self.revisions = updated_revisions
        self.metas = updated_metas
        return {
            "connected": True, "status": state,
            "records": dict(updated_cache), "metas": dict(updated_metas),
            "segments": extract_segments(updated_cache),
            "warnings": warnings,
            "fetched_at": time.time(),
            "source": "PHONE_MSERVER_READ_ONLY",
        }
