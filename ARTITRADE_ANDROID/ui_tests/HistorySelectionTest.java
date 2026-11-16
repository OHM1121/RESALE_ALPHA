package com.artitrade.mobile;

public final class HistorySelectionTest {
    static void assertTrue(boolean b,String name){if(!b)throw new AssertionError(name);}
    public static void main(String[] args){
        assertTrue(HistorySelection.frame("5min").equals("5m"),"5min");
        assertTrue(HistorySelection.frame("1h").equals("60m"),"1h");
        assertTrue(HistorySelection.frame("1d").equals("day"),"1d");
        assertTrue(HistorySelection.matches("at","5m","all"),"all means all");
        assertTrue(HistorySelection.matches("ma","30m","30m"),"MA included in 30m");
        assertTrue(HistorySelection.matches("at","30m","30m"),"AT included in 30m");
        assertTrue(!HistorySelection.matches("gmo","","30m"),"GMO not in timeframe");
        assertTrue(HistorySelection.matches("gmo","","gmo"),"GMO category");
        assertTrue(HistorySelection.matches("close","","close"),"CLOSE category");
        assertTrue(HistorySelection.strategyTag("ma","5m").equals("MA · 5분봉"),"MA tag");
        assertTrue(HistorySelection.strategyTag("gmo","").equals("수급매매"),"GMO tag");
        assertTrue(HistorySelection.semantic("시세 연결 끊김")==-1,"red disconnected");
        assertTrue(HistorySelection.semantic("관측 중")==1,"orange pending");
        assertTrue(HistorySelection.semantic("승계 완료")==2,"green complete");
        assertTrue(HistorySelection.semantic("2026-11-13 13:00")==0,"date neutral");
        System.out.println("HISTORY_FILTER_SEMANTIC_TEST_PASS");
    }
}
