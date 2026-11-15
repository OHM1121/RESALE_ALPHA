"""Asynchronous, strategy-ledger-independent persistence of approved v048 evidence.

Scope: actual *confirmed app fills* (LIVE/PAPER separately where provable),
and explicitly observed virtual entries. Unexecuted candidates are excluded.
No trading database, broker order, account balance or original trade row edits.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from contextlib import closing

import ai_research_v040 as jr
import ai_entry_context_v047 as v47
import ai_approved_metrics_v048 as met

_TABLE="trade_evidence_v048"
_LAST_BENCHMARK_ATTEMPT={}

def _ensure(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS trade_evidence_v048(
       strategy TEXT NOT NULL,evidence_key TEXT NOT NULL,
       role TEXT NOT NULL,entry_mode TEXT NOT NULL,
       frame TEXT NOT NULL,symbol TEXT NOT NULL,entry_ts INTEGER NOT NULL,
       source_position_id TEXT,entry_price REAL,initial_qty REAL,
       market_id TEXT,execution_kind TEXT NOT NULL,
       exit_ts INTEGER,closed INTEGER NOT NULL DEFAULT 0,
       entry_json TEXT NOT NULL,post_json TEXT NOT NULL,
       first_seen INTEGER NOT NULL,updated_at INTEGER NOT NULL,
       PRIMARY KEY(strategy,evidence_key));
    CREATE INDEX IF NOT EXISTS v048_trade_scope
       ON trade_evidence_v048(strategy,entry_mode,frame,entry_ts);
    CREATE INDEX IF NOT EXISTS v048_trade_symbol
       ON trade_evidence_v048(strategy,frame,symbol,entry_ts);
    CREATE TABLE IF NOT EXISTS trade_exit_events_v048(
       strategy TEXT NOT NULL,evidence_key TEXT NOT NULL,
       execution_key TEXT NOT NULL,at_ms INTEGER NOT NULL,
       side TEXT NOT NULL,payload TEXT NOT NULL,
       PRIMARY KEY(strategy,evidence_key,execution_key));
    CREATE INDEX IF NOT EXISTS v048_exit_parent
       ON trade_exit_events_v048(strategy,evidence_key,at_ms);
    CREATE TABLE IF NOT EXISTS candle_extra_v048(
       strategy TEXT NOT NULL,frame TEXT NOT NULL,symbol TEXT NOT NULL,
       bar_ts INTEGER NOT NULL,open_price REAL,turnover REAL,
       origin TEXT NOT NULL,
       PRIMARY KEY(strategy,frame,symbol,bar_ts));
    CREATE TABLE IF NOT EXISTS benchmark_candles_v048(
       benchmark TEXT NOT NULL,frame TEXT NOT NULL,bar_ts INTEGER NOT NULL,
       close REAL NOT NULL,origin TEXT NOT NULL,stored_at INTEGER NOT NULL,
       PRIMARY KEY(benchmark,frame,bar_ts));
    """)

def _kind(row,role):
    if role=="VIRTUAL":return "SHADOW_UNFILLED"
    raw=str(met._first(row,"execution_mode","fill_method","order_mode",
                       "broker_mode","account_mode") or "").upper()
    if any(x in raw for x in ("PAPER","SIMUL","MOCK","VIRTUAL_FILL")):return "PAPER"
    if any(x in raw for x in ("LIVE","BROKER","REAL_FILL")):return "LIVE"
    return "UNKNOWN_FILLED_LEDGER"

def _market_id(market,row):
    if market=="coin":return "KRW-BTC"
    raw=str(met._first(row,"market_id","exchange","market_type","listing_market",
                       "market") or "").upper()
    if "KOSDAQ" in raw or raw in ("KQ","KQ11"):return "^KQ11"
    if "KOSPI" in raw or raw in ("KS","KS11"):return "^KS11"
    return None

def _price(row):
    return met._num(met._first(row,"entry_price","entry","buy_price",
                               "avg_buy_price","price","fill_price"))

def _quantity(row):
    return met._num(met._first(row,"qty0","entry_qty","original_qty",
                               "initial_qty","buy_qty"))

