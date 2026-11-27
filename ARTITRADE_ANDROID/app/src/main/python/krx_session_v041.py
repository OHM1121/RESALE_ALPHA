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
CLOSING_AUCTION_MINUTES=10     # KRX closing single-price auction 15:20-15:30 (owner 2026-11-27): orders are accepted, the quote is frozen, nothing is matched until the single price at 15:30


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
    if minute>=end-CLOSING_AUCTION_MINUTES:
        return True,"종가 단일가"          # still the regular session (scores keep running), but no price movement and no matching: a silent feed is normal here
    return True,"장중"


def is_regular_open(at=None):
    return status(at)[0]


AFTER_HOURS_START=16*60        # the engine's own POST session (d_v19_portfolio.live_session / extended_window): 16:00:00-20:00:00
AFTER_HOURS_END=20*60          # the NXT after-market closes at 20:00
PRE_START=8*60                 # the engine's NXT PRE session: 08:00:00-08:30:00
PRE_END=8*60+30


def after_hours_status(at=None):
    """(open: bool, reason): a trading day inside one of the engine's two extended sessions: NXT PRE 08:00-08:30 or KRX POST 16:00-20:00.

    Used ONLY to keep recording the stock market score outside the regular session (owner 2026-11-27: stock buys happen 16:00-20:00 and had no score; the owner also
    corrected the window: the after-hours market is 16:00-20:00, nothing trades 15:30-16:00). It does not open any order path. The NXT closed day 2026-11-19 has no window.
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
    if PRE_START<=minute<PRE_END:
        return True,"NXT 프리마켓"
    if AFTER_HOURS_START<=minute<AFTER_HOURS_END:
        return True,"시간외"
    return False,"시간외 아님"


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


def in_closing_auction(at=None):
    """True inside the KRX closing single-price auction (the last 10 minutes of the regular session of that date)."""
    if at is None:now=dt.datetime.now(KST)
    elif isinstance(at,dt.datetime):now=at.astimezone(KST) if at.tzinfo else at.replace(tzinfo=KST)
    else:now=dt.datetime.fromtimestamp(float(at),KST)
    if not is_regular_open(now):return False
    end=venue_bounds(now,"KRX")[1]
    m=now.hour*60+now.minute
    return end-CLOSING_AUCTION_MINUTES<=m<end


def _kst(at):
    if at is None:return dt.datetime.now(KST)
    if isinstance(at,dt.datetime):return at.astimezone(KST) if at.tzinfo else at.replace(tzinfo=KST)
    return dt.datetime.fromtimestamp(float(at),KST)


def in_post_clock(at=None):
    """KRX after-hours (POST) session 16:00-20:00, the engine's own table (`d_v19_portfolio.live_session`): from 2026-09-14, 17:00 on the college-exam day. Clock only."""
    now=_kst(at)
    if now.strftime("%Y%m%d")<"20260914":return False
    if now.date().weekday()>=5 or now.date().year not in CLOSED or now.date().isoformat() in CLOSED[now.date().year]:return False
    start=17*60 if now.date().isoformat()=="2026-11-19" else AFTER_HOURS_START
    return start<=now.hour*60+now.minute<AFTER_HOURS_END


def in_tradable_clock(at,venue="KRX"):
    """0.6.6 (owner 2026-11-27: MA, supply-demand and MarketTrend may also trade after hours): the KRX continuous time 09:00-15:20 OR the after-hours session 16:00-20:00.
    The closing auction 15:20-15:30 and the gap 15:30-16:00 are not tradable. Clock only: a stock still needs fresh quotes and, after 16:00, an after-market entitlement."""
    if venue!="KRX":return in_regular_clock(at,venue)
    return in_regular_clock(at,"KRX") or in_post_clock(at)


def is_tradable_session(at=None):
    """Regular continuous time (09:00-15:20, a trading day) or the after-hours session (16:00-20:00 on a trading day)."""
    now=_kst(at)
    if is_regular_open(now):
        return not in_closing_auction(now)
    return after_hours_status(now)[0] and in_post_clock(now)


def in_regular_clock(at,venue="KRX"):
    """Clock gate only. Never asserts market open or orders executable.

    0.6.5: for KRX this is the CONTINUOUS trading time (09:00-15:20): the closing auction (15:20-15:30) matches nothing until 15:30, so the paper matcher of the
    supply-demand strategy and the gap detection do not work on its frozen quotes. NXT is unchanged."""
    v=venue_bounds(at,venue)
    if v is None:return False
    t=at.astimezone(KST) if at.tzinfo else at.replace(tzinfo=KST)
    m=t.hour*60+t.minute
    end=v[1]-(CLOSING_AUCTION_MINUTES if venue=="KRX" else 0)
    return v[0]<=m<end
