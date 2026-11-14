"""ARTITRADE v042 private per-position trade lifecycle research.

All data stays inside the two market-scoped AI_RESEARCH_V040.sqlite3 journals
that research ZIP exports already include. Neither order state nor source
strategy SQLite is edited. Indicator data at BUY/SELL is derived from bars
already CLOSED before those times; unknown values stay null.

Every covered position keeps entry, exit, holding means, quote trajectory,
market-score interval, adverse/favorable excursion, sell reason and provenance.
This is a research feature archive, not a user-visible telemetry panel.
"""
from __future__ import annotations

import bisect
import json
import math
import sqlite3
import statistics
import time
from collections import defaultdict
from pathlib import Path

import ai_research_v040 as journal

BAR_SECONDS={"5m":300,"30m":1800,"60m":3600,"day":86400,"gmo":86400}
MEASURE_KEYS=(
    "atr","atr_pct","rsi","williams_r","volume","volume_ratio",
    "vwap","obv","mfi","adx","macd","bollinger_bandwidth_pct",
    "spread_pct","gap_pct","bid_imbalance","market_volatility",
    "market_breadth","return_5bars_pct","foreign_net_buy",
    "institution_net_buy","trade_value","orderbook_depth","score",
)
MARKET_TTL={"coin":180,"stock":600}


def _identity(row,strategy):
    return journal._key(row,strategy,"position")


def _when(row,fields):
    for field in fields:
        stamp=journal._timestamp(row.get(field))
        if stamp is not None:return stamp
    return None


def _price(row,fields):
    for f in fields:
        result=journal._number(row.get(f))
        if result is not None:return result
    return None


def _asof_scores(root,market,conn):
    events={}
    try:
        for ts,v in conn.execute(
            "SELECT score_ts,score FROM market_score_observations"):
            if v is not None:events[int(ts)]=float(v)
    except sqlite3.Error:pass
    path=Path(root)/"market_research_v033.sqlite3"
    if path.is_file():
        try:
            with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                uri=True,timeout=3) as orig:
                for ts,v in orig.execute(
                    "SELECT ts,score FROM score_events WHERE market=?",(market,)):
                    if v is not None:events[int(ts)]=float(v)
        except (OSError,sqlite3.Error):pass
    return sorted(events.items())


def _market_at(events,moment,market):
    if moment is None or not events:return None
    times=[x[0] for x in events]
    idx=bisect.bisect_right(times,int(moment))-1
    if idx<0 or int(moment)-times[idx]>MARKET_TTL[market]:return None
    return events[idx][1]


def _market_average(events,entry,end,market):
    if entry is None or end is None or end<=entry:return None,0,0
    limit=MARKET_TTL[market]
    weighted=seconds=0.0
    observations=0
    # Actual score-time coverage, do not invent overnight/weekend values.
    for i,(ts,score) in enumerate(events):
        if ts>=end:break
        start=max(entry,ts)
        after=min(end,ts+limit,events[i+1][0] if i+1<len(events) else end)
        duration=max(0,after-start)
        if duration:
            weighted+=score*duration
            seconds+=duration
            observations+=1
    return (round(weighted/seconds,4) if seconds else None,
            round(seconds,2),observations)


def _derive_bars(candles,ts,frame,market):
    """Strictly pre-event closed bars, never in-progress or future candles."""
    if ts is None:return {},0
    period=BAR_SECONDS.get(frame)
    if period is None:return {},0
    eligible=[r for r in candles if r["bar_ts"]+period<=ts]
    if len(eligible)<21:return {},len(eligible)
    vals=[(int(r["bar_ts"]),r["high"],r["low"],r["close"],r["volume"])
          for r in eligible[-110:]]
    result=journal.compute_candle_indicators(vals)
    if vals[-1][4] is not None:result["volume"]=vals[-1][4]
    return result,len(eligible)


def _snapshot_at(observations,moment,frame):
    if moment is None:return {},None
    period=BAR_SECONDS.get(frame,60)
    matched=[row for row in observations
             if row["observed_minute"]<=moment
             and moment-row["observed_minute"]<=min(1800,max(60,period))]
    if not matched:return {},None
    last=max(matched,key=lambda x:x["observed_minute"])
    return journal._json_obj(last["features_json"]),last["origin"]


def _rollup(values):
    total=defaultdict(float)
    counts=defaultdict(int)
    for features in values:
        for field,value in features.items():
            n=journal._number(value)
            if n is None:continue
            total[field]+=n;counts[field]+=1
    return {k:round(v/counts[k],6) for k,v in total.items() if counts[k]>0},dict(counts)


