"""Independent RSI20 MARKET_TREND cohort analysis across parent AT/MA accounts.

Never mixes RSI20 shadow/virtual signals with original AT/MA research, or
claimed fills with real confirmed broker orders. Source is trend journal and
its unchanged underlying broker/strategy ledgers included in the export.
"""
from __future__ import annotations
import json
import sqlite3
import statistics
import time
from collections import Counter,defaultdict
from pathlib import Path
from contextlib import closing

import ai_entry_context_v047 as entry

FRAMES=("5m","30m","60m","day")
LABELS={"5m":"5분봉","30m":"30분봉","60m":"60분봉","day":"일봉"}


def _read(root,market,limit=None):
    if market not in ("stock","coin"):raise ValueError("invalid market")
    db=Path(root)/"market_trend_v043.sqlite3"
    result={"events":[],"positions":[],"virtual":[],"source":str(db.name)}
    if db.is_file():
      with closing(sqlite3.connect(db.resolve().as_uri()+"?mode=ro",
                                   uri=True,timeout=8)) as c:
          c.row_factory=sqlite3.Row
          names={x[0] for x in c.execute(
              "SELECT name FROM sqlite_master WHERE type='table'")}
          for key,table in (("events","events"),("positions","position_state"),
                            ("virtual","virtual_position_state")):
              if table not in names:continue
              sql="SELECT * FROM "+table+" WHERE market=?"
              params=[market]
              if limit:
                  sql+=(" ORDER BY "+("at" if key=="events" else "updated_at")+
                        " DESC LIMIT ?")
                  params.append(int(limit))
              result[key]=[dict(r) for r in c.execute(sql,params)]
    # New MT54 own ledger is read-only. Its synthetic decision rows are
    # deduplicated against MT54:SIG events in the original research journal.
    new_db=Path(root)/market/'trend'/'market_trend_wallet_v054.sqlite3'
    if new_db.is_file():
        with closing(sqlite3.connect(new_db.resolve().as_uri()+'?mode=ro',uri=True,timeout=8)) as c:
            c.row_factory=sqlite3.Row
            existing={x['event_key'] for x in result['events']}
            positions=[dict(x) for x in c.execute('SELECT * FROM positions')]
            for p in positions:
                data=json.loads(p.get('payload') or '{}')
                result['positions'].append({'market':market,'strategy':'trend',
                    'frame':p['tf'],'symbol':p['symbol'],'position_id':p['id'],
                    'status':'OPEN' if p['status']=='TREND_OPEN' else p['status'],
                    'payload':p['payload']})
            for e in (dict(x) for x in c.execute('SELECT * FROM executions ORDER BY id')):
                mode='ACTUAL' if e['execution_mode']=='LIVE' else 'PAPER'
                if e['side']=='BUY':
                    key='MT54:SIG:'+market+':'+e['symbol']+':'+e['tf']+':'+str(e['at'])
                    prefix='MT54:SIG:'+market+':'+e['symbol']+':'+e['tf']+':'
                    if not any(str(x).startswith(prefix) for x in existing):
                        result['events'].append(dict(event_key=key,at=e['at'],market=market,
                             strategy='trend',frame=e['tf'],symbol=e['symbol'],
                             mode=mode,reason='BOUGHT',price=e['price'],
                             rsi20_live=None,payload='{}'))
                    result['events'].append(dict(event_key=e['event_key']+':FILLED',
                        at=e['at'],market=market,strategy='trend',frame=e['tf'],
                        symbol=e['symbol'],mode=mode,reason='MT_BUY_FILLED',
                        payload=json.dumps({'position_id':e['position_id']})))
                elif e['side']=='SELL':
                    result['events'].append(dict(event_key=e['event_key']+':FILLED',
                        at=e['at'],market=market,strategy='trend',frame=e['tf'],
                        symbol=e['symbol'],mode=mode,reason=e['reason'],
                        payload=json.dumps({'position_id':e['position_id'],'net_pnl':e['net_pnl']})))
    return result


def _stats(items):
    return {"n":len(items),
            "mean":round(statistics.mean(items),5) if items else None,
            "median":round(statistics.median(items),5) if items else None,
            "min":min(items) if items else None,
            "max":max(items) if items else None}


