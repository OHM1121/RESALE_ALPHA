"""Read-only official Upbit 1m/60m backtest for KRW-NEAR Aug 11, 2026.

Compare BOTH timestamp interpretations at 2026-08-11 23:00 KST.
For a genuine RSI20 no-buy proof, use the entire official 1-minute
high-low range containing the actual -12-second order decision.
Never hallucinate an exact 1-second quote from completed minute OHLC.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from pathlib import Path

import requests
from market_trend_v043 import rsi20, RSI_ARM, RSI_WATCH

KST = dt.timezone(dt.timedelta(hours=9))
MARKET = "KRW-NEAR"
BASE = "https://api.upbit.com"
OUTPUT = Path("near_20260811_official_1m_evidence")
OUTPUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "ARTITRADE-official-readonly-2026-08-11-audit",
                  "Accept": "application/json"})

def get_candles(unit, until, n=200):
    endpoint = f"{BASE}/v1/candles/minutes/{unit}"
    utc_to = until.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for i in range(7):
        try:
            res = S.get(endpoint, params={"market": MARKET, "to": utc_to,
                                          "count": n}, timeout=15)
            if res.status_code == 200 and isinstance(res.json(), list):
                return res.json()
            print("UPBIT_RETRY",unit,res.status_code,res.text[:130],flush=True)
        except (requests.RequestException,ValueError) as exc:
            print("UPBIT_RETRY",unit,type(exc).__name__,flush=True)
        time.sleep(1+i)
    raise RuntimeError(f"OFFICIAL_UPBIT_FETCH_FAILED:{unit}")

def start_kst(row):
    return dt.datetime.fromisoformat(row["candle_date_time_kst"]).replace(tzinfo=KST)

def hour_reconstruct(start, minutes):
    vals=sorted((x for x in minutes
                 if start <= start_kst(x) < start+dt.timedelta(hours=1)),
                key=start_kst)
    if not vals:return None
    return {"minute_count":len(vals),
            "first_minute_start":start_kst(vals[0]).isoformat(),
            "last_minute_start":start_kst(vals[-1]).isoformat(),
            "open":vals[0]["opening_price"],
            "high":max(float(x["high_price"]) for x in vals),
            "low":min(float(x["low_price"]) for x in vals),
            "close":vals[-1]["trade_price"],
            "volume":sum(float(x["candle_acc_trade_volume"]) for x in vals)}

def judgement(label,start,hours,minutes):
    target=next((x for x in hours if start_kst(x)==start),None)
    if target is None:
        raise RuntimeError("MISSING_OFFICIAL_1H:"+start.isoformat())
    previous=sorted((x for x in hours if start_kst(x)<start),
                    key=start_kst)
    to_close=previous+[target]
    prev_closes=[float(x["trade_price"]) for x in previous]
    rsi_closed=rsi20([x["trade_price"] for x in to_close])
    rsi_previous=rsi20(prev_closes)
    rebuilt=hour_reconstruct(start,minutes)
    decision_time=start+dt.timedelta(hours=1,seconds=-12)
    check_time=start+dt.timedelta(hours=1,seconds=-15)
    # Upbit 1-minute bar starts at 22:59 or 23:59 KST, contains
    # any 1-second quote at the exact gate but has no per-second timeline.
    minute_label=decision_time.replace(second=0,microsecond=0)
    decision_bar=next((x for x in minutes if start_kst(x)==minute_label),None)
    if decision_bar:
        low=float(decision_bar["low_price"])
        high=float(decision_bar["high_price"])
        floor=rsi20(prev_closes,low)
        ceiling=rsi20(prev_closes,high)
        interval=[min(floor,ceiling),max(floor,ceiling)] if (
            floor is not None and ceiling is not None) else None
    else:
        low=high=None
        interval=None
    if interval is None:
        verdict="UNVERIFIED_NO_LAST_MINUTE_CANDLE"
        buy_feasible=None
    elif interval[0]>RSI_ARM:
        verdict="NO_BUY_RSI20_ABOVE_30_EVEN_AT_LAST_MINUTE_LOW"
        buy_feasible=False
    elif interval[1]<=RSI_ARM:
        verdict="RSI_ELIGIBLE_BUT_OTHER_GATES_AND_EXACT_12S_UNVERIFIED"
        buy_feasible=None
    else:
        verdict="INDETERMINATE_RSI20_CROSSES_30_INSIDE_LAST_MINUTE"
        buy_feasible=None
    return {"interpretation":label,
            "start_kst":start.isoformat(),
            "end_kst":(start+dt.timedelta(hours=1)).isoformat(),
            "rsi_live_check_kst":check_time.isoformat(),
            "rsi_buy_decision_kst":decision_time.isoformat(),
            "official_60m_open":target["opening_price"],
            "official_60m_close":target["trade_price"],
            "minute_reconstruction":rebuilt,
            "full_hour_minute_count_60":bool(rebuilt and rebuilt["minute_count"]==60),
            "official_close_equals_1m_close":(
                bool(rebuilt) and float(target["trade_price"])==float(rebuilt["close"])),
            "wilder_rsi20_prior_closed_hour":rsi_previous,
            "wilder_rsi20_at_full_hour_close":rsi_closed,
            "rsi20_main_le_30_at_hour_close":(
                rsi_closed<=RSI_ARM if rsi_closed is not None else None),
            "watch_pending_threshold_rsi20_le_35":35,
            "arm_threshold":RSI_ARM,
            "rsi20_last_minute_low_price":low,
            "rsi20_last_minute_high_price":high,
            "rsi20_possible_range_at_minus12s":interval,
            "exact_12s_trade_quote_from_1m_available":False,
            "latch_state_orderbook_and_market_score_available":False,
            "buy_possible_based_on_price_range":buy_feasible,
            "verdict":verdict}

def main():
    until=dt.datetime(2026,8,12,1,0,tzinfo=KST)
    hours=get_candles(60,until)
    # 200 minutes back from Aug 12 00:00 covers 22:00-23:00 and
    # 23:00-00:00 and preceding lookback to cross-check full OHLCV.
    minutes=get_candles(1,dt.datetime(2026,8,12,0,0,tzinfo=KST))
    evidence={"hours_60m":hours,"minutes_1m":minutes}
    blob=json.dumps(evidence,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    (OUTPUT/"upbit_official_raw_1m_60m.json").write_text(blob,encoding="utf8")
    stamps=[
        ("23:00 KST CLOSING EVENT (22:00-23:00)",
            dt.datetime(2026,8,11,22,0,tzinfo=KST)),
        ("23:00 KST CANDLE LABEL (23:00-00:00)",
            dt.datetime(2026,8,11,23,0,tzinfo=KST))]
    result={"market":MARKET,"date":"2026-08-11",
            "source":"UPBIT_OFFICIAL_PUBLIC_1m_PLUS_60m_REST",
            "raw_sha256":hashlib.sha256(blob.encode("utf8")).hexdigest(),
            "rsi_calculation":"Wilder RSI(20) main line, 20-smoothed; not RSI signal line",
            "notes":"1m OHLC bounds -12s price but cannot prove orderbook or exact tick",
            "checks":[judgement(label,at,hours,minutes) for label,at in stamps]}
    (OUTPUT/"near_20260811_rsi20_decision.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2),encoding="utf8")
    print("OFFICIAL_KRW_NEAR_AUG_11",json.dumps(result,ensure_ascii=False),
          flush=True)

if __name__=="__main__":
    main()
