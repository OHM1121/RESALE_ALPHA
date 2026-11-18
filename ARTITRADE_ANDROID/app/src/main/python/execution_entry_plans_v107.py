"""Append-only v107 verified candidate/entry plans for 8 independent strategies.

This is evidence only, never an order. An unavailable exit target is NULL,
not fabricated from an entry limit, live ask or unfinished candle.
Separate SQLite WAL journal is included in the fresh reset and full ZIPs.
"""
from __future__ import annotations
import datetime as dt
import json
from pathlib import Path
import sqlite3

NAME="execution_entry_plans_v107.sqlite3"
SCHEMA="ARTITRADE_ENTRY_EVIDENCE_V107"
_VALID_MARKETS=("stock","coin")
_VALID_STRATEGIES={
    "stock":("at","ma","close","gmo","trend"),
    "coin":("general","ma","trend")}
_READY=False

def _clean(v,max_len=1000):
    if v is None:return None
    return str(v)[:max_len]

def _original_row(row):
    if row is None:return {}
    try:
        data=dict(row)
    except (TypeError,ValueError):
        return {}
    return {str(k):v for k,v in data.items()
            if isinstance(v,(str,int,float,bool,type(None)))}

def record_plan(root,market,strategy,frame,symbol,reference,*,reason,
                candidate_target=None,exit_target=None,target_source=None,
                explanation=None,phase="CANDIDATE",extra=None):
    """Persist verified pre-entry reason with unknown targets explicitly NULL.

    Never contains a private API key/order credential, hypothetical fill,
    future candle, or invented target. Repeated scans update the SAME key.
    """
    if market not in _VALID_MARKETS or strategy not in _VALID_STRATEGIES[market]:
        raise ValueError("unknown strategy scope")
    if not frame or not symbol or not reference or not reason:
        raise ValueError("required candidate identity/evidence missing")
    base=Path(root).resolve()
    if not base.is_dir():return False
    path=base/NAME
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    payload=json.dumps(extra if isinstance(extra,dict) else {},
                       ensure_ascii=False,sort_keys=True,default=str)[:16000]
    with sqlite3.connect(str(path),timeout=0.3) as c:
        c.execute("PRAGMA busy_timeout=300")
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("""CREATE TABLE IF NOT EXISTS entry_plans (
             id INTEGER PRIMARY KEY,
             market TEXT NOT NULL,strategy TEXT NOT NULL,
             frame TEXT NOT NULL,symbol TEXT NOT NULL,
             reference TEXT NOT NULL,phase TEXT NOT NULL,
             buy_reason TEXT NOT NULL,candidate_target TEXT,
             planned_exit_target TEXT,exit_target_source TEXT,
             explanation TEXT,evidence_json TEXT NOT NULL,
             created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
             UNIQUE(market,strategy,frame,symbol,reference,phase))""")
        c.execute("""INSERT INTO entry_plans (
             market,strategy,frame,symbol,reference,phase,buy_reason,
             candidate_target,planned_exit_target,exit_target_source,
             explanation,evidence_json,created_at,updated_at)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(market,strategy,frame,symbol,reference,phase)
             DO UPDATE SET
               buy_reason=excluded.buy_reason,
               candidate_target=COALESCE(excluded.candidate_target,entry_plans.candidate_target),
               planned_exit_target=COALESCE(excluded.planned_exit_target,entry_plans.planned_exit_target),
               exit_target_source=COALESCE(excluded.exit_target_source,entry_plans.exit_target_source),
               explanation=COALESCE(excluded.explanation,entry_plans.explanation),
               evidence_json=excluded.evidence_json,
               updated_at=excluded.updated_at""",
              (market,strategy,_clean(frame),_clean(symbol),_clean(reference),
               _clean(phase),_clean(reason),_clean(candidate_target),
               _clean(exit_target),_clean(target_source),
               _clean(explanation),payload,now,now))
    return True

def capture_candidate(root,market,strategy,reference,candidate,
                      *,reason,phase="CANDIDATE"):
    row=_original_row(candidate)
    if not row:return False
    frame=row.get("tf") or row.get("frame")
    symbol=row.get("code") or row.get("market") or row.get("symbol")
    if not frame or not symbol:return False
    return record_plan(root,market,strategy,frame,symbol,reference,
                       reason=reason,phase=phase,
                       candidate_target=row.get("target") or row.get("first_target"),
                       target_source="ORIGINAL_VERIFIED_CANDIDATE_TARGET"
                           if row.get("target") or row.get("first_target") else None,
                       extra={"candidate":row,"no_fill_inferred":True})
