"""Read-only, reproducible, quantitative AI evidence bundle for ARTITRADE.

The Android SAF export writes these human/agent-readable summaries alongside
unmodified WAL-safe copies of ALL eligible strategy SQLite/JSON originals.
No entry/exit condition, balance, live broker state or research history changes.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import ai_strategy_analysis_v037 as base

SCHEMA="ARTITRADE_AI_DEEP_EVIDENCE_V046"
KST=dt.timezone(dt.timedelta(hours=9))
SOURCE=base.SOURCE


def _read_json(value):
    result=json.loads(value) if isinstance(value,str) else value
    if not isinstance(result,dict):
        raise ValueError("expected JSON object")
    return result


def _database(root,relative):
    file=Path(root)/relative
    if not file.is_file() or file.is_symlink():
        return None
    return sqlite3.connect(file.resolve().as_uri()+"?mode=ro",uri=True,timeout=8)


def _has(conn,table):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)
    ).fetchone())


def _table_cols(conn,table):
    escaped=table.replace('"','""')
    return [{"name":row[1],"sql_type":row[2],"not_null":bool(row[3]),
             "primary_key":bool(row[5])}
            for row in conn.execute('PRAGMA table_info("'+escaped+'")')]


def _count_groups(conn,table,column,limit=100):
    if not _has(conn,table) or column not in {
        r["name"] for r in _table_cols(conn,table)}:
        return {"available":False,"groups":[]}
    tab=table.replace('"','""')
    col=column.replace('"','""')
    items=conn.execute(
        'SELECT "'+col+'",COUNT(*) FROM "'+tab+'" GROUP BY "'+col+'"'
        ' ORDER BY COUNT(*) DESC LIMIT ?',(limit+1,)
    ).fetchall()
    return {"available":True,"groups":[
        {"value":None if value is None else str(value),"n":int(n)}
        for value,n in items[:limit]],
        "group_cap":limit,"group_limit_reached":len(items)>limit,
        "raw_full_records_in_zip":True}


def _score_metrics(root,market):
    try:
        conn=_database(root,"market_research_v033.sqlite3")
        if conn is None:return {"available":False,"reason":"MARKET_SCORE_DB_MISSING"}
        with closing(conn):
            if not _has(conn,"score_events"):
                return {"available":False,"reason":"SCORE_EVENTS_TABLE_MISSING"}
            row=conn.execute("""
                SELECT COUNT(*),MIN(ts),MAX(ts),MIN(score),MAX(score),
                AVG(score),SUM(CASE WHEN score<0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN score>=0 THEN 1 ELSE 0 END)
                FROM score_events WHERE market=?""",(market,)).fetchone()
            n=int(row[0] or 0)
            return {"available":True,"observations":n,
                    "first_ts":row[1],"last_ts":row[2],
                    "min_score":row[3],"max_score":row[4],
                    "sample_mean_score":round(row[5],4) if row[5] is not None else None,
                    "negative_samples":int(row[6] or 0),
                    "zero_or_positive_samples":int(row[7] or 0),
                    "average_weighting":"SAMPLE_WEIGHTED_NOT_DURATION_WEIGHTED",
                    "note":"점수 횟수는 시장 상태 유지시간이 아님; 누락구간은 보정하지 않음"}
    except (OSError,sqlite3.Error) as exc:
        return {"available":False,"reason":type(exc).__name__}


def _trend_metrics(root,market,strategy):
    try:
        conn=_database(root,"market_trend_v043.sqlite3")
        if conn is None:return {"available":False,"reason":"TREND_JOURNAL_MISSING"}
        with closing(conn):
            result={"available":True,"market":market,"strategy":strategy,
                    "entry_actual_vs_virtual":"Separate paper/live fill events from shadow-only missed signals"}
            if _has(conn,"events"):
                result["decision_dispositions"]=[{
                    "frame":frame,"mode":mode,"reason":reason,"n":int(count)}
                    for frame,mode,reason,count in conn.execute("""
                      SELECT frame,mode,reason,COUNT(*) FROM events
                      WHERE market=? AND strategy=? AND event_key LIKE 'MT43:SIG:%'
                      GROUP BY frame,mode,reason ORDER BY COUNT(*) DESC""",
                      (market,strategy)).fetchall()]
                result["confirmed_fills"]=[{
                    "frame":frame,"execution_mode":mode,"n":int(count)}
                    for frame,mode,count in conn.execute("""
                      SELECT frame,mode,COUNT(*) FROM events
                      WHERE market=? AND strategy=? AND reason='MT_BUY_FILLED'
                      GROUP BY frame,mode ORDER BY frame,mode""",
                      (market,strategy)).fetchall()]
                result["exit_reasons"]=[{
                    "reason":reason,"mode":mode,"n":int(count)}
                    for reason,mode,count in conn.execute("""
                      SELECT reason,mode,COUNT(*) FROM events
                      WHERE market=? AND strategy=?
                        AND event_key LIKE 'MT43:SELL:%'
                      GROUP BY reason,mode ORDER BY COUNT(*) DESC""",
                      (market,strategy)).fetchall()]
                result["events_total"]=conn.execute(
                    "SELECT COUNT(*) FROM events WHERE market=? AND strategy=?",
                    (market,strategy)).fetchone()[0]
                result["all_events_in_original_db"]=True
            if _has(conn,"gate_events"):
                row=conn.execute("""
                    SELECT COUNT(*),MAX(at) FROM gate_events WHERE market=?""",
                    (market,)).fetchone()
                result["mode_switch_events"]={"n":int(row[0]),"latest_epoch":row[1]}
            if _has(conn,"manual_trend_mode"):
                row=conn.execute(
                    "SELECT enabled,changed_at FROM manual_trend_mode WHERE market=?",
                    (market,)).fetchone()
                result["manual_switch"]={"enabled":bool(row[0]) if row else False,
                                         "last_change_epoch":row[1] if row else None}
            if _has(conn,"stop_reentry_blocks"):
                result["stop_day_blocks"]=int(conn.execute("""
                    SELECT COUNT(*) FROM stop_reentry_blocks WHERE market=?""",
                    (market,)).fetchone()[0])
            if _has(conn,"watch_observations"):
                result["rsi20_watch_buckets"]=int(conn.execute("""
                    SELECT COUNT(*) FROM watch_observations WHERE market=?""",
                    (market,)).fetchone()[0])
            return result
    except (OSError,sqlite3.Error) as exc:
        return {"available":False,"reason":type(exc).__name__}


def _source_schema(root,market,strategy):
    specific,_=SOURCE[market,strategy]
    selected=[specific,"market_research_v033.sqlite3",
              "market_trend_v043.sqlite3",
              "stock/ai/AI_RESEARCH_V040.sqlite3" if market=="stock"
              else "coin/ai/AI_RESEARCH_V040.sqlite3"]
    result=[]
    for rel in selected:
        try:
            conn=_database(root,rel)
            if conn is None:
                result.append({"source":rel,"available":False})
                continue
            with closing(conn):
                tables=[]
                for (name,) in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                    tables.append({"table":name,"columns":_table_cols(conn,name)})
                result.append({"source":rel,"available":True,
                               "tables":tables,
                               "original_database_in_zip":True})
        except (OSError,sqlite3.Error) as exc:
            result.append({"source":rel,"available":False,
                           "error":type(exc).__name__})
    return result


def _account_metrics(root,market,strategy):
    source,_=SOURCE[market,strategy]
    try:
        conn=_database(root,source)
        if conn is None:return {"available":False,"source":source}
        with closing(conn):
            result={"available":True,"source":source}
            for table,field in (("positions","status"),("executions","side"),
                                ("orders","status"),("pending","status"),
                                ("candidates","status")):
                if _has(conn,table):
                    result[table]={"count":int(conn.execute(
                        'SELECT COUNT(*) FROM "'+table+'"').fetchone()[0]),
                        "by_"+field:_count_groups(conn,table,field)}
            return result
    except (OSError,sqlite3.Error) as exc:
        return {"available":False,"source":source,
                "error":type(exc).__name__}


def _frame_evidence(frame):
    diagnostic=frame.get("diagnosis") or {}
    effect=diagnostic.get("effect") or frame.get("improvement") or {}
    if not isinstance(effect,dict):effect={}
    negative=diagnostic.get("negative") or {}
    positive=diagnostic.get("positive") or {}
    n=frame.get("evaluated",0)
    count=lambda v: int(v or 0) if isinstance(v,(int,float)) else 0
    return {
        "frame":frame.get("frame"),"label":frame.get("label"),
        "signals_observed":count(frame.get("signals")),
        "actual_positions":count(frame.get("entries")),
        "verified_closed_roi_n":count(n),
        "closed_status_unverified_roi_n":count(frame.get("closed_missing")),
        "open_positions_n":count(frame.get("open")),
        "open_marked_n":count(frame.get("open_marked")),
        "mean_verified_closed_roi_pct":frame.get("avg_realized_pct"),
        "mean_open_mark_to_market_pct":frame.get("avg_open_pct"),
        "hold_market_score_mean":frame.get("avg_hold_market_score"),
        "hold_market_score_samples":count(frame.get("hold_market_samples")),
        "factor_coverage_pct":frame.get("coverage"),
        "factor_patterns":frame.get("patterns"),
        "negative_pattern":{"code":negative.get("code"),"n":negative.get("n"),
            "cohort_n":negative.get("cohort_n"),
            "comparison_n":negative.get("comparison_n"),
            "exposed_avg_return_pct":negative.get("avg_return_pct"),
            "comparison_gap_pp":negative.get("relative_pp"),
            "support_basis":negative.get("basis")},
        "positive_pattern":{"code":positive.get("code"),"n":positive.get("n"),
            "cohort_n":positive.get("cohort_n"),
            "comparison_n":positive.get("comparison_n"),
            "exposed_avg_return_pct":positive.get("avg_return_pct"),
            "comparison_gap_pp":positive.get("relative_pp"),
            "support_basis":positive.get("basis")},
        "historical_filter_scenario":{
            "method":effect.get("method"),
            "validated":bool(effect.get("validated",False)),
            "n_comparison":count(effect.get("sample_n")),
            "excluded_n":count(effect.get("excluded_n")),
            "baseline_mean_roi_pct":effect.get("baseline_pct"),
            "scenario_mean_roi_pct":effect.get("scenario_pct"),
            "delta_percentage_points":effect.get("delta_pp"),
            "ci95_percentage_points":effect.get("ci95_pp"),
            "type":"EXPLORATORY_OR_HISTORICAL_HOLDOUT_NOT_LIVE_PROOF"},
        "rsi20":frame.get("market_trend_rsi20") or {},
        "source_status":frame.get("status"),
        "data_maturity_index_not_win_probability":frame.get("maturity"),
    }


GUIDE="""ARTITRADE AI 심층 정량자료 - 후속 ChatGPT 분석 가이드

