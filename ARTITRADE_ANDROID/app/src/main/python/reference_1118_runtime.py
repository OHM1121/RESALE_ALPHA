"""Workbook reference-bar PAPER execution policy installed over original wallets.

Only NEW original fills receive NL1118 stage intents. Exact native SQL,
real 0B/0D/Upbit books, genuine BUY/SELL, live guards, quotas, fees, manual
order execution and WAL remain with their original owners. No old PC ledger
is migrated or restarted.
"""
from __future__ import annotations
import datetime as dt
import json
import math
from decimal import Decimal, ROUND_DOWN
import sqlite3
import time
from pathlib import Path

import threading

import new_legacy_1118_policy as nl
import new_legacy_1118_resting as resting

# 0.2.9: whether the score was positive at THIS buy (an UP-market buy may have a
# negative score; then the "bought while positive, score < -10 -> sell" exit does not apply).
_BUY_CTX=threading.local()


def _up_market(market,symbol=None,frame=None,conn=None):
    """0.2.10: the bought symbol is UP on its own frame (owner: judge each bought symbol)."""
    try:
        import market_regime_v0290 as regime
        return regime.symbol_regime(market,symbol,frame,conn)[0]==regime.UP
    except Exception:
        return False


def _surge_reason(c,tf,code,ref_ts):
    try:
        import at_surge_filter_v0290 as surge
        return surge.stock_check(c,tf,code,ref_ts)
    except Exception:
        return None

_INSTALLED=False
_TBL="""CREATE TABLE IF NOT EXISTS nl1118_reference_positions(
    position_id INTEGER PRIMARY KEY, policy TEXT NOT NULL,
    market TEXT NOT NULL,frame TEXT NOT NULL,symbol TEXT NOT NULL,
    intent_json TEXT NOT NULL,updated_at TEXT NOT NULL)"""

def _is_base_coin(self):
    try:
        p=Path(self.path).resolve()
        return (p.name=="upbit_paper.sqlite3" and p.parent.name=="base"
                and p.parent.parent.name=="coin")
    except (AttributeError,TypeError,OSError):return False

def _market_score(root,market):
    from at_market_score_gate_v107 import score_evidence
    proof=score_evidence(root,market)
    score=proof.get("score")
    return score if proof.get("basis")=="SIGNED_MARKET_SCORE_V033" else None

def _mark(c,position,market):
    try:
        p=dict(position)
        entry=p["entry_price"] if market=="stock" else p["entry"]
        ref_low=p["ref_low"] if market=="stock" else None
        ref_high=p["ref_high"] if market=="stock" else None
        if market=="coin":
            r=c.execute("""SELECT features FROM candidates WHERE tf=?
              AND market=? AND ref_at=? ORDER BY id DESC LIMIT 1""",
              (p["tf"],p["market"],p["ref_at"])).fetchone()
            facts=json.loads(r[0]) if r and r[0] else {}
            ref_low=facts.get("low")
            ref_high=facts.get("high")
        plan=nl.targets(entry,p["tf"],"reference",
                        ref_low=ref_low,ref_high=ref_high)
        # Stock positions store an integer, coin positions a decimal string.
        plan.update({"market":market,"symbol":p["code"] if market=="stock" else p["market"],
            "position_id":int(p["id"]),"qty0":float(p["entry_qty"] if market=="stock" else p["qty0"]),
            "qty":float(p["qty"]),"stage":0,"stage1_filled":0.0,
            "stage2_filled":0.0,"peak_after_second":float(entry),
            "seen_above_entry":False,
            "score_positive_at_buy":bool(getattr(_BUY_CTX,"positive",True))})
        if market=="coin":
            # Original Upbit wallet's immutable actual hold_until remains
            # authoritative. Its default auto SELL is suppressed only for
            # THIS new policy, so re-apply same deadline here.
            plan["deadline"]=str(p["hold_until"])
        elif "hold_until" in p.keys() and p["hold_until"]:
            plan["deadline"]=str(p["hold_until"])
        plan["sell_intents"][0]["quantity"]=nl.stage_quantity(plan["qty0"],market,1)
        plan["sell_intents"][1]["quantity"]=nl.stage_quantity(plan["qty0"],market,2)
        c.execute(_TBL)
        c.execute("""INSERT OR IGNORE INTO nl1118_reference_positions(
          position_id,policy,market,frame,symbol,intent_json,updated_at)
          VALUES(?,?,?,?,?,?,?)""",
          (int(p["id"]),nl.POLICY,market,p["tf"],plan["symbol"],
           json.dumps(plan,ensure_ascii=False),
           dt.datetime.now(dt.timezone.utc).isoformat()))
        original=resting.amount(p["entry_qty"] if market=="stock" else p["qty0"])
        if market=="coin":
            step=Decimal("0.00000001")
            first=(original*Decimal(".30")).quantize(step,rounding=ROUND_DOWN)
            second=(original*Decimal(".35")).quantize(step,rounding=ROUND_DOWN)
        else:
            first=resting.amount(nl.stage_quantity(original,market,1))
            second=resting.amount(nl.stage_quantity(original,market,2))
        resting.place(c,int(p["id"]),market,plan["symbol"],p["tf"],
                      str(p.get("entry_ts") or p.get("entry_at") or ""),
                      original,plan["first"],plan["second"],first,second,
                      paper=True)
        return True
    except (KeyError,TypeError,ValueError,sqlite3.Error,ArithmeticError):
        # Fail closed for this new policy. Do not silently turn an unplanned
        # buy into a holding with obsolete legacy exits.
        return False

