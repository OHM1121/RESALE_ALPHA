package com.artitrade.mobile;

/** Plain JVM regression test: no Android SDK, no order or ledger side effects. */
public final class HistoryPagerTest {
    private static void check(boolean ok,String what){if(!ok)throw new AssertionError(what);}
    public static void main(String[] args){
        check(HistoryPager.start(0,0)==0,"empty start");
        check(HistoryPager.end(0,0)==0,"empty end");
        check(HistoryPager.start(0,85)==0,"first page");
        check(HistoryPager.end(0,85)==40,"40 visible of 85");
        check(HistoryPager.next(0,85)==40,"second page");
        check(HistoryPager.next(40,85)==80,"third page");
        check(HistoryPager.next(80,85)==80,"last page cannot move beyond total");
        check(HistoryPager.end(80,85)==85,"last five records available");
        check(HistoryPager.previous(80,85)==40,"go back one page");
        check(HistoryPager.previous(0,85)==0,"no negative page");
        check(HistoryPager.start(480,25)==0,"stale offset clamps to first page");
        check(HistoryPager.start(480,95)==80,"stale offset clamps to last page");
        check(HistoryPager.start(-40,95)==0,"negative offset clamps");
        check(HistoryPager.end(0,1000000)==40,"huge history shows a bounded window");
        check(HistoryPager.next(999999,1000000)==999960,"huge history end is reachable");
        System.out.println("HISTORY_PAGER_REGRESSION_PASS");
    }
}
