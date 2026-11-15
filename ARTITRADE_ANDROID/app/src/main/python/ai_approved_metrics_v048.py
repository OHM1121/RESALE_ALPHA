"""ARTITRADE v048: user-approved read-only trading research evidence.

Numbers approved: 02,05,09,10,11,13,14,15,16,17.
No strategy condition, order object, broker adapter, or cash ledger is modified.
Observations are in a market-specific research-only SQLite DB, not order DBs.
Every missing measurement is NULL with a reason, not zero or an estimate.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import random
import sqlite3
import statistics
import time
from collections import Counter,defaultdict
from pathlib import Path
from urllib.parse import quote

import ai_research_v040 as jr
import ai_entry_context_v047 as entry

SCHEMA="ARTITRADE_APPROVED_TRADE_EVIDENCE_V048"
APPROVED=("02","05","09","10","11","13","14","15","16","17")
KST=dt.timezone(dt.timedelta(hours=9))
MAX_SOURCE_AGE_SEC=3
PERIOD=entry.PERIOD

def _num(value):
    return jr._number(value)

def _first(mapping,*names):
    if not isinstance(mapping,dict):return None
    for name in names:
        if name in mapping and mapping[name] is not None and str(mapping[name])!="":
            return mapping[name]
    return None

def _time_ms(value):
    """Precise source times; rejects undocumented local time and fake dates."""
    if value is None or isinstance(value,bool):return None
    try:
        if isinstance(value,(int,float)):
            if not math.isfinite(value):return None
            s=str(int(value))
            if len(s)==14 and 1990<=int(s[:4])<=2100:
                d=dt.datetime.strptime(s,"%Y%m%d%H%M%S").replace(tzinfo=KST)
                return int(d.timestamp()*1000)
            if value>1e12:return int(value)
            if value>1e9:return int(value*1000)
        raw=str(value).strip()
        if len(raw)==14 and raw.isdigit():
            d=dt.datetime.strptime(raw,"%Y%m%d%H%M%S").replace(tzinfo=KST)
            return int(d.timestamp()*1000)
        if len(raw)>=10 and raw[4]=="-" and raw[7]=="-":
            d=dt.datetime.fromisoformat(raw.replace("Z","+00:00"))
            if d.tzinfo is None:d=d.replace(tzinfo=KST)
            return int(d.timestamp()*1000)
    except (TypeError,ValueError,OverflowError):pass
    return None

def _metric(value=None,reason=None,source=None,**parts):
    result={"value":value,"source":source if value is not None else None,
            "missing_reason":reason if value is None else None}
    result.update(parts)
    return result

def _diff_ms(a,b):
    a,b=_time_ms(a),_time_ms(b)
    if a is None or b is None or b<a or b-a>86400000:return None
    return b-a

def entry_spread(row,when):
    """Only a timestamped book observed no later than three seconds after BUY."""
    book=_first(row,"entry_quote","quote_snapshot","buy_orderbook","best_book_at_entry")
    if not isinstance(book,dict):book=row
    bid=_num(_first(book,"bid","best_bid","bid_price"))
    ask=_num(_first(book,"ask","best_ask","ask_price"))
    ts=_time_ms(_first(book,"at_epoch","quote_at","timestamp","observed_at","book_at",
                       "entry_quote_at","quote_at_ms"))
    entry_ms=_time_ms(when)
    if bid is None or ask is None or ask<bid or bid<=0:
        return _metric(reason="NO_VALID_TIMESTAMPED_BEST_BOOK")
    if ts is None or entry_ms is None or abs(ts-entry_ms)>3000:
        return _metric(reason="BOOK_TIME_UNVERIFIED_OR_STALE",quote_timestamp_ms=ts)
    spread=ask-bid
    return _metric(round(10000*spread/((ask+bid)/2),6),
                   source="SOURCE_BEST_BID_ASK",unit="bps",
                   absolute=round(spread,10),best_bid=bid,best_ask=ask,
                   quote_timestamp_ms=ts,
                   quote_age_ms=entry_ms-ts,
                   formula="10000*(ask-bid)/((ask+bid)/2)")

def order_latency(row):
    """Four direct source timestamps; no reconstruction from daily price bars."""
    times={
       "signal":_first(row,"signal_at","signal_ts","signal_decision_at"),
       "submitted":_first(row,"order_sent_at","order_submit_at","submitted_at"),
       "accepted":_first(row,"order_ack_at","order_accepted_at","ack_at"),
       "filled":_first(row,"filled_at","order_filled_at","fill_at")
    }
    intervals={
       "signal_to_submit_ms":_diff_ms(times["signal"],times["submitted"]),
       "submit_to_ack_ms":_diff_ms(times["submitted"],times["accepted"]),
       "ack_to_fill_ms":_diff_ms(times["accepted"],times["filled"]),
       "submit_to_fill_ms":_diff_ms(times["submitted"],times["filled"]),
       "signal_to_fill_ms":_diff_ms(times["signal"],times["filled"])
    }
    present={k:v for k,v in intervals.items() if v is not None}
    return _metric(present if present else None,
                   reason="ORDER_STAGE_TIMESTAMPS_NOT_RECORDED",
                   source="SOURCE_ORDER_STAGE_EVENTS" if present else None,
                   timestamps_ms={k:_time_ms(v) for k,v in times.items()},
                   time_precision="MILLISECONDS_ONLY_IF_SOURCE_CONTAINS_MS")

def _norm_bars(bars):
    clean={}
    for r in bars or ():
        try:
            ts=int(r[0])
            hi,lo,close=[_num(r[i]) for i in (1,2,3)]
            if any(x is None for x in (hi,lo,close)) or not hi>=close>=lo>0:
                continue
            volume=_num(r[4]) if len(r)>4 else None
            turnover=_num(r[5]) if len(r)>5 else None
            opened=_num(r[6]) if len(r)>6 else None
            if opened is not None and not lo<=opened<=hi:opened=None
            clean[ts]=(ts,hi,lo,close,volume,turnover,opened)
        except (TypeError,ValueError,IndexError,OverflowError):continue
    return [clean[t] for t in sorted(clean)]

def entry_open_gap(bars,entry_ts,frame):
    """True entry-candle open vs preceding completed candle close, in percent.

    Missing original opens remain NULL; the last trade price is never used as
    an opening-price substitute. Bars may arrive after candle finalization,
    since the OPEN value occurred no later than this bar's entry timestamp.
    """
    period=PERIOD.get(frame)
    if period is None:return _metric(reason="UNKNOWN_TIMEFRAME")
    series=_norm_bars(bars)
    for index,bar in enumerate(series):
        if not bar[0]<=entry_ts<bar[0]+period:continue
        if index==0:return _metric(reason="NO_PREVIOUS_COMPLETED_CANDLE")
        prev=series[index-1]
        opened=_num(bar[6])
        previous_close=_num(prev[3])
        if opened is None or opened<=0:
            return _metric(reason="ENTRY_CANDLE_OPEN_UNAVAILABLE")
        if previous_close is None or previous_close<=0:
            return _metric(reason="PREVIOUS_CLOSE_UNAVAILABLE")
        gap=round(100*(opened/previous_close-1),6)
        return _metric(gap,source="VERIFIED_ENTRY_OPEN_AND_PREVIOUS_CLOSE",
                       unit="pct",opening_price=opened,
                       previous_close=previous_close,
                       formula="100*(entry_candle_open/previous_close-1)")
    return _metric(reason="ENTRY_CANDLE_NOT_IN_AVAILABLE_HISTORY")


def entry_technicals(bars,entry_ts,frame,entry_price=None,asof=None):
    """Compute #09 after close, #10/#11 from predecessor CLOSED candles only."""
    seconds=PERIOD.get(frame)
    if seconds is None:raise ValueError("unknown timeframe")
    asof=time.time() if asof is None else float(asof)
    series=_norm_bars(bars)
    index=next((i for i,x in enumerate(series) if x[0]<=entry_ts<x[0]+seconds),None)
    empty={
        "09":_metric(reason="ENTRY_CANDLE_NOT_FINALIZED"),
        "10":_metric(reason="INSUFFICIENT_PRE_ENTRY_HISTORY"),
        "11":_metric(reason="TRUE_PRE_ENTRY_TURNOVER_MISSING"),
        "13":_metric(reason="NO_COMPLETE_POST_ENTRY_CANDLE")
    }
    if index is None:
        for v in empty.values():v["missing_reason"]="ENTRY_CANDLE_NOT_IN_AVAILABLE_HISTORY"
        return empty
    entry_bar=series[index]
    prior=[x for x in series[:index] if x[0]+seconds<=entry_bar[0]]
    asof_close=entry_bar[0]+seconds<=asof
    if asof_close:
        hi,lo,close,opened=entry_bar[1],entry_bar[2],entry_bar[3],entry_bar[6]
        span=hi-lo
        if opened is None:
            empty["09"]=_metric(reason="ENTRY_OPEN_NOT_AVAILABLE")
        elif span<=0:
            empty["09"]=_metric({"open":opened,"high":hi,"low":lo,"close":close,
                "direction":"DOJI","body_pct_of_range":None,
                "upper_wick_pct_of_range":None,"lower_wick_pct_of_range":None},
                source="FINAL_ENTRY_CANDLE_OHLCV",denominator="ZERO_RANGE")
        else:
            body=abs(close-opened)
            top=max(close,opened)
            bottom=min(close,opened)
            empty["09"]=_metric({
                "open":opened,"high":hi,"low":lo,"close":close,
                "direction":"UP" if close>opened else "DOWN" if close<opened else "DOJI",
                "body_pct_of_range":round(100*body/span,6),
                "upper_wick_pct_of_range":round(100*(hi-top)/span,6),
                "lower_wick_pct_of_range":round(100*(bottom-lo)/span,6),
                "close_location_pct":round(100*(close-lo)/span,6)
            },source="FINAL_ENTRY_CANDLE_OHLCV",phase="AFTER_ENTRY_CANDLE_FINALIZED")
    if len(prior)>=20:
        # The 14 true ranges end BEFORE the entry bar. No look-ahead.
        prev=prior[-15:]
        trs=[max(prev[i][1]-prev[i][2],
                 abs(prev[i][1]-prev[i-1][3]),
                 abs(prev[i][2]-prev[i-1][3])) for i in range(1,15)]
        atr=sum(trs)/14
        close=prior[-1][3]
        closes=[x[3] for x in prior[-20:]]
        center=statistics.mean(closes)
        sigma=statistics.pstdev(closes)
        upper,lower=center+2*sigma,center-2*sigma
        vals={"atr14_simple":round(atr,8),
              "atr14_pct_of_prev_close":round(100*atr/close,6) if close>0 else None,
              "bollinger20_mid":round(center,8),
              "bollinger20_upper":round(upper,8),
              "bollinger20_lower":round(lower,8),
              "bollinger20_bandwidth_pct":round(100*(upper-lower)/center,6)
                  if center>0 else None,
              "bollinger20_percent_b":round((close-lower)/(upper-lower),6)
                  if upper>lower else None,
              "reference_close":close}
        empty["10"]=_metric(vals,source="PRE_ENTRY_20_CLOSED_CANDLES",
            phase="KNOWN_BEFORE_ENTRY",atr_definition="SMA14_TRUE_RANGE",
            bollinger_definition="SMA20_PLUS_MINUS_2_POP_STD")
    if len(prior)>=30:
        values=prior[-30:]
        volumes=[x[4] for x in values]
        amounts=[x[5] for x in values]
        if all(v is not None and v>=0 for v in volumes) and all(
            t is not None and t>=0 for t in amounts) and sum(volumes)>0:
            vwap=sum(amounts)/sum(volumes)
            px=_num(entry_price)
            empty["11"]=_metric({
                "prior30_true_vwap":round(vwap,8),
                "entry_price":px,
                "entry_minus_vwap_bps":round(10000*(px/vwap-1),6)
                    if px is not None and px>0 and vwap>0 else None,
                "traded_volume":round(sum(volumes),8),
                "traded_value":round(sum(amounts),8)
            },source="PRE_ENTRY_30_CLOSED_CANDLES_TRADED_VALUE",
               phase="KNOWN_BEFORE_ENTRY",
               definition="SUM_EXCHANGE_TURNOVER_DIVIDED_BY_SUM_VOLUME",
               no_ohlc_proxy=True)
    return empty

