"""v095 original-ledger BUY/SELL causal reasons for UI and manual AI analysis.

No claim of an indicator not present as a qualifying source candidate.
Read-only joins use market+strategy+frame+symbol+position ID+exact execution
ID and optional timestamp. Present technical condition from the ORIGINAL
candidate/exit rule, not execution price source ("Upbit public ask") slogans.
No order, broker balance, AI score or position changes.
"""
from __future__ import annotations
import datetime as dt
import json
import math
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

FRAMES={'5m':'5분봉','30m':'30분봉','60m':'60분봉','day':'일봉'}
UP_TARGET={'5m':'2%','30m':'2.5%','60m':'3%','day':'5%'}
MAX_SUMMARY=120


def _num(value):
    try:
        x=float(value)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError,OverflowError):return None


def _price(v):
    n=_num(v)
    return (('%g'%n) if n is not None else '실제 체결가 미기록')


def _db(root,market,strategy):
    base=Path(root)
    if strategy=='ma':
        if market=='stock':
            names=('stock/MA_EPOCH_V067/STOCK_MA_ENGINE_V1/ma_paper.sqlite3',
                   'stock/MA_EPOCH_V062/STOCK_MA_ENGINE_V1/ma_paper.sqlite3',
                   'stock/STOCK_MA_ENGINE_V1/ma_paper.sqlite3')
        else:
            names=('coin/ma_epoch_v067/upbit_paper.sqlite3',
                   'coin/ma_epoch_v062/upbit_paper.sqlite3',
                   'coin/ma/upbit_paper.sqlite3')
        return [(base/p,p) for p in names]
    if strategy=='trend':
        p=market+'/trend/market_trend_wallet_v054.sqlite3'
        return [(base/p,p)]
    return []


def _original_candidate(con,market,pos):
    try:
        if market=='stock':
            row=con.execute("SELECT features FROM candidates WHERE id=?",
                            (pos['candidate_id'],)).fetchone()
        else:
            row=con.execute("""SELECT reason,features FROM candidates
                WHERE tf=? AND market=? AND ref_at=? LIMIT 1""",
                (pos['tf'],pos['market'],pos['ref_at'])).fetchone()
    except (sqlite3.Error,KeyError):return {},''
    if row is None:return {},''
    try:info=json.loads(row['features'] or '{}')
    except (ValueError,TypeError):info={}
    return (info if isinstance(info,dict) else {},
            str(row['reason'] or '') if 'reason' in row.keys() else '')


def _ma_entry(features,candidate_reason,frame,market):
    kind=str(features.get('ma_v067_kind') or features.get('ma_v061_kind') or '')
    bar=FRAMES.get(frame,frame)
    data={}
    if kind=='REVERSAL_UP':
        delta=_num(features.get('ma_v067_120_delta'))
        description=(bar+' MA 상승전환: 5·10·20·60 이동평균선 모두 120선 아래, '
             '직전 확정봉에는 미충족이던 네 단기선 기울기가 현재봉에서 처음 양수 전환. '
             'MA120 기울기 엄격한 음수, 봉 종료 10~2초 전 검증된 상승전환봉 진입')
        if delta is not None:
            description+='; 저장된 MA120 변화량 '+_price(delta)
            data['ma120_delta']=delta
        if features.get('ma_v067_bar_start'):
            description+='; 신호봉 '+str(features['ma_v067_bar_start'])[:28]
        return description,'ORIGINAL_MA_V067_CANDIDATE',data
    if kind=='REVERSAL_BEAR':
        description=(bar+' MA 하락반전: 5·10·20·60 이동평균선이 120선 아래에서 '
                     '음의 기울기 정렬을 유지한 상태로, 확정된 대음봉 조건 충족')
        body=_num(features.get('ma_v067_body_pct'))
        if body is not None:
            description+='; 신호봉 음봉 몸통비율 '+_price(body)+'%'
            data['bear_body_pct']=body
        high=features.get('ma_v067_high')
        if _num(high) is not None:description+='; 기준봉 고가 '+_price(high)
        return description,'ORIGINAL_MA_V067_CANDIDATE',data
    if kind=='TURNPOINT':
        return (bar+' 과거 MA 상승전환: 구버전 원본 매수후보의 전환점 조건 충족',
                'ORIGINAL_MA_V061_CANDIDATE',{})
    if kind=='DOWN_BEAR':
        return (bar+' 과거 MA 하락반전: 구버전 원본 대음봉 후보 조건 충족',
                'ORIGINAL_MA_V061_CANDIDATE',{})
    if candidate_reason:
        return ('원본 MA 후보조건 '+str(candidate_reason)[:140],
                'ORIGINAL_MA_CANDIDATE_REASON',{})
    return None,None,{}


