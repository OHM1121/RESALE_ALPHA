"""v052 trade-centric, evidence-gated analysis over all available local source families.

Never write original strategy/broker DBs; create only market/ai/AI_COMPREHENSIVE_V052.sqlite3.
A present SQLite file is not the same thing as a linked entry-time observation.
Every skipped/unmatched source is counted, not silently declared "used".
"""
from __future__ import annotations
import datetime as dt
import json
import math
import sqlite3
import statistics
import time
from collections import Counter,defaultdict
from contextlib import closing
from pathlib import Path

import ai_research_v040 as research

SCHEMA="ARTITRADE_AI_COMPREHENSIVE_V052"
DB_NAME="AI_COMPREHENSIVE_V052.sqlite3"
KST=dt.timezone(dt.timedelta(hours=9))
MAX_CASES=20000
MAX_SIDECAR_ROWS=60000
MAX_AUDIT_FILES=1000
MAX_TABLES_PER_FILE=250
MAX_FEATURES_PER_CASE=500
MAX_EVIDENCE_PREVIEW=12
ENTRY_PHASES=("entry",)
PHASES=("entry","execution","entry_closed","holding","exit","post","virtual","unverified")
SIDE_TABS={
    "entry_candle_context_v047":"entry_context",
    "trade_evidence_v048":"approved_metrics",
    "position_lifecycle_v042":"lifecycle",
    "position_factor_events_v042":"holding_events",
    "indicator_observations":"indicator_history",
    "candle_observations":"ohlcv_history",
    "market_score_observations":"market_score_history",
    "feature_snapshots":"signal_history",
    "benchmark_candles_v048":"benchmark_history",
    "trade_exit_events_v048":"exit_events",
    "position_observations":"position_snapshots",
}
POST_FIELDS=("forward_","future_","post_","after_","observed_","exit_","sell_",
             "profit_","realized_","unrealized_","target_","peak_")
SOURCE_LEVEL={"LIVE":"LIVE","PAPER":"PAPER","SHADOW_UNFILLED":"SHADOW_UNFILLED",
              "UNKNOWN_FILLED_LEDGER":"UNKNOWN_FILLED_LEDGER"}


def _number(value):
    if isinstance(value,bool) or value is None:return None
    try:
        out=float(value)
        return out if math.isfinite(out) else None
    except (ValueError,TypeError,OverflowError):
        return None


