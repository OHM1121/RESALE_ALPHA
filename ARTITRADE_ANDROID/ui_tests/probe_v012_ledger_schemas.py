"""Read-only generated TEST-wallet schema probe; never opens user data."""
import datetime as dt
import json, tempfile
from pathlib import Path
from types import SimpleNamespace
import reference_1118_runtime as ref
import ma_v067_stock_runtime as hs
import ma_v067_coin_runtime as hc
import market_trend_1118_runtime as trend
from d_storage import DStore
from d_v19_portfolio import D19PaperEngine
from d_upbit import CoinLedger
from coin_ma_engine import MACoinLedger
from stock_ma_engine import StockMAService
from market_trend_wallet_v054 import TrendWalletV054

ref.install();hs.install();hc.install();trend.install()
def inspect(label,c):
    tables=[a[0] for a in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    found={}
    for name in tables:
        if name.startswith('sqlite_'):continue
        cols=[(r[1],r[2]) for r in c.execute('PRAGMA table_info("'+name+'")')]
        found[name]=cols
    for name in ('accounts','v19_accounts','wallets','balances','frame_accounts',
                 'v19_seed_allocations'):
        if name in tables:
            print('V012_ACCOUNT_SAMPLE '+label+' '+name+' '+
                json.dumps([dict(row) for row in c.execute(
                    'SELECT * FROM "'+name+'" LIMIT 8')],sort_keys=True,default=str),flush=True)
    print('V012_SCHEMA '+label+' '+json.dumps(found,ensure_ascii=True,sort_keys=True),flush=True)
native_root=Path('/tmp/artitrade012_native')
for name in ('d_engine.py','d_v19_portfolio.py','d_upbit.py','stock_ma_engine.py'):
    f=native_root/name
    if not f.is_file():continue
    lines=f.read_text('utf8').splitlines()
    idxs=[i for i,line in enumerate(lines) if (
        'UPDATE v19_accounts' in line or 'UPDATE wallets' in line or
        'UPDATE accounts' in line or 'realized_pnl=realized_pnl' in line or
        'realized=realized' in line)]
    for i in idxs[:20]:
        print('V012_WALLET_ACCOUNT_SQL '+name+':'+str(i+1)+' '+json.dumps(
            lines[max(0,i-8):min(len(lines),i+14)]),flush=True)
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)
    stock=DStore(root/'stock')
    D19PaperEngine(stock)
    with stock.connect() as c:inspect('STOCK_AT',c)
    base=CoinLedger(root/'coin'/'base')
    with base.connect() as c:inspect('COIN_AT',c)
    m=MACoinLedger(root/'coin'/'ma')
    with m.connect() as c:inspect('COIN_MA',c)
    w=StockMAService(root/'stock',stock)
    import stock_ma_engine as native
    with native._connect(w.db) as c:inspect('STOCK_MA',c)
    rt=SimpleNamespace(root=root,stock_runner=None,coin_runner=None,live_enabled=lambda x:False)
    t=TrendWalletV054(rt)
    for market in ('stock','coin'):
        with t._connect(market) as c:inspect(market.upper()+'_TREND',c)
    print('V012_ROOT_PATHS '+json.dumps(sorted(str(p.relative_to(root)) for p in root.rglob('*.sqlite3'))),flush=True)