def _ma_exit(reason,frame,pos,fill):
    reason=str(reason or '')
    frame_target=UP_TARGET.get(frame,'해당 주기 목표')
    price=_price(fill['price'])
    prefix=(FRAMES.get(frame,frame)+' MA · ')
    tags={
        'MA_V1118_UP_FIRST_30PCT':'NEW LEGACY 11.18 1차 매도: 시간봉별 매수가 +2/3/3.5/4% 목표 도달, 최초 수량 30%',
        'MA_V1118_UP_SECOND_35PCT':'NEW LEGACY 11.18 2차 매도: 첫 목표보다 +2/2/2.5/3% 상승, 최초 수량 35%',
        'MA_V1118_UP_AFTER_SECOND_TRAIL':'NEW LEGACY 11.18 3차: 최고가 대비 시간봉별 -2.5/-3/-3/-4% 하락시 잔량 전량',
        'MA_V1118_STOP_MINUS_15':'NEW LEGACY 11.18 손절: 실제 가격 매수가 -15% 도달, 전량매도',
        'MA_NL1120_LOOKBACK_LOW_CLOSE_STOP':'NEW LEGACY 11.20(2) 손절: 매수 직전 완성봉 50개 중 최저가를 정한 손절가 아래로 완성봉 종가 마감, 전량매도',
        'MA_V1118_HOLD_EXPIRY':'NEW LEGACY 11.18 보유기한 만료: 48/80/120/360시간 후 첫 유효 시세에서 전량',
        'MA_V065_UP_FIRST_10PCT':f'1차 매도: 실제 최우선 매수호가가 매수가 대비 +{frame_target} 목표에 도달, 최초 매수량 10% 청산',
        'MA_V065_UP_SECOND_HALF_REMAINING':'2차 매도: 1차 목표가에서 추가 목표 상승에 도달, 1차 매도 후 잔량의 50% 청산',
        'MA_V065_UP_AFTER_SECOND_FIRST_TARGET_FLOOR':'3차 전량매도: 2차 매도 후 주가가 1차 목표가 이하로 하락',
        'MA_V065_UP_PEAK_MINUS_3':'3차 전량매도: 2차 이후 관측 최고가 대비 3% 하락',
        'MA_V065_UP_AFTER_FIRST_ENTRY_PLUS_1':'잔량 전량매도: 1차 매도 후 매수가 대비 +1% 이하로 하락',
        'MA_V065_UP_PRE_FIRST_ENTRY_RETURN':'1차 매도 전 전량매도: 매수가 상향 이탈 뒤 매수가 이하 회귀',
        'MA_V065_FIRST_30_AT_REVERSAL_HIGH':'1차 매도: 하락반전 기준봉 고가에 도달, 최초 매수량 30% 청산',
        'MA_V065_SECOND_HALF_REMAINING':'2차 매도: 기준봉 고가 대비 추가 2% 상승, 1차 잔량의 절반 청산',
        'MA_V065_HIGH_TARGET_MIDPOINT_STOP':'3차 전량매도: 기준봉 고가와 2차 목표가의 중간지점까지 하락',
        'MA_V065_PEAK_MINUS_3':'3차 전량매도: 2차 이후 최고가 대비 3% 하락',
        'MA_V065_FORCE_120_UP_FOUR_ABOVE_TURN_DOWN':'위험관리 강제매도: MA120 상승 상태에서 네 단기선이 MA120 위에서 동시 음의 기울기 전환',
        'MA_V067_SECOND_FOLLOWUP_BELOW_ENTRY':'손실방어 전량매도: 매수봉 뒤 두 번째 후속 봉의 확인가격이 매수가 미만',
        'MA_V061_FIRST_20PCT':'구버전 상승전환 1차 매도: 최초 수량의 20% 목표가격 청산',
    }
    why=tags.get(reason)
    if not why:
        if reason.startswith('USER_SIM_MANUAL:'):
            why='사용자가 직접 요청한 가상 강제매도 (자동 전략 목표가 도달 아님)'
        elif reason.startswith('AUTO_NIGHT_EWY_PREEMPTIVE_STOP'):
            why='야간 EWY 하락폭이 손절가에 닿을 것으로 판단되어 장 시작 직후 자동 전량매도'
        elif reason:
            why='원본 매도조건 식별자 '+reason[:110]+' (상세 분기 원본 확인 필요)'
        else:return None
    return prefix+why+'; 실제 SELL 체결가 '+price


