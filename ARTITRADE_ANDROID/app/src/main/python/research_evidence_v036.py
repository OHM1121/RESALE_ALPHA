"""Read-only EXE forward-evidence labels: no price-high is ever a SELL fill.

The original APK pulled only actual FIRST_TARGET sell executions and often
omitted the separately recorded observed 1m highs/timestamps. These are
recovered as *observations*, never converted into broker/paper fills.
"""
from __future__ import annotations

def observation_status(row):
    try:
        missed=int(row.get("missing_observation_minutes") or 0)
        seen=int(row.get("observation_minutes") or 0)
    except (TypeError,ValueError):
        return "OBSERVATION_UNKNOWN"
    if missed>0:return "DATA_GAP"
    if seen==0:return "NO_OBSERVATIONS"
    return "OBSERVING"


def from_at_forward(row):
    goal=row.get("first_target")
    ts=row.get("first_observed_target_after_touch")
    try:threshold=float(goal)
    except (ValueError,TypeError):threshold=0.0
    verified=bool(ts) and threshold>0
    return {
        "observed_target_price":goal if verified else None,
        "observed_target_at":ts if verified else None,
        "observed_target_evidence":"STOCK_MINUTE_HIGH_NOT_FILL" if verified else "",
        "observation_status":observation_status(row),
        "sell_evidence":"",
    }


def explain_unobserved(row):
    status=str(row.get("observation_status") or observation_status(row))
    if status=="DATA_GAP":
        return "원본 1분봉 관측 누락 · 미관측분은 목표 미도달로 계산하지 않음"
    if status=="NO_OBSERVATIONS":
        return "후속 1분봉 관측자료 0건 · 시세 수집/승계 상태 확인"
    if status=="OBSERVING":
        return "후속 시세 관측 중 · 실제 매도 체결 별도 확인"
    return "원본 연구 관측상태 확인 필요"


def coin_shadow_observed_target(rows,goal,entry_at):
    """Only actual observed BOOK_BID shadow paths after virtual entry."""
    if not entry_at:return None
    try:target=float(goal)
    except (ValueError,TypeError):return None
    if target<=0:return None
    matches=[]
    for r in rows or ():
        if not isinstance(r,dict) or str(r.get("source") or "")!="UPBIT_BOOK_BID":
            continue
        # 1m bucket high has no exact tick instant; bucket's last_at is
        # the earliest conservative time at which that HIGH is known.
        when=str(r.get("last_at") or "")
        try:high=float(r.get("bid_high"))
        except (TypeError,ValueError):continue
        if when and when>=str(entry_at) and high>=target:
            matches.append((when,target))
    if not matches:return None
    first=min(matches)
    return {"observed_target_price":first[1],
            "observed_target_at":first[0],
            "observed_target_evidence":"COIN_BOOK_BID_HIGH_NOT_FILL"}
