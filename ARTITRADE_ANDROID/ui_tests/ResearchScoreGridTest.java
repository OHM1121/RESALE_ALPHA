package com.artitrade.mobile;

public final class ResearchScoreGridTest {
    private static void check(boolean yes,String msg){if(!yes)throw new AssertionError(msg);}
    public static void main(String[] args){
        ResearchScoreGrid grid=new ResearchScoreGrid();
        grid.add("5m",55,true,false,"HIT");
        grid.add("5m",58,true,false,"MISS");
        grid.add("5m",53,false,true,"HIT");
        grid.add("5m",55,false,true,"PENDING");
        grid.add("5m",53,false,false,"HIT");
        grid.add("30m",75,true,false,"HIT");
        grid.add("day",101,true,false,"HIT");
        ResearchScoreGrid.Cell five=grid.cell("5m","41~60");
        check(five.signals==5,"all five signal rows");
        check(five.actualCount==2 && five.actualHit==1 && five.actualMiss==1,"real wins/misses");
        check(five.actualRate()==50.0,"real rate 50%");
        check(five.virtualEntered==2 && five.virtualHit==1 && five.virtualPending==1,"virtual entered and pending");
        check(five.virtualRate()==100.0,"pending excluded from rate");
        check(five.virtualNotEntered==1,"virtual not entered excluded");
        check(grid.cell("30m","61~80").actualHit==1,"30m isolated");
        check(grid.cell("5m","61~80").actualCount==0,"bands isolated");
        check(Double.isNaN(grid.cell("5m","0~20").actualRate()),"zero denominator unknown");
        check(grid.cell("day","점수 없음").signals==1,"out of range not hidden");
        check(grid.totals().signals==7,"no lost history");
        check(grid.totals().actualHit==3,"total separated");
        ResearchScoreGrid gmo=new ResearchScoreGrid();
        gmo.add("gmo",null,true,false,"HIT");
        check(gmo.cell("gmo","점수 없음").actualRate()==100.0,"unscored GMO visible");
        System.out.println("RESEARCH_SCORE_GRID_TEST_PASS");
    }
}