def _read(c,pid):
    c.execute(_TBL)
    row=c.execute("""SELECT intent_json FROM nl1118_reference_positions
        WHERE position_id=? AND policy=?""",(pid,nl.POLICY)).fetchone()
    if row is None:return None
    try:return json.loads(row[0])
    except (TypeError,ValueError):return None

def _save(c,plan):
    c.execute("""UPDATE nl1118_reference_positions
        SET intent_json=?,updated_at=? WHERE position_id=?""",
        (json.dumps(plan,ensure_ascii=False,separators=(",",":")),
         dt.datetime.now(dt.timezone.utc).isoformat(),plan["position_id"]))

def _act(self,c,p,minute,at):
    plan=_read(c,p["id"])
    if plan is None:
        # Workbook is the only automatic exit authority. Never fall back
        # to retired D19 3LEG31 / original V2.6 for unplanned holdings.
        # Existing cash/positions remain untouched for explicit migration.
        return p
    if str(at)<=str(p["entry_ts"]):return p
    try:
        at_dt=dt.datetime.strptime(str(at),"%Y%m%d%H%M%S").replace(
            tzinfo=dt.timezone(dt.timedelta(hours=9)))
        verified_price=float(minute["close"])
        if not math.isfinite(verified_price) or verified_price<=0:return p
        plan["qty"]=float(p["qty"])
        if plan["qty"]<=0:return p
        score=_market_score(self.store.root,"stock")
        # An out-of-order exchange print must not reverse stage/watch state.
        previous=str(plan.get('last_verified_tick_at') or '')
        if previous and str(at)<previous:return p
        plan['last_verified_tick_at']=str(at)
        due=nl.exit_signal(plan,verified_price,at_dt,score)
        _save(c,plan)
        if due is None:return p
        reason,fraction=due
        qty=min(int(p["qty"]),max(1,int(math.ceil(
            float(p["qty"])*fraction-1e-10))))
        # Native single 0B verified SELL path uses ONE immutable event key,
        # original cash accounting and execution ledger. No actual broker
        # order is inferred by a target merely being staged at purchase.
        event_key=("NL1118:"+str(p["id"])+":"+reason+":"+str(at))
        done=self._sell(c,p,at,qty,int(verified_price),reason,
                        event_key=event_key)
        if done is None:return p
        executed=c.execute("""SELECT id,qty,price FROM executions
            WHERE event_key=? AND position_id=? AND side='SELL'
            ORDER BY id DESC LIMIT 1""",(event_key,p["id"])).fetchone()
        if executed is None:
            raise RuntimeError("NL1118_STOCK_SELL_RECORD_NOT_CONFIRMED")
        qty=int(executed["qty"])
        if qty<=0:
            raise RuntimeError("NL1118_STOCK_SELL_ZERO_QTY")
        if reason in ("NL1118_TP1","NL1118_TP2"):
            resting.confirmed_fill(c,int(p["id"]),1 if reason=="NL1118_TP1" else 2,
                                   int(executed["id"]))
        else:
            resting.cancel_remaining(c,int(p["id"]),reason)
        if reason=="NL1118_TP1":
            plan["stage1_filled"]+=qty
            if plan["stage1_filled"]+1e-8>=nl.stage_quantity(
                    plan["qty0"],"stock",1):
                plan["stage"]=max(1,plan["stage"])
                plan["sell_intents"][0]["state"]="FILLED"
        elif reason=="NL1118_TP2":
            plan["stage2_filled"]+=qty
            if plan["stage2_filled"]+1e-8>=nl.stage_quantity(
                    plan["qty0"],"stock",2):
                plan["stage"]=max(2,plan["stage"])
                plan["peak_after_second"]=verified_price
                plan["sell_intents"][1]["state"]="FILLED"
        plan["qty"]=float(done["qty"])
        _save(c,plan)
        # A genuinely observed post-gap 0B price can clear BOTH profit
        # targets before another tick arrives. Execute TP2 in this same
        # verified native SQLite transaction, but only after a real TP1
        # SELL is written and confirmed. Never trigger TP3 recursively.
        if (reason=="NL1118_TP1" and plan["stage"]==1 and
            plan["qty"]>0 and verified_price>=float(plan["second"])):
            current=c.execute("SELECT * FROM positions WHERE id=? AND status='OPEN'",
                              (p["id"],)).fetchone()
            if current is not None:
                return _act(self,c,current,minute,at)
        return done
    except (ValueError,TypeError,KeyError,ArithmeticError,sqlite3.Error):
        # Reject unsafe fallback; do not manufacture a SELL.
        return p

