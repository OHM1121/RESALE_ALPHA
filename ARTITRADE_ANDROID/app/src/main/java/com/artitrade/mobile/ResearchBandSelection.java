package com.artitrade.mobile;

/** Pure score/row selection helper: screen taps do not change any trade state. */
final class ResearchBandSelection {
    private ResearchBandSelection(){}

    static boolean matches(String candidateFrame,double score,String selectedFrame,String selectedBand){
        if(selectedBand==null)return false;
        String candidate=ResearchScoreGrid.scopeFrame(candidateFrame==null?"":candidateFrame);
        String selected=ResearchScoreGrid.scopeFrame(selectedFrame==null?"":selectedFrame);
        return candidate.equals(selected)&&ResearchScoreGrid.band(score).equals(selectedBand);
    }

    /** Exact heatmap column, not merely the Y-position of its score row. */
    static String tapSide(float x,float width,float density){
        if(!Float.isFinite(x)||!Float.isFinite(width)||
           !Float.isFinite(density)||density<=0||width<190*density)return null;
        float pad=10*density,scoreRight=pad+66*density,gap=9*density;
        float column=(width-scoreRight-pad-gap*2)/2f;
        float actualLeft=scoreRight+gap,virtualLeft=actualLeft+column+gap;
        if(x>=actualLeft&&x<=actualLeft+column)return "actual";
        if(x>=virtualLeft&&x<=virtualLeft+column)return "virtual";
        return null;
    }

    static boolean includeInPopup(boolean bought,boolean entryObserved,String side){
        return "actual".equals(side)?bought:
            "virtual".equals(side)&&!bought&&entryObserved;
    }

    /** Unknown/missing evidence must never be mislabeled as a failure. */
    static String goalSymbol(String outcome,boolean verified){
        if(!verified)return "—";
        if("HIT".equalsIgnoreCase(outcome))return "O";
        if("MISS".equalsIgnoreCase(outcome))return "X";
        return "—";
    }

    /** Mirror ResearchScoreMatrixView.onDraw row coordinates exactly. */
    static String bandAtY(float y,float viewHeight,float density,int rowCount){
        if(!Float.isFinite(y)||!Float.isFinite(viewHeight)||!Float.isFinite(density)||
            density<=0||rowCount<1||rowCount>ResearchScoreGrid.BANDS.length)return null;
        float top=59f*density;
        float usable=viewHeight-68f*density;
        if(usable<=0||y<top||y>=viewHeight-9f*density)return null;
        int row=(int)((y-top)/(usable/rowCount));
        return row>=0&&row<rowCount?ResearchScoreGrid.BANDS[row]:null;
    }

    static String eventType(boolean confirmedEntry,boolean virtualEntry){
        return confirmedEntry?"실제 체결":virtualEntry?"가상 관측":"체결 없음";
    }
    static String sellEvidence(boolean confirmedSell,boolean observedTarget){
        return confirmedSell?"매도 체결":observedTarget?"목표 관측 (미체결)":"매도 미기록";
    }
}
