"""Android v0.7.5 runtime compatibility layer.

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


def _hub_records_v075(self):
    raw=_ORIG_HUB_RECORDS(self)
    try:
        out=json.loads(raw) if isinstance(raw,str) else raw
    except Exception:
        return raw
    try:
        gmo=getattr(self.stock_runner,'gmo',None) if self.stock_runner else None
        if gmo is not None:
            snap=gmo.snapshot() or {}
            wallet=snap.get('wallet') or {}
            sells=[x for x in (snap.get('executions') or []) if str(x.get('side') or '').upper()=='SELL']
            wins=sum(1 for x in sells if float(x.get('net_profit') or 0)>0)
            ledger={
                'schema':'phone-v075','origin':'phone',
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
        self._err('hub_records_gmo_v075',exc)
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
_core.Runtime.hub_records = _hub_records_v075
_core.Runtime.stop = _patched_stop

# Re-export the stable Android bridge API.
start = _core.start
stop = _core.stop
snapshot = _core.snapshot
hub_records = _core.hub_records
set_live = _core.set_live
