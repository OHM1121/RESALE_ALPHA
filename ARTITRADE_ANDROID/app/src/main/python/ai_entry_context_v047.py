"""Timestamp-anchored entry-candle evidence for real/virtual stock and coin fills.

Runs on the existing async AI research worker, NEVER the order or broker thread.
The ENTRY CANDLE is analyzed only after its closing OHLCV is available.
An entry-time strength observation must have a matching event timestamp.
No quote, volume, SMA or execution strength is fabricated or forward-filled.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import time
from pathlib import Path

import ai_research_v040 as journal

SCHEMA="ARTITRADE_ENTRY_CANDLE_V047"
PERIOD={"5m":300,"30m":1800,"60m":3600,"day":86400}
MOVING=(7,20,60,120)
SLOPE_WINDOW=30


def _numeric(value):
    return journal._number(value)


def _mean(values):
    return sum(values)/len(values) if values else None


def _regression(values):
    n=len(values)
    if n<2:return None
    # x = 0..n-1. Ordinary least squares, not peak picking.
    xbar=(n-1)/2
    den=n*(n*n-1)/12.0
    return sum((i-xbar)*value for i,value in enumerate(values))/den


def candle_context(bars,entry_ts,frame,now=None):
    """bar tuples (start_epoch,high,low,close,volume).

    Price/volume of the entry candle and moving-average states are NULL
    until that candle closes. Slopes use exactly the 30 CLOSED predecessor
    candles, excluding entry candle, and are normalized by entry close.
    """
    now=time.time() if now is None else float(now)
    length=PERIOD.get(frame)
    if length is None:raise ValueError("invalid entry timeframe")
    if entry_ts is None or entry_ts<=0:raise ValueError("invalid entry timestamp")
    unique={}
    for raw in bars:
        try:
            ts=int(raw[0])
            hi,lo,close=[float(raw[i]) for i in (1,2,3)]
            volume=_numeric(raw[4]) if len(raw)>4 else None
            if not all(math.isfinite(v) for v in (hi,lo,close)) or hi<close or close<lo or lo<=0:
                continue
            unique[ts]=(ts,hi,lo,close,volume)
        except (ValueError,TypeError,IndexError,OverflowError):continue
    ordered=sorted(unique.values())
    idx=next((i for i,r in enumerate(ordered) if r[0]<=entry_ts<r[0]+length),None)
    result={"schema":SCHEMA,"frame":frame,"entry_epoch":int(entry_ts),
            "ma_close_basis":"FINAL_ENTRY_CANDLE_CLOSE","ma_touch_rule":"EXACT_CLOSE_EQUALS_SMA",
            "slope_method":"OLS_PREVIOUS_30_CLOSED_BARS",
            "slope_normalization":"100*price_per_bar/entry_candle_close",
            "volume_method":"FINAL_ENTRY_CANDLE_VOLUME / MEAN_PREVIOUS_30_CLOSED_BARS",
            "entry_candle_complete":False,
            "state":"PENDING_BAR_FINALIZATION","source_bars":len(ordered),
            "high_slope_price_per_bar":None,"low_slope_price_per_bar":None,
            "high_slope_pct_per_bar":None,"low_slope_pct_per_bar":None,
            "prior30_volume_mean":None,"entry_volume":None,
            "volume_ratio30":None,"sma":{},
            "entry_candle_close":None}
    if idx is None:
        result["state"]="ENTRY_CANDLE_NOT_IN_SOURCE"
        return result
    chosen=ordered[idx]
    result["entry_bar_start_epoch"]=chosen[0]
    result["entry_bar_end_epoch"]=chosen[0]+length
    if now<chosen[0]+length:
        return result
    result["entry_candle_complete"]=True
    result["entry_candle_close"]=chosen[3]
    result["entry_volume"]=chosen[4]
    prev=ordered[:idx]
    prior30=prev[-30:]
    if len(prior30)==30 and all(r[0]+length<=chosen[0] for r in prior30):
        high=_regression([r[1] for r in prior30])
        low=_regression([r[2] for r in prior30])
        result["high_slope_price_per_bar"]=round(high,10)
        result["low_slope_price_per_bar"]=round(low,10)
        result["high_slope_pct_per_bar"]=round(high*100/chosen[3],8)
        result["low_slope_pct_per_bar"]=round(low*100/chosen[3],8)
        vols=[r[4] for r in prior30]
        if all(v is not None and v>=0 for v in vols):
            avg=_mean(vols)
            result["prior30_volume_mean"]=round(avg,8)
            if chosen[4] is not None and chosen[4]>=0 and avg>0:
                result["volume_ratio30"]=round(chosen[4]/avg,8)
    result["prior30_bars_available"]=len(prior30)
    # The entry candle's *final* close is compared with SMA including itself.
    # High/low intersecting an SMA is NOT a touch if the close differs.
    for width in MOVING:
        segment=ordered[:idx+1][-width:]
        if len(segment)!=width or not all(
                segment[j][0]+length<=segment[j+1][0]
                for j in range(len(segment)-1)):
            result["sma"][str(width)]={"period":width,"sma":None,
                "position":None,"reason":"INSUFFICIENT_CLOSED_BARS"}
            continue
        ma=_mean([r[3] for r in segment])
        close=chosen[3]
        # Floating-point rounding tolerance, NOT a made-up % touch band.
        tag=("TOUCH" if math.isclose(close,ma,rel_tol=1e-12,abs_tol=1e-12)
             else "ABOVE" if close>ma else "BELOW")
        result["sma"][str(width)]={"period":width,"sma":round(ma,10),
            "position":tag,"close_minus_sma":round(close-ma,10),
            "close_minus_sma_pct":round(100*(close/ma-1),8)}
    result["state"]="FINALIZED" if len(prior30)==30 else "PARTIAL_PRIOR_HISTORY"
    return result


def _candidate(row,strategy,role):
    symbol=str(row.get("code") or row.get("market") or "")
    frame=journal._frame(row.get("tf") or row.get("frame"),strategy)
    if frame=="gmo" or strategy=="gmo":frame="day"
    if strategy=="close":frame="day"
    if frame not in PERIOD or not symbol:return None
    mode=str(row.get("entry_mode") or row.get("mode") or "").upper()
    entry_mode="MARKET_TREND" if "마켓트렌드" in mode or "MARKET_TREND" in mode else "ORIGINAL"
    # Source row's BUY event time, not analysis time or a reference candle date.
    fields=("entry_at","entry_ts","buy_at","filled_at","buy_ts",
            "virtual_buy_at","first_observed_limit_touch") if role=="ACTUAL" else (
            "virtual_buy_at","first_observed_limit_touch","shadow_entry_at","at","ts")
    when=next((journal._timestamp(row.get(k)) for k in fields
               if journal._timestamp(row.get(k)) is not None),None)
    if when is None:return None
    uid=str(row.get("event_key") or row.get("id") or row.get("position_id") or
            row.get("signal_id") or row.get("candidate_id") or "")
    # Timestamp makes repeat fills in the same instrument distinguishable.
    key="|".join([role,entry_mode,strategy,frame,symbol,str(int(when)),uid])
    strength=_numeric(next((row.get(k) for k in (
        "execution_strength","trade_strength","chegyeol_strength","체결강도")
        if row.get(k) is not None),None))
    strength_time=journal._timestamp(row.get("execution_strength_at") or
                                    row.get("trade_strength_at"))
    # No timestamp means it cannot be asserted to be an ENTRY-time sample.
    timely=bool(strength is not None and strength_time is not None and
                abs(strength_time-when)<=3)
    return {"key":key,"role":role,"market":None,"strategy":strategy,
            "entry_mode":entry_mode,"frame":frame,"symbol":symbol,"entry_ts":int(when),
            "entry_strength":strength if timely else None,
            "entry_strength_source":str(row.get("execution_strength_source") or
                                       row.get("trade_strength_source") or "SOURCE_TICK")
                                       if timely else "NOT_TIMESTAMPED_OR_UNAVAILABLE",
            "entry_strength_epoch":int(strength_time) if timely else None}


def _ensure(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS entry_candle_context_v047(
      strategy TEXT NOT NULL,evidence_key TEXT NOT NULL,
      role TEXT NOT NULL,entry_mode TEXT NOT NULL,
      frame TEXT NOT NULL,symbol TEXT NOT NULL,entry_ts INTEGER NOT NULL,
      state TEXT NOT NULL,source TEXT,context_json TEXT NOT NULL,
      first_seen INTEGER NOT NULL,updated_at INTEGER NOT NULL,
      PRIMARY KEY(strategy,evidence_key)
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS ix_entry_context_cohort_v047
      ON entry_candle_context_v047(strategy,entry_mode,frame,entry_ts)""")