def _series_outcomes(positions,entry_price,entry_ts,exit_ts):
    states=[r for r in positions if (entry_ts is None or r["observed_minute"]>=entry_ts)
            and (exit_ts is None or r["observed_minute"]<=exit_ts)]
    states.sort(key=lambda r:r["observed_minute"])
    gains=[]
    prices=[]
    for row in states:
        mark=journal._number(row["mark"])
        if mark is not None and mark>0:
            prices.append(mark)
            if entry_price and entry_price>0:
                gains.append(100*(mark/entry_price-1))
    running_peak=entry_price if entry_price and entry_price>0 else 0
    drawdowns=[]
    for price in prices:
        running_peak=max(running_peak,price)
        if running_peak>0:drawdowns.append(100*(price/running_peak-1))
    return {
        "observed_marks":len(prices),
        "max_favorable_pct":round(max(gains),4) if gains else None,
        "max_adverse_pct":round(min(gains),4) if gains else None,
        "peak_to_trough_pct":round(min(drawdowns),4) if drawdowns else None,
        "mark_first":prices[0] if prices else None,
        "mark_last":prices[-1] if prices else None,
    }


def _rollup_one(root,market,strategy,row,conn,events,candles_cache,inds_cache,
                positions_cache,now,fill_exit=None):
    key=_identity(row,strategy)
    if not key:return None
    frame=journal._frame(row.get("tf") or row.get("frame"),strategy)
    if frame not in BAR_SECONDS:return None
    symbol=str(row.get("code") or row.get("market") or "")
    if not symbol:return None
    entry_ts=_when(row,("entry_at","entry_ts","buy_at","buy_ts","filled_at","time"))
    if entry_ts is None:
        # A calendar-only date is NOT an executable trade timestamp.
        return None
    state=str(row.get("status") or row.get("state") or "UNKNOWN").upper()
    closed=state in ("CLOSED","DONE","SOLD","FILLED_EXIT")
    exit_ts=_when(row,("exit_at","exit_ts","closed_at","sold_at","sell_at"))
    if exit_ts is None and closed and fill_exit is not None:exit_ts=fill_exit
    if not closed:exit_ts=None
    bound=exit_ts if exit_ts is not None else now
    if bound<entry_ts:return None
    entry_price=_price(row,("entry_price","entry","buy_price","avg_buy_price"))
    symbolframe=(frame,symbol)
    if symbolframe not in candles_cache:
        candles_cache[symbolframe]=[
            dict(x) for x in conn.execute(
                "SELECT bar_ts,high,low,close,volume,origin "
                "FROM candle_observations WHERE strategy=? AND frame=? "
                "AND symbol=? ORDER BY bar_ts",
                (strategy,frame,symbol))]
    candles=candles_cache[symbolframe]
    if symbolframe not in inds_cache:
        inds_cache[symbolframe]=[
            dict(x) for x in conn.execute(
                "SELECT observed_minute,features_json,origin FROM "
                "indicator_observations WHERE strategy=? AND frame=? "
                "AND symbol=? ORDER BY observed_minute",
                (strategy,frame,symbol))]
    indicators=inds_cache[symbolframe]
    if key not in positions_cache:
        positions_cache[key]=[
            dict(x) for x in conn.execute(
                "SELECT observed_minute,mark,entry_price,market_score,extra_json "
                "FROM position_observations WHERE strategy=? AND position_key=? "
                "AND observed_minute BETWEEN ? AND ? ORDER BY observed_minute",
                (strategy,key,entry_ts,bound))]
    state_rows=positions_cache[key]
    entry_features,entry_bars=_derive_bars(candles,entry_ts,frame,market)
    exit_features,exit_bars=_derive_bars(candles,exit_ts,frame,market)
    entry_origin="PRE_ENTRY_CLOSED_OHLCV" if entry_features else None
    exit_origin="PRE_EXIT_CLOSED_OHLCV" if exit_features else None
    # Exact-time recorded indicators are safe only if they were observed
    # before BUY/SELL. Never use current indicators as historic entries.
    if not entry_features:
        entry_features,entry_origin=_snapshot_at(indicators,entry_ts,frame)
    if exit_ts is not None and not exit_features:
        exit_features,exit_origin=_snapshot_at(indicators,exit_ts,frame)
    # Non-time-stamped row fields must not be backdated as BUY indicators.
    hold_samples=[]
    for indicator in indicators:
        when=indicator["observed_minute"]
        if entry_ts<=when<=bound:
            hold_samples.append(journal._json_obj(indicator["features_json"]))
    interval_bars=[r for r in candles if entry_ts<=r["bar_ts"]+BAR_SECONDS[frame]<=bound]
    bar_volumes=[r["volume"] for r in interval_bars if r["volume"] is not None]
    for position in state_rows:
        features=journal._json_obj(position["extra_json"])
        if features:hold_samples.append(features)
    # Position-bound factor observations include spread, depth, market score,
    # technical snapshots and other real-time indicators not in public candles.
    live_events=[dict(x) for x in conn.execute("""
        SELECT observed_minute,features_json,market_score
        FROM position_factor_events_v042 WHERE strategy=? AND position_key=?
        AND observed_minute BETWEEN ? AND ? ORDER BY observed_minute""",
        (strategy,key,entry_ts,bound))]
    for fact in live_events:
        values=journal._json_obj(fact.get("features_json"))
        if fact["market_score"] is not None:
            values["market_score"]=fact["market_score"]
        if values:hold_samples.append(values)
    averages,feature_counts=_rollup(hold_samples)
    # Average volume is a genuine per-bar mean, not an average of a mean
    # with event-side tick-volume observations.
    if bar_volumes:
        averages["volume"]=round(sum(bar_volumes)/len(bar_volumes),6)
        feature_counts["volume"]=len(bar_volumes)
    market_entry=_market_at(events,entry_ts,market)
    market_exit=_market_at(events,exit_ts,market)
    market_avg,market_covered_seconds,market_points=_market_average(
        events,entry_ts,bound,market)
    trajectories=_series_outcomes(state_rows,entry_price,entry_ts,exit_ts)
    first=state_rows[0] if state_rows else None
    last=state_rows[-1] if state_rows else None
    record={
        "entry_features":entry_features,
        "exit_features":exit_features if exit_ts is not None else None,
        "holding_mean":averages,
        "feature_counts":feature_counts,
        "market_entry":market_entry,"market_exit":market_exit,
        "market_holding_mean":market_avg,
        "market_coverage_seconds":market_covered_seconds,
        "market_points":market_points,
        "entry_feature_origin":entry_origin,"exit_feature_origin":exit_origin,
        "entry_bar_count":entry_bars,"exit_bar_count":exit_bars,
        "bar_observations":len(interval_bars),
        "indicator_observations":len(hold_samples),
        "position_factor_events":len(live_events),
        "volume_observations":len(bar_volumes),
        "start_price":entry_price,
        "duration_seconds":max(0,bound-entry_ts),
        "state":state,"closed":closed,"entry_ts":entry_ts,"exit_ts":exit_ts,
        "hold_return_first":(
            round(100*(first["mark"]/entry_price-1),4) if first and entry_price
            and first["mark"] is not None and entry_price>0 else None),
        "hold_return_last":(
            round(100*(last["mark"]/entry_price-1),4) if last and entry_price
            and last["mark"] is not None and entry_price>0 else None),
        "entry_time_captured":bool(entry_features),"exit_time_captured":bool(exit_features)
            if closed else False,
        **trajectories,
    }
    return (key,frame,symbol,entry_ts,exit_ts,record)


