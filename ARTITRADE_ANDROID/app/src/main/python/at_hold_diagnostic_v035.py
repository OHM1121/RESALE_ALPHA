"""Read-only 1h stock AT holding-age explanation, not a sell policy.

Uses the position's saved hold_days. No simulated fill, fake quote,
retroactive exit, default guessed hold-days or order modification.
"""
from __future__ import annotations

from datetime import date,datetime,timedelta
from pathlib import Path


def _days(raw):
    if isinstance(raw,datetime):return raw.date()
    if isinstance(raw,date):return raw
    value=str(raw or "")
    if len(value)>=10 and value[4]=="-" and value[7]=="-":
        return date.fromisoformat(value[:10])
    if len(value)>=8 and value[:8].isdigit():
        return datetime.strptime(value[:8],"%Y%m%d").date()
    raise ValueError("Unrecognized trading date")


def _closed(root=None,additional=()):
    dates=set(additional or [])
    try:
        from d_calendar import STATIC_CLOSED,load_closed_overrides
        dates.update(STATIC_CLOSED)
        if root is not None:dates.update(load_closed_overrides(Path(root)))
    except (ImportError,OSError):
        # A missing official calendar must not be mistaken for verified KRX
        # session entitlement. The projection below remains explicitly estimated.
        pass
    return dates


def _market_day(day,closed):
    return day.weekday()<5 and day.strftime("%Y%m%d") not in closed


def diagnose_at_position(position,now,*,root=None,closed_dates=()):
    if not isinstance(position,dict):
        raise TypeError("Position must be a ledger dict")
    now_day=_days(now)
    try:
        first=_days(position.get("entry_ts") or position.get("entry_at"))
    except (ValueError,TypeError):
        return {"status":"MISSING_BUY_TIME","sessions_elapsed":None,"hold_days":None,
                "maturity_date":None,"sold":False,
                "exit_scenario":"매수일자 확인 불가 · 보유기간 계산 불가"}
    try:
        hold=int(position["hold_days"])
        if hold<1:raise ValueError()
    except (ValueError,TypeError,KeyError):
        return {"status":"MISSING_HOLD_POLICY","sessions_elapsed":None,"hold_days":None,
                "maturity_date":None,"sold":False,
                "exit_scenario":"원장에 보유기간 정책 미기록 · 임의 만기 설정 안 함"}
    closed=_closed(root,closed_dates)
    elapsed=0
    day=first
    while day<=now_day:
        if _market_day(day,closed):elapsed+=1
        day+=timedelta(days=1)
    maturity=first
    counted=0
    for _ in range(500):
        if _market_day(maturity,closed):
            counted+=1
            if counted==hold:break
        maturity+=timedelta(days=1)
    else:
        raise ValueError("Unsupported holding deadline")
    until=maturity.isoformat()
    note=f"{elapsed}/{hold}거래일 · 예상 만기 {until} (매수일 포함)"
    first_sold=bool(position.get("first_sold"))
    trailing=bool(position.get("hold_profit_trail"))
    if str(position.get("status") or "").upper()=="CLOSED":
        status="ALREADY_CLOSED"
        msg="원장상 이미 종료 · 체결내역과 화면 갱신 상태 확인"
    elif first_sold:
        status="PARTIAL_EXIT_TRAILING"
        msg="1차 매도 이후 잔여물량 별도 추적 중 · 만기 강제매도 규칙 제외"
    elif trailing:
        status="PROFIT_TRAILING"
        msg="만기 순이익 발생으로 고점추적모드 전환 · 즉시 전량매도 아님"
    elif elapsed<hold:
        status="NOT_MATURED"
        msg="홀딩기간 미도래 · 조기목표·손절은 별도 평가"
    else:
        status="MATURE_AWAITING_VERIFIED_TICK"
        msg=("만기 도래 · 실제 KRX 0B 신선틱 시각, 수익여부, "
             "주문차단·체결 여부 확인 필요 (일반장 15:19:15 / 검증된 연장장 19:59:15)")
    return {"status":status,"sessions_elapsed":elapsed,"hold_days":hold,
            "maturity_date":until,"sold":False,"entry_at":str(position.get("entry_ts") or ""),
            "exit_scenario":msg+" · "+note,
            "note":"실시간 시세와 실제 매도체결을 조회한 결과는 아님"}
