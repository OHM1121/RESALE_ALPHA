"""ARTITRADE v041 post-entry HIGH + bar-time recovery, research only.

The original candidate_forward.max_price is not attributed to an invented
timestamp. For each real or recorded paper entry this worker fetches verified
OHLC bars from the same stock code and searches only AFTER the entry.
A minute bar gives the opening minute of the maximum, not its exact trade
second. Unavailable historical API coverage remains unavailable, never a
fictional time/high. This database is part of the research ZIP.
"""
from __future__ import annotations
import datetime as dt
from contextlib import closing
import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import quote

from ai_research_v040 import _timestamp,_number
from market_research_v033 import _public_json
from krx_session_v041 import KST,status as market_status

DB_NAME="forward_high_v041.sqlite3"
_LOCK=threading.RLock()
_WORKERS={}
SOURCE_TTL=900
MISSING_TTL=900
MAX_ATTEMPT=120


def normalized_code(code):
    s=str(code or "").strip().upper()
    if s.startswith("A") and len(s)==7:s=s[1:]
    return s if len(s)==6 and s.isdigit() else None


def _json_chart(symbol,interval,span):
    for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
        try:
            url="https://"+host+"/v8/finance/chart/"+quote(symbol,safe="")
            response=_public_json(url,{"interval":interval,"range":span})
            data=response["chart"]["result"][0]
            metadata=data.get("meta") or {}
            fetched_symbol=str(metadata.get("symbol") or symbol).upper()
            if fetched_symbol!=symbol.upper():continue
            indexes=data.get("timestamp") or []
            quotes=(data.get("indicators") or {}).get("quote") or []
            if not quotes:return None
            q=quotes[0]
            highs=q.get("high") or []
            result=[]
            for i,stamp in enumerate(indexes):
                high=_number(highs[i] if i<len(highs) else None)
                if high is not None and high>0:
                    result.append((int(stamp),high))
            if result:return result
        except Exception:
            continue
    return None


