package com.artitrade.mobile;

/** Research heatmap exact column hit-test, cohort denominator and row scope. */
public final class ResearchPopupV036Test {
    private static void check(boolean ok,String label){
        if(!ok)throw new AssertionError(label);
    }
    private static void eq(String expected,String actual,String label){
        check(expected.equals(actual),label+": "+actual);
    }
    private static void near(double expected,double actual,String label){
        check(Math.abs(expected-actual)<.01,label+": "+actual);
    }
    public static void main(String[] args){
        eq("actual",ResearchBandSelection.tapSide(125,330,1.0f),"actual column");
        eq("virtual",ResearchBandSelection.tapSide(275,330,1.0f),"virtual column");
        check(ResearchBandSelection.tapSide(38,330,1.0f)==null,"score cell should not open a mixed popup");
        check(ResearchBandSelection.tapSide(173,330,1.0f)==null,"column gap should not open mixed");
        check(ResearchBandSelection.includeInPopup(true,true,"actual"),"real purchase");
        check(!ResearchBandSelection.includeInPopup(false,true,"actual"),"exclude virtual from actual");
        check(ResearchBandSelection.includeInPopup(false,true,"virtual"),"virtual entry observed");
        check(!ResearchBandSelection.includeInPopup(false,false,"virtual"),"never-entered candidate must not appear");
        eq("O",ResearchBandSelection.goalSymbol("HIT",true),"verified target");
        eq("X",ResearchBandSelection.goalSymbol("MISS",true),"confirmed no-target");
        eq("—",ResearchBandSelection.goalSymbol("PENDING",false),"pending never X");
        eq("—",ResearchBandSelection.goalSymbol("HIT",false),"unverified high must not count as fill");
        ResearchScoreGrid.Cell c=new ResearchScoreGrid.Cell();
        c.actualCount=10;c.actualHit=1;c.actualPending=9;
        near(10.0,c.actualConfirmedShare(),"100% from 1/1 is misleading; show 1/10");
        near(100.0,c.actualRate(),"legacy decided rate semantics remain for audit");
        c.virtualEntered=5;c.virtualHit=2;c.virtualPending=3;
        near(40.0,c.virtualConfirmedShare(),"2/5");
        System.out.println("RESEARCH_POPUP_V036_COHORT_SCOPE_PASS");
    }
}
