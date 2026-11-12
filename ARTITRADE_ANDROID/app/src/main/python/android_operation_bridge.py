"""Android operating-account adapter for ARTITRADE.

Operational capital/slot controls only. Strategy thresholds and transitions are
untouched. Defaults follow the user's phone-main requirement: 2,000,000 KRW
seed and 4 maximum holdings for every strategy account.
"""
from __future__ import annotations
import json, os
from datetime import datetime, timezone, timedelta
from dataclasses import replace
from pathlib import Path

import android_live_bridge as core

FRAMES=('5m','30m','60m','day')
DEFAULT_SEED=2_000_000
DEFAULT_MAX=4
STRATEGIES={'stock':('at','ma','close','gmo'),'coin':('general','ma')}

def _now_kst():
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds')


def _defaults():
    return {m:{s:{'seed':DEFAULT_SEED,'max_positions':DEFAULT_MAX} for s in ss}
            for m,ss in STRATEGIES.items()}


def _path(rt=None):
    r=rt or getattr(core,'_RUNTIME',None)
    base=Path(r.root if r is not None else Path.home()/'.artitrade_android')
    return base/'ANDROID_OPERATION_V074.json'


def _validate(data):
    out=_defaults()
    if not isinstance(data,dict):data={}
    for market,strategies in STRATEGIES.items():
        rawm=data.get(market,{}) if isinstance(data.get(market,{}),dict) else {}
        for strategy in strategies:
            raw=rawm.get(strategy,{}) if isinstance(rawm.get(strategy,{}),dict) else {}
            seed=int(raw.get('seed',DEFAULT_SEED));maxp=int(raw.get('max_positions',DEFAULT_MAX))
            if not 10_000<=seed<=2_000_000_000:raise ValueError(f'{market}/{strategy} seed out of range')
            if not 1<=maxp<=100:raise ValueError(f'{market}/{strategy} max_positions out of range')
            out[market][strategy]={'seed':seed,'max_positions':maxp}
    return out


def _load(rt=None):
    p=_path(rt)
    if not p.exists():return _defaults()
    try:return _validate(json.loads(p.read_text(encoding='utf-8')))
    except Exception:return _defaults()


def _save(rt,data):
    checked=_validate(data);p=_path(rt);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(checked,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,p)
    return checked