def _start_of_next_bar(ts,seconds):
    # Starting minute of a candle cannot be used if the entry occurs inside it.
    return ((int(ts)+seconds-1)//seconds)*seconds


def _same_market_date(ts):
    return dt.datetime.fromtimestamp(int(ts),KST).date().isoformat()


def _daily_conflicts(symbol,quotes,entry_ts,precision):
    # Accept 1m or 5m highs only if not above the exchange's own daily OHLC.
    # For days strictly after the entry date, the daily HIGH must not exceed
    # recovered intraday HIGH, or the intraday source is incomplete.
    daily=_json_chart(symbol,"1d","1mo")
    if not daily:return False,False
    full={_same_market_date(ts):high for ts,high in daily}
    intraday={}
    for ts,high in quotes:
        date=_same_market_date(ts)
        intraday[date]=max(intraday.get(date,0),high)
    entry_day=_same_market_date(entry_ts)
    for day,peak in intraday.items():
        reference=full.get(day)
        if reference is not None and peak>reference+0.000001*max(1,reference):
            return True,False  # provider contradiction: reject entire value
    checked=0
    for day,peak in full.items():
        if day<=entry_day:continue
        # All subsequent trading sessions must be present in 1-minute/5-minute
        # history; a single recovered candle must not certify a missing week.
        if day not in intraday:
            return False,False
        checked+=1
        if intraday[day]+0.000001*max(1,peak)<peak:
            return False,False
    return False,True if checked or entry_day in intraday else False


def compute_forward_high(symbol,entry_ts,now=None):
    """After-entry exact HIGH and start-of-bar time (1m preferred, 5m fallback).

    A first-partial minute is excluded. No future bar, no off-symbol high, and
    no use of candidate_forward.last_ts as peak time.
    """
    now=int(time.time() if now is None else now)
    entry_ts=_timestamp(entry_ts)
    code=normalized_code(symbol)
    if code is None or entry_ts is None or entry_ts>now:
        return {"state":"BAD_ENTRY","reason":"매수시각 또는 종목코드 미확인"}
    age=now-entry_ts
    sources=[("1m","7d",60,"YAHOO_1M"),
             ("5m","1mo",300,"YAHOO_5M")]
    failures=[]
    for interval,span,precision,origin in sources:
        if interval=="1m" and age>6*86400+22*3600:
            continue  # beyond 1m provider retention; try 5m source
        if interval=="5m" and age>28*86400:
            continue
        for suffix in (".KS",".KQ"):
            provider_code=code+suffix
            candles=_json_chart(provider_code,interval,span)
            if not candles:continue
            first=_start_of_next_bar(entry_ts,precision)
            after=[(ts,high) for ts,high in candles
                   if ts>=first and ts+precision<=now]
            if not after:continue
            # 1m resolution can establish a minute window, not second-level
            # tick high. Test daily HIGH for provider contradictions.
            contradiction,complete=_daily_conflicts(
                provider_code,after,entry_ts,precision)
            if contradiction:
                failures.append(provider_code+":DAILY_HIGH_CONTRADICTION")
                continue
            high=max(v for _,v in after)
            when=min(ts for ts,val in after if val==high)
            first_seen=min(ts for ts,_ in after)
            last_seen=max(ts+precision for ts,_ in after)
            # Require an observation from the purchase date when it has been
            # possible to trade after purchase in the same regular session.
            local_entry=dt.datetime.fromtimestamp(entry_ts,KST)
            minutes=local_entry.hour*60+local_entry.minute
            expected_same_day=minutes<15*60+25 and market_status(entry_ts)[0]
            entry_day=_same_market_date(entry_ts)
            starting_ok=(not expected_same_day or
                         any(_same_market_date(ts)==entry_day for ts,_ in after))
            # The source must still cover through at least the last session
            # represented by its daily history; a public provider may omit
            # full sessions. Daily reconciliation is required for a "verified".
            complete=bool(complete and starting_ok)
            if not complete:
                failures.append(provider_code+":INCOMPLETE_MINUTE_HISTORY")
                continue
            return {"state":"OK","code":code,"entry_ts":entry_ts,
                    "high_price":high,"high_at":when,
                    "precision_seconds":precision,"source":origin,
                    "coverage_start":first_seen,"coverage_end":last_seen,
                    "observed_bars":len(after)}
    return {"state":"SOURCE_UNAVAILABLE","code":code,"entry_ts":entry_ts,
            "reason":";".join(failures[:3]) or "시세 API에서 매수 이후 봉 기록 조회 불가"}


def _db(root):
    path=Path(root)/"stock"/DB_NAME
    path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(str(path),timeout=6,check_same_thread=False)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA busy_timeout=6000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS price_highs(
        signal_id TEXT NOT NULL,code TEXT NOT NULL,
        entry_ts INTEGER NOT NULL,
        high_price REAL,high_at INTEGER,
        precision_seconds INTEGER,source TEXT,
        coverage_start INTEGER,coverage_end INTEGER,
        observed_bars INTEGER,checked_at INTEGER NOT NULL,
        state TEXT NOT NULL,reason TEXT,
        PRIMARY KEY(signal_id,code,entry_ts)
      );
    """)
    return conn


def _row_key(item):
    code=normalized_code(item.get("code"))
    sid=item.get("signal_id")
    entry=item.get("buy_at") or item.get("entry_ts") or item.get("virtual_buy_at")
    parsed=_timestamp(entry)
    if code is None or sid is None or parsed is None:return None
    return str(sid),code,parsed


def fetch_cache(root,items):
    """One indexed query per research refresh; use only matching entry ID."""
    wanted=[_row_key(item) for item in items]
    wanted=[item for item in wanted if item is not None]
    if not wanted:return {}
    path=Path(root)/"stock"/DB_NAME
    if not path.is_file():return {}
    try:
        with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                             uri=True,timeout=3) as conn:
            conn.row_factory=sqlite3.Row
            rows=conn.execute("SELECT * FROM price_highs").fetchall()
        lookup=set(wanted)
        return {(r["signal_id"],r["code"],r["entry_ts"]):dict(r)
                for r in rows if (r["signal_id"],r["code"],r["entry_ts"]) in lookup}
    except (sqlite3.Error,OSError):
        return {}


def enrich(root,items):
    if not isinstance(items,list):return items
    source=fetch_cache(root,items)
    for row in items:
        key=_row_key(row)
        if key is None:continue
        result=source.get(key)
        if result is None:
            row["forward_high_status"]="API_QUERY_QUEUED"
            continue
        if result.get("state")!="OK":
            row["forward_high_status"]=result.get("state","API_QUERY_QUEUED")
            row["forward_high_error"]=result.get("reason")
            continue
        row["observed_max_price"]=result["high_price"]
        row["observed_max_at"]=result["high_at"]
        row["observed_max_evidence"]="VERIFIED_MINUTE_BAR"
        row["forward_high_source"]=result["source"]
        row["forward_high_precision_seconds"]=result["precision_seconds"]
        row["forward_high_status"]="API_RECOVERED"
    return items


class _PriceWorker:
    def __init__(self,root):
        self.root=Path(root)
        self.lock=threading.RLock()
        self.pending={}
        self.signal=threading.Event()
        self.thread=threading.Thread(target=self._loop,daemon=True,
            name="artitrade-forward-high-api-v041")
        self.thread.start()

    def offer(self,items):
        with self.lock:
            for row in items:
                key=_row_key(row)
                if key is not None:self.pending[key]=key
            if self.pending:self.signal.set()

    def _loop(self):
        while True:
            self.signal.wait(10)
            with self.lock:
                pending=list(self.pending.values())[:100]
                for key in pending:self.pending.pop(key,None)
                if not self.pending:self.signal.clear()
            if not pending:continue
            try:
                with closing(_db(self.root)) as conn:
                    now=int(time.time())
                    for sid,code,entry in pending:
                        previous=conn.execute(
                            "SELECT * FROM price_highs "
                            "WHERE signal_id=? AND code=? AND entry_ts=?",
                            (sid,code,entry)).fetchone()
                        ttl=SOURCE_TTL if previous and previous["state"]=="OK" else MISSING_TTL
                        if previous and now-previous["checked_at"]<ttl:continue
                        try:
                            found=compute_forward_high(code,entry,now)
                        except Exception as exc:
                            found={"state":"SOURCE_UNAVAILABLE",
                                   "reason":type(exc).__name__}
                        if previous and previous["state"]=="OK":
                            old_high=previous["high_price"]
                            new_high=_number(found.get("high_price"))
                            # A later failed/partial API request must not erase
                            # an already verified peak. Highest after entry is
                            # monotonic across extended observation windows.
                            if (new_high is None or old_high is not None and
                                    new_high<old_high):
                                found={k:previous[k] for k in (
                                    "state","high_price","high_at",
                                    "precision_seconds","source","coverage_start",
                                    "coverage_end","observed_bars")}
                        conn.execute("""INSERT INTO price_highs(
                            signal_id,code,entry_ts,high_price,high_at,
                            precision_seconds,source,coverage_start,
                            coverage_end,observed_bars,checked_at,state,reason)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT(signal_id,code,entry_ts)
                            DO UPDATE SET high_price=excluded.high_price,
                                high_at=excluded.high_at,
                                precision_seconds=excluded.precision_seconds,
                                source=excluded.source,
                                coverage_start=excluded.coverage_start,
                                coverage_end=excluded.coverage_end,
                                observed_bars=excluded.observed_bars,
                                checked_at=excluded.checked_at,state=excluded.state,
                                reason=excluded.reason""",
                            (sid,code,entry,found.get("high_price"),
                             found.get("high_at"),found.get("precision_seconds"),
                             found.get("source"),found.get("coverage_start"),
                             found.get("coverage_end"),found.get("observed_bars"),
                             now,found.get("state","SOURCE_UNAVAILABLE"),
                             found.get("reason")))
                        conn.commit()
                        time.sleep(0.2)
            except (sqlite3.Error,OSError):
                pass


def queue(root,rows):
    key=str(Path(root).resolve())
    with _LOCK:
        worker=_WORKERS.get(key)
        if worker is None:
            worker=_PriceWorker(key)
            _WORKERS[key]=worker
    worker.offer(rows)
    return True
