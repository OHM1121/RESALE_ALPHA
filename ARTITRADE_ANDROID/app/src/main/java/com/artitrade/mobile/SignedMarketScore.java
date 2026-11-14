package com.artitrade.mobile;

/** Original EXE market scale: bearish -100, neutral 0, bullish +100. */
final class SignedMarketScore {
    private SignedMarketScore(){}
    static boolean isValid(double score){
        return Double.isFinite(score)&&score>=-100&&score<=100;
    }
    static double position(double score){
        return isValid(score)?(score+100.0)/200.0:Double.NaN;
    }
}
