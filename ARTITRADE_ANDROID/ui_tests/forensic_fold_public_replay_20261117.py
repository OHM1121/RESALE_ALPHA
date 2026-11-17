"""Read-only replay of FOLD 2026-11-17 05:25 versus 08:55, from PUBLIC Upbit quotations.

Never uses account keys, broker adapters, ledger writes, synthetic order books or synthetic fills.
5-minute candles are FINAL historical facts; 1-second candles reconstruct available
trade-prices near the original close-minus-10..2-second execution gates.
Upbit's public APIs cannot prove whether this PHONE received/scheduled those packets.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json, time, traceback
import ma_v067_policy as policy

KST=timezone(timedelta(hours=9))
DATE="2026-11-17"
MARKET="KRW-FOLD"
root=Path("out")
root.mkdir(exist_ok=True)
api_errors=[]

def moment(t):
    return datetime.fromisoformat(DATE+"T"+t+"+09:00")

def get_api(path, params):
    url="https://api.upbit.com"+path+"?"+urlencode(params)
    for attempt in range(3):
        try:
            req=Request(url,headers={"Accept":"application/json","User-Agent":"ARTITRADE-ReadOnly-FOLD-Historical-Replay/1.0"})
            with urlopen(req,timeout=20) as response:
                return json.loads(response.read())
        except Exception as exc:
            err={"path":path,"params":params,"type":type(exc).__name__,"error":str(exc)}
            if attempt==2:
                api_errors.append(err)
                print("UPBIT_API_ERROR",json.dumps(err,ensure_ascii=False))
                return []
            time.sleep(1.0+attempt)
    return []

def from_kst(raw):
    return datetime.fromisoformat(raw).replace(tzinfo=KST)

def five_minute():
    end=moment("09:05:00")
    rows={}
    for page in range(3):
        data=get_api("/v1/candles/minutes/5",{"market":MARKET,"to":end.isoformat(),"count":200})
        if not isinstance(data,list) or not data:break
        for r in data:
            if not all(k in r for k in ("candle_date_time_kst","trade_price","high_price","low_price")):continue
            dt=from_kst(r["candle_date_time_kst"])
            rows[dt]={"time":dt,"close":Decimal(str(r["trade_price"])),
                     "open":Decimal(str(r["opening_price"])),
                     "high":Decimal(str(r["high_price"])),
                     "low":Decimal(str(r["low_price"]))}
        oldest=min(from_kst(x["candle_date_time_kst"]) for x in data)
        end=oldest
        if oldest<moment("16:00:00")-timedelta(days=1):break
        time.sleep(.2)
    return sorted(rows.values(),key=lambda x:x["time"])

def snap(prior,px):
    if len(prior)<121:return {"status":"SMA_HISTORY_REQUIRED","count":len(prior)}
    prev=policy.slopes(prior)
    curr=policy.slopes(prior+[px])
    if prev is None or curr is None:return {"status":"INVALID_MA_HISTORY"}
    short=policy.SHORT
    below=all(curr[n]["value"]<curr[120]["value"] for n in short)
    positive=all(curr[n]["delta"]>0 for n in short)
    root=policy.ma120_down_for_entry(curr)
    def original(m):
        return policy.ma120_down_for_entry(m) and all(m[n]["delta"]>0 for n in short)
    def user(m):
        return all(m[n]["delta"]>0 and m[n]["value"]<m[120]["value"] for n in short)
    def patched(m):
        return original(m) and user(m)
    first_original=original(curr) and not original(prev)
    first_user=user(curr) and not user(prev)
    first_patched=patched(curr) and not patched(prev)
    values={str(n):{"ma":str(curr[n]["value"].quantize(Decimal("0.000001"))),
                    "slope":str(curr[n]["delta"].quantize(Decimal("0.000001")))}
            for n in (*short,120)}
    return {"status":"OK","short4_below120":below,"short4_all_positive":positive,
            "ma120_down_policy":root,"old_first_signal":first_original,
            "user_first_signal":first_user,"patched_first_signal":first_patched,
            "prior_short4_already_positive_and_below":user(prev),
            "ma":values}

def prior_closes(bars,start):
    return [b["close"] for b in bars if b["time"]<start]

def replay_one_second(bars,candle_start):
    ending=candle_start+timedelta(minutes=5)
    raw=get_api("/v1/candles/seconds",{"market":MARKET,"to":ending.isoformat(),"count":200})
    if not isinstance(raw,list):raw=[]
    window=[]
    prior=prior_closes(bars,candle_start)
    for row in raw:
        try:
            stamp=from_kst(row["candle_date_time_kst"])
            remaining=(ending-stamp).total_seconds()
            if not (2<=remaining<=10 and stamp.date()==candle_start.date()):continue
            price=Decimal(str(row["trade_price"]))
            evaluated=snap(prior,price)
            actual=policy.upward_transition_on_forming(prior,price,remaining)
            window.append({"when":stamp.isoformat(),"seconds_left":remaining,
                  "trade_price":str(price),"old_first":evaluated.get("old_first_signal"),
                  "user_first":evaluated.get("user_first_signal"),
                  "patched_first":evaluated.get("patched_first_signal"),
                  "below120":evaluated.get("short4_below120"),
                  "policy_reason":actual.get("reason"),"ma":evaluated.get("ma")})
        except (ValueError,KeyError,TypeError) as exc:
            print("SECOND_ROW_ERROR",type(exc).__name__,str(exc))
    window.sort(key=lambda x:x["when"])
    return {"bar_start":candle_start.isoformat(),"bar_end":ending.isoformat(),
            "second_candles_returned":len(raw),"seconds_in_order_window":len(window),
            "replay":window}

def historical_trades(candle_start):
    # A 05:25 KST candle is UTC 20:25 the PREVIOUS day. This is the documented
    # public 7-day history, NOT the user's private phone order or trade ledger.
    ending=candle_start+timedelta(minutes=5)
    utc=ending.astimezone(timezone.utc)
    offset=(datetime.now(timezone.utc).date()-utc.date()).days
    if offset>7 or offset<0:return {"error":"TRADE_HISTORY_OUTSIDE_7_DAYS"}
    params={"market":MARKET,"to":utc.strftime("%H:%M:%S"),"count":500}
    if offset:params["days_ago"]=offset
    rows=get_api("/v1/trades/ticks",params)
    found=[]
    if isinstance(rows,list):
        for r in rows:
            try:
                stamp=datetime.fromtimestamp(int(r["timestamp"])/1000,timezone.utc).astimezone(KST)
                if candle_start+timedelta(minutes=4,seconds=50)<=stamp<ending-timedelta(seconds=1):
                    found.append({"at":stamp.isoformat(timespec="milliseconds"),
                        "price":str(r["trade_price"]),"side":r.get("ask_bid")})
            except (ValueError,TypeError,KeyError):continue
    return {"returned":len(rows) if isinstance(rows,list) else 0,
            "trades_close_minus_10_to_minus_2":sorted(found,key=lambda x:x["at"]),
            "count":len(found),"source":"Upbit PUBLIC exchange trade ticks"}

def main():
    markets=get_api("/v1/market/all",{"isDetails":"false"})
    fold_matches=[{"market":x.get("market"),"name":x.get("korean_name")}
                  for x in markets if isinstance(x,dict) and
                  ("FOLD" in str(x.get("market","")) or "폴드" in str(x.get("korean_name","")))]
    print("FOLD_MARKETS",json.dumps(fold_matches,ensure_ascii=False))
    bars=five_minute()
    if len(bars)<122:
        report={"error":"INSUFFICIENT_UPBIT_5MIN_CANDLES","market":MARKET,
            "candle_count":len(bars),"first":str(bars[0]["time"]) if bars else None,
            "api_errors":api_errors,"market_matches":fold_matches}
        print("FOLD_REPLAY_SUMMARY",json.dumps(report,ensure_ascii=False))
        (root/"fold_replay_20261117.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
        return
    starts={b["time"]:b for b in bars}
    missing=[]
    for i in range(1,len(bars)):
        diff=(bars[i]["time"]-bars[i-1]["time"]).total_seconds()
        if diff!=300:missing.append({"from":bars[i-1]["time"].isoformat(),"to":bars[i]["time"].isoformat(),"gap_sec":diff})
    print("CANDLES",json.dumps({"count":len(bars),"first":bars[0]["time"].isoformat(),
        "last":bars[-1]["time"].isoformat(),"missing_intervals":missing[:30]},ensure_ascii=False))
    trace=[]
    for bar in bars:
        start=bar["time"]
        if not(moment("04:00:00")<=start<=moment("09:00:00")):continue
        prior=prior_closes(bars,start)
        result=snap(prior,bar["close"])
        trace.append({"bar":start.isoformat(),"close":str(bar["close"]),
                     "high":str(bar["high"]),"low":str(bar["low"]),**result})
    marked={moment(x) for x in ("05:15:00","05:20:00","05:25:00","05:30:00",
                                  "05:35:00","08:45:00","08:50:00","08:55:00")}
    chosen=[x for x in trace if
           from_kst(x["bar"][:19]).date()==moment("05:25:00").date() and
           (from_kst(x["bar"][:19]) in marked or x.get("old_first_signal")
            or x.get("user_first_signal") or x.get("patched_first_signal"))]
    for r in chosen:
        print("FOLD_FIVE_MIN_MA",json.dumps(r,ensure_ascii=False))
    focus=[moment(x) for x in ("05:20:00","05:25:00","05:30:00","08:50:00","08:55:00")]
    seconds=[]
    trades=[]
    for st in focus:
        sec=replay_one_second(bars,st)
        seconds.append(sec)
        print("FOLD_SECOND_REPLAY",json.dumps(sec,ensure_ascii=False))
        trade=historical_trades(st)
        trades.append({"bar_start":st.isoformat(),**trade})
        print("FOLD_PUBLIC_TRADES",json.dumps(trades[-1],ensure_ascii=False))
        time.sleep(.2)
    report={"schema":"ARTITRADE_FOLD_PUBLIC_HISTORICAL_FORENSICS_V1",
        "symbol":MARKET,"date_kst":DATE,
        "method":"exact historical 5m MA and public 1-second/ticks price-only replay; NO native phone packet/order reconstruction",
        "all_ma_snapshots_0400_to_0900":trace,"focus_ma_snapshots":chosen,
        "focus_second_by_candle":seconds,"focus_public_trades":trades,
        "missing_five_minute_candles":missing,
        "availability":{"market_matches":fold_matches,"candle_count":len(bars)},
        "api_errors":api_errors,
        "limitations":["historical 5m close is only known after the bar completes",
                       "1-second candle price is not original WebSocket receive time",
                       "historical live orderbook/ask volume not recoverable from Upbit candle API",
                       "phone session startup, cache warmup, signal candidate rejection and ledger not supplied",
                       "NEVER assume historical candle cross means a phone order/fill"]}
    (root/"fold_replay_20261117.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf8")
    print("FOLD_REPLAY_COUNTS",json.dumps({"candle_count":len(bars),"snapshot_count":len(trace),
          "first_user":[x["bar"] for x in trace if x.get("user_first_signal")],
          "first_original":[x["bar"] for x in trace if x.get("old_first_signal")],
          "first_patched":[x["bar"] for x in trace if x.get("patched_first_signal")],
          "sec_windows":[{"bar":x["bar_start"],"n":x["seconds_in_order_window"],
                          "user_first":[t["when"] for t in x["replay"] if t.get("user_first")],
                          "original_first":[t["when"] for t in x["replay"] if t.get("old_first")]}
                         for x in seconds],
          "api_error_count":len(api_errors)},ensure_ascii=False))

if __name__=="__main__":
    try: main()
    except Exception as exc:
        print("FOLD_REPLAY_FATAL",type(exc).__name__,str(exc))
        traceback.print_exc()
        raise