_ORIGINAL_STOCK_EXIT=None
_ORIGINAL_D19_EXIT=None

def install():
    global _INSTALLED,_ORIGINAL_STOCK_EXIT,_ORIGINAL_D19_EXIT
    if _INSTALLED:return
    import d_engine,d_v19_portfolio,d_upbit

    _ORIGINAL_STOCK_EXIT=d_engine.PaperEngine._minute_exits
    _ORIGINAL_D19_EXIT=d_v19_portfolio.D19PaperEngine._minute_exits
    d_engine.PaperEngine._minute_exits=_act
    # D19 overrides PaperEngine._minute_exits; patch both independently.
    # Otherwise almost all modern stock AT BUY positions silently retain
    # obsolete 3LEG31 and ignore the user's 11.18 profit/stop policy.
    d_v19_portfolio.D19PaperEngine._minute_exits=_act

    native_d19=d_v19_portfolio.D19PaperEngine._buy_limit_depth
    def buy_d19(self,c,row,when,venue,observed_trade_price,
                observed_trade_qty=0):
        # One source-proven lookup per attempted BUY, after normalizing the
        # DStore(.../stock) key to the shared score publisher's app root.
        # Previous v012 returned None for every eligible KRX D19 order,
        # leaving all limit-touched candidates forever PENDING. Distinguish
        # missing/stale publisher from an actual signed score <=0 so a
        # legitimate entry is never fabricated or misdiagnosed.
        from at_market_score_gate_v107 import score_evidence
        proof=score_evidence(self.store.root,"stock")
        positive=(proof.get("basis")=="SIGNED_MARKET_SCORE_V033" and
                  nl.validate_stock_score(proof.get("score")))
        data=dict(row)
        if not positive and not _up_market("stock",data.get("code"),data.get("tf"),c):
            if proof.get("basis")!="SIGNED_MARKET_SCORE_V033":
                return False,"NL1118_MARKET_SCORE_UNAVAILABLE:"+str(proof.get("basis") or "UNKNOWN")
            return False,"NL1118_MARKET_SCORE_NOT_POSITIVE"
        ref_ts=data.get("ref_ts")
        if not ref_ts and data.get("signal_id") is not None:
            # v19_orders rows carry signal_id only; the reference bar lives on the signal.
            try:
                sig=c.execute("SELECT ref_ts FROM signals WHERE id=?",(data["signal_id"],)).fetchone()
                ref_ts=sig[0] if sig else None
            except Exception:
                ref_ts=None
        blocked=_surge_reason(c,data.get("tf"),data.get("code"),ref_ts)
        if blocked:
            return False,blocked
        _BUY_CTX.positive=bool(positive)
        try:
            result=native_d19(self,c,row,when,venue,observed_trade_price,
                              observed_trade_qty)
        except Exception:
            _BUY_CTX.positive=True
            raise
        if isinstance(result,tuple) and result[0]:
            p=c.execute("""SELECT * FROM positions WHERE tf=? AND code=?
                AND status='OPEN' ORDER BY id DESC LIMIT 1""",
                (row["tf"],row["code"])).fetchone()
            if p is None or str(p["entry_ts"])!=when.strftime("%Y%m%d%H%M%S"):
                raise RuntimeError("NL1118_D19_BUY_POSITION_MISSING_ROLLBACK")
            if not _mark(c,p,"stock"):
                raise RuntimeError("NL1118_D19_BUY_INTENT_MISSING_ROLLBACK")
            c.execute("UPDATE positions SET exit_policy=? WHERE id=?",
                      (nl.POLICY,p["id"]))
        _BUY_CTX.positive=True
        return result
    d_v19_portfolio.D19PaperEngine._buy_limit_depth=buy_d19

    legacy_buy=d_engine.PaperEngine._buy_if_touch
    def buy_legacy(self,c,tf,minute,at):
        positive=nl.validate_stock_score(_market_score(self.store.root,"stock"))
        try:
            pending=self._pending(c,tf)
            raw=dict(pending) if pending is not None else {}
        except Exception:
            raw={}
        if not positive and not _up_market("stock",raw.get("code"),tf,c):
            return None
        if raw and _surge_reason(c,tf,raw.get("code"),raw.get("ref_ts")):
            return None
        _BUY_CTX.positive=bool(positive)
        try:
            p=legacy_buy(self,c,tf,minute,at)
            if p is not None:
                if not _mark(c,p,"stock"):
                    raise RuntimeError("NL1118_STOCK_BUY_INTENT_MISSING_ROLLBACK")
                c.execute("UPDATE positions SET exit_policy=? WHERE id=?",
                          (nl.POLICY,p["id"]))
                p=c.execute("SELECT * FROM positions WHERE id=?",(p["id"],)).fetchone()
        finally:
            _BUY_CTX.positive=True
        return p
    d_engine.PaperEngine._buy_if_touch=buy_legacy

    native_coin=d_upbit.CoinLedger._buy
    def buy_coin(self,c,order,book,seen,available):
        positive=True
        if _is_base_coin(self):
            root=Path(self.path).resolve().parent.parent.parent
            positive=nl.validate_stock_score(_market_score(root,"coin"))
            if not positive and not _up_market("coin",order["market"],order["tf"]):
                return 0
        _BUY_CTX.positive=bool(positive)
        try:
            bought=native_coin(self,c,order,book,seen,available)
            if _is_base_coin(self) and bought:
                p=c.execute("""SELECT * FROM positions WHERE tf=? AND market=?
                   AND ref_at=? AND status='OPEN' ORDER BY id DESC LIMIT 1""",
                   (order["tf"],order["market"],order["ref_at"])).fetchone()
                if p is None or not _mark(c,p,"coin"):
                    raise RuntimeError("NL1118_COIN_BUY_INTENT_MISSING_ROLLBACK")
        finally:
            _BUY_CTX.positive=True
        return bought
    d_upbit.CoinLedger._buy=buy_coin

    original_coin_sell=d_upbit.CoinLedger._sell
    def sell_coin(self,c,p,desired,price,at,reason,event_key=None):
        if _is_base_coin(self):
            manual=str(reason).startswith(("USER:","사용자")) or str(
                event_key or "").startswith("USER:")
            # A missing sidecar is NOT permission for native V2.6/SR1 exits.
            # Treat pre-workbook holdings as migration-required, no auto
            # trade. New positions are rolled back if their sidecar is absent.
            if not manual and (_read(c,p["id"]) is None or
                               not str(reason).startswith("NL1118_")):
                return False
        return original_coin_sell(self,c,p,desired,price,at,reason,
                                  event_key=event_key)
    d_upbit.CoinLedger._sell=sell_coin

    original_book=d_upbit.CoinLedger.process_book
    def book_coin(self,book,at=None):
        result=original_book(self,book,at)
        if not _is_base_coin(self) or not isinstance(book,dict):return result
        now=at or dt.datetime.now(dt.timezone(dt.timedelta(hours=9)))
        if not isinstance(now,dt.datetime) or now.tzinfo is None:return result
        try:
            if abs(now.timestamp()-float(book["timestamp"])/1000)>3:
                return result
            bid=float(book["bid"]);size=float(book["bid_size"])
            if bid<=0 or size<=0 or not math.isfinite(bid):return result
            symbol=str(book["market"])
            from d_upbit import dec,qty_floor
            with self.lock,self.connect() as c:
                c.execute(_TBL)
                positions=c.execute("""SELECT * FROM positions WHERE
                     market=? AND status='OPEN' AND qty!='0' ORDER BY id""",
                     (symbol,)).fetchall()
                for pos in positions:
                    # One verified current book can execute TP1 then TP2.
                    # Never exceed actual half visible 0D capacity, and
                    # never reuse an older/replayed book timestamp.
                    available_depth=dec(size)*dec('.5')
                    for stage_attempt in range(2):
                        if stage_attempt:
                            pos=c.execute("SELECT * FROM positions WHERE id=? AND status='OPEN'",
                                          (pos['id'],)).fetchone()
                            if pos is None:break
                        if available_depth<=0:break
                        plan=_read(c,pos["id"])
                        if plan is None:continue
                        plan["qty"]=float(pos["qty"])
                        stamp=int(float(book['timestamp']))
                        if (stage_attempt==0 and stamp<=int(plan.get('last_verified_book_ms') or 0)):
                            break
                        # Save chronology on EVERY verified observation, not
                        # only on a SELL. Late books cannot forge a reversal.
                        plan['last_verified_book_ms']=stamp
                        score=_market_score(Path(self.path).resolve().parent.parent.parent,"coin")
                        exit_due=nl.exit_signal(plan,bid,now,score)
                        _save(c,plan)
                        if not exit_due:break
                        reason,fraction=exit_due
                        if stage_attempt and reason!='NL1118_TP2':break
                        from d_upbit import dec,qty_floor
                        wanted=qty_floor(min(dec(pos["qty"])*dec(fraction),
                            available_depth))
                        if wanted<=0:break
                        key=("NL1118:"+str(pos["id"])+":"+reason+":"+
                             str(int(float(book["timestamp"]))))
                        if not self._sell(c,pos,wanted,dec(bid),now.isoformat(),
                                          reason,event_key=key):break
                        proof=c.execute("""SELECT id,qty,price FROM executions
                            WHERE event_key=? AND position_id=? AND side='SELL'
                            ORDER BY id DESC LIMIT 1""",
                            (key,pos["id"])).fetchone()
                        if proof is None or dec(proof["qty"])<=0:
                            raise RuntimeError("NL1118_COIN_SELL_RECORD_NOT_CONFIRMED")
                        wanted=dec(proof["qty"])
                        if reason in ("NL1118_TP1","NL1118_TP2"):
                            resting.confirmed_fill(c,int(pos["id"]),
                                1 if reason=="NL1118_TP1" else 2,int(proof["id"]))
                        else:
                            resting.cancel_remaining(c,int(pos["id"]),reason)
                        available_depth=max(dec(0),available_depth-wanted)
                        if reason=="NL1118_TP1":
                            plan["stage1_filled"]+=float(wanted)
                            if plan["stage1_filled"]+1e-7>=nl.stage_quantity(
                                    plan["qty0"],"coin",1):
                                plan["stage"]=max(1,plan["stage"])
                                plan["sell_intents"][0]["state"]="FILLED"
                        elif reason=="NL1118_TP2":
                            plan["stage2_filled"]+=float(wanted)
                            if plan["stage2_filled"]+1e-7>=nl.stage_quantity(
                                    plan["qty0"],"coin",2):
                                plan["stage"]=max(2,plan["stage"])
                                plan["sell_intents"][1]["state"]="FILLED"
                                plan["peak_after_second"]=bid
                        updated=c.execute("SELECT qty FROM positions WHERE id=?",
                                          (pos["id"],)).fetchone()
                        if updated:plan["qty"]=float(updated[0])
                        plan['last_verified_book_ms']=int(float(book['timestamp']))
                        _save(c,plan)
                        if reason!='NL1118_TP1' or plan['stage']!=1:break
        except (sqlite3.Error,KeyError,TypeError,ValueError,ArithmeticError,
                AttributeError):
            return result
        return result
    d_upbit.CoinLedger.process_book=book_coin
    _INSTALLED=True