"""Display-only facts for every holding: first profit target, nearest stop-loss price, buy basis.

Nothing here changes a position, an order, a stop or a target. It READS the strategy's own saved
plan/state and composes text/numbers for the phone UI. A fact that is not stored stays missing
(never guessed), and every reader is fail-soft: a missing table/row only removes that line.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path

KST=dt.timezone(dt.timedelta(hours=9))
FRAMES={"5m":"5분봉","30m":"30분봉","60m":"60분봉","day":"일봉"}


def num(value):
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError,OverflowError):
        return None


def fmt_price(value):
    v=num(value)
    if v is None:return "—"
    if abs(v)>=100:return "{:,.0f}원".format(v)
    text=("%.6f"%v).rstrip("0").rstrip(".")
    return text+"원"


def kst_text(raw):
    """ISO (with zone), epoch s/ms or naive KST 'YYYYMMDDHHMMSS' -> '11월 21일 09:25' (KST)."""
    if raw in (None,""):return ""
    try:
        text=str(raw).strip()
        if text.isdigit() and len(text) in (10,13):
            seconds=int(text)/(1000 if len(text)==13 else 1)
            moment=dt.datetime.fromtimestamp(seconds,dt.timezone.utc)
        elif text.isdigit() and len(text) in (12,14):
            moment=dt.datetime.strptime(text[:14].ljust(14,"0"),"%Y%m%d%H%M%S").replace(tzinfo=KST)
        else:
            moment=dt.datetime.fromisoformat(text.replace("Z","+00:00"))
            if moment.tzinfo is None:moment=moment.replace(tzinfo=KST)
        moment=moment.astimezone(KST)
        return "%d월 %d일 %02d:%02d"%(moment.month,moment.day,moment.hour,moment.minute)
    except (ValueError,TypeError,OverflowError,OSError):
        return ""


def _first(row,*keys):
    if not isinstance(row,dict):return None
    for key in keys:
        value=row.get(key)
        if value is not None and str(value).strip() not in ("","null","None"):
            return value
    return None


def _features(candidate):
    if not isinstance(candidate,dict):return {}
    for key in ("features","feature_json"):
        value=candidate.get(key)
        if isinstance(value,str):
            try:value=json.loads(value)
            except (ValueError,TypeError):value=None
        if isinstance(value,dict):return value
    return {}


# --------------------------------------------------------------------------- stops / targets
def _candidate(price,label,entry):
    p=num(price)
    if p is None or p<=0 or entry is None or entry<=0:return None
    return {"price":p,"label":label,"pct":round((p/entry-1.0)*100.0,1)}


def nearest_stop(candidates,mark,entry):
    """The stop closest to the current price: the highest stop at/below it, else the lowest one above."""
    rows=[c for c in candidates if c]
    if not rows:return None
    ref=num(mark) or entry
    below=[c for c in rows if c["price"]<=ref]
    if below:return max(below,key=lambda c:c["price"])
    return min(rows,key=lambda c:c["price"])


def stops_reference(plan,entry):
    """AT (reference bar): hard stop under the reference candle, plus the stage rules."""
    stage=int(num(plan.get("stage")) or 0)
    out=[_candidate(plan.get("stop"),"기준봉 손절",entry)]
    if stage>=1:out.append(_candidate(entry*1.01,"1차 매도 후 본전+1%",entry))
    # 0.3.4 (#149): a coin GEN plan arms this exit only after entry +1.5 % (rte_armed_v0340).
    armed=(plan.get("rte_armed_v0340") is True if plan.get("market")=="coin"
           else plan.get("seen_above_entry") is True)
    if stage<=0 and armed:
        out.append(_candidate(entry,"상승 후 매수가 복귀",entry))
    if stage>=2:
        out.append(_candidate(plan.get("first"),"2차 매도 후 1차 목표가 이탈",entry))
        peak=num(plan.get("peak_after_second")) or num(plan.get("peak"))
        trail=num(plan.get("trail"))
        if peak and trail:out.append(_candidate(peak*(1-trail),"고점 추적 손절",entry))
    return out


def stops_ma_trend(entry,stage,hard,lookback_status,lookback_price,lookback_confirmed,
                   first_target=None,peak=None,trail=None):
    out=[_candidate(hard if hard is not None else entry*0.85,"-15% 손절",entry)]
    if lookback_status=="ACTIVE" and lookback_confirmed is not True:
        out.append(_candidate(lookback_price,"직전봉 최저가 손절",entry))
    if stage==1:out.append(_candidate(entry*1.01,"1차 매도 후 본전+1%",entry))
    if stage>=2:
        out.append(_candidate(first_target,"2차 매도 후 1차 목표가 이탈",entry))
        if peak and trail:out.append(_candidate(peak*(1-trail),"고점 추적 손절",entry))
    return out


def _apply(position,first_target,candidates,mark,entry,second_target=None,stage=0):
    if num(first_target) and num(first_target)>0:
        position["first_profit_target"]=num(first_target)
    # 0.2.5: how many profit stages are already SOLD, and the next target. After the first sell the card must
    # show the second target, not the first one that is already filled.
    position["profit_stage"]=max(0,min(2,int(stage or 0)))
    if num(second_target) and num(second_target)>0:
        position["second_profit_target"]=num(second_target)
    pick=nearest_stop(candidates,mark,entry)
    if pick:
        position["stop_loss_price"]=pick["price"]
        position["stop_loss_pct"]=pick["pct"]
        position["stop_loss_kind"]=pick["label"]
        position["stop_loss_all"]=[c for c in candidates if c]


def _entry_mark(position):
    entry=num(_first(position,"entry","entry_price","buy_price","avg_buy_price"))
    mark=num(_first(position,"mark","last_price","current_price","market_price","stock_quote_display_price"))
    return entry,mark


def _connect_ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",uri=True,timeout=3)


def read_reference_plans(path):
    """{position_id: plan} of the NEW LEGACY reference-bar positions (stock AT / coin base)."""
    plans={}
    db=Path(path)
    if not db.is_file():return plans
    try:
        with _connect_ro(db) as c:
            for pid,raw in c.execute("""SELECT position_id,intent_json FROM nl1118_reference_positions
                    WHERE policy='NEW_LEGACY_1118'"""):
                try:plans[str(pid)]=json.loads(raw)
                except (ValueError,TypeError):pass
    except sqlite3.Error:
        pass
    return plans


def read_ma_states(path):
    states={}
    db=Path(path)
    if not db.is_file():return states
    try:
        with _connect_ro(db) as c:
            for pid,kind,raw in c.execute("SELECT position_id,kind,state_json FROM ma_v067_positions"):
                try:state=json.loads(raw or "{}")
                except (ValueError,TypeError):continue
                if isinstance(state,dict) and kind=="REVERSAL_UP":states[str(pid)]=state
    except sqlite3.Error:
        pass
    return states


def enrich_reference(positions,db_path):
    plans=read_reference_plans(db_path)
    for position in positions or ():
        try:
            plan=plans.get(str(position.get("id")))
            if not plan:continue
            entry,mark=_entry_mark(position)
            entry=entry or num(plan.get("entry"))
            if not entry:continue
            _apply(position,plan.get("first"),stops_reference(plan,entry),mark,entry,
                   second_target=plan.get("second"),stage=int(num(plan.get("stage")) or 0))
        except Exception:
            continue


# Close-betting (종가베팅) policy constants of the original engine (AT_CLOSE POLICY['CLOSE']):
# first target +1.2 %, HARD_STOP at -2.0 % of the entry.
CLOSE_FIRST_PCT=1.2
CLOSE_STOP_PCT=2.0


def enrich_close(positions):
    for position in positions or ():
        try:
            entry,mark=_entry_mark(position)
            if not entry:continue
            stop=_candidate(entry*(1-CLOSE_STOP_PCT/100.0),"종가베팅 손절",entry)
            _apply(position,entry*(1+CLOSE_FIRST_PCT/100.0),[stop],mark,entry)
        except Exception:
            continue


def enrich_ma(positions,db_path):
    import ma_v067_policy as policy
    states=read_ma_states(db_path)
    for position in positions or ():
        try:
            state=states.get(str(position.get("id")))
            if state is None:continue
            entry,mark=_entry_mark(position)
            frame=str(position.get("tf") or "")
            if not entry or frame not in FRAMES:continue
            first_t,_second_t=policy.up_targets(entry,frame)
            first_done=bool(state.get("first_completed_at"))
            second_done=bool(state.get("second_completed_at"))
            stage=2 if second_done else (1 if first_done else 0)
            trail=float(policy.UP_TRAIL_FROM_SECOND.get(frame,0)) or None
            candidates=stops_ma_trend(entry,stage,num(state.get("hard_stop")),
                state.get("nl1120_stop_status"),state.get("nl1120_stop_price"),
                state.get("nl1120_stop_confirmed"),first_target=first_t,
                peak=num(state.get("peak_after_second")),trail=trail)
            _apply(position,first_t,candidates,mark,entry,second_target=_second_t,stage=stage)
        except Exception:
            continue


def enrich_trend_row(position,payload):
    """Called by the trend wallet snapshot with the row's saved payload before it is dropped."""
    try:
        entry,mark=_entry_mark(position)
        if not entry or not isinstance(payload,dict):return
        stage=int(num(payload.get("stage")) or 0)
        candidates=stops_ma_trend(entry,stage,num(payload.get("nl1118_stop")),
            payload.get("nl1120_stop_status"),payload.get("nl1120_stop_price"),
            payload.get("nl1120_stop_confirmed"),first_target=num(payload.get("target1")),
            peak=num(payload.get("peak_after_second")),trail=None)
        _apply(position,payload.get("target1"),candidates,mark,entry,
               second_target=payload.get("target2"),stage=stage)
    except Exception:
        return