def _stamp(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        n=_number(value)
        if n is None or n<=0:return None
        if n>1e12:return int(n//1000)
        if n>1e9:return int(n)
        return None
    s=str(value or "").strip()
    if not s:return None
    try:
        if len(s)==14 and s.isdigit():
            return int(dt.datetime.strptime(s,"%Y%m%d%H%M%S").replace(tzinfo=KST).timestamp())
        if len(s)>=10 and s[4]=="-" and s[7]=="-":
            x=dt.datetime.fromisoformat(s.replace("Z","+00:00"))
            if x.tzinfo is None:x=x.replace(tzinfo=KST)
            return int(x.timestamp())
    except (ValueError,OverflowError,TypeError):
        pass
    return None


def _obj(value):
    if isinstance(value,dict):return value
    if isinstance(value,str):
        try:
            parsed=json.loads(value)
            return parsed if isinstance(parsed,dict) else {}
        except (ValueError,TypeError):pass
    return {}


def _flatten(value,prefix="",depth=0):
    """All numeric leaves + numeric series summaries, not a fixed 11-item list."""
    if depth>5:return {}
    out={}
    if isinstance(value,dict):
        for k,v in value.items():
            name=str(k).strip().lower().replace(" ","_").replace(".","_")
            if not name or len(name)>75:continue
            key=(prefix+"."+name).strip(".")
            if any(piece in ("id","key","ts","timestamp","epoch","at_ms","seq","version")
                   for piece in name.split("_")):
                continue
            out.update(_flatten(v,key,depth+1))
    elif isinstance(value,list):
        vals=[_number(x) for x in value[:500] if not isinstance(x,(list,dict))]
        vals=[x for x in vals if x is not None]
        if vals:
            out[prefix+".count"]=float(len(vals))
            out[prefix+".mean"]=sum(vals)/len(vals)
            out[prefix+".low"]=min(vals)
            out[prefix+".high"]=max(vals)
    else:
        n=_number(value)
        if n is not None and prefix:out[prefix]=n
    return out


def _safe_mode(row,fallback="UNKNOWN_FILLED_LEDGER"):
    raw=str((row or {}).get("execution_mode") or (row or {}).get("fill_method")
            or (row or {}).get("account_mode") or "").upper()
    if any(x in raw for x in ("PAPER","SIMUL","MOCK")):return "PAPER"
    if any(x in raw for x in ("LIVE","BROKER_FOK","REAL_FILL")):return "LIVE"
    if any(x in raw for x in ("SHADOW","VIRTUAL","UNFILLED")):return "SHADOW_UNFILLED"
    return fallback


def _normframe(value,strategy):
    if strategy=="gmo":return "gmo"
    if strategy=="close":return "day"
    return research._frame(value,strategy)


def _read_table(root,market,table,where="",args=(),limit=MAX_SIDECAR_ROWS):
    path=research.db_path(root,market)
    if not path.is_file():return [],0,None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                     uri=True,timeout=6)) as conn:
            conn.row_factory=sqlite3.Row
            known={x[0] for x in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if table not in known:return [],0,"TABLE_MISSING"
            clause=(" WHERE "+where) if where else ""
            count=conn.execute("SELECT COUNT(*) FROM "+table+clause,args).fetchone()[0]
            rows=[dict(x) for x in conn.execute(
                "SELECT * FROM "+table+clause+" ORDER BY rowid DESC LIMIT ?",
                tuple(args)+(int(limit),))]
            return rows,int(count),None
    except (OSError,sqlite3.Error,ValueError) as e:
        return [],0,type(e).__name__


def _load_sidecars(root,market,strategy):
    mode="MARKET_TREND" if strategy=="trend" else "ORIGINAL"
    result={}
    for table,label in SIDE_TABS.items():
        if table in ("market_score_observations","benchmark_candles_v048"):
            where="";args=()
        elif table=="entry_candle_context_v047" or table=="trade_evidence_v048":
            where="entry_mode=?"+(" AND strategy=?" if strategy!="trend" else "")
            args=(mode,)+( (strategy,) if strategy!="trend" else () )
        elif table=="trade_exit_events_v048":
            # Trend's parent strategy exit rows have another name: filter in join.
            if strategy=="trend":where="";args=()
            else:where="strategy=?";args=(strategy,)
        else:
            if strategy=="trend":continue
            where="strategy=?";args=(strategy,)
        rows,total,error=_read_table(root,market,table,where,args)
        groups=defaultdict(list)
        by_evidence_key=defaultdict(list)
        by_position_key=defaultdict(list)
        for entry in rows:
            if entry.get("evidence_key"):
                by_evidence_key[str(entry["evidence_key"])].append(entry)
            if entry.get("position_key"):
                by_position_key[str(entry["position_key"])].append(entry)
            symbol=str(entry.get("symbol") or entry.get("code") or
                       entry.get("benchmark") or "")
            frame=_normframe(entry.get("frame") or entry.get("tf"),
                             "gmo" if strategy=="gmo" and label!="benchmark_history"
                             else "at")
            if symbol and frame:groups[(frame,symbol)].append(entry)
        result[label]={"groups":groups,"by_evidence_key":by_evidence_key,
                       "by_position_key":by_position_key,
                       "unscoped":rows if label=="market_score_history" else [],
                       "total":total,"loaded":len(rows),"error":error,
                       "truncated":total>len(rows)}
    return result


def _case(raw,frame,when,mode,kind,status,roi=None,source=""):
    row=_obj(raw)
    symbol=str(row.get("code") or row.get("market") or row.get("symbol") or "")
    if not symbol or not frame:return None
    ident=str(row.get("id") or row.get("position_id") or row.get("event_key")
              or row.get("signal_id") or "").strip()
    ts=_stamp(when)
    owner=str(row.get("strategy") or "")
    seed=(ident if ident else ("at:"+str(ts) if ts else "unknown"))
    key="|".join((kind,mode,owner,frame,symbol,str(ts or ""),seed))
    return {"key":key,"kind":kind,"mode":mode,"frame":frame,"symbol":symbol,
            "owner_strategy":owner,"source_id":ident,"entry_ts":ts,"status":status,"roi":_number(roi),
            "source":source,"features":[],"sources":set(),"missing":Counter(),
            "source_row_hits":Counter(),"raw":row}


def _add(case,phase,values,source,observed_at=None):
    if not isinstance(values,dict) or phase not in PHASES:return
    flat=_flatten(values)
    for key,val in flat.items():
        if len(case["features"])>=MAX_FEATURES_PER_CASE:
            case["missing"]["FEATURE_CAP_EXCEEDED"]+=1
            break
        if any(x["phase"]==phase and x["key"]==key and x["source"]==source
               for x in case["features"]):
            continue
        case["features"].append({"phase":phase,"key":key,
                                 "value":round(val,10),"source":source,
                                 "observed_at":_stamp(observed_at)})
        case["sources"].add(source)


def _match(indexed_rows,case):
    """Indexed exact position identity or unique <=3 second evidence association."""
    rows=indexed_rows.get((case["frame"],case["symbol"]),())
    if case["entry_ts"] is None:return None,"MISSING_ENTRY_TIMESTAMP"
    options=[]
    for row in rows:
        if str(row.get("symbol") or row.get("code") or "")!=case["symbol"]:
            continue
        if (case.get("owner_strategy") and row.get("strategy") and
                str(row["strategy"])!=case["owner_strategy"]):
            continue
        if _normframe(row.get("frame") or row.get("tf"),"gmo" if case["frame"]=="gmo" else "at")!=case["frame"]:
            continue
        source_id=str(row.get("source_position_id") or row.get("position_id") or "")
        if case["source_id"] and source_id:
            if case["source_id"]==source_id:options.append((0,row))
            continue
        when=_stamp(row.get("entry_ts") or row.get("entry_at"))
        if when is not None and abs(when-case["entry_ts"])<=3:
            options.append((abs(when-case["entry_ts"]),row))
    if not options:return None,"NO_EXACT_POSITION_OR_ENTRY_TIME_MATCH"
    options.sort(key=lambda x:x[0])
    if len(options)>1:
        return None,"AMBIGUOUS_SAME_TICKER_JOIN"
    return options[0][1],None


def _source_audit(root,market,used_names):
    """Inventory all export-eligible local data sources, without reading secret files.

    Source tables that lack a trade key/time remain in the raw verified ZIP,
    clearly marked as not joined. Counts are from source databases, not guesses.
    """
    from android_ledger_backup import _candidate
    base=Path(root)
    files=sorted(x for x in base.rglob("*")
                 if x.is_file() and _candidate(base,x)
                 and (x.relative_to(base).parts[0]==market or
                      len(x.relative_to(base).parts)==1))
    total_files=len(files)
    out=[]
    for path in files[:MAX_AUDIT_FILES]:
        relative=path.relative_to(base).as_posix()
        used=used_names.get(relative,0)
        if path.suffix.lower() in (".sqlite3",".sqlite",".db"):
            try:
                with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                             uri=True,timeout=3)) as conn:
                    tables=[x[0] for x in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'")]
                    for name in tables[:MAX_TABLES_PER_FILE]:
                        try:
                            safe='"'+name.replace('"','""')+'"'
                            n=conn.execute("SELECT COUNT(*) FROM "+safe).fetchone()[0]
                        except sqlite3.Error:n=None
                        integrated=(used>0 and (name in SIDE_TABS or
                            relative.endswith("market_trend_v043.sqlite3")))
                        out.append({"file":relative,"table":name,"rows":n,
                            "integration":"TRADE_LINKED" if integrated else
                                "SOURCE_READ_FOR_BASE_ANALYSIS" if (
                                    name in ("positions","executions","candidates",
                                             "signals","gmo_candidates")) else
                                "AVAILABLE_UNATTRIBUTED",
                            "reason":None if integrated else
                                "NO_VERIFIED_PER_TRADE_LINK_OR_TIME"})
                    if len(tables)>MAX_TABLES_PER_FILE:
                        out.append({"file":relative,"table":"*","rows":None,
                                    "integration":"TABLE_LIMIT",
                                    "reason":"TABLES_EXCEED_AUDIT_CAP"})
            except (OSError,sqlite3.Error) as e:
                out.append({"file":relative,"table":"*","rows":None,
                    "integration":"UNREADABLE","reason":type(e).__name__})
        else:
            out.append({"file":relative,"table":None,"rows":None,
                        "integration":"FILE_AVAILABLE","reason":"NON_TABULAR_OR_SETTINGS"})
    return {"tables":out,"files_seen":total_files,
            "files_audited":min(total_files,MAX_AUDIT_FILES),
            "files_skipped":max(0,total_files-MAX_AUDIT_FILES)}


def _ingest_before_entry(case,trade):
    ts=case["entry_ts"]
    signal_at=_stamp(trade.get("signal_ts"))
    features=trade.get("features") or {}
    # Historical pre-entry signal features require an actual source signal clock.
    if signal_at is not None and ts is not None and signal_at<=ts:
        _add(case,"entry",features,"SIGNAL_SAVED_AT_OR_BEFORE_ENTRY",signal_at)
    else:
        _add(case,"unverified",features,"SIGNAL_TIMESTAMP_UNVERIFIED",signal_at)
    _add(case,"holding",trade.get("current_features") or {},
         "CURRENT_POSITION_SNAPSHOT_NOT_ENTRY_CAUSE")


def _ingest_lifecycle(case,row):
    if not row:return
    phase=("entry" if str(row.get("entry_feature_origin") or "").upper()
           in ("PRE_ENTRY_CLOSED_OHLCV","OBSERVED_PRE_ENTRY_TIMESTAMPED") else "unverified")
    _add(case,phase,_obj(row.get("entry_features")),
         "PRE_ENTRY_CANDLES_OR_SOURCE" if phase=="entry" else "ENTRY_SOURCE_TIME_UNVERIFIED",
         row.get("entry_ts"))
    _add(case,"holding",_obj(row.get("holding_mean")),"HOLDING_PERIOD_INDICATORS")
    _add(case,"exit",_obj(row.get("exit_features")),"PRE_EXIT_CANDLES",row.get("exit_ts"))
    keep={k:row.get(k) for k in (
        "max_favorable_pct","max_adverse_pct","peak_to_trough_pct",
        "hold_return_first","hold_return_last","market_holding_mean",
        "indicator_observations","position_factor_events","bar_observations",
        "volume_observations","duration_seconds") if row.get(k) is not None}
    _add(case,"holding",keep,"OBSERVED_POSITION_LIFECYCLE")
    if row.get("market_entry") is not None and case.get("entry_ts") is not None:
        _add(case,"entry",{"market_score":row["market_entry"]},
             "POINT_IN_TIME_MARKET_SCORE",row.get("entry_ts"))
    if row.get("market_exit") is not None:
        _add(case,"exit",{"market_score":row["market_exit"]},
             "POINT_IN_TIME_EXIT_MARKET_SCORE",row.get("exit_ts"))


def _ingest_entry_candle(case,row):
    context=_obj(row.get("context_json"))
    before={k:context[k] for k in ("high_slope_pct_per_bar",
        "low_slope_pct_per_bar","high_slope_price_per_bar",
        "low_slope_price_per_bar","prior30_volume_mean")
        if context.get(k) is not None}
    if before:_add(case,"entry",before,"V047_PRE_ENTRY_30_CLOSED_BARS",
                    row.get("entry_ts"))
    if context.get("entry_strength_source") not in (
            None,"NOT_TIMESTAMPED_OR_UNAVAILABLE"):
        _add(case,"execution",{"entry_strength_pct":context.get("entry_strength_pct")},
             "V047_TIMESTAMPED_ENTRY_FLOW",context.get("entry_strength_epoch"))
    completed={k:context[k] for k in ("entry_candle_close","entry_volume",
        "volume_ratio30") if context.get(k) is not None}
    if completed and context.get("entry_candle_complete"):
        _add(case,"entry_closed",completed,"V047_FINALIZED_ENTRY_BAR",
             context.get("entry_bar_end_epoch"))
    for width,obj in _obj(context.get("sma")).items():
        pos=str(_obj(obj).get("position") or "").upper()
        item={"distance_pct":_obj(obj).get("close_minus_sma_pct")}
        if pos in ("BELOW","TOUCH","ABOVE"):
            item["close_position"]={"BELOW":-1,"TOUCH":0,"ABOVE":1}[pos]
        if context.get("entry_candle_complete"):
            _add(case,"entry_closed",item,"V047_SMA_"+str(width),
                 context.get("entry_bar_end_epoch"))


def _ingest_approved(case,row):
    if case["mode"]=="UNKNOWN_FILLED_LEDGER":
        mode=str(row.get("execution_kind") or "").upper()
        if mode in ("LIVE","PAPER") and str(row.get("role"))=="ACTUAL":
            case["mode"]=mode
    case["approved_evidence_key"]=str(row.get("evidence_key") or "")
    case["market_id"]=str(row.get("market_id") or "")
    entry=_obj(row.get("entry_json"))
    post=_obj(row.get("post_json"))
    counts={}
    categories={"02":"execution","05":"execution","09":"entry_closed",
                "10":"entry","11":"entry","16":"execution"}
    for code,phase in categories.items():
        metric=_obj(entry.get(code))
        if metric.get("value") is not None:
            _add(case,phase,{code:metric["value"]},
                 "V048_APPROVED_"+code,row.get("entry_ts"))
            counts[code]="AVAILABLE"
        else:
            reason=str(metric.get("missing_reason") or "NOT_CAPTURED")
            case["missing"]["V048_"+code+":"+reason]+=1
    for code,phase in (("13","post"),("14","exit"),("15","post")):
        metric=_obj(post.get(code))
        if metric.get("value") is not None:
            _add(case,phase,{code:metric["value"]},"V048_APPROVED_"+code,
                 row.get("exit_ts") or row.get("updated_at"))
        else:case["missing"]["V048_"+code+":"+
                str(metric.get("missing_reason") or "NOT_CAPTURED")]+=1
    return counts


def _associate(groups,case,kind,fn):
    block=groups.get(kind) or {}
    row,why=_match(block.get("groups",{}),case)
    if row is not None:
        fn(case,row)
        return True
    if why:case["missing"][kind+":"+why]+=1
    return False


def _rank_cases(cases):
    """Explore *all* observed pre-entry numeric fields; no preselected 11 gates.

    Chronological holdout is needed before a pattern can be labelled held-out,
    and even then this is association, not a trading decision/causal proof.
    """
    cohorts=defaultdict(list)
    for case in cases:
        if case["roi"] is not None and case["kind"]=="FILLED":
            cohorts[(case["frame"],case["mode"])].append(case)
    output=[]
    for (frame,mode),group in cohorts.items():
        if len(group)<8:continue
        group=sorted(group,key=lambda x:(x["entry_ts"] or 0,x["key"]))
        by_feature=defaultdict(list)
        for case in group:
            entry={}
            for item in case["features"]:
                if item["phase"]=="entry":
                    entry.setdefault(item["key"],item["value"])
            for key,value in entry.items():
                by_feature[key].append((case["entry_ts"] or 0,value,case["roi"]))
        for key,values in by_feature.items():
            if len(values)<8:continue
            series=sorted(values)
            amounts=sorted(x[1] for x in series)
            pivot=statistics.median(amounts)
            low=[r for t,v,r in series if v<pivot]
            high=[r for t,v,r in series if v>=pivot]
            if len(low)<4 or len(high)<4:continue
            delta=statistics.mean(high)-statistics.mean(low)
            validated=False;holdout=None;status="EXPLORATORY_ONLY"
            if len(series)>=30:
                cut=int(len(series)*.7)
                train,test=series[:cut],series[cut:]
                train_threshold=statistics.median(x[1] for x in train)
                train_low=[r for t,v,r in train if v<train_threshold]
                train_high=[r for t,v,r in train if v>=train_threshold]
                test_low=[r for t,v,r in test if v<train_threshold]
                test_high=[r for t,v,r in test if v>=train_threshold]
                if (min(len(train_low),len(train_high))>=5 and
                        min(len(test_low),len(test_high))>=3):
                    train_sign=statistics.mean(train_high)-statistics.mean(train_low)
                    test_effect=statistics.mean(test_high)-statistics.mean(test_low)
                    validated=train_sign*test_effect>0
                    holdout={"train_n":len(train),"test_n":len(test),
                             "test_delta_pp":round(test_effect,4),
                             "direction_consistent":validated}
                    status=("DIRECTION_REPLICATED" if validated
                            else "HOLDOUT_NOT_REPLICATED")
            output.append({"frame":frame,"execution_mode":mode,
                "feature":key,"samples":len(series),"cohort_closed":len(group),
                "median_threshold":round(pivot,6),
                "lower_n":len(low),"upper_n":len(high),
                "lower_avg_roi":round(statistics.mean(low),4),
                "upper_avg_roi":round(statistics.mean(high),4),
                "delta_pp":round(delta,4),"holdout":holdout,"status":status,
                "causal":False,"auto_apply_to_orders":False,
                "evidence_phase":"PRE_ENTRY_ONLY"})
    output.sort(key=lambda x:(x["status"]!="DIRECTION_REPLICATED",
                               -x["samples"],-abs(x["delta_pp"]),x["feature"]))
    return output


def _persistence(root,market,strategy,cases,sources,report):
    """Separate research-only SQLite; all original trading ledgers untouched."""
    path=Path(root)/market/"ai"/DB_NAME
    path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(str(path),timeout=10)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trade_cases(
            scope TEXT NOT NULL,case_key TEXT NOT NULL,symbol TEXT NOT NULL,
            frame TEXT NOT NULL,execution_mode TEXT NOT NULL,
            kind TEXT NOT NULL,entry_ts INTEGER,realized_roi REAL,
            case_json TEXT NOT NULL,updated_at INTEGER NOT NULL,
            PRIMARY KEY(scope,case_key));
        CREATE INDEX IF NOT EXISTS v052_symbol_time
            ON trade_cases(scope,symbol,frame,entry_ts);
        CREATE TABLE IF NOT EXISTS analysis_runs(
            scope TEXT NOT NULL,generated_at INTEGER NOT NULL,
            summary_json TEXT NOT NULL,PRIMARY KEY(scope,generated_at));
        CREATE TABLE IF NOT EXISTS source_inventory(
            scope TEXT NOT NULL,file TEXT NOT NULL,table_name TEXT NOT NULL,
            rows_total INTEGER,integration TEXT NOT NULL,reason TEXT,
            audited_at INTEGER NOT NULL,PRIMARY KEY(scope,file,table_name));
        """)
        scope=market+"/"+strategy
        now=report["generated_at_epoch"]
        for case in cases:
            safe={k:v for k,v in case.items() if k not in ("sources","missing","raw")}
            safe["sources"]=sorted(case["sources"])
            safe["missing"]=dict(case["missing"])
            safe["source_row_hits"]=dict(case["source_row_hits"])
            # Original source rows are in the unchanged manifest ZIP, not duplicated.
            payload=json.dumps(safe,ensure_ascii=False,allow_nan=False)
            conn.execute("""INSERT INTO trade_cases VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(scope,case_key) DO UPDATE SET
                  symbol=excluded.symbol,frame=excluded.frame,
                  execution_mode=excluded.execution_mode,kind=excluded.kind,
                  entry_ts=excluded.entry_ts,realized_roi=excluded.realized_roi,
                  case_json=excluded.case_json,updated_at=excluded.updated_at""",
                (scope,case["key"],case["symbol"],case["frame"],case["mode"],
                 case["kind"],case["entry_ts"],case["roi"],payload,now))
        for item in sources["tables"]:
            conn.execute("""INSERT INTO source_inventory VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(scope,file,table_name) DO UPDATE SET
                  rows_total=excluded.rows_total,integration=excluded.integration,
                  reason=excluded.reason,audited_at=excluded.audited_at""",
                (scope,item["file"],item["table"] or "",item["rows"],
                 item["integration"],item["reason"],now))
        conn.execute("""INSERT INTO analysis_runs VALUES(?,?,?)
            ON CONFLICT(scope,generated_at) DO UPDATE SET
            summary_json=excluded.summary_json""",
            (scope,now,json.dumps(report,ensure_ascii=False,allow_nan=False)) )
        conn.commit()
    return path


def analyze(root,market,strategy,signals,trades,lifecycle,archive=None,now=None,
            extra_sources=None):
    """Called once by the user's manual AI button, never on a trading thread."""
    if market not in ("stock","coin") or strategy not in (
            "at","ma","gmo","close","general","trend"):
        raise ValueError("UNSUPPORTED_COMPREHENSIVE_SCOPE")
    when=int(now or time.time())
    cases=[]
    used=Counter()
    for trade in list(trades)[:MAX_CASES]:
        raw=trade.get("raw") or {}
        if not isinstance(raw,dict):continue
        frame=trade.get("frame") or _normframe(raw.get("tf") or raw.get("frame"),strategy)
        if not frame:continue
        entry=trade.get("at") or raw.get("entry_at") or raw.get("buy_at")
        case=_case(raw,frame,entry,_safe_mode(raw),"FILLED",
                   trade.get("state","UNKNOWN"),trade.get("closed_roi"),
                   "STRATEGY_POSITION_LEDGER")
        if case is None:continue
        _ingest_before_entry(case,trade)
        # Preserve every numeric field found in the original position state.
        # Timestamp/unverified fields never enter pre-buy causal comparisons.
        _add(case,"unverified",raw,"ORIGINAL_POSITION_STATE_AS_OBSERVED")
        cases.append(case)
    # Non-buys are research signals, NOT zero-profit closed trades.
    filled_by=( {(x["frame"],x["symbol"],x["entry_ts"])
                 for x in cases if x["entry_ts"] is not None} )
    for signal in list(signals)[:MAX_CASES]:
        raw=signal.get("raw") or {}
        if not isinstance(raw,dict):continue
        frame=signal.get("frame") or _normframe(raw.get("tf") or raw.get("frame"),strategy)
        entry=signal.get("signal_ts")
        timestamp=_stamp(entry)
        symbol=str(raw.get("code") or raw.get("market") or raw.get("symbol") or
                   signal.get("symbol") or "")
        if (frame,symbol,timestamp) in filled_by:continue
        if not symbol or not frame:continue
        row=dict(raw,code=symbol)
        case=_case(row,frame,entry,"SHADOW_UNFILLED","UNFILLED_SIGNAL",
                   str(row.get("outcome") or "UNFILLED"),None,"STRATEGY_CANDIDATE")
        if case is None:continue
        if timestamp is not None and signal.get("source")!="CAPTURED_LATE":
            _add(case,"entry",signal.get("features") or {},
                 "SOURCE_CANDIDATE_AT_SIGNAL",timestamp)
        else:
            _add(case,"unverified",signal.get("features") or {},
                 "CANDIDATE_TIMESTAMP_UNVERIFIED")
        _add(case,"unverified",raw,"ORIGINAL_CANDIDATE_HISTORY_AS_OBSERVED")
        value=row.get("target_hit")
        if isinstance(value,bool):
            _add(case,"virtual",{"observed_target_hit":int(value)},
                 "CONFIRMED_SHADOW_PRICE_OBSERVATION")
        cases.append(case)
    sidecars=_load_sidecars(root,market,strategy)
    byid={}
    for life in lifecycle:
        key=str(life.get("position_key") or "")
        if key:byid[key]=life
    for case in cases:
        if case["kind"]!="FILLED":continue
        life=None
        # Exact position-key association; same ticker is NOT sufficient.
        for raw_name in (case["source_id"],case["key"]):
            if raw_name in byid:life=byid[raw_name];break
        if life is None:
            matches=[v for v in lifecycle
                     if v.get("frame")==case["frame"] and
                     v.get("symbol")==case["symbol"] and
                     _stamp(v.get("entry_ts"))==case["entry_ts"]]
            if len(matches)==1:life=matches[0]
            elif len(matches)>1:case["missing"]["AMBIGUOUS_LIFECYCLE_MATCH"]+=1
        if life:
            case["lifecycle_key"]=str(life.get("position_key") or "")
            case["exit_ts"]=_stamp(life.get("exit_ts"))
            _ingest_lifecycle(case,life)
            used[market+"/ai/AI_RESEARCH_V040.sqlite3"]+=1
        for label,fn in (("entry_context",_ingest_entry_candle),
                         ("approved_metrics",_ingest_approved)):
            if _associate(sidecars,case,label,fn):
                used[market+"/ai/AI_RESEARCH_V040.sqlite3"]+=1
        from ai_comprehensive_sources_v052 import ingest as ingest_sources
        hits=ingest_sources(case,sidecars,_add,_stamp)
        for name,n in hits.items():
            used[market+"/ai/AI_RESEARCH_V040.sqlite3"]+=n
            case["source_row_hits"][name]=n
        # Position snapshots carry mark/P&L history even without candle data.
        position_events=(sidecars.get("position_snapshots",{})
            .get("by_position_key",{}).get(case.get("lifecycle_key"),()))
        for event in position_events:
            at=_stamp(event.get("observed_minute"))
            if at is None or at<case["entry_ts"] or (
                  case.get("exit_ts") is not None and at>case["exit_ts"]):
                continue
            _add(case,"holding",dict(mark=event.get("mark"),
                 quantity=event.get("quantity"),pnl=event.get("pnl"),
                 market_score=event.get("market_score")),
                 "V052_ORIGINAL_POSITION_OBSERVATION",at)
            _add(case,"holding",_obj(event.get("extra_json")),
                 "V052_ORIGINAL_POSITION_INDICATORS",at)
            case["source_row_hits"]["position_snapshots"]+=1
    # Any scalar fields present only in the input signal's CURRENT snapshot stay
    # in holding/unverified cohorts, never reverse-engineered into pre-buy causes.
    # Complete raw source files remain in the signed on-device ZIP export.
    source_audit=_source_audit(root,market,used)
    ranked=_rank_cases(cases)
    phases=Counter();metrics=Counter();modes=Counter();missing=Counter()
    for case in cases:
        modes[case["mode"]]+=1
        missing.update(case["missing"])
        for metric in case["features"]:
            phases[metric["phase"]]+=1
            metrics[metric["phase"]+"."+metric["key"]]+=1
    verified=[x for x in cases if x["kind"]=="FILLED" and x["roi"] is not None]
    shadow=[x for x in cases if x["kind"]=="UNFILLED_SIGNAL"]
    sources=Counter(item["integration"] for item in source_audit["tables"])
    report={"schema":SCHEMA,"ok":True,"scope":market+"/"+strategy,
        "generated_at_epoch":when,"analysis_model":"READ_ONLY_STATISTICAL_EVIDENCE",
        "orders_untouched":True,"autotrading_rules_unchanged":True,
        "total_linked_cases":len(cases),"filled_cases":len(cases)-len(shadow),
        "closed_with_verified_roi":len(verified),
        "unfilled_virtual_candidates":len(shadow),
        "virtual_observed_hit_n":sum(1 for x in shadow
                                    if any(f["key"]=="observed_target_hit" and
                                    f["value"]==1 for f in x["features"])),
        "by_execution_mode":dict(modes),"feature_observations_by_phase":dict(phases),
        "distinct_numeric_factors":len(metrics),
        "numeric_factor_coverage":dict(metrics.most_common(35)),
        "source_inventory_counts":dict(sources),
        "source_files_seen":source_audit["files_seen"],
        "source_files_audited":source_audit["files_audited"],
        "source_files_skipped":source_audit["files_skipped"],
        "source_tables_audited":len(source_audit["tables"]),
        "source_tables_unlinked":sum(1 for x in source_audit["tables"]
            if x["integration"]=="AVAILABLE_UNATTRIBUTED"),
        "trade_joined_source_rows":dict(sum(
            (case["source_row_hits"] for case in cases),Counter())),
        "unlinked_sources_are_not_claimed_used":True,
        "sidecar_rows_loaded":{name:v["loaded"] for name,v in sidecars.items()},
        "sidecar_rows_total":{name:v["total"] for name,v in sidecars.items()},
        "sidecar_errors":{name:v["error"] for name,v in sidecars.items()
                          if v.get("error")},
        "sidecar_truncated":{name:v["total"]-v["loaded"]
            for name,v in sidecars.items() if v["truncated"]},
        "missing_evidence_reasons":dict(missing.most_common(25)),
        "factor_comparisons_evaluated":len(ranked),
        "factors_preview":ranked[:25],
        "cases_preview":[{"symbol":c["symbol"],"frame":c["frame"],"mode":c["mode"],
            "kind":c["kind"],"entry_ts":c["entry_ts"],"closed_roi":c["roi"],
            "metric_count":len(c["features"]),"source_count":len(c["sources"]),
            "missing_n":sum(c["missing"].values())}
            for c in cases[:MAX_EVIDENCE_PREVIEW]],
        "analysis_case_cap":MAX_CASES,
        "input_trades_skipped_cap":max(0,len(trades)-MAX_CASES),
        "input_signals_skipped_cap":max(0,len(signals)-MAX_CASES),
        "information_integrity":{
            "real_paper_virtual_cohorts_separate":True,
            "only_pre_entry_features_ranked":True,
            "entry_time_ambiguous_not_joined":True,
            "future_prices_never_used_as_buy_cause":True,
            "hypotheses_not_order_instructions":True,
            "unlinked_sources_explicit":True,
            "no_trading_or_broker_db_writes":True},
        "full_case_details":"{market}/ai/AI_COMPREHENSIVE_V052.sqlite3::trade_cases".replace(
                            "{market}",market)}
    path=_persistence(root,market,strategy,cases,source_audit,report)
    report["saved_database"]=path.relative_to(Path(root)).as_posix()
    return report


def analyze_trend(root,market,now=None):
    """Build distinct TREND cohort from its own durable journal and original fills."""
    from ai_trend_analysis_v047 import _read
    data=_read(root,market)
    entries={};sell=defaultdict(list)
    for event in data["events"]:
        if event.get("reason")=="MT_BUY_FILLED":
            pid=_obj(event.get("payload")).get("position_id")
            if pid is not None:
                entries[(str(event.get("strategy")),str(pid))]=event
        if (str(event.get("event_key") or "").startswith("MT43:SELL:") and
                str(event.get("event_key") or "").endswith(":FILLED")):
            pid=_obj(event.get("payload")).get("position_id")
            if pid is not None:
                sell[(str(event.get("strategy")),str(pid))].append(event)
    trades=[]
    for item in data["positions"]:
        pid=str(item.get("position_id") or "")
        strategy=str(item.get("strategy") or "")
        raw=dict(_obj(item.get("payload")),
                 code=item.get("symbol"),tf=item.get("frame"),
                 strategy=strategy,position_id=pid)
        buy=entries.get((strategy,pid))
        mode=str(raw.get("execution_mode") or "").upper()
        if mode not in ("LIVE","PAPER") and buy:
            mode="LIVE" if buy["mode"]=="ACTUAL" else (
                 "PAPER" if buy["mode"]=="PAPER" else "UNKNOWN_FILLED_LEDGER")
        if mode not in ("LIVE","PAPER"):mode="UNKNOWN_FILLED_LEDGER"
        raw["execution_mode"]=mode
        raw["entry_mode"]="MARKET_TREND"
        entry=raw.get("entry_at") or (buy or {}).get("at")
        roi=None
        related=sell.get((strategy,pid),[])
        if str(item.get("status") or "").upper()=="CLOSED" and related:
            basis=_number(raw.get("entry"));qty=_number(raw.get("original_qty"))
            profit=[_number(_obj(e.get("payload")).get("net_pnl")) for e in related]
            if basis and qty and all(x is not None for x in profit):
                roi=100*sum(profit)/(basis*qty)
        fields={"rsi20_live":_number((buy or {}).get("rsi20_live")),
                "rsi20_closed":_number((buy or {}).get("rsi20_closed"))}
        fields={k:v for k,v in fields.items() if v is not None}
        trades.append({"raw":raw,"frame":item.get("frame"),"at":entry,
                       "signal_ts":(buy or {}).get("at"),
                       "features":fields,"current_features":{},
                       "closed_roi":roi,
                       "state":"CLOSED" if item.get("status")=="CLOSED" else "OPEN"})
    signals=[]
    for signal in data["virtual"]:
        raw=_obj(signal.get("payload"))
        raw=dict(raw,code=signal.get("symbol"),tf=signal.get("frame"),
                 id=signal.get("signal_key"),execution_mode="SHADOW_UNFILLED")
        signals.append({"raw":raw,"frame":signal.get("frame"),
            "symbol":signal.get("symbol"),"signal_ts":raw.get("entry_at"),
            "features":{},"source":"MARKET_TREND_VIRTUAL"})
    return analyze(root,market,"trend",signals,trades,[],now=now)
