"""ARTITRADE market research v033, strictly independent of all order engines.

Stock scoring follows EXE d_market_briefing.py _evidence impact/weight rules,
displayed unchanged in [-100,100]. Crypto is an experimental public-
data-only rubric with the same signed scale. No BUY/SELL or broker operation is ever performed here.
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
import threading
import time
from pathlib import Path
from urllib.parse import quote

BANDS=(
    "-100~-81", "-80~-61", "-60~-41", "-40~-21", "-20~-1",
    "0~19", "20~39", "40~59", "60~79", "80~100",
)
_STOCK_FACTORS={
    "KOSPI":(1.5,False,1.30),"KOSDAQ":(2.0,False,0.90),
    "KOSPI5":(4.0,False,0.65),"S&P500":(1.5,False,0.55),
    "NASDAQ":(1.8,False,0.85),"SOX":(2.5,False,1.00),
    "VIX":(8.0,True,0.70),"USD/KRW":(1.2,True,0.75),
    "US10Y":(2.0,True,0.35),"WTI":(3.5,True,0.20),
}
_PUBLIC={
    "KOSPI":"^KS11","KOSDAQ":"^KQ11","S&P500":"^GSPC",
    "NASDAQ":"^IXIC","SOX":"^SOX","VIX":"^VIX",
    "USD/KRW":"KRW=X","US10Y":"^TNX","WTI":"CL=F",
}
TOTAL_STOCK_WEIGHT=sum(p[2] for p in _STOCK_FACTORS.values())+1.10+0.55


def finite(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError,TypeError):return None


def clamp(v,low=0.0,high=100.0):
    return max(low,min(high,v))


def market_band(score):
    """Twenty-point intervals on a continuous signed [-100,+100] scale.

    E.g. -80.0 begins the -80~-61 bin; -80.1 belongs to -100~-81.
    +100 is included in the final bin rather than being dropped.
    """
    v=finite(score)
    if v is None or v < -100 or v > 100:
        return None
    index=min(len(BANDS)-1, int(math.floor((v+100)/20)))
    return BANDS[index]


def _stock_driver(name,value,scale,invert,weight):
    v=finite(value)
    if v is None:return None
    impact=clamp(v/scale,-1,1)*100.0
    if invert:impact=-impact
    return {"name":name,"weight":weight,"impact":round(impact,2),
            "value":round(v,4)}


def stock_score(changes,flows):
    """Preserve EXE's signed weighted drivers, do not fill missing data with 50."""
    changes=changes or {};flows=flows or {}
    drivers=[]
    for name,(scale,invert,w) in _STOCK_FACTORS.items():
        component=_stock_driver(name,changes.get(name),scale,invert,w)
        if component:drivers.append(component)
    for label,key,weight,amplitude in (
            ("외국인 수급","foreign",1.10,60),
            ("기관 수급","institution",0.55,40)):
        sign=finite(flows.get(key))
        if sign is not None:
            drivers.append({"name":label,"weight":weight,
                "impact":(amplitude if sign>0 else -amplitude if sign<0 else 0.0),
                "value":sign})
    used=sum(x["weight"] for x in drivers)
    coverage=round(used/TOTAL_STOCK_WEIGHT,3)
    if finite(changes.get("KOSPI")) is None or finite(changes.get("KOSDAQ")) is None:
        return {"score":None,"raw_score":None,"coverage":coverage,
                "factors":drivers,"status":"KOSPI/KOSDAQ 미수신"}
    raw=sum(x["impact"]*x["weight"] for x in drivers)/used
    return {"score":round(clamp(raw,-100,100),1),
            "raw_score":round(raw,1),"coverage":coverage,"factors":drivers,
            "status":"정상" if coverage>.90 else "일부 지표 미수신"}


def _closes(candles):
    values=[]
    for candle in reversed(candles or []):
        if not isinstance(candle,dict):continue
        price=finite(candle.get("trade_price"))
        if price is not None and price>0:values.append(price)
    return values


def _change(now,then):
    return (now/then-1.0)*100 if now is not None and then not in (None,0) else None



