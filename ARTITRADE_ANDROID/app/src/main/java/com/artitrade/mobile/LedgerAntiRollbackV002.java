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
    private static boolean cfgSingleCleanupAttemptedV015=false;
    private static boolean ledgerResetAttemptedV0130=false;
    private static boolean historyResetAttemptedV0140=false;
    private static boolean candidateResetAttemptedV0150=false;

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
        // Explicit user-requested v0.0.12 PAPER-only migration. The signed
        // Python module validates the pre-change witness, backs up only
        // the six AT/MA/Trend wallets, removes attributed old-policy PAPER
        // trades, reconciles cash/P&L and rebases this SAME witness.
        // GMO/CW are neither opened for mutation nor rebaselined.
        // Must finish BEFORE normal read-only anti-rollback verification
        // and BEFORE any trading engine starts; not a general reset route.
        if(root.isDirectory()&&independent.isFile())
            Python.getInstance().getModule("legacy_purge_v012")
                .callAttr("run_once",root.getAbsolutePath());
        // The user approved exactly ONE previously reported KRW-CFG coin MA
        // 5m PAPER cycle. Run only once per phone process, before engines start.
        // A symbol-only match NEVER authorizes deletion. The Python module
        // backs up the exact native wallet, verifies original BUY receipts,
        // updates cash/P&L and rebases the independent witness atomically.
        if(!cfgSingleCleanupAttemptedV015&&root.isDirectory()&&independent.isFile()){
            try{
                String status=Python.getInstance()
                    .getModule("cfg_single_paper_cleanup_v015")
                    .callAttr("run_json",root.getAbsolutePath()).toString();
                JSONObject result=new JSONObject(status);
                String state=result.optString("status","UNVERIFIED");
                // Persist a non-private result code for diagnostics even if no
                // matching historic position remains on this device.
                c.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit()
                    .putString("cfg_single_v015_status",state)
                    .putString("cfg_single_v015_reason",
                        result.optString("reason","")).commit();
                if("SKIPPED".equals(state))
                    android.util.Log.w("ARTITRADE_CFG_V015",
                        "Exact-single PAPER cleanup skipped: "+
                        result.optString("reason","NO_SINGLE_POSITION"));
            }finally{
                cfgSingleCleanupAttemptedV015=true;
            }
        }
        // Owner instruction 2026-11-20 (0.1.3): one-time PAPER ledger reset of
        // everything EXCEPT the GMO ledger, to measure the new policy from zero.
        // Only ledger rows are removed (exact cash/P&L reversal); engine code and
        // schemas are untouched. Attempted once per process, BEFORE any engine
        // starts, and never again after its DONE marker exists. A failure restores
        // the wallets inside the module and must NOT block trading, so it is caught.
        if(!ledgerResetAttemptedV0130&&root.isDirectory()&&independent.isFile()){
            ledgerResetAttemptedV0130=true;
            try{
                JSONObject reset=new JSONObject(Python.getInstance()
                    .getModule("ledger_reset_v0130")
                    .callAttr("run_json",root.getAbsolutePath()).toString());
                c.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit()
                    .putString("ledger_reset_v0130_status",
                        reset.optString("status",reset.optString("skipped","UNVERIFIED")))
                    .commit();
            }catch(Throwable resetFailure){
                android.util.Log.w("ARTITRADE_RESET_V0130",
                    "Ledger reset not applied: "+resetFailure.getClass().getSimpleName());
                c.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit()
                    .putString("ledger_reset_v0130_status",
                        "FAILED:"+resetFailure.getClass().getSimpleName()).commit();
            }
        }
        // Phase 2 of the owner's reset (0.1.4): clear only INDEPENDENT research /
        // error / AI history files (engine-owned state, signals, pending orders,
        // bars, market score and every GMO file are left alone). Once per process,
        // before any engine starts, caught so it can never block trading. The app
        // fault log is cleared only on the run that actually applied the reset.
        if(!historyResetAttemptedV0140&&root.isDirectory()){
            historyResetAttemptedV0140=true;
            try{
                JSONObject history=new JSONObject(Python.getInstance()
                    .getModule("history_reset_v0140")
                    .callAttr("run_json",root.getAbsolutePath()).toString());
                if(history.optBoolean("applied_now",false)&&
                    "DONE".equals(history.optString("status")))
                    new RuntimeCrashStore(c).clear();
                c.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit()
                    .putString("history_reset_v0140_status",
                        history.optString("status",history.optString("skipped","UNVERIFIED")))
                    .commit();
            }catch(Throwable historyFailure){
                android.util.Log.w("ARTITRADE_RESET_V0140",
                    "History reset not applied: "+historyFailure.getClass().getSimpleName());
                c.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit()
                    .putString("history_reset_v0140_status",
                        "FAILED:"+historyFailure.getClass().getSimpleName()).commit();
            }
        }
        // Phase 3 of the owner's reset (0.1.5): clear candidate / signal-linked
        // research and buy-pending rows (stock AT, close-betting, MA, coin). Rows
        // only: engine code, schemas, bars, market score, accounts and every GMO
        // file stay. Once per process before any engine starts; caught so it can
        // never block trading.
        if(!candidateResetAttemptedV0150&&root.isDirectory()){
            candidateResetAttemptedV0150=true;
            try{
                JSONObject candidates=new JSONObject(Python.getInstance()
                    .getModule("candidate_reset_v0150")
                    .callAttr("run_json",root.getAbsolutePath()).toString());
                c.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit()
                    .putString("candidate_reset_v0150_status",
                        candidates.optString("status",candidates.optString("skipped","UNVERIFIED")))
                    .commit();
            }catch(Throwable candidateFailure){
                android.util.Log.w("ARTITRADE_RESET_V0150",
                    "Candidate reset not applied: "+candidateFailure.getClass().getSimpleName());
                c.getSharedPreferences(PREFS,Context.MODE_PRIVATE).edit()
                    .putString("candidate_reset_v0150_status",
                        "FAILED:"+candidateFailure.getClass().getSimpleName()).commit();
            }
        }
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
