"""Read-only sale/expected-exit discovery from original ARTITRADE V070+V043 ledgers.

Only durable original SELL fills or authoritative CLOSED virtual fills count as
confirmed. Holding deadlines for virtual PAPER are strictly EXPECTED, never
promoted to simulated fills or real broker orders.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from post_exit_high_v053 import parsed_at,finite_positive,deadline,KST

LIMIT=12000
READ_TIMEOUT=3


def _rows(root,relative,table,where="",args=(),order="id"):
    path=Path(root)/relative
    if not path.is_file():return [],None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",
                                     uri=True,timeout=READ_TIMEOUT)) as c:
            c.row_factory=sqlite3.Row
            known={r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if table not in known:
                # Optional shadow/trend tables are absent in older ledgers.
                # Do not log thousands of false alarms on each refresh.
                return [],None
            columns={r[1] for r in c.execute('PRAGMA table_info("'+table+'")')}
            ordering="rowid" if order not in columns else order
            query='SELECT * FROM "'+table+'"'+(' WHERE '+where if where else '')+\
                  ' ORDER BY "'+ordering+'" DESC LIMIT ?'
            return [dict(x) for x in c.execute(query,tuple(args)+(LIMIT,))],None
    except (sqlite3.Error,OSError,ValueError,TypeError) as exc:
        return [],"READ_ERROR:"+relative+":"+table+":"+type(exc).__name__


def _safe_ts(value):
    stamp=parsed_at(value)
    return stamp.isoformat() if stamp is not None else None


def _symbol(row,market):
    code=str(row.get("code") or row.get("market") or row.get("symbol") or "").strip()
    if market=="stock" and code.startswith("A") and code[1:].isdigit():
        code=code[1:]
    return code


def _exit(row,executions):
    """Prefer the final acknowledged SELL execution over a status-only update."""
    sells=[x for x in executions if x.get("side")=="SELL"]
    times=[parsed_at(x.get("ts") or x.get("at") or x.get("time"))
           for x in sells]
    times=[x for x in times if x is not None]
    return max(times).isoformat() if times else None


def _actual(root,market,strategy,relative,problems):
    """Identify separately verified partial SELLs and the original final SELL.

    A position may remain OPEN after its first 10/30/40% realized sale.
    Each confirmed SELL event starts an independent forward-price window;
    no sale event and no public post-sale print means NO observable peak.
    Existing final-exit case identifiers remain byte-for-byte compatible.
    """
    positions,error=_rows(root,relative,"positions",
        "status IN ('OPEN','CLOSED','SOLD','DONE','FILLED_EXIT')",order="id")
    if error:problems.append(error);return []
    executions,error=_rows(root,relative,"executions",order="id")
    if error:problems.append(error)
    by_position=defaultdict(list)
    for row in executions:
        pid=row.get("position_id")
        if pid is not None:by_position[str(pid)].append(row)
    output=[]
    for p in positions:
        state=str(p.get("status") or "").upper()
        if state not in ("OPEN","CLOSED","SOLD","DONE","FILLED_EXIT"):
            continue
        symbol=_symbol(p,market)
        if not symbol:continue
        tf=str(p.get("tf") or p.get("frame") or "day")
        tradeid=str(p.get("id") or "")
        own=by_position.get(tradeid,[])
        if any("MARKET_TREND" in str(x.get("reason") or "") for x in own):
            continue
        price=(p.get("entry_price") or p.get("entry") or
               p.get("buy_price") or p.get("avg_buy_price"))
        if finite_positive(price) is None:continue
        original=str(p.get("ref_ts") or p.get("ref_at") or
                     p.get("entry_ts") or p.get("entry_at") or "")
        source=relative+":position:"+tradeid+":"+original
        modes={str(x.get("fill_method") or "").upper() for x in own
               if str(x.get("side") or "").upper()=="BUY"}
        mode="LIVE" if any("LIVE" in x or "BROKER" in x for x in modes) else (
             "ORIGINAL_PAPER_FILL" if modes else "UNVERIFIED_FILLED_LEDGER")
        closed=state in ("CLOSED","SOLD","DONE","FILLED_EXIT")
        sells=[]
        for row in own:
            if str(row.get("side") or "").upper()!="SELL":
                continue
            when=parsed_at(row.get("ts") or row.get("at") or row.get("time"))
            if when is not None:
                sells.append((when,row))
        sells.sort(key=lambda pair:(pair[0],str(pair[1].get("id") or "")))
        final_id=None
        if closed:
            exit_at=_exit(p,own)
            if exit_at is None:
                problems.append("SELL_TIME_UNVERIFIED:"+relative+":"+tradeid)
                continue
            if sells:
                final_id=str(sells[-1][1].get("id") or "")
            output.append(dict(market=market,strategy=strategy,frame=tf,
                symbol=symbol,cohort="ACTUAL_FILLED",execution_mode=mode,
                entry_price=price,exit_at=exit_at,source_ref=source,
                exit_evidence="CONFIRMED",
                target_price=p.get("target"),notes="Source-confirmed final SELL"))
        # OPEN means ALL actually executed SELLs are partial. CLOSED means
        # every SELL EXCEPT the confirmed final event is a partial exit.
        for when,sale in sells:
            fillid=str(sale.get("id") or "")
            if (not fillid or fillid==final_id or
                finite_positive(sale.get("qty")) is None):
                continue
            output.append(dict(market=market,strategy=strategy,frame=tf,
                symbol=symbol,cohort="ACTUAL_FILLED",execution_mode=mode,
                entry_price=price,exit_at=when.isoformat(),
                source_ref=relative+":partialfill:"+tradeid+":"+fillid,
                exit_evidence="CONFIRMED",target_price=p.get("target"),
                notes="Confirmed partial SELL; original position can remain OPEN"))
    return output

def _shadows(root,market,strategy,relative,problems,now):
    table="stock_shadow_trials" if market=="stock" else "coin_shadow_trials"
    items,error=_rows(root,relative,table,order="signal_id" if market=="stock" else "candidate_id")
    if error:problems.append(error);return []
    out=[]
    for p in items:
        key=p.get("signal_id") if market=="stock" else p.get("candidate_id")
        symbol=_symbol(p,market)
        frame=str(p.get("tf") or "")
        price=p.get("entry_price")
        entry=parsed_at(p.get("entry_ts") if market=="stock" else p.get("entry_at"))
        if entry is None or finite_positive(price) is None or not symbol:
            continue
        state=str(p.get("state") or "").upper()
        at=p.get("sold_at") if market=="stock" else p.get("sell_at")
        known=parsed_at(at) if at else None
        expected=None
        if known is None and state=="OPEN":
            if market=="coin":
                expected=parsed_at(p.get("hold_until"))
            else:
                hold=p.get("hold_days")
                try:
                    count=max(1,int(hold))
                    # The virtual holding-day count includes entry day. Expected
                    # close is based on the last holding date, not on now().
                    if count<=400:
                        from krx_session_v041 import CLOSED,SPECIAL_SESSIONS,END
                        d=entry.date();seen=1;verified=d.year in CLOSED
                        while verified and seen<count:
                            d+=dt.timedelta(days=1)
                            if d.year not in CLOSED:verified=False;break
                            if d.weekday()<5 and d.isoformat() not in CLOSED[d.year]:
                                seen+=1
                        if verified:
                            close=SPECIAL_SESSIONS.get(d.isoformat(),(0,END))[1]
                            expected=dt.datetime.combine(
                                d,dt.time(close//60,close%60),KST)
                except (ValueError,TypeError):pass
        if known is None and (expected is None or expected.timestamp()>now):
            continue
        exit_at=known or expected
        if exit_at is None or exit_at<entry:continue
        out.append(dict(market=market,strategy=strategy,frame=frame,
            symbol=symbol,cohort="PAPER",execution_mode="COUNTERFACTUAL",
            entry_price=price,exit_at=exit_at.isoformat(),
            source_ref=relative+":"+table+":"+str(key)+":"+entry.isoformat(),
            exit_evidence="CONFIRMED" if known and state=="CLOSED" else "EXPECTED",
            target_price=p.get("target_price") or p.get("target"),
            notes=("Virtual final SELL observed" if known and state=="CLOSED"
                   else "Virtual holding deadline; SELL not confirmed")))
    return out


def _gmo(root,problems,now):
    path=Path(root)/"stock"/"PROJECTGMO_V1.0_account_state.json"
    if not path.is_file():return []
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,ValueError,TypeError) as e:
        problems.append("GMO_STATE:"+type(e).__name__);return []
    out=[]
    buys=data.get("buy_fills") or []
    sells=data.get("fills") or []
    timeline=defaultdict(list)
    for row in buys:
        if isinstance(row,dict) and row.get("code"):
            timeline[str(row["code"])].append((parsed_at(row.get("time")),0,row))
    for row in sells:
        if isinstance(row,dict) and row.get("code"):
            timeline[str(row["code"])].append((parsed_at(row.get("time")),1,row))
    for symbol,records in timeline.items():
        valid=sorted((r for r in records if r[0] is not None),key=lambda x:(x[0],x[1]))
        available=0.0;weighted=0.0;first=None
        for at,side,item in valid:
            n=finite_positive(item.get("qty"))
            if n is None:continue
            if side==0:
                price=finite_positive(item.get("price"))
                if price is None:continue
                if available<=1e-8:weighted=0.0;first=at
                weighted+=price*n;available+=n
            else:
                if available<n-1e-6:
                    problems.append("GMO_SELL_EXCEEDS_BUY:"+symbol)
                    available=0;weighted=0;first=None
                    continue
                weighted=weighted*(available-n)/available if available>0 else 0
                available-=n
                if available<=1e-8 and first:
                    # Average original entry is the BUY-weighted basis, never
                    # the later SELL price.
                    prices=[x for t,kind,x in valid if kind==0 and
                            first<=t<=at and finite_positive(x.get("price"))]
                    total=sum(float(x["qty"]) for x in prices)
                    avg=(sum(float(x["price"])*float(x["qty"]) for x in prices)/total
                         if total>0 else None)
                    if avg is not None:
                        out.append(dict(market="stock",strategy="gmo",frame="gmo",
                            symbol=_symbol({"code":symbol},"stock"),
                            cohort="ACTUAL_FILLED",execution_mode="ORIGINAL_PAPER_FILL",
                            entry_price=avg,exit_at=at.isoformat(),
                            source_ref="stock/GMO:buy:"+first.isoformat()+":"+symbol,
                            exit_evidence="CONFIRMED",notes="GMO final filled sell"))
                    available=0;weighted=0;first=None
    # Non-selected candidates are only hypothetical if an independently
    # recorded D+1 opening price and completed D+15 horizon exist.
    rel="stock/PROJECTGMO_V1.0_research.sqlite3"
    candidates,error=_rows(root,rel,"gmo_candidates",order="event_date")
    if error:problems.append(error)
    daily,error=_rows(root,rel,"gmo_daily_ohlc",order="trade_date")
    if error:problems.append(error)
    d15={(x.get("code"),x.get("event_date")):x for x in daily
         if int(x.get("day_index") or 0)==15 and int(x.get("final") or 0)==1}
    for c in candidates:
        symbol=str(c.get("code") or "")
        source=(symbol,c.get("event_date"))
        final=d15.get(source)
        p=finite_positive(c.get("opening_price"))
        if not final or p is None:continue
        day=final.get("trade_date")
        try:
            d=dt.date.fromisoformat(str(day))
            from krx_session_v041 import SPECIAL_SESSIONS,END
            clock=SPECIAL_SESSIONS.get(d.isoformat(),(0,END))[1]
            at=dt.datetime.combine(d,dt.time(clock//60,clock%60),KST)
        except (TypeError,ValueError):continue
        if at.timestamp()>now:continue
        # This is an expected virtual sale at D+15, NOT a fill.
        out.append(dict(market="stock",strategy="gmo",frame="gmo",
            symbol=_symbol({"code":symbol},"stock"),
            cohort="PAPER",execution_mode="COUNTERFACTUAL",
            entry_price=p,exit_at=at.isoformat(),
            source_ref=rel+":candidate:"+symbol+":"+str(c.get("event_date")),
            exit_evidence="EXPECTED",notes="D+15 virtual horizon; not an execution"))
    return out


def _trend(root,problems,now):
    rel="market_trend_v043.sqlite3"
    states,error=_rows(root,rel,"position_state",order="updated_at")
    if error:problems.append(error)
    virtual,error=_rows(root,rel,"virtual_position_state",order="updated_at")
    if error:problems.append(error)
    events,error=_rows(root,rel,"events",order="at")
    if error:problems.append(error)
    sold_by=defaultdict(list);virtual_by=defaultdict(list)
    for e in events:
        payload={}
        try:payload=json.loads(e.get("payload") or "{}")
        except (ValueError,TypeError):pass
        key=(str(e.get("market")),str(e.get("strategy")),str(e.get("frame")),
             str(e.get("symbol")),str(payload.get("position_id")))
        if str(e.get("event_key") or "").startswith("MT43:SELL:") and \
           str(e.get("event_key") or "").endswith(":FILLED"):
            sold_by[key].append(e)
        if str(e.get("event_key") or "").startswith("MT43:VEXIT:"):
            k=key[:4]
            virtual_by[k].append(e)
    out=[]
    for virtual_flag,items in ((False,states),(True,virtual)):
        for p in items:
            state=str(p.get("status") or "").upper()
            if state!="CLOSED" and not (virtual_flag and state=="OPEN"):
                continue
            try:payload=json.loads(p.get("payload") or "{}")
            except (ValueError,TypeError):continue
            entry=parsed_at(payload.get("entry_at"))
            price=finite_positive(payload.get("entry"))
            if entry is None or price is None:continue
            market=str(p.get("market") or "")
            strategy=str(p.get("strategy") or "")
            frame=str(p.get("frame") or "")
            symbol=_symbol(p,market)
            if market not in ("stock","coin") or not symbol:continue
            if virtual_flag:
                # The signal_key serializes the exact original virtual tuple.
                # Do not borrow a VEXIT from another entry of the same ticker.
                try:
                    identity=json.loads(str(p.get("signal_key") or ""))
                    event_prefix="MT43:VEXIT:"+":".join(str(x) for x in identity)+":"
                except (ValueError,TypeError):
                    event_prefix=""
                choices=[e for e in virtual_by.get((market,strategy,frame,symbol),())
                         if event_prefix and
                         str(e.get("event_key") or "").startswith(event_prefix)
                         and entry.timestamp()<=float(e["at"])<=float(p["updated_at"])+10]
                at=parsed_at(max(choices,key=lambda x:x["at"])["at"]) if choices else None
                evidence="CONFIRMED" if at else "EXPECTED"
                if at is None:at=parsed_at(payload.get("deadline"))
                if state=="OPEN":
                    at=parsed_at(payload.get("deadline"))
                    evidence="EXPECTED"
            else:
                key=(market,strategy,frame,symbol,str(p.get("position_id")))
                choices=sold_by.get(key,())
                at=parsed_at(max(choices,key=lambda x:x["at"])["at"]) if choices else None
                evidence="CONFIRMED"
            if at is None or at<entry or at.timestamp()>now:continue
            out.append(dict(market=market,strategy="trend",frame=frame,
                symbol=symbol,cohort="PAPER" if virtual_flag else "ACTUAL_FILLED",
                execution_mode="COUNTERFACTUAL" if virtual_flag else
                    str(payload.get("execution_mode") or "UNVERIFIED_FILLED_LEDGER"),
                entry_price=price,exit_at=at.isoformat(),
                source_ref=rel+":"+("virtual:" if virtual_flag else "position:")+
                    str(p.get("signal_key") if virtual_flag else p.get("position_id"))+
                    ":"+strategy+":"+entry.isoformat(),
                exit_evidence=evidence,
                target_price=payload.get("target1"),
                notes=("Market-trend simulated final exit" if virtual_flag else
                       "Market-trend acknowledged final sell")))
    return out



def _trend_v054(root,problems,now):
    """Confirmed final sells from standalone MT54 wallets; WATCH is never a trade."""
    out=[]
    for market in ("stock","coin"):
        rel=market+"/trend/market_trend_wallet_v054.sqlite3"
        positions,error=_rows(root,rel,"positions","status='CLOSED'",order="id")
        if error:problems.append(error)
        executions,error=_rows(root,rel,"executions",order="id")
        if error:problems.append(error)
        sells=defaultdict(list)
        for row in executions:
            if str(row.get('side') or '').upper()=='SELL' and \
               str(row.get('event_key') or '').startswith('MT54:SELL:'):
                sells[str(row.get('position_id'))].append(row)
        for row in positions:
            identity=str(row.get('id') or '')
            choices=sells.get(identity,())
            if not choices:continue
            stamps=[(parsed_at(x.get('at')),x) for x in choices]
            stamped=[(at,x) for at,x in stamps if at is not None]
            if not stamped:continue
            final,sale=max(stamped,key=lambda t:t[0])
            entry=parsed_at(row.get('entry_at'))
            price=finite_positive(row.get('entry'))
            symbol=str(row.get('symbol') or '')
            if (entry is None or price is None or not symbol or final<entry
                    or final.timestamp()>now):continue
            out.append(dict(market=market,strategy='trend',frame=str(row.get('tf')),
                symbol=symbol,cohort='ACTUAL_FILLED',
                execution_mode=str(row.get('execution_mode') or 'UNVERIFIED_FILLED_LEDGER'),
                entry_price=price,exit_at=final.isoformat(),
                source_ref=rel+':position:'+identity+':'+entry.isoformat(),
                exit_evidence='CONFIRMED',
                notes='MT54 standalone acknowledged final SELL'))
    return out

def scan(root,now_epoch):
    """Return authenticated per-trade study windows; never mutate input files."""
    sources=[
      ("stock","at","stock/ARTITRADE_D.sqlite3"),
      ("stock","ma","stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3"),
      ("stock","close","stock/close/AT_CLOSE_PAPER.sqlite3"),
      ("coin","general","coin/base/upbit_paper.sqlite3"),
      ("coin","ma","coin/ma/upbit_paper.sqlite3"),
    ]
    out=[];problems=[]
    for market,strategy,path in sources:
        out.extend(_actual(root,market,strategy,path,problems))
        if (market,strategy) in (("stock","at"),("coin","general"),("coin","ma")):
            out.extend(_shadows(root,market,strategy,path,problems,now_epoch))
    out.extend(_gmo(root,problems,now_epoch))
    out.extend(_trend(root,problems,now_epoch))
    out.extend(_trend_v054(root,problems,now_epoch))
    # Favor confirmed and deduplicate exact source-episode keys.
    result={}
    for item in out:
        key=(item["market"],item["strategy"],item["cohort"],item["source_ref"],
             item["exit_at"])
        if key not in result or item["exit_evidence"]=="CONFIRMED":result[key]=item
    return list(result.values()),problems[:250]
