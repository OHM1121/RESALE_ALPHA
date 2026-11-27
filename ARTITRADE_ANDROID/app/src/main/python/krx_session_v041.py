"""KRX regular-session calendar for market-score recording (not trade routing).

Stocks may only receive a "current" score while the regular KRX session is
open. Uses Asia/Seoul independently of the device timezone. Announced 2026
holiday schedule and provisional 2027 dates; unusual closures require an update.
No broker connection or orders are changed.
"""
from __future__ import annotations

import datetime as dt
KST=dt.timezone(dt.timedelta(hours=9),"KST")
CLOSED_2026={
    "2026-01-01","2026-02-16","2026-02-17","2026-02-18",
    "2026-03-02","2026-05-01","2026-05-05","2026-05-25",
    "2026-06-03","2026-07-17","2026-08-17",
    "2026-09-24","2026-09-25","2026-10-05",
    "2026-10-09","2026-12-25","2026-12-31",
}
# Provisional: recheck KRX's official holiday publication at year-end.
CLOSED_2027={
    "2027-01-01","2027-02-08","2027-02-09","2027-03-01",
    "2027-05-03","2027-05-05","2027-05-13","2027-07-19",
    "2027-08-16","2027-09-14","2027-09-15","2027-09-16",
    "2027-10-04","2027-10-11","2027-12-27","2027-12-31",
}
CLOSED={2026:CLOSED_2026,2027:CLOSED_2027}
# 2026 national college admission exam trading hours formally announced.
SPECIAL_SESSIONS={"2026-11-19":(10*60,16*60+30)}
START=9*60
END=15*60+30


def status(at=None):
    """(open: bool, reason: str), state from KST date and trading hours."""
    if at is None:
        stamp=dt.datetime.now(KST)
    elif isinstance(at,dt.datetime):
        stamp=at.replace(tzinfo=KST) if at.tzinfo is None else at.astimezone(KST)
    elif isinstance(at,dt.date):
        stamp=dt.datetime.combine(at,dt.time(12,0),KST)
    else:
        stamp=dt.datetime.fromtimestamp(float(at),KST)
    date=stamp.date()
    day=date.isoformat()
    if date.weekday()>=5:
        return False,"휴장 · 주말"
    if date.year not in CLOSED:
        # Trading-hours control must not silently assume an unverified future
        # market calendar is current. The public broker feed can be added here.
        return False,"거래 일정 확인 필요"
    if day in CLOSED[date.year]:
        return False,"휴장 · 거래소 휴장일"
    minute=stamp.hour*60+stamp.minute
    start,end=SPECIAL_SESSIONS.get(day,(START,END))
    if minute<start:
        return False,"개장 전"
    if minute>=end:
        return False,"정규장 종료"
    return True,"장중"


def is_regular_open(at=None):
    return status(at)[0]


AFTER_HOURS_END=20*60          # the NXT after-market closes at 20:00


def after_hours_status(at=None):
    """(open: bool, reason): a trading day, after the KRX regular session and before the NXT after-market close (20:00).

    Used ONLY to keep recording the stock market score after the close (owner 2026-11-27: stock buys happen 16:00-20:00 and had no score).
    It does not open any order path. The NXT closed day of 2026-11-19 (college admission exam) has no after-hours window.
    """
    if at is None:
        stamp=dt.datetime.now(KST)
    elif isinstance(at,dt.datetime):
        stamp=at.replace(tzinfo=KST) if at.tzinfo is None else at.astimezone(KST)
    elif isinstance(at,dt.date):
        stamp=dt.datetime.combine(at,dt.time(12,0),KST)
    else:
        stamp=dt.datetime.fromtimestamp(float(at),KST)
    date=stamp.date()
    day=date.isoformat()
    if date.weekday()>=5 or date.year not in CLOSED or day in CLOSED[date.year]:
        return False,"휴장"
    if day=="2026-11-19":
        return False,"NXT 휴장"
    minute=stamp.hour*60+stamp.minute
    end=SPECIAL_SESSIONS.get(day,(START,END))[1]
    if minute<end:
        return False,"정규장 또는 개장 전"
    if minute>=AFTER_HOURS_END:
        return False,"NXT 애프터마켓 종료"
    return True,"시간외"


def venue_bounds(at=None,venue="KRX"):
    """Explicit per-date exchange schedule, not a recurring exam-day rule.

    All exceptional sessions have a FULL ISO date. Unlisted dates use the
    normal official schedule; fresh broker 0B/0D remains mandatory to trade.
    """
    if at is None:now=dt.datetime.now(KST)
    elif isinstance(at,dt.datetime):now=at.astimezone(KST) if at.tzinfo else at.replace(tzinfo=KST)
    elif isinstance(at,dt.date):now=dt.datetime.combine(at,dt.time(12,0),KST)
    else:now=dt.datetime.fromtimestamp(float(at),KST)
    if venue=="KRX":
        return SPECIAL_SESSIONS.get(now.date().isoformat(),(START,END))
    if venue=="NXT":
        if now.date().isoformat()=="2026-11-19":return None
        return (8*60,8*60+30)
    return None


def in_regular_clock(at,venue="KRX"):
    """Clock gate only. Never asserts market open or orders executable."""
    v=venue_bounds(at,venue)
    if v is None:return False
    t=at.astimezone(KST) if at.tzinfo else at.replace(tzinfo=KST)
    m=t.hour*60+t.minute
    return v[0]<=m<v[1]
