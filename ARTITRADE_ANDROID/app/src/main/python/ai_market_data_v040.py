"""Best-effort public OHLCV collection for ARTITRADE v040 AI research.

Only read-only public KRX chart / Upbit candle endpoints. Runs on the
independent research daemon, not on a buy/sell or market-data dispatch thread.
A historical entry feature is reconstructed ONLY from candles that had closed
before the exact signal timestamp. Missing candles remain missing.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote

from ai_research_v040 import (
    _number, _timestamp, _frame, _records, _rows, _key,
    _connect, compute_candle_indicators,
)

FRAME_SECONDS={"5m":300,"30m":1800,"60m":3600,"day":86400}
FRAME_UPBIT={"5m":5,"30m":30,"60m":60}
FRAME_YAHOO={"5m":"5m","30m":"30m","60m":"60m","day":"1d"}


def _http(url,params):
    # Reuse the public market-score provider's tested timeout and headers.
    from market_research_v033 import _public_json
    return _public_json(url,params)


def fetch_candles(market,symbol,frame,now=None):
    if frame not in FRAME_SECONDS or market not in ("stock","coin"):
        return [],""
    now=int(time.time() if now is None else now)
    if market=="coin":
        if not symbol.startswith("KRW-"):
            return [],""
        endpoint=("/v1/candles/days" if frame=="day" else
                  "/v1/candles/minutes/"+str(FRAME_UPBIT[frame]))
        data=_http("https://api.upbit.com"+endpoint,
                   {"market":symbol,"count":120})
        if not isinstance(data,list):return [],""
        bars=[]
        for row in data:
            if not isinstance(row,dict):continue
            ts=_timestamp(row.get("candle_date_time_kst"))
            if ts is None or ts+FRAME_SECONDS[frame]>now:continue
            high=_number(row.get("high_price"))
            low=_number(row.get("low_price"))
            close=_number(row.get("trade_price"))
            volume=_number(row.get("candle_acc_trade_volume"))
            if None in (high,low,close) or high<close or close<low:continue
            bars.append((ts,high,low,close,volume))
        return sorted({x[0]:x for x in bars}.values()),"UPBIT_CLOSED_OHLCV"
    # KRX numeric code: test both suffixes; never assign a different ticker.
    code=str(symbol).zfill(6)
    if len(code)!=6 or not code.isdigit():return [],""
    for suffix in (".KS",".KQ"):
        for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
            try:
                url="https://"+host+"/v8/finance/chart/"+quote(code+suffix)
                result=_http(url,{"interval":FRAME_YAHOO[frame],
                                  "range":"3mo" if frame=="day" else "1mo"})
                chart=result["chart"]["result"][0]
                candles=chart["indicators"]["quote"][0]
                timestamps=chart.get("timestamp") or []
                if len(timestamps)<16:continue
                h=candles.get("high") or []
                l=candles.get("low") or []
                c=candles.get("close") or []
                v=candles.get("volume") or []
                bars=[]
                for i,ts in enumerate(timestamps):
                    if int(ts)+FRAME_SECONDS[frame]>now:continue
                    hi=_number(h[i] if i<len(h) else None)
                    lo=_number(l[i] if i<len(l) else None)
                    close=_number(c[i] if i<len(c) else None)
                    volume=_number(v[i] if i<len(v) else None)
                    if None in (hi,lo,close) or hi<close or close<lo:continue
                    bars.append((int(ts),hi,lo,close,volume))
                if len(bars)>=16:
                    return sorted({x[0]:x for x in bars}.values())[-160:],"YAHOO_KRX_PUBLIC_CHART"
            except (OSError,ValueError,KeyError,IndexError,TypeError):
                continue
    return [],""


def record(root,market,strategy,symbol,frame,bars,origin,
           signals=(),now=None):
    if frame not in FRAME_SECONDS or not bars:return {"bars":0,"entries":0}
    now=int(time.time() if now is None else now)
    # GMO is one daily-strategy cohort, not a fabricated fifth timeframe.
    save_frame="gmo" if strategy=="gmo" and frame=="day" else frame
    valid=sorted((x for x in bars if x[0]+FRAME_SECONDS[frame]<=now),
                 key=lambda x:x[0])
    if not valid:return {"bars":0,"entries":0}
    valid=valid[-160:]  # recent complete source candles; older rows remain in DB
    indicators=compute_candle_indicators(valid)
    with _connect(root,market) as conn:
        for ts,hi,lo,close,volume in valid:
            conn.execute("""INSERT OR IGNORE INTO candle_observations
                (strategy,frame,symbol,bar_ts,high,low,close,volume,origin)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (strategy,save_frame,symbol,ts,hi,lo,close,volume,origin))
        if indicators:
            last=valid[-1][0]
            conn.execute("""INSERT INTO indicator_observations(
               strategy,frame,symbol,observed_minute,features_json,origin)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(strategy,frame,symbol,observed_minute)
               DO UPDATE SET features_json=excluded.features_json,
                             origin=excluded.origin""",
                (strategy,save_frame,symbol,last,
                 json.dumps(indicators,sort_keys=True),origin))
        reconstructed=0
        for signal in signals:
            if not isinstance(signal,dict):continue
            if str(signal.get("code") or signal.get("market") or "")!=symbol:continue
            if _frame(signal.get("tf") or signal.get("frame"),strategy)!=save_frame:continue
            event=_timestamp(signal.get("at") or signal.get("signal_ts") or
                             signal.get("created_at") or signal.get("event_date") or
                             signal.get("ts") or signal.get("ref_at") or signal.get("ref_ts"))
            if event is None:continue
            historical=[row for row in valid if row[0]+FRAME_SECONDS[frame]<=event]
            if len(historical)<21:continue
            evidence=compute_candle_indicators(historical)
            if not evidence:continue
            key=_key(signal,strategy,"signal")
            if not key:continue
            encoded=json.dumps(evidence,sort_keys=True,separators=(",",":"))
            digest=hashlib.sha256(encoded.encode("utf8")).hexdigest()[:24]
            conn.execute("""INSERT INTO feature_snapshots
                (strategy,frame,signal_key,event_ts,first_captured,last_seen,
                 features_sha,features_json,origin)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(strategy,signal_key,features_sha)
                DO UPDATE SET last_seen=excluded.last_seen,
                              origin='RECONSTRUCTED_PRE_ENTRY_OHLCV'""",
                (strategy,save_frame,key,event,now,now,digest,encoded,
                 "RECONSTRUCTED_PRE_ENTRY_OHLCV"))
            reconstructed+=1
    return {"bars":len(valid),"entries":reconstructed}


