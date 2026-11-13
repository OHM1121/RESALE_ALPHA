package com.artitrade.mobile;

import java.util.Locale;

/** Classifies stored paper-ledger rows for read-only UI filtering. */
final class HistorySelection {
    static final String ALL="all";
    private HistorySelection(){}

    static String frame(String raw){
        if(raw==null)return "";
        String x=raw.trim().toLowerCase(Locale.ROOT);
        switch(x){
            case "5m":case "5min":case "5":case "5분":return "5m";
            case "30m":case "30min":case "30":case "30분":return "30m";
            case "60m":case "60min":case "60":case "1h":case "1시간":return "60m";
            case "day":case "1d":case "d":case "daily":case "일봉":return "day";
            default:return "";
        }
    }
    static String frameLabel(String frame){
        switch(frame(frame)){
            case "5m":return "5분봉";
            case "30m":return "30분봉";
            case "60m":return "1시간봉";
            case "day":return "일봉";
            default:return "시간봉 미분류";
        }
    }
    static String strategyTag(String strategy,String tf){
        if("gmo".equals(strategy))return "수급매매";
        if("close".equals(strategy))return "종가베팅";
        String prefix="ma".equals(strategy)?"MA · ":"기준봉 · ";
        return prefix+frameLabel(tf);
    }
    static String filterLabel(String filter){
        if("all".equals(filter))return "전체";
        if("gmo".equals(filter))return "수급매매";
        if("close".equals(filter))return "종가베팅";
        return frameLabel(filter);
    }
    static boolean matches(String strategy,String tf,String filter){
        if(filter==null||"all".equals(filter))return true;
        if("gmo".equals(filter)||"close".equals(filter))return filter.equals(strategy);
        if("gmo".equals(strategy)||"close".equals(strategy))return false;
        return filter.equals(frame(tf));
    }
    static int semantic(String message){
        if(message==null)return 0;
        String v=message.toLowerCase(Locale.ROOT);
        if(v.contains("실패")||v.contains("오류")||v.contains("끊김")||
           v.contains("연결 불가")||v.contains("미수신")||v.contains("차단")||
           v.contains("불일치")||v.contains("손실")||v.contains("마이너스")||
           v.contains("disconnected")||v.contains("failed")||v.contains("unreachable"))return -1;
        if(v.contains("보류")||v.contains("대기")||v.contains("확인 중")||
           v.contains("진행 중")||v.contains("관측 중")||v.contains("미기록")||
           v.contains("pending")||v.contains("wait")||v.contains("loading"))return 1;
        if(v.contains("성공")||v.contains("정상")||v.contains("완료")||
           v.contains("연결됨")||v.contains("live")||v.contains("imported"))return 2;
        return 0;
    }
}
