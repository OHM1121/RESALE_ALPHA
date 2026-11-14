"""v042 root-pattern research for one selected strategy/timeframe.

A decline is an OUTCOME, never a cause. A condition is considered a possible
precursor only when its timestamp is at/before entry or its movement is
observed *during* the holding interval. Historical opportunity exclusion is
explicitly a backtest scenario, not guaranteed future profit. All private
raw ATR/RSI/Williams/volume/market-score values remain in SQLite/ZIP.
"""
from __future__ import annotations

import math
from collections import defaultdict

import ai_research_v040 as journal

# (key, consumer-facing interpretation, decision/action, early condition)
PRECURSORS=(
    ("market_weak","시장 약세 구간에서 신규 진입",
     "시장 회복 확인 전 신규 매수 제한"),
    ("market_volume","시장 약세와 비정상 거래량 증가가 동반된 진입",
     "시장 약세·과열 거래량 동시 충족 시 진입 제외"),
    ("heat_chase","단기 과매수 상태에서 추격 진입",
     "과열 해소와 매수세 재확인 후 진입"),
    ("volatility_risk","변동성 확대 구간에서 진입",
     "변동성 확장 시 진입가격·손절 조건 조정"),
    ("thin_bid","매수호가가 약해진 구간에서 진입",
     "호가 매수세와 스프레드 확인 후 진입"),
    ("excess_gap","단기 급등 후 높은 가격에 진입",
     "급등 시 가격 추격 제한"),
    ("market_loss","보유 중 시장환경 악화가 선행",
     "시장환경 하락 때 보유물량 축소 검토"),
    ("momentum_loss","보유 중 상승 동력 약화가 선행",
     "상승동력 약화 확인 시 이익보호 강화"),
    ("volatility_rise","보유 중 변동성이 확장",
     "변동성 확장 시 보호매도 조건 사전검토"),
    ("stable_market","시장 지지와 함께 보유수익 개선",
     "양호한 시장에서 기존 진입 기회 유지"),
    ("trend_hold","과열이 아닌 상승추세에서 진입",
     "상승추세와 유효 거래량이 확인될 때 진입 유지"),
)

CAUSAL_ORDER={entry[0]:i for i,entry in enumerate(PRECURSORS)}
TEXT={key:(name,action) for key,name,action in PRECURSORS}


def _num(v):
    return journal._number(v)


def _features(lifecycle):
    x=lifecycle or {}
    entry=dict(x.get("entry_features") or {})
    hold=dict(x.get("holding_mean") or {})
    exit_=dict(x.get("exit_features") or {})
    if x.get("market_entry") is not None:
        entry["market_score"]=x["market_entry"]
    if x.get("market_exit") is not None:
        exit_["market_score"]=x["market_exit"]
    if x.get("market_holding_mean") is not None:
        hold["market_score"]=x["market_holding_mean"]
    return entry,hold,exit_


def _precursors(record):
    entry,hold,exit_=_features(record)
    activated=set()
    ec=lambda key:_num(entry.get(key))
    hc=lambda key:_num(hold.get(key))
    if ec("market_score") is not None and ec("market_score")<=-20:
        activated.add("market_weak")
    if (ec("market_score") is not None and ec("market_score")<=-20 and
        ec("volume_ratio") is not None and ec("volume_ratio")>=2):
        activated.add("market_volume")
    if ((ec("rsi") is not None and ec("rsi")>=72) or
        (ec("williams_r") is not None and ec("williams_r")>=-20)):
        activated.add("heat_chase")
    if (ec("atr_pct") is not None and ec("atr_pct")>=4) or (
        ec("spread_pct") is not None and ec("spread_pct")>=.5):
        activated.add("volatility_risk")
    if ec("bid_imbalance") is not None and ec("bid_imbalance")<.35:
        activated.add("thin_bid")
    if ec("gap_pct") is not None and ec("gap_pct")>=4:
        activated.add("excess_gap")
    if (ec("market_score") is not None and hc("market_score") is not None
        and hc("market_score")-ec("market_score")<=-20):
        activated.add("market_loss")
    if (ec("rsi") is not None and hc("rsi") is not None
        and hc("rsi")-ec("rsi")<=-12) or (
        ec("williams_r") is not None and hc("williams_r") is not None
        and hc("williams_r")-ec("williams_r")<=-22):
        activated.add("momentum_loss")
    if (ec("atr_pct") is not None and hc("atr_pct") is not None
        and hc("atr_pct")>=1.5*max(.1,ec("atr_pct"))):
        activated.add("volatility_rise")
    if (ec("market_score") is not None and ec("market_score")>=25
        and hc("market_score") is not None and hc("market_score")>=10):
        activated.add("stable_market")
    if (ec("rsi") is not None and 42<=ec("rsi")<=68
        and ec("volume_ratio") is not None and 1<=ec("volume_ratio")<=2.5
        and (ec("market_score") is None or ec("market_score")>=0)):
        activated.add("trend_hold")
    return activated


def _mean(rows):
    return round(sum(rows)/len(rows),3) if rows else None


