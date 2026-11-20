package com.artitrade.mobile;

import android.content.Context;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Locale;

/**
 * Append-only independent daily full-PAPER ZIP archive transport.
 * Every <=256KiB chunk is SHA-256 checked and encrypted via Android Keystore
 * before being durably written in MSERVER's separate private app directory.
 * Nothing here imports an older SQLite file or touches trading engine data.
 */
final class DailyBackupStoreV003 {
    static final int CHUNK_BYTES=256*1024;
    private static final long MAX_BYTES=4L*1024*1024*1024;
    private static final int MAX_CHUNKS=16384;
    private final Context ctx;
    private final SecureBox crypt;

    DailyBackupStoreV003(Context context,SecureBox secrets){
        ctx=context.getApplicationContext();
        crypt=secrets;
    }
    private static JSONObject result(Object...pairs){
        JSONObject obj=new JSONObject();
        try{
            for(int i=0;i<pairs.length;i+=2)obj.put((String)pairs[i],pairs[i+1]);
        }catch(Exception e){throw new IllegalStateException(e);}
        return obj;
    }
    private static boolean valid(String market,String date,String epoch){
        if(!("stock".equals(market)||"coin".equals(market)))return false;
        if(date==null||!date.matches("20[0-9]{2}-[0-9]{2}-[0-9]{2}"))return false;
        try{
            java.time.LocalDate parsed=java.time.LocalDate.parse(date);
            if(!parsed.toString().equals(date))return false;
        }catch(Exception ex){return false;}
        return epoch!=null&&epoch.matches("[A-Za-z0-9_-]{8,96}");
    }
    private File dataRoot(){
        File dir=new File(ctx.getFilesDir(),"artitrade_daily_paper_v003");
        if(!dir.isDirectory()&&!dir.mkdirs())
            throw new IllegalStateException("DAILY_BACKUP_STORAGE_UNAVAILABLE");
        return dir;
    }
    private File folder(String market,String date){
        return new File(new File(dataRoot(),market),date);
    }
    private static String digest(byte[] raw){
        try{
            byte[] buf=MessageDigest.getInstance("SHA-256").digest(raw);
            StringBuilder str=new StringBuilder(64);
            for(byte b:buf)str.append(String.format(Locale.ROOT,"%02x",b&255));
            return str.toString();
        }catch(Exception e){throw new IllegalStateException(e);}
    }
    private static byte[] read(File file,int max)throws Exception{
        if(!file.isFile()||file.length()>max)throw new IOException("DAILY_BACKUP_DATA_MISSING_OR_TOO_LARGE");
        byte[] out=new byte[(int)file.length()];
        try(FileInputStream in=new FileInputStream(file)){
            int off=0,n;
            while(off<out.length&&(n=in.read(out,off,out.length-off))>0)off+=n;
            if(off!=out.length)throw new IOException("DAILY_BACKUP_SHORT_READ");
        }
        return out;
    }
    private static JSONObject metadata(File dir){
        File target=new File(dir,"meta.json");
        if(!target.isFile())return null;
        try{return new JSONObject(new String(read(target,32768),StandardCharsets.UTF_8));}
        catch(Exception e){throw new IllegalStateException("DAILY_BACKUP_METADATA_INVALID",e);}
    }
    private static void writeMeta(File dir,JSONObject meta){
        try{
            MigrationJournalIO.replace(new File(dir,"meta.json"),
                meta.toString().getBytes(StandardCharsets.UTF_8));
        }catch(IOException e){throw new IllegalStateException("DAILY_BACKUP_META_NOT_DURABLE",e);}
    }
    private static File part(File folder,int index){
        return new File(folder,String.format(Locale.ROOT,"part_%06d.enc",index));
    }
    private byte[] originalPart(File folder,int index)throws Exception{
        byte[] encoded=read(part(folder,index),CHUNK_BYTES*3);
        String plain=crypt.unseal(new String(encoded,StandardCharsets.UTF_8));
        byte[] raw=Base64.decode(plain,Base64.DEFAULT);
        if(raw.length>CHUNK_BYTES)throw new SecurityException("DAILY_BACKUP_CHUNK_OVERSIZE");
        return raw;
    }
    private static boolean same(JSONObject meta,String market,String date,String epoch,
                                String sha,long bytes,int chunks){
        return market.equals(meta.optString("market"))&&
            date.equals(meta.optString("date"))&&
            epoch.equals(meta.optString("epoch_id"))&&
            sha.equalsIgnoreCase(meta.optString("sha256"))&&
            bytes==meta.optLong("bytes",-1)&&chunks==meta.optInt("chunks",-1);
    }
    synchronized JSONObject begin(String market,String date,String epoch,String session,
                                   long bytes,String sha,int chunks){
        if(!valid(market,date,epoch)||session==null||
           !session.matches("[A-Za-z0-9_-]{8,96}")||sha==null||
           !sha.matches("[0-9a-fA-F]{64}")||
           bytes<1||bytes>MAX_BYTES||chunks<1||chunks>MAX_CHUNKS||
           (bytes+CHUNK_BYTES-1)/CHUNK_BYTES!=chunks)
            return result("ok",false,"error","daily_backup_invalid_manifest");
        File dir=folder(market,date);
        JSONObject prior=metadata(dir);
        if(prior!=null){
            if(!same(prior,market,date,epoch,sha,bytes,chunks))
                return result("ok",false,"error","immutable_daily_backup_conflict");
            if(prior.optBoolean("committed"))
                return result("ok",true,"committed",true,"received",chunks,
                              "sha256",sha,"date",date);
            if(!session.equals(prior.optString("session")))
                return result("ok",false,"error","daily_backup_different_session_in_progress");
            return result("ok",true,"received",prior.optInt("received",0),
                          "committed",false,"resumed",true);
        }
        long free=new android.os.StatFs(dataRoot().getAbsolutePath()).getAvailableBytes();
        if(free<bytes*2+128L*1024*1024)
            return result("ok",false,"error","daily_backup_not_enough_space");
        if(!dir.isDirectory()&&!dir.mkdirs())
            throw new IllegalStateException("DAILY_BACKUP_DIRECTORY_CREATE_FAILED");
        JSONObject saved=result(
            "schema","ARTITRADE_DAILY_PAPER_BACKUP_V003","market",market,
            "date",date,"epoch_id",epoch,"session",session,
            "bytes",bytes,"sha256",sha.toLowerCase(Locale.ROOT),
            "chunk_bytes",CHUNK_BYTES,"chunks",chunks,"received",0,
            "committed",false,"created_at_ms",System.currentTimeMillis());
        writeMeta(dir,saved);
        return result("ok",true,"received",0,"committed",false);
    }
    synchronized JSONObject upload(String market,String date,String session,int index,
                                   String b64,String sha){
        if(!("stock".equals(market)||"coin".equals(market))||
           date==null||!date.matches("20[0-9]{2}-[0-9]{2}-[0-9]{2}"))
            return result("ok",false,"error","invalid_daily_market_date");
        File dir=folder(market,date);
        JSONObject meta=metadata(dir);
        if(meta==null||meta.optBoolean("committed")||
           !session.equals(meta.optString("session"))||
           index<0||index>=meta.optInt("chunks",0)||
           b64==null||b64.length()>360000||
           sha==null||!sha.matches("[0-9a-fA-F]{64}"))
            return result("ok",false,"error","daily_chunk_session_invalid");
        int next=meta.optInt("received",0);
        if(index>next)return result("ok",false,"error","daily_chunk_out_of_order",
                                     "expected_index",next);
        byte[] raw;
        try{raw=Base64.decode(b64,Base64.DEFAULT);}
        catch(Exception e){return result("ok",false,"error","daily_chunk_base64_invalid");}
        long remaining=meta.optLong("bytes",0)-(long)index*CHUNK_BYTES;
        int expected=(int)Math.min(CHUNK_BYTES,remaining);
        if(expected<1||raw.length!=expected||!digest(raw).equalsIgnoreCase(sha))
            return result("ok",false,"error","daily_chunk_sha_or_size_mismatch");
        File target=part(dir,index);
        try{
            if(index<next){
                if(!target.isFile()||!digest(originalPart(dir,index)).equalsIgnoreCase(sha))
                    return result("ok",false,"error","daily_chunk_retry_conflict");
                return result("ok",true,"received",next,"duplicate",true);
            }
            String protectedBytes=crypt.seal(Base64.encodeToString(raw,Base64.NO_WRAP));
            MigrationJournalIO.replace(target,protectedBytes.getBytes(StandardCharsets.UTF_8));
            meta.put("received",index+1).put("updated_at_ms",System.currentTimeMillis());
            writeMeta(dir,meta);
            return result("ok",true,"received",index+1);
        }catch(Exception e){throw new IllegalStateException("DAILY_CHUNK_WRITE_FAILED",e);}
    }
    synchronized JSONObject commit(String market,String date,String session){
        File dir=folder(market,date);
        JSONObject meta=metadata(dir);
        if(meta==null||!session.equals(meta.optString("session")))
            return result("ok",false,"error","daily_backup_unknown_session");
        if(meta.optBoolean("committed"))
            return result("ok",true,"committed",true,"sha256",meta.optString("sha256"),
                "bytes",meta.optLong("bytes"),"date",date,"duplicate",true);
        int count=meta.optInt("chunks",0);
        if(meta.optInt("received",0)!=count)
            return result("ok",false,"error","daily_backup_incomplete");
        try{
            MessageDigest md=MessageDigest.getInstance("SHA-256");
            long total=0;
            for(int i=0;i<count;i++){
                byte[] raw=originalPart(dir,i);
                int expected=(int)Math.min(CHUNK_BYTES,
                    meta.getLong("bytes")-(long)i*CHUNK_BYTES);
                if(raw.length!=expected)
                    return result("ok",false,"error","daily_chunk_missing_or_short","index",i);
                md.update(raw);total+=raw.length;
            }
            StringBuilder sum=new StringBuilder(64);
            for(byte b:md.digest())sum.append(String.format(Locale.ROOT,"%02x",b&255));
            if(total!=meta.getLong("bytes")||
                !sum.toString().equalsIgnoreCase(meta.getString("sha256")))
                return result("ok",false,"error","daily_backup_archive_sha_mismatch");
            meta.put("committed",true).put("committed_at_ms",System.currentTimeMillis());
            writeMeta(dir,meta);
            return result("ok",true,"committed",true,
                "sha256",meta.getString("sha256"),"bytes",total,"date",date);
        }catch(Exception e){throw new IllegalStateException("DAILY_BACKUP_FINAL_VALIDATION_FAILED",e);}
    }
    synchronized JSONObject latest(String market,String epoch){
        if(!("stock".equals(market)||"coin".equals(market)))
            return result("ok",false,"error","invalid_daily_market");
        File[] folders=new File(dataRoot(),market).listFiles();
        if(folders==null)return result("ok",true,"exists",false);
        java.util.Arrays.sort(folders,(a,b)->b.getName().compareTo(a.getName()));
        for(File dir:folders){
            if(!dir.isDirectory())continue;
            JSONObject data=metadata(dir);
            if(data==null||!data.optBoolean("committed")||
               !market.equals(data.optString("market"))||
               (!epoch.isEmpty()&&!epoch.equals(data.optString("epoch_id"))))
                continue;
            return result("ok",true,"exists",true,"date",data.optString("date"),
                "epoch_id",data.optString("epoch_id"),
                "sha256",data.optString("sha256"),"bytes",data.optLong("bytes"),
                "chunks",data.optInt("chunks"),"chunk_bytes",CHUNK_BYTES,
                "committed_at_ms",data.optLong("committed_at_ms"));
        }
        return result("ok",true,"exists",false);
    }
    synchronized JSONObject download(String market,String date,int index,String epoch){
        if(!valid(market,date,epoch))
            return result("ok",false,"error","daily_backup_invalid_key");
        File dir=folder(market,date);
        JSONObject meta=metadata(dir);
        if(meta==null||!meta.optBoolean("committed")||
            !epoch.equals(meta.optString("epoch_id"))||
            index<0||index>=meta.optInt("chunks",0))
            return result("ok",false,"error","daily_backup_not_committed");
        try{
            byte[] chunk=originalPart(dir,index);
            return result("ok",true,"index",index,"bytes",chunk.length,
                "sha256",digest(chunk),"data_b64",
                Base64.encodeToString(chunk,Base64.NO_WRAP));
        }catch(Exception e){throw new IllegalStateException("DAILY_BACKUP_READ_INVALID",e);}
    }
}
