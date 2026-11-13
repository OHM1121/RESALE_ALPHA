package com.artitrade.mobile;

import java.util.LinkedHashMap;
import java.util.Map;

/** Count each research signal once by account frame and score band. Never write trades. */
final class ResearchScoreGrid {
    static final String NO_SCORE="점수 없음";
    static final String[] BANDS={"0~20","21~40","41~60","61~80","81~100",NO_SCORE};
    static final class Cell {
        int signals,actualCount,actualHit,actualMiss,actualPending;
        int virtualEntered,virtualHit,virtualMiss,virtualPending,virtualNotEntered;
        double actualRate(){
            int decided=actualHit+actualMiss;
            return decided==0?Double.NaN:100.0*actualHit/decided;
        }
        double virtualRate(){
            int decided=virtualHit+virtualMiss;
            return decided==0?Double.NaN:100.0*virtualHit/decided;
        }
    }
    private final Map<String,Cell> cells=new LinkedHashMap<>();
    private final Cell total=new Cell();

    static String band(double score){
        if(Double.isNaN(score)||Double.isInfinite(score)||score<0||score>100)return NO_SCORE;
        if(score<=20)return BANDS[0];
        if(score<=40)return BANDS[1];
        if(score<=60)return BANDS[2];
        if(score<=80)return BANDS[3];
        return BANDS[4];
    }
    static String scopeFrame(String raw){
        String tf=HistorySelection.frame(raw);
        if(!tf.isEmpty())return tf;
        if("gmo".equals(raw)||"close".equals(raw))return raw;
        return "미분류";
    }
    private static void accumulate(Cell c,boolean actual,boolean entered,String outcome){
        c.signals++;
        String verdict=outcome==null?"PENDING":outcome.trim().toUpperCase(java.util.Locale.ROOT);
        if(actual){
            c.actualCount++;
            if("HIT".equals(verdict))c.actualHit++;
            else if("MISS".equals(verdict))c.actualMiss++;
            else c.actualPending++;
        }else if(entered){
            c.virtualEntered++;
            if("HIT".equals(verdict))c.virtualHit++;
            else if("MISS".equals(verdict))c.virtualMiss++;
            else c.virtualPending++;
        }else c.virtualNotEntered++;
    }
    void add(String frame,Double score,boolean bought,boolean entryObserved,String outcome){
        String tf=scopeFrame(frame),band=band(score==null?Double.NaN:score.doubleValue()),key=tf+"/"+band;
        Cell value=cells.computeIfAbsent(key,k->new Cell());
        accumulate(value,bought,entryObserved,outcome);
        accumulate(total,bought,entryObserved,outcome);
    }
    Cell cell(String frame,String band){
        Cell v=cells.get(scopeFrame(frame)+"/"+band);
        return v==null?new Cell():v;
    }
    Cell totals(){return total;}
    boolean any(String frame){
        String tf=scopeFrame(frame)+"/";
        for(Map.Entry<String,Cell> e:cells.entrySet())if(e.getKey().startsWith(tf)&&e.getValue().signals>0)return true;
        return false;
    }
}
