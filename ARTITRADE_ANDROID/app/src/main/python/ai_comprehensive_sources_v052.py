"""Source-specific, point-in-time adapters for the v052 comprehensive research sidecar.

Only records linked to one strategy, timeframe, symbol and (where available)
position ID are attached. Entries after the buy never become buy-time causes.
"""
from __future__ import annotations
import bisect
import json
import math
import statistics
from collections import defaultdict

PERIOD={"5m":300,"30m":1800,"60m":3600,"day":86400,"gmo":86400}
MAX_CANDLES_PER_CASE=1500
MAX_FACTORS_PER_WINDOW=50


def _numeric(v):
    if isinstance(v,bool) or v is None:return None
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError,OverflowError):return None


def _j(v):
    if isinstance(v,dict):return v
    if isinstance(v,str):
        try:
            r=json.loads(v)
            return r if isinstance(r,dict) else {}
        except (TypeError,ValueError):pass
    return {}


def _candle_view(rows,at,seconds,count=35):
    """Strictly completed OHLCV bars before event time, sorted chronologically."""
    eligible=[]
    for r in rows:
        t=_numeric(r.get("bar_ts"))
        hi=_numeric(r.get("high"));lo=_numeric(r.get("low"))
        cl=_numeric(r.get("close"));v=_numeric(r.get("volume"))
        if None in (t,hi,lo,cl) or t+seconds>at:
            continue
        if hi<cl or cl<lo or lo<0:continue
        eligible.append((int(t),hi,lo,cl,v))
    eligible.sort(key=lambda x:x[0])
    return eligible[-count:]


def _summary_bars(bars):
    if not bars:return {}
    close=[x[3] for x in bars]
    vol=[x[4] for x in bars if x[4] is not None]
    first=close[0]
    result={"observed_bars":len(bars),"last_closed_price":close[-1],
            "high_lookback":max(x[1] for x in bars),
            "low_lookback":min(x[2] for x in bars)}
    if first>0:
        result["price_move_pct"]=100*(close[-1]/first-1)
    if len(close)>=5:
        result["sma5"]=statistics.mean(close[-5:])
    if len(close)>=20:
        result["sma20"]=statistics.mean(close[-20:])
        if result["sma20"]>0:
            result["price_to_sma20_pct"]=100*(close[-1]/result["sma20"]-1)
    if vol:
        result["volume_mean"]=statistics.mean(vol)
        result["volume_last"]=vol[-1]
        if len(vol)>=5 and statistics.mean(vol[-5:])>0:
            result["last_volume_vs_recent5"]=vol[-1]/statistics.mean(vol[-5:])
    return result


def _metric(root_case,phase,features,source,ts,add):
    if not isinstance(features,dict) or not features:return False
    start=len(root_case["features"])
    add(root_case,phase,features,source,ts)
    return len(root_case["features"])>start