def _foreign_trend_row(row):
    if not isinstance(row,dict):return False
    mode=str(row.get("entry_mode") or row.get("mode") or "").upper()
    status=str(row.get("status") or "").upper()
    reason=str(row.get("reason") or "").upper()
    return ("MARKET_TREND" in mode or status.startswith("TREND_") or
            reason.startswith("MARKET_TREND_") or
            str(row.get("id") or "").startswith(("MT43:","MT54:")))


def _event_candidates(research,ledger,strategy):
    signals=jr._rows(research,("items","candidates","signals"))
    positions=jr._rows(ledger,("positions","holdings"))
    waiting=jr._rows(ledger,("pending","pending_orders","orders","candidates"))
    for row in positions:
        if strategy!="trend" and _foreign_trend_row(row):continue
        e=v47._candidate(row,strategy,"ACTUAL")
        if e:yield row,e
    for row in signals+waiting:
        if not isinstance(row,dict):continue
        if strategy!="trend" and _foreign_trend_row(row):continue
        if row.get("ledger_is_virtual_only") or row.get("virtual_entered") or (
            row.get("entry_observed") and not row.get("bought")):
            role="VIRTUAL"
        elif row.get("bought") or row.get("actually_bought"):
            role="ACTUAL"
        else:continue
        e=v47._candidate(row,strategy,role)
        if e:yield row,e

def _add_entry(conn,row,event,market,strategy,now):
    key=event["key"]
    old=conn.execute("""SELECT entry_json,post_json,entry_price,initial_qty,
                      market_id,execution_kind,source_position_id,exit_ts,closed
                      FROM trade_evidence_v048
                      WHERE strategy=? AND evidence_key=?""",
                      (strategy,key)).fetchone()
    spread=met.entry_spread(row,event["entry_ts"])
    if spread.get('value') is None:
        # The original trade rows generally have no bid/ask timestamp.
        # Recover ONLY a separately recorded pre-entry fresh book; a future
        # observation must never be attributed to the past trade.
        from ai_live_book_v058 import lookup
        observed=lookup(conn,event["symbol"],event["entry_ts"])
        if observed is not None:
            spread=met.entry_spread({'entry_quote':observed},event["entry_ts"])
    item={
       "02":spread,
       "gap_pct":met._metric(reason="WAITING_FOR_VERIFIED_ENTRY_CANDLE_OPEN"),
       "05":met.order_latency(row),
       "09":met._metric(reason="WAITING_FOR_FINAL_ENTRY_CANDLE_OHLC"),
       "10":met._metric(reason="WAITING_FOR_PRE_ENTRY_CANDLES"),
       "11":met._metric(reason="WAITING_FOR_TRUE_PRE_ENTRY_TURNOVER"),
       "16":met.signed_trade_flow(row,event["entry_ts"])
    }
    prior=json.loads(old[0]) if old else {}
    for code,value in item.items():
        before=prior.get(code,{}).get("value")
        if isinstance(before,(dict,list,int,float)) or before is not None:continue
        prior[code]=value
    post=json.loads(old[1]) if old else {
        "13":met._metric(reason="NO_POST_ENTRY_COMPLETED_CANDLES"),
        "14":met._metric(reason="NO_VERIFIED_SELL_STEPS"),
        "15":met._metric(reason="BENCHMARK_NOT_YET_AVAILABLE")
    }
    position_id=str(met._first(row,"id","position_id") or "") if event["role"]=="ACTUAL" else ""
    px=_price(row)
    qty=_quantity(row)
    exit_ts=jr._timestamp(met._first(row,"exit_at","closed_at","exit_ts",
                                     "sold_at","sell_at"))
    status=str(met._first(row,"status","state") or "").upper()
    is_closed=1 if status in ("CLOSED","TREND_CLOSED","DONE","SOLD","FILLED_EXIT") else 0
    kind=_kind(row,event["role"])
    mid=_market_id(market,row)
    if old:
        px=px if px is not None else old[2]
        qty=qty if qty is not None else old[3]
        mid=mid or old[4]
        kind=kind if kind not in ("UNKNOWN_FILLED_LEDGER",) else old[5]
        position_id=position_id or old[6] or ""
        exit_ts=exit_ts or old[7]
        is_closed=max(is_closed,int(old[8]))
    conn.execute("""INSERT INTO trade_evidence_v048(
       strategy,evidence_key,role,entry_mode,frame,symbol,entry_ts,
       source_position_id,entry_price,initial_qty,market_id,execution_kind,
       exit_ts,closed,entry_json,post_json,first_seen,updated_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
       ON CONFLICT(strategy,evidence_key) DO UPDATE SET
       source_position_id=excluded.source_position_id,
       entry_price=excluded.entry_price,initial_qty=excluded.initial_qty,
       market_id=excluded.market_id,execution_kind=excluded.execution_kind,
       exit_ts=excluded.exit_ts,closed=excluded.closed,
       entry_json=excluded.entry_json,updated_at=excluded.updated_at""",
       (strategy,key,event["role"],event["entry_mode"],event["frame"],
        event["symbol"],event["entry_ts"],position_id or None,px,qty,mid,kind,
        exit_ts,is_closed,
        json.dumps(prior,ensure_ascii=False,allow_nan=False,separators=(",",":")),
        json.dumps(post,ensure_ascii=False,allow_nan=False,separators=(",",":")),
        now,now))
    return key

