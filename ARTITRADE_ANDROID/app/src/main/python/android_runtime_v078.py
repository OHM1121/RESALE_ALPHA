"""Android v0.7.8 runtime compatibility layer.

Does not alter strategy thresholds/state machines. It wires the latest extracted
GMO service into the same Kiwoom 0B/0D stream before the live-order adapter
patches the GMO paper-fill transition.
"""
from pathlib import Path
import sqlite3
import hashlib, hmac, json, uuid, time
import threading
from urllib.parse import urlencode
import android_live_bridge as _core
import d_live as _stock_feed_core

# Preserve the original EXE state machines. Only observe the transport progress
# which the Android bridge previously discarded, and surface fatal startup errors.
_ORIGINAL_STOCK_PROGRESS = _core.Runtime._stock_progress
_ORIGINAL_RUNTIME_SNAPSHOT = _core.Runtime.snapshot
_ORIGINAL_STOCK_RUN = _stock_feed_core.LivePaperRunner.run

def _phone_stock_progress(self, item):
    if isinstance(item, dict) and item.get('stage'):
        self._phone_stock_progress = {
            'stage': str(item.get('stage') or '')[:110],
            'detail': str(item.get('detail') or '')[:210],
            'state': str(item.get('state') or '')[:25],
            'done': int(item.get('done') or 0),
            'total': int(item.get('total') or 0),
            'updated_at_ms': int(time.time() * 1000),
        }
    return _ORIGINAL_STOCK_PROGRESS(self, item)

async def _phone_guard_stock_run(self):
    try:
        self._progress('주식 원장 사전점검', 0, 1, 'PC 승계 원장 및 키움 시세 엔진 준비', state='running')
        return await _ORIGINAL_STOCK_RUN(self)
    except Exception as exc:
        # The Windows runner's bare background thread previously died silently:
        # UI remained WAIT while neither the exception nor connection stage was visible.
        msg = type(exc).__name__ + ': ' + str(exc)[:200]
        try:
            self._health('연결 끊김 - 재연결 대기', msg)
            self._progress('키움 실시간 엔진 시작 오류', 0, 0, msg, state='failed')
            from at_close_bridge import set_broker_transport
            set_broker_transport('disconnected', msg[:120])
        except Exception:
            self.status = '키움 실시간 엔진 시작 오류'
        try:
            self.store.issue('ANDROID_KIWOOM_FEED_FATAL',
                '키움 실시간 수신 엔진이 시작 중 예외로 종료됨',
                details={'type': type(exc).__name__, 'message': str(exc)[:180]})
        except Exception:
            pass
        return None

def _phone_runtime_snapshot(self):
    raw = _ORIGINAL_RUNTIME_SNAPSHOT(self)
    try:
        snapshot = json.loads(raw) if isinstance(raw, str) else dict(raw)
        runner = getattr(self, 'stock_runner', None)
        status = str(getattr(runner, 'status', '전략엔진 준비')) if runner is not None else '전략엔진 준비'
        stock = snapshot.get('stock') or {}
        # Never classify a disconnected stock feed as live solely from a status substring.
        stock['status'] = status
        snapshot['stock'] = stock
        last = dict(getattr(self, '_phone_stock_progress', {}) or {})
        try:
            from at_close_bridge import broker_transport_snapshot
            transport = broker_transport_snapshot()
        except Exception:
            transport = {}
        # REG/tick was confirmed on this socket AND the broker transport is still alive.
        # The runner may temporarily change its status to NXT-universe refresh during LIVE.
        stock['connected'] = bool(runner is not None and
            getattr(runner, '_confirmed_ws_session', False) and
            transport.get('state') == 'connected')
        try:
            preload = runner.preload_gate.snapshot() if runner is not None else {}
        except Exception:
            preload = {}
        snapshot['stock_feed'] = {
            'stage': last.get('stage') or status,
            'detail': last.get('detail') or (transport.get('reason') or ''),
            'state': last.get('state') or 'waiting',
            'updated_at_ms': last.get('updated_at_ms') or 0,
            'subscribed': len(getattr(runner, 'subscriptions', []) or []) if runner else 0,
            'last_frame_ms': int(float(getattr(runner, '_watch_feed_last_epoch', 0) or 0) * 1000) if runner else 0,
            'transport': transport,
            'preload': preload,
        }
        return json.dumps(snapshot, ensure_ascii=False, default=str)
    except Exception:
        return raw

_core.Runtime._stock_progress = _phone_stock_progress
_core.Runtime.snapshot = _phone_runtime_snapshot
_stock_feed_core.LivePaperRunner.run = _phone_guard_stock_run

_ORIG_PATCH_GMO = _core.Runtime._patch_gmo
_ORIG_STOP = _core.Runtime.stop
_ORIG_STOCK_AT_LEDGER = _core.Runtime._stock_at_ledger
_ORIG_HUB_RECORDS = _core.Runtime.hub_records
_FRAMES=("5m","30m","60m","day")




def _upbit_jwt_hs512(self, params=None):
    head=self._b64(b'{"alg":"HS512","typ":"JWT"}')
    payload={'access_key':self.access,'nonce':str(uuid.uuid4())}
    if params:
        q=urlencode([(k,str(v)) for k,v in params.items()])
        payload['query_hash']=hashlib.sha512(q.encode()).hexdigest()
        payload['query_hash_alg']='SHA512'
    body=self._b64(json.dumps(payload,separators=(',',':')).encode())
    sig=hmac.new(self.secret.encode(),(head+'.'+body).encode(),hashlib.sha512).digest()
    return head+'.'+body+'.'+self._b64(sig)



def _sell_net_stock(e, pos):
    try:
        if str(e.get('side') or '').upper()!='SELL':
            return None
        q=float(e.get('qty') or 0)
        eq=max(1.0,float(pos.get('entry_qty') or q or 1))
        entry=float(pos.get('entry_price') or e.get('price') or 0)
        entry_fee=float(pos.get('entry_fee') or 0)*(q/eq)
        return round((float(e.get('price') or 0)-entry)*q-entry_fee-float(e.get('fees') or 0)-float(e.get('tax') or 0))
    except Exception:
        return None



def _closed_position_win_rate(positions, executions):
    closed={int(p.get('id') or 0) for p in positions or [] if str(p.get('status') or '').upper()=='CLOSED' and int(p.get('id') or 0)>0}
    if not closed:return None
    pnl={pid:0.0 for pid in closed}
    seen=set()
    for e in executions or []:
        try:pid=int(e.get('position_id') or 0)
        except Exception:continue
        if pid not in closed or str(e.get('side') or '').upper()!='SELL':continue
        val=e.get('net_profit')
        if val is None:val=e.get('net_pnl')
        if val is None:val=e.get('net_realized')
        try:pnl[pid]+=float(val or 0);seen.add(pid)
        except Exception:pass
    evaluated=[pid for pid in closed if pid in seen]
    if not evaluated:return None
    return round(sum(1 for pid in evaluated if pnl.get(pid,0)>0)*100.0/len(evaluated),2)


def _target_summary(rows, mode):
    hit=miss=pending=0
    for p in rows or []:
        status=str(p.get('status') or '').upper()
        reached=False
        if mode=='stock_at':
            target=float(p.get('ref_bodytop') or 0);peak=float(p.get('peak') or 0)
            reached=bool(p.get('first_sold')) or (target>0 and peak>=target)
        elif mode=='stock_ma':
            target=float(p.get('target') or 0);peak=float(p.get('peak') or 0)
            reached=bool(p.get('first_sold')) or (target>0 and peak>=target)
        elif mode=='coin':
            try: reached=float(p.get('first_sold_qty') or 0)>0
            except Exception: reached=False
            try:
                target=float(p.get('target') or 0);peak=float(p.get('peak') or 0)
                reached=reached or (target>0 and peak>=target)
            except Exception: pass
        elif mode=='close':
            reached=bool(p.get('first_sold'))
        if reached: hit+=1
        elif status=='CLOSED' or (mode=='stock_at' and int(p.get('qty') or 0)<=0): miss+=1
        else: pending+=1
    evaluated=hit+miss
    return {'evaluated':evaluated,'hit':hit,'miss':miss,'pending':pending,
            'rate_pct':round(hit*100.0/evaluated,2) if evaluated else None}