def ingest(case,sidecars,add,stamp,origin_mode="ORIGINAL"):
    """Attach every indexed source family and return exact source-row hits.

    Evidence that cannot be proved attributable to the case is counted missing,
    not guessed from same-ticker historical state.
    """
    entry=case.get("entry_ts")
    frame=case.get("frame")
    symbol=case.get("symbol")
    identity=case.get("source_id")
    if entry is None or not frame or not symbol:
        case["missing"]["V052_SOURCE_LINK_ENTRY_ID_OR_CLOCK_MISSING"]+=1
        return {}
    hits=defaultdict(int)
    def found(label):
        block=sidecars.get(label) or {}
        return (block.get("groups") or {}).get((frame,symbol),())
    end=int(case.get("exit_ts") or entry+10**9)
    period=PERIOD.get(frame)
    # Completed bars are safe as-of observations. After-entry extremes must
    # never be re-used as entry predictors, including the entry candle itself.
    if period:
        candles=found("ohlcv_history")
        before=_candle_view(candles,entry,period,35)
        if _metric(case,"entry",_summary_bars(before),
                   "V052_COMPLETED_PRE_ENTRY_OHLCV",entry,add):
            hits["ohlcv_history"]+=len(before)
        held=[x for x in _candle_view(candles,min(end,int(10**10)),
                    period,MAX_CANDLES_PER_CASE)
              if x[0]>=entry and x[0]+period<=end]
        if held:
            prices={"high":max(x[1] for x in held),
                    "low":min(x[2] for x in held),
                    "last_close":held[-1][3],"bars":len(held)}
            if _metric(case,"holding",prices,"V052_OBSERVED_HOLDING_OHLCV",
                       held[-1][0]+period,add):
                hits["ohlcv_history"]+=len(held)
    # Exact-time indicator observations are treated as contemporaneous only
    # where the source explicitly marks reconstruction from PRE-ENTRY bars.
    indicators=list(found("indicator_history"))
    if indicators:
        indicators.sort(key=lambda x:int(x.get("observed_minute") or 0))
        prior=[x for x in indicators if 0<int(x.get("observed_minute") or 0)<=entry]
        if prior:
            row=prior[-1]
            origin=str(row.get("origin") or "").upper()
            phase=("entry" if "PRE_ENTRY" in origin or "CLOSED" in origin
                   else "unverified")
            if _metric(case,phase,_j(row.get("features_json")),
                       "V052_INDICATOR_"+origin,row.get("observed_minute"),add):
                hits["indicator_history"]+=1
        after=[x for x in indicators if entry<int(x.get("observed_minute") or 0)<=end]
        # To preserve temporal shape without exploding the per-case output,
        # retain FIRST/LAST + mean of all numerically recorded holding samples.
        if after:
            series=defaultdict(list)
            for row in after:
                for name,value in _j(row.get("features_json")).items():
                    n=_numeric(value)
                    if n is not None:series[name].append(n)
            result={"sample_count":len(after)}
            result.update({name+"_mean":statistics.mean(vals)
                           for name,vals in series.items() if vals})
            if _metric(case,"holding",result,"V052_HOLDING_INDICATOR_SERIES",
                       after[-1].get("observed_minute"),add):
                hits["indicator_history"]+=len(after)
    # Position factor events are stored separately from the original ledger.
    factors=list(found("holding_events"))
    if factors:
        linked=[]
        for row in factors:
            pkey=str(row.get("position_key") or "")
            if identity and identity!=pkey:
                continue
            if not identity:
                # Without an exact ID, use only the unique matching observed
                # entry timestamp attached by the source.
                if stamp(row.get("entry_ts"))!=entry:continue
            at=stamp(row.get("observed_minute"))
            if at is None or at<entry or at>end:continue
            linked.append((at,row))
        linked.sort(key=lambda x:x[0])
        if linked:
            series=defaultdict(list)
            for at,row in linked:
                for name,value in _j(row.get("features_json")).items():
                    n=_numeric(value)
                    if n is not None:series[name].append(n)
            nums={"observations":len(linked)}
            nums.update({k+"_mean":statistics.mean(v)
                         for k,v in series.items() if v})
            if _metric(case,"holding",nums,"V052_POSITION_FACTOR_EVENTS",
                       linked[-1][0],add):hits["holding_events"]+=len(linked)
    # Snapshot records are historical, but only a source stamped BEFORE entry
    # (or reconstructed exclusively from completed pre-entry bars) qualifies.
    snaps=sidecars.get("signal_history") or {}
    for group in (snaps.get("groups") or {}).values():
        for row in group[:MAX_FACTORS_PER_WINDOW]:
            if row.get("frame")!=frame:continue
            t=stamp(row.get("event_ts"))
            if t is None or abs(t-entry)>30*86400:continue
            key=str(row.get("signal_key") or "")
            if symbol not in key.split("|"):continue
            first=stamp(row.get("first_captured"))
            reconstructed=(row.get("origin")=="RECONSTRUCTED_PRE_ENTRY_OHLCV")
            phase="entry" if (reconstructed or first is not None and
                               t<=entry and first<=entry) else "unverified"
            if _metric(case,phase,_j(row.get("features_json")),
                       "V052_SAVED_SIGNAL_"+str(row.get("origin") or "UNKNOWN"),
                       first if first is not None else t,add):
                hits["signal_history"]+=1
    # Shared market conditions apply to all symbols but only at their own
    # timestamp, never use the next event to fill an earlier entry.
    scores=sidecars.get("market_score_history") or {}
    observations=list(scores.get("unscoped") or ())
    if observations:
        observations.sort(key=lambda x:float(x.get("score_ts") or 0))
        prior=[x for x in observations if
               stamp(x.get("score_ts")) is not None and
               0<=entry-stamp(x.get("score_ts"))<=900]
        if prior:
            row=prior[-1]
            if _metric(case,"entry",{"market_score":row.get("score")},
                       "V052_POINT_IN_TIME_MARKET_SCORE",
                       row.get("score_ts"),add):hits["market_score_history"]+=1
    # Benchmark data is a separate observed contextual series. Its ID is
    # retained; it is never recast as the traded asset's own P&L.
    bench=sidecars.get("benchmark_history") or {}
    benchmark=case.get("market_id")
    bseries=(bench.get("groups") or {}).get((frame,str(benchmark or "")),())
    if bseries and period:
        window=_candle_view(
            [dict(bar_ts=x["bar_ts"],high=x.get("close"),low=x.get("close"),
                  close=x.get("close")) for x in bseries],entry,period,32)
        if _metric(case,"entry",_summary_bars(window),
                   "V052_BENCHMARK_"+str(benchmark),entry,add):
            hits["benchmark_history"]+=len(window)
    # FULL V048 SELL list must be matched via source evidence key, not ticker.
    ekey=case.get("approved_evidence_key")
    if ekey:
        ex=sidecars.get("exit_events") or {}
        rows=(ex.get("by_evidence_key") or {}).get(str(ekey),())
        for row in rows[:150]:
            at=stamp(row.get("at_ms"))
            if at is None or at<entry:continue
            if end!=entry+10**9 and at>end+300:continue
            if _metric(case,"exit",_j(row.get("payload")),
                       "V052_CONFIRMED_POSITION_EXIT_EVENT",
                       row.get("at_ms"),add):
                hits["exit_events"]+=1
    return dict(hits)