_LAST_ATTEMPT={}
_ROTATION=0


def refresh_packet(root,packet,now=None,max_calls=4):
    """Rotate across scopes, prioritizing open positions but no starvation."""
    global _ROTATION
    now=int(time.time() if now is None else now)
    work=[]
    data=packet if isinstance(packet,dict) else {}
    for market,strategy in (
        ("coin","general"),("coin","ma"),("stock","at"),("stock","ma"),
        ("stock","gmo"),("stock","close")):
        research,ledger=_records(data,market,strategy)
        signals=_rows(research,("items","candidates","signals"))
        holdings=_rows(ledger,("positions","holdings"))
        for source in (holdings,signals[:40]):
            for row in source:
                if not isinstance(row,dict):continue
                symbol=str(row.get("code") or row.get("market") or "")
                frame=_frame(row.get("tf") or row.get("frame"),strategy)
                if frame=="gmo":frame="day"
                if not symbol or frame not in FRAME_SECONDS:continue
                key=(market,strategy,symbol,frame)
                work.append((key,signals))
    unique={}
    for key,signals in work:unique.setdefault(key,signals)
    entries=list(unique.items())
    if entries:
        n=_ROTATION%len(entries)
        entries=entries[n:]+entries[:n]
        _ROTATION+=1
    count=0
    for key,signals in entries:
        if count>=max_calls:break
        last=_LAST_ATTEMPT.get(key,0)
        if now-last<300:continue
        _LAST_ATTEMPT[key]=now
        market,strategy,symbol,frame=key
        try:
            bars,origin=fetch_candles(market,symbol,frame,now)
            if len(bars)>=16:
                record(root,market,strategy,symbol,frame,bars,origin,signals,now)
            count+=1
        except (OSError,sqlite3.Error,ValueError,TypeError,KeyError):
            count+=1
    return count
