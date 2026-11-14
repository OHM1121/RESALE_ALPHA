package com.artitrade.mobile;

/** V031: score row double-tap must match exactly the displayed frame and band. */
public final class ResearchBandSelectionTest {
    private static void check(boolean ok,String what){if(!ok)throw new AssertionError(what);}
    public static void main(String[] args){
        check(ResearchBandSelection.matches("5m",41,"5m","41~60"),"lower boundary");
        check(ResearchBandSelection.matches("5m",60,"5m","41~60"),"upper boundary");
        check(!ResearchBandSelection.matches("5m",40,"5m","41~60"),"wrong band");
        check(!ResearchBandSelection.matches("30m",55,"5m","41~60"),"wrong frame");
        check(ResearchBandSelection.matches("gmo",55,"gmo","41~60"),"stock GMO");
        check(ResearchBandSelection.matches("close",Double.NaN,"close","점수 없음"),"close unscored");
        check(ResearchBandSelection.matches("day",79,"day","61~80"),"daily");
        float h=218,scale=1,rows=5,rowHeight=(h-68)/rows;
        check("41~60".equals(ResearchBandSelection.bandAtY(59+rowHeight*2.5f,h,scale,rows)),
            "third painted band must be 41~60");
        check(ResearchBandSelection.bandAtY(30,h,scale,rows)==null,"header not a band");
        check(ResearchBandSelection.bandAtY(215,h,scale,rows)==null,"bottom border not a band");
        check(ResearchBandSelection.bandAtY(300,h,scale,rows)==null,"out of bounds");
        float h6=246;
        check("점수 없음".equals(ResearchBandSelection.bandAtY(
            59+(h6-68)/6f*5.5f,h6,1,6)),"sixth unscored row");
        check("실제 체결".equals(ResearchBandSelection.eventType(true,false)),"actual fill");
        check("가상 관측".equals(ResearchBandSelection.eventType(false,true)),"virtual observation");
        check("체결 없음".equals(ResearchBandSelection.eventType(false,false)),"unselected");
        check("목표 관측 (미체결)".equals(ResearchBandSelection.sellEvidence(false,true)),
            "observed high not a sale");
        check("매도 체결".equals(ResearchBandSelection.sellEvidence(true,true)),
            "only proven fill may be called sell");
        check("매도 미기록".equals(ResearchBandSelection.sellEvidence(false,false)),
            "no fabricated sale");
        System.out.println("RESEARCH_BAND_SELECTION_PASS");
    }
}