def _research_scope(items):
    actual=[x for x in items if bool(x.get('bought'))]
    virtual=[x for x in items if not bool(x.get('bought'))]
    actual_done=[x for x in actual if x.get('outcome') in ('HIT','MISS')]
    virtual_entered=[x for x in virtual if bool(x.get('entry_observed'))]
    virtual_done=[x for x in virtual_entered if x.get('outcome') in ('HIT','MISS')]
    def summary(rows,all_rows):
        h=sum(1 for x in rows if x.get('outcome')=='HIT')
        return {'evaluated':len(rows),'hit':h,'miss':len(rows)-h,
                'pending':max(0,len(all_rows)-len(rows)),
                'rate_pct':round(h*100.0/len(rows),2) if rows else None}
    out={'signals':len(items),'bought':len(actual),'not_bought':len(virtual),
         'virtual_entry':len(virtual_entered)}
    out['actual_summary']=summary(actual_done,actual)
    out['virtual_summary']=summary(virtual_done,virtual_entered)
    return out


def _research_bands(items):
    out=[]
    for lo,hi,label in ((0,20,'0~20점'),(21,40,'21~40점'),(41,60,'41~60점'),(61,80,'61~80점'),(81,100,'81~100점')):
        part=[]
        for x in items:
            try:
                sc=float(x.get('score'))
                if lo<=sc<=hi: part.append(x)
            except Exception: pass
        q=_research_scope(part)
        a=q['actual_summary'];v=q['virtual_summary']
        out.append({'band':label,'signals':q['signals'],'bought':q['bought'],'not_bought':q['not_bought'],
                    'virtual_entry':q['virtual_entry'],
                    'actual_evaluated':a['evaluated'],'actual_hit':a['hit'],'actual_miss':a['miss'],
                    'actual_pending':a['pending'],'actual_rate_pct':a['rate_pct'],
                    'virtual_evaluated':v['evaluated'],'virtual_hit':v['hit'],'virtual_miss':v['miss'],
                    'virtual_pending':v['pending'],'virtual_rate_pct':v['rate_pct']})
    return out


def _research_record(items, score_items=None):
    q=_research_scope(items)
    a=q['actual_summary'];v=q['virtual_summary']
    return {'schema':'phone-v078-research','items':items,'score_items':score_items if score_items is not None else items,
            'signals':q['signals'],'bought':q['bought'],'not_bought':q['not_bought'],'virtual_entry':q['virtual_entry'],
            'actual_summary':a,'virtual_summary':v,'bands':_research_bands(items),
            'actual_rate_pct':a['rate_pct'],'virtual_rate_pct':v['rate_pct']}


def _stock_at_research(root):
    db=Path(root)/'stock'/'ARTITRADE_D.sqlite3'
    candidates=_core._db_rows(db, """SELECT signal_id,tf,code,name,signal_ts AS at,ref_ts,
        diverse_11_score AS score,limit_price,first_target FROM candidate_snapshots
        ORDER BY signal_id DESC LIMIT 3000""")
    positions=_core._db_rows(db,"SELECT tf,code,ref_ts,status,peak,entry_ts,first_sold FROM positions")
    shadows={int(x.get('signal_id') or 0):x for x in _core._db_rows(db,
        "SELECT signal_id,state,entry_ts,entry_price,peak,realized_net_krw,sold_at FROM stock_shadow_trials")}
    target_fills={int(x.get('signal_id') or 0) for x in _core._db_rows(db,
        "SELECT DISTINCT signal_id FROM stock_shadow_fills WHERE reason='FIRST_TARGET_40PCT'")}
    actual={(str(x.get('tf')),str(x.get('code')),str(x.get('ref_ts'))):x for x in positions}
    decisions={int(x.get('signal_id') or 0):x for x in _core._db_rows(db,
        "SELECT signal_id,decision_at_evaluation,reason_at_evaluation,origin_10_rank,"
        "diverse_11_rank FROM candidate_cohort_decisions")}
    items=[]
    for x in candidates:
        key=(str(x.get('tf')),str(x.get('code')),str(x.get('ref_ts')))
        decision=decisions.get(int(x.get('signal_id') or 0),{})
        p=actual.get(key);sh=shadows.get(int(x.get('signal_id') or 0))
        bought=p is not None;entry_observed=bought or bool(sh and sh.get('entry_ts'))
        outcome='PENDING';closed=False
        if bought:
            try: reached=bool(p.get('first_sold')) or (float(p.get('peak') or 0)>=float(x.get('first_target') or 0)>0)
            except Exception: reached=bool(p.get('first_sold'))
            closed=str(p.get('status') or '').upper()=='CLOSED'
            outcome='HIT' if reached else 'MISS' if closed else 'PENDING'
        elif sh and sh.get('entry_ts'):
            closed=str(sh.get('state') or '').upper()=='CLOSED'
            outcome='HIT' if int(x.get('signal_id') or 0) in target_fills else 'MISS' if closed else 'PENDING'
        items.append({'at':x.get('at'),'tf':x.get('tf'),'strategy':x.get('tf'),'code':x.get('code'),
            'name':x.get('name'),'ref_ts':x.get('ref_ts'),'score':x.get('score'),
            'bought':bought,'entry_observed':entry_observed,'closed':closed,'outcome':outcome,
            'target_hit': True if outcome=='HIT' else False if outcome=='MISS' else None,
            'entry_status':'ACTUAL_FILLED' if bought else 'VIRTUAL_SHADOW' if entry_observed else 'VIRTUAL_NOT_ENTERED',
            'signal_id':x.get('signal_id'),
            'decision':decision.get('decision_at_evaluation'),
            'origin_rank':decision.get('origin_10_rank'),
            'diverse_rank':decision.get('diverse_11_rank'),
            'reason':decision.get('reason_at_evaluation') or '선정 근거 원본 별도 저장'})
    return _research_record(items,candidates)


def _stock_ma_research(root):
    db=Path(root)/'stock'/'STOCK_MA_ENGINE_V1'/'ma_paper.sqlite3'
    candidates=_core._db_rows(db,"SELECT id,tf,code,name,ref_ts,created_at AS at,score,status,target,features FROM candidates ORDER BY id DESC LIMIT 3000")
    positions=_core._db_rows(db,"SELECT id,candidate_id,tf,code,name,status,first_sold,peak,target,entry_at FROM positions")
    pos={int(x.get('candidate_id') or 0):x for x in positions if x.get('candidate_id') is not None}
    items=[]
    for x in candidates:
        p=pos.get(int(x.get('id') or 0));bought=p is not None
        outcome='PENDING';closed=False
        if p:
            closed=str(p.get('status') or '').upper()=='CLOSED'
            reached=bool(p.get('first_sold'))
            try:reached=reached or (float(p.get('peak') or 0)>=float(p.get('target') or 0)>0)
            except Exception:pass
            outcome='HIT' if reached else 'MISS' if closed else 'PENDING'
        items.append({'at':x.get('at'),'tf':x.get('tf'),'strategy':'MA','code':x.get('code'),'name':x.get('name'),
            'ref_ts':x.get('ref_ts'),'score':x.get('score'),'bought':bought,'entry_observed':bought,
            'closed':closed,'outcome':outcome,'target_hit':True if outcome=='HIT' else False if outcome=='MISS' else None,
            'entry_status':'ACTUAL_FILLED' if bought else 'VIRTUAL_NOT_REPLAYED',
            'candidate_id':x.get('id'),'decision':x.get('status'),
            'reason':x.get('status') or '판단 근거 별도 저장'})
    return _research_record(items,candidates)