def _details(row,link):
    outcome=row.get("closed_roi")
    closed=outcome is not None
    # Unclosed positions enter the pattern-observation cohort only. Never use
    # their mark-to-market P&L as a historically realized trade.
    if not closed:
        outcome=row.get("open_roi")
    if outcome is None:return None
    diagnostic={"key":row["key"],"outcome":float(outcome),"closed":closed,
                "first":row.get("at"),"features":_features(link)}
    diagnostic["precursors"]=_precursors(link)
    return diagnostic


def _best_pattern(observed,cohort,prefer_loss,allow_hold=True):
    subset=[r for r in observed if r["outcome"]<0] if prefer_loss else [
            r for r in observed if r["outcome"]>0]
    if not subset:return None
    candidates=[]
    for key,name,action in PRECURSORS:
        exposed=[r for r in subset if key in r["precursors"]]
        if not exposed:continue
        comparator=[r for r in observed if key not in r["precursors"]]
        n=len(exposed)
        other=_mean([r["outcome"] for r in comparator])
        group_avg=_mean([r["outcome"] for r in exposed])
        contrast=(other-group_avg if prefer_loss else group_avg-other) if other is not None else None
        # Require a coherent effect vs comparator. With only one sample,
        # display an observation, not a statistically supported root cause.
        rank=10*n/max(1,len(subset))+max(0,contrast or 0)
        if key in ("market_volume","heat_chase","market_loss","momentum_loss"):
            rank+=1.5
        candidates.append((rank,key,n,group_avg,contrast,len(comparator)))
    if not candidates:return None
    _,key,n,mean_roi,contrast,comp_n=max(candidates,
        key=lambda a:(a[0],-CAUSAL_ORDER[a[1]]))
    return {"code":key,"title":TEXT[key][0],"action":TEXT[key][1],
            "n":n,"cohort_n":len(subset),"avg_return_pct":mean_roi,
            "relative_pp":round(contrast,3) if contrast is not None else None,
            "comparison_n":comp_n,
            "basis":"종료거래" if all(r["closed"] for r in exposed) else
                    "보유 중 관측 포함"}


# A holding-phase change cannot be used as an entry-time filter.
# Its exit benefit requires timestamped trigger and executable fill evidence,
# not a retrospective "we would not have bought" assumption.
ENTRY_FILTERS=frozenset({
    "market_weak","market_volume","heat_chase","volatility_risk",
    "thin_bid","excess_gap"
})

def _scenario(closed,pattern):
    if not pattern or not closed:return None
    if pattern["code"] not in ENTRY_FILTERS:return None
    matched=[r for r in closed if pattern["code"] in r["precursors"]]
    if not matched:return None
    # Same-trade-count opportunity average with skipped trades set to 0.
    # This is an IN-SAMPLE price-only scenario, not a proven/live uplift.
    baseline=sum(r["outcome"] for r in closed)/len(closed)
    after=sum(r["outcome"] for r in closed if r not in matched)/len(closed)
    return {"baseline_pct":round(baseline,3),"scenario_pct":round(after,3),
            "delta_pp":round(after-baseline,3),"excluded_n":len(matched),
            "sample_n":len(closed),"validated":False,
            "method":"IN_SAMPLE_AVOID_ENTRY","label":pattern["title"]}


def diagnose(frame_result,trades,lifecycles,market,strategy,frame):
    bykey={x["position_key"]:x for x in lifecycles if x.get("frame")==frame}
    observed=[]
    for trade in trades:
        if trade.get("frame")!=frame:continue
        row=trade.get("raw") or {}
        key=row.get("position_key") or journal._key(row,strategy,"position")
        link=bykey.get(key)
        if link is None:continue
        detail=_details(trade,link)
        if detail is not None:observed.append(detail)
    closed=[r for r in observed if r["closed"]]
    negative=_best_pattern(observed,closed,True)
    positive=_best_pattern(observed,closed,False)
    if negative is None:
        down="손실 거래의 선행조건 추가 비교 중"
    else:
        down=negative["title"]
    if positive is None:
        up="상승 거래의 선행조건 추가 비교 중"
    else:
        up=positive["title"]
    # Underlying cause is a research hypothesis, NEVER "stock fell 10%".
    # Show the main *pre-event* condition or deterioration while holding.
    if negative:
        cause=(negative["title"]+"이 손실 거래에 반복 관측됨")
        recommendation=negative["action"]
    elif positive:
        cause="상승 거래에서 "+positive["title"]+"이 반복 관측됨"
        recommendation=positive["action"]+" · 손실 비교 후 기준 조정"
    elif observed:
        cause="현재 보유종목의 진입조건·시장변화와 손익을 비교 중"
        recommendation="관측된 진입조건 유지 · 신규 거래와 비교"
    else:
        cause="종목별 매수·보유·매도 기록의 선행조건 수집 중"
        recommendation="현재 전략 조건 유지 · 신규 관측 축적"
    effect=_scenario(closed,negative)
    pattern_n=(negative["n"] if negative else 0)
    result={
        "cause":cause,
        "decline":down,
        "rise":up,
        "recommendation":recommendation,
        "effect":effect,
        "negative":negative,
        "positive":positive,
        "matched_trades":len(observed),
        "realized_trades":len(closed),
        "pattern_support":pattern_n,
        "sample_n":len(observed),
        "confidence":"검증 필요" if len(closed)<20 else
                     "최근 표본 별도 검증 필요",
    }
    frame_result["diagnosis"]=result
    return frame_result