def _rsi14_closed(closes,period=14):
    """Wilder RSI from fully closed daily candles (exclude today's live bar)."""
    if len(closes)<period+1:
        return None
    changes=[closes[i]-closes[i-1] for i in range(1,len(closes))]
    ups=[max(0.0,x) for x in changes[:period]]
    downs=[max(0.0,-x) for x in changes[:period]]
    gain=sum(ups)/period
    loss=sum(downs)/period
    for x in changes[period:]:
        gain=(gain*(period-1)+max(0.0,x))/period
        loss=(loss*(period-1)+max(0.0,-x))/period
    if gain==0 and loss==0:
        return 50.0
    if loss==0:
        return 100.0
    return 100.0-100.0/(1.0+gain/loss)


def coin_score(tickers,btc_candles,eth_candles):
    """Experimental breadth+volume+trend model, not an official Fear/Greed index."""
    ticks=[t for t in (tickers or []) if isinstance(t,dict) and
           str(t.get("market") or "").startswith("KRW-") and
           str(t.get("market") or "") not in ("KRW-USDT","KRW-USDC") and
           finite(t.get("signed_change_rate")) is not None and
           (finite(t.get("acc_trade_price_24h")) or 0)>0]
    btc=_closes(btc_candles);eth=_closes(eth_candles)
    if len(ticks)<10 or len(btc)<31 or len(eth)<8:
        return {"score":None,"coverage":0.0,"factors":[],
            "status":"BTC/ETH 일봉 또는 KRW 마켓 표본 부족"}
    # Omit current unfinished daily bar in volume comparisons; trend already
    # uses the latest observed price and is explicitly an observation.
    btc7=_change(btc[-1],btc[-8]);eth7=_change(eth[-1],eth[-8])
    btc_ma7=statistics.mean(btc[-7:])
    btc_ma30=statistics.mean(btc[-30:])
    positive=sum(1 for t in ticks if float(t["signed_change_rate"])>0)
    turnover=sum(float(t["acc_trade_price_24h"]) for t in ticks)
    rising_turnover=sum(float(t["acc_trade_price_24h"]) for t in ticks
        if float(t["signed_change_rate"])>0)
    daily_returns=[_change(btc[i],btc[i-1]) for i in range(1,len(btc))]
    vol=statistics.pstdev(daily_returns[-20:])
    close_to_ma=clamp(50+((btc[-1]/btc_ma30-1)*100)*6)
    momentum=clamp(50+(btc7 or 0)*6)
    trend=(close_to_ma+momentum)/2
    breadth=100*positive/len(ticks)
    cashflow=100*rising_turnover/turnover if turnover else 50
    relative=clamp(50+((eth7 or 0)-(btc7 or 0))*5)
    # A 2%-10% daily return volatility range is a heuristic (not calibrated).
    vol_health=clamp((10.0-vol)*12.5)
    # Today's Upbit daily candle is not yet finalized: only closed candles
    # may establish an RSI extreme. 30=oversold (+100), 70=overbought (-100).
    rsi14=_rsi14_closed(btc[:-1])
    if rsi14 is None:
        return {"score":None,"coverage":0.0,"factors":[],
                "status":"BTC 확정 일봉 RSI 표본 부족"}
    rsi_health=clamp((70.0-rsi14)*2.5)

    factors=[
        {"name":"BTC 추세","value":round(trend,1),"weight":0.15},
        {"name":"KRW 상승종목 비율","value":round(breadth,1),"weight":0.25},
        {"name":"상승종목 거래대금 비율","value":round(cashflow,1),"weight":0.20},
        {"name":"ETH/BTC 7일 상대강도","value":round(relative,1),"weight":0.15},
        {"name":"BTC 20일 변동성 건강도","value":round(vol_health,1),"weight":0.10},
        {"name":"BTC RSI(14) 과매도/과매수","value":round(rsi_health,1),"weight":0.15,
         "rsi14":round(rsi14,2)},
    ]
    # Each component is reported on the same bearish/neutral/bullish scale.
    # Raw value is retained independently as the 0~100 component observation.
    for item in factors:
        item["impact"]=round(clamp(2*item["value"]-100,-100,100),1)
    total=sum(x["impact"]*x["weight"] for x in factors)
    return {"score":round(clamp(total,-100,100),1),"coverage":1.0,"factors":factors,
        "status":"관측 완료 · 실험적 점수",
        "sample_count":len(ticks),"btc_7d_pct":round(btc7,2),
        "btc_vol20_pct":round(vol,2), "btc_rsi14":round(rsi14,2)}


