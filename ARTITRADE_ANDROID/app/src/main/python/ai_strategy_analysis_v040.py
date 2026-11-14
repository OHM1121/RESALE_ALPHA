"""ARTITRADE v040: manual, scope-bound, evidence-ranked AI strategy diagnosis.

- One exact (market,strategy) per invocation, timeframe separated.
- Recent OPEN positions and any available research signals are useful today.
- More evidence tomorrow upgrades counts and validation, never fabricates a
  completed trade, entry signal feature, exit or market score.
- Exploratory past filters are NOT causal findings or executable trade orders.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import random
import sqlite3
import statistics
from collections import Counter,defaultdict
from pathlib import Path

import ai_research_v040 as journal
import ai_strategy_analysis_v037 as old

SCOPES=old.SOURCE
FRAMES=("5m","30m","60m","day")
FRAME_NAMES={"5m":"5분봉","30m":"30분봉","60m":"60분봉","day":"일봉","gmo":"수급매매"}
TITLES={("stock","at"):"주식 · 기준봉",("stock","ma"):"주식 · MA",
        ("stock","gmo"):"주식 · 수급매매",("stock","close"):"주식 · 종가베팅",
        ("coin","general"):"코인 · 기준봉",("coin","ma"):"코인 · MA"}
# Order-entry features only. Holding-market averages are described separately.
RULES=(
    ("market_score","<=",-20,"시장점수 약세","약세장 매수 보류"),
    ("atr_pct",">=",3.0,"ATR 확장","변동성 급등 진입 제한"),
    ("volume_ratio",">=",2.5,"거래량 급증","과열 거래량 진입 필터"),
    ("williams_r",">=",-20,"Williams 과매수","과매수 구간 신규진입 제한"),
    ("williams_r","<=",-80,"Williams 과매도","과매도 반등 확인 후 진입"),
    ("rsi",">=",75,"RSI 과열","RSI 과열구간 신규진입 보류"),
    ("spread_pct",">=",.5,"호가 공백","호가 스프레드 확대 시 매수 보류"),
    ("gap_pct",">=",4,"갭 급등","상승 갭 발생 종목 진입 필터"),
    ("bid_imbalance","<=",.35,"매수호가 약세","매수호가 비중 회복 확인"),
    ("score","<=",35,"진입점수 낮음","최소 진입점수 상향 시험"),
    ("market_volatility",">=",4,"시장 변동성","시장 변동성 급증 시 진입 축소"),
)
MIN_ENTRY_SUPPORT=1
MAX_OPEN_SAMPLES=8000


def _row_time(row,fields):
    for k in fields:
        stamp=journal._timestamp(row.get(k))
        if stamp is not None:return stamp
    return None


def _candidate_key(row,market,strategy):
    # Prefer a native id. If no native id, require instrument+reference timestamp.
    tf=journal._frame(row.get("tf") or row.get("frame"),strategy)
    symbol=str(row.get("code") or row.get("market") or "")
    explicit=(row.get("signal_id") or row.get("candidate_id") or row.get("id"))
    if explicit is not None and str(explicit).strip():
        return "id:"+str(explicit)+"|"+tf+"|"+symbol
    reference=str(row.get("ref_ts") or row.get("ref_at") or
                  row.get("event_date") or row.get("gmo_event_date") or "")
    return "combo:"+tf+"|"+symbol+"|"+reference if symbol and reference else None


def _position_key(row,market,strategy):
    tf=journal._frame(row.get("tf") or row.get("frame"),strategy)
    symbol=str(row.get("code") or row.get("market") or "")
    ref=str(row.get("ref_ts") or row.get("ref_at") or
            row.get("event_date") or row.get("gmo_event_date") or
            row.get("entry_date") or "")
    candidate=row.get("signal_id") if strategy in ("at","close") else row.get("candidate_id")
    if candidate is not None:
        return "id:"+str(candidate)+"|"+tf+"|"+symbol
    # Native portfolio id is NOT the candidate id; use its exact matching
    # reference triple before trying any other position.
    return "combo:"+tf+"|"+symbol+"|"+ref if symbol and ref else None


def _link(candidate_rows,position,market,strategy):
    candidate_id=position.get("signal_id") if strategy in ("at","close") else position.get("candidate_id")
    tf=journal._frame(position.get("tf") or position.get("frame"),strategy)
    code=str(position.get("code") or position.get("market") or "")
    ref=str(position.get("ref_ts") or position.get("ref_at") or
            position.get("event_date") or position.get("gmo_event_date") or
            position.get("entry_date") or "")
    if candidate_id is not None:
        for row in candidate_rows:
            pk=row["raw"].get("signal_id") or row["raw"].get("candidate_id") or row["raw"].get("id")
            if str(pk)==str(candidate_id) and row["frame"]==tf and row["symbol"]==code:
                return row
    if code and ref:
        for row in candidate_rows:
            r=row["raw"]
            c_ref=str(r.get("ref_ts") or r.get("ref_at") or
                      r.get("event_date") or r.get("gmo_event_date") or "")
            if row["frame"]==tf and row["symbol"]==code and c_ref==ref:return row
    # GMO can have different D-day/event_date and purchase date. Never map a
    # position to an arbitrary same-symbol candidate from a different date.
    return None


def _market_interval(stamps,scores,start,end):
    if start is None or end is None or end<start:return None,0
    a=bisect.bisect_left(stamps,int(start))
    b=bisect.bisect_right(stamps,int(end))
    vals=scores[a:b]
    if not vals:return None,0
    return round(sum(vals)/len(vals),2),len(vals)


def _unrealized_pct(row):
    status=str(row.get("status") or row.get("state") or "").upper()
    if status in ("CLOSED","SOLD","DONE","RESET_B_V1","FILLED_EXIT"):
        return None
    entry=journal._number(row.get("entry_price") or row.get("entry") or
                          row.get("buy_price") or row.get("avg_buy_price"))
    mark=journal._number(row.get("last_price") or row.get("mark") or
                         row.get("current_price") or row.get("market_price"))
    if entry and entry>0 and mark and mark>0:
        return round((mark/entry-1)*100,3)
    return None


def _is_closed(row):
    status=str(row.get("status") or row.get("state") or "").upper()
    if status in ("CLOSED","SOLD","DONE","FILLED_EXIT"):return True
    return False


def _returns(row,fills):
    if not _is_closed(row):return None,"OPEN"
    # Original EXE net realized PnL/cost-basis only; no guessed sell price.
    pnl,state=old._pnl_return(dict(row,status="CLOSED"),fills)
    return pnl,state


def _signal_rows(raw,research,market,strategy,archive):
    rawitems=[r for r in raw.get("candidates",[]) if isinstance(r,dict)]
    snapshots=[r for r in research.get("items",[]) if isinstance(r,dict)]
    results=[]
    known={}
    for row in rawitems+snapshots:
        frame=journal._frame(row.get("tf") or row.get("frame"),strategy)
        if not frame and strategy not in ("gmo","close"):continue
        key=_candidate_key(row,market,strategy)
        if not key:continue
        if key in known:
            base=known[key]["raw"]
            for k,v in row.items():
                if k not in base or base[k] is None:base[k]=v
            known[key]["features"].update({k:v for k,v in
                 journal.extract_features(row,known[key]["signal_ts"]).items()
                 if k not in known[key]["features"]})
            continue
        at=_row_time(row,("at","signal_ts","created_at","ts","ref_ts","ref_at",
                          "event_date","gmo_event_date"))
        features=journal.extract_features(row,at)
        item={"key":key,"frame":frame,"symbol":str(row.get("code") or row.get("market") or ""),
              "signal_ts":at,"features":features,"raw":dict(row),
              "source":"ORIGINAL_SIGNAL"}
        known[key]=item
        results.append(item)
    # Older archived feature snapshots may be observed much later; they are
    # available as historical evidence, but never backdated as entry features.
    for archived in archive.get("features",[]):
        frame=archived.get("frame","")
        if not frame:continue
        key=archived.get("signal_key","")
        try:features=json.loads(archived["features_json"])
        except (ValueError,KeyError,TypeError):continue
        event_ts=archived.get("event_ts")
        first=archived.get("first_captured") or 0
        safe_at_entry=event_ts is not None and first<=int(event_ts)+120
        # Native journal primary ID may differ from the identity of a raw row.
        source_item=None
        for item in results:
            if item["frame"]!=frame:continue
            if key and (key in (item["key"],journal._key(item["raw"],strategy,"signal"))):
                source_item=item;break
        if source_item is not None:
            if safe_at_entry:
                for k,v in features.items():source_item["features"].setdefault(k,v)
            continue
        # Current journal can expose a signal even when original EXE source DB
        # has not been synced to this phone yet. Timestamp may be unknown.
        results.append({"key":"archived:"+key,"frame":frame,
                        "symbol":key.split("|")[-1] if "|" in key else "",
                        "signal_ts":event_ts,"features":features if safe_at_entry else {},
                        "raw":{},"source":"CAPTURED_AT_SIGNAL" if safe_at_entry else
                        "CAPTURED_LATE"})
    return results


def _frame_summary(frame,signals,trades,archive,events,now,source):
    label=FRAME_NAMES[frame]
    candidates=[x for x in signals if x["frame"]==frame]
    group=[x for x in trades if x["frame"]==frame]
    completed=[x for x in group if x["closed_roi"] is not None]
    open_items=[x for x in group if x["state"]=="OPEN"]
    open_marked=[x for x in open_items if x["open_roi"] is not None]
    unknown=[x for x in group if x["state"]!="OPEN" and x["closed_roi"] is None]
    feature_set=("atr_pct","volume_ratio","williams_r","rsi",
                 "market_score","score","spread_pct","gap_pct")
    cover={}
    # Report indicator availability from both entry-time snapshots and LIVE
    # holding telemetry. Current features never become entry-time causes.
    combined=candidates+group
    for feature in feature_set:
        n=sum(feature in (x.get("features") or x.get("current_features") or {})
              for x in combined)
        cover[feature]=round(100*n/len(combined),1) if combined else 0
    signal_count=len(candidates)
    average=_mean(x["closed_roi"] for x in completed) if completed else None
    avg_open=_mean(x["open_roi"] for x in open_marked) if open_marked else None
    hold_scores=[x["hold_market_avg"] for x in group if x.get("hold_market_avg") is not None]
    avg_hold=_mean(hold_scores) if hold_scores else None
    total_market_obs=sum(x.get("hold_market_n",0) for x in group)
    patterns=[]
    for feature,title in (("atr_pct","ATR"),("volume_ratio","거래량"),
                          ("williams_r","Williams %R"),("rsi","RSI"),
                          ("market_score","진입 시장점수")):
        vals=[(x.get("features") or x.get("current_features") or {})[feature]
              for x in combined
              if feature in (x.get("features") or x.get("current_features") or {})]
        if vals:
            patterns.append({"name":title,"median":round(statistics.median(vals),2),
                             "n":len(vals),"coverage":round(100*len(vals)/max(1,len(combined)))})
    rules=[]
    # Learn from concluded trades only. Current OPEN position can show risk
    # factor exposure, not an invented realized loss or "successful filter".
    for feature,operator,threshold,name,advice in RULES:
        eligible=[x for x in completed if feature in x["features"]]
        if not eligible:continue
        def risk(x):
            v=x["features"][feature]
            return v>=threshold if operator==">=" else v<=threshold
        bad=[x for x in eligible if risk(x)]
        safe=[x for x in eligible if not risk(x)]
        if not bad:continue
        bad_avg=_mean(x["closed_roi"] for x in bad)
        safe_avg=_mean(x["closed_roi"] for x in safe) if safe else None
        avg_gap=(safe_avg-bad_avg) if safe_avg is not None else None
        # Zero-return for an avoided opportunity: a *retrospective scenario*.
        # Do not call it expected profit from future trades.
        improvement=-sum(x["closed_roi"] for x in bad)/len(completed)
        if safe and avg_gap is not None and avg_gap>0:
            rank=avg_gap*(len(eligible)/max(2,len(completed)))+max(0,improvement)
        else:
            rank=max(0,improvement)*.33
        rules.append({"factor":feature,"name":name,"label":advice,
                      "condition":f"{feature} {operator} {threshold:g}",
                      "exposed":len(bad),"compared":len(safe),
                      "bad_avg":round(bad_avg,3),
                      "safe_avg":round(safe_avg,3) if safe_avg is not None else None,
                      "delta_pp":round(improvement,3),
                      "rank":rank})
    rules.sort(key=lambda x:x["rank"],reverse=True)
    best=next((r for r in rules if r["delta_pp"]>0),None)
    # If no realized P&L, rank the risk exposure within currently held
    # positions, but do NOT claim the exposure caused a loss.
    open_risk=[]
    for feature,operator,threshold,name,advice in RULES:
        hits=sum(1 for x in open_items
                 if feature in (x.get("features") or x.get("current_features") or {})
                 and (((x.get("features") or x.get("current_features") or {})[feature]>=threshold)
                      if operator==">=" else
                      ((x.get("features") or x.get("current_features") or {})[feature]<=threshold)))
        if hits:
            open_risk.append({"name":name,"label":advice,
                              "hits":hits,"factor":feature})
    open_risk.sort(key=lambda x:x["hits"],reverse=True)
    status="EMPTY"
    improvement=None
    if best:
        status="EXPLORATORY"
        improvement={"delta_pp":best["delta_pp"],"baseline_pct":round(average,3),
                     "scenario_pct":round(average+best["delta_pp"],3),
                     "excluded_n":best["exposed"],"sample_n":len(completed),
                     "label":best["name"],"validated":False,"method":"SCENARIO"}
    if completed and not best:status="OBSERVED"
    if not completed and (signal_count or group):status="LIVE_OBSERVATION"
    # For many trades, select rule on chronology train and report its
    # incremental effect on unseen recent test, not the training itself.
    if len(completed)>=20:
        sorted_trades=sorted(completed,key=lambda x:(x["at"] or 0,x["key"]))
        cut=max(10,int(len(sorted_trades)*.7))
        train,test=sorted_trades[:cut],sorted_trades[cut:]
        if len(test)>=6:
            ranked=[]
            for feature,op,threshold,name,advice in RULES:
                eligible=[x for x in train if feature in x["features"]]
                if len(eligible)<8:continue
                def risk(x):
                    v=x["features"].get(feature)
                    if v is None:return False
                    return v>=threshold if op==">=" else v<=threshold
                bad=[x for x in eligible if risk(x)]
                safe=[x for x in eligible if not risk(x)]
                if len(bad)<3 or len(safe)<3:continue
                if _mean(x["closed_roi"] for x in bad)>=_mean(x["closed_roi"] for x in safe):
                    continue
                ranked.append((-sum(x["closed_roi"] for x in bad)/len(train),
                               feature,op,threshold,name,advice))
            if ranked:
                selected=max(ranked)
                _,feature,op,threshold,name,advice=selected
                risk=lambda x:(feature in x["features"] and
                    (x["features"][feature]>=threshold if op==">=" else
                     x["features"][feature]<=threshold))
                bad=[x for x in test if risk(x)]
                known=sum(feature in x["features"] for x in test)
                if bad and known==len(test):
                    baseline=_mean(x["closed_roi"] for x in test)
                    delta=-sum(x["closed_roi"] for x in bad)/len(test)
                    ci=_cluster_ci(test,risk)
                    verified=bool(ci and ci[0]>0 and delta>0)
                    improvement={"delta_pp":round(delta,3),
                                 "baseline_pct":round(baseline,3),
                                 "scenario_pct":round(baseline+delta,3),
                                 "excluded_n":len(bad),"sample_n":len(test),
                                 "label":name,"validated":verified,
                                 "ci95_pp":ci,"method":"RECENT_HOLDOUT"}
                    best={"name":name,"label":advice,"factor":feature,
                          "delta_pp":round(delta,3)}
                    status="VALIDATED" if verified else "HOLDOUT"
    observed_n=signal_count+len(group)
    evidence_samples=len(completed)+len(open_marked)
    cover_pct=round(sum(cover.values())/len(cover),1)
    # An evidence coverage index, not a probability of correctness.
    maturity=round(min(100,35*math.log1p(evidence_samples)/math.log(41)
                      +45*(cover_pct/100)+20*min(1,len(completed)/20)))
    scenario=best["label"] if best else (
        open_risk[0]["label"] if open_risk else
        "기존 진입 유지 · 신규 증거 축적")
    finding=best["name"] if best else (
        open_risk[0]["name"]+" 노출" if open_risk else
        "특정 손실 선행조건 미검증")
    mode="과거 종료거래 비교" if best else (
        "현재 보유 위험노출" if open_risk else "연구 신호 관측")
    # Do not present price drops / volume increase as a proven root cause.
    cause_kind="상관 가설" if best else "진입조건 관측"
    return {
        "frame":frame,"label":label,"status":status,"signals":signal_count,
        "entries":len(group),"closed":len(completed),"open":len(open_items),
        "open_marked":len(open_marked),"closed_missing":len(unknown),
        "avg_realized_pct":round(average,3) if average is not None else None,
        "avg_open_pct":round(avg_open,3) if avg_open is not None else None,
        "avg_hold_market_score":round(avg_hold,2) if avg_hold is not None else None,
        "hold_market_samples":total_market_obs,
        "coverage":cover,"coverage_index":cover_pct,"maturity":maturity,
        "patterns":patterns[:6],
        "finding":finding,"finding_basis":mode,"cause_kind":cause_kind,
        "proposal":scenario,"risk_count":best["exposed"] if best and "exposed" in best else
                      open_risk[0]["hits"] if open_risk else 0,
        "risk_open":open_risk[:3],
        "improvement":improvement,
        "source":source,"updated_at_epoch":now,
        "sample_type":"realized" if completed else "open" if open_items else "signals",
    }


def _mean(xs):
    vals=[x for x in xs if x is not None]
    return sum(vals)/len(vals) if vals else None


def _cluster_ci(rows,rule,reps=300):
    by_day=defaultdict(list)
    for row in rows:
        ts=row["at"]
        day=dt.datetime.fromtimestamp(ts,dt.timezone(dt.timedelta(hours=9))).date().isoformat() if ts else "unknown"
        by_day[day].append(row)
    days=list(by_day)
    if len(days)<5:return None
    rng=random.Random(4040)
    effects=[]
    for i in range(reps):
        sampled=[r for d in (rng.choice(days) for _ in days) for r in by_day[d]]
        effects.append(-sum(r["closed_roi"] for r in sampled if rule(r))/len(sampled))
    effects.sort()
    return [round(effects[int(reps*.025)],3),round(effects[int(reps*.975)],3)]


def _matches_symbol(p,a):
    # Prevent a different same-ticker/same-day trade from being used.
    return bool(a["symbol"] and a["symbol"]==str(p.get("code") or p.get("market") or ""))


def _build_trades(raw,ledger,signals,archive,market,strategy,now,events):
    original=raw.get("positions") or []
    live=[r for r in ledger.get("positions",[]) if isinstance(r,dict)]
    executions=[r for r in (raw.get("executions") or ledger.get("executions") or [])
                if isinstance(r,dict)]
    fills_by_position=defaultdict(list)
    for fill in executions:
        if fill.get("position_id") is not None:
            fills_by_position[str(fill["position_id"])].append(fill)
    rows=[]
    seen=set()
    for row in original+live:
        if not isinstance(row,dict):continue
        frame=journal._frame(row.get("tf") or row.get("frame"),strategy)
        if not frame and strategy not in ("gmo","close"):continue
        key=(row.get("id"),frame,str(row.get("code") or row.get("market") or ""),
             str(row.get("entry_ts") or row.get("entry_at") or row.get("entry_date") or ""))
        if key in seen:continue
        seen.add(key)
        if str(row.get("status") or "").upper() in ("RESET_B_V1",):continue
        match=_link(signals,row,market,strategy)
        feats=dict(match["features"]) if match is not None else {}
        # A current holding's indicators describe CURRENT exposure, not a
        # feature recorded at the historical moment of buying.
        context=journal.extract_features(row,now)
        if match is None and not feats:
            # Do not convert current indicators into entry-time causes.
            pass
        entry=_row_time(row,("entry_at","entry_ts","buy_at","time","entry_date"))
        exit_ts=_row_time(row,("exit_at","closed_at","sold_at","sell_at"))
        result,state=_returns(row,fills_by_position.get(str(row.get("id")),[]))
        open_roi=_unrealized_pct(row)
        if not _is_closed(row):state="OPEN"
        hold_avg,hold_n=_market_interval(*events,entry,exit_ts or now)
        rows.append({"key":str(key),"frame":frame,"raw":row,
                     "features":feats,"current_features":context,
                     "closed_roi":result,"open_roi":open_roi,
                     "state":state,"at":entry or (match["signal_ts"] if match else None),
                     "hold_market_avg":hold_avg,"hold_market_n":hold_n})
    # If a source DB has not arrived on the phone but position snapshots were
    # captured previously, still count open holdings without fabricating P&L.
    for row in archive.get("positions",[]):
        if row.get("frame") not in (FRAMES+("gmo",)):continue
        key=row.get("position_key","")
        if any(key and key in str(t["key"]) for t in rows):continue
        if row.get("status") in ("CLOSED","DONE","SOLD"):continue
        # The same position can have dozens of periodic journal rows.
        older=next((r for r in rows if r.get("archive_key")==key),None)
        if older:continue
        last=max((v for v in archive["positions"] if v.get("position_key")==key),
                 key=lambda v:v.get("observed_minute") or 0)
        if last is not row:continue
        entry=last.get("entry_price")
        mark=last.get("mark")
        open_roi=(100*(mark/entry-1)) if entry and mark and entry>0 and mark>0 else None
        rows.append({"key":"archived:"+key,"archive_key":key,
                     "frame":row.get("frame"),"features":{},
                     "current_features":journal._json_obj(row.get("extra_json")),
                     "raw":row,"closed_roi":None,"open_roi":open_roi,
                     "state":"OPEN","at":row.get("entry_ts"),
                     "hold_market_avg":None,"hold_market_n":0})
    return rows


def analyze(root,market,strategy,research_json="{}",ledger_json="{}"):
    if (market,strategy) not in SCOPES:
        return json.dumps({"ok":False,"message":"선택 전략 확인 필요"},ensure_ascii=False)
    from time import time
    now=int(time())
    research=journal._json_obj(research_json)
    ledger=journal._json_obj(ledger_json)
    # Include every real sample visible at the moment ANALYZE is pressed.
    try:
        journal.capture_scope(root,market,strategy,research,ledger,now)
        archive=journal.stored(root,market,strategy)
    except (OSError,sqlite3.Error,ValueError) as exc:
        archive={"features":[],"positions":[]}
    raw=old._read_source(root,market,strategy)
    events=old._market_events(root,market)
    signals=_signal_rows(raw,research,market,strategy,archive)
    # Chronological score at signal time is available for recent signals even
    # when the original strategy did not persist a market_score column.
    for signal in signals:
        if "market_score" not in signal["features"]:
            value=old._point_in_time_market(*events,signal["signal_ts"],market)
            if value is not None:signal["features"]["market_score"]=value
    trades=_build_trades(raw,ledger,signals,archive,market,strategy,now,events)
    available=["gmo"] if strategy=="gmo" else (["day"] if strategy=="close" else list(FRAMES))
    frames=[_frame_summary(f,signals,trades,archive,events,now,raw["source"])
            for f in available]
    report={
        "ok":True,"schema":"ARTITRADE_AI_V040",
        "market":market,"strategy":strategy,"title":TITLES[market,strategy],
        "generated_at_epoch":now,
        "scope":market+"/"+strategy,
        "source":raw["source"],"frames":frames,
        "signals":sum(x["signals"] for x in frames),
        "open":sum(x["open"] for x in frames),
        "closed":sum(x["closed"] for x in frames),
        "feature_snapshots":len(archive["features"]),
        "position_snapshots":len(archive["positions"]),
        "error":raw.get("errors",[]),
    }
    try:
        journal.save_report(root,market,strategy,report,now)
    except (OSError,sqlite3.Error,ValueError):
        pass
    return json.dumps(report,ensure_ascii=False,allow_nan=False)
