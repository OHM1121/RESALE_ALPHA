"""ARTITRADE v040 incremental AI evidence journal.

Separate research-only SQLite files under stock/ai and coin/ai are included in
both scoped and full verified exports. Never writes strategy or order ledgers.
All snapshots store the time at which the phone *observed* the value; historical
signal timestamps are never substituted for collection timestamps.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import sqlite3
import threading
import time
from pathlib import Path

SCOPES = (("stock","at"),("stock","ma"),("stock","close"),("stock","gmo"),
          ("coin","general"),("coin","ma"))
FRAMES = ("5m","30m","60m","day")
FEATURE_NAMES = {
    "score": ("score","diverse_11_score","gmo_score_at_signal","signal_score"),
    "atr": ("atr","atr14","atr_14","atr_value"),
    "atr_pct": ("atr_pct","atr_percent","atr_ratio_pct","atr_to_price_pct"),
    "volume": ("volume","volume_current","trade_volume","acc_trade_volume_24h","vol"),
    "volume_ratio": ("volume_ratio","vol_ratio","relative_volume","rvol","volume_multiple"),
    "williams_r": ("williams_r","williams","williams_percent_r","williams_pct_r","williams14","wr14"),
    "rsi": ("rsi","rsi14","rsi_14"),
    "mfi": ("mfi","mfi14"),
    "adx": ("adx","adx14","adx_14"),
    "macd": ("macd","macd_value","macd_histogram"),
    "vwap": ("vwap","volume_weighted_average_price"),
    "obv": ("obv","on_balance_volume"),
    "bollinger_bandwidth_pct": ("bollinger_bandwidth_pct","bb_width_pct"),
    "spread_pct": ("spread_pct","bid_ask_spread_pct","spread_percent"),
    "gap_pct": ("gap_pct","gap_percent","open_gap_pct"),
    "bid_imbalance": ("bid_imbalance","buy_orderbook_ratio","orderbook_bid_ratio"),
    "market_score": ("market_score","market_regime_score","market_condition_score"),
    "market_breadth": ("market_breadth","market_advance_ratio"),
    "price": ("entry_price","buy_price","limit_price","close","last_price"),
    "previous_close": ("previous_close","prev_close"),
    "price_change_pct": ("change_pct","price_change_pct","return_1d_pct"),
    "trade_value": ("trade_value","acc_trade_price_24h","turnover"),
    "foreign_net_buy": ("foreign_net_buy","foreign_net_amount"),
    "institution_net_buy": ("institution_net_buy","institution_net_amount"),
    "orderbook_depth": ("orderbook_depth","bid_depth","total_bid_qty"),
    "market_volatility": ("market_volatility","volatility","realized_volatility"),
}
EXCLUDED = ("future_","forward_","observed_","post_","after_","exit_",
            "sell_","target_","profit_","realized_","unrealized_")
DB_NAME = "AI_RESEARCH_V040.sqlite3"
LOCK = threading.RLock()
WORKERS = {}


def _number(value):
    if value is None or isinstance(value,bool):return None
    try:
        v=float(str(value).replace(",","").replace("%",""))
        return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None


def _timestamp(value):
    # Numeric KST YYYYMMDDHHMMSS is NOT a Unix millisecond timestamp.
    # Parse compact calendar timestamps before attempting epoch conversion.
    s=str(value or "").strip()
    if s.endswith(".0") and s[:-2].isdigit():s=s[:-2]
    if isinstance(value,(float,int)) and value>=1000000000 and not (
            len(s)==14 and s[:4].isdigit() and 1990<=int(s[:4])<=2100):
        return int(value/1000) if value>100000000000 else int(value)
    try:
        if len(s)>=14 and s[:14].isdigit():
            d=dt.datetime.strptime(s[:14],"%Y%m%d%H%M%S")
            return int(d.replace(tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp())
        if len(s)==8 and s.isdigit():
            d=dt.datetime.strptime(s,"%Y%m%d")
            return int(d.replace(tzinfo=dt.timezone(dt.timedelta(hours=9))).timestamp())
        if len(s)>=10 and s[:10].count("-")==2:
            d=dt.datetime.fromisoformat(s.replace("Z","+00:00"))
            if d.tzinfo is None:d=d.replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
            return int(d.timestamp())
    except (ValueError,TypeError,OverflowError):pass
    return None


def _frame(value,strategy):
    if strategy=="gmo":return "gmo"
    if strategy=="close":return "day"
    s=str(value or "").lower().strip()
    if s in ("5m","5","5min","5분"):return "5m"
    if s in ("30m","30","30min","30분"):return "30m"
    if s in ("60m","60","60min","1h","1시간"):return "60m"
    if s in ("day","d","1d","daily","일봉"):return "day"
    return ""


def _json_obj(x):
    if isinstance(x,dict):return x
    if isinstance(x,str):
        try:
            d=json.loads(x)
            return d if isinstance(d,dict) else {}
        except (ValueError,TypeError):pass
    return {}


def _n_relevant_bars(row, asof=None):
    for field in ("bars","candles","ohlcv","historical_candles","recent_bars",
                  "signal_candles"):
        vals=row.get(field)
        if isinstance(vals,str):
            try:vals=json.loads(vals)
            except (ValueError,TypeError):continue
        if not isinstance(vals,list):continue
        usable=[]
        for bar in vals:
            if not isinstance(bar,dict):continue
            ts=_timestamp(bar.get("ts") or bar.get("at") or bar.get("time") or
                          bar.get("candle_date_time_kst"))
            if asof is not None and (ts is None or ts>asof):continue
            o=_number(bar.get("open") or bar.get("opening_price"))
            h=_number(bar.get("high") or bar.get("high_price"))
            l=_number(bar.get("low") or bar.get("low_price"))
            c=_number(bar.get("close") or bar.get("trade_price"))
            v=_number(bar.get("volume") or bar.get("candle_acc_trade_volume"))
            if c is None or h is None or l is None or h<l or h<c or l>c:continue
            usable.append((ts,h,l,c,v))
        usable.sort(key=lambda item:item[0] or 0)
        return usable[-90:]
    return []


def compute_candle_indicators(bars):
    """Only historical completed OHLCV bars, never predicted price or future bars."""
    out={}
    if len(bars)<15:return out
    recent=bars[-14:]
    highest=max(x[1] for x in recent)
    lowest=min(x[2] for x in recent)
    close=bars[-1][3]
    if highest>lowest:
        out["williams_r"]=round(-100*(highest-close)/(highest-lowest),3)
    tr=[]
    for i in range(-14,0):
        high,low=bars[i][1],bars[i][2]
        previous=bars[i-1][3]
        tr.append(max(high-low,abs(high-previous),abs(low-previous)))
    out["atr"]=round(sum(tr)/14,6)
    if close>0:out["atr_pct"]=round(100*out["atr"]/close,4)
    # Wilder RSI; more data improves stability.
    if len(bars)>=16:
        diff=[bars[i][3]-bars[i-1][3] for i in range(1,len(bars))]
        changes=diff[-14:]
        avg_up=sum(max(v,0) for v in changes)/14
        avg_down=sum(max(-v,0) for v in changes)/14
        out["rsi"]=round(100 if avg_down==0 and avg_up>0 else
                         50 if avg_down==0 else 100-100/(1+avg_up/avg_down),3)
    volumes=[x[4] for x in bars]
    if len(bars)>=21 and all(v is not None for v in volumes[-21:]):
        typical=sum(volumes[-21:-1])/20
        if typical>0:
            out["volume_ratio"]=round(volumes[-1]/typical,4)
    observed=[x for x in bars[-20:] if x[4] is not None]
    if len(observed)>=14:
        total=sum(x[4] for x in observed)
        if total>0:out["vwap"]=round(sum(x[3]*x[4] for x in observed)/total,6)
    if len(bars)>=6 and bars[-6][3]>0:
        out["return_5bars_pct"]=round(100*(close/bars[-6][3]-1),4)
    return out


def extract_features(row, asof=None):
    """Allowlisted, point-in-time fields only. Never infer missing indicators."""
    if not isinstance(row,dict):return {}
    raw=dict(row)
    for name in ("features","feature_json","reason_json","indicators",
                 "signal_features","technical_indicators","market_factors"):
        nested=_json_obj(row.get(name))
        for key,value in nested.items():
            if key not in raw:raw[key]=value
    lower={str(k).lower():v for k,v in raw.items()
           if not str(k).lower().startswith(EXCLUDED)}
    result={}
    for key,aliases in FEATURE_NAMES.items():
        for alias in aliases:
            value=_number(lower.get(alias))
            if value is not None:
                result[key]=value
                break
    candle_values=compute_candle_indicators(_n_relevant_bars(row,asof))
    for key,value in candle_values.items():
        if key not in result:result[key]=value
    if "atr" in result and "atr_pct" not in result:
        price=result.get("price")
        if price and price>0:result["atr_pct"]=round(100*result["atr"]/price,4)
    return result


def _records(packet,market,strategy):
    section=_json_obj(packet.get(market))
    if market=="stock":
        if strategy in ("at","ma","gmo"):
            at=_json_obj(section.get("at"))
            led=_json_obj(at.get("ledger"))
            res=_json_obj(at.get("research"))
            if strategy=="ma":
                return (_json_obj(res.get("ma")),
                        _json_obj(led.get("ma_subledger") or led.get("ma")))
            if strategy=="gmo":
                return (_json_obj(res.get("gmo")),_json_obj(led.get("gmo")))
            return res,led
        cw=_json_obj(section.get("cw"))
        return _json_obj(cw.get("research")),_json_obj(cw.get("ledger"))
    coin=_json_obj(section.get(strategy))
    return _json_obj(coin.get("research")),_json_obj(coin.get("ledger"))


def _rows(record,keys):
    for key in keys:
        values=record.get(key)
        if isinstance(values,list):
            return [x for x in values if isinstance(x,dict)]
    return []


def _key(row,strategy,kind):
    code=str(row.get("code") or row.get("market") or "")
    tf=_frame(row.get("tf") or row.get("frame"),strategy)
    stamp=str(row.get("ref_ts") or row.get("ref_at") or row.get("event_date") or
              row.get("gmo_event_date") or row.get("entry_date") or "")
    primary=row.get("signal_id") if kind=="signal" else row.get("id")
    if primary is None:primary=row.get("candidate_id") if kind=="signal" else row.get("position_id")
    if primary is not None:return str(primary)+"|"+tf+"|"+code
    if not code and not stamp:return None
    return tf+"|"+code+"|"+stamp


def db_path(root, market):
    if market not in ("stock","coin"):raise ValueError("invalid market")
    return Path(root)/market/"ai"/DB_NAME


def _connect(root,market):
    path=db_path(root,market)
    path.parent.mkdir(parents=True,exist_ok=True)
    conn=sqlite3.connect(str(path),timeout=8)
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS feature_snapshots(
        strategy TEXT NOT NULL,frame TEXT NOT NULL,signal_key TEXT NOT NULL,
        event_ts INTEGER,first_captured INTEGER NOT NULL,last_seen INTEGER NOT NULL,
        features_sha TEXT NOT NULL,features_json TEXT NOT NULL,
        PRIMARY KEY(strategy,signal_key,features_sha)
      );
      CREATE TABLE IF NOT EXISTS position_observations(
        strategy TEXT NOT NULL,frame TEXT NOT NULL,position_key TEXT NOT NULL,
        observed_minute INTEGER NOT NULL,entry_ts INTEGER,exit_ts INTEGER,
        status TEXT NOT NULL,mark REAL,entry_price REAL,quantity REAL,
        pnl REAL,market_score REAL,extra_json TEXT NOT NULL,
        PRIMARY KEY(strategy,position_key,observed_minute)
      );
      CREATE INDEX IF NOT EXISTS pos_dates_v040
        ON position_observations(strategy,observed_minute);
      CREATE INDEX IF NOT EXISTS feat_dates_v040
        ON feature_snapshots(strategy,frame,event_ts);
      CREATE TABLE IF NOT EXISTS candle_observations(
        strategy TEXT NOT NULL,frame TEXT NOT NULL,symbol TEXT NOT NULL,
        bar_ts INTEGER NOT NULL,high REAL NOT NULL,low REAL NOT NULL,
        close REAL NOT NULL,volume REAL,origin TEXT NOT NULL,
        PRIMARY KEY(strategy,frame,symbol,bar_ts)
      );
      CREATE TABLE IF NOT EXISTS market_score_observations(
        score_ts INTEGER NOT NULL PRIMARY KEY,
        score REAL NOT NULL,source TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS analysis_runs(
        strategy TEXT NOT NULL,
        generated_at INTEGER NOT NULL,
        report_json TEXT NOT NULL,
        PRIMARY KEY(strategy,generated_at)
      );
    """)
    return conn


