package com.artitrade.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import org.json.JSONObject;
import java.io.File;

/**
 * Read-only, independent monotonic PAPER execution witness.
 * Never restores older databases, deletes holdings or infers a fill.
 * Called before trading startup and periodically outside broker quote threads.
 */
final class LedgerAntiRollbackV002 {
    private static final String PREFS="artitrade_ledger_guard_v002";
    private LedgerAntiRollbackV002(){}

    static synchronized JSONObject verify(Context c)throws Exception{
        JSONObject epoch=FreshEpochGateV106.read(c);
        if(epoch==null||!"COMMITTED".equals(epoch.optString("state")))
            throw new IllegalStateException("PAPER_EPOCH_NOT_COMMITTED_NO_TRADING");
        SharedPreferences witness=c.getSharedPreferences(PREFS,Context.MODE_PRIVATE);
        String remembered=witness.getString("epoch_id","");
        String id=epoch.getString("epoch_id");
        if(!remembered.isEmpty()&&!id.equals(remembered))
            throw new IllegalStateException("LEDGER_EPOCH_REPLACED_BLOCK_TRADING");
        if(!witness.getString("blocked_reason","").isEmpty())
            throw new IllegalStateException("LEDGER_ROLLBACK_PREVIOUSLY_BLOCKED");
        File root=new File(c.getFilesDir(),"ARTITRADE_PHONE_MAIN_V070");
        File independent=new File(c.getFilesDir(),"ARTITRADE_LEDGER_GUARD_V002.json");
        // If the checkpoint disappears after this version had initialized it,
        // refusing a new baseline is the only safe choice: a restored older
        // ledger must never be disguised as a fresh high-water starting point.
        if(!remembered.isEmpty()&&!independent.isFile())
            throw new IllegalStateException(
                "LEDGER_GUARD_WITNESS_MISSING_NO_AUTORESET");
        if(!Python.isStarted())
            Python.start(new AndroidPlatform(c.getApplicationContext()));
        String raw=Python.getInstance()
            .getModule("ledger_rollback_guard_v002")
            .callAttr("verify_json",root.getAbsolutePath(),
                     !remembered.isEmpty()).toString();
        JSONObject result=new JSONObject(raw);
        if(!result.optBoolean("ok",false)||
            !"MONOTONIC_PAPER_EPOCH_OK".equals(result.optString("status"))||
            !id.equals(result.optString("epoch_id")))
            throw new IllegalStateException("LEDGER_CHECKPOINT_PROOF_FAILED");
        if(remembered.isEmpty()&&
            !witness.edit().putString("epoch_id",id).commit())
            throw new IllegalStateException("LEDGER_GUARD_PREF_NOT_DURABLE");
        return result;
    }

    static void block(Context c,Throwable cause){
        // Only integrity status is stored; never copy private broker secrets
        // or old execution records into the blocking reason.
        String category=(cause instanceof IllegalStateException)
            ?cause.getMessage():"LEDGER_INTEGRITY_PROOF_FAILED";
        if(category==null||category.isEmpty())
            category="LEDGER_INTEGRITY_PROOF_FAILED";
        c.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit()
            .putString("blocked_reason",category.substring(0,
                Math.min(120,category.length())))
            .putLong("blocked_at_ms",System.currentTimeMillis()).commit();
    }
}
