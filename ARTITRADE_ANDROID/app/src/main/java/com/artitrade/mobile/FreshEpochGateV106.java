package com.artitrade.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;

/**
 * v106 source-only fresh PAPER epoch gate. It NEVER deletes SQLite or invokes
 * a reset. Only the separately approved OFFLINE reset utility may create the
 * marker. A marker must block legacy PC ZIP import on every future startup.
 */
final class FreshEpochGateV106 {
    private static final String SCHEMA="ARTITRADE_FRESH_PAPER_EPOCH_V106";
    private static final String MARKER="ARTITRADE_FRESH_EPOCH_V106.json";
    private static final String CHECKPOINT="artitrade_fresh_epoch_applied_v106";
    private FreshEpochGateV106(){}

    private static File marker(Context c){
        return new File(new File(c.getFilesDir(),"ARTITRADE_PHONE_MAIN_V070"),MARKER);
    }

    static boolean hasMarker(Context c){return marker(c).exists();}

    /**
     * This new source is a fresh-PAPER-epoch-only build. Before its very first
     * strategy startup, ALL previous PAPER histories must be removed. This
     * is not a legacy-position migration and cannot be skipped.
     */
    static boolean mandatoryFreshStartPending(Context c)throws Exception{
        // Two independent durable witnesses outlive a replaced stock/coin
        // folder. Once ANY witness exists, NEVER run reset again, including
        // when the old marker has reverted to PREPARING with the SAME id.
        String prior=c.getSharedPreferences(CHECKPOINT,Context.MODE_PRIVATE)
            .getString("applied_epoch_id","");
        String second=c.getSharedPreferences(
            "artitrade_ledger_guard_v002",Context.MODE_PRIVATE)
            .getString("epoch_id","");
        File diskWitness=new File(c.getFilesDir(),
            "ARTITRADE_LEDGER_GUARD_V002.json");
        JSONObject saved=read(c);
        if(!prior.isEmpty()&&!second.isEmpty()&&!prior.equals(second))
            throw new IllegalStateException(
                "INDEPENDENT_EPOCH_WITNESSES_CONFLICT_BLOCK");
        String remembered=prior.isEmpty()?second:prior;
        if(!remembered.isEmpty()||diskWitness.isFile()){
            if(saved==null)
                throw new IllegalStateException(
                    "COMMITTED_EPOCH_MARKER_MISSING_NO_REINITIALIZE");
            if(!"COMMITTED".equals(saved.optString("state")))
                throw new IllegalStateException(
                    "COMMITTED_EPOCH_MARKER_REWOUND_TO_PREPARING_NO_RESET");
            if(!remembered.isEmpty()&&
                !remembered.equals(saved.optString("epoch_id")))
                throw new IllegalStateException(
                    "OLD_EPOCH_MARKER_REPLACED_KEEP_NEW_LEDGER_AND_BLOCK");
            // The independent file witness is validated again by the
            // monotonic guard before any strategy can start.
            return false;
        }
        return saved==null||!"COMMITTED".equals(saved.optString("state"));
    }

    static JSONObject read(Context c) throws Exception{
        File f=marker(c);
        if(!f.isFile())return null;
        if(f.length()<10||f.length()>16384)
            throw new IllegalStateException("FRESH_EPOCH_MARKER_SIZE_INVALID");
        ByteArrayOutputStream memory=new ByteArrayOutputStream((int)f.length());
        try(FileInputStream input=new FileInputStream(f)){
            byte[] block=new byte[4096];
            int n;
            while((n=input.read(block))!=-1){
                memory.write(block,0,n);
                if(memory.size()>16384)
                    throw new IllegalStateException("FRESH_EPOCH_MARKER_TOO_LARGE");
            }
        }
        JSONObject json=new JSONObject(memory.toString(StandardCharsets.UTF_8.name()));
        if(!SCHEMA.equals(json.optString("schema"))||
                json.optString("epoch_id").isEmpty()||
                !("PREPARING".equals(json.optString("state"))||
                  "COMMITTED".equals(json.optString("state"))))
            throw new IllegalStateException("FRESH_EPOCH_MARKER_INVALID");
        return json;
    }