def rebuild(root,market,strategy,positions,now=None,executions=()):
    """Recalculate one strategy at analysis click using all known evidence.

    Avoids attributing data from same-ticker other trades. Missing fields are
    saved explicitly as JSON null. Uses immutable original strategy positions.
    """
    if (market,strategy) not in journal.SCOPES:return []
    now=int(now or time.time())
    with journal._connect(root,market) as conn:
        events=_asof_scores(root,market,conn)
        filled_exits={}
        for fill in executions or ():
            if not isinstance(fill,dict):continue
            if str(fill.get("side") or "").upper() not in ("SELL","S"):continue
            id_=fill.get("position_id")
            stamp=_when(fill,("fill_at","filled_at","time","ts","at","sell_at"))
            if id_ is not None and stamp is not None:
                filled_exits[str(id_)]=max(stamp,filled_exits.get(str(id_),0))
        candles_cache={};inds_cache={};positions_cache={}
        output=[]
        latest={}
        # Same position can be represented by original EXE and a phone snapshot.
        for row in positions or ():
            if not isinstance(row,dict):continue
            key=_identity(row,strategy)
            if key is None:continue
            existing=latest.get(key)
            if existing is None:
                latest[key]=dict(row)
            else:
                # Preserve exit metadata when an older phone snapshot says OPEN.
                for k,v in row.items():
                    if v is None:continue
                    if existing.get(k) is None:existing[k]=v
                if str(row.get("status") or "").upper() in (
                        "CLOSED","DONE","SOLD","FILLED_EXIT"):
                    existing["status"]=row["status"]
                    for k,v in row.items():
                        if k.endswith("at") or k.endswith("ts") or k.startswith("exit_"):
                            if v is not None:existing[k]=v
        for row in list(latest.values())[:20000]:
            result=_rollup_one(root,market,strategy,row,conn,events,
                               candles_cache,inds_cache,positions_cache,now,
                               filled_exits.get(str(row.get("id"))))
            if not result:continue
            key,frame,symbol,entry,exit_ts,record=result
            serialized=json.dumps(record,ensure_ascii=False,
                                  separators=(",",":"),allow_nan=False)
            conn.execute("""INSERT INTO position_lifecycle_v042(
                strategy,position_key,frame,symbol,entry_ts,exit_ts,
                last_computed,summary_json)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(strategy,position_key) DO UPDATE SET
                    frame=excluded.frame,symbol=excluded.symbol,
                    entry_ts=excluded.entry_ts,exit_ts=excluded.exit_ts,
                    last_computed=excluded.last_computed,
                    summary_json=excluded.summary_json""",
                (strategy,key,frame,symbol,entry,exit_ts,now,serialized))
            output.append(dict(record,position_key=key,frame=frame,symbol=symbol))
        return output