def _coin_research(root,which):
    db=Path(root)/'coin'/which/'upbit_paper.sqlite3'
    candidates=_core._db_rows(db,"SELECT id,tf,market,name,ref_at AS ref_ts,ref_at AS at,score,limit_price,target,reason,status FROM candidates ORDER BY id DESC LIMIT 3000")
    positions=_core._db_rows(db,"SELECT * FROM positions")
    actual={(str(x.get('tf')),str(x.get('market')),str(x.get('ref_at'))):x for x in positions if str(x.get('status') or '')!='RESET_B_V1'}
    shadows={int(x.get('candidate_id') or 0):x for x in _core._db_rows(db,
        "SELECT candidate_id,state,entry_at,entry_price,peak,realized_net,sell_at FROM coin_shadow_trials")}
    target_fills=set()
    for x in _core._db_rows(db,"SELECT candidate_id,side,reason FROM coin_shadow_fills WHERE side='SELL'"):
        reason=str(x.get('reason') or '').upper()
        if 'FIRST_TARGET' in reason or '1차 목표' in reason or 'FIRST' in reason and 'TARGET' in reason:
            target_fills.add(int(x.get('candidate_id') or 0))
    items=[]
    for x in candidates:
        key=(str(x.get('tf')),str(x.get('market')),str(x.get('ref_ts')))
        p=actual.get(key);sh=shadows.get(int(x.get('id') or 0))
        bought=p is not None;entry_observed=bought or bool(sh and sh.get('entry_at'))
        outcome='PENDING';closed=False
        if p:
            closed=str(p.get('status') or '').upper()=='CLOSED'
            try: reached=float(p.get('first_sold_qty') or 0)>0 or (float(p.get('peak') or 0)>=float(p.get('target') or 0)>0)
            except Exception: reached=False
            outcome='HIT' if reached else 'MISS' if closed else 'PENDING'
        elif sh and sh.get('entry_at'):
            closed=str(sh.get('state') or '').upper()=='CLOSED'
            outcome='HIT' if int(x.get('id') or 0) in target_fills else 'MISS' if closed else 'PENDING'
        items.append({'at':x.get('at'),'tf':x.get('tf'),'strategy':x.get('tf'),'market':x.get('market'),
            'name':x.get('name'),'ref_ts':x.get('ref_ts'),'score':x.get('score'),
            'bought':bought,'entry_observed':entry_observed,'closed':closed,'outcome':outcome,
            'target_hit':True if outcome=='HIT' else False if outcome=='MISS' else None,
            'entry_status':'ACTUAL_FILLED' if bought else 'VIRTUAL_SHADOW' if entry_observed else 'VIRTUAL_NOT_ENTERED',
            'reason':x.get('reason')})
    return _research_record(items,candidates)


def _close_full(self):
    db=Path(self.root)/'stock'/'close'/'AT_CLOSE_PAPER.sqlite3'
    wallets=_core._db_rows(db,"SELECT * FROM wallets")
    positions=_core._db_rows(db,"SELECT * FROM positions WHERE status='OPEN' ORDER BY id DESC LIMIT 300")
    all_positions=_core._db_rows(db,"SELECT * FROM positions ORDER BY id DESC LIMIT 5000")
    executions=_core._db_rows(db,"SELECT * FROM executions ORDER BY id DESC LIMIT 3000")
    day=_core.datetime.now(_core.timezone(_core.timedelta(hours=9))).date().isoformat()
    pending=_core._db_rows(db, """SELECT s.id AS signal_id,s.ts AS ref_ts,s.ts AS at,s.strategy,s.code,s.name,
        s.decision,s.rank_no,s.trading_value_krw,s.price,s.feature_json AS reason
        FROM signals s WHERE s.decision='BUY' AND substr(s.ts,1,10)=?
          AND (s.entry_result IS NULL OR s.entry_result NOT LIKE 'BOUGHT %')
        ORDER BY s.id DESC LIMIT 500""",(day,))
    for e in executions:
        e['at']=e.get('ts');e['net_profit']=e.get('net_realized');e['strategy']='CLOSE'
    realized=sum(float(x.get('realized') or 0) for x in wallets)
    unreal=sum((float(x.get('last_price') or 0)-float(x.get('entry_price') or 0))*float(x.get('qty') or 0) for x in positions)
    win_rate=_closed_position_win_rate(all_positions,executions)
    target=_target_summary(all_positions,'close')
    bought=len(all_positions);closed=sum(1 for x in all_positions if str(x.get('status') or '').upper()=='CLOSED')
    performance={'close':{'realized_won':round(realized),'holding_won':round(unreal),'total_trades':bought,
        'bought_count':bought,'open_count':len(positions),'closed_count':closed,'pending_count':len(pending),
        'actual_target_rate_pct':target['rate_pct'],'actual_target_evaluated':target['evaluated'],
        'actual_target_hit':target['hit'],'actual_target_miss':target['miss'],'actual_target_pending':target['pending']}}
    return {'schema':'phone-v078','origin':'phone','accounts':wallets,'positions':positions,'pending':pending,'executions':executions,
        'performance_by_frame':performance,'actual_target':target,'realized_pnl':round(realized),'unrealized_pnl':round(unreal),
        'total_pnl':round(realized+unreal),'win_rate':win_rate,
        'positions_count':len(positions),'pending_count':len(pending)}


def _close_research(root):
    db=Path(root)/'stock'/'close'/'AT_CLOSE_PAPER.sqlite3'
    signals=_core._db_rows(db,"SELECT id,ts AS at,strategy,code,name,decision,entry_result,feature_json,reason_json FROM signals ORDER BY id DESC LIMIT 3000")
    positions=_core._db_rows(db,"SELECT * FROM positions ORDER BY id DESC LIMIT 5000")
    by_signal={int(x.get('signal_id') or 0):x for x in positions if x.get('signal_id') is not None}
    items=[]
    for x in signals:
        p=by_signal.get(int(x.get('id') or 0));bought=p is not None
        outcome='PENDING';closed=False
        if p:
            closed=str(p.get('status') or '').upper()=='CLOSED'
            outcome='HIT' if bool(p.get('first_sold')) else 'MISS' if closed else 'PENDING'
        items.append({'at':x.get('at'),'strategy':'CLOSE','code':x.get('code'),'name':x.get('name'),
            'score':None,'bought':bought,'entry_observed':bought,'closed':closed,'outcome':outcome,
            'target_hit':True if outcome=='HIT' else False if outcome=='MISS' else None,
            'entry_status':'ACTUAL_FILLED' if bought else 'VIRTUAL_NOT_REPLAYED',
            'reason':x.get('reason_json') or x.get('entry_result') or x.get('decision')})
    score_rows=_core._db_rows(db,"SELECT id,ts AS at,strategy,code,name,decision,near_signal,feature_json,reason_json FROM research_candidates ORDER BY id DESC LIMIT 3000")
    return _research_record(items,score_rows)


