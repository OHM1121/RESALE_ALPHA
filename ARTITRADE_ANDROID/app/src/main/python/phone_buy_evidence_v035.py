"""Mobile-origin BUY evidence from exact strategy-ledger BUY execution rows.

This does not change orders or the strategy SQLite. It joins each open
position to its verified BUY execution by *position ID*, then optionally
adds the exact candidate/signal evidence. An independent phone-only journal
stores immutable provenance even when the display snapshot is regenerated.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import queue
from pathlib import Path

_ALLOWED={"stock":{"at","ma","close","gmo"},
          "coin":{"base","ma"}}


def _value(row,*names):
    if not isinstance(row,dict):return None
    for name in names:
        value=row.get(name)
        if value is not None and str(value).strip() not in ("","null","None"):
            return value
    return None


def _identity(row,market):
    return str(_value(row,("market" if market=="coin" else "code"),
                         ("code" if market=="coin" else "market")) or "").strip().upper()


def _cid(value):
    if value is None:return ""
    return str(value).strip()


def _timestamp(row):
    return str(_value(row,"at","ts","time","buy_at","entry_at","entry_ts") or "")


def _candidate_reason(candidate):
    raw=_value(candidate,"reason","reason_json","entry_reason","signal_reason")
    if raw:return str(raw).strip()
    for key in ("features","feature_json"):
        features=candidate.get(key)
        if isinstance(features,str):
            try:features=json.loads(features)
            except (ValueError,TypeError):features=None
        if isinstance(features,dict):
            direct=_value(features,"reason","signal_reason",
                         "selection_reason","entry_reason","qualification_reason")
            if direct:return str(direct).strip()
    return ""


def attach_buy_evidence(positions,executions,candidates=(),*,market,strategy,
                        linked_ids=None):
    """Mutate only VIEW dictionaries, never source SQLite or execution records.

    Strict original BUY position_id is required; no matching by ticker alone,
    no made-up reference candle treated as a fill. At most one initial BUY
    provenance per position. Market+strategy namespaces prevent ID collisions.
    """
    if strategy not in _ALLOWED.get(market,set()):
        raise ValueError("Invalid phone research market/strategy")
    linked_ids=linked_ids or {}
    by_pos={}
    for e in executions or ():
        if not isinstance(e,dict) or str(e.get("side") or "").upper()!="BUY":
            continue
        pid=_cid(e.get("position_id"))
        when=_timestamp(e)
        reason=_value(e,"reason","fill_reason","execution_reason")
        if not pid or not when or not reason:
            continue
        by_pos.setdefault(pid,[]).append(e)
    by_cid={_cid(x.get("id")):x for x in candidates or ()
            if isinstance(x,dict) and x.get("id") is not None}
    by_ref={}
    for x in candidates or ():
        if not isinstance(x,dict):continue
        tf=str(_value(x,"tf") or "")
        symbol=_identity(x,market)
        ref=str(_value(x,"ref_at","ref_ts","ts") or "")
        if symbol and ref:
            by_ref.setdefault((tf,symbol,ref),[]).append(x)
    result=[]
    for position in positions or ():
        if not isinstance(position,dict):continue
        pid=_cid(position.get("id"))
        if not pid:continue
        symbol=_identity(position,market)
        tf=str(position.get("tf") or "")
        if not symbol:continue
        valid=[]
        for e in by_pos.get(pid,[]):
            esymbol=_identity(e,market)
            etf=str(e.get("tf") or "")
            if esymbol and esymbol!=symbol:continue
            if etf and tf and tf!=etf:continue
            valid.append(e)
        if not valid:continue
        first=min(valid,key=lambda e:(_timestamp(e),_cid(e.get("id"))))
        at=_timestamp(first)
        raw=str(_value(first,"reason","fill_reason","execution_reason") or "")
        if not raw:continue
        candidate_id=_cid(_value(position,"candidate_id","signal_id") or
                          linked_ids.get(pid))
        candidate=by_cid.get(candidate_id)
        if candidate is not None:
            candidate_symbol=_identity(candidate,market)
            candidate_tf=str(candidate.get("tf") or "")
            if (candidate_symbol and candidate_symbol!=symbol) or (
                    candidate_tf and tf and candidate_tf!=tf):
                candidate=None
        if candidate is None:
            ref=str(_value(position,"ref_at","ref_ts","original_ref_ts") or "")
            hits=by_ref.get((tf,symbol,ref),[])
            if len(hits)==1:
                candidate=hits[0]
        original_signal=_candidate_reason(candidate) if candidate else ""
        # Never replace a written reason by a later BUY, selection change or
        # a different PC-imported position with the same symbol.
        if not _value(position,"buy_reason"):
            position["buy_reason"]=raw
        if not _value(position,"buy_at","entry_at","entry_ts"):
            position["buy_at"]=at
        position["buy_reason_at"]=at
        position["buy_reason_source"]="ORIGINAL_BUY_EXECUTION"
        if original_signal and not _value(position,"buy_signal_reason"):
            position["buy_signal_reason"]=original_signal
        if candidate and candidate.get("score") is not None:
            position.setdefault("entry_signal_score",candidate["score"])
        result.append({"market":market,"strategy":strategy,"position_id":pid,
            "code":symbol,"tf":tf,"buy_at":at,"buy_reason":raw,
            "buy_signal_reason":original_signal,"source":"ORIGINAL_BUY_EXECUTION"})
    return result


def read_open_signal_evidence(path,market,strategy):
    """Lookup each still-open BUY signal with a fixed read-only indexed JOIN.

    The app previously fetched the newest 12,000 irrelevant candidate rows
    every hub refresh, potentially hiding an old open position's original
    reason and causing UI lag. Every query is anchored to a held position.
    """
    queries={
        ("stock","at"):"""SELECT s.id,s.tf,s.code,s.ref_ts,s.features,
                                   s.score,s.decision
                 FROM positions AS p
                 JOIN v19_members AS m ON m.position_id=p.id
                 JOIN signals AS s ON s.id=m.signal_id
                 WHERE p.status='OPEN' ORDER BY p.id DESC""",
        ("stock","ma"):"""SELECT c.id,c.tf,c.code,c.ref_ts,c.features,c.score
                 FROM positions AS p
                 JOIN candidates AS c ON c.id=p.candidate_id
                 WHERE p.status='OPEN' ORDER BY p.id DESC""",
        ("stock","close"):"""SELECT s.id,s.strategy,s.code,s.ts AS ref_ts,
                                   s.reason_json AS reason,
                                   s.feature_json AS features
                 FROM positions AS p
                 JOIN signals AS s ON s.id=p.signal_id
                 WHERE p.status='OPEN' ORDER BY p.id DESC""",
        ("coin","base"):"""SELECT c.id,c.tf,c.market,c.ref_at,c.reason,c.score,c.features
                 FROM positions AS p
                 JOIN candidates AS c ON c.tf=p.tf AND c.market=p.market
                    AND c.ref_at=p.ref_at
                 WHERE p.status='OPEN' ORDER BY p.id DESC""",
        ("coin","ma"):"""SELECT c.id,c.tf,c.market,c.ref_at,c.reason,c.score,c.features
                 FROM positions AS p
                 JOIN candidates AS c ON c.tf=p.tf AND c.market=p.market
                    AND c.ref_at=p.ref_at
                 WHERE p.status='OPEN' ORDER BY p.id DESC"""
    }
    query=queries.get((market,strategy))
    if query is None:raise ValueError("Unknown strategy evidence source")
    db=Path(path)
    if not db.is_file():return []
    with sqlite3.connect(db.resolve().as_uri()+"?mode=ro",uri=True,timeout=4) as c:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        return [dict(r) for r in c.execute(query)]


def read_at_open_buy_fills(path):
    """Read only BUY executions attached to currently OPEN original AT positions.

    Unlike the 350-fill UI screen limit, this preserves an old open holding's
    real saved BUY reason even after thousands of later executions.
    """
    db=Path(path)
    if not db.is_file():return []
    with sqlite3.connect(db.resolve().as_uri()+"?mode=ro",uri=True,timeout=4) as c:
        c.row_factory=sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        return [dict(row) for row in c.execute(
            """SELECT e.* FROM executions AS e
               JOIN positions AS p ON p.id=e.position_id
               WHERE p.status='OPEN' AND e.side='BUY'
               ORDER BY e.id DESC""")]


def attach_gmo_buy_evidence(positions,executions,entry_dates):
    """GMO phone holdings use shared id=-1. Match strictly code + BUY day.

    No other symbol's or later re-buy's paper fills may be presented as this
    position's purchase decision. Original GMO JSON buy_fills are durable.
    """
    out=[]
    for position in positions or ():
        if not isinstance(position,dict):continue
        code=str(position.get("code") or "").strip()
        day=str((entry_dates or {}).get(code) or "")[:10]
        if len(day)==8 and day.isdigit():
            day=day[:4]+"-"+day[4:6]+"-"+day[6:8]
        if not code or len(day)!=10 or day[4]!='-':
            continue
        buys=[]
        for e in executions or ():
            if not isinstance(e,dict):continue
            ts=_timestamp(e)
            if (str(e.get("code") or "")!=code or
                str(e.get("side") or "").upper()!="BUY" or
                ts[:10]!=day or not _value(e,"reason","purpose")):
                continue
            buys.append(e)
        if not buys:continue
        first=min(buys,key=lambda e:_timestamp(e))
        when=_timestamp(first)
        reason=str(_value(first,"reason","purpose"))
        if not position.get("buy_reason"):
            position["buy_reason"]=reason
        position["buy_reason_source"]="ORIGINAL_GMO_PHONE_BUY_FILL"
        position["buy_reason_at"]=when
        position.setdefault("entry_at",when)
        out.append({"market":"stock","strategy":"gmo",
                    "position_id":code+":"+day,"code":code,"tf":"gmo",
                    "buy_at":when,"buy_reason":reason,"buy_signal_reason":"",
                    "source":"ORIGINAL_GMO_PHONE_BUY_FILL"})
    return out


def store_buy_evidence(root,events):
    """Append-only phone journal; never write PC/EXE or trading-state databases."""
    valid=[x for x in events or [] if isinstance(x,dict) and
        x.get("strategy") in _ALLOWED.get(x.get("market"),set()) and
        all(x.get(k) not in (None,"") for k in
            ("position_id","code","buy_at","buy_reason"))]
    if not valid:return 0
    root=Path(root)
    root.mkdir(parents=True,exist_ok=True)
    db=root/"phone_buy_evidence_v035.sqlite3"
    with sqlite3.connect(db,timeout=5) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS buy_evidence(
            market TEXT NOT NULL,strategy TEXT NOT NULL,position_id TEXT NOT NULL,
            code TEXT NOT NULL,tf TEXT NOT NULL,buy_at TEXT NOT NULL,
            buy_reason TEXT NOT NULL,buy_signal_reason TEXT NOT NULL,
            evidence_source TEXT NOT NULL,
            PRIMARY KEY(market,strategy,position_id,buy_at))""")
        count=0
        for e in valid:
            cur=c.execute("""INSERT OR IGNORE INTO buy_evidence VALUES(?,?,?,?,?,?,?,?,?)""",
                (str(e["market"]),str(e["strategy"]),str(e["position_id"]),
                 str(e["code"]),str(e.get("tf") or ""),
                 str(e["buy_at"]),str(e["buy_reason"]),
                 str(e.get("buy_signal_reason") or ""),
                 str(e.get("source") or "ORIGINAL_BUY_EXECUTION")))
            count+=cur.rowcount
        return count


_QUEUE=queue.Queue(maxsize=128)
_RUNNING=False
_START_LOCK=threading.Lock()
_QUEUED=set()


def _persist_worker():
    while True:
        root,event,key=_QUEUE.get()
        try:
            store_buy_evidence(root,[event])
        except Exception:
            # Original strategy executions remain authoritative and will be
            # retried on a later hub snapshot. No broker/order exception.
            with _START_LOCK:
                _QUEUED.discard(key)
        finally:
            _QUEUE.task_done()


def queue_phone_buy_evidence(root,events):
    """Nonblocking app-view snapshot notification; original BUY ledger is durable."""
    global _RUNNING
    if not events:return 0
    accepted=0
    with _START_LOCK:
        if not _RUNNING:
            threading.Thread(target=_persist_worker,daemon=True,
                name="artitrade-buy-reason-sidecar").start()
            _RUNNING=True
        for e in events:
            key=(str(Path(root)),str(e["market"]),str(e["strategy"]),
                 str(e["position_id"]),str(e["buy_at"]))
            if key in _QUEUED:continue
            try:_QUEUE.put_nowait((root,e,key))
            except queue.Full:break
            _QUEUED.add(key)
            accepted+=1
    return accepted
