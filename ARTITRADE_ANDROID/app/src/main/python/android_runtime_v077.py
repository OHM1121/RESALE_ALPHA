"""Android v0.7.7 runtime compatibility layer.

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
        executions=[dict(r) for r in c.execute("""SELECT e.* FROM executions e WHERE
              EXISTS(SELECT 1 FROM v19_members m WHERE m.position_id=e.position_id) OR
              EXISTS(SELECT 1 FROM v19_legacy_members l WHERE l.position_id=e.position_id)
              ORDER BY e.id DESC LIMIT 2000""").fetchall()]
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
            performance[tf]={
                'realized_won':round(realized),'holding_won':round(hold),
                'total_trades':bought,'bought_count':bought,'open_count':open_count,
                'closed_count':closed,'pending_count':pending_count
            }
            accounts.append({'tf':tf,'strategy':'at','cash':w.get('cash',0),
                'seed':w.get('seed',2_000_000),'realized_pnl':round(realized),
                'unrealized_pnl':round(hold)})
            total_real+=realized;total_hold+=hold
    base.update({'schema':'phone-v077','origin':'phone','accounts':accounts,'positions':positions,
        'pending':pending,'executions':executions,'performance_by_frame':performance,
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
    realized=sum(float((perf.get(tf) or {}).get('realized_won') or 0) for tf in _FRAMES)
    holding=sum(float((perf.get(tf) or {}).get('holding_won') or 0) for tf in _FRAMES)
    accounts=[]
    wallets=snap.get('wallets') or {}
    for tf in _FRAMES:
        w=wallets.get(tf) or {}
        accounts.append({'tf':tf,'strategy':'ma','cash':w.get('cash',0),'seed':w.get('seed',2_000_000),
                         'realized_pnl':(perf.get(tf) or {}).get('realized_won',0),
                         'unrealized_pnl':(perf.get(tf) or {}).get('holding_won',0),
                         'max_positions':w.get('max_positions',4)})
    out=dict(snap)
    out.update({'schema':'phone-v077','origin':'phone','accounts':accounts,
                'realized_pnl':round(realized),'unrealized_pnl':round(holding),
                'total_pnl':round(realized+holding),
                'positions_count':len(snap.get('positions') or []),
                'pending_count':len(snap.get('pending') or [])})
    sells=[x for x in (snap.get('executions') or []) if str(x.get('side') or '').upper()=='SELL']
    out['win_rate']=(sum(1 for x in sells if float(x.get('net_profit') or 0)>0)*100.0/len(sells)) if sells else None
    return out


def _coin_full(ledger):
    snap=ledger.snapshot() or {}
    positions=[];pending=[];executions=[];accounts=[];performance={}
    with ledger.lock,ledger.connect() as c:
        for tf in _FRAMES:
            frame=snap.get(tf) or {}
            pos=frame.get('positions') or [];orders=frame.get('orders') or [];exe=frame.get('executions') or []
            positions.extend(pos);pending.extend(orders);executions.extend(exe)
            bought=int(c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status IN ('OPEN','CLOSED')",(tf,)).fetchone()[0])
            open_count=int(c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status='OPEN' AND qty!='0'",(tf,)).fetchone()[0])
            closed=int(c.execute("SELECT COUNT(*) FROM positions WHERE tf=? AND status='CLOSED'",(tf,)).fetchone()[0])
            pending_count=int(c.execute("SELECT COUNT(*) FROM orders WHERE tf=? AND status='PENDING'",(tf,)).fetchone()[0])
            realized=float(frame.get('realized') or 0);holding=float(frame.get('holding') or 0)
            performance[tf]={'realized_won':round(realized),'holding_won':round(holding),
                'total_trades':bought,'bought_count':bought,'open_count':open_count,
                'closed_count':closed,'pending_count':pending_count}
            accounts.append({'tf':tf,'cash':frame.get('cash',0),'seed':frame.get('seed',2_000_000),
                'realized_pnl':round(realized),'unrealized_pnl':round(holding),
                'max_positions':frame.get('max_positions',4)})
    realized=sum(float(x.get('realized_pnl') or 0) for x in accounts)
    holding=sum(float(x.get('unrealized_pnl') or 0) for x in accounts)
    for e in executions:
        e['net_profit']=e.get('net_pnl');e['strategy']=e.get('tf')
    sells=[x for x in executions if str(x.get('side') or '').upper()=='SELL']
    return {'schema':'phone-v077','origin':'phone','accounts':accounts,'positions':positions,
            'pending':pending,'executions':executions,'performance_by_frame':performance,
            'realized_pnl':round(realized),'unrealized_pnl':round(holding),
            'total_pnl':round(realized+holding),
            'win_rate':(sum(1 for x in sells if float(x.get('net_pnl') or 0)>0)*100.0/len(sells) if sells else None),
            'positions_count':len(positions),'pending_count':len(pending)}


def _research_history(self, out):
    try:
        stockdb=Path(self.root)/'stock'/'ARTITRADE_D.sqlite3'
        madb=Path(self.root)/'stock'/'STOCK_MA_ENGINE_V1'/'ma_paper.sqlite3'
        closedb=Path(self.root)/'stock'/'AT_CLOSE'/'AT_CLOSE_PAPER.sqlite3'
        cdb=Path(self.root)/'coin'/'base'/'upbit_paper.sqlite3'
        cmdb=Path(self.root)/'coin'/'ma'/'upbit_paper.sqlite3'
        at=_core._db_rows(stockdb,"SELECT ts as at,tf as strategy,code,name,ref_ts,score,decision,features as reason FROM signals ORDER BY id DESC LIMIT 2000")
        ma=_core._db_rows(madb,"SELECT ts as at,tf as strategy,code,ref_ts,verdict as decision,detail as reason FROM research ORDER BY id DESC LIMIT 2000")
        close=_core._db_rows(closedb,"SELECT ts as at,strategy,code,name,decision,feature_json as reason FROM research_candidates ORDER BY id DESC LIMIT 2000")
        coin=_core._db_rows(cdb,"SELECT created_at as at,tf as strategy,market,name,ref_at as ref_ts,score,reason FROM candidates ORDER BY id DESC LIMIT 2000")
        coinma=_core._db_rows(cmdb,"SELECT created_at as at,tf as strategy,market,name,ref_at as ref_ts,score,reason FROM candidates ORDER BY id DESC LIMIT 2000")
        for x in ma:x['strategy']='MA'
        for x in close:x['strategy']='CLOSE'
        out.setdefault('stock',{}).setdefault('at',{}).setdefault('research',{})['items']=at
        out['stock']['at']['research']['ma']={'items':ma}
        out.setdefault('stock',{}).setdefault('cw',{}).setdefault('research',{})['items']=close
        out.setdefault('coin',{}).setdefault('general',{}).setdefault('research',{})['items']=coin
        out.setdefault('coin',{}).setdefault('ma',{}).setdefault('research',{})['items']=coinma
    except Exception as exc:
        self._err('research_history_v077',exc)
    return out


def _hub_records_v077(self):
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
                'schema':'phone-v077','origin':'phone',
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
            stock.setdefault('research',{})['gmo']={
                'items':items,
                'target_hit_rate':(research.get('bought') or {}).get('rate_pct'),
                'candidate_count':(research.get('all') or {}).get('signals',len(items)),
                'bought_count':(research.get('bought') or {}).get('signals',0),
                'evaluated':(research.get('bought') or {}).get('evaluated',0),
                'insights':research.get('insights') or []
            }
    except Exception as exc:
        self._err('hub_records_v077',exc)
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
        gmo_root = Path(self.root) / 'stock' / 'gmo'
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
_core.Runtime.hub_records = _hub_records_v077
_core.Runtime.stop = _patched_stop

# Re-export the stable Android bridge API.
start = _core.start
stop = _core.stop
snapshot = _core.snapshot
hub_records = _core.hub_records
set_live = _core.set_live