def _stock_at_v19_full(self, base):
    if not self.stock_runner:
        return base
    store=self.stock_runner.store
    with store.lock,store.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'v19_accounts' not in tables or 'v19_orders' not in tables:
            return base
        wallets=self.stock_runner.engine.wallet_snapshot(c)
        positions=[dict(r) for r in c.execute("""SELECT p.* FROM positions p
            WHERE p.status='OPEN' AND p.qty>0 AND (
              EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id) OR
              EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id))
            ORDER BY p.id DESC""").fetchall()]
        pending=[dict(r) for r in c.execute("""SELECT o.*,s.name,s.ref_ts,s.ts
            FROM v19_orders o JOIN signals s ON s.id=o.signal_id
            WHERE o.status='PENDING' AND NOT EXISTS(
              SELECT 1 FROM positions p WHERE p.code=o.code AND p.status='OPEN' AND p.qty>0
              AND (EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id)
                   OR EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id)))
            ORDER BY o.score DESC,o.since_ts DESC""").fetchall()]
        posmap={int(r['id']):dict(r) for r in c.execute("""SELECT p.* FROM positions p WHERE
              EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id) OR
              EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id)""").fetchall()}
        all_positions=list(posmap.values())
        executions=[dict(r) for r in c.execute("""SELECT e.* FROM executions e WHERE
              EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=e.position_id) OR
              EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=e.position_id)
              ORDER BY e.id DESC LIMIT 10000""").fetchall()]
        for e in executions:
            e['at']=e.get('ts');e['strategy']=e.get('tf') or 'AT'
            net=_sell_net_stock(e,posmap.get(int(e.get('position_id') or 0),{}))
            if net is not None:e['net_profit']=net
        performance={}
        accounts=[]
        total_real=0.0;total_hold=0.0
        for tf in _FRAMES:
            w=wallets.get(tf) or {}
            held=[p for p in positions if str(p.get('tf') or '')==tf]
            hold=sum((float(p.get('last_price') or 0)-float(p.get('entry_price') or 0))*float(p.get('qty') or 0) for p in held)
            realized=float(w.get('realized_pnl') or 0)
            bought=int(c.execute("""SELECT COUNT(*) FROM positions p WHERE p.tf=? AND (
                EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id) OR
                EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id))""",(tf,)).fetchone()[0])
            open_count=len(held)
            closed=int(c.execute("""SELECT COUNT(*) FROM positions p WHERE p.tf=? AND (p.status='CLOSED' OR p.qty<=0) AND (
                EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=p.id) OR
                EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=p.id))""",(tf,)).fetchone()[0])
            pending_count=sum(1 for p in pending if str(p.get('tf') or '')==tf)
            tgt=_target_summary([p for p in all_positions if str(p.get('tf') or '')==tf],'stock_at')
            performance[tf]={
                'realized_won':round(realized),'holding_won':round(hold),
                'total_trades':bought,'bought_count':bought,'open_count':open_count,
                'closed_count':closed,'pending_count':pending_count,
                'actual_target_rate_pct':tgt['rate_pct'],'actual_target_evaluated':tgt['evaluated'],
                'actual_target_hit':tgt['hit'],'actual_target_miss':tgt['miss'],'actual_target_pending':tgt['pending']
            }
            accounts.append({'tf':tf,'strategy':'at','cash':w.get('cash',0),
                'seed':w.get('seed',2_000_000),'realized_pnl':round(realized),
                'unrealized_pnl':round(hold)})
            total_real+=realized;total_hold+=hold
    base.update({'schema':'phone-v078','origin':'phone','accounts':accounts,'positions':positions,
        'pending':pending,'executions':executions,'performance_by_frame':performance,
        'actual_target':_target_summary(all_positions,'stock_at'),
        'realized_pnl':round(total_real),'unrealized_pnl':round(total_hold),
        'total_pnl':round(total_real+total_hold),'positions_count':len(positions),
        'pending_count':len(pending)})
    base['win_rate']=_closed_position_win_rate(all_positions,executions)
    return base


def _ma_pending_diagnostics(self,snap):
    """Explain MA paper pending without changing fill/quote/real-order requirements."""
    try:
        from datetime import datetime
        from d_storage import KST
        from d_v19_portfolio import live_session
        from stock_ma_engine import trading_sessions_elapsed, _connect
        now=datetime.now(KST)
        ma=self.stock_runner.ma
        # Expiry in the Windows EXE is inside on_tick after a valid 0D quote.
        # Without a quote, stale PENDING orders can remain indefinitely.
        # Apply ONLY the original session-based expiry, never create a fill.
        last=getattr(self,'_phone_ma_expiry_at',0.0)
        if now.weekday()<5 and live_session(now,'KRX') and time.monotonic()-last>=90:
            self._phone_ma_expiry_at=time.monotonic()
            max_sessions={'5m':1,'30m':3,'60m':3,'day':8}
            expired=0
            with ma.lock, _connect(ma.db) as conn:
                candidates=conn.execute("SELECT id,tf,ref_ts FROM candidates WHERE status='PENDING' LIMIT 500").fetchall()
                for c in candidates:
                    allowed=max_sessions.get(str(c['tf']))
                    if allowed is None:continue
                    if trading_sessions_elapsed(str(c['ref_ts']),now.strftime('%Y%m%d%H%M%S'),ma.market_store.root)>allowed:
                        conn.execute("UPDATE candidates SET status='EXPIRED' WHERE id=? AND status='PENDING'",(c['id'],))
                        expired+=1
            if expired:
                ma._refresh_watched()
                snap=ma.snapshot() or {}
        quote_book=getattr(getattr(self.stock_runner,'engine',None),'book',None)
        book=getattr(quote_book,'_items',{}) or {}
        feed_ok=bool(getattr(self.stock_runner,'_confirmed_ws_session',False))
        for row in snap.get('pending') or []:
            code=str(row.get('code') or '')
            limit=int(row.get('limit_won') or 0)
            quote=book.get((code,'KRX'))
            reason='KRX 정규장 외 · 다음 거래일 대기'
            if live_session(now,'KRX'):
                if not feed_ok:reason='키움 0B/0D 실시간 구독 미확인 · WAIT'
                elif quote is None:reason='0D 실시간 호가 미수신 · 임의 체결 금지'
                else:
                    ask,bid,ask_qty,seen_at=quote
                    try:age=(now-seen_at).total_seconds()
                    except Exception:age=999
                    if not 0<=age<=3:reason='호가 정보 3초 초과 · 신규 매수 차단'
                    elif int(ask or 0)<=0 or int(bid or 0)<=0:reason='매수·매도 호가 유효성 미확인'
                    elif int(ask)>limit:reason='최우선 매도호가가 기준봉 저가 지정가 초과'
                    elif int(ask_qty or 0)<1:reason='최우선 매도호가 수량 부족'
                    else:reason='지정가 도달 · 0B 실제 체결가와 잔량 동시 확인 대기'
            row['reason']=reason
            row['blocked']=True  # Pending is NOT a filled broker/virtual position.
            row['ma_pending_diagnostic']=True
    except Exception as e:
        self._err('stock_ma_pending_diagnostics',e)
    return snap

def _stock_ma_full(self):
    if not self.stock_runner:
        return {}
    snap=_ma_pending_diagnostics(self,self.stock_runner.ma.snapshot() or {})
    perf=snap.get('performance_by_frame') or {}
    db=Path(self.root)/'stock'/'STOCK_MA_ENGINE_V1'/'ma_paper.sqlite3'
    all_positions=_core._db_rows(db,"SELECT * FROM positions ORDER BY id DESC LIMIT 10000")
    full_exec=_core._db_rows(db,"SELECT * FROM executions ORDER BY id DESC LIMIT 10000")
    for x in full_exec:
        x['at']=x.get('ts');x['strategy']='MA'
    realized=sum(float((perf.get(tf) or {}).get('realized_won') or 0) for tf in _FRAMES)
    holding=sum(float((perf.get(tf) or {}).get('holding_won') or 0) for tf in _FRAMES)
    accounts=[]
    wallets=snap.get('wallets') or {}
    for tf in _FRAMES:
        w=wallets.get(tf) or {}
        tgt=_target_summary([p for p in all_positions if str(p.get('tf') or '')==tf],'stock_ma')
        perf.setdefault(tf,{})
        perf[tf].update({'actual_target_rate_pct':tgt['rate_pct'],'actual_target_evaluated':tgt['evaluated'],
                         'actual_target_hit':tgt['hit'],'actual_target_miss':tgt['miss'],'actual_target_pending':tgt['pending']})
        accounts.append({'tf':tf,'strategy':'ma','cash':w.get('cash',0),'seed':w.get('seed',2_000_000),
                         'realized_pnl':(perf.get(tf) or {}).get('realized_won',0),
                         'unrealized_pnl':(perf.get(tf) or {}).get('holding_won',0),
                         'max_positions':w.get('max_positions',4)})
    out=dict(snap)
    out['executions']=full_exec
    out['performance_by_frame']=perf
    out.update({'schema':'phone-v078','origin':'phone','accounts':accounts,
                'realized_pnl':round(realized),'unrealized_pnl':round(holding),
                'total_pnl':round(realized+holding),'actual_target':_target_summary(all_positions,'stock_ma'),
                'positions_count':len(snap.get('positions') or []),
                'pending_count':len(snap.get('pending') or [])})
    out['win_rate']=_closed_position_win_rate(all_positions,full_exec)
    return out

def _first_existing(*values):
    """Use actual stored/quoted values only; never invent prices or reference candles."""
    for value in values:
        if value is not None and value != '' and str(value).lower() not in ('none','null','nan','0','0.0'):
            return value
    return None