def _upsert(conn,event,context,source,now):
    _ensure(conn)
    context=dict(context)
    context["entry_strength_pct"]=event.get("entry_strength")
    context["entry_strength_source"]=event.get("entry_strength_source")
    context["entry_strength_epoch"]=event.get("entry_strength_epoch")
    encoded=json.dumps(context,ensure_ascii=False,separators=(",",":"),
                       allow_nan=False)
    conn.execute("""INSERT INTO entry_candle_context_v047(
      strategy,evidence_key,role,entry_mode,frame,symbol,entry_ts,state,
      source,context_json,first_seen,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(strategy,evidence_key) DO UPDATE SET
        state=CASE WHEN entry_candle_context_v047.state='FINALIZED'
                   AND excluded.state!='FINALIZED'
             THEN entry_candle_context_v047.state ELSE excluded.state END,
        context_json=CASE WHEN entry_candle_context_v047.state='FINALIZED'
                   AND excluded.state!='FINALIZED'
             THEN entry_candle_context_v047.context_json ELSE excluded.context_json END,
        source=CASE WHEN entry_candle_context_v047.state='FINALIZED'
                   AND excluded.state!='FINALIZED'
             THEN entry_candle_context_v047.source ELSE excluded.source END,
        updated_at=excluded.updated_at
      WHERE entry_candle_context_v047.state!='FINALIZED'
        AND (entry_candle_context_v047.state<>excluded.state
             OR excluded.state IN ('FINALIZED','PARTIAL_PRIOR_HISTORY'))""",
        (event["strategy"],event["key"],event["role"],event["entry_mode"],
         event["frame"],event["symbol"],event["entry_ts"],context["state"],
         source,encoded,int(now),int(now)))


