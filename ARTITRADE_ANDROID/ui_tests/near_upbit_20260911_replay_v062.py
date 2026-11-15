"""Official Upbit historical 1m + 60m RSI20 reconciliation (read only).

Reconstructs KRW-NEAR exact 1m OHLC for both possible meanings of
'2026-09-11 23:00 1-hour close'; never predicts -12sec quote from 1m data.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import quote

import requests
from market_trend_v043 import rsi20, SignalLatch, RSI_ARM

KST=dt.timezone(dt.timedelta(hours=9))
MARKET="KRW-NEAR"
BASE="https://api.upbit.com"
OUTPUT=Path("near_20260911_evidence_v062")
OUTPUT.mkdir(exist_ok=True)
S=requests.Session()
S.headers["User-Agent"]="ARTITRADE-historical-readonly-audit/0.7.62"
S.headers["Accept"]="application/json"

def fetch(unit,until,n=200):
    target=until.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    url=f"{BASE}/v1/candles/minutes/{unit}"
    payload=None
    for attempt in range(5):
        resp=S.get(url,params=dict(market=MARKET,to=target,count=n),timeout=12)
        if resp.status_code==200:
            payload=resp.json()
            break
        print("UPBIT_ERROR",unit,resp.status_code,resp.text[:200],flush=True)
        time.sleep(.45*(attempt+1))
    if not isinstance(payload,list):raise RuntimeError(f"NO_UPBIT_CANDLES:{unit}:{target}")
    return payload

def kst(row):
    return dt.datetime.fromisoformat(row["candle_date_time_kst"]).replace(tzinfo=KST)

def aggregate(start,by_minute):
    rows=[x for x in by_minute if start<=kst(x)<start+dt.timedelta(hours=1)]
    rows.sort(key=kst)
    if not rows:return None
    return {"start":start.isoformat(),"one_minute_count":len(rows),
            "first":kst(rows[0]).isoformat(),"last":kst(rows[-1]).isoformat(),
            "open":rows[0]["opening_price"],
            "high":max(x["high_price"] for x in rows),
            "low":min(x["low_price"] for x in rows),
            "close":rows[-1]["trade_price"],
            "volume":sum(x["candle_acc_trade_volume"] for x in rows)}

def assess(label,at,all_hours,all_minutes):
    hourly=next((x for x in all_hours if kst(x)==at),None)
    if not hourly:raise RuntimeError("UPBIT_60M_CANDLE_MISSING:"+at.isoformat())
    older=[x for x in all_hours if kst(x)<=at]
    older.sort(key=kst)
    val=rsi20([x["trade_price"] for x in older])
    prior=older[:-1]
    prior_rsi=rsi20([x["trade_price"] for x in prior])
    aggregated=aggregate(at,all_minutes)
    reconciled=(aggregated is not None and
                abs(float(aggregated["close"])-float(hourly["trade_price"]))<1e-9)
    # 1-minute OHLC does NOT prove the exact 12/15 seconds before hour-close.
    # Other gates: WATCH after <=35, latch, fresh orderbook, LIVE lease,
    # sufficient balance, broker FOK and market score.
    return {"label":label,"candle_start_kst":at.isoformat(),
        "candle_end_kst":(at+dt.timedelta(hours=1)).isoformat(),
        "upbit_official_60m_close":hourly["trade_price"],
        "reconstructed_1m_hour":aggregated,
        "upbit_close_matches_1m":reconciled,
        "rsi20_wilder_main_at_close":val,
        "rsi20_wilder_previous_hour_close":prior_rsi,
        "rsi20_main_at_close_le30":val is not None and val<=RSI_ARM,
        "rsi20_9_signal_line_not_used":True,
        "can_reconstruct_actual_minus12sec_quote":False,
        "actual_buy_decision":"NO_CLOSING_RSI_GATE" if val is not None and val>RSI_ARM
            else "NOT_DETERMINABLE_WITHOUT_12S_QUOTE_LATCH_BOOK_ACCOUNT",
        "market_score_available_from_ohlcv":False}

def main():
    t=dt.datetime(2026,9,12,1,0,tzinfo=KST)
    hourly=fetch(60,t,200)
    mins=fetch(1,dt.datetime(2026,9,12,0,0,tzinfo=KST),200)
    raw={"hourly":hourly,"minute":mins}
    rawstr=json.dumps(raw,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    (OUTPUT/"official_candles.json").write_text(rawstr,encoding="utf-8")
    inputs_sha=hashlib.sha256(rawstr.encode()).hexdigest()
    stamps=[("23:00 KST-labelled hour",dt.datetime(2026,9,11,23,0,tzinfo=KST)),
            ("hour whose close OCCURRED at 23:00 KST",
                dt.datetime(2026,9,11,22,0,tzinfo=KST))]
    report={"market":MARKET,
      "source":"UPBIT_PUBLIC_REST_OFFICIAL_MINUTES_1_AND_60",
      "raw_sha256":inputs_sha,
      "timestamp_convention":"Candle labels are hour START; the 23:00 candle closes at 00:00 on Sep 12.",
      "checks":[assess(label,stamp,hourly,mins) for label,stamp in stamps]}
    (OUTPUT/"decision.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("UPBIT_KRW_NEAR_REPLAY",json.dumps(report,ensure_ascii=False),flush=True)

if __name__=="__main__":
    main()
