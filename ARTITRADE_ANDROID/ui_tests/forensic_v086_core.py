"""Read exact bundled original EXE 82-module core without altering runtime or user data."""
import ast,base64,io,zipfile,hashlib
from pathlib import Path
p=Path('ARTITRADE_ANDROID/app/src/main/assets/strategy_v070')
blob=base64.b64decode(''.join((p/f'part_{i:02d}.txt').read_text('ascii') for i in range(22)))
with zipfile.ZipFile(io.BytesIO(blob)) as z:
    assert z.testzip() is None
    names=z.namelist()
    print('ORIGINAL_MODULES',len(names))
    for file in ('d_live.py','stock_ma_engine.py','d_upbit.py','coin_ma_engine.py',
                 'd_gmo_runtime.py','at_close_bridge.py','android_live_bridge.py',
                 'd_engine.py','d_storage.py','d_stock_v19.py','d_coin.py','d_gmo.py'):
        if file not in names:continue
        raw=z.read(file)
        src=raw.decode()
        print('SOURCE_SHA256',file,hashlib.sha256(raw).hexdigest())
        lines=src.splitlines()
        tree=ast.parse(src)
        print('\nFILE',file,'LINES',len(lines))
        for node in tree.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                if any(key in node.name.lower() for key in ('sell','exit','tick','book','ws','watch','subscr','snapshot','quote','reconnect','manual')):
                    print('FUNCTION',node.name,node.lineno,node.end_lineno)
            elif isinstance(node,ast.ClassDef):
                print('CLASS',node.name,node.lineno)
                if file=='d_storage.py' and node.name=='DStore':
                    for method in node.body:
                        if isinstance(method,ast.FunctionDef) and method.name in ('__init__','connect','_init','_setup','_schema'):
                            for lineno in range(method.lineno-1,min(method.end_lineno,method.lineno+80)):
                                print(f'INTERNAL_DSTORE {lineno+1:4d}: {lines[lineno]}')
                methods=[n for n in node.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))]
                for n in methods:
                    if any(k in n.name.lower() for k in ('sell','exit','tick','book','ws','watch','subscr','snapshot','quote','order','match','run','feed','send','recv','manual')):
                        print('METHOD',node.name+'.'+n.name,n.lineno,n.end_lineno)
                        if file in ('d_live.py','stock_ma_engine.py','d_upbit.py','coin_ma_engine.py',
                                    'd_gmo_runtime.py') and n.name in (
                                  '_tick_job_loop','_gmo_priority_tick_job_loop','_new_tick','_pinned_stocks',
                                  '_ws','_run_ws','_sell','_exit','on_tick',
                                  '_desired_subscriptions','_register_subscriptions',
                                  '_v071_feed_worker','_v071_enqueue_feed',
                                  'process_book','_process_exits','manual_sell',
                                  '_ws_loop','_refresh_subscriptions','_dispatch',
                                  'paper_match','paper_match_krx_print_proxy',
                                  '_minute_exits','_minute_exits_threeleg',
                                  'process_realtime_tick','_refresh_watched',
                                  '_both_watched_codes','watched_codes',
                                  '_on_book','_on_trade','_on_tick',
                                  '_gmo_priority_tick_job_loop'):
                            show=lines[n.lineno-1:min(n.end_lineno,n.lineno+165)]
                            print('--- SOURCE '+node.name+'.'+n.name+' ---')
                            for idx,line in enumerate(show,n.lineno):print(f'{idx:4d}: {line}')
        if file in ('d_live.py','d_upbit.py'):
            for idx,line in enumerate(lines):
                if any(s in line for s in (
                    '_tick_job_loop(','.watched_codes(', 'MA_', '0B','0D',
                    'on_tick(', 'process_book(', 'stock_quote',
                    'last_book','_v083_refresh_subscriptions','_ma',
                    'paper_match(','.subscriptions','tracked','_both_watched_codes')):
                    if line.lstrip().startswith('#'):continue
                    print('MATCH',idx+1,line[:170])