def _capture_sells(conn,strategy,executions,now):
    """Append durable original SELL fills while parent UI still includes them."""
    entries=[dict(r) for r in conn.execute(
        "SELECT evidence_key,source_position_id,frame,symbol,entry_ts "
        "FROM trade_evidence_v048 WHERE strategy=? AND role='ACTUAL' "
        "AND source_position_id IS NOT NULL",(strategy,))]
    by_id={}
    for e in entries:by_id.setdefault(str(e["source_position_id"]),[]).append(e)
    writes=0
    for fill in executions:
        if not isinstance(fill,dict) or str(fill.get("side") or "").upper()!="SELL":
            continue
        pid=str(fill.get("position_id") or "")
        if pid not in by_id:continue
        when=met._time_ms(met._first(fill,"at","ts","filled_at","execution_at"))
        if when is None:continue
        symbol=str(met._first(fill,"code","market") or "")
        frame=str(met._first(fill,"tf","frame") or "")
        for e in by_id[pid]:
            if when/1000<e["entry_ts"]-3:continue
            if symbol and symbol!=e["symbol"]:continue
            if frame and jr._frame(frame,strategy)!=e["frame"]:continue
            rawkey=str(met._first(fill,"event_key","execution_id","id") or "")
            if not rawkey:
                core="|".join(str(met._first(fill,x) or "") for x in (
                    "ts","at","qty","price","reason","side"))
                rawkey="source_fields_"+hashlib.sha256(core.encode()).hexdigest()[:24]
            writes+=conn.execute(
                "INSERT OR IGNORE INTO trade_exit_events_v048 "
                "(strategy,evidence_key,execution_key,at_ms,side,payload) "
                "VALUES(?,?,?,?,?,?)",
                (strategy,e["evidence_key"],rawkey,when,"SELL",
                 json.dumps(fill,ensure_ascii=False,default=str))
            ).rowcount
    return writes

