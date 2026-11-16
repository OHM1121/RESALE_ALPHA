package com.artitrade.mobile;

/** Pure JVM regression: neither taps nor mostly vertical scrolling changes strategy. */
public final class StrategySwipeThresholdTest {
    static void check(boolean v,String msg){if(!v)throw new AssertionError(msg);}
    public static void main(String[] args){
        float min=112f;
        check(!StrategySwipeThreshold.shouldSwitch(30,0,min),"tap jitter rejected");
        check(!StrategySwipeThreshold.shouldSwitch(78,0,min),"old 78dp too sensitive");
        check(!StrategySwipeThreshold.shouldSwitch(105,0,min),"near threshold rejected");
        check(StrategySwipeThreshold.shouldSwitch(-125,12,min),"left swipe accepted");
        check(StrategySwipeThreshold.shouldSwitch(125,-11,min),"right swipe accepted");
        check(!StrategySwipeThreshold.shouldSwitch(132,112,min),"diagonal scroll rejected");
        check(!StrategySwipeThreshold.shouldSwitch(155,175,min),"vertical scroll rejected");
        System.out.println("SWIPE_THRESHOLD_PASS");
    }
}