def _normalize_coin_holdings(positions, history):
    """Join compact frame snapshots with read-only DB rows without rewriting the ledger.

    The old UI adaptation assigned entry_price=x.get('entry'), overwriting a
    legitimate pre-existing entry_price with None when the snapshot schema differed.
    """
    by_id={str(row['id']):row for row in history if row.get('id') is not None}
    by_key={}
    for row in history:
        if str(row.get('status') or '').upper() not in ('OPEN','HOLD','HOLDING'):
            continue
        tf=str(row.get('tf') or '')
        symbol=str(_first_existing(row.get('symbol'),row.get('market'),row.get('code'),row.get('ticker')) or '')
        if symbol:
            by_key.setdefault((tf,symbol),[]).append(row)
    result=[]
    for raw in positions:
        if not isinstance(raw,dict):continue
        row=dict(raw)
        db=by_id.get(str(row.get('id'))) if row.get('id') is not None else None
        if db is None:
            tf=str(row.get('tf') or '')
            symbol=str(_first_existing(row.get('symbol'),row.get('market'),row.get('code'),row.get('ticker')) or '')
            matches=by_key.get((tf,symbol),[])
            if len(matches)==1:db=matches[0]
        db=db or {}
        ref=_first_existing(row.get('ref_ts'),row.get('ref_at'),row.get('reference_ts'),
            row.get('reference_at'),row.get('base_candle_time'),row.get('ref_time'),
            db.get('ref_ts'),db.get('ref_at'),db.get('reference_ts'),db.get('reference_at'))
        entry=_first_existing(row.get('entry_price'),row.get('entry'),row.get('avg_buy_price'),
            row.get('buy_price'),row.get('avg_entry'),db.get('entry_price'),
            db.get('entry'),db.get('avg_buy_price'),db.get('buy_price'))
        mark=_first_existing(row.get('last_price'),row.get('mark'),row.get('current_price'),
            row.get('market_price'),row.get('mark_price'),db.get('last_price'),
            db.get('mark'),db.get('current_price'))
        if ref is not None:row['ref_ts']=ref
        if entry is not None:row['entry_price']=entry
        if mark is not None:row['last_price']=mark
        result.append(row)
    return result