def capture_scope(root,market,strategy,research,ledger,now=None,market_score=None):
    if (market,strategy) not in SCOPES:return {"signals":0,"positions":0}
    now=int(now if now is not None else time.time())
    featrows=_rows(_json_obj(research),("items","candidates","signals"))
    positions=_rows(_json_obj(ledger),("positions","holdings"))
    signal_count=position_count=0
    with _connect(root,market) as conn:
        for row in featrows:
            key=_key(row,strategy,"signal")
            if not key:continue
            frame=_frame(row.get("tf") or row.get("frame"),strategy)
            if not frame and strategy not in ("gmo","close"):continue
            when=_timestamp(row.get("at") or row.get("signal_ts") or
                            row.get("created_at") or row.get("event_date") or
                            row.get("ref_ts") or row.get("ref_at") or row.get("ts"))
            # Features captured *today* for an imported old signal are retained,
            # but their capture time is never backdated to the signal date.
            indicators_bars=_n_relevant_bars(row,when if when is not None else now)
            for bar_ts,hi,lo,close,volume in indicators_bars:
                if bar_ts is None:continue
                conn.execute("""INSERT OR IGNORE INTO candle_observations
                    (strategy,frame,symbol,bar_ts,high,low,close,volume,origin)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (strategy,frame,str(row.get("code") or row.get("market") or ""),
                     bar_ts,hi,lo,close,volume,"SOURCE_OHLCV"))
            features=extract_features(row,when if when is not None else now)
            if not features:continue
            encoded=json.dumps(features,sort_keys=True,separators=(",",":"))
            digest=hashlib.sha256(encoded.encode("utf8")).hexdigest()[:24]
            conn.execute("""INSERT INTO feature_snapshots
                (strategy,frame,signal_key,event_ts,first_captured,last_seen,
                 features_sha,features_json) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(strategy,signal_key,features_sha)
                DO UPDATE SET last_seen=excluded.last_seen""",
                (strategy,frame,key,when,now,now,digest,encoded))
            signal_count+=1
        for row in positions:
            key=_key(row,strategy,"position")
            if not key:continue
            frame=_frame(row.get("tf") or row.get("frame"),strategy)
            status=str(row.get("status") or row.get("state") or "UNKNOWN").upper()
            entry_ts=_timestamp(row.get("entry_at") or row.get("entry_ts") or
                                row.get("buy_at") or row.get("time"))
            exit_ts=_timestamp(row.get("exit_at") or row.get("closed_at") or
                               row.get("sold_at") or row.get("sell_at"))
            mark=_number(row.get("last_price") or row.get("mark") or
                         row.get("current_price") or row.get("market_price"))
            entry=_number(row.get("entry_price") or row.get("entry") or
                          row.get("buy_price"))
            qty=_number(row.get("qty") or row.get("quantity") or
                        row.get("initial_qty"))
            pnl=next((_number(row.get(x)) for x in
                       ("unrealized_pnl","net_pnl","pnl","paper_pnl","net_profit")
                       if _number(row.get(x)) is not None),None)
            extra=extract_features(row,now)
            if market_score is not None:extra["current_market_score"]=market_score
            conn.execute("""INSERT INTO position_observations
                (strategy,frame,position_key,observed_minute,entry_ts,exit_ts,
                 status,mark,entry_price,quantity,pnl,market_score,extra_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(strategy,position_key,observed_minute)
                DO UPDATE SET status=excluded.status,mark=excluded.mark,
                    quantity=excluded.quantity,pnl=excluded.pnl,
                    market_score=excluded.market_score,extra_json=excluded.extra_json""",
                (strategy,frame,key,now//60*60,entry_ts,exit_ts,status,
                 mark,entry,qty,pnl,market_score,
                 json.dumps(extra,sort_keys=True,separators=(",",":"))))
            position_count+=1
    return {"signals":signal_count,"positions":position_count}


def capture_packet(root,packet,now=None):
    data=_json_obj(packet)
    scores=_json_obj(data.get("market_research"))
    totals={"signals":0,"positions":0}
    # Duplicate the exact event-time score into each market-specific export DB.
    # Never replace the event timestamp with the present observation time.
    for market in ("stock","coin"):
        evidence=_json_obj(scores.get(market))
        score=_number(evidence.get("score"))
        asof=_timestamp(evidence.get("asof"))
        if score is None or asof is None or not -100<=score<=100:continue
        try:
            with _connect(root,market) as conn:
                conn.execute("""INSERT OR IGNORE INTO market_score_observations
                  (score_ts,score,source) VALUES (?,?,?)""",
                  (asof,score,str(evidence.get("source") or "MARKET_RESEARCH_V033")))
        except (sqlite3.Error,OSError):
            pass
    for market,strategy in SCOPES:
        research,ledger=_records(data,market,strategy)
        score=_number(_json_obj(scores.get(market)).get("score"))
        try:
            result=capture_scope(root,market,strategy,research,ledger,now,score)
            for name in totals:totals[name]+=result[name]
        except (sqlite3.Error,OSError,ValueError):
            # One broken archive must never block other strategy evidence.
            continue
    return totals


def stored(root,market,strategy):
    """Readable union; only the selected strategy, never mixed markets."""
    path=db_path(root,market)
    output={"features":[],"positions":[],"market_scores":[],"candles":[]}
    if not path.is_file():return output
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=5) as conn:
        conn.row_factory=sqlite3.Row
        output["features"]=[dict(x) for x in conn.execute(
            "SELECT * FROM feature_snapshots WHERE strategy=? ORDER BY event_ts",
            (strategy,))]
        output["positions"]=[dict(x) for x in conn.execute(
            "SELECT * FROM position_observations WHERE strategy=? ORDER BY observed_minute",
            (strategy,))]
        output["market_scores"]=[dict(x) for x in conn.execute(
            "SELECT score_ts,score FROM market_score_observations ORDER BY score_ts")]
        output["candles"]=[dict(x) for x in conn.execute(
            "SELECT frame,symbol,bar_ts,high,low,close,volume FROM candle_observations "
            "WHERE strategy=? ORDER BY bar_ts",(strategy,))]
    return output


def save_report(root,market,strategy,report,when=None):
    """Preserve every button-triggered report in scoped research ZIP exports."""
    if (market,strategy) not in SCOPES:return
    moment=int(when if when is not None else time.time())
    if not isinstance(report,dict) or report.get("scope")!=market+"/"+strategy:
        raise ValueError("analysis scope mismatch")
    with _connect(root,market) as conn:
        conn.execute("""INSERT INTO analysis_runs(strategy,generated_at,report_json)
                        VALUES (?,?,?)
                        ON CONFLICT(strategy,generated_at)
                        DO UPDATE SET report_json=excluded.report_json""",
                     (strategy,moment,json.dumps(report,ensure_ascii=False,
                                                 separators=(",",":"))))


class _EvidenceWorker:
    def __init__(self,root):
        self.root=str(root)
        self._lock=threading.RLock()
        self._signal=threading.Event()
        self._pending=None
        self._latest=0
        self._thread=threading.Thread(target=self._loop,daemon=True,
            name="artitrade-ai-evidence-v040")
        self._thread.start()

    def offer(self,packet):
        now=time.monotonic()
        with self._lock:
            if now-self._latest<35:return
            self._pending=packet
            self._latest=now
            self._signal.set()

    def _loop(self):
        while True:
            self._signal.wait()
            with self._lock:
                packet=self._pending
                self._pending=None
                self._signal.clear()
            if packet is None:continue
            try:capture_packet(self.root,packet)
            except Exception:pass


def offer_records(root,packet):
    """Non-blocking queue: hub trading data is NOT written on hub thread."""
    key=str(Path(root).resolve())
    with LOCK:
        worker=WORKERS.get(key)
        if worker is None:
            worker=_EvidenceWorker(key)
            WORKERS[key]=worker
    worker.offer(packet)
    return True
