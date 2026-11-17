"""V053 authoritative post-SALE high tracker; research only, never an order source.

OLD signal-date and minimum-seven-day follow-up rules are not used here.
The exit/expected-exit anchor is per position. Every high is from a received,
time-stamped real public quote; gaps are explicit, not fabricated candles.
"""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import math
import queue
import sqlite3
import threading
import time
from collections import Counter,defaultdict
from contextlib import closing
from pathlib import Path

KST=dt.timezone(dt.timedelta(hours=9))
SCHEMA="ARTITRADE_POST_EXIT_HIGH_V053"
FILE="POST_EXIT_HIGH_V053.sqlite3"
WINDOWS={"5m":5,"30m":5,"60m":10,"day":15,"gmo":5,"close":5}
MAX_QUEUE=8192
MAX_WATCH_STOCK=140
MAX_WATCH_COIN=120


def parsed_at(value):
    if isinstance(value,dt.datetime):
        return value.astimezone(KST) if value.tzinfo else None
    if isinstance(value,(int,float)):
        if not math.isfinite(float(value)) or float(value)<=0:return None
        epoch=float(value)/1000 if float(value)>10**12 else float(value)
        try:return dt.datetime.fromtimestamp(epoch,KST)
        except (ValueError,OverflowError,OSError):return None
    text=str(value or "").strip()
    if not text:return None
    try:
        if len(text)==14 and text.isdigit():
            return dt.datetime.strptime(text,"%Y%m%d%H%M%S").replace(tzinfo=KST)
        stamp=dt.datetime.fromisoformat(text.replace("Z","+00:00"))
        return stamp.astimezone(KST) if stamp.tzinfo else None
    except (ValueError,OverflowError):return None


def finite_positive(v):
    if isinstance(v,bool):return None
    try:
        n=float(v)
        return n if math.isfinite(n) and n>0 else None
    except (ValueError,TypeError,OverflowError):return None


def normalized_frame(frame,strategy):
    if strategy=="gmo":return "gmo"
    if strategy=="close":return "close"
    s=str(frame or "").lower()
    return {"1h":"60m","60min":"60m","1d":"day","daily":"day",
            "d":"day","5min":"5m","30min":"30m"}.get(s,s)