def capture_events(root,market,strategy,signals=(),positions=(),bars=(),
                   symbol=None,frame=None,source="NOT_AVAILABLE",now=None):
    """Save pending keys even without bars, finalize only with historical OHLCV."""
    if market not in ("stock","coin"):return 0
    now=int(time.time() if now is None else now)
    rows=[]
    for row in positions:
        if isinstance(row,dict):
            item=_candidate(row,strategy,"ACTUAL")
            if item:rows.append(item)
    for row in signals:
        if not isinstance(row,dict):continue
        # Research-only nonbuy candidate is not a simulated fill.
        if row.get("bought") or row.get("actually_bought"):
            role="ACTUAL"
        elif (row.get("entry_observed") or row.get("virtual_entered") or
              row.get("shadow_entered") or
              (row.get("virtual_buy_at") and row.get("virtual_buy_evidence")) or
              str(row.get("entry_mode","")).upper() in ("MARKET_TREND","마켓트렌드")
              and str(row.get("id","")).startswith("MT43:SIG:")):
            role="VIRTUAL"
        else:continue
        item=_candidate(row,strategy,role)
        if item:rows.append(item)
    if not rows:return 0
    count=0
    with journal._connect(root,market) as conn:
        _ensure(conn)
        for event in rows:
            if symbol is not None and event["symbol"]!=symbol:continue
            if frame is not None and event["frame"]!=frame:continue
            context=candle_context(bars,event["entry_ts"],event["frame"],now)
            _upsert(conn,event,context,source,now)
            count+=1
    return count


def captured(root,market,entry_mode=None):
    path=journal.db_path(root,market)
    if not path.is_file():return []
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True) as conn:
        conn.row_factory=sqlite3.Row
        if not _ensure_available(conn):return []
        where="WHERE entry_mode=?" if entry_mode else ""
        params=(entry_mode,) if entry_mode else ()
        return [dict(row,context=json.loads(row["context_json"]))
                for row in conn.execute(
                    "SELECT * FROM entry_candle_context_v047 "+where+
                    " ORDER BY entry_ts DESC",params)]


def _ensure_available(conn):
    return bool(conn.execute("""SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='entry_candle_context_v047'""").fetchone())


def summarize(root,market,entry_mode=None):
    rows=captured(root,market,entry_mode)
    counts={}
    by_frame={}
    for row in rows:
        counts[row["state"]]=counts.get(row["state"],0)+1
        by_frame[row["frame"]]=by_frame.get(row["frame"],0)+1
    return {"schema":SCHEMA,"market":market,"entry_mode":entry_mode,
            "entry_events":len(rows),"status_counts":counts,"frame_counts":by_frame,
            "confirmed_ma_decisions":sum(1 for row in rows if
                row["context"].get("entry_candle_complete")),
            "strength_captured":sum(1 for row in rows if
                row["context"].get("entry_strength_pct") is not None),
            "all_evidence_in_original_ai_sqlite":True}


def capture_packet_pending(root,packet):
    """Durably remember real/virtual fills even before candle-network fetch.

    Pure research sidecar; only confirmed BUY or simulated-entry rows qualify.
    Later OHLCV responses finalize SMA/volume without time leakage.
    """
    data=journal._json_obj(packet)
    total=0
    for market,strategy in journal.SCOPES:
        signals,ledger=journal._records(data,market,strategy)
        rows=journal._rows(signals,("items","candidates","signals"))
        holdings=journal._rows(ledger,("positions","holdings"))
        try:
            total+=capture_events(root,market,strategy,
                     signals=rows,positions=holdings,source="PENDING_OHLCV")
        except (OSError,sqlite3.Error,ValueError,TypeError):
            continue
    return total


def export_rows(root,market,entry_mode=None,max_preview=600):
    """Small text index; every unabridged context remains in original SQLite."""
    rows=captured(root,market,entry_mode)
    result={
        "schema":SCHEMA,"market":market,"entry_mode":entry_mode,
        "total_rows":len(rows),"preview_n":min(len(rows),max_preview),
        "preview_limited":len(rows)>max_preview,
        "full_table":"<market>/ai/AI_RESEARCH_V040.sqlite3::entry_candle_context_v047",
        "meaning":"all numeric evidence is authoritative ONLY after candle finalizes",
        "rows":[{
            "strategy":r["strategy"],"evidence_key":r["evidence_key"],
            "role":r["role"],"entry_mode":r["entry_mode"],
            "frame":r["frame"],"symbol":r["symbol"],
            "entry_epoch":r["entry_ts"],"state":r["state"],
            "source":r["source"],"context":r["context"]}
            for r in rows[:max_preview]]
    }
    return json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)