def _apply_at(rt,cfg):
    import d_v19_portfolio as v19
    seed=int(cfg['seed']);maxp=int(cfg['max_positions'])
    # Latest EXE keeps v19_accounts.seed physically fixed at 2m with a CHECK.
    # User operating capital is tracked separately in v19_seed_allocations and
    # applied as a cash delta. Strategy thresholds/state transitions are untouched.
    v19.MAX_POSITIONS=maxp
    v19.MAX_POSITIONS_BY_TF={f:maxp for f in FRAMES}
    per_symbol=max(10_000,seed//maxp)
    v19.MAX_SYMBOL_WON=per_symbol
    v19.MAX_SYMBOL_WON_BY_TF={f:per_symbol for f in FRAMES}
    store=rt.stock_runner.store
    with store.lock,store.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'v19_accounts' not in tables:return {'applied':True,'wallet':'engine_constants_only','per_symbol_limit':per_symbol}
        c.execute('''CREATE TABLE IF NOT EXISTS v19_seed_allocations(
           tf TEXT PRIMARY KEY,target_seed INTEGER NOT NULL,changed_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS v19_seed_adjustment_audit(
           id INTEGER PRIMARY KEY AUTOINCREMENT,tf TEXT NOT NULL,at TEXT NOT NULL,
           before_seed INTEGER NOT NULL,after_seed INTEGER NOT NULL,delta INTEGER NOT NULL,
           cash_before INTEGER NOT NULL,cash_after INTEGER NOT NULL,reason TEXT NOT NULL)''')
        rows=c.execute('SELECT tf,cash FROM v19_accounts').fetchall()
        changes=[]
        for r in rows:
            old=c.execute('SELECT target_seed FROM v19_seed_allocations WHERE tf=?',(r['tf'],)).fetchone()
            prev=int(old[0]) if old else DEFAULT_SEED
            before=int(r['cash']);delta=seed-prev;after=before+delta
            if after<0:raise ValueError(str(r['tf'])+' 계좌 잔여현금 부족: 시드 감소 불가')
            changes.append((r['tf'],prev,before,delta,after))
        now=_now_kst()
        for tf,prev,before,delta,after in changes:
            if delta:
                c.execute('UPDATE v19_accounts SET cash=?,last_update=? WHERE tf=?',(after,now,tf))
                c.execute('INSERT INTO v19_seed_adjustment_audit(tf,at,before_seed,after_seed,delta,cash_before,cash_after,reason) VALUES(?,?,?,?,?,?,?,?)',
                          (tf,now,prev,seed,delta,before,after,'ANDROID_USER_SEED_CHANGE'))
            c.execute('INSERT INTO v19_seed_allocations(tf,target_seed,changed_at) VALUES(?,?,?) ON CONFLICT(tf) DO UPDATE SET target_seed=excluded.target_seed,changed_at=excluded.changed_at',
                      (tf,seed,now))
    return {'applied':True,'per_symbol_limit':per_symbol}


def _apply_stock_ma(rt,cfg):
    values={}
    for f in FRAMES:
        values[f+'_seed']=int(cfg['seed']);values[f+'_max_positions']=int(cfg['max_positions'])
    rt.stock_runner.ma.settings(values)
    return {'applied':True}


def _apply_gmo(rt,cfg):
    gmo=rt.stock_runner.gmo;seed=int(cfg['seed']);maxp=int(cfg['max_positions'])
    with gmo._lock:
        eng=gmo.engine;delta=float(seed)-float(eng.cfg.seed)
        if float(eng.cash)+delta<-0.00001:raise ValueError('수급매매 잔여현금 부족: 시드 감소 불가')
        if abs(delta)>0.001:eng.cash+=delta
        eng.cfg=replace(eng.cfg,seed=float(seed),max_positions=maxp,fraction_per_position=1.0/maxp)
        gmo._persist(True)
    return {'applied':True}


def _apply_close(rt,cfg):
    old=(rt.close_store.settings() or {}).get('CLOSE',{})
    seed=int(cfg['seed']);maxp=int(cfg['max_positions'])
    rt.close_store.update_setting('CLOSE',seed,maxp,max(10_000,seed//maxp),int(old.get('enabled',1)))
    return {'applied':True}


def _apply_coin(ledger,cfg):
    frames={f:{'allocated_krw':int(cfg['seed']),'max_positions':int(cfg['max_positions'])} for f in FRAMES}
    ledger.save_allocation({'frames':frames})
    return {'applied':True}


def _apply_runtime(rt,data):
    result={'ok':True,'strategies':{}}
    jobs=[
      ('stock.at',lambda:_apply_at(rt,data['stock']['at'])),
      ('stock.ma',lambda:_apply_stock_ma(rt,data['stock']['ma'])),
      ('stock.close',lambda:_apply_close(rt,data['stock']['close'])),
      ('stock.gmo',lambda:_apply_gmo(rt,data['stock']['gmo'])),
      ('coin.general',lambda:_apply_coin(rt.coin_ledger,data['coin']['general'])),
      ('coin.ma',lambda:_apply_coin(rt.coin_ma_ledger,data['coin']['ma'])),
    ]
    for name,fn in jobs:
        try:result['strategies'][name]=fn()
        except Exception as exc:
            result['ok']=False;result['strategies'][name]={'applied':False,'error':type(exc).__name__+': '+str(exc)[:240]}
    return result


def apply_saved():
    rt=getattr(core,'_RUNTIME',None)
    if rt is None:return json.dumps({'ok':False,'error':'RUNTIME_NOT_STARTED'},ensure_ascii=False)
    data=_load(rt)
    if not _path(rt).exists():_save(rt,data)
    out=_apply_runtime(rt,data);out['settings']=data
    return json.dumps(out,ensure_ascii=False,default=str)


def apply(raw):
    rt=getattr(core,'_RUNTIME',None)
    if rt is None:return json.dumps({'ok':False,'error':'RUNTIME_NOT_STARTED'},ensure_ascii=False)
    data=_save(rt,json.loads(str(raw) or '{}'))
    out=_apply_runtime(rt,data);out['settings']=data
    return json.dumps(out,ensure_ascii=False,default=str)


def snapshot():
    rt=getattr(core,'_RUNTIME',None)
    data=_load(rt)
    return json.dumps({'ok':True,'settings':data,'runtime_started':rt is not None},ensure_ascii=False)