def deadline(anchor,market,frame):
    """For stocks count the next N KRX TRADING DATES after the sale date.

    Quotes after sale on day zero are accepted; expire after the close of the
    fifth/tenth/fifteenth *following* trading session. For crypto count exact
    consecutive 24-hour days from the sale timestamp.
    """
    sold=parsed_at(anchor)
    if sold is None:raise ValueError("EXIT_TIMESTAMP_UNVERIFIED")
    if market not in ("stock","coin"):raise ValueError("INVALID_MARKET")
    sessions=WINDOWS.get(frame)
    if sessions is None:raise ValueError("UNKNOWN_POST_SALE_FRAME")
    if market=="coin":
        return sold+dt.timedelta(days=sessions)
    from krx_session_v041 import CLOSED,SPECIAL_SESSIONS,END
    next_day=sold.date()
    found=0
    for _ in range(375):
        next_day+=dt.timedelta(days=1)
        if next_day.weekday()>=5:continue
        if next_day.year not in CLOSED:
            raise ValueError("KRX_CALENDAR_NOT_VERIFIED_FOR_YEAR")
        if next_day.isoformat() in CLOSED[next_day.year]:continue
        found+=1
        if found==sessions:
            close=SPECIAL_SESSIONS.get(next_day.isoformat(),(0,END))[1]
            return dt.datetime.combine(next_day,dt.time(close//60,close%60),KST)
    raise ValueError("NO_VERIFIED_TRADING_DEADLINE")


def _safe_name(s):
    s=str(s or "")
    return s[:120]


class Tracker:
    def __init__(self,root):
        self.root=Path(root)
        # Root-level research journal is included in signed ZIP backup.
        self.path=self.root/FILE
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self._lock=threading.RLock()
        self._watch={"stock":set(),"coin":set()}
        self._positions_by_symbol={"stock":defaultdict(tuple),"coin":defaultdict(tuple)}
        self._stop=threading.Event()
        self._thread=None
        self._queue=queue.Queue(maxsize=MAX_QUEUE)
        self._dropped=0
        self._last_scan=0.0
        self._source_signature=None
        self._error=""
        self._last_stock_watch_gap=0
        self._last_coin_watch_gap=0
        self._init()
        self._refresh_watch()

    def _connect(self,read_only=False):
        if read_only:
            con=sqlite3.connect(self.path.resolve().as_uri()+"?mode=ro",
                                uri=True,timeout=4)
        else:con=sqlite3.connect(str(self.path),timeout=8)
        con.row_factory=sqlite3.Row
        con.execute("PRAGMA busy_timeout=8000")
        return con

    def _init(self):
        with closing(self._connect()) as con:
            con.executescript("""PRAGMA journal_mode=WAL;
             CREATE TABLE IF NOT EXISTS cases(
               case_id TEXT PRIMARY KEY, market TEXT NOT NULL,
               strategy TEXT NOT NULL, frame TEXT NOT NULL, symbol TEXT NOT NULL,
               cohort TEXT NOT NULL, execution_mode TEXT NOT NULL,
               exit_evidence TEXT NOT NULL, source_ref TEXT NOT NULL,
               entry_price REAL NOT NULL, exit_ts INTEGER NOT NULL,
               until_ts INTEGER, target_price REAL, status TEXT NOT NULL,
               peak REAL, peak_at INTEGER, peak_pct REAL,
               trough REAL, trough_at INTEGER, trough_pct REAL,
               last_at INTEGER, sample_count INTEGER NOT NULL DEFAULT 0,
               target_hit_at INTEGER, notes TEXT NOT NULL DEFAULT '',
               updated_at INTEGER NOT NULL);
             CREATE INDEX IF NOT EXISTS case_watch
               ON cases(market,symbol,status,exit_ts,until_ts);
             CREATE INDEX IF NOT EXISTS case_source
               ON cases(market,strategy,cohort,source_ref,exit_evidence);
             CREATE TABLE IF NOT EXISTS minute_path(
               case_id TEXT NOT NULL,source TEXT NOT NULL,bucket_epoch INTEGER NOT NULL,
               first_at INTEGER NOT NULL,last_at INTEGER NOT NULL,
               high REAL NOT NULL,high_at INTEGER NOT NULL,
               low REAL NOT NULL,low_at INTEGER NOT NULL,
               high_from_buy_pct REAL NOT NULL,low_from_buy_pct REAL NOT NULL,
               samples INTEGER NOT NULL,
               PRIMARY KEY(case_id,source,bucket_epoch));
             CREATE TABLE IF NOT EXISTS high_history(
               case_id TEXT NOT NULL,high_at INTEGER NOT NULL,price REAL NOT NULL,
               pct_from_buy REAL NOT NULL,source TEXT NOT NULL,
               PRIMARY KEY(case_id,high_at,price));
             CREATE TABLE IF NOT EXISTS errors(
               at INTEGER NOT NULL,scope TEXT NOT NULL,reason TEXT NOT NULL);
             """)
            con.commit()

    def register(self,market,strategy,frame,symbol,cohort,
                 entry_price,exit_at,source_ref,execution_mode="UNKNOWN",
                 exit_evidence="CONFIRMED",target_price=None,notes=""):
        """Deterministically register only a recorded sale or explicit forecast.

        LIVE/PAPER broker mode and ACTUAL/PAPER research cohort are separate.
        No source data is ever changed by this method.
        """
        market=str(market).lower()
        strategy=str(strategy).lower()
        frame=normalized_frame(frame,strategy)
        cohort=str(cohort).upper()
        symbol=str(symbol or "").strip()
        entry=finite_positive(entry_price)
        anchor=parsed_at(exit_at)
        if market not in ("stock","coin") or frame not in WINDOWS:
            raise ValueError("UNKNOWN_MARKET_STRATEGY_OR_FRAME")
        if cohort not in ("ACTUAL_FILLED","PAPER"):
            raise ValueError("INVALID_RESEARCH_COHORT")
        if not symbol or entry is None or anchor is None or not str(source_ref):
            raise ValueError("MISSING_PRICE_SYMBOL_SOURCE_OR_EXIT")
        if market=="coin" and not symbol.startswith("KRW-"):
            raise ValueError("UNVERIFIED_COIN_SYMBOL")
        if market=="stock" and not symbol.isdigit():
            raise ValueError("UNVERIFIED_STOCK_SYMBOL")
        evidence=str(exit_evidence).upper()
        if evidence not in ("CONFIRMED","EXPECTED"):
            raise ValueError("UNVERIFIED_EXIT_EVIDENCE")
        until=None;status="TRACKING"
        try:until=int(deadline(anchor,market,frame).timestamp())
        except ValueError as e:
            status="CALENDAR_UNVERIFIED" if "CALENDAR" in str(e) else "INVALID_END"
        ep=int(anchor.timestamp())
        ref=_safe_name(source_ref)
        # Distinct EXPECTED/CONFIRMED anchor episodes are not mixed.
        key=f"{market}|{strategy}|{frame}|{symbol}|{cohort}|{ref}|{evidence}|{ep}"
        case_id=hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]
        now=int(time.time())
        if until is not None and now>=until:status="EXPIRED_NO_OBSERVATION"
        with self._lock,closing(self._connect()) as con:
            if evidence=="CONFIRMED":
                con.execute("""UPDATE cases SET status='SUPERSEDED',updated_at=?
                    WHERE market=? AND strategy=? AND symbol=? AND
                    cohort=? AND source_ref=? AND exit_evidence='EXPECTED'
                    AND status!='SUPERSEDED'""",
                    (now,market,strategy,symbol,cohort,ref))
            con.execute("""INSERT OR IGNORE INTO cases(
                case_id,market,strategy,frame,symbol,cohort,execution_mode,
                exit_evidence,source_ref,entry_price,exit_ts,until_ts,
                target_price,status,updated_at,notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (case_id,market,strategy,frame,symbol,cohort,
                 str(execution_mode).upper(),evidence,ref,entry,ep,until,
                 finite_positive(target_price),status,now,_safe_name(notes)))
            con.commit()
        return case_id

    def _refresh_watch(self,now=None):
        instant=int(time.time() if now is None else now)
        with self._lock,closing(self._connect(True)) as con:
            groups={"stock":defaultdict(list),"coin":defaultdict(list)}
            for row in con.execute("""SELECT case_id,market,symbol,exit_ts,until_ts
                FROM cases WHERE status IN ('TRACKING','PENDING_NO_QUOTES')
                  AND exit_ts<=? AND until_ts>? ORDER BY until_ts ASC""",
                  (instant,instant)):
                groups[row["market"]][row["symbol"]].append(
                    (row["case_id"],row["exit_ts"],row["until_ts"]))
            self._positions_by_symbol=groups
            self._watch={m:set(by) for m,by in groups.items()}

    def active_symbols(self,market):
        with self._lock:return set(self._watch.get(market,()))

    def quote(self,market,symbol,price,when,source,received_at=None):
        """Nonblocking inbound mark. No broker or original-ledger interaction."""
        if market not in ("stock","coin"):return False
        value=finite_positive(price)
        stamp=parsed_at(when)
        if value is None or stamp is None:return False
        now=(parsed_at(received_at) or dt.datetime.now(KST))
        # Do not record stale, future, un-timestamped or replayed ticks.
        if abs((now-stamp).total_seconds())>10:return False
        with self._lock:
            if symbol not in self._watch[market]:return False
        try:
            self._queue.put_nowait((market,symbol,value,int(stamp.timestamp()),
                                    _safe_name(source)))
            return True
        except queue.Full:
            self._dropped+=1
            return False

    def record_batch(self,quotes):
        """Synchronous unit-test/replayable ingestion of already validated ticks."""
        with self._lock,closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            total=0
            for market,symbol,price,at,source in quotes:
                value=finite_positive(price)
                stamp=parsed_at(at)
                if value is None or stamp is None:continue
                ts=int(stamp.timestamp())
                for row in con.execute("""SELECT case_id,entry_price,target_price,
                    peak,trough,exit_ts,until_ts,sample_count
                    FROM cases WHERE market=? AND symbol=?
                    AND status IN ('TRACKING','PENDING_NO_QUOTES')
                    AND exit_ts<? AND until_ts>=?""",
                    (market,symbol,ts,ts)).fetchall():
                    entry=float(row["entry_price"])
                    if entry<=0:continue
                    case=row["case_id"]
                    ret=(value/entry-1)*100
                    # Source-minute highs have their real observed *tick* times.
                    bucket=ts//60
                    con.execute("""INSERT INTO minute_path VALUES(
                        ?,?,?,?,?,?,?,?,?,?,?,1)
                        ON CONFLICT(case_id,source,bucket_epoch) DO UPDATE SET
                        first_at=MIN(minute_path.first_at,excluded.first_at),
                        last_at=MAX(minute_path.last_at,excluded.last_at),
                        high=MAX(minute_path.high,excluded.high),
                        high_at=CASE WHEN excluded.high>minute_path.high
                            THEN excluded.high_at ELSE minute_path.high_at END,
                        low=MIN(minute_path.low,excluded.low),
                        low_at=CASE WHEN excluded.low<minute_path.low
                            THEN excluded.low_at ELSE minute_path.low_at END,
                        high_from_buy_pct=MAX(minute_path.high_from_buy_pct,
                            excluded.high_from_buy_pct),
                        low_from_buy_pct=MIN(minute_path.low_from_buy_pct,
                            excluded.low_from_buy_pct),
                        samples=minute_path.samples+1""",
                        (case,source,bucket,ts,ts,value,ts,value,ts,ret,ret))
                    high_new=row["peak"] is None or value>float(row["peak"])
                    low_new=row["trough"] is None or value<float(row["trough"])
                    if high_new:
                        con.execute("""INSERT OR IGNORE INTO high_history VALUES(?,?,?,?,?)""",
                                    (case,ts,value,ret,source))
                    target_hit=(row["target_price"] is not None and
                                value>=float(row["target_price"]))
                    con.execute("""UPDATE cases SET
                      peak=CASE WHEN peak IS NULL OR ? > peak THEN ? ELSE peak END,
                      peak_at=CASE WHEN peak IS NULL OR ? > peak THEN ? ELSE peak_at END,
                      peak_pct=CASE WHEN peak IS NULL OR ? > peak THEN ? ELSE peak_pct END,
                      trough=CASE WHEN trough IS NULL OR ? < trough THEN ? ELSE trough END,
                      trough_at=CASE WHEN trough IS NULL OR ? < trough THEN ? ELSE trough_at END,
                      trough_pct=CASE WHEN trough IS NULL OR ? < trough THEN ? ELSE trough_pct END,
                      target_hit_at=CASE WHEN target_hit_at IS NULL AND ?
                        THEN ? ELSE target_hit_at END,
                      last_at=CASE WHEN last_at IS NULL OR ? > last_at
                        THEN ? ELSE last_at END,
                      sample_count=sample_count+1,status='TRACKING',
                      updated_at=? WHERE case_id=?""",
                      (value,value,value,ts,value,ret,
                       value,value,value,ts,value,ret,
                       int(target_hit),ts,ts,ts,int(time.time()),case))
                    total+=1
            con.commit()
        return total

    def _source_signature_now(self):
        # Re-reading every candidate and order in every SQLite on a 20-second
        # poll adds load without new evidence. WAL is part of the signature.
        paths=("stock/ARTITRADE_D.sqlite3",
               "stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
               "stock/close/AT_CLOSE_PAPER.sqlite3",
               "coin/base/upbit_paper.sqlite3",
               "coin/ma_epoch_v062/upbit_paper.sqlite3",
               "stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3",
               "coin/ma/upbit_paper.sqlite3",
               "stock/PROJECTGMO_V1.0_account_state.json",
               "stock/PROJECTGMO_V1.0_research.sqlite3",
               "market_trend_v043.sqlite3")
        result=[]
        for relative in paths:
            source=self.root/relative
            for tail in ("","-wal"):
                file=Path(str(source)+tail)
                try:
                    info=file.stat()
                    result.append((relative+tail,info.st_size,info.st_mtime_ns))
                except OSError:
                    result.append((relative+tail,None,None))
        return tuple(result)

    def scan_sources(self,now=None):
        from post_exit_sources_v053 import scan
        instant=int(time.time() if now is None else now)
        signature=self._source_signature_now()
        changed=signature!=self._source_signature
        scans,problems=scan(self.root,instant) if changed else ([],[])
        if changed and not problems:self._source_signature=signature
        added=0
        # Historical pre-v053 closes are preserved in the original signed
        # ledgers; there are no recorded post-sale ticks to backfill blindly.
        # Only the recent 50 calendar days can have an active 15-session
        # observation window, even accounting for long holidays.
        recent=instant-50*86400
        for item in scans:
            stamp=parsed_at(item.get("exit_at"))
            if stamp is None or stamp.timestamp()<recent:continue
            try:self.register(**item);added+=1
            except (ValueError,TypeError,sqlite3.Error) as e:
                problems.append("REGISTER:"+type(e).__name__+":"+str(e)[:100])
        with self._lock,closing(self._connect()) as con:
            con.execute("""UPDATE cases SET status=CASE
              WHEN sample_count>0 THEN 'COMPLETE' ELSE 'ENDED_NO_OBSERVATION' END,
              updated_at=? WHERE status IN ('TRACKING','PENDING_NO_QUOTES')
              AND until_ts IS NOT NULL AND until_ts<=?""",
              (instant,instant))
            for error in problems[:25]:
                con.execute("INSERT INTO errors VALUES(?,?,?)",
                            (instant,"SOURCE_SCAN",str(error)[:180]))
            con.commit()
            self._last_scan=time.monotonic()
            self._error="; ".join(problems[:4])
        self._refresh_watch(instant)
        return {"cases_seen":added,"sources_changed":changed,
                "problems":problems[:12]}

    def _run(self):
        # Separate research-only worker never waits on a brokerage order.
        while not self._stop.is_set():
            if time.monotonic()-self._last_scan>=20:
                try:self.scan_sources()
                except Exception as e:self._error="SCAN:"+type(e).__name__+":"+str(e)[:100]
            events=[]
            try:events.append(self._queue.get(timeout=.6))
            except queue.Empty:continue
            while len(events)<250:
                try:events.append(self._queue.get_nowait())
                except queue.Empty:break
            try:self.record_batch(events)
            except Exception as e:self._error="OBSERVE:"+type(e).__name__+":"+str(e)[:100]
            finally:
                for _ in events:self._queue.task_done()

    def start(self):
        if self._thread and self._thread.is_alive():return
        self.scan_sources()
        self._stop.clear()
        self._thread=threading.Thread(target=self._run,
            name="artitrade-v053-postsale-research",daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def summary(self,market=None,strategy=None,limit=16):
        where=[];args=[]
        if market in ("stock","coin"):
            where.append("market=?");args.append(market)
        if strategy in ("at","ma","trend","gmo","close","general"):
            where.append("strategy=?");args.append(strategy)
        clause=(" WHERE "+" AND ".join(where)) if where else ""
        with self._lock,closing(self._connect(True)) as con:
            counts=[dict(x) for x in con.execute("""SELECT market,strategy,frame,cohort,
                 status,COUNT(*) n FROM cases"""+clause+
                 " GROUP BY market,strategy,frame,cohort,status",tuple(args))]
            entries=[dict(x) for x in con.execute(
                """SELECT case_id,market,strategy,frame,symbol,cohort,
                    execution_mode,exit_evidence,entry_price,exit_ts,until_ts,
                    peak,peak_at,peak_pct,trough,trough_at,trough_pct,
                    sample_count,target_price,target_hit_at,status,source_ref
                    FROM cases"""+clause+
                    " ORDER BY exit_ts DESC LIMIT ?",tuple(args)+(int(limit),))]
            recent=[]
            for e in entries[:min(6,limit)]:
                recent.append({"case_id":e["case_id"],
                    "peak_updates":[dict(x) for x in con.execute(
                      "SELECT high_at,price,pct_from_buy,source FROM high_history WHERE case_id=? ORDER BY high_at DESC LIMIT 10",
                      (e["case_id"],))]})
            error_count=con.execute("SELECT COUNT(*) FROM errors").fetchone()[0]
        return {"ok":True,"schema":SCHEMA,"policy":"V053_REPLACES_OLD_FOLLOWUP",
            "stock_days":"TRADING_DATES_AFTER_SELL",
            "coin_days":"CALENDAR_24H_AFTER_SELL",
            "days_by_frame":dict(WINDOWS),"cohorts":counts,
            "cases":entries,"high_history_preview":recent,
            "total_cases":sum(x["n"] for x in counts),
            "dropped_ticks":self._dropped,
            "unsubscribed_stock_tracking_symbols":self._last_stock_watch_gap,
            "unsubscribed_coin_tracking_symbols":self._last_coin_watch_gap,
            "errors_in_journal":error_count,
            "last_error":self._error,
            "raw_research_db":self.path.relative_to(self.root).as_posix(),
            "original_trade_databases_untouched":True,
            "sell_timestamp_required":True,
            "confirmed_vs_estimated_exit_separate":True,
            "only_observed_public_prices":True}


def _ma_v062_post_exit_report(root,market,limit):
    """Read only post-reset MA exits; old source tracker remains intact."""
    from ma_epoch_v062 import epoch_start
    stamp=epoch_start(root,market)
    if not stamp:
        return {"ok":False,"schema":SCHEMA,"reason":"MA_NEW_EPOCH_NOT_STARTED"}
    cutoff=int(dt.datetime.fromisoformat(stamp).timestamp())
    path=Path(root)/FILE
    if not path.is_file():
        return {"ok":True,"schema":SCHEMA,"cohorts":[],"cases":[],
                "total_cases":0,"ma_epoch_started_at":stamp,
                "old_ma_post_exit_excluded":True}
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                 uri=True,timeout=4)) as con:
        con.row_factory=sqlite3.Row
        rows=[dict(x) for x in con.execute("""
          SELECT case_id,market,strategy,frame,symbol,cohort,
            execution_mode,exit_evidence,entry_price,exit_ts,until_ts,
            peak,peak_at,peak_pct,trough,trough_at,trough_pct,
            sample_count,target_price,target_hit_at,status,source_ref
          FROM cases WHERE market=? AND strategy='ma' AND exit_ts>=?
          ORDER BY exit_ts DESC LIMIT ?""",(market,cutoff,int(limit)))]
        counts=[dict(x) for x in con.execute("""
          SELECT market,strategy,frame,cohort,status,COUNT(*) n
          FROM cases WHERE market=? AND strategy='ma' AND exit_ts>=?
          GROUP BY market,strategy,frame,cohort,status""",
          (market,cutoff))]
    return {"ok":True,"schema":SCHEMA,"cohorts":counts,"cases":rows,
            "total_cases":sum(int(x["n"]) for x in counts),
            "ma_epoch_started_at":stamp,"old_ma_post_exit_excluded":True,
            "raw_research_db":FILE,"original_trade_databases_untouched":True}


def report(root,market=None,strategy=None,limit=16):
    if strategy=="ma" and market in ("coin","stock"):
        return _ma_v062_post_exit_report(root,market,limit)
    path=Path(root)/FILE
    if not path.is_file():
        return {"ok":False,"schema":SCHEMA,"reason":"TRACKER_NOT_STARTED"}
    return Tracker(root).summary(market,strategy,limit)