def _trend_historical_rows(root,market,strategy):
    """Backfill independent RSI20 actual/shadow entries incl. CLOSED positions.

    Never alter parent trading ledgers. Match an MT buy event by its ORIGINAL
    broker position_id and preserve the LIVE vs PAPER distinction.
    """
    file=Path(root)/"market_trend_v043.sqlite3"
    if not file.is_file():return []
    try:
        with closing(sqlite3.connect(file.resolve().as_uri()+"?mode=ro",
                                     uri=True,timeout=5)) as conn:
            conn.row_factory=sqlite3.Row
            tables={r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"position_state","virtual_position_state"}.issubset(tables):
                return []
            fills={}
            if "events" in tables:
                for e in conn.execute("""SELECT mode,payload FROM events
                     WHERE market=? AND strategy=? AND reason='MT_BUY_FILLED'""",
                     (market,strategy)):
                    try:pid=json.loads(e["payload"] or "{}").get("position_id")
                    except (TypeError,ValueError):pid=None
                    if pid is not None:
                        fills[str(pid)]="LIVE" if e["mode"]=="ACTUAL" else (
                            "PAPER" if e["mode"]=="PAPER" else "UNKNOWN_FILLED_LEDGER")
            results=[]
            for r in conn.execute("""SELECT position_id,frame,symbol,payload,status
                 FROM position_state WHERE market=? AND strategy=?""",
                 (market,strategy)):
                try:payload=json.loads(r["payload"])
                except (TypeError,ValueError):continue
                if not isinstance(payload,dict):continue
                row={**payload,"id":r["position_id"],"position_id":r["position_id"],
                     "tf":r["frame"],"code":r["symbol"],"market":r["symbol"],
                     "entry_price":payload.get("entry"),"qty0":payload.get("original_qty"),
                     "status":r["status"],"entry_mode":"MARKET_TREND",
                     "execution_mode":fills.get(str(r["position_id"]),
                                                 "UNKNOWN_FILLED_LEDGER")}
                results.append((row,"ACTUAL"))
            for r in conn.execute("""SELECT signal_key,frame,symbol,payload,status
                 FROM virtual_position_state WHERE market=? AND strategy=?""",
                 (market,strategy)):
                try:payload=json.loads(r["payload"])
                except (TypeError,ValueError):continue
                if not isinstance(payload,dict):continue
                row={**payload,"id":r["signal_key"],"tf":r["frame"],
                     "code":r["symbol"],"market":r["symbol"],
                     "virtual_buy_at":payload.get("entry_at"),
                     "entry_price":payload.get("entry"),"qty0":payload.get("original_qty"),
                     "status":r["status"],"entry_mode":"MARKET_TREND",
                     "virtual_entered":True}
                results.append((row,"VIRTUAL"))
            return results
    except (OSError,sqlite3.Error,ValueError):return []


def _trend_sell_rows(root,market,strategy):
    """Read verified RSI20 sell fills, never reconstruct missing executions."""
    file=Path(root)/"market_trend_v043.sqlite3"
    if not file.is_file():return []
    try:
        with closing(sqlite3.connect(file.resolve().as_uri()+"?mode=ro",
                                     uri=True,timeout=5)) as conn:
            conn.row_factory=sqlite3.Row
            rows=conn.execute("""SELECT event_key,at,frame,symbol,reason,
                  price,qty,payload FROM events
                  WHERE market=? AND strategy=? AND
                  event_key LIKE 'MT43:SELL:%:FILLED'
                  ORDER BY at""",(market,strategy)).fetchall()
        out=[]
        for rec in rows:
            try:payload=json.loads(rec["payload"] or "{}")
            except (ValueError,TypeError):continue
            pid=payload.get("position_id")
            if pid is None:continue
            out.append({"event_key":rec["event_key"],"position_id":pid,
                "at":rec["at"],"side":"SELL","tf":rec["frame"],
                "market":rec["symbol"],"reason":rec["reason"],
                "qty":rec["qty"],"price":rec["price"],
                "net_pnl":payload.get("net_pnl"),
                "fee":payload.get("fee"),"tax":payload.get("tax"),
                "execution_source":"MT43_VERIFIED_SELL_FILL"})
        return out
    except (sqlite3.Error,OSError,ValueError):return []


def _independent_wallet_evidence(root,market):
    """Read all MT54 positions and broker-confirmed/PAPER executions.

    The snapshot only previews recent rows; the immutable independent wallet
    remains the authoritative source, without modifying its SQLite files.
    """
    path=Path(root)/market/"trend"/"market_trend_wallet_v054.sqlite3"
    if not path.is_file():return [],[]
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                     uri=True,timeout=4)) as conn:
            conn.row_factory=sqlite3.Row
            positions=[]
            for p in conn.execute("SELECT * FROM positions ORDER BY id"):
                row=dict(p)
                row.update({"id":p["id"],"position_id":p["id"],
                    "tf":p["tf"],"code":p["symbol"],"market":p["symbol"],
                    "entry_at":p["entry_at"],"entry_price":p["entry"],
                    "qty0":p["qty0"],"execution_mode":p["execution_mode"],
                    "entry_mode":"MARKET_TREND","status":p["status"]})
                positions.append(row)
            executions=[]
            for event in conn.execute("SELECT * FROM executions WHERE side='SELL'"):
                event=dict(event)
                event.update({"tf":event["tf"],"code":event["symbol"],
                              "market":event["symbol"]})
                executions.append(event)
            return positions,executions
    except (sqlite3.Error,OSError,ValueError):
        return [],[]