def _trend_entry(pos,fill,frame):
    try:p=json.loads(pos['payload'] or '{}')
    except (ValueError,TypeError):p={}
    if not isinstance(p,dict):p={}
    rsi=_num(p.get('rsi20_live'))
    if rsi is None or not str(fill['reason'] or '').startswith('MARKET_TREND_RSI20_BUY'):
        return None,None,{}
    if (str(p.get('entry_signal_policy') or '').startswith('NL1118_') and
            p.get('entry_rule')=='NL1120_BAR_CLOSE_ABOVE_BOLL_MID'):
        # 11.20 workbook: RSI watch <=24h, Bollinger MID 20/1.5 slope strictly
        # (-15,+20) and the COMPLETED signal bar closed above the MID.
        reason=(FRAMES.get(frame,frame)+' 급락 전환봉: Wilder RSI(20) '
                +_price(rsi)+' (30 이하 관측 후 최대 24시간 추적), '
                '볼린저밴드(20,1.5) 중앙선 기울기 -15° 초과/+20° 미만, '
                '확정된 봉의 종가가 중앙선 위에서 형성되어 검증된 PAPER 매수')
    elif str(p.get('entry_signal_policy') or '').startswith('NL1118_'):
        # Positions bought under the 11.19 rule keep their original evidence:
        # slope strictly (-20,+30) and a genuine exchange print crossed MID.
        reason=(FRAMES.get(frame,frame)+' 급락 전환봉: Wilder RSI(20) '
                +_price(rsi)+' (30 이하 관측 후 최대 24시간 추적), '
                '볼린저밴드(20,1.5) 중앙선 기울기 -20° 초과/+30° 미만, '
                '실시간 가격 중앙선 도달에 따른 검증된 PAPER 매수')
    else:
        # Older already-owned positions retain their proven original evidence.
        reason=(FRAMES.get(frame,frame)+' 마켓트렌드: Wilder RSI(20) '
               +_price(rsi)+'로 30 이하; 기존 기록 기준의 매수')
    if p.get('bar_key'):reason+='; 신호봉 '+str(p['bar_key'])[:30]
    return reason,'ORIGINAL_MT54_RSI20_EXECUTION',{'rsi20_live':rsi}


