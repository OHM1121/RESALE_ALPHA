package com.artitrade.mobile;

/** Horizontal page switch requires deliberate displacement, not scroll jitter. */
final class StrategySwipeThreshold {
    private StrategySwipeThreshold(){}
    static boolean shouldSwitch(float dx,float dy,float requiredDistancePx){
        return Float.isFinite(dx)&&Float.isFinite(dy)&&requiredDistancePx>0f
            &&Math.abs(dx)>=requiredDistancePx
            &&Math.abs(dx)>Math.abs(dy)*1.35f;
    }
}