    static JSONObject migrationGate(Context c){
        try{
            // Recheck the independent witness here too: a background Service
            // can start without the Activity's cold-launch validation.
            if(mandatoryFreshStartPending(c))
                return new JSONObject().put("ok",false)
                    .put("status","FRESH_EPOCH_BLOCKED")
                    .put("reason","MISSING_OR_INCOMPLETE_EPOCH");
            JSONObject marker=read(c);
            if(marker==null)throw new IllegalStateException("MISSING");
            String state=marker.optString("state","");
            if(!"COMMITTED".equals(state))
                return new JSONObject().put("ok",false)
                    .put("status","FRESH_EPOCH_BLOCKED")
                    .put("reason","PREPARING_RESET_INCOMPLETE_NO_START_OR_PC_IMPORT");
            return new JSONObject().put("ok",true)
                .put("status","FRESH_EPOCH_NO_PC_IMPORT")
                .put("epoch_id",marker.getString("epoch_id"))
                .put("fresh_paper_only",true)
                .put("legacy_pc_archive_disabled",true);
        }catch(Exception e){
            try{
                return new JSONObject().put("ok",false)
                    .put("status","FRESH_EPOCH_BLOCKED")
                    .put("reason","MARKER_MALFORMED_NO_START_OR_PC_IMPORT")
                    .put("exception",e.getClass().getSimpleName());
            }catch(Exception ignored){throw new IllegalStateException(e);}
        }
    }

    /**
     * Discard ONLY stale presentation, import and PAPER-execution notification
     * caches after a committed offline reset. Broker credentials, package,
     * strategy preferences, operation seed/max and software are preserved.
     */
    static void clearDerivedCachesIfNeeded(Context c)throws Exception{
        JSONObject data=read(c);
        if(data==null||!"COMMITTED".equals(data.optString("state")))
            throw new IllegalStateException("FRESH_EPOCH_NOT_COMMITTED");
        SharedPreferences live=c.getSharedPreferences("unified_live_orders",Context.MODE_PRIVATE);
        if(live.getBoolean("live_stock",false)||live.getBoolean("live_coin",false))
            throw new IllegalStateException("LIVE_ORDERS_ARMED_FRESH_RESET_BLOCKED");
        String epoch=data.getString("epoch_id");
        SharedPreferences applied=c.getSharedPreferences(CHECKPOINT,Context.MODE_PRIVATE);
        if(epoch.equals(applied.getString("applied_epoch_id","")))return;
        String[] derived={
            "artitrade_local_records_v075",
            "artitrade_runtime_snapshot",
            "artitrade_quote_plane_v089",
            "artitrade_pc_ledger_import_v1",
            "artitrade_pc_ledger_import_progress_v078",
            "artitrade_pc_ledger_import_result",
            "artitrade_mserver_record_hash_v078",
            "artitrade_mserver_sync_v078",
            "artitrade_hub_publish_v075",
            "artitrade_manual_sale_v085",
            "artitrade_operation_pending",
            "artitrade_operation_result",
            "artitrade_preload_v081",
            "artitrade_background_health_v1",
            "artitrade_recovery_network_v092",
            "artitrade_trend_manual_result_v045"
        };
        for(String name:derived){
            if(!c.getSharedPreferences(name,Context.MODE_PRIVATE).edit().clear().commit())
                throw new IllegalStateException("FRESH_EPOCH_CACHE_CLEAR_FAILED:"+name);
        }
        // Never publish a new empty epoch into the old PC-sync session.
        if(!c.getSharedPreferences("artitrade_pc_live_sync_v1",Context.MODE_PRIVATE)
                .edit().putBoolean("enabled",false).commit())
            throw new IllegalStateException("FRESH_EPOCH_PC_SYNC_DISARM_FAILED");
        // The old snapshot is not a source ledger and must not appear as new fills.
        LargeRecordStoreV087.store(c).delete();
        if(!applied.edit().putString("applied_epoch_id",epoch).commit())
            throw new IllegalStateException("FRESH_EPOCH_CHECKPOINT_NOT_DURABLE");
    }
}