def _trend_exit(reason,pos,fill,frame):
    tags={
       'MT1118_STOP_15PCT':'NEW LEGACY 11.18 손절: 매수가 대비 -15% 전량',
       'NL1120_LOOKBACK_LOW_CLOSE_STOP':'NEW LEGACY 11.20(2) 손절: 매수 직전 완성봉 200/90/90개 중 최저 저가를 손절가로 정하고 완성봉 종가가 아래로 마감, 전량매도',
       'MT1118_HOLD_DEADLINE':'NEW LEGACY 11.18 보유기한 100시간/8일/15일 만료 전량',
       'NL1118_AFTER_FIRST_PLUS_1_STOP':'NEW LEGACY 11.18 첫 익절 뒤 매수가+1% 방어선까지 회귀, 잔량 전량',
       'NL1118_AFTER_SECOND_FIRST_FLOOR':'NEW LEGACY 11.18 두번째 익절 뒤 첫 목표가까지 회귀, 잔량 전량',
       'NL1118_AFTER_SECOND_TRAIL':'NEW LEGACY 11.18 두번째 익절 뒤 최고가 대비 -4/-5/-6% 추적청산',
       'NL1118_PRE_FIRST_RETURN_TO_ENTRY':'NEW LEGACY 11.18 첫 목표가 미달 상태 매수가 상향 후 회귀 전량',
       'MT_TP1':'1차 매도: 1차 목표가 도달, 최초 매수량 30%',
       'MT_TP2':'2차 매도: 2차 목표가 도달, 최초 매수량 35%',
       'MT_TP3_TRAIL':'3차 잔량 전량매도: 2차 목표가 이하 회귀 또는 2차 이후 최고가 대비 4% 하락',
       'MT_STOP_9PCT':'손절 전량매도: 매수가 대비 -9% 이하',
       'MT_HOLD_DEADLINE':'보유기한 만료에 따른 잔량 전량매도',
       'MT_RSI_3_BARS':'후속 3개봉 RSI 관측 종료에 따른 잔량 전량매도',
       'MT_TP1_RETOUCH':'1차 매도 목표가 재이탈에 따른 잔량 전량매도',
       'MT_ENTRY_RETOUCH':'매수가 위로 상승 후 매수가 이하 복귀에 따른 전량매도'}
    why=tags.get(str(reason or ''))
    if why is None:
        if str(reason or '').startswith('USER_SIM_MANUAL:'):
            why='사용자가 직접 지시한 가상 매도 (자동 목표조건 아님)'
        elif str(reason or '').startswith('AUTO_NIGHT_EWY_PREEMPTIVE_STOP'):
            why='야간 EWY 하락폭이 손절가에 닿을 것으로 판단되어 장 시작 직후 자동 전량매도'
        elif reason:why='원본 마켓트렌드 매도식별자 '+str(reason)[:110]+' (상세 분기 미기록)'
        else:return None
    try:p=json.loads(pos['payload'] or '{}')
    except (ValueError,TypeError):p={}
    if not isinstance(p,dict):p={}
    target=(p.get('target1') if reason in ('MT_TP1','MT_TP1_RETOUCH') else
            p.get('target2') if reason in ('MT_TP2','MT_TP3_TRAIL') else None)
    if _num(target) is not None:why+='; 저장된 목표가 '+_price(target)
    return (FRAMES.get(frame,frame)+' 마켓트렌드 · '+why+
            '; 실제 SELL 체결가 '+_price(fill['price']))


def from_original(root,market,strategy,frame,symbol,pid,side,
                  execution_id='',at=None):
    """Only one exact source position and an actual matched fill qualify."""
    if (market not in ('stock','coin') or strategy not in ('ma','trend')
        or not symbol or not pid or side not in ('BUY','SELL')):return {}
    matched=[]
    for path,relative in _db(root,market,strategy):
        if not path.is_file():continue
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',
                        uri=True,timeout=2)) as c:
                c.row_factory=sqlite3.Row
                position=c.execute('SELECT * FROM positions WHERE id=? AND tf=?',
                    (str(pid),frame)).fetchone()
                if position is None:continue
                identifier=('code' if market=='stock' and strategy=='ma' else
                            'market' if strategy=='ma' else 'symbol')
                if str(position[identifier])!=str(symbol):continue
                if execution_id:
                    fill=c.execute("""SELECT * FROM executions WHERE id=?
                        AND position_id=? AND side=?""",
                        (str(execution_id),str(pid),side)).fetchone()
                else:
                    fill=c.execute("""SELECT * FROM executions WHERE position_id=?
                        AND side=? ORDER BY id DESC LIMIT 1""",
                        (str(pid),side)).fetchone()
                if fill is None:continue
                # An identical position ID can exist in old/new SQLite.
                # Fill IDs (or an execution timestamp) must disambiguate.
                if at is not None:
                    source_stamp=fill['ts'] if 'ts' in fill.keys() else fill['at']
                    try:
                        source_time=dt.datetime.fromisoformat(str(source_stamp)).timestamp()
                        if abs(source_time-float(at))>120:continue
                    except (ValueError,TypeError):continue
                if strategy=='ma':
                    if side=='BUY':
                        f,candidate=_original_candidate(c,market,position)
                        desc,origin,metrics=_ma_entry(f,candidate,frame,market)
                    else:
                        desc=_ma_exit(fill['reason'],frame,position,fill)
                        origin='ORIGINAL_MA_SELL_EXECUTION'
                        metrics={}
                else:
                    if side=='BUY':desc,origin,metrics=_trend_entry(position,fill,frame)
                    else:
                        desc=_trend_exit(fill['reason'],position,fill,frame)
                        origin='ORIGINAL_MT54_SELL_EXECUTION';metrics={}
                if desc:
                    matched.append({"verified_trade_basis":desc,
                        "verified_basis_source":origin,
                        "verified_numeric_factors":metrics,
                        "verified_source_file":relative,
                        "verified_original_position_id":str(pid),
                        "verified_original_execution_id":str(fill['id'])})
        except (sqlite3.Error,OSError,ValueError,TypeError,KeyError):
            continue
    return matched[0] if len(matched)==1 else {}