def holding_returns(records):
    """Only positive cost and actual marked holdings, in percentage points."""
    out={}
    for identity,row in (records or {}).items():
        if not isinstance(row,dict):continue
        cost=finite(row.get("entry_price"))
        last=finite(row.get("last_price"))
        qty=finite(row.get("qty"))
        if cost is None or cost<=0 or last is None or last<=0 or qty is None or qty<=0:
            continue
        out[str(identity)]=round((last/cost-1)*100,5)
    return out


class ScoreLedger:
    """Append-only score journal and per-band frozen holding cohort in SQLite."""
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.conn=sqlite3.connect(str(self.path),check_same_thread=False,
                                  timeout=3,isolation_level=None)
        self.conn.row_factory=sqlite3.Row
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=3000")
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS score_events(
                    market TEXT NOT NULL, ts INTEGER NOT NULL, score REAL NOT NULL,
                    band TEXT NOT NULL, meta TEXT NOT NULL,
                    PRIMARY KEY(market,ts));
                CREATE TABLE IF NOT EXISTS cohort_segments(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT NOT NULL,
                    band TEXT NOT NULL, start_ts INTEGER NOT NULL,end_ts INTEGER);
                CREATE INDEX IF NOT EXISTS idx_cohort_active
                    ON cohort_segments(market,end_ts);
                CREATE TABLE IF NOT EXISTS cohort_positions(
                    segment_id INTEGER NOT NULL,identity TEXT NOT NULL,
                    start_return REAL NOT NULL,latest_return REAL NOT NULL,
                    last_ts INTEGER NOT NULL,
                    PRIMARY KEY(segment_id,identity));
                CREATE INDEX IF NOT EXISTS idx_scores_time
                    ON score_events(market,ts);
            """)
            self._upgrade_signed_scale_if_needed()
            self._upgrade_20point_bands_if_needed()

    def _upgrade_signed_scale_if_needed(self):
        """One-time, transactional migration of v033 0..100 score observations.

        Preserve timestamps, linked holding baselines, return deltas and identity.
        Original stock raw_score stored in meta is authoritative when present.
        """
        version=int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version>=34:
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            rows=self.conn.execute(
                "SELECT market,ts,score,meta FROM score_events"
            ).fetchall()
            for row in rows:
                try:
                    meta=json.loads(row["meta"] or "{}")
                    if not isinstance(meta,dict):meta={}
                except (ValueError,TypeError):
                    meta={}
                legacy=finite(row["score"])
                if legacy is None:
                    continue
                if row["market"]=="stock":
                    signed=finite(meta.get("raw_score"))
                    if signed is None:signed=2*legacy-100
                else:
                    signed=2*legacy-100
                signed=round(clamp(signed,-100,100),1)
                meta["score_scale"]="-100..+100"
                meta["migrated_from_v033"]=True
                self.conn.execute(
                    "UPDATE score_events SET score=?,band=?,meta=? WHERE market=? AND ts=?",
                    (signed,market_band(signed),json.dumps(meta,ensure_ascii=False),
                     row["market"],row["ts"]))
            segments=self.conn.execute(
                "SELECT id,market,start_ts FROM cohort_segments"
            ).fetchall()
            for segment in segments:
                event=self.conn.execute(
                    "SELECT band FROM score_events WHERE market=? AND ts=?",
                    (segment["market"],segment["start_ts"])).fetchone()
                if event:
                    self.conn.execute(
                        "UPDATE cohort_segments SET band=? WHERE id=?",
                        (event["band"],segment["id"]))
            self.conn.execute("PRAGMA user_version=34")
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def _upgrade_20point_bands_if_needed(self):
        """Remap old bucket labels in place, never overwrite score or P&L."""
        version=int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        if version>=35:
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for row in self.conn.execute(
                    "SELECT market,ts,score,band FROM score_events").fetchall():
                label=market_band(row["score"])
                if label is not None and label!=row["band"]:
                    self.conn.execute(
                        "UPDATE score_events SET band=? WHERE market=? AND ts=?",
                        (label,row["market"],row["ts"]))
            # Old active/closed cohorts retain original positions and return
            # baselines; only their display band string changes.
            segments=self.conn.execute(
                "SELECT id,market,start_ts,band FROM cohort_segments").fetchall()
            for group in segments:
                matched=self.conn.execute(
                    "SELECT band FROM score_events WHERE market=? AND ts<=? "
                    "ORDER BY ts DESC LIMIT 1",
                    (group["market"],group["start_ts"])).fetchone()
                if matched and matched["band"]!=group["band"]:
                    self.conn.execute("UPDATE cohort_segments SET band=? WHERE id=?",
                                      (matched["band"],group["id"]))
            self.conn.execute("PRAGMA user_version=35")
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def close(self):
        with self.lock:self.conn.close()

    def _active(self,market):
        return self.conn.execute("SELECT id,band,start_ts FROM cohort_segments "
            "WHERE market=? AND end_ts IS NULL ORDER BY id DESC LIMIT 1",(market,)).fetchone()

    def _update(self,segment,positions,ts):
        if segment is None:return
        for identity,v in (positions or {}).items():
            n=finite(v)
            if n is None:continue
            self.conn.execute("UPDATE cohort_positions SET latest_return=?,last_ts=? "
                "WHERE segment_id=? AND identity=? AND last_ts<=?",
                (n,int(ts),segment["id"],str(identity),int(ts)))

    def _start(self,market,band,positions,ts):
        cur=self.conn.execute("INSERT INTO cohort_segments(market,band,start_ts)"
            " VALUES(?,?,?)",(market,band,int(ts)))
        sid=int(cur.lastrowid)
        for key,val in (positions or {}).items():
            n=finite(val)
            if n is not None:
                self.conn.execute("INSERT INTO cohort_positions VALUES(?,?,?,?,?)",
                    (sid,str(key),n,n,int(ts)))

    def record_score(self,market,score,ts,positions,meta=None):
        band=market_band(score)
        if market not in ("stock","coin") or band is None:
            return False
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                active=self._active(market)
                if active and int(ts)<active["start_ts"]:
                    self.conn.execute("ROLLBACK")
                    return False
                if active and active["band"]!=band:
                    self._update(active,positions,ts)
                    self.conn.execute("UPDATE cohort_segments SET end_ts=? WHERE id=?",
                        (int(ts),active["id"]))
                    self._start(market,band,positions,ts)
                elif not active:
                    self._start(market,band,positions,ts)
                else:
                    self._update(active,positions,ts)
                self.conn.execute("INSERT OR REPLACE INTO score_events VALUES(?,?,?,?,?)",
                    (market,int(ts),float(score),band,
                    json.dumps(meta or {},ensure_ascii=False)))
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return True

    def observe_positions(self,market,positions,ts):
        if market not in ("stock","coin"):return
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                active=self._active(market)
                if active and int(ts)>=active["start_ts"]:
                    # First valid holding snapshot after score initialized:
                    # no retrospective return is invented for missing initial data.
                    present=int(self.conn.execute(
                        "SELECT COUNT(*) FROM cohort_positions WHERE segment_id=?",
                        (active["id"],)).fetchone()[0])
                    if present==0 and int(ts)-active["start_ts"]<=600:
                        for key,val in (positions or {}).items():
                            n=finite(val)
                            if n is not None:
                                self.conn.execute(
                                    "INSERT OR IGNORE INTO cohort_positions VALUES(?,?,?,?,?)",
                                    (active["id"],str(key),n,n,int(ts)))
                    self._update(active,positions,ts)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise

    def score_at(self,market,ts,max_age_seconds=900):
        with self.lock:
            row=self.conn.execute("SELECT score,ts FROM score_events "
                "WHERE market=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                (market,int(ts))).fetchone()
        if row is None or int(ts)-row["ts"]>max_age_seconds:return None
        return row["score"]

    def snapshot(self,market):
        with self.lock:
            active=self._active(market)
            last=self.conn.execute("SELECT score,ts,meta FROM score_events "
                "WHERE market=? ORDER BY ts DESC LIMIT 1",(market,)).fetchone()
            groups=self.conn.execute(
                "SELECT band,COUNT(*) AS n FROM score_events "
                "WHERE market=? GROUP BY band",(market,)).fetchall()
            active_data=self.conn.execute("""
                SELECT COUNT(*) AS n,AVG(latest_return-start_return) AS avg_pp
                FROM cohort_positions WHERE segment_id=?
            """,(active["id"] if active else -1,)).fetchone()
            rows=self.conn.execute(
                "SELECT ts,score,band FROM score_events WHERE market=? ORDER BY ts DESC LIMIT 720",
                (market,)).fetchall()
        metadata=json.loads(last["meta"]) if last else {}
        return {"market":market,"score":last["score"] if last else None,
            "asof":last["ts"] if last else None,
            "active_band":active["band"] if active else None,
            "active_since":active["start_ts"] if active else None,
            "active_count":active_data["n"] if active else 0,
            "active_avg_change_pp":active_data["avg_pp"] if active else None,
            "bands":[{"band":row["band"],"count":row["n"]}
                     for row in groups],
            "recent_scores":[dict(row) for row in reversed(rows)],
            "coverage":metadata.get("coverage"),
            "factors":metadata.get("factors",[]),
            "status":metadata.get("status","시장점수 수집 대기"),
            "sample_count":metadata.get("sample_count"),
            "raw_score":metadata.get("raw_score"),
            "source":metadata.get("source"),
        }


def display_snapshot(market,snapshot,now=None,poll_error=None):
    """A historical last score is not necessarily a live quote."""
    output=dict(snapshot or {})
    stamp=finite(output.get("asof"))
    now=time.time() if now is None else now
    limit=3600 if market=="stock" else 900
    stale=stamp is not None and now-stamp>limit
    output["stale"]=bool(stale)
    if stale:
        output["status"]=str(output.get("status") or "시장점수") + " · 마지막 관측 오래됨"
    if poll_error:
        output["status"]=str(poll_error) + (" · 마지막 점수 표시" if stamp else "")
    return output


def _extract_positions(records):
    result={"stock":{},"coin":{}}
    if not isinstance(records,dict):return result
    layout={
        "stock":[("at",(("stock","at","ledger","positions"))),
                 ("ma",(("stock","at","ledger","ma","positions"))),
                 ("close",(("stock","cw","ledger","positions"))),
                 ("gmo",(("stock","at","ledger","gmo","positions")))],
        "coin":[("general",(("coin","general","ledger","positions"))),
                ("ma",(("coin","ma","ledger","positions")))]
    }
    for market,paths in layout.items():
        for strategy,keys in paths:
            node=records
            for key in keys:
                if not isinstance(node,dict):break
                node=node.get(key)
            if not isinstance(node,list):continue
            for item in node:
                if not isinstance(item,dict):continue
                code=str(item.get("code") or item.get("market") or "").strip().upper()
                if not code:continue
                tf=str(item.get("tf") or ("gmo" if strategy=="gmo" else "all"))
                pos_id=item.get("id")
                if pos_id is None:pos_id=code
                identity="/".join((market,strategy,tf,str(pos_id)))
                cost=item.get("entry_price",item.get("entry",item.get("avg_buy_price")))
                price=item.get("last_price",item.get("mark",item.get("current_price")))
                qty=item.get("qty",item.get("quantity",item.get("holding_qty")))
                result[market][identity]={"entry_price":cost,"last_price":price,
                    "qty":qty,"code":code,"tf":tf,"strategy":strategy}
    return result


def _public_json(url,params):
    import requests
    response=requests.get(url,params=params,timeout=5,
        headers={"User-Agent":"Mozilla/5.0 ARTITRADE market research read-only"})
    response.raise_for_status()
    return response.json()


def _domestic_index_fallback(name):
    """Independent Korean index source if Yahoo daily chart is unreachable."""
    code={"KOSPI":"KOSPI","KOSDAQ":"KOSDAQ"}.get(name)
    if not code:
        return None
    try:
        obj=_public_json("https://m.stock.naver.com/api/index/"+code+"/basic",{})
        if isinstance(obj,dict):
            raw=obj.get("fluctuationsRatio")
            if raw is None and isinstance(obj.get("result"),dict):
                raw=obj["result"].get("fluctuationsRatio")
            if raw is not None:
                return finite(str(raw).replace(",","").replace("%","")
                              .replace("−","-"))
    except Exception:
        pass
    return None


def _fetch_stock():
    changes={};history={};failed=[];naver_used=[]
    for name,symbol in _PUBLIC.items():
        prices=None
        for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
            try:
                result=_public_json(
                    "https://"+host+"/v8/finance/chart/"+quote(symbol,safe=""),
                    {"interval":"1d","range":"2mo"})
                data=result["chart"]["result"][0]
                closes=[finite(v) for v in (
                    data["indicators"]["quote"][0].get("close") or [])]
                prices=[v for v in closes if v is not None and v>0]
                if len(prices)>=2:
                    break
            except Exception:
                prices=None
        if not prices or len(prices)<2:
            # Fallback is only for the two mandatory domestic indices.
            # Never manufacture index quotes from unrelated symbols.
            backup=_domestic_index_fallback(name)
            if backup is not None:
                changes[name]=round(backup,4)
                naver_used.append(name)
                continue
            failed.append(name)
            continue
        changes[name]=round((prices[-1]/prices[-2]-1)*100,4)
        history[name]=prices
    kospi=history.get("KOSPI") or []
    if len(kospi)>=6:
        changes["KOSPI5"]=round((kospi[-1]/kospi[-6]-1)*100,4)
    scored=stock_score(changes,{})
    scored["source"]="Yahoo Finance public chart latest available daily closes"
    scored["received"]=sorted(changes)
    scored["naver_fallback"]=naver_used
    scored["missing"]=failed
    if scored["score"] is None:
        scored["status"]="코스피·코스닥 지표 미수신 · 다음 조회 재시도"
    return scored


def _upbit_tickers():
    """Use Upbit KRW all-quote endpoint, fallback to documented per-pair ticker."""
    err=""
    try:
        rows=_public_json("https://api.upbit.com/v1/ticker/all",
                          {"quote_currencies":"KRW"})
        if isinstance(rows,list) and len(rows)>=10:
            return rows
        err="시장 전체 시세 표본 부족"
    except Exception as exc:
        err=type(exc).__name__
    markets=_public_json("https://api.upbit.com/v1/market/all",
                         {"isDetails":"false"})
    codes=[str(x.get("market") or "") for x in markets if isinstance(x,dict)
           and str(x.get("market") or "").startswith("KRW-")
           and str(x.get("market") or "") not in ("KRW-USDT","KRW-USDC")]
    if len(codes)<10:
        raise ValueError("KRW 거래종목 목록 미수신 ("+err+")")
    result=[]
    for i in range(0,len(codes),80):
        one=_public_json("https://api.upbit.com/v1/ticker",
                         {"markets":",".join(codes[i:i+80])})
        if not isinstance(one,list):
            raise ValueError("KRW 종목별 현재가 형식 오류")
        result.extend(one)
    if len(result)<10:
        raise ValueError("KRW 현재가 표본 부족 ("+err+")")
    return result


def _fetch_coin():
    try:
        tickers=_upbit_tickers()
        btc=_public_json("https://api.upbit.com/v1/candles/days",
                         {"market":"KRW-BTC","count":35})
        eth=_public_json("https://api.upbit.com/v1/candles/days",
                         {"market":"KRW-ETH","count":12})
        scored=coin_score(tickers,btc,eth)
    except Exception as exc:
        scored={"score":None,"coverage":0,"factors":[],
                "status":"업비트 시세 확인 실패: "+type(exc).__name__}
    scored["source"]="Upbit KRW public tickers and BTC/ETH daily candles"
    return scored


class _MarketWorker:
    """Independent 24h crypto polling, never held behind slow stock API calls."""
    def __init__(self,root):
        self.root=Path(root)
        self._cache_lock=threading.RLock()
        self._cache={"stock":{"score":None,"status":"주식 시세 확인 중"},
                     "coin":{"score":None,"status":"코인 시세 확인 중"}}
        self._pending=None
        self._last_failure={}
        self._stop=threading.Event()
        self._fetch_threads=[]
        self._thread=threading.Thread(target=self._loop,daemon=True,
            name="artitrade-market-ledger-v038")
        self._thread.start()

    def offer(self,records):
        with self._cache_lock:
            self._pending=records

    def snapshot(self):
        with self._cache_lock:
            return dict(self._cache)

    def stop(self):
        """Used by deterministic regression tests; app daemon normally remains alive."""
        self._stop.set()
        self._thread.join(timeout=8)

    def _fetch_loop(self,ledger,market,fetch):
        # Stock 3min, crypto 60sec even on weekends/holidays.
        interval=60 if market=="coin" else 180
        while not self._stop.is_set():
            try:
                with self._cache_lock:
                    pending=self._pending
                positions=_extract_positions(pending or {})[market]
                evidence=fetch()
                score=finite(evidence.get("score"))
                if score is not None:
                    ledger.record_score(market,score,int(time.time()),
                                        holding_returns(positions),evidence)
                    self._last_failure.pop(market,None)
                else:
                    self._last_failure[market]=evidence.get(
                        "status","시장자료 미수신")
            except Exception as exc:
                self._last_failure[market]="시세 수집 오류: "+type(exc).__name__
            try:
                snapshot=display_snapshot(market,ledger.snapshot(market),
                            poll_error=self._last_failure.get(market))
                with self._cache_lock:
                    self._cache[market]=snapshot
            except Exception as exc:
                with self._cache_lock:
                    self._cache[market]={"score":None,
                        "status":"시장점수 원장 오류: "+type(exc).__name__}
            self._stop.wait(interval)

    def _loop(self):
        ledger=None
        try:
            ledger=ScoreLedger(self.root/"market_research_v033.sqlite3")
            # Deliver retained scores immediately; score collection is independent
            # and can be slower than the first user-visible dashboard frame.
            for market in ("coin","stock"):
                with self._cache_lock:
                    self._cache[market]=display_snapshot(
                        market,ledger.snapshot(market))
            for market,fetch in (("coin",_fetch_coin),("stock",_fetch_stock)):
                thread=threading.Thread(target=self._fetch_loop,
                    args=(ledger,market,fetch),daemon=True,
                    name="artitrade-"+market+"-market-score-v038")
                self._fetch_threads.append(thread)
                thread.start()
            while not self._stop.is_set():
                with self._cache_lock:
                    pending=self._pending
                holdings=_extract_positions(pending or {})
                for market in ("coin","stock"):
                    try:
                        ledger.observe_positions(market,
                            holding_returns(holdings[market]),int(time.time()))
                        current=display_snapshot(
                            market,ledger.snapshot(market),
                            poll_error=self._last_failure.get(market))
                        with self._cache_lock:
                            self._cache[market]=current
                    except Exception as exc:
                        with self._cache_lock:
                            self._cache[market]={"score":None,
                                "status":"시장점수 처리 오류: "+type(exc).__name__}
                self._stop.wait(12)
        except Exception as exc:
            with self._cache_lock:
                for market in ("stock","coin"):
                    self._cache[market]={"score":None,
                        "status":"시장분석 엔진 오류: "+type(exc).__name__}
        finally:
            self._stop.set()
            for thread in self._fetch_threads:
                thread.join(timeout=8)
            if ledger is not None:
                ledger.close()


_WORKERS={}
_WORKERS_LOCK=threading.Lock()


def offer_records(root,records):
    """Called by existing hub records adapter; only enqueue a reference and return."""
    path=str(Path(root).resolve())
    with _WORKERS_LOCK:
        worker=_WORKERS.get(path)
        if worker is None:
            worker=_MarketWorker(path)
            _WORKERS[path]=worker
    worker.offer(records)
    return worker.snapshot()
