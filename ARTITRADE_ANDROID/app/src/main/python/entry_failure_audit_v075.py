"""Read-only, async GMO/CW no-entry explanation for the existing error-history UI.

Both frozen strategy engines and their SQLite/JSON order ledgers remain unchanged.
An ordinary rule rejection is never mislabeled as a broker order or a code crash.
The full original DB rows remain in the user's existing ZIP export.
"""
from __future__ import annotations
import datetime as dt
import json
import sqlite3
import threading
import time
from pathlib import Path

KST=dt.timezone(dt.timedelta(hours=9))
_REFRESH_SECONDS=15.0
_LOCK=threading.RLock()
_JOBS={}
_MAX_ITEMS=1600


def _read_rows(db,sql,args=()):
    if not db.is_file():return []
    try:
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=.7) as c:
            c.row_factory=sqlite3.Row
            return [dict(row) for row in c.execute(sql,args)]
    except (sqlite3.Error,OSError,ValueError):
        return []


def _decode(value):
    if not value:return ""
    try:
        decoded=json.loads(value) if isinstance(value,str) else value
        if isinstance(decoded,list):return ', '.join(str(v) for v in decoded)[:650]
        if isinstance(decoded,dict):
            reason=decoded.get('reasons',decoded.get('reason',decoded))
            if isinstance(reason,list):return ', '.join(str(x) for x in reason)[:650]
            if isinstance(reason,str):return reason[:650]
            return json.dumps(reason,ensure_ascii=False,default=str)[:650]
        return str(decoded)[:650]
    except (ValueError,TypeError):
        return str(value)[:650]


def _row(at,code,stage,reason,status,source,*,name="",frame="",detail=""):
    stamp=str(at or "")
    # No invented timestamps. Source row's date is retained unchanged.
    return {'at':stamp,'ts':stamp,'code':str(code or ''),
            'symbol':str(code or ''),'name':str(name or ''),
            'stage':str(stage),'message':str(reason or '')[:700],
            'detail':str(detail or '')[:700],'status':str(status),
            'source':str(source),'frame':str(frame),
            'market':'stock','confirmed_order':False,
            'audit_only':True}


def _gmo(root):
    home=Path(root)/'stock'
    db=home/'PROJECTGMO_V1.0_research.sqlite3'
    result=[]
    # A COMPLETE scan can still mean NO_3PARTY_CANDIDATE or a
    # well-formed but rejected full selector; preserve the exact reason.
    for row in _read_rows(db,"""SELECT trade_date,code,status,detail,checked_at
                 FROM gmo_scan ORDER BY checked_at DESC LIMIT 1100"""):
        reason=str(row.get('detail') or '')
        if reason in ('SIGNAL_ACCEPTED','ACCEPTED','OK') or not reason:continue
        accepted=str(row.get('status') or '').upper()
        status='DATA_ERROR' if accepted=='ERROR' else 'RULE_NOT_MET'
        result.append(_row(row.get('checked_at') or row.get('trade_date'),
            row.get('code'),'GMO_D_DAY_SCAN_'+status,
            reason,status,'GMO_ORIGINAL_GMO_SCAN',
            detail='D='+str(row.get('trade_date'))+
                   '; status='+str(row.get('status'))))
    # Existing original issues (including absent verified KRX open and 0D ask).
    for row in _read_rows(db,"""SELECT at,category,code,detail
                 FROM gmo_issues ORDER BY at DESC LIMIT 1000"""):
        category=str(row.get('category') or '')
        result.append(_row(row.get('at'),row.get('code'),
            'GMO_'+category,row.get('detail') or category,
            'OPERATIONAL_BLOCK' if ('BLOCK' in category or
               'ERROR' in category or 'MISSING' in category)
               else 'DIAGNOSTIC','GMO_ORIGINAL_GMO_ISSUES'))
    # Accepted but unfilled original candidates are distinct from rejected
    # D-days and from a broker-confirmed order or missing exchange print.
    today=dt.datetime.now(KST).date().isoformat()
    for row in _read_rows(db,"""SELECT code,name,event_date,entry_date,status
                 FROM gmo_candidates ORDER BY entry_date DESC LIMIT 500"""):
        d=str(row.get('entry_date') or '')
        if not d or d>=today:continue
        state=str(row.get('status') or '')
        if state in ('보유중','청산완료','종결','체결'):continue
        result.append(_row(d,row.get('code'),
            'GMO_D1_CANDIDATE_WITHOUT_CONFIRMED_BUY',
            '선정 후보의 D+1 체결 근거 없음; 당시 시세·호가·잔고·원장 확인 필요',
            'UNFILLED_CAUSE_UNVERIFIED','GMO_ORIGINAL_GMO_CANDIDATES',
            name=row.get('name'),detail='event='+str(row.get('event_date'))+
                '; entry='+d+'; candidate_status='+state))
    # Frozen engine JSON is only READ, never altered or reserialized.
    state=home/'PROJECTGMO_V1.0_account_state.json'
    if state.is_file():
        try:
            obj=json.loads(state.read_text(encoding='utf-8'))
            for a in (obj.get('audit') or [])[-650:]:
                kind=str(a.get('kind') or '')
                if not (kind.startswith(('SIGNAL_REJECT','ENTRY_','BUY_','GMO_D1_',
                      'STALE_','PAPER_LEGACY_','GMO_LEGACY_REARM_DATE_BLOCKED'))
                      or kind in ('CANCEL_INTENT','ORDER_INTENT')):
                    continue
                if kind=='ORDER_INTENT' and not str(a.get('detail') or '').find(' BUY ')>=0:
                    continue
                result.append(_row(a.get('time'),a.get('code'),
                    'GMO_ENGINE_'+kind,a.get('detail') or kind,
                    'PAPER_INTENT_ONLY' if kind=='ORDER_INTENT' else 'BLOCK_OR_RULE',
                    'GMO_ORIGINAL_ENGINE_AUDIT'))
        except (OSError,ValueError,TypeError,AttributeError):
            pass
    return result


