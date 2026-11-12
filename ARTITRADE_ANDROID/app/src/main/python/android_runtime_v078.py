"""Android v0.7.8 runtime compatibility layer.

Does not alter strategy thresholds/state machines. It wires the latest extracted
GMO service into the same Kiwoom 0B/0D stream before the live-order adapter
patches the GMO paper-fill transition.
"""
from pathlib import Path
import hashlib, hmac, json, uuid
from urllib.parse import urlencode
import android_live_bridge as _core

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
    items=[]
    for x in candidates:
        key=(str(x.get('tf')),str(x.get('code')),str(x.get('ref_ts')))
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
            'reason':outcome})
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
            'entry_status':'ACTUAL_FILLED' if bought else 'VIRTUAL_NOT_REPLAYED','reason':x.get('status')})
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
    db=Path(self.root)/'stock'/'AT_CLOSE'/'AT_CLOSE_PAPER.sqlite3'
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
    sells=[x for x in executions if str(x.get('side') or '').upper()=='SELL']
    target=_target_summary(all_positions,'close')
    bought=len(all_positions);closed=sum(1 for x in all_positions if str(x.get('status') or '').upper()=='CLOSED')
    performance={'close':{'realized_won':round(realized),'holding_won':round(unreal),'total_trades':bought,
        'bought_count':bought,'open_count':len(positions),'closed_count':closed,'pending_count':len(pending),
        'actual_target_rate_pct':target['rate_pct'],'actual_target_evaluated':target['evaluated'],
        'actual_target_hit':target['hit'],'actual_target_miss':target['miss'],'actual_target_pending':target['pending']}}
    return {'schema':'phone-v078','origin':'phone','accounts':wallets,'positions':positions,'pending':pending,'executions':executions,
        'performance_by_frame':performance,'actual_target':target,'realized_pnl':round(realized),'unrealized_pnl':round(unreal),
        'total_pnl':round(realized+unreal),'win_rate':(sum(1 for x in sells if float(x.get('net_realized') or 0)>0)*100/len(sells) if sells else None),
        'positions_count':len(positions),'pending_count':len(pending)}


def _close_research(root):
    db=Path(root)/'stock'/'AT_CLOSE'/'AT_CLOSE_PAPER.sqlite3'
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
    sells=[x for x in executions if str(x.get('side') or '').upper()=='SELL' and x.get('net_profit') is not None]
    base['win_rate']=(sum(1 for x in sells if float(x.get('net_profit') or 0)>0)*100.0/len(sells)) if sells else None
    return base


def _stock_ma_full(self):
    if not self.stock_runner:
        return {}
    snap=self.stock_runner.ma.snapshot() or {}
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
    sells=[x for x in full_exec if str(x.get('side') or '').upper()=='SELL']
    out['win_rate']=(sum(1 for x in sells if float(x.get('net_profit') or 0)>0)*100.0/len(sells)) if sells else None
    return out

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
    for x in positions:
        x['ref_ts']=x.get('ref_at');x['entry_price']=x.get('entry');x['last_price']=x.get('mark')
    for x in pending:
        x['ref_ts']=x.get('ref_at');x['strategy']=x.get('tf')
    for e in executions:
        e['net_profit']=e.get('net_pnl');e['strategy']=e.get('tf')
    realized=sum(float(x.get('realized_pnl') or 0) for x in accounts)
    holding=sum(float(x.get('unrealized_pnl') or 0) for x in accounts)
    sells=[x for x in executions if str(x.get('side') or '').upper()=='SELL']
    return {'schema':'phone-v078','origin':'phone','accounts':accounts,'positions':positions,
            'pending':pending,'executions':executions,'performance_by_frame':performance,
            'actual_target':_target_summary(all_positions,'coin'),
            'realized_pnl':round(realized),'unrealized_pnl':round(holding),
            'total_pnl':round(realized+holding),
            'win_rate':(sum(1 for x in sells if float(x.get('net_pnl') or 0)>0)*100.0/len(sells) if sells else None),
            'positions_count':len(positions),'pending_count':len(pending)}

def _research_history(self, out):
    try:
        out.setdefault('stock',{}).setdefault('at',{})['research']=_stock_at_research(self.root)
        out['stock']['at']['research']['ma']=_stock_ma_research(self.root)
        out.setdefault('stock',{}).setdefault('cw',{})['research']=_close_research(self.root)
        out.setdefault('coin',{}).setdefault('general',{})['research']=_coin_research(self.root,'base')
        out.setdefault('coin',{}).setdefault('ma',{})['research']=_coin_research(self.root,'ma')
    except Exception as exc:
        self._err('research_history_v078',exc)
    return out

def _hub_records_v078(self):
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

# Re-export the stable Android bridge API.
start = _core.start
stop = _core.stop
snapshot = _core.snapshot
hub_records = _core.hub_records
set_live = _core.set_live
