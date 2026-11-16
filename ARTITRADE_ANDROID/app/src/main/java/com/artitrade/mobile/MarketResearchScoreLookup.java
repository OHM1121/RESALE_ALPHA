package com.artitrade.mobile;

import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import java.io.File;

/** Historical score lookup: only event samples at or BEFORE execution are valid. */
final class MarketResearchScoreLookup {
    private MarketResearchScoreLookup(){}

    /** A single read-only SQLite scan replaces N individual DB opens during
     * user-triggered sorting or filtering of historical score columns. */
    static java.util.NavigableMap<Long,Double> scoreTimeline(File root,String market){
        java.util.NavigableMap<Long,Double> result=new java.util.TreeMap<>();
        File dbFile=new File(root,"market_research_v033.sqlite3");
        if(!dbFile.isFile()||!("stock".equals(market)||"coin".equals(market)))
            return result;
        try(SQLiteDatabase db=SQLiteDatabase.openDatabase(dbFile.getAbsolutePath(),
                null,SQLiteDatabase.OPEN_READONLY);
            Cursor c=db.rawQuery(
                "SELECT ts,score FROM score_events WHERE market=? ORDER BY ts",
                new String[]{market})){
            while(c.moveToNext()){
                long at=c.getLong(0);
                double score=c.getDouble(1);
                if(SignedMarketScore.isValid(score))result.put(at,score);
            }
        }catch(Exception ignored){
            result.clear();
        }
        return result;
    }

    static Double atTime(java.util.NavigableMap<Long,Double> scores,long epochMillis){
        if(scores==null||scores.isEmpty()||epochMillis<=0)return null;
        long at=epochMillis/1000L;
        java.util.Map.Entry<Long,Double> row=scores.floorEntry(at);
        return row==null||at-row.getKey()>900L?null:row.getValue();
    }

    static Double scoreAt(File root,String market,long fillEpochMillis){
        if(!("stock".equals(market)||"coin".equals(market))||fillEpochMillis<=0)return null;
        File dbFile=new File(root,"market_research_v033.sqlite3");
        if(!dbFile.isFile())return null;
        long ts=fillEpochMillis/1000L;
        // Live scores are sampled every ~180s. A sample >15m old
        // or collected after a trade cannot truthfully describe that trade.
        try(SQLiteDatabase db=SQLiteDatabase.openDatabase(dbFile.getAbsolutePath(),
                null,SQLiteDatabase.OPEN_READONLY);
            Cursor c=db.rawQuery("SELECT score FROM score_events "+
                "WHERE market=? AND ts<=? AND ts>=? ORDER BY ts DESC LIMIT 1",
                new String[]{market,Long.toString(ts),
                    Long.toString(Math.max(0,ts-900))})){
            if(!c.moveToFirst()||c.isNull(0))return null;
            double score=c.getDouble(0);
            return SignedMarketScore.isValid(score)?score:null;
        }catch(Exception error){
            // Both a missing DB and an EXE-imported record without market
            // observations must display "미기록", never a synthetic 50.
            return null;
        }
    }
}