def after_entry_extremes(bars,entry_ts,frame,buy_price,end_ts=None,asof=None):
    """Entry candle excluded: highs before intra-candle BUY cannot become MFE."""
    seconds=PERIOD.get(frame)
    if seconds is None:raise ValueError("unknown timeframe")
    px=_num(buy_price)
    if px is None or px<=0:
        return _metric(reason="BUY_PRICE_NOT_AVAILABLE")
    ceiling=float(time.time() if asof is None else asof)
    if end_ts is not None:ceiling=min(ceiling,float(end_ts))
    candle_finish=(int(entry_ts)//seconds+1)*seconds
    eligible=[x for x in _norm_bars(bars)
              if x[0]>=candle_finish and x[0]+seconds<=ceiling]
    if not eligible:return _metric(reason="NO_FULL_POST_ENTRY_BAR")
    best=max(eligible,key=lambda x:x[1])
    worst=min(eligible,key=lambda x:x[2])
    return _metric({
        "mfe_pct":round(100*(best[1]/px-1),6),
        "mae_pct":round(100*(worst[2]/px-1),6),
        "highest_post_entry_price":best[1],
        "lowest_post_entry_price":worst[2],
        "mfe_epoch":best[0],"mae_epoch":worst[0],
        "mfe_elapsed_bars":(best[0]-candle_finish)//seconds+1,
        "mae_elapsed_bars":(worst[0]-candle_finish)//seconds+1,
        "observed_completed_bars":len(eligible),
        "first_observed_start":eligible[0][0],
        "last_observed_end":eligible[-1][0]+seconds,
        "coverage_gaps":sum(1 for a,b in zip(eligible,eligible[1:])
                            if b[0]-a[0]>seconds),
        "coverage_complete":eligible[0][0]==candle_finish and not any(
           b[0]-a[0]>seconds for a,b in zip(eligible,eligible[1:]))
    },source="POST_ENTRY_COMPLETED_OHLCV",
       entry_candle_excluded=True,realized_pnl=False,
       source_coverage_warning="MFE/MAE only among observed post-entry bars")

def signed_trade_flow(row,entry_ts,window_sec=60):
    """Only actual aggressor-tagged tick events. OHLC volume is not CVD."""
    sample=_first(row,"aggressor_ticks","signed_trades","trade_ticks")
    if not isinstance(sample,list):return _metric(reason="SIGNED_TRADE_TICKS_NOT_AVAILABLE")
    buyer=seller=0.0;n=0
    for tick in sample:
        if not isinstance(tick,dict):continue
        millis=_time_ms(_first(tick,"at","ts","timestamp","trade_at"))
        if millis is None or not 0<=entry_ts*1000-millis<=window_sec*1000:continue
        side=str(_first(tick,"aggressor_side","trade_side","side") or "").upper()
        qty=_num(_first(tick,"volume","qty","trade_volume","size"))
        if qty is None or qty<=0:continue
        if side in ("BID","BUY","B","ASK_TAKER"):buyer+=qty
        elif side in ("ASK","SELL","S","BID_TAKER"):seller+=qty
        else:continue
        n+=1
    if not n:return _metric(reason="NO_VERIFIED_SIGNED_TICKS_IN_WINDOW")
    total=buyer+seller
    return _metric({"cvd_qty":round(buyer-seller,10),
        "aggressive_buy_qty":round(buyer,10),
        "aggressive_sell_qty":round(seller,10),
        "buy_fraction_pct":round(100*buyer/total,6),
        "signed_ticks":n,"window_sec":window_sec},
        source="TIMESTAMPED_AGGRESSOR_TRADE_TICKS",phase="PRE_ENTRY_ONLY")

def exit_steps(position,executions,entry_ts,entry_price):
    """Exact position+frame+symbol SELL fills only, never ticker-only joins."""
    pid=str(_first(position,"id","position_id") or "")
    frame=str(_first(position,"tf","frame") or "")
    symbol=str(_first(position,"code","market") or "")
    if not pid:return _metric(reason="POSITION_ID_UNAVAILABLE")
    px=_num(entry_price)
    buys=[]
    sells=[]
    for raw in executions or ():
        if not isinstance(raw,dict) or str(raw.get("position_id") or "")!=pid:
            continue
        if frame and raw.get("tf") and str(raw["tf"])!=frame:continue
        if symbol and raw.get("code",raw.get("market")) and str(
                raw.get("code",raw.get("market")))!=symbol:continue
        at_ms=_time_ms(_first(raw,"at","ts","filled_at","execution_at"))
        if at_ms is None or at_ms//1000<entry_ts-3:continue
        side=str(raw.get("side") or "").upper()
        if side=="BUY":buys.append(raw)
        elif side=="SELL":sells.append((at_ms,raw))
    sells.sort(key=lambda x:(x[0],str(x[1].get("event_key") or x[1].get("id") or "")))
    if not sells:return _metric(reason="NO_CONFIRMED_SELL_EXECUTIONS")
    initial_qty=_num(_first(position,"qty0","entry_qty","original_qty","initial_qty"))
    if initial_qty is None and buys:
        initial_qty=sum(_num(_first(x,"qty","quantity")) or 0 for x in buys)
    steps=[]
    total_qty=total_pnl=0.0
    complete_net=True
    for i,(ts,row) in enumerate(sells,1):
        qty=_num(_first(row,"qty","quantity"))
        sale=_num(_first(row,"price","fill_price"))
        net=_num(_first(row,"net_pnl","net_profit","realized_pnl"))
        fee=_num(_first(row,"fee","fees"))
        tax=_num(_first(row,"tax","taxes"))
        if qty is not None:total_qty+=qty
        if net is None:complete_net=False
        else:total_pnl+=net
        steps.append({"step":i,"epoch_ms":ts,
           "elapsed_seconds":round(ts/1000-entry_ts,3),
           "reason":row.get("reason"),
           "filled_qty":qty,"price":sale,"fee":fee,"tax":tax,
           "verified_net_pnl":net,
           "step_roi_pct":round(100*(sale/px-1),6)
               if px and px>0 and sale is not None else None,
           "fraction_of_initial_pct":round(100*qty/initial_qty,6)
               if qty is not None and initial_qty and initial_qty>0 else None})
    basis=px*initial_qty if px and initial_qty else None
    result={"steps":steps,"sell_fills_n":len(steps),
        "sold_qty_sum":round(total_qty,10),"initial_qty":initial_qty,
        "sold_pct":round(100*total_qty/initial_qty,6)
           if initial_qty and initial_qty>0 else None,
        "net_pnl_verified":round(total_pnl,8) if complete_net else None,
        "net_return_pct":round(100*total_pnl/basis,6)
           if complete_net and basis and basis>0 else None,
        "all_sell_net_pnl_verified":complete_net}
    return _metric(result,source="POSITION_ID_LINKED_EXECUTION_LEDGER",
        historical_partial_fills_possible=True)

def _at_or_before(bars,moment,seconds,max_stale=None):
    if moment is None:return None
    max_stale=seconds*3 if max_stale is None else max_stale
    choices=[r for r in bars if r[0]+seconds<=moment and
             0<=moment-(r[0]+seconds)<=max_stale]
    return max(choices,key=lambda r:r[0]) if choices else None

def benchmark_relative(bars,benchmark_name,frame,entry_ts,exit_ts,
                       entry_price,exit_price):
    """Only a fully observed, named KRX index or KRW-BTC benchmark."""
    seconds=PERIOD.get(frame)
    if seconds is None or not benchmark_name:
        return _metric(reason="BENCHMARK_IDENTITY_UNAVAILABLE")
    b=_norm_bars(bars)
    # Weekend KRX daily bars can be more than 3 days old.
    stale=seconds*5 if frame=="day" else seconds*3
    start=_at_or_before(b,entry_ts,seconds,stale)
    finish=_at_or_before(b,exit_ts,seconds,stale)
    buy,sell=_num(entry_price),_num(exit_price)
    if start is None or finish is None or exit_ts is None or exit_ts<entry_ts:
        return _metric(reason="TIMESTAMP_MATCHED_BENCHMARK_BARS_MISSING")
    if buy is None or sell is None or buy<=0 or sell<=0 or start[3]<=0:
        return _metric(reason="CLOSED_POSITION_PRICES_MISSING")
    ref_return=100*(finish[3]/start[3]-1)
    own_return=100*(sell/buy-1)
    return _metric({"benchmark":benchmark_name,
        "benchmark_entry_close":start[3],"benchmark_exit_close":finish[3],
        "benchmark_entry_bar_end":start[0]+seconds,
        "benchmark_exit_bar_end":finish[0]+seconds,
        "benchmark_return_pct":round(ref_return,6),
        "gross_instrument_return_pct":round(own_return,6),
        "gross_relative_return_pp":round(own_return-ref_return,6)},
        source="PRE_EVENT_CLOSED_BENCHMARK_CANDLES",
        caveat="Gross price return; commissions, taxes and slippage excluded",
        timeframe_alignment=frame)

def cohort_stats(verified_returns,*,source="VERIFIED_CLOSED_TRADES"):
    """Deterministic bootstrap CI; never a predictive guarantee."""
    vals=[float(x) for x in verified_returns if _num(x) is not None]
    n=len(vals)
    if n==0:return {"n":0,"status":"NO_VERIFIED_CLOSED_TRADES",
                    "mean_return_pct":None,"profit_factor":None,
                    "max_drawdown_pct":None,"ci95_mean_pct":None}
    wins=[x for x in vals if x>0]
    losses=[x for x in vals if x<0]
    gross_gains=sum(wins)
    gross_losses=-sum(losses)
    factor=round(gross_gains/gross_losses,6) if gross_losses>0 else None
    equity=1.0;peak=1.0;worst=0.0
    for pct in vals:
        equity*=max(0,1+pct/100)
        peak=max(equity,peak)
        worst=min(worst,100*(equity/peak-1))
    ci=None
    if n>=20:
        rng=random.Random(48017)
        samples=sorted(statistics.mean([vals[rng.randrange(n)]
                for _ in range(n)]) for _ in range(500))
        ci=[round(samples[12],5),round(samples[487],5)]
    return {"n":n,"source":source,
       "mean_return_pct":round(statistics.mean(vals),6),
       "median_return_pct":round(statistics.median(vals),6),
       "win_n":len(wins),"loss_n":len(losses),
       "flat_n":n-len(wins)-len(losses),
       "win_rate_pct":round(100*len(wins)/n,6),
       "gross_positive_return_points":round(gross_gains,6),
       "gross_negative_return_points":round(gross_losses,6),
       "profit_factor":factor,
       "profit_factor_zero_losses":not losses,
       "max_drawdown_pct":round(worst,6),
       "ci95_mean_pct":ci,
       "ci_method":"500_REPLICATE_FIXED_SEED_BOOTSTRAP" if ci else "N_LT_20",
       "chronological_order_required":True,
       "verified_to_predict_future":False}
