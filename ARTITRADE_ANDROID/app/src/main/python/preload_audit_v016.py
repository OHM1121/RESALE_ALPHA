"""0.0.16 - read-only initial data readiness audit.

No additional WS/REST login, order submission, SQLite mutation or fabricated
bars. Original preload_gate and trend prefetch workers continue to own all
recovery. The unit counts and reasons are for the UI ONLY.
"""
from __future__ import annotations
import datetime as dt
import math
from collections import Counter

SCHEMA="ARTITRADE_VERIFIED_PRELOAD_AUDIT_V016"
MIN_BARS=21
FRAME_SECONDS={"5m":300,"30m":1800,"60m":3600,"day":86400}

def _stamp(value):
    if isinstance(value,dt.datetime):
        return value if value.tzinfo else None
    try:
        v=dt.datetime.fromisoformat(str(value).replace("Z","+00:00"))
        return v if v.tzinfo else None
    except (TypeError,ValueError,OverflowError):
        return None

def verify_window(rows,market,frame,now):
    """Verify recent native *completed* source candles without any API call."""
    if frame not in FRAME_SECONDS:
        return {"status":"INVALID_FRAME","count":0,"gaps":0}
    unique={}
    for item in rows or ():
        if not isinstance(item,dict):continue
        stamp=_stamp(item.get("end"))
        if stamp is None or stamp>now:continue
        try:
            close=float(item.get("close"))
            high=float(item.get("high"))
            low=float(item.get("low"))
        except (TypeError,ValueError,OverflowError):continue
        if not all(map(math.isfinite,(close,high,low))) or not 0<low<=close<=high:
            continue
        source=str(item.get("source") or "")
        if source not in ("NATIVE_UPBIT_VERIFIED_COMPLETED",
                          "UPBIT_VERIFIED","LOCAL_KRX_VERIFIED",
                          "KIWOOM_VERIFIED","UPBIT_OFFICIAL_COMPLETED",
                          "UPBIT_CLOSED"):
            continue
        unique[stamp]=item
    stamps=sorted(unique)[-MIN_BARS:]
    if len(stamps)<MIN_BARS:
        return {"status":"MISSING_OFFICIAL_CANDLES",
                "count":len(stamps),"gaps":max(0,MIN_BARS-len(stamps))}
    sec=FRAME_SECONDS[frame]
    # An old but contiguous archive does not prove post-reconnect coverage.
    # Upbit completed daily candles close at 09:00 KST = 00:00 UTC.
    # No invented candles: a missing newest expected bar remains MISSING.
    if market=="coin":
        expected=dt.datetime.fromtimestamp(
            (int(now.timestamp())//sec)*sec,dt.timezone.utc)
        if stamps[-1]<expected-dt.timedelta(seconds=1):
            return {"status":"LATEST_OFFICIAL_CANDLE_MISSING",
                    "count":len(stamps),"gaps":1,
                    "last_closed":stamps[-1].isoformat(),
                    "expected_last_closed":expected.isoformat()}
    gaps=0
    for prior,cur in zip(stamps,stamps[1:]):
        delta=(cur-prior).total_seconds()
        if market=="coin":
            if abs(delta-sec)>1.1:gaps+=1
        elif frame!="day" and prior.date()==cur.date():
            # KRX official session gaps and partial last 60m candles may
            # be shorter than the regular interval; never invent bars.
            if delta>sec*1.5:gaps+=1
    return {"status":"VERIFIED_COMPLETE" if gaps==0 else "OFFICIAL_CANDLE_GAP",
            "count":len(stamps),"gaps":gaps,
            "last_closed":stamps[-1].isoformat()}

def _snapshot_gate(runner):
    try:
        value=runner.preload_gate.snapshot() if runner else {}
        return value if isinstance(value,dict) else {}
    except (AttributeError,TypeError,ValueError,RuntimeError):
        return {}

def _history(rt,now):
    trend=getattr(rt,"market_trend",None)
    r={market:{"required":0,"ready":0,"short":0,"gap":0,
               "newest_bars":0,"status":"NOT_STARTED"} for market in ("stock","coin")}
    if trend is None:return r
    try:
        from new_legacy_1118_policy import TREND_FRAMES
        with trend.lock:
            pairs=[]
            for market in ("stock","coin"):
                candidates=tuple((trend._candidates.get(market) or {}).keys())
                held=tuple((p.symbol,p.frame) for p in trend._positions.values()
                    if p.market==market and p.qty>0)
                pairs.extend((market,code,frame)
                    for code,frame in set(held) |
                        {(code,frame) for code in candidates for frame in TREND_FRAMES})
            captured={(m,code,frame):list(trend._history.get((m,code,frame),()))
                      for m,code,frame in pairs}
    except (ImportError,AttributeError,TypeError,ValueError,RuntimeError):
        for m in r:r[m]["status"]="HISTORY_OWNER_UNAVAILABLE"
        return r
    for market,code,frame in pairs:
        info=r[market];info["required"]+=1
        proof=verify_window(captured.get((market,code,frame)),market,frame,now)
        if proof["status"]=="VERIFIED_COMPLETE":info["ready"]+=1
        elif proof["status"]=="OFFICIAL_CANDLE_GAP":info["gap"]+=1
        else:info["short"]+=1
        info["newest_bars"]=max(info["newest_bars"],proof["count"])
    for m,info in r.items():
        info["status"]=("TOP50_HISTORY_SCAN_PENDING" if info["required"]==0
             else "VERIFIED_COMPLETE" if info["ready"]==info["required"]
             else "OFFICIAL_HISTORY_REPAIRING")
    return r

def audit(rt,baseline,now=None):
    """Attach actual evidence / missing counts, no synthetic completion."""
    now=now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:raise ValueError("timezone required")
    stage=(baseline or {}).get("startup_preload_v081") or {}
    stock=stage.get("stock") or {}
    coin=stage.get("coin") or {}
    score=baseline.get("market_feed_health_v004") or {}
    sh=score.get("stock") or {}
    ch=score.get("coin") or {}
    history=_history(rt,now)
    required=bool(stock.get("keys_present") and stock.get("session_required"))
    warm_stock=(stock.get("preload") or {}).get("ready") if required else True
    warm_coin=bool((coin.get("preload") or {}).get("ready"))
    coin_ws=bool(coin.get("ws_ready") and coin.get("ws_supervisor_running"))
    stock_ws=bool(stock.get("ws_ready") and stock.get("subscriptions",0)>0) if required else True
    quote_stock=not required or (stock_ws and sh.get("fresh_0b_count",0)>0)
    book_stock=not required or (stock_ws and sh.get("fresh_0d_count",0)>0)
    quote_coin=coin_ws and (ch.get("last_ws_quote_age_sec") is not None
          and ch.get("last_ws_quote_age_sec")<=10)
    requested=int(ch.get("requested_book_count") or 0)
    active=int(ch.get("active_book_count") or 0)
    book_coin=coin_ws and requested>0 and active>=requested
    checks=[
        ("ENGINE",bool(stage.get("engine_initialized"))),
        ("STOCK_NATIVE_GATE",bool(warm_stock)),
        ("COIN_NATIVE_GATE",bool(warm_coin)),
        ("STOCK_WS",bool(stock_ws)),
        ("COIN_WS",bool(coin_ws)),
        ("STOCK_VERIFIED_0B",bool(quote_stock)),
        ("STOCK_VERIFIED_0D",bool(book_stock)),
        ("COIN_VERIFIED_TICK",bool(quote_coin)),
        ("COIN_VERIFIED_BOOK",bool(book_coin))]
    for market in ("stock","coin"):
        data=history[market]
        # Unknown/ranking-pending does NOT falsely count as history complete.
        checks.extend((market.upper()+"_OFFICIAL_BAR_"+str(i),i<data["ready"])
                      for i in range(max(1,data["required"])))
    done=sum(bool(ok) for _,ok in checks)
    total=len(checks)
    failures=[name for name,ok in checks if not ok]
    percent=min(99,int(done*100/total)) if total else 0
    complete=(done==total)
    return {
        "schema":SCHEMA,"source":"ORIGINAL_NATIVE_READONLY",
        "work_units_done":done,"work_units_total":total,
        "percent":100 if complete else percent,"ready":complete,
        "missing":len(failures),"first_missing":failures[0] if failures else "",
        "failed_check_names":failures[:14],
        "official_history":history,
        "stock":{"ws":stock_ws,"trade_0b":quote_stock,"book_0d":book_stock,
                 "official_gate":bool(warm_stock)},
        "coin":{"ws":coin_ws,"ticker":bool(quote_coin),"book":bool(book_coin),
                "book_requested":requested,"book_active":active,
                "official_gate":bool(warm_coin)},
        "all_missing_recovery_owned_by_original_workers":True,
        "new_exchange_connection":False,"synthetic_fills":False,
    }

def install():
    # Deliberately no strategy engine monkey-patch or external access.
    return SCHEMA