파일 구성
1. analysis/AI_FULL_REPORT_V046.json : 앱 AI분석 버튼에서 산출한 선택 전략 전체 원문
2. analysis/AI_QUANTITATIVE_V046.json : 표본·신뢰도·진입 실패사유·실제/가상 구분·시장점수 수치
3. analysis/AI_SOURCE_SCHEMA_V046.json : 선택 전략 및 공용 DB 전체 테이블/컬럼 정보
4. analysis/AI_README_V046.txt : 지금 읽고 있는 판단 원칙
5. manifest.json 및 analysis/source_table_catalog.json : 원본 데이터 해시/행수 검증
6. stock/, coin/ 및 공용 market_research_v033.sqlite3 : SQLite/JSON 원본 전체
   (앱이 보유하지 않은 과거 데이터는 만들어내지 않음)

분석 방법
A. manifest.json 검증 및 DB 테이블별 원본 행수 확인. 누락·오류·부분 표본 표시.
B. 실제 체결(LIVE), 가상 체결(PAPER), 미매수 관측(VIRTUAL), 미체결(PENDING)을 분리.
   동일 종목도 시장/전략/시간봉/원시 주문키·체결시각을 근거로 구분.
C. RSI14 연구지표와 RSI20 마켓트렌드 매매지표를 혼동하지 말 것.
D. 진입 직전/당시 측정치만 진입 가설에 이용하고, 진입 후 측정치는 보유·청산 분석에만 이용.
E. closed_status_unverified_roi_n 건은 종료 표시는 있으나 실현손익이 검증되지 않은 것.
F. n, 비교집단 n, 장중 시장점수 시각, 미수신율, 호가·주문 응답 누락 건수를 함께 표시.
G. 대안 수치(손절/익절/시점/필터)는 근거자료와 시계열 검증을 거쳐 제안만 할 것.
   시뮬레이션 delta_pp는 실제 기대수익률도 확정 인과관계도 아님.