# --------------------------------------------------------------------------- buy basis (text)
def _diff_text(entry,low):
    if entry is None or low is None or low<=0 or entry<=low:return ""
    return " (기준봉 저가보다 %s·%.1f%% 높은 가격)"%(fmt_price(entry-low),(entry/low-1)*100)


def basis_reference(market,position,fill,candidate):
    """AT: 'the reference bar appeared at T, its low was X; bought at Y at time Z'."""
    features=_features(candidate)
    frame=str(position.get("tf") or "")
    low=num(_first(position,"ref_low")) or num(features.get("low"))
    high=num(_first(position,"ref_high")) or num(features.get("high"))
    ref=_first(position,"ref_ts","ref_at","original_ref_ts") or _first(candidate or {},"ref_ts","ref_at")
    entry=num(_first(position,"entry_price","entry")) or num(_first(fill or {},"price","fill_price"))
    bought=_first(fill or {},"at","ts","time") or _first(position,"entry_ts","entry_at","buy_at")
    ref_text,buy_text=kst_text(ref),kst_text(bought)
    if not (ref_text and low and entry and buy_text):return None
    # Candle times are stored at the bar START (UTC for coin, KST for stock); say so plainly.
    text="%s 시작 %s 기준봉 발생 (저가 %s"%(ref_text,FRAMES.get(frame,frame),fmt_price(low))
    if high:text+=" · 고가 %s"%fmt_price(high)
    text+="). %s 시세 %s에 매수%s"%(buy_text,fmt_price(entry),_diff_text(entry,low))
    limit=num(_first(candidate or {},"limit_price"))
    if limit and abs(limit-entry)>1e-12:text+=" · 매수 지정가 %s"%fmt_price(limit)
    return text


