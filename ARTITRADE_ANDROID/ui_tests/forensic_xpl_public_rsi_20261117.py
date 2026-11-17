"""Read-only Upbit KRW-XPL 2026-11-17 08:59:49 Wilder RSI(20) frame contrast."""
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

import market_trend_v043 as trend

KST=dt.timezone(dt.timedelta(hours=9))
WHEN=dt.datetime(2026,11,17,8,59,49,tzinfo=KST)
SYMBOL="KRW-XPL"
FRAMES={"5m":300,"30m":1800,"60m":3600,"day":86400}
out=Path("forensic-results")
out.mkdir(exist_ok=True)
result={"schema":"XPL_PUBLIC_RSI20_V1","symbol":SYMBOL,"as_of_kst":WHEN.isoformat(),
        "note":"Public historical candle/second approximation. NO phone ledger or original XPL order time provenance.",
        "rsi":{},"errors":[]}

def get(path,params):
    url="https://api.upbit.com"+path+"?"+urllib.parse.urlencode(params)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={
                "Accept":"application/json",
                "User-Agent":"ARTITRADE-Public-RSI-Frame-Audit/1.0"}),timeout=18) as r:
                return json.loads(r.read())
        except Exception as exc:
            if attempt==2:
                result["errors"].append({"path":path,"error":type(exc).__name__+":"+str(exc)})
                return []
            time.sleep(.5+attempt)
    return []

def start_of(row):
    return dt.datetime.fromisoformat(row["candle_date_time_kst"]).replace(tzinfo=KST)

markets=get("/v1/market/all",{"isDetails":"false"})
found=[x for x in markets if isinstance(x,dict) and "XPL" in x.get("market","")]
result["market_candidates"]=found
print("XPL_MARKETS",json.dumps(found,ensure_ascii=False))

seconds=get("/v1/candles/seconds",{"market":SYMBOL,"to":(WHEN+dt.timedelta(seconds=1)).isoformat(),"count":200})
latest=None
if isinstance(seconds,list):
    sec=[x for x in seconds if start_of(x)<=WHEN]
    if sec:
        selected=max(sec,key=start_of)
        latest={"time":start_of(selected).isoformat(),"price":float(selected["trade_price"])}
result["public_last_second"]=latest
print("XPL_PUBLIC_PRICE",json.dumps(latest))
for frame,width in FRAMES.items():
    endpoint=("/v1/candles/days" if frame=="day" else
              "/v1/candles/minutes/"+frame[:-1])
    bars=get(endpoint,{"market":SYMBOL,"to":(WHEN+dt.timedelta(seconds=1)).isoformat(),"count":200})
    if not isinstance(bars,list):bars=[]
    old=[]
    discarded=0
    for row in bars:
        try:
            st=start_of(row)
            end=st+dt.timedelta(seconds=width)
            if end>WHEN:
                discarded+=1
                continue
            old.append((st,float(row["trade_price"])))
        except (KeyError,ValueError,TypeError):pass
    old.sort()
    closes=[x[1] for x in old]
    previous=trend.rsi20(closes)
    simulated_forming=(trend.rsi20(closes,latest["price"]) if latest is not None
                       else None)
    info={"frame":frame,"seconds_per_bar":width,"completed_bar_count":len(closes),
          "last_completed_end":((old[-1][0]+dt.timedelta(seconds=width)).isoformat() if old else None),
          "prior_closed_rsi20":round(previous,6) if previous is not None else None,
          "forming_rsi20_using_public_last_second":round(simulated_forming,6) if simulated_forming is not None else None,
          "first_cross_possible_from_public_quote":bool(previous is not None
              and simulated_forming is not None and previous>30 and simulated_forming<=30),
          "discarded_in_progress_candles":discarded,
          "missing_source_candles_not_filled":True}
    result["rsi"][frame]=info
    print("XPL_RSI_FRAME",json.dumps(info,ensure_ascii=False))
    time.sleep(.12)
(out/"xpl_rsi20_20261117_085949.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
print("XPL_RSI_COMPLETE",json.dumps({"frames":list(result["rsi"]),"errors":result["errors"]},ensure_ascii=False))