def merge_original_signal_features(root,market,strategy,lifecycles,trades):
    """Source candidate from the EXACT matched signal, never a later candle.

    Signal-time indicators can serve as initial pre-buy evidence, but their
    timestamps and provenance are explicit; no fabricated trade-time reading.
    When closed candles genuinely establish buy-time values they take priority.
    """
    matched={}
    for trade in trades:
        if not isinstance(trade,dict):continue
        original=trade.get("raw") or {}
        key=original.get("position_key") or journal._key(
            original,strategy,"position")
        if key is None:continue
        signal_ts=trade.get("signal_ts")
        if signal_ts is None:continue
        facts={k:journal._number(v) for k,v in
               (trade.get("features") or {}).items() if k in MEASURE_KEYS}
        facts={k:v for k,v in facts.items() if v is not None}
        if facts:matched[key]=(int(signal_ts),facts)
    with journal._connect(root,market) as conn:
        for row in lifecycles:
            item=matched.get(row.get("position_key"))
            if item is None:continue
            signal_ts,facts=item
            entry=row.get("entry_ts")
            if entry is None or signal_ts>entry or entry-signal_ts>30*86400:
                continue
            src=row.get("entry_features") or {}
            missing={k:v for k,v in facts.items()
                     if k not in src and k!="market_score"}
            if not missing:continue
            merged=dict(src,**missing)
            row["entry_features"]=merged
            row["entry_time_captured"]=True
            row["entry_feature_origin"]=(
                (row.get("entry_feature_origin") or "")+
                ("+" if row.get("entry_feature_origin") else "")+
                "MATCHED_PRE_ENTRY_SIGNAL")
            conn.execute(
                "UPDATE position_lifecycle_v042 SET summary_json=? "
                "WHERE strategy=? AND position_key=?",
                (json.dumps({k:v for k,v in row.items()
                            if k not in ("position_key","frame","symbol")},
                            ensure_ascii=False,separators=(",",":"),allow_nan=False),
                 strategy,row["position_key"]))
    return lifecycles


def existing(root,market,strategy):
    path=journal.db_path(root,market)
    if not path.exists():return []
    try:
        with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                             uri=True,timeout=4) as conn:
            rows=conn.execute(
                "SELECT position_key,frame,symbol,summary_json "
                "FROM position_lifecycle_v042 WHERE strategy=?",(strategy,)).fetchall()
        return [dict(json.loads(row[3]),position_key=row[0],frame=row[1],symbol=row[2])
                for row in rows]
    except (sqlite3.Error,OSError,ValueError,TypeError):
        return []


def event_record(conn,strategy,frame,key,symbol,when,entry_ts,exit_ts,
                 status,market_score,price,volume,features,source):
    """Idempotent observed-timestamp row; one per position/time."""
    conn.execute("""INSERT INTO position_factor_events_v042
        (strategy,position_key,frame,symbol,observed_minute,entry_ts,exit_ts,
         status,price,volume,market_score,features_json,source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(strategy,position_key,observed_minute)
        DO UPDATE SET price=excluded.price,volume=excluded.volume,
            market_score=excluded.market_score,features_json=excluded.features_json,
            status=excluded.status,exit_ts=excluded.exit_ts""",
        (strategy,key,frame,symbol,int(when)//60*60,entry_ts,exit_ts,status,
         price,volume,market_score,
         json.dumps(features,sort_keys=True,separators=(",",":")),source))


def captured_event_features(conn,strategy,frame,symbol,now):
    # Strictly previous completed candle indicators and their observation time.
    # Never attach signal-time values from a future update.
    latest=conn.execute("""
        SELECT observed_minute,features_json,origin
        FROM indicator_observations
        WHERE strategy=? AND frame=? AND symbol=? AND observed_minute<=?
        ORDER BY observed_minute DESC LIMIT 1""",
        (strategy,frame,symbol,int(now))).fetchone()
    if latest is None:return {},None
    if now-int(latest[0])>min(1800,max(300,BAR_SECONDS.get(frame,300)*2)):
        return {},None
    return journal._json_obj(latest[1]),latest[2]
