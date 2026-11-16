"""Append-only observation history for the six EXE strategies on Android.

The embedded original EXE engines remain authoritative. This module observes
their SQLite and GMO JSON writes without changing trades, strategy criteria,
orders, or source records. Repeated *changes* to a row become new events, so
past real/virtual research evidence is retained even when the source UPDATEs.
All raw source databases are separately preserved in the phone ZIP export.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo("Asia/Seoul")
AUDIT_REL="stock/ANDROID_RESEARCH_OBSERVATIONS.sqlite3"
SOURCES={
    "stock/ARTITRADE_D.sqlite3":"stock_at",
    "stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3":"stock_ma",
    "stock/close/AT_CLOSE_PAPER.sqlite3":"stock_close",
    "stock/PROJECTGMO_V1.0_research.sqlite3":"stock_gmo",
    "coin/base/upbit_paper.sqlite3":"coin_general",
    "coin/ma/upbit_paper.sqlite3":"coin_ma",
}
GMO_STATE="stock/PROJECTGMO_V1.0_account_state.json"
_ALLOWED_TABLE_PARTS=(
    "candidate","research","shadow","signal","execution","fill","trade",
    "position","order","milestone","cohort","forward","flow","issues",
    "open","scan","event","history","wallet","account","book_path",
    "gmo_","policy_restoration"
)

def _category(name):
    t=name.lower()
    if "shadow" in t or "forward" in t or "milestone" in t or "book_path" in t:
        return "VIRTUAL"
    if t in ("executions","fills","positions","orders","wallets","accounts",
             "gmo_fills","gmo_position_history") or "execution" in t:
        return "ACTUAL"
    return "SIGNAL_AND_RESEARCH"

def _fingerprint(payload):
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def _key(row, index):
    for field in ("id","signal_id","candidate_id","execution_id","order_id"):
        if row.get(field) is not None:
            return field+":"+str(row[field])
    subset={x:row[x] for x in (
        "code","symbol","market","tf","event_date","trade_date",
        "day_index","ts","time","at","side","state") if row.get(x) is not None}
    return json.dumps(subset,ensure_ascii=False,sort_keys=True) if subset else "row:"+str(index)

def _init(db):
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("""CREATE TABLE IF NOT EXISTS observations(
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        observed_at_kst TEXT NOT NULL,
        strategy TEXT NOT NULL,
        source_file TEXT NOT NULL,
        source_table TEXT NOT NULL,
        record_kind TEXT NOT NULL,
        record_key TEXT NOT NULL,
        raw_json TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        UNIQUE(source_file,source_table,record_key,source_sha256)
    )""")
    db.execute("""CREATE INDEX IF NOT EXISTS ix_observation_strategy
        ON observations(strategy,record_kind,sequence)""")
    db.execute("""CREATE INDEX IF NOT EXISTS ix_observation_source
        ON observations(source_file,source_table,record_key,sequence)""")

def capture(root_path):
    root=Path(root_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError("ARTITRADE runtime account directory missing")
    journal=root/AUDIT_REL
    journal.parent.mkdir(parents=True,exist_ok=True)
    inserted=0
    failures=[]
    stamp=datetime.now(KST).isoformat(timespec="seconds")
    with closing(sqlite3.connect(journal,timeout=15)) as output:
        _init(output)
        for rel,strategy in SOURCES.items():
            source=root/rel
            if not source.is_file():
                continue
            try:
                with closing(sqlite3.connect(source.resolve().as_uri()+"?mode=ro",
                                             uri=True,timeout=4)) as incoming:
                    incoming.row_factory=sqlite3.Row
                    tables=[r[0] for r in incoming.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                    for table in tables:
                        if not any(part in table.lower() for part in _ALLOWED_TABLE_PARTS):
                            continue
                        escaped=table.replace('"','""')
                        cursor=incoming.execute('SELECT * FROM "'+escaped+'"')
                        index=0
                        while True:
                            chunk=cursor.fetchmany(256)
                            if not chunk:
                                break
                            for record in chunk:
                                index+=1
                                raw={key:record[key] for key in record.keys()}
                                # Some EXE feature BLOBs are bytes; keep bytes losslessly as hex.
                                for key,value in raw.items():
                                    if isinstance(value,bytes):
                                        raw[key]={"encoding":"hex","data":value.hex()}
                                payload=json.dumps(raw,ensure_ascii=False,sort_keys=True,
                                                   separators=(",",":"),default=str)
                                digest=_fingerprint(payload)
                                rowkey=_key(raw,index)
                                cur=output.execute("""INSERT OR IGNORE INTO observations(
                                    observed_at_kst,strategy,source_file,source_table,
                                    record_kind,record_key,raw_json,source_sha256)
                                    VALUES(?,?,?,?,?,?,?,?)""",
                                    (stamp,strategy,rel,table,_category(table),
                                     rowkey,payload,digest))
                                inserted+=max(0,cur.rowcount)
                        output.commit()
            except (sqlite3.Error,ValueError,OSError) as error:
                failures.append({"source":rel,"error":type(error).__name__})
        state=root/GMO_STATE
        if state.is_file():
            try:
                with state.open("r",encoding="utf-8") as stream:
                    original=json.load(stream)
                if not isinstance(original,dict):
                    raise ValueError("GMO state root invalid")
                # Record each buy/sell fill individually; state updates remain append-only.
                for key in ("buy_fills","fills"):
                    for index,record in enumerate(original.get(key) or []):
                        if not isinstance(record,dict):
                            continue
                        payload=json.dumps(record,ensure_ascii=False,sort_keys=True,
                                           separators=(",",":"),default=str)
                        record_key=_key(record,index)
                        cur=output.execute("""INSERT OR IGNORE INTO observations(
                            observed_at_kst,strategy,source_file,source_table,
                            record_kind,record_key,raw_json,source_sha256)
                            VALUES(?,?,?,?,?,?,?,?)""",
                            (stamp,"stock_gmo",GMO_STATE,key,"ACTUAL",
                             record_key,payload,_fingerprint(payload)))
                        inserted+=max(0,cur.rowcount)
                for code,record in (original.get("positions") or {}).items():
                    payload=json.dumps(record,ensure_ascii=False,sort_keys=True,
                                       separators=(",",":"),default=str)
                    cur=output.execute("""INSERT OR IGNORE INTO observations(
                        observed_at_kst,strategy,source_file,source_table,
                        record_kind,record_key,raw_json,source_sha256)
                        VALUES(?,?,?,?,?,?,?,?)""",
                        (stamp,"stock_gmo",GMO_STATE,"positions","ACTUAL",
                         str(code),payload,_fingerprint(payload)))
                    inserted+=max(0,cur.rowcount)
                output.commit()
            except (OSError,ValueError,TypeError,sqlite3.Error) as error:
                failures.append({"source":GMO_STATE,"error":type(error).__name__})
    return {"ok":not failures,"added_observations":inserted,
            "source_errors":failures,"journal":AUDIT_REL}