def basis_ma(market,position,fill,candidate):
    from trade_decision_basis_v095 import _ma_entry
    features=_features(candidate)
    frame=str(position.get("tf") or "")
    description,_source,_data=_ma_entry(features,str((candidate or {}).get("reason") or ""),frame,market)
    entry=num(_first(position,"entry_price","entry")) or num(_first(fill or {},"price","fill_price"))
    bought=_first(fill or {},"at","ts","time") or _first(position,"entry_at","buy_at")
    buy_text=kst_text(bought)
    if not description or not entry or not buy_text:return None
    signal=kst_text(features.get("ma_v067_bar_start") or features.get("ma_v067_submit_at"))
    prefix=(signal+" 신호봉 · ") if signal else ""
    limit=num(_first(candidate or {},"limit_price"))
    tail="; %s 시세 %s에 매수"%(buy_text,fmt_price(entry))
    if limit and abs(limit-entry)>1e-12:tail+=" (지정가 %s 이하 체결)"%fmt_price(limit)
    return prefix+description+tail


def basis_trend(position,payload):
    """MarketTrend: RSI watch -> completed bar above the Bollinger MIDDLE -> buy; plus size note."""
    if not isinstance(payload,dict):return None
    frame=str(position.get("tf") or payload.get("frame") or "")
    rsi=num(payload.get("rsi20_closed_at_buy"))
    if rsi is None:rsi=num(payload.get("rsi20_live"))
    entry=num(_first(position,"entry_price","entry"))
    buy_text=kst_text(_first(position,"entry_at","buy_at"))
    bar_text=kst_text(payload.get("bar_key"))
    if rsi is None or not entry or not buy_text:return None
    rule=str(payload.get("entry_rule") or "")
    if rule=="NL1120_BAR_CLOSE_ABOVE_BOLL_MID":
        body=("%s: RSI(20)이 30 이하로 내려간 뒤(%s 이내) 첫 확정봉이 볼린저밴드(20,1.5) "
              "중앙선 위에서 마감, 중앙선 기울기 -15°~+20° 조건 충족. 매수 시점 RSI(20) %.1f"%(
              FRAMES.get(frame,frame),"4일" if frame=="day" else "24시간",rsi))
    else:
        body="%s: RSI(20)이 30 이하로 내려간 뒤 볼린저밴드(20,1.5) 중앙선 도달. 매수 시점 RSI(20) %.1f"%(
              FRAMES.get(frame,frame),rsi)
    text=("%s 신호봉 · "%bar_text if bar_text else "")+body
    text+="; %s 시세 %s에 매수"%(buy_text,fmt_price(entry))
    score=num(payload.get("market_score_at_buy"))
    factor=str(payload.get("buy_size_factor") or "")
    if score is not None and factor and factor!="1":
        text+="; 시장점수 %.1f(-10 이하)로 1종목 한도 1/2 적용"%score
    return text