def _coin_full(ledger):
    snap=ledger.snapshot() or {}
    positions=[];accounts=[];performance={}
    with ledger.lock,ledger.connect() as c:
        all_positions=[dict(x) for x in c.execute("SELECT * FROM positions WHERE status<>'RESET_B_V1' ORDER BY id DESC LIMIT 10000").fetchall()]
        pending=[dict(x) for x in c.execute("SELECT * FROM orders WHERE status='PENDING' ORDER BY score DESC,created_at DESC LIMIT 3000").fetchall()]
        executions=[dict(x) for x in c.execute("SELECT * FROM executions ORDER BY id DESC LIMIT 10000").fetchall()]
        for tf in _FRAMES:
            frame=snap.get(tf) or {}
            pos=frame.get('positions') or []
            positions.extend(pos)
            bought=int(c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status IN ('OPEN','CLOSED')",(tf,)).fetchone()[0])
            open_count=int(c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status='OPEN' AND qty!='0'",(tf,)).fetchone()[0])
            closed=int(c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status='CLOSED'",(tf,)).fetchone()[0])
            pending_count=int(c.execute("SELECT COUNT(*) FROM orders WHERE tf=? AND status='PENDING'",(tf,)).fetchone()[0])
            realized=float(frame.get('realized') or 0);holding=float(frame.get('holding') or 0)
            tgt=_target_summary([p for p in all_positions if str(p.get('tf') or '')==tf],'coin')
            performance[tf]={'realized_won':round(realized),'holding_won':round(holding),
                'total_trades':bought,'bought_count':bought,'open_count':open_count,
                'closed_count':closed,'pending_count':pending_count,
                'actual_target_rate_pct':tgt['rate_pct'],'actual_target_evaluated':tgt['evaluated'],
                'actual_target_hit':tgt['hit'],'actual_target_miss':tgt['miss'],'actual_target_pending':tgt['pending']}
            accounts.append({'tf':tf,'cash':frame.get('cash',0),'seed':frame.get('seed',2_000_000),
                'realized_pnl':round(realized),'unrealized_pnl':round(holding),
                'max_positions':frame.get('max_positions',4)})
    positions=_normalize_coin_holdings(positions,all_positions)
    if not positions:
        # Startup may not have populated the frame snapshot yet. Show only
        # truly OPEN rows from imported SQLite, marked as historical holdings.
        fallback=[dict(x) for x in all_positions
                  if str(x.get('status') or '').upper()=='OPEN'
                  and str(x.get('qty') or '0') not in ('','0','0.0')]
        positions=_normalize_coin_holdings(fallback,all_positions)
        for x in positions:x['ledger_fallback']=True
    for x in pending:
        x['ref_ts']=_first_existing(x.get('ref_ts'),x.get('ref_at'),
            x.get('reference_ts'),x.get('reference_at'))
        x['strategy']=x.get('tf')
    for e in executions:
        e['net_profit']=e.get('net_pnl');e['strategy']=e.get('tf')
    realized=sum(float(x.get('realized_pnl') or 0) for x in accounts)
    holding=sum(float(x.get('unrealized_pnl') or 0) for x in accounts)
    win_rate=_closed_position_win_rate(all_positions,executions)
    return {'schema':'phone-v078','origin':'phone','accounts':accounts,'positions':positions,
            'pending':pending,'executions':executions,'performance_by_frame':performance,
            'actual_target':_target_summary(all_positions,'coin'),
            'realized_pnl':round(realized),'unrealized_pnl':round(holding),
            'total_pnl':round(realized+holding),'win_rate':win_rate,
            'positions_count':len(positions),'pending_count':len(pending)}

def _research_source_audit(db,primary,evidence):
    """Read-only original SQLite count audit, independent of the 3000-row UI window.

    A missing DB or table is never reported as 'zero researched candidates'.
    All evidence tables remain stored in the original imported database.
    """
    db=Path(db)
    if not db.is_file():
        return {'source_total':-1,'source_status':'DB_MISSING','evidence_counts':{}}
    try:
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=4) as conn:
            tables={r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if primary not in tables:
                return {'source_total':-1,'source_status':'TABLE_MISSING',
                        'evidence_counts':{}}
            total=int(conn.execute('SELECT COUNT(*) FROM "'+primary+'"').fetchone()[0])
            counts={}
            for name in evidence:
                if name in tables:
                    counts[name]=int(conn.execute('SELECT COUNT(*) FROM "'+name+'"').fetchone()[0])
        return {'source_total':total,'source_status':'OK','evidence_counts':counts}
    except (sqlite3.Error,OSError,ValueError) as exc:
        return {'source_total':-1,'source_status':'READ_ERROR',
                'source_error':type(exc).__name__,'evidence_counts':{}}


def _research_history(self, out):
    """Read every strategy independently: one legacy table failure must not mask other research.

    No write to PC/phone SQLite. Results are scoped to the originating strategy
    so the UI can distinguish a genuinely empty history from a failed load.
    """
    roots={
        'AT':('stock/ARTITRADE_D.sqlite3','candidate_snapshots',
              ('candidate_events','candidate_forward','candidate_milestones',
               'candidate_cohort_decisions','candidate_research_health',
               'stock_shadow_trials','stock_shadow_fills','signals','positions','executions')),
        'MA':('stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3','candidates',
              ('positions','executions','candidate_decisions')),
        'CLOSE':('stock/close/AT_CLOSE_PAPER.sqlite3','signals',
                 ('research_candidates','positions','executions')),
        'COIN_BASE':('coin/base/upbit_paper.sqlite3','candidates',
                     ('candidate_decisions','coin_scan_decisions','coin_shadow_trials',
                      'coin_shadow_fills','coin_shadow_book_path','positions','executions')),
        'COIN_MA':('coin/ma/upbit_paper.sqlite3','candidates',
                   ('candidate_decisions','coin_scan_decisions','coin_shadow_trials',
                    'coin_shadow_fills','coin_shadow_book_path','positions','executions')),
    }
    def read(source, loader):
        rel,primary,evidence=roots[source]
        audit=_research_source_audit(Path(self.root)/rel,primary,evidence)
        try:
            result=loader()
            if not isinstance(result,dict):
                raise TypeError('research result is not a dict')
            result.update(audit)
            result['load_status']='OK' if audit['source_status']=='OK' else audit['source_status']
            result['origin']='IMPORTED_PC_AND_PHONE'
            result['loaded_items']=len(result.get('items') or [])
            result['partial_window']=(
                audit['source_total']>result['loaded_items'] if audit['source_total']>=0 else False)
            return result
        except Exception as exc:
            self._err('research_history_v078_'+source,exc)
            result={'schema':'phone-v078-research','origin':'IMPORTED_PC_AND_PHONE',
                    'load_status':'ERROR','load_error':source,'loaded_items':0,
                    'items':[],'score_items':[],'bands':[]}
            result.update(audit)
            return result
    stock=out.setdefault('stock',{}).setdefault('at',{})
    stock['research']=read('AT',lambda:_stock_at_research(self.root))
    stock['research']['ma']=read('MA',lambda:_stock_ma_research(self.root))
    out.setdefault('stock',{}).setdefault('cw',{})['research']=read(
        'CLOSE',lambda:_close_research(self.root))
    out.setdefault('coin',{}).setdefault('general',{})['research']=read(
        'COIN_BASE',lambda:_coin_research(self.root,'base'))
    out.setdefault('coin',{}).setdefault('ma',{})['research']=read(
        'COIN_MA',lambda:_coin_research(self.root,'ma'))
    return out

def _gmo_original_record(root):
    """Read the unmodified PC GMO SQLite + full JSON state without an engine restart.

    This is the same PC archive as the other strategies, not a second import.
    There is no synthetic execution or target result: missing evidence stays pending.
    """
    root = Path(root) / 'stock'
    state_file = root / 'PROJECTGMO_V1.0_account_state.json'
    db = root / 'PROJECTGMO_V1.0_research.sqlite3'
    if not state_file.is_file() or not db.is_file():
        return None
    state = json.loads(state_file.read_text(encoding='utf-8'))
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True, timeout=4) as conn:
        conn.row_factory = sqlite3.Row
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'gmo_candidates' not in tables:
            raise ValueError("GMO candidate source table missing")
        candidates = [dict(r) for r in conn.execute(
            "SELECT * FROM gmo_candidates ORDER BY event_date DESC,code LIMIT 3000")]
        full_count = conn.execute("SELECT COUNT(*) FROM gmo_candidates").fetchone()[0]
        candles = [dict(r) for r in conn.execute(
            "SELECT code,event_date,trade_date,day_index,high,final FROM gmo_daily_ohlc "
            "WHERE day_index BETWEEN 1 AND 15")] if 'gmo_daily_ohlc' in tables else []
    buys = state.get('buy_fills') or []
    sells = state.get('fills') or []
    positions = state.get('positions') or {}
    orders = state.get('orders') or {}
    signals = state.get('signals') or {}
    if not isinstance(positions, dict) or not isinstance(orders, dict):
        raise ValueError("GMO account state positions/orders schema invalid")
    names = {str(k): str(v.get('name') or k) for k,v in signals.items() if isinstance(v,dict)}
    # Opening price / last price are intentionally NOT manufactured.
    active = []
    for code,p in positions.items():
        if not isinstance(p,dict): continue
        qty = int(p.get('qty') or 0)
        if qty <= 0: continue
        active.append({'tf':'gmo','strategy':'gmo','code':code,
            'name':p.get('name') or names.get(code,code),'qty':qty,
            'entry_price':p.get('entry_price'),'last_price':None,
            'gmo_score':p.get('gmo_score'),'status':'OPEN',
            'first_sold':bool(p.get('first_filled_qty')),
            'reason':p.get('stage'),'gmo_event_date':p.get('entry_date')})
    pending = []
    for value in orders.values():
        if not isinstance(value,dict):continue
        if str(value.get('side','')).upper()!='BUY' or str(value.get('state','')).upper() in ('FILLED','CANCELED'):continue
        code = str(value.get('code') or '')
        pending.append({'tf':'gmo','strategy':'gmo','code':code,
            'name':names.get(code,code),'qty':max(0,int(value.get('requested') or 0)-int(value.get('filled') or 0)),
            'limit_price':value.get('limit'),'at':value.get('time'),
            'reason':value.get('purpose'),'status':value.get('state')})
    executions = []
    for kind,rows in (('SELL',sells),('BUY',buys)):
        for f in rows:
            if not isinstance(f,dict):continue
            code=str(f.get('code') or '')
            executions.append({'tf':'gmo','strategy':'gmo','code':code,
                'name':names.get(code,code),'side':kind,'ts':f.get('time'),
                'qty':f.get('qty'),'price':f.get('price'),
                'net_profit':f.get('net_profit') if kind=='SELL' else None,
                'fees':f.get('fees',f.get('fee')),'tax':f.get('tax'),
                'reason':f.get('purpose')})
    executions.sort(key=lambda x:str(x.get('ts') or ''),reverse=True)
    # Use exactly the EXE's original 15-session evidence and paper-fill
    # attribution. A candle HIGH is not a broker/paper SELL execution.
    from d_gmo_score_research_paper import research_rows
    def canonical_date(value):
        raw=str(value or '').strip()
        if len(raw)>=8 and raw[:8].isdigit() and (len(raw)==8 or raw[8] in 'T '):
            return raw[:4]+'-'+raw[4:6]+'-'+raw[6:8]
        return raw[:10]
    normalized=[dict(c,entry_date=canonical_date(c.get('entry_date')))
                for c in candidates]
    original_rows=research_rows(normalized,candles,buys,sells)
    items=[]
    for c,r in zip(candidates,original_rows):
        bought=bool(r.get('paper_bought'))
        if bought:
            # Only a confirmed first-target paper SELL fill proves success.
            outcome=('HIT' if bool(r.get('paper_first_target_filled')) else
                     'MISS' if r.get('paper_position_status')=='EXIT_SOLD' else
                     'PENDING')
        else:
            virtual_status=r.get('research_5pct_status')
            outcome=('HIT' if virtual_status=='TOUCHED_HIGH_NOT_FILL' else
                     'MISS' if virtual_status=='NOT_TOUCHED_15_FINAL_DAYS' else
                     'PENDING')
        opener=float(c.get('opening_price') or 0)
        items.append({
            'tf':'gmo','strategy':'gmo','code':c.get('code'),
            'name':c.get('name'),'at':c.get('saved_at'),
            'event_date':c.get('event_date'),'entry_date':c.get('entry_date'),
            'score':r.get('gmo_original_score'),'score_state':r.get('observation_quality'),
            'bought':bought,'entry_observed':bought or opener>0,
            'outcome':outcome,
            'target_hit':True if outcome=='HIT' else False if outcome=='MISS' else None,
            'reason':('실제 PAPER 매도 체결 기준' if bought else
                      '가상 D+1 공식시가 대비 5% · D+1~D+15 관측')+
                     ' · '+str(r.get('research_5pct_status') or '기록 미완료'),
            'entry_status':('ACTUAL_FILLED' if bought else
                           'VIRTUAL_OPEN_OBSERVED' if opener>0 else 'VIRTUAL_NOT_ENTERED'),
            'first_target_filled':r.get('paper_first_target_filled'),
            'paper_realized_net_won':r.get('paper_realized_net_won'),
            'research_5pct_status':r.get('research_5pct_status'),
            'research_first_hit_day':r.get('first_5pct_research_hit_day')
        })
    research = _research_record(items,items)
    audit = _research_source_audit(db,'gmo_candidates',
        ('gmo_flow','gmo_daily_ohlc','gmo_minute_ohlc','gmo_scan',
         'gmo_open','gmo_issues','gmo_policy_restorations'))
    research.update(audit)
    research.update({'load_status':audit['source_status'],'loaded_items':len(items),
                     'partial_window':full_count>len(items),'origin':'IMPORTED_PC_GMO_ORIGINAL'})
    wallet={'tf':'gmo','strategy':'gmo','seed':(state.get('cfg') or {}).get('seed'),
            'cash':state.get('cash'),'realized_pnl':state.get('realized'),
            'unrealized_pnl':None}
    ledger={'schema':'phone-v078','origin':'IMPORTED_PC_GMO_ORIGINAL',
            'accounts':[wallet],'positions':active,'pending':pending,
            'executions':executions,'realized_pnl':state.get('realized'),
            'unrealized_pnl':None,'positions_count':len(active),
            'pending_count':len(pending),'candidate_count':full_count,
            'load_diagnostic':{'stage':'PC_ORIGINAL_LOADED','files_present':[
                state_file.name,db.name]}}
    return ledger,research


def _capture_pc_research_evidence_async(self):
    """Observe every source SQLite change off the UI/broker thread.

    The EXE strategy DB remains the truth. This audit logs row versions without
    changing any entry/exit rule or modifying an original research table.
    """
    now=time.monotonic()
    if getattr(self,'_phone_evidence_capture_busy',False):
        return
    if now-float(getattr(self,'_phone_evidence_last_started',0) or 0)<90.0:
        return
    self._phone_evidence_capture_busy=True
    self._phone_evidence_last_started=now
    def work():
        try:
            from android_research_audit import capture
            result=capture(self.root)
            if result.get('source_errors'):
                try:
                    self._err('research_evidence_capture_partial',
                        RuntimeError(str(result['source_errors'])[:500]))
                except Exception:
                    pass
        except Exception as error:
            try:self._err('research_evidence_capture',error)
            except Exception:pass
        finally:
            self._phone_evidence_capture_busy=False
    threading.Thread(target=work,daemon=True,
        name='artitrade-research-evidence-audit').start()


def _hub_records_v078(self):
    _capture_pc_research_evidence_async(self)
    raw=_ORIG_HUB_RECORDS(self)
    try:
        out=json.loads(raw) if isinstance(raw,str) else raw
    except Exception:
        return raw
    try:
        stock_at=out.setdefault('stock',{}).setdefault('at',{})
        base=stock_at.setdefault('ledger',{})
        stock_at['ledger']=_stock_at_v19_full(self,base)
        ma=_stock_ma_full(self)
        stock_at['ledger']['ma_subledger']=ma
        stock_at['ledger']['ma']=ma
        out.setdefault('stock',{}).setdefault('cw',{})['ledger']=_close_full(self)
        if self.coin_runner:
            out.setdefault('coin',{}).setdefault('general',{})['ledger']=_coin_full(self.coin_runner.ledger)
            if getattr(self.coin_runner,'ma_ledger',None) is not None:
                out.setdefault('coin',{}).setdefault('ma',{})['ledger']=_coin_full(self.coin_runner.ma_ledger)
        _research_history(self,out)
        gmo=getattr(self.stock_runner,'gmo',None) if self.stock_runner else None
        if gmo is not None:
            snap=gmo.snapshot() or {}
            wallet=snap.get('wallet') or {}
            sells=[x for x in (snap.get('executions') or []) if str(x.get('side') or '').upper()=='SELL']
            wins=sum(1 for x in sells if float(x.get('net_profit') or 0)>0)
            ledger={
                'schema':'phone-v078','origin':'phone',
                'accounts':[{'tf':'gmo','strategy':'gmo','seed':wallet.get('seed',2_000_000),
                             'cash':wallet.get('cash',0),'realized_pnl':wallet.get('realized',0),
                             'unrealized_pnl':wallet.get('holding',0)}],
                'positions':snap.get('positions') or [],
                'pending':snap.get('pending') or [],
                'executions':snap.get('executions') or [],
                'realized_pnl':wallet.get('realized',0),
                'unrealized_pnl':wallet.get('holding',0),
                'total_pnl':float(wallet.get('realized') or 0)+float(wallet.get('holding') or 0),
                'win_rate':(wins*100.0/len(sells) if sells else None),
                'positions_count':len(snap.get('positions') or []),
                'pending_count':len(snap.get('pending') or []),
                'candidate_count':snap.get('candidate_count',0),
                'actual_target':{
                    'evaluated':int((gmo.research_dashboard().get('bought') or {}).get('evaluated') or 0),
                    'hit':int((gmo.research_dashboard().get('bought') or {}).get('touched') or 0),
                    'miss':int((gmo.research_dashboard().get('bought') or {}).get('failed') or 0),
                    'pending':int((gmo.research_dashboard().get('bought') or {}).get('pending') or 0),
                    'rate_pct':(gmo.research_dashboard().get('bought') or {}).get('rate_pct')
                },
                'restorations':snap.get('restorations') or [],
                'last_scan':snap.get('last_scan'),
                'last_error':snap.get('last_error')
            }
            research=gmo.research_dashboard() or {}
            items=[]
            for row in research.get('candidate_rows') or []:
                status=str(row.get('status') or '')
                items.append({
                    'at':row.get('event_date'),'event_date':row.get('event_date'),
                    'gmo_event_date':row.get('event_date'),'strategy':'gmo',
                    'code':row.get('code'),'name':row.get('name'),
                    'score':row.get('gmo_score_at_signal'),'bought':bool(row.get('bought')),
                    'target_hit': True if status=='TOUCHED' else False if status=='NOT_TOUCHED' else None,
                    'decision':status,
                    'reason':'1차 목표 '+str(row.get('first_exit_target_pct') if row.get('first_exit_target_pct') is not None else '—')+'% · '+status
                })
            stock=out.setdefault('stock',{}).setdefault('at',{})
            stock.setdefault('ledger',{})['gmo']=ledger
            gr=_research_record(items,items)
            gmo_audit=_research_source_audit(Path(self.root)/'stock'/
                'PROJECTGMO_V1.0_research.sqlite3','gmo_candidates',
                ('gmo_events','gmo_fills','gmo_position_history'))
            gr.update(gmo_audit)
            gr['load_status']='OK' if gmo_audit['source_status']=='OK' else gmo_audit['source_status']
            gr['loaded_items']=len(items)
            gr['partial_window']=gmo_audit['source_total']>len(items) if gmo_audit['source_total']>=0 else False
            gr['actual_summary']={
                'evaluated':int((research.get('bought') or {}).get('evaluated') or 0),
                'hit':int((research.get('bought') or {}).get('touched') or 0),
                'miss':int((research.get('bought') or {}).get('failed') or 0),
                'pending':int((research.get('bought') or {}).get('pending') or 0),
                'rate_pct':(research.get('bought') or {}).get('rate_pct')
            }
            gr['actual_rate_pct']=gr['actual_summary']['rate_pct']
            gr['insights']=research.get('insights') or []
            stock.setdefault('research',{})['gmo']=gr
            # If a running GMO engine publishes an empty snapshot while its
            # imported originals have records, read the originals directly.
            # Never synthesize orders, P&L, or modify the paper-state JSON.
            if not (ledger['positions'] or ledger['executions'] or gr.get('loaded_items')):
                try:
                    originals=_gmo_original_record(self.root)
                    if originals is not None:
                        source_ledger,source_research=originals
                        if (source_ledger['positions'] or source_ledger['executions']
                                or source_research.get('loaded_items')):
                            stock['ledger']['gmo']=source_ledger
                            stock['research']['gmo']=source_research
                except Exception as original_error:
                    self._err('gmo_imported_original_fallback',original_error)
        else:
            # The same PC import already holds the GMO originals. Display their
            # verified original state even if the live GMO service is not ready.
            try:
                restored=_gmo_original_record(self.root)
                if restored is not None:
                    source_ledger,source_research=restored
                    stock=out.setdefault('stock',{}).setdefault('at',{})
                    stock.setdefault('ledger',{})['gmo']=source_ledger
                    stock.setdefault('research',{})['gmo']=source_research
                else:
                    pass
            except Exception as gmo_exc:
                self._err('gmo_original_record',gmo_exc)
                restored=None
            if restored is not None:
                return json.dumps(out,ensure_ascii=False,default=str)
            # Do not report an empty GMO screen as a successful PC ledger load.
            # Read-only source-file diagnostics distinguish absent data from
            # a strategy engine which has not initialized its account state.
            gmo_root=Path(self.root)/'stock'
            present=[name for name in ('PROJECTGMO_V1.0_research.sqlite3',
                'PROJECTGMO_V1.0_account_state.json') if (gmo_root/name).is_file()]
            stock=out.setdefault('stock',{}).setdefault('at',{})
            stock.setdefault('ledger',{}).setdefault('gmo',{})
            stock['ledger']['gmo']['load_diagnostic']={
                'stage':'ENGINE_NOT_READY' if present else 'PC_SOURCE_NOT_PRESENT',
                'files_present':present,'files_expected':2,
                'message':'GMO original file present but its runtime is not ready'
                          if present else 'No GMO original files located in phone root'
            }
            research=stock.setdefault('research',{})
            if not isinstance(research.get('gmo'),dict):
                state=_research_source_audit(Path(self.root)/'stock'/
                    'PROJECTGMO_V1.0_research.sqlite3','gmo_candidates',
                    ('gmo_events','gmo_fills','gmo_position_history'))
                research['gmo']={'items':[],'score_items':[],'bands':[],
                    'origin':'IMPORTED_PC_AND_PHONE','load_status':'ENGINE_NOT_READY',
                    'loaded_items':0,**state}
    except Exception as exc:
        self._err('hub_records_v078',exc)
    return json.dumps(out,ensure_ascii=False,default=str)

def _stock_at_ledger_v19(self):
    out=_ORIG_STOCK_AT_LEDGER(self)
    if not self.stock_runner:
        return out
    try:
        store=self.stock_runner.store
        with store.lock,store.connect() as c:
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if 'v19_accounts' not in tables:
                return out
            try:
                wallets=self.stock_runner.engine.wallet_snapshot(c)
            except Exception:
                wallets={}
                try:
                    alloc={r['tf']:int(r['target_seed']) for r in c.execute('SELECT tf,target_seed FROM v19_seed_allocations')}
                except Exception:
                    alloc={}
                for r in c.execute('SELECT tf,cash,realized_pnl,seed FROM v19_accounts').fetchall():
                    wallets[str(r['tf'])]={
                        'tf':str(r['tf']),'cash':int(r['cash']),
                        'realized_pnl':int(r['realized_pnl']),
                        'seed':alloc.get(str(r['tf']),int(r['seed']))
                    }
        accounts=[]
        positions=out.get('positions') or []
        realized=0.0;holding=0.0
        for tf in ('5m','30m','60m','day'):
            w=wallets.get(tf) or {}
            held=sum(float(x.get('paper_pnl') or 0) for x in positions if str(x.get('tf') or '')==tf)
            rp=float(w.get('realized_pnl') or 0)
            accounts.append({
                'tf':tf,'strategy':'at','cash':w.get('cash',0),'seed':w.get('seed',2_000_000),
                'realized_pnl':rp,'unrealized_pnl':held
            })
            realized+=rp;holding+=held
        out['accounts']=accounts
        out['realized_pnl']=realized
        out['unrealized_pnl']=holding
        out['total_pnl']=realized+holding
    except Exception as exc:
        self._err('stock_at_v19_wallet_snapshot',exc)
    return out

def _patched_gmo(self):
    if not self.stock_runner:
        return _ORIG_PATCH_GMO(self)
    if not hasattr(self.stock_runner, 'gmo') or self.stock_runner.gmo is None:
        from d_gmo_runtime import GMOService
        gmo_root = Path(self.root) / 'stock'
        gmo = GMOService(gmo_root)
        self.stock_runner.gmo = gmo

        original_tick = self.stock_runner._new_tick
        def tick_with_gmo(tick):
            try:
                gmo.on_priority_tick(tick)
                gmo.on_live_tick(tick)
            except Exception as exc:
                self._err('gmo_tick', exc)
            return original_tick(tick)
        self.stock_runner._new_tick = tick_with_gmo

        original_book = self.stock_runner.engine.book.on_message
        def book_with_gmo(message, *args, **kwargs):
            try:
                gmo.on_book_message(message, self.stock_runner.market_map)
            except Exception as exc:
                self._err('gmo_book', exc)
            return original_book(message, *args, **kwargs)
        self.stock_runner.engine.book.on_message = book_with_gmo

        gmo.start(lambda: self.stock_runner.api,
                  lambda: dict(self.stock_runner.market_map))
    return _ORIG_PATCH_GMO(self)


def _patched_stop(self):
    try:
        gmo = getattr(self.stock_runner, 'gmo', None) if self.stock_runner else None
        if gmo is not None:
            gmo.stop()
    except Exception as exc:
        self._err('stop_gmo', exc)
    return _ORIG_STOP(self)


_core.UpbitBroker._jwt = _upbit_jwt_hs512
_core.Runtime._patch_gmo = _patched_gmo
_core.Runtime._stock_at_ledger = _stock_at_ledger_v19
_core.Runtime.hub_records = _hub_records_v078
_core.Runtime.stop = _patched_stop

def ledger_audit(manifest_json):
    """Read actual phone SQLite files with the same sqlite3 used by strategy engines.

    Read-only, no schema changes, no order execution. Unlike the Android system
    SQLite helper, this can open the exact DB format used by the bundled EXE.
    """
    import re,sqlite3
    try:
        manifest=json.loads(manifest_json) if isinstance(manifest_json,str) else manifest_json
        root=Path(getattr(getattr(_core,'_RUNTIME',None),'root',''))
        if not root.is_dir() or not isinstance(manifest,dict):
            raise RuntimeError('phone runtime or PC manifest missing')
        files=manifest.get('files') or {}
        if not files:raise RuntimeError('PC archive has no files')
        total_src=0;total_phone=0;checked=0;results={};problems=[]
        for rel,meta in files.items():
            if not isinstance(meta,dict):continue
            installed_rel='stock/close/AT_CLOSE_PAPER.sqlite3' if rel=='stock/AT_CLOSE/AT_CLOSE_PAPER.sqlite3' else rel
            file=root/installed_rel
            if not file.is_file():
                problems.append('DB_MISSING:'+rel);continue
            counts=meta.get('counts') or {}
            file_result={'path':installed_rel,'source_rows':0,'phone_rows':0,'tables_checked':0}
            if rel.endswith('.sqlite3'):
                connection=None
                try:
                    connection=sqlite3.connect('file:'+file.resolve().as_posix()+'?mode=ro',uri=True,timeout=10)
                    connection.execute('PRAGMA query_only=ON')
                    actual_tables={x[0] for x in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
                    for table,want in counts.items():
                        if not re.fullmatch('[A-Za-z0-9_]+',str(table)) or not isinstance(want,int) or want<0:
                            continue
                        if table not in actual_tables:
                            problems.append('TABLE_MISSING:'+rel+'/'+str(table));continue
                        if not any(k in table.lower() for k in ('position','execution','order','candidate',
                                                              'signal','shadow','research','event','trade','fill',
                                                              'history','restoration')):
                            continue
                        got=int(connection.execute('SELECT COUNT(*) FROM "'+table+'"').fetchone()[0])
                        file_result['source_rows']+=want
                        file_result['phone_rows']+=got
                        file_result['tables_checked']+=1
                        if got<want:
                            problems.append('ROW_SHORTFALL:'+rel+'/'+table+' pc='+str(want)+' phone='+str(got))
                except Exception as exc:
                    problems.append('SQLITE_READ_ERROR:'+rel+':'+type(exc).__name__+':'+str(exc)[:120])
                finally:
                    if connection is not None:connection.close()
            else:
                file_result['source_rows']=sum(v for k,v in counts.items() if isinstance(v,int)
                    and any(t in k.lower() for t in ('position','order','fill','trade')))
                file_result['phone_rows']=file_result['source_rows']
            results[rel]=file_result
            total_src+=file_result['source_rows'];total_phone+=file_result['phone_rows']
            checked+=1
        return json.dumps({'ok':not problems,'files_checked':checked,'source_rows':total_src,
            'phone_rows':total_phone,'files':results,'message':'; '.join(problems[:6]),
            'error_count':len(problems)},ensure_ascii=False)
    except Exception as exc:
        return json.dumps({'ok':False,'message':type(exc).__name__+': '+str(exc)[:240],
            'files_checked':0},ensure_ascii=False)

# Re-export the stable Android bridge API.
start = _core.start
stop = _core.stop
snapshot = _core.snapshot
hub_records = _core.hub_records
set_live = _core.set_live