def _by_frame(root,market,frame,events,positions,virtuals,contexts):
    sigs=[x for x in events if x.get("frame")==frame and
          str(x.get("event_key","")).startswith(("MT43:SIG:","MT54:SIG:"))]
    fills=[x for x in events if x.get("frame")==frame and
           x.get("reason")=="MT_BUY_FILLED" and
           x.get("mode") in ("ACTUAL","PAPER")]
    exits=[x for x in events if x.get("frame")==frame and
           str(x.get("event_key","")).startswith(("MT43:SELL:","MT54:SELL:")) and
           str(x.get("event_key","")).endswith(":FILLED")]
    by_position=defaultdict(list)
    for x in exits:
        try:
            data=json.loads(x.get("payload") or "{}")
            pid=data.get("position_id")
            if pid is not None:
                by_position[(x["strategy"],str(pid))].append((x,data))
        except (TypeError,ValueError):continue
    concluded=[]
    missing_closed=0
    open_count=0
    for pos in positions:
        if pos.get("frame")!=frame:continue
        try:record=json.loads(pos.get("payload") or "{}")
        except (ValueError,TypeError):record={}
        state=str(pos.get("status") or "UNKNOWN").upper()
        if state=="OPEN":
            open_count+=1
            continue
        if state!="CLOSED":continue
        key=(pos.get("strategy"),str(pos.get("position_id")))
        history=by_position.get(key) or []
        start=record.get("entry");qty=record.get("original_qty")
        try:
            basis=float(start)*float(qty)
            nets=[float(x[1]["net_pnl"]) for x in history]
            if basis<=0 or not nets:raise ValueError("No validated net fills")
            concluded.append(100.0*sum(nets)/basis)
        except (ValueError,TypeError,KeyError):missing_closed+=1
    virtual_frame=[x for x in virtuals if x.get("frame")==frame]
    entry_frame=[x for x in contexts if x.get("frame")==frame]
    real_context=[x for x in entry_frame if x.get("role")=="ACTUAL"]
    finalized=[x["context"] for x in real_context
               if x.get("state")=="FINALIZED"]
    rising=[x["high_slope_pct_per_bar"] for x in finalized
            if x.get("high_slope_pct_per_bar") is not None]
    low_slopes=[x["low_slope_pct_per_bar"] for x in finalized
                if x.get("low_slope_pct_per_bar") is not None]
    volumes=[x["volume_ratio30"] for x in finalized
             if x.get("volume_ratio30") is not None]
    mas={str(width):Counter(
        x.get("sma",{}).get(str(width),{}).get("position")
        for x in finalized) for width in entry.MOVING}
    strength=[x.get("entry_strength_pct") for x in finalized
              if x.get("entry_strength_pct") is not None]
    n_closed=len(concluded)
    evidence={
        "frame":frame,"signal_count":len(sigs),
        "real_signals":sum(1 for x in sigs if x["mode"]=="ACTUAL"),
        "virtual_signals":sum(1 for x in sigs if x["mode"]=="VIRTUAL"),
        "confirmed_buy_fills":len(fills),
        "confirmed_sell_fills":len(exits),
        "position_states":len([x for x in positions if x["frame"]==frame]),
        "virtual_position_states":len(virtual_frame),
        "exit_reasons":dict(Counter(x["reason"] for x in exits)),
        "signal_rejection_reasons":dict(Counter(
            x["reason"] for x in sigs if x["mode"]=="VIRTUAL")),
        "entry_context_rows":len(entry_frame),
        "final_entry_candles":len(finalized),
        "entry_strength_timestamped_n":len(strength),
        "high_slope_pct_per_bar":_stats(rising),
        "low_slope_pct_per_bar":_stats(low_slopes),
        "entry_volume_ratio30":_stats(volumes),
        "entry_strength_pct":_stats(strength),
        "ma_close_positions":{k:dict(v) for k,v in mas.items()},
        "closed_roi_n":n_closed,
        "closed_roi_unverified":missing_closed,
        "mean_closed_roi_pct":round(statistics.mean(concluded),5) if concluded else None
    }
    cover=100*len(finalized)/max(1,len(real_context))
    return {
        "frame":frame,"label":LABELS[frame],
        "status":"OBSERVED" if n_closed else "LIVE_OBSERVATION" if sigs else "EMPTY",
        "signals":len(sigs),"entries":len(fills),
        "evaluated":n_closed,"closed":n_closed+missing_closed,
        "closed_missing":missing_closed,"open":open_count,
        "avg_realized_pct":evidence["mean_closed_roi_pct"],
        "coverage":{"market_score":0,"rsi":0,"atr_pct":0,
                    "volume_ratio":round(cover,2),"williams_r":0,
                    "spread_pct":0,"gap_pct":0,"score":0},
        "diagnosis":{
            "confidence":"추가 검증 필요" if n_closed<20 else "기간 분리검증 필요",
            "sample_n":len(sigs),
            "cause":"RSI(20) 매수 신호와 실제 체결·가상 미체결을 구분함",
            "decline":"종료된 실거래의 매도 사유와 선행요인별 정량 비교 필요",
            "rise":"마켓트렌드로 진입한 매수봉 기울기·이평·거래량의 후속수익률 분석",
            "recommendation":"검증된 표본 확보 후 조건변경 별도 검토",
            "effect":None},
        "improvement":None,"market_trend_rsi20":evidence,
        "entry_candle_evidence":evidence,
        "sample_type":"realized" if n_closed else "signals",
    }


