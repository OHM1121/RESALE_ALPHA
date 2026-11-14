package com.artitrade.mobile;

/** v034: score visualization uses -100..0..+100, not 0..100. */
public final class SignedMarketScoreTest {
    static void check(boolean condition,String message){
        if(!condition)throw new AssertionError(message);
    }
    static void near(double actual,double expected,String message){
        check(Math.abs(actual-expected)<.0001,message+" "+actual);
    }
    public static void main(String[] args){
        check(SignedMarketScore.isValid(-100),"negative score valid");
        check(SignedMarketScore.isValid(0),"neutral is zero");
        check(SignedMarketScore.isValid(100),"positive score valid");
        check(!SignedMarketScore.isValid(-101),"out of range negative");
        check(!SignedMarketScore.isValid(101),"out of range positive");
        near(SignedMarketScore.position(-100),0.0,"left endpoint");
        near(SignedMarketScore.position(0),0.5,"zero center");
        near(SignedMarketScore.position(100),1.0,"right endpoint");
        near(SignedMarketScore.position(-50),0.25,"minus half");
        near(SignedMarketScore.position(50),0.75,"plus half");
        System.out.println("SIGNED_MARKET_SCORE_SCALE_OK");
    }
}