def attach_basis(market,strategy,position,fill,candidate):
    """Set position['buy_basis'] when the stored facts are enough; otherwise leave it unset."""
    try:
        if strategy in ("at","base"):
            text=basis_reference(market,position,fill,candidate)
        elif strategy=="ma":
            text=basis_ma(market,position,fill,candidate)
        else:
            text=None
        if text:position["buy_basis"]=text
    except Exception:
        return


# ------------------------------------------------------------------ last-good facts (0.3.6, #151)
# The target / stop / buy-basis fields are VIEW data derived from the SQLite plan and the stored BUY evidence.
# A failed or timed-out read used to be swallowed and the card silently fell back to the vendor fields
# (reference-candle body top as "1차 목표", stop "미확인", no basis). Keep the last good values per position and
# restore only what is missing; nothing here is a trading input.
FACT_KEYS=("first_profit_target","second_profit_target","profit_stage","stop_loss_price","stop_loss_pct",
           "stop_loss_kind","stop_loss_all","buy_basis")
_FACT_CACHE={}


def remember_facts(scope,positions):
    for position in positions or ():
        try:
            if not isinstance(position,dict) or position.get("id") is None:continue
            facts={k:position[k] for k in FACT_KEYS if position.get(k) not in (None,"")}
            if facts.get("stop_loss_price") is not None or facts.get("buy_basis"):
                _FACT_CACHE[(scope,str(position.get("id")))]=facts
        except Exception:
            continue


def restore_facts(scope,positions):
    """Fill ONLY missing keys from the last good enrichment of the same position. Returns how many were filled."""
    filled=0
    for position in positions or ():
        try:
            if not isinstance(position,dict) or position.get("id") is None:continue
            cached=_FACT_CACHE.get((scope,str(position.get("id"))))
            if not cached:continue
            for key,value in cached.items():
                if position.get(key) in (None,""):
                    position[key]=value
                    filled+=1
        except Exception:
            continue
    return filled