def capture_packet(root,packet,now=None):
    """Must be called ONLY from the existing slow research background worker."""
    now=int(time.time() if now is None else now)
    data=jr._json_obj(packet)
    counts={"rows_seen":0,"sell_fills_preserved":0}
    for market,strategy in (*jr.SCOPES,("stock","trend"),("coin","trend")):
        research,ledger=jr._records(data,market,strategy)
        pending={}
        for row,event in _event_candidates(research,ledger,strategy):
            pending.setdefault(event["key"],(row,event))
        # Old MT43 parent trades remain in their original archive and must
        # not be recaptured as fresh original AT/MA trade evidence.
        wallet_positions,wallet_executions=(
            _independent_wallet_evidence(root,market) if strategy=='trend'
            else ([],[]))
        for row in wallet_positions:
            observed=v47._candidate(row,strategy,"ACTUAL")
            if observed:pending.setdefault(observed["key"],(row,observed))
        executions=jr._rows(ledger,("executions","fills","trades"))
        executions+=wallet_executions
        # Trend sells are committed in the independent market-trend journal;
        # join by the same source_position_id, never merely by ticker.
        # Standalone Trend owns its own broker fills; parent AT/MA never
        # import trend exit events into their outcome cohort.
        if not pending and not executions:continue
        try:
            from ma_v062_ai_scope import ai_root
            research_root=ai_root(root) if strategy=="ma" else root
            with jr._connect(research_root,market) as conn:
                # This local journal defaults to sqlite3 tuple rows.
                # Sidecar dictionaries must use named rows to avoid rollback.
                conn.row_factory=sqlite3.Row
                _ensure(conn)
                for row,event in pending.values():
                    _add_entry(conn,row,event,market,strategy,now)
                    counts["rows_seen"]+=1
                counts["sell_fills_preserved"]+=_capture_sells(
                    conn,strategy,executions,now)
        except (OSError,sqlite3.Error,ValueError,TypeError):
            continue
    return counts

def _all_ohlcv(conn,strategy,frame,symbol):
    """Raw complete-source candles + separate true turnover/open evidence."""
    return [tuple(r) for r in conn.execute("""
        SELECT c.bar_ts,c.high,c.low,c.close,c.volume,
               x.turnover,x.open_price
        FROM candle_observations AS c
        LEFT JOIN candle_extra_v048 AS x
          ON x.strategy=c.strategy AND x.frame=c.frame
         AND x.symbol=c.symbol AND x.bar_ts=c.bar_ts
        WHERE c.strategy=? AND c.frame=? AND c.symbol=?
        ORDER BY c.bar_ts""",(strategy,frame,symbol))]

def _benchmark_bars(conn,name,frame):
    if not name:return []
    return [(r[0],r[1],r[1],r[1],None,None,None) for r in conn.execute(
        "SELECT bar_ts,close FROM benchmark_candles_v048 "
        "WHERE benchmark=? AND frame=? ORDER BY bar_ts",(name,frame))]