def summary_for_ai(root,market,strategy,limit=MAX_SUMMARY):
    """Manual-AI-only, source-linked observed factors; no model forecast.

    Original ledger is never edited. Cases are matched by original position,
    execution and timestamp; missing evidence is counted instead of guessed.
    """
    findings=[];total=0;unmatched=0;groups=Counter()
    for path,relative in _db(root,market,strategy):
        if not path.is_file():continue
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',
                        uri=True,timeout=3)) as c:
                c.row_factory=sqlite3.Row
                # MA stock has e.ts + p.code; MA coin has e.at + p.market;
                # MT54 has e.at + p.symbol. Never assume the other tables'
                # columns exist: SQLite would otherwise return zero evidence.
                ecols={z[1] for z in c.execute('PRAGMA table_info(executions)')}
                pcols={z[1] for z in c.execute('PRAGMA table_info(positions)')}
                tsfield='e.ts' if 'ts' in ecols else 'e.at'
                codefield=('p.code' if strategy=='ma' and market=='stock'
                           else 'p.market' if strategy=='ma' else 'p.symbol')
                records=c.execute("""SELECT e.id,e.position_id,e.side,
                    """+tsfield+""" AS fill_at,e.reason,e.price,p.tf,
                    """+codefield+""" AS symbol
                    FROM executions e JOIN positions p ON p.id=e.position_id
                    WHERE e.side IN ('BUY','SELL')
                    ORDER BY e.id DESC LIMIT ?""",(min(600,int(limit)),)).fetchall()
                for row in records:
                    total+=1
                    symbol=row['symbol']
                    at_raw=row['fill_at']
                    try:ts=dt.datetime.fromisoformat(str(at_raw)).timestamp()
                    except (ValueError,TypeError):ts=None
                    found=from_original(root,market,strategy,str(row['tf']),
                        str(symbol),str(row['position_id']),str(row['side']),
                        str(row['id']),at=ts)
                    if found:
                        groups[str(row['side'])]+=1
                        if len(findings)<100:
                            findings.append({"market":market,"strategy":strategy,
                                "frame":str(row['tf']),"symbol":str(symbol),
                                "position_id":str(row['position_id']),
                                "execution_id":str(row['id']),
                                "side":str(row['side']),"source":found['verified_basis_source'],
                                "reason":found['verified_trade_basis'],
                                "numeric_factors":found['verified_numeric_factors']})
                    else:unmatched+=1
        except (sqlite3.Error,OSError,ValueError,TypeError,KeyError):
            continue
    return {"schema":"ARTITRADE_VERIFIED_ORIGINAL_TRADE_BASIS_V095",
        "market":market,"strategy":strategy,"read_only":True,
        "verified_fills":sum(groups.values()),"unmatched_source_fills":unmatched,
        "examined_fills":total,"verified_buy_count":groups['BUY'],
        "verified_sell_count":groups['SELL'],
        "causal_origin":"ORIGINAL_CANDIDATE_PRE_ENTRY_ONLY",
        "original_trade_ledger_untouched":True,
        "ai_uses_exact_confirmed_execution":True,
        "explanations":findings}