H. 표본 20건 미만 또는 검증구간 없음/95% CI가 0을 통과하면 '검증 미완료'로 표시.
I. 제안별 기대 개선값, 비교기간, 검증건수, 제외건수, 부작용(진입수 감소)을 표로 산출.
J. 자동매매 소스 수정은 사용자 별도 지시 후에만 시행. 원본 주문·체결·원장 보호.

앱 성능 수치는 스냅샷 관측치(호출수/평균/최댓값 ms)임. 작업용 CPU 정밀 측정치가
아니며 실제 주문 API 왕복지연은 브로커 로그가 없다면 추정하지 말 것.
"""


def build_reports(root_path,scope,analysis_json,ui_runtime_json="{}"):
    """Return <=8 small UTF-8 metadata files for android_ledger_backup.

    The backup utility will append the full original DBs without truncating
    history, validate SQLite integrity, and SHA-check the saved Android URI.
    """
    if not isinstance(scope,str) or scope.count("/")!=1:
        raise ValueError("Invalid analysis scope")
    market,strategy=scope.split("/")
    if (market,strategy) not in SOURCE:
        raise ValueError("Invalid analysis market/strategy")
    root=Path(root_path)
    if not root.is_dir():raise FileNotFoundError("strategy root unavailable")
    report=_read_json(analysis_json)
    if not report.get("ok") or report.get("scope")!=scope:
        raise ValueError("Analysis result does not match selected strategy")
    telemetry=_read_json(ui_runtime_json)
    now=time.time()
    frames=[_frame_evidence(x) for x in report.get("frames",[])
            if isinstance(x,dict)]
    details={
        "schema":SCHEMA,
        "scope":scope,
        "generated_kst":dt.datetime.fromtimestamp(now,KST).isoformat(),
        "selected_strategy_only":True,
        "all_original_strategy_files_in_zip":True,
        "no_strategy_or_order_mutation":True,
        "definitions":{
            "actual_positions":"원본 포지션 수; 실제/가상 운용은 세부 원장 실행 모드로 추가 검증",
            "verified_closed_roi_n":"실현 원가/순손익 확인된 종료 포지션 수",
            "mean_verified_closed_roi_pct":"각 종료 포지션 검증 수익률의 산술평균(가중 평균 아님)",
            "mean_open_mark_to_market_pct":"현재 보유 평가손익; 실현손익에 합산 금지",
            "delta_percentage_points":"과거 조건제외 시나리오의 표본당 차이; 향후 예측치 아님",
            "signals_observed":"관측 후보와 실제 포지션 수가 일치하지 않을 수 있음"
        },
        "analysis_source":report.get("source"),
        "errors":report.get("error") or [],
        "frame_statistics":frames,
        "score_history":_score_metrics(root,market),
        "trend_signal_decisions":_trend_metrics(root,market,strategy),
        "source_account_counts":_account_metrics(root,market,strategy),
        "runtime_performance_snapshot":telemetry,
        "quality_warnings":[
            "원본 데이터가 없으면 0으로 보정하지 않고 누락 사실을 표시",
            "수동 마켓트렌드 전환과 거래 시간봉의 역사적 매수 근거는 별도 조회",
            "호가부족·부분체결·미체결 등은 실제 주문/체결 증거가 있어야 확정",
            "전략별 SQLite/JSON 전체 기록은 본 ZIP의 원본 파일을 직접 읽어 검증",
        ],
    }
    outputs={
        "analysis/AI_FULL_REPORT_V046.json":
            json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),
        "analysis/AI_QUANTITATIVE_V046.json":
            json.dumps(details,ensure_ascii=False,indent=2,allow_nan=False),
        "analysis/AI_SOURCE_SCHEMA_V046.json":
            json.dumps({"schema":"ARTITRADE_AI_SOURCE_SCHEMA_V046",
                        "scope":scope,"sources":_source_schema(root,market,strategy)},
                       ensure_ascii=False,indent=2,allow_nan=False),
        "analysis/AI_README_V046.txt":GUIDE,
    }
    for name,value in outputs.items():
        if len(value.encode("utf-8"))>8*1024*1024:
            raise ValueError("Report "+name+" exceeds metadata limit; full DBs preserved")
    return json.dumps(outputs,ensure_ascii=False)
