"""Read exact Android-bundled 82 original strategy modules with method context.

This runs only in isolated GitHub CI: no source edits, Android data, API key,
network quote, trading order or broker session.
"""
import ast,base64,io,zipfile
from pathlib import Path
path=Path('ARTITRADE_ANDROID/app/src/main/assets/strategy_v070')
data=base64.b64decode(''.join((path/f'part_{n:02}.txt').read_text('ascii') for n in range(22)))
with zipfile.ZipFile(io.BytesIO(data)) as z:
    assert z.testzip() is None and len(z.namelist())==82
    print('CLOSE_MODULES',[n for n in z.namelist() if 'close' in n.lower() or 'cw' in n.lower() or 'gmo' in n.lower()])
    files={
      'd_engine.py':{'manual_sell','_sell','_minute_exits_threeleg','process_realtime_tick'},
      'd_live.py':{'latest_krx_trade','latest_trade','_tick_job_loop',
                    '_new_tick','_desired_subscriptions','_register_subscriptions',
                    '_run_ws','_refresh_universe_if_due','_pinned_stocks'},
      'at_close_bridge.py':None,
      'at_close_engine.py':None,
      'at_close_store.py':None,
      'at_close_runner.py':None,
      'd_close.py':None,
      'close_betting_engine.py':None,
      'cw_runtime.py':None,
      'd_gmo_runtime.py':{'_exit','paper_match','paper_match_krx_print_proxy',
                            'on_live_tick','on_book_message','on_priority_tick','fill','_order','mark_submitted','_persist','_manual_sell','_sell'},
      'market_trend_v043.py':{'sell','action'},
      'stock_ma_engine.py':{'_sell','on_tick'},
    }
    for file,methods in files.items():
        if file not in z.namelist():continue
        src=z.read(file).decode('utf-8')
        lines=src.splitlines()
        print('SOURCE_FILE',file,len(lines),flush=True)
        if file in ('at_close_engine.py','at_close_store.py','at_close_runner.py'):
            for n,line in enumerate(lines[:125],1):print('CLOSEFULL',file,n,line,flush=True)
        tree=ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                parent='';
                if methods is None:
                    if any(q in node.name.lower() for q in ('sell','close','exit','manual','pinned','broker','stock','fill','order','wallet','connect','persist','position','snapshot')):
                        pass
                    else:continue
                elif node.name not in methods:continue
                print(f'===== {file}:{node.lineno}-{node.end_lineno} {node.name} =====',flush=True)
                for line_number in range(node.lineno,min(node.end_lineno,node.lineno+155)+1):
                    print(f'{line_number}: {lines[line_number-1]}',flush=True)
