package com.artitrade.mobile;

/** Only human-actionable summary labels; raw diagnostics stay in Errors. */
final class SyncStatusV036 {
    private SyncStatusV036(){}
    static String phase(boolean complete,boolean error,int done,int total,boolean active){
        if(error)return "오류";
        if(complete)return "완료";
        if(active&&(done>0||total>0))return "진행중";
        return "미완료";
    }
    static String percent(int done,int total){
        int pct=total<=0?0:(int)Math.round(100.0*
            Math.max(0,Math.min(done,total))/total);
        return pct+"%";
    }
}