def _approved_metrics(root,market):
    try:
        from ai_approved_collect_v048 import summarize
        return summarize(root,market,"MARKET_TREND",preview_max=10)
    except (ImportError,OSError,sqlite3.Error,ValueError,TypeError) as exc:
        return {"schema":"ARTITRADE_APPROVED_TRADE_EVIDENCE_V048",
                "status":"READ_FAILED","error":type(exc).__name__}


def analyze(root,market):
    if market not in ("stock","coin"):
        raise ValueError("unknown market")
    data=_read(root,market)
    # Independent cohort: both AT/MA parent wallets, but no native buys.
    contexts=entry.captured(root,market,"MARKET_TREND")
    frames=[_by_frame(root,market,frame,data["events"],data["positions"],
                      data["virtual"],contexts) for frame in FRAMES]
    return {
        "ok":True,"scope":market+"/trend","schema":"ARTITRADE_AI_TREND_V047",
        "market":market,"strategy":"trend","entry_mode":"MARKET_TREND",
        "title":("주식" if market=="stock" else "코인")+" · 마켓트렌드",
        "generated_at_epoch":int(time.time()),
        "source":"market_trend_v043.sqlite3 + independent MT54 wallet (read only)",
        "frames":frames,
        "signals":sum(x["signals"] for x in frames),
        "closed":sum(x["closed"] for x in frames),
        "open":sum(x["open"] for x in frames),
        "position_snapshots":len(data["positions"]),
        "virtual_positions":len(data["virtual"]),
        "events_total":len(data["events"]),
        "entry_context_record_count":len(contexts),
        "source_record_counts":{
            "signal_table_total":sum(x["signals"] for x in frames),
            "position_rows_loaded":len(data["positions"]),
            "execution_rows_loaded":len(data["events"]),
            "virtual_rows_loaded":len(data["virtual"]),
            "source_rows_truncated":False,
            "all_original_rows_in_verified_export":True},
        "approved_metrics_v048":_approved_metrics(root,market),
        "market_trend_rsi20":{
            "signals":sum(x["signals"] for x in frames),
            "filled":sum(x["market_trend_rsi20"]["confirmed_buy_fills"] for x in frames),
            "virtual":sum(x["market_trend_rsi20"]["virtual_signals"] for x in frames),
            "frames":{x["frame"]:x["market_trend_rsi20"] for x in frames}},
        "error":[]
    }


def research_rows(root,market,limit=350):
    """Bounded UI list, complete original rows always in the verified export."""
    raw=_read(root,market,limit=limit)
    rows=[]
    for e in raw["events"]:
        key=str(e.get("event_key") or "")
        if not key.startswith(("MT43:SIG:","MT54:SIG:")):continue
        try:meta=json.loads(e.get("payload") or "{}")
        except (ValueError,TypeError):meta={}
        # Older ACTUAL signals did not distinguish paper fills from broker
        # fills. Preserve them as unverified unless provenance is explicit.
        origin=(("LIVE" if meta.get("execution_mode")=="ACTUAL"
                 else "UNKNOWN_FILLED_LEDGER") if e["mode"]=="ACTUAL"
                else "PAPER" if e["mode"]=="PAPER" else "SHADOW_UNFILLED")
        rows.append({"id":key,"strategy":e["strategy"],
            "entry_mode":"MARKET_TREND","tf":e["frame"],"code":e["symbol"],
            "at":e["at"],"price":e["price"],
            "score":meta.get("score"),"bought":e["mode"] in ("ACTUAL","PAPER"),
            "entry_observed":e["mode"]=="VIRTUAL","execution_mode":origin,
            "outcome":"REAL_FILL" if origin=="LIVE" else
                      "PAPER_FILL" if origin=="PAPER" else
                      "UNVERIFIED_FILL" if origin=="UNKNOWN_FILLED_LEDGER" else "VIRTUAL",
            "reason":e["reason"],"rsi20_live":e["rsi20_live"],
            "original_mode":e["mode"],"evidence_source":"TREND_JOURNAL_OR_STANDALONE_LEDGER"})
    return rows
