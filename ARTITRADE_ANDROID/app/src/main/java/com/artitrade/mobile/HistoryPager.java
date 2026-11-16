package com.artitrade.mobile;

/** A bounded, non-destructive window into a complete historical ledger. */
final class HistoryPager {
    static final int PAGE_SIZE=40;
    private HistoryPager(){}

    static int start(int offset,int total){
        if(total<=0)return 0;
        int last=((total-1)/PAGE_SIZE)*PAGE_SIZE;
        int requested=(Math.max(0,offset)/PAGE_SIZE)*PAGE_SIZE;
        return Math.min(requested,last);
    }
    static int end(int offset,int total){
        return total<=0?0:Math.min(total,start(offset,total)+PAGE_SIZE);
    }
    static int next(int offset,int total){
        return start(start(offset,total)+PAGE_SIZE,total);
    }
    static int previous(int offset,int total){
        return start(Math.max(0,start(offset,total)-PAGE_SIZE),total);
    }
}