def update_one(conn,row,bars,asof):
    """Sidecar UPDATE; all order/strategy state remains read-only."""
    strategy,key=row["strategy"],row["evidence_key"]
    entry_json=json.loads(row["entry_json"])
    post_json=json.loads(row["post_json"])
    info=met.entry_technicals(bars,row["entry_ts"],row["frame"],
                              row["entry_price"],asof)
    for code in ("09","10","11"):
        candidate=info[code]
        if candidate.get("value") is not None or entry_json.get(code,{}).get("value") is None:
            entry_json[code]=candidate
    opening_gap=met.entry_open_gap(bars,row["entry_ts"],row["frame"])
    if opening_gap.get("value") is not None or (
            entry_json.get("gap_pct") or {}).get("value") is None:
        entry_json["gap_pct"]=opening_gap
    exit_ts=row["exit_ts"]
    if not row["closed"] or exit_ts is not None:
        item=met.after_entry_extremes(bars,row["entry_ts"],row["frame"],
                       row["entry_price"],exit_ts,asof)
        if item["value"] is not None or post_json.get("13",{}).get("value") is None:
            post_json["13"]=item
    sells=[dict(json.loads(x[0])) for x in conn.execute(
        "SELECT payload FROM trade_exit_events_v048 "
        "WHERE strategy=? AND evidence_key=? ORDER BY at_ms",
        (strategy,key))]
    if row["role"]=="ACTUAL":
        original={"id":row["source_position_id"],"tf":row["frame"],
                  "market":row["symbol"],"qty0":row["initial_qty"]}
        steps=met.exit_steps(original,sells,row["entry_ts"],row["entry_price"])
        if steps["value"] is not None or post_json.get("14",{}).get("value") is None:
            post_json["14"]=steps
        if row["closed"] and exit_ts is None and steps["value"] is not None:
            stepvals=steps["value"]["steps"]
            if stepvals:exit_ts=int(stepvals[-1]["epoch_ms"]//1000)
    # A fully sold parent position is closed even if it has disappeared from
    # the current phone UI (trend and restored historical fills included).
    if row["role"]=="ACTUAL":
        step=(post_json.get("14") or {}).get("value")
        if step and step.get("sold_pct") is not None and step["sold_pct"]>=99.999:
            if not row["closed"]:
                conn.execute("UPDATE trade_evidence_v048 SET closed=1 WHERE strategy=? AND evidence_key=?",
                             (strategy,key))
                row["closed"]=1
            if exit_ts is None and step["steps"]:
                exit_ts=int(step["steps"][-1]["epoch_ms"]//1000)
                conn.execute("UPDATE trade_evidence_v048 SET exit_ts=? WHERE strategy=? AND evidence_key=?",
                             (exit_ts,strategy,key))
    if row["closed"] and exit_ts is not None and row["role"]=="ACTUAL":
        step=(post_json.get("14") or {}).get("value")
        if step is not None:
            values=step["steps"]
            sold=sum(x.get("filled_qty") or 0 for x in values)
            qty=row["initial_qty"]
            if qty and sold>=qty-1e-8:
                qty_total=sum(x.get("filled_qty") or 0 for x in values
                              if x.get("price") is not None)
                exit_price=(sum(x["filled_qty"]*x["price"] for x in values
                              if x.get("price") is not None and x.get("filled_qty"))
                              /qty_total) if qty_total>0 and qty_total>=sold-1e-8 else None
                benchmark=_benchmark_bars(conn,row["market_id"],row["frame"])
                post_json["15"]=met.benchmark_relative(
                    benchmark,row["market_id"],row["frame"],
                    row["entry_ts"],exit_ts,row["entry_price"],exit_price)
    encoded_entry=json.dumps(entry_json,ensure_ascii=False,allow_nan=False,
                             separators=(",",":"))
    encoded_post=json.dumps(post_json,ensure_ascii=False,allow_nan=False,
                            separators=(",",":"))
    if encoded_entry!=row["entry_json"] or encoded_post!=row["post_json"]:
        conn.execute("UPDATE trade_evidence_v048 "
            "SET entry_json=?,post_json=?,updated_at=? "
            "WHERE strategy=? AND evidence_key=?",
            (encoded_entry,encoded_post,int(asof),strategy,key))

def record_candles(root,market,strategy,symbol,frame,raw_rows,origin,now=None):
    """Called after existing v040 OHLCV write, never from order threads."""
    now=int(time.time() if now is None else now)
    if market not in ("coin","stock") or frame not in met.PERIOD:return 0
    bars=met._norm_bars(raw_rows)
    if not bars:return 0
    from ma_v062_ai_scope import ai_root
    research_root=ai_root(root) if strategy=="ma" else root
    with jr._connect(research_root,market) as conn:
        conn.row_factory=sqlite3.Row
        _ensure(conn)
        conn.executemany("""INSERT INTO candle_extra_v048(
           strategy,frame,symbol,bar_ts,open_price,turnover,origin)
           VALUES(?,?,?,?,?,?,?)
           ON CONFLICT(strategy,frame,symbol,bar_ts) DO UPDATE SET
             open_price=COALESCE(candle_extra_v048.open_price,excluded.open_price),
             turnover=COALESCE(candle_extra_v048.turnover,excluded.turnover)
           """,[(strategy,frame,symbol,x[0],x[6],x[5],origin) for x in bars
                 if x[0]+met.PERIOD[frame]<=now])
        linked=[dict(row) for row in conn.execute(
            "SELECT * FROM trade_evidence_v048 WHERE strategy=? AND frame=? "
            "AND symbol=? ORDER BY entry_ts DESC",
            (strategy,frame,symbol))]
        if not linked:return 0
        all_candles=_all_ohlcv(conn,strategy,frame,symbol)
        for event in linked:
            update_one(conn,event,all_candles,now)
        return len(linked)

def _research_rows(root,market,entry_mode=None,strategy=None,limit=None):
    from ma_v062_ai_scope import ai_root
    db=jr.db_path(ai_root(root) if strategy=="ma" else root,market)
    if not db.is_file():return []
    with closing(sqlite3.connect(db.resolve().as_uri()+"?mode=ro",
                                 uri=True,timeout=10)) as conn:
        conn.row_factory=sqlite3.Row
        has=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                         "AND name='trade_evidence_v048'").fetchone()
        if not has:return []
        cond=[];args=[]
        if entry_mode:
            cond.append("entry_mode=?");args.append(entry_mode)
        if strategy:
            cond.append("strategy=?");args.append(strategy)
        sql="SELECT * FROM trade_evidence_v048"
        if cond:sql+=" WHERE "+" AND ".join(cond)
        sql+=" ORDER BY entry_ts DESC"
        if limit is not None:sql+=" LIMIT ?";args.append(int(limit))
        return [dict(x) for x in conn.execute(sql,args)]

def summarize(root,market,entry_mode=None,strategy=None,preview_max=40):
    if market not in ("stock","coin"):raise ValueError("market")
    rows=_research_rows(root,market,entry_mode,strategy)
    coverage={key:{"known":0,"missing":0,"by_reason":Counter()}
              for key in (*met.APPROVED,"gap_pct") if key!="17"}
    frame_coverage={}
    modes=Counter();roles=Counter();frames=Counter()
    confirmed_returns=[]
    examples=[]
    for item in rows:
        kind=item["execution_kind"]
        modes[kind]+=1;roles[item["role"]]+=1;frames[item["frame"]]+=1
        if item["frame"] not in frame_coverage:
            frame_coverage[item["frame"]]={key:{"known":0,"missing":0}
                                           for key in coverage}
        a=json.loads(item["entry_json"]);p=json.loads(item["post_json"])
        vals={**a,**p}
        for code in coverage:
            reading=vals.get(code) or {}
            if reading.get("value") is None:
                coverage[code]["missing"]+=1
                frame_coverage[item["frame"]][code]["missing"]+=1
                coverage[code]["by_reason"][reading.get("missing_reason") or "NO_SOURCE"]+=1
            else:
                coverage[code]["known"]+=1
                frame_coverage[item["frame"]][code]["known"]+=1
        stage=(p.get("14") or {}).get("value") or {}
        roi=stage.get("net_return_pct")
        if item["closed"] and item["role"]=="ACTUAL" and roi is not None and (
            stage.get("sold_pct") is not None and stage["sold_pct"]>=99.999):
            confirmed_returns.append((item["entry_ts"],float(roi),kind))
        if len(examples)<preview_max:
            examples.append({"strategy":item["strategy"],"key":item["evidence_key"],
                 "role":item["role"],"entry_mode":item["entry_mode"],
                 "execution_kind":kind,"frame":item["frame"],
                 "symbol":item["symbol"],"entry_ts":item["entry_ts"],
                 "entry_recording":a,"post_entry_analysis":p})
    confirmed_returns.sort(key=lambda v:v[0])
    by_execution={}
    for kind in ("LIVE","PAPER","UNKNOWN_FILLED_LEDGER"):
        vals=[ret for _,ret,m in confirmed_returns if m==kind]
        by_execution[kind]=met.cohort_stats(vals)
    return {
       "schema":met.SCHEMA,"approved_codes":list(met.APPROVED),
       "market":market,"entry_mode":entry_mode,"strategy":strategy,
       "entry_records":len(rows),"by_role":dict(roles),
       "by_execution_kind":dict(modes),"by_frame":dict(frames),
       "coverage":{
          code:{"known":c["known"],"missing":c["missing"],
                "pct":round(100*c["known"]/len(rows),2) if rows else None,
                "missing_reasons":dict(c["by_reason"])}
          for code,c in coverage.items()},
       "coverage_by_frame":{
          frame:{code:{"known":val["known"],"missing":val["missing"],
                       "pct":round(100*val["known"]/(val["known"]+val["missing"]),2)
                       if val["known"]+val["missing"] else None}
                 for code,val in items.items()}
          for frame,items in frame_coverage.items()},
       "17":{
          "verified_closed_n":len(confirmed_returns),
          "all_confirmed_fills_chronological":met.cohort_stats(
             [x[1] for x in confirmed_returns]),
          "by_execution_kind":by_execution,
          "note":"Outcome sample requires source net profit, exact position-linked "
                 "sell qty and full position closure; otherwise excluded"},
       "preview_max":preview_max,"preview_truncated":len(rows)>preview_max,
       "preview":examples,
       "all_details_in_original_market_ai_sqlite":True,
       "database_table":"trade_evidence_v048",
       "no_broker_or_strategy_ledger_modified":True}

def export_json(root,market,entry_mode=None,strategy=None,preview_max=500):
    record=summarize(root,market,entry_mode,strategy,preview_max)
    return json.dumps(record,ensure_ascii=False,indent=2,allow_nan=False)

def _fetch_yahoo_index(code,frame,now):
    from market_research_v033 import _public_json
    resolution={"5m":"5m","30m":"30m","60m":"60m","day":"1d"}[frame]
    query={"interval":resolution,"range":"1y" if frame=="day" else
           "1mo" if frame=="5m" else "3mo"}
    for host in ("query1.finance.yahoo.com","query2.finance.yahoo.com"):
        try:
            result=_public_json("https://"+host+
                "/v8/finance/chart/"+quote(code,safe=""),query)
            chart=result["chart"]["result"][0]
            quote0=chart["indicators"]["quote"][0]
            candles=[]
            for ts,close in zip(chart["timestamp"],quote0["close"]):
                value=met._num(close)
                if value is not None and value>0 and int(ts)+met.PERIOD[frame]<=now:
                    candles.append((int(ts),value))
            if candles:return candles,"YAHOO_KRX_INDEX_CLOSED"
        except (OSError,ValueError,TypeError,KeyError,IndexError,ImportError):
            continue
    return [],""

def refresh_benchmark(root,market,frame,now=None):
    """One network call per benchmark/frame window, research worker only."""
    if market not in ("stock","coin") or frame not in met.PERIOD:return 0
    now=int(time.time() if now is None else now)
    targets=["KRW-BTC"] if market=="coin" else ["^KS11","^KQ11"]
    updated=0
    for name in targets:
        key=(str(root),market,name,frame)
        last=_LAST_BENCHMARK_ATTEMPT.get(key,0)
        if now-last<1200:continue
        _LAST_BENCHMARK_ATTEMPT[key]=now
        try:
            if market=="coin":
                from ai_market_data_v040 import fetch_candles
                records,origin=fetch_candles("coin","KRW-BTC",frame,now)
                items=[(int(row[0]),float(row[3])) for row in records]
            else:items,origin=_fetch_yahoo_index(name,frame,now)
            if not items:continue
            with jr._connect(root,market) as conn:
                _ensure(conn)
                conn.executemany("INSERT OR IGNORE INTO benchmark_candles_v048 "
                   "(benchmark,frame,bar_ts,close,origin,stored_at) VALUES(?,?,?,?,?,?)",
                   [(name,frame,ts,close,origin,now) for ts,close in items])
            updated+=len(items)
        except (OSError,sqlite3.Error,ValueError,TypeError,KeyError,ImportError):
            continue
    return updated