def _cw(root):
    db=Path(root)/'stock'/'close'/'AT_CLOSE_PAPER.sqlite3'
    result=[]
    for r in _read_rows(db,"""SELECT ts,code,name,stage,reason,detail_json
                 FROM research_scan_skips ORDER BY id DESC LIMIT 650"""):
        reason=str(r.get('reason') or '')
        result.append(_row(r.get('ts'),r.get('code'),
            'CLOSE_SCAN_'+str(r.get('stage') or '')+'_'+reason,
            reason,'DATA_UNAVAILABLE_OR_STALE','CLOSE_ORIGINAL_SCAN_SKIPS',
            name=r.get('name'),detail=_decode(r.get('detail_json'))))
    for r in _read_rows(db,"""SELECT ts,code,name,decision,reason_json
                 FROM research_candidates WHERE strategy='CLOSE'
                 ORDER BY id DESC LIMIT 650"""):
        decision=str(r.get('decision') or '')
        if decision=='BUY':continue
        why=_decode(r.get('reason_json'))
        result.append(_row(r.get('ts'),r.get('code'),
            'CLOSE_RULE_FILTER',why or decision,
            'RULE_NOT_MET','CLOSE_ORIGINAL_RESEARCH_CANDIDATES',
            name=r.get('name'),detail='decision='+decision))
    for r in _read_rows(db,"""SELECT ts,code,name,decision,entry_result
                 FROM signals WHERE strategy='CLOSE'
                 ORDER BY id DESC LIMIT 350"""):
        state=str(r.get('entry_result') or '')
        if state.startswith('BOUGHT '):continue
        result.append(_row(r.get('ts'),r.get('code'),
            'CLOSE_SIGNAL_NO_CONFIRMED_BUY',
            state or 'BUY signal recorded without confirmed fill',
            'UNFILLED_OR_PENDING','CLOSE_ORIGINAL_SIGNALS',
            name=r.get('name'),detail=str(r.get('decision') or '')))
    for r in _read_rows(db,"""SELECT ts,level,event,detail_json
                 FROM research_events ORDER BY id DESC LIMIT 350"""):
        event=str(r.get('event') or '')
        level=str(r.get('level') or '')
        detail=_decode(r.get('detail_json'))
        if (level not in ('WARN','ERROR') and
                event not in ('CLOSE_ENTRY_DEFERRED','CLOSE_ENTRY_LIVE_RETRY',
                              'CLOSE_ENTRY_WINDOW_EXPIRED','SIGNAL_RESULT')):
            continue
        try:
            info=json.loads(r.get('detail_json') or '{}')
        except (TypeError,ValueError):
            info={}
        if event=='SIGNAL_RESULT' and info.get('ok') is True:
            continue
        result.append(_row(r.get('ts'),info.get('code'),
            'CLOSE_EVENT_'+event,
            detail or event,'OPERATIONAL_EVENT',
            'CLOSE_ORIGINAL_RESEARCH_EVENTS'))
    return result


