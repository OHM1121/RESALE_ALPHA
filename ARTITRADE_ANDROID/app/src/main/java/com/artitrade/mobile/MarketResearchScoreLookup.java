package com.artitrade.mobile;

import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import java.io.File;

/** Historical score lookup: only event samples at or BEFORE execution are valid. */
final class MarketResearchScoreLookup {
    private MarketResearchScoreLookup(){}

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
            return Double.isFinite(score)&&score>=0&&score<=100?score:null;
        }catch(Exception error){
            // Both a missing DB and an EXE-imported record without market
            // observations must display "미기록", never a synthetic 50.
            return null;
        }
    }
}
