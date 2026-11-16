"""Android operating-account adapter for ARTITRADE.

Only operating capital and maximum-holding controls are changed here.
Strategy thresholds/state transitions are untouched.

Account structure follows the latest extracted EXEs exactly:
- stock AT/reference: 5m / 30m / 60m / day independent wallets
- stock MA: 5m / 30m / 60m / day independent accounts
- stock CLOSE: one account
- stock GMO: one account
- coin reference: 5m / 30m / 60m / day independent wallets
- coin MA: 5m / 30m / 60m / day independent wallets
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


def _now_kst():
    return datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds')


def _frame_defaults():
    return {tf:{'seed':DEFAULT_SEED,'max_positions':DEFAULT_MAX} for tf in FRAMES}


def _defaults():
    return {
      'stock':{
        'at':{'frames':_frame_defaults()},
        'ma':{'frames':_frame_defaults()},
        'close':{'seed':DEFAULT_SEED,'max_positions':DEFAULT_MAX},
        'gmo':{'seed':DEFAULT_SEED,'max_positions':DEFAULT_MAX},
      },
      'coin':{
        'general':{'frames':_frame_defaults()},
        'ma':{'frames':_frame_defaults()},
      },
    }


def _path(rt=None):
    r=rt or getattr(core,'_RUNTIME',None)
    base=Path(r.root if r is not None else Path.home()/'.artitrade_android')
    return base/'ANDROID_OPERATION_V074.json'


def _read_pair(raw, where):
    if not isinstance(raw,dict):raw={}
    try:
        seed=int(raw.get('seed',DEFAULT_SEED))
        maxp=int(raw.get('max_positions',DEFAULT_MAX))
    except (TypeError,ValueError) as exc:
        raise ValueError(where+' 운용값은 정수여야 합니다') from exc
    if not 10_000<=seed<=2_000_000_000:
        raise ValueError(where+' 시드는 1만원~20억원 범위여야 합니다')
    if not 1<=maxp<=100:
        raise ValueError(where+' 최대보유는 1~100 범위여야 합니다')
    return {'seed':seed,'max_positions':maxp}


def _frames(raw, where):
    # Migration: v0.7.4/v0.7.5 stored one seed/max pair for the whole strategy.
    if not isinstance(raw,dict):raw={}
    raw_frames=raw.get('frames')
    if not isinstance(raw_frames,dict):
        one=_read_pair(raw,where)
        return {tf:dict(one) for tf in FRAMES}
    return {tf:_read_pair(raw_frames.get(tf,{}),where+'/'+tf) for tf in FRAMES}


def _validate(data):
    if not isinstance(data,dict):data={}
    stock=data.get('stock',{}) if isinstance(data.get('stock',{}),dict) else {}
    coin=data.get('coin',{}) if isinstance(data.get('coin',{}),dict) else {}
    return {
      'stock':{
        'at':{'frames':_frames(stock.get('at',{}),'stock/at')},
        'ma':{'frames':_frames(stock.get('ma',{}),'stock/ma')},
        'close':_read_pair(stock.get('close',{}),'stock/close'),
        'gmo':_read_pair(stock.get('gmo',{}),'stock/gmo'),
      },
      'coin':{
        'general':{'frames':_frames(coin.get('general',{}),'coin/general')},
        'ma':{'frames':_frames(coin.get('ma',{}),'coin/ma')},
      },
    }


def _load(rt=None):
    p=_path(rt)
    if not p.exists():return _defaults()
    try:return _validate(json.loads(p.read_text(encoding='utf-8')))
    except Exception:return _defaults()


def _save(rt,data):
    checked=_validate(data);p=_path(rt);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix('.tmp')
    tmp.write_text(json.dumps(checked,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(tmp,p)
    return checked


def _apply_at(rt,cfg):
    import d_v19_portfolio as v19
    frames=cfg['frames']
    v19.MAX_POSITIONS_BY_TF={tf:int(frames[tf]['max_positions']) for tf in FRAMES}
    v19.MAX_SYMBOL_WON_BY_TF={
        tf:max(10_000,int(frames[tf]['seed'])//max(1,int(frames[tf]['max_positions'])))
        for tf in FRAMES
    }
    store=rt.stock_runner.store
    with store.lock,store.connect() as c:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if 'v19_accounts' not in tables:
            return {'applied':True,'wallet':'engine_constants_only','frames':frames}
        c.execute('''CREATE TABLE IF NOT EXISTS v19_seed_allocations(
           tf TEXT PRIMARY KEY,target_seed INTEGER NOT NULL,changed_at TEXT NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS v19_seed_adjustment_audit(
           id INTEGER PRIMARY KEY AUTOINCREMENT,tf TEXT NOT NULL,at TEXT NOT NULL,
           before_seed INTEGER NOT NULL,after_seed INTEGER NOT NULL,delta INTEGER NOT NULL,
           cash_before INTEGER NOT NULL,cash_after INTEGER NOT NULL,reason TEXT NOT NULL)''')
        rows={r['tf']:r for r in c.execute('SELECT tf,cash FROM v19_accounts').fetchall()}
        changes=[]
        for tf in FRAMES:
            r=rows.get(tf)
            if r is None:raise ValueError('기준봉 '+tf+' 계좌가 없습니다')
            old=c.execute('SELECT target_seed FROM v19_seed_allocations WHERE tf=?',(tf,)).fetchone()
            prev=int(old[0]) if old else DEFAULT_SEED
            target=int(frames[tf]['seed'])
            before=int(r['cash']);delta=target-prev;after=before+delta
            if after<0:raise ValueError('기준봉 '+tf+' 계좌 잔여현금 부족: 시드 감소 불가')
            changes.append((tf,prev,target,before,delta,after))
        now=_now_kst()
        for tf,prev,target,before,delta,after in changes:
            if delta:
                c.execute('UPDATE v19_accounts SET cash=?,last_update=? WHERE tf=?',(after,now,tf))
                c.execute('INSERT INTO v19_seed_adjustment_audit(tf,at,before_seed,after_seed,delta,cash_before,cash_after,reason) VALUES(?,?,?,?,?,?,?,?)',
                          (tf,now,prev,target,delta,before,after,'ANDROID_USER_SEED_CHANGE'))
            c.execute('INSERT INTO v19_seed_allocations(tf,target_seed,changed_at) VALUES(?,?,?) ON CONFLICT(tf) DO UPDATE SET target_seed=excluded.target_seed,changed_at=excluded.changed_at',
                      (tf,target,now))
    return {'applied':True,'frames':frames,'per_symbol_limit':dict(v19.MAX_SYMBOL_WON_BY_TF)}


def _apply_stock_ma(rt,cfg):
    frames=cfg['frames']
    values={}
    for tf in FRAMES:
        values[tf+'_seed']=int(frames[tf]['seed'])
        values[tf+'_max_positions']=int(frames[tf]['max_positions'])
    rt.stock_runner.ma.settings(values)
    return {'applied':True,'frames':frames}


def _apply_gmo(rt,cfg):
    gmo=rt.stock_runner.gmo;seed=int(cfg['seed']);maxp=int(cfg['max_positions'])
    with gmo._lock:
        eng=gmo.engine;delta=float(seed)-float(eng.cfg.seed)
        if float(eng.cash)+delta<-0.00001:
            raise ValueError('수급매매 잔여현금 부족: 시드 감소 불가')
        if abs(delta)>0.001:eng.cash+=delta
        eng.cfg=replace(eng.cfg,seed=float(seed),max_positions=maxp,fraction_per_position=1.0/maxp)
        gmo._persist(True)
    return {'applied':True,'seed':seed,'max_positions':maxp}


def _apply_close(rt,cfg):
    old=(rt.close_store.settings() or {}).get('CLOSE',{})
    seed=int(cfg['seed']);maxp=int(cfg['max_positions'])
    rt.close_store.update_setting('CLOSE',seed,maxp,max(10_000,seed//maxp),int(old.get('enabled',1)))
    return {'applied':True,'seed':seed,'max_positions':maxp}


def _apply_coin(ledger,cfg):
    frames=cfg['frames']
    payload={'frames':{
        tf:{'allocated_krw':int(frames[tf]['seed']),'max_positions':int(frames[tf]['max_positions'])}
        for tf in FRAMES
    }}
    ledger.save_allocation(payload)
    return {'applied':True,'frames':frames}


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
            result['ok']=False
            result['strategies'][name]={'applied':False,'error':type(exc).__name__+': '+str(exc)[:240]}
    return result


def apply_saved():
    rt=getattr(core,'_RUNTIME',None)
    if rt is None:return json.dumps({'ok':False,'error':'RUNTIME_NOT_STARTED'},ensure_ascii=False)
    data=_load(rt)
    # Persist migrated schema on first successful startup.
    _save(rt,data)
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
    return json.dumps({'ok':True,'schema':'PER_ACCOUNT_V076','settings':data,'runtime_started':rt is not None},ensure_ascii=False)