def _native_ma(root):
    """Read original MA epoch databases from both markets without any writes.

    These native candidate/issue records exist separately from the market
    trend observation writer. A watch without a real order stays observation.
    """
    base=Path(root)
    targets={
        'stock':base/'stock'/'MA_EPOCH_V062'/'STOCK_MA_ENGINE_V1'/'ma_paper.sqlite3',
        'coin':base/'coin'/'ma_epoch_v062'/'upbit_paper.sqlite3'}
    out={'stock':[],'coin':[]}
    for market,db in targets.items():
        if not db.is_file():continue
        # The original column schemas are versioned. SELECT * preserves
        # backward compatibility; fields absent in older ledgers stay unknown.
        for r in _read_rows(db,'SELECT * FROM research ORDER BY rowid DESC LIMIT 350'):
            decision=str(r.get('verdict',r.get('decision',r.get('status',''))) or '')
            if decision.upper() in ('BUY','FILLED','BOUGHT','QUALIFIED','ACCEPTED'):
                continue
            code=r.get('code',r.get('market',''))
            stamp=r.get('ts',r.get('at',r.get('created_at','')))
            explanation=_decode(r.get('detail',r.get('reason',r.get('reason_json',decision))))
            row=_row(stamp,code,'MA_NATIVE_RESEARCH_'+(decision or 'OBS'),
                explanation or decision,'RULE_NOT_MET' if decision.upper() in
                ('REJECT','REJECTED','NO_SIGNAL','SKIP','BLOCK') else
                'NON_ORDER_OBSERVATION','MA_ORIGINAL_RESEARCH',
                frame=r.get('tf',r.get('frame','')))
            row['market']=market
            out[market].append(row)
        for r in _read_rows(db,'SELECT * FROM issues ORDER BY rowid DESC LIMIT 300'):
            text=str(r.get('message',r.get('detail',r.get('reason',''))) or '')
            row=_row(r.get('at',r.get('ts','')),
                r.get('code',r.get('market','')),
                'MA_NATIVE_ISSUE_'+str(r.get('severity',r.get('category',''))),
                text or 'MA issue recorded','OPERATIONAL_EVENT',
                'MA_ORIGINAL_ISSUES')
            row['market']=market
            out[market].append(row)
        for r in _read_rows(db,'SELECT * FROM candidates ORDER BY rowid DESC LIMIT 400'):
            state=str(r.get('status','') or '').upper()
            if state in ('','OPEN','BOUGHT','FILLED','CLOSED','EXECUTED'):
                continue
            code=r.get('code',r.get('market',''))
            reason=str(r.get('reason') or state)
            row=_row(r.get('created_at',r.get('ref_ts',r.get('ref_at',''))),
                code,'MA_NATIVE_CANDIDATE_'+state,
                reason,'ORDER_PENDING_UNVERIFIED' if state=='PENDING'
                else 'NATIVE_CANDIDATE_NOT_FILLED',
                'MA_ORIGINAL_CANDIDATES',
                name=r.get('name',''),frame=r.get('tf',r.get('frame','')),
                detail='candidate is not proof of actual broker order or fill')
            row['market']=market
            out[market].append(row)
    return out


def _load(root):
    native=_native_ma(root)
    rows=_gmo(root)+_cw(root)+native['stock']
    seen=set();out=[]
    for r in sorted(rows,key=lambda x:x.get('at') or '',reverse=True):
        key=(r['source'],r['stage'],r['code'],r['at'],r['message'])
        if key in seen:continue
        seen.add(key);out.append(r)
        if len(out)>=_MAX_ITEMS:break
    coin_out=[];coin_seen=set()
    for r in sorted(native['coin'],key=lambda x:x.get('at') or '',reverse=True):
        key=(r['source'],r['stage'],r['code'],r['at'],r['message'])
        if key in coin_seen:continue
        coin_seen.add(key);coin_out.append(r)
        if len(coin_out)>=_MAX_ITEMS:break
    return {'stock':out,'coin':coin_out,
            'schema':'ARTITRADE_ENTRY_FAILURE_AUDIT_V076',
            'truncated_for_ui':len(rows)>_MAX_ITEMS or
                                len(native['coin'])>_MAX_ITEMS,
            'original_sqlite_files_preserved':True,
            'sources':['gmo_scan','gmo_issues','gmo_candidates',
                       'original GMO account audit','close research_scan_skips',
                       'close research_candidates','close signals','close research_events',
                       'stock/MA_EPOCH_V062 native research/issues/candidates',
                       'coin/ma_epoch_v062 native research/issues/candidates']}


def _work(key,root):
    try:res=_load(root)
    except (OSError,ValueError,TypeError,sqlite3.Error) as exc:
        res={'stock':[],'coin':[],'read_error':type(exc).__name__,
             'schema':'ARTITRADE_ENTRY_FAILURE_AUDIT_V076'}
    with _LOCK:
        job=_JOBS.get(key)
        if job is not None:
            job['result']=res
            job['read_completed_at']=time.monotonic()
            job['busy']=False


def snapshot_for_root(root):
    """Never block a live decision or UI serialization on an audit database."""
    key=str(Path(root).resolve())
    now=time.monotonic()
    with _LOCK:
        job=_JOBS.setdefault(key,{'result':{'stock':[],'coin':[],
            'schema':'ARTITRADE_ENTRY_FAILURE_AUDIT_V076','initializing':True},
            'started_at':-1e12,'busy':False,'read_completed_at':0})
        if not job['busy'] and now-job['started_at']>=_REFRESH_SECONDS:
            job['busy']=True
            job['started_at']=now
            worker=threading.Thread(target=_work,args=(key,key),
                daemon=True,name='ARTITRADE_V075_ENTRY_AUDIT')
            worker.start()
        return job['result']
