package com.artitrade.mobile;

import android.content.Context;
import android.util.Base64;
import org.json.JSONObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.FileInputStream;
import java.security.MessageDigest;
import java.util.Locale;

/**
 * Read-only recovery selection: authenticated MSERVER latest ZIP -> private
 * quarantine -> complete SHA256 and per-source WAL-safe manifest verification.
 * It NEVER overwrites or deletes current SQLite nor resets epoch witnesses.
 * Restoring into the active live engine is intentionally forbidden.
 */
final class DailyRecoveryV003 {
    private DailyRecoveryV003(){}
    private static String sha(byte[] raw)throws Exception{
        byte[] bytes=MessageDigest.getInstance("SHA-256").digest(raw);
        StringBuilder s=new StringBuilder(64);
        for(byte b:bytes)s.append(String.format(Locale.ROOT,"%02x",b&255));
        return s.toString();
    }
    static JSONObject latest(Context context,String market)throws Exception{
        if(!"stock".equals(market)&&!"coin".equals(market))
            throw new IllegalArgumentException("RECOVERY_INVALID_MARKET");
        String epoch=FreshEpochGateV106.read(context).getString("epoch_id");
        JSONObject record=new LocalHubClient(context).dailyLatest(market,epoch);
        if(!record.optBoolean("ok",false))
            throw new IllegalStateException("DAILY_MSERVER_LATEST_QUERY_FAILED:"+
                record.optString("error",""));
        if(record.optBoolean("exists",false)&&
            !epoch.equals(record.optString("epoch_id")))
            throw new SecurityException("DAILY_RECOVERY_EPOCH_DIFFERENT");
        return record;
    }

    static JSONObject downloadAndVerify(Context context,String market)throws Exception{
        JSONObject record=latest(context,market);
        if(!record.optBoolean("exists",false))
            throw new IllegalStateException("DAILY_RECOVERY_NO_COMMITTED_SNAPSHOT");
        String date=record.getString("date");
        String epoch=record.getString("epoch_id");
        String expected=record.getString("sha256");
        long size=record.getLong("bytes");
        int chunks=record.getInt("chunks");
        if(!expected.matches("[0-9a-fA-F]{64}")||size<1||
           size>4L*1024*1024*1024||chunks<1||chunks>16384||
           (size+DailyBackupStoreV003.CHUNK_BYTES-1)/
               DailyBackupStoreV003.CHUNK_BYTES!=chunks)
            throw new SecurityException("DAILY_BACKUP_UNTRUSTED_MANIFEST");
        File parent=new File(context.getCacheDir(),"daily_recovery_quarantine_v003");
        if(!parent.isDirectory()&&!parent.mkdirs())
            throw new IllegalStateException("DAILY_RECOVERY_QUARANTINE_FAILED");
        File file=new File(parent,
            "RECOVERY_"+market+"_"+date+"_"+expected.substring(0,12)+".zip");
        File temp=new File(parent,file.getName()+".partial");
        MessageDigest digest=MessageDigest.getInstance("SHA-256");
        long collected=0;
        LocalHubClient hub=new LocalHubClient(context);
        try(FileOutputStream output=new FileOutputStream(temp,false)){
            for(int i=0;i<chunks;i++){
                JSONObject res=hub.dailyDownload(market,date,epoch,i);
                if(!res.optBoolean("ok",false)||res.optInt("index",-1)!=i)
                    throw new IllegalStateException("MSERVER_DAILY_DOWNLOAD_PART_FAILURE");
                byte[] part=Base64.decode(res.getString("data_b64"),Base64.DEFAULT);
                int want=(int)Math.min(
                    DailyBackupStoreV003.CHUNK_BYTES,
                    size-(long)i*DailyBackupStoreV003.CHUNK_BYTES);
                if(part.length!=want||
                   !sha(part).equalsIgnoreCase(res.getString("sha256")))
                    throw new SecurityException("MSERVER_DAILY_DOWNLOAD_PART_TAMPERED");
                output.write(part);
                digest.update(part);
                collected+=part.length;
            }
            output.flush();output.getFD().sync();
        }catch(Exception error){
            temp.delete();
            throw error;
        }
        StringBuilder sum=new StringBuilder(64);
        for(byte b:digest.digest())
            sum.append(String.format(Locale.ROOT,"%02x",b&255));
        if(collected!=size||!expected.equalsIgnoreCase(sum.toString())){
            temp.delete();throw new SecurityException("DAILY_RECOVERY_ZIP_DIGEST_MISMATCH");
        }
        if(!Python.isStarted())
            Python.start(new AndroidPlatform(context.getApplicationContext()));
        JSONObject validation;
        try{
            validation=new JSONObject(Python.getInstance()
                .getModule("android_ledger_backup")
                .callAttr("validate_backup_json",temp.getAbsolutePath())
                .toString());
        }catch(Throwable failure){
            temp.delete();throw new SecurityException(
                "DAILY_RECOVERY_ZIP_SQLITE_OR_MEMBER_UNVERIFIABLE",failure);
        }
        if(!validation.optBoolean("ok",false)||
           !market.equals(validation.optString("market_scope"))||
           !epoch.equals(validation.optString("epoch_id"))||
           !expected.equalsIgnoreCase(validation.optString("sha256"))){
            temp.delete();throw new SecurityException(
                "DAILY_RECOVERY_SIGNED_MANIFEST_SCOPE_MISMATCH");
        }
        if(file.exists()&&!file.delete())throw new SecurityException(
            "DAILY_RECOVERY_OLD_QUARANTINE_CANNOT_REPLACE");
        if(!temp.renameTo(file))throw new IllegalStateException(
            "DAILY_RECOVERY_QUARANTINE_RENAME_FAILED");
        return new JSONObject().put("ok",true)
            .put("status","READ_ONLY_RECOVERY_ZIP_VERIFIED_NO_ACTIVE_RESTORE")
            .put("date",date).put("epoch_id",epoch).put("market",market)
            .put("bytes",size).put("sha256",expected)
            .put("files",validation.optInt("files"))
            .put("staged_private_path",file.getAbsolutePath());
    }
}
