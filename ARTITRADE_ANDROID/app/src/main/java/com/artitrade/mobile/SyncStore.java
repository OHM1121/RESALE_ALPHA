package com.artitrade.mobile;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.content.ContentValues;
import android.util.Base64;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.RandomAccessFile;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/** Encrypted phone-side data store and single-writer trading-device coordinator.
 *  The API NEVER executes broker orders; existing EXE/APK engines must call check
 *  before each new order, reconcile fills, and fail closed on network loss.
 */
final class SyncStore extends SQLiteOpenHelper {
    private final SecureBox box;
    private final Context context;
    private static final long MIGRATION_MAX_BYTES=4L*1024L*1024L*1024L;
    private static final int MIGRATION_MAX_CHUNKS=5000;
    private static final long MIN_FREE_AFTER_UPLOAD=128L*1024L*1024L;
    private static final int RECORD_MAX_BYTES=32*1024*1024;
    private static final int MIGRATION_UPLOAD_CHUNK_MAX=1_000_000;
    private static final int MIGRATION_READ_CHUNK=240000;
    private static final long LEASE_MS=30000;
    SyncStore(Context context,SecureBox box){
        super(context,"artitrade_phone_sync_v1.db",null,1);
        this.context=context.getApplicationContext();
        this.box=box;
    }
    @Override public void onCreate(SQLiteDatabase db){
        db.execSQL("CREATE TABLE records (market TEXT NOT NULL, strategy TEXT NOT NULL, bucket TEXT NOT NULL, revision INTEGER NOT NULL, sealed TEXT NOT NULL, updated INTEGER NOT NULL, PRIMARY KEY(market,strategy,bucket))");
        db.execSQL("CREATE TABLE owners (market TEXT PRIMARY KEY, desired TEXT NOT NULL, device TEXT NOT NULL, client_id TEXT NOT NULL, epoch INTEGER NOT NULL, expires INTEGER NOT NULL, review INTEGER NOT NULL)");
    }
    @Override public void onUpgrade(SQLiteDatabase db,int oldVersion,int newVersion){
        throw new IllegalStateException("Manual migration required; trading records are never dropped");
    }
    static boolean validMarket(String market){return "stock".equals(market)||"coin".equals(market);}
    static boolean validStrategy(String market,String strategy){
        return "stock".equals(market) ? ("at".equals(strategy)||"cw".equals(strategy)||"shared".equals(strategy)) :
            ("coin".equals(market)&&("general".equals(strategy)||"ma".equals(strategy)||"shared".equals(strategy)));
    }
    static boolean validBucket(String bucket){
        return "settings".equals(bucket)||"ledger".equals(bucket)||"research".equals(bucket)||"errors".equals(bucket);
    }
    static boolean validClient(String client){
        return client!=null && client.matches("[A-Za-z0-9:_-]{8,80}");
    }
    private JSONObject obj(Object... pairs){
        JSONObject o=new JSONObject();
        try { for(int i=0;i<pairs.length;i+=2)o.put((String)pairs[i],pairs[i+1]); }
        catch(Exception e){throw new IllegalStateException(e);}
        return o;
    }
    synchronized JSONObject getRecord(String market,String strategy,String bucket){
        if(!validStrategy(market,strategy)||!validBucket(bucket))
            return obj("ok",false,"error","invalid_key");
        try(Cursor c=getReadableDatabase().rawQuery("SELECT revision,sealed,updated FROM records WHERE market=? AND strategy=? AND bucket=?",
                new String[]{market,strategy,bucket})){
            if(!c.moveToFirst())return obj("ok",true,"exists",false,"revision",0,"data",new JSONObject());
            return obj("ok",true,"exists",true,"revision",c.getInt(0),
                    "data",new JSONObject(box.unseal(c.getString(1))),"updated_at",c.getLong(2));
        }catch(Exception e){throw new IllegalStateException("Cannot read encrypted record",e);}
    }
    synchronized JSONObject getRecordMeta(String market,String strategy,String bucket){
        if(!validStrategy(market,strategy)||!validBucket(bucket))
            return obj("ok",false,"error","invalid_key");
        try(Cursor c=getReadableDatabase().rawQuery(
                "SELECT revision,updated FROM records WHERE market=? AND strategy=? AND bucket=?",
                new String[]{market,strategy,bucket})){
            if(!c.moveToFirst())return obj("ok",true,"exists",false,"revision",0,"updated_at",0);
            return obj("ok",true,"exists",true,"revision",c.getInt(0),"updated_at",c.getLong(1));
        }
    }

    synchronized JSONObject putRecord(String market,String strategy,String bucket,int expected,JSONObject data){
        if(!validStrategy(market,strategy)||!validBucket(bucket)||expected<0 || data==null)
            return obj("ok",false,"error","invalid_record");
        String plain=data.toString();
        int plainBytes=plain.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
        if(plainBytes>RECORD_MAX_BYTES)
            return obj("ok",false,"error","data_too_large","bytes",plainBytes,"max_bytes",RECORD_MAX_BYTES);
        SQLiteDatabase db=getWritableDatabase();
        db.beginTransaction();
        try{
            int rev=0;
            try(Cursor c=db.rawQuery("SELECT revision FROM records WHERE market=? AND strategy=? AND bucket=?",
                    new String[]{market,strategy,bucket})){
                if(c.moveToFirst())rev=c.getInt(0);
            }
            if(rev!=expected)return obj("ok",false,"error","revision_conflict","current_revision",rev);
            long now=System.currentTimeMillis();
            ContentValues cv=new ContentValues();
            cv.put("market",market);cv.put("strategy",strategy);cv.put("bucket",bucket);
            cv.put("revision",rev+1);cv.put("sealed",box.seal(plain));cv.put("updated",now);
            if(db.insertWithOnConflict("records",null,cv,SQLiteDatabase.CONFLICT_REPLACE)<0)
                throw new IllegalStateException("DB insert failure");
            db.setTransactionSuccessful();
            return obj("ok",true,"revision",rev+1,"updated_at",now);
        }finally{db.endTransaction();}
    }
    private void ensureOwner(SQLiteDatabase db,String market){
        ContentValues cv=new ContentValues();
        cv.put("market",market);cv.put("desired","off");cv.put("device","");cv.put("client_id","");
        cv.put("epoch",0);cv.put("expires",0);cv.put("review",0);
        db.insertWithOnConflict("owners",null,cv,SQLiteDatabase.CONFLICT_IGNORE);
    }
    private JSONObject current(SQLiteDatabase db,String market){
        ensureOwner(db,market);
        try(Cursor c=db.rawQuery("SELECT desired,device,client_id,epoch,expires,review FROM owners WHERE market=?",
                new String[]{market})){
            if(!c.moveToFirst())throw new IllegalStateException("Owner missing");
            long now=System.currentTimeMillis();
            return obj("market",market,"desired",c.getString(0),"active_device",c.getString(1),
                "client_id",c.getString(2),"epoch",c.getLong(3),"expires_at",c.getLong(4),
                "review_required",c.getInt(5)==1,"lease_active",!c.getString(1).isEmpty() && c.getLong(4)>now);
        }
    }
    synchronized JSONObject owner(String market){
        if(!validMarket(market))return obj("ok",false,"error","invalid_market");
        JSONObject status=current(getWritableDatabase(),market);
        try{status.put("ok",true);}catch(Exception e){throw new IllegalStateException(e);}
        return status;
    }
    synchronized JSONObject prefer(String market,String desired){
        if(!validMarket(market)||!("pc".equals(desired)||"phone".equals(desired)||"off".equals(desired)))
            return obj("ok",false,"error","invalid_preference");
        SQLiteDatabase db=getWritableDatabase();
        ensureOwner(db,market);
        ContentValues cv=new ContentValues();cv.put("desired",desired);
        db.update("owners",cv,"market=?",new String[]{market});
        return owner(market);
    }
    synchronized JSONObject claim(String market,String device,String clientId){
        if(!validMarket(market)||!("pc".equals(device)||"phone".equals(device))||!validClient(clientId))
            return obj("ok",false,"error","invalid_client");
        SQLiteDatabase db=getWritableDatabase();
        db.beginTransaction();
        try{
            JSONObject o=current(db,market); long now=System.currentTimeMillis();
            if(!device.equals(o.optString("desired")))
                return obj("ok",false,"error","device_is_read_only","desired",o.optString("desired"));
            if(o.optBoolean("review_required"))
                return obj("ok",false,"error","manual_order_review_required");
            String holder=o.optString("client_id");
            boolean same=clientId.equals(holder)&&device.equals(o.optString("active_device"));
            if(!holder.isEmpty() && !same && o.optLong("expires_at")>now)
                return obj("ok",false,"error","previous_device_still_active");
            // Expired lease from another client requires explicit human reconciliation.
            if(!holder.isEmpty() && !same){
                ContentValues block=new ContentValues();block.put("review",1);
                db.update("owners",block,"market=?",new String[]{market});
                db.setTransactionSuccessful();
                return obj("ok",false,"error","manual_order_review_required");
            }
            long epoch=same && o.optLong("expires_at")>now ?
                    o.optLong("epoch") : o.optLong("epoch")+1;
            ContentValues cv=new ContentValues();
            cv.put("device",device);cv.put("client_id",clientId);
            cv.put("epoch",epoch);cv.put("expires",now+LEASE_MS);
            db.update("owners",cv,"market=?",new String[]{market});
            db.setTransactionSuccessful();
            return obj("ok",true,"granted",true,"epoch",epoch,"expires_at",now+LEASE_MS);
        }finally{db.endTransaction();}
    }
    synchronized JSONObject renew(String market,String device,String clientId,long epoch,boolean release){
        if(!validMarket(market)||!("pc".equals(device)||"phone".equals(device))||!validClient(clientId)||epoch<=0)
            return obj("ok",false,"error","invalid_client");
        SQLiteDatabase db=getWritableDatabase();
        JSONObject o=current(db,market);long now=System.currentTimeMillis();
        boolean valid=device.equals(o.optString("active_device"))
             && clientId.equals(o.optString("client_id"))
             && epoch==o.optLong("epoch") && !o.optBoolean("review_required")
             && o.optLong("expires_at")>now;
        if(!valid)return obj("ok",false,"error","lease_invalid_or_expired");
        if(!release && !device.equals(o.optString("desired")))
            return obj("ok",false,"error","switch_requested_stop_orders");
        ContentValues cv=new ContentValues();
        if(release){cv.put("device","");cv.put("client_id","");cv.put("expires",0);cv.put("epoch",epoch+1);}
        else cv.put("expires",now+LEASE_MS);
        db.update("owners",cv,"market=?",new String[]{market});
        return obj("ok",true,"granted",!release,"epoch",release?epoch+1:epoch,
            "expires_at",release?0:now+LEASE_MS);
    }
    synchronized JSONObject check(String market,String device,String clientId,long epoch){
        if(!validMarket(market)||!("pc".equals(device)||"phone".equals(device))||!validClient(clientId)||epoch<=0)
            return obj("ok",false,"allowed",false,"error","invalid_client");
        JSONObject o=current(getWritableDatabase(),market);
        boolean allowed=device.equals(o.optString("desired"))
             && device.equals(o.optString("active_device"))
             && clientId.equals(o.optString("client_id"))
             && epoch==o.optLong("epoch") && o.optLong("expires_at")>System.currentTimeMillis()
             && !o.optBoolean("review_required");
        return obj("ok",true,"allowed",allowed,"epoch",o.optLong("epoch"));
    }

    private File migrationDir(){
        File d=new File(context.getFilesDir(),"pc_ledger_migration_v1");
        if(!d.exists()&&!d.mkdirs())throw new IllegalStateException("Cannot create migration directory");
        return d;
    }
    private File migrationMeta(){return new File(migrationDir(),"meta.json");}
    private File migrationArchive(){return new File(migrationDir(),"pc_ledger_import.zip");}
    private static boolean validSession(String s){return s!=null&&s.matches("[A-Za-z0-9_-]{12,80}");}
    private static String sha256(byte[] data){
        try{
            MessageDigest md=MessageDigest.getInstance("SHA-256");
            byte[] d=md.digest(data);StringBuilder b=new StringBuilder(64);
            for(byte x:d)b.append(String.format(java.util.Locale.ROOT,"%02x",x&255));
            return b.toString();
        }catch(Exception e){throw new IllegalStateException(e);}
    }
    private static String sha256File(File file)throws Exception{
        MessageDigest md=MessageDigest.getInstance("SHA-256");
        byte[] buf=new byte[65536];
        try(FileInputStream in=new FileInputStream(file)){int n;while((n=in.read(buf))!=-1)md.update(buf,0,n);}
        StringBuilder b=new StringBuilder(64);for(byte x:md.digest())b.append(String.format(java.util.Locale.ROOT,"%02x",x&255));return b.toString();
    }
    private JSONObject readMigrationMeta(){
        File f=migrationMeta();if(!f.isFile())return new JSONObject();
        try(FileInputStream in=new FileInputStream(f)){
            ByteArrayOutputStream out=new ByteArrayOutputStream((int)Math.min(f.length(),65536));
            byte[] buf=new byte[8192];int n;while((n=in.read(buf))!=-1)out.write(buf,0,n);
            return new JSONObject(new String(out.toByteArray(),StandardCharsets.UTF_8));
        }catch(Exception e){throw new IllegalStateException("Cannot read migration metadata",e);}
    }
    private void writeMigrationMeta(JSONObject meta){
        File target=migrationMeta(),tmp=new File(target.getParentFile(),"meta.json.tmp");
        try(FileOutputStream out=new FileOutputStream(tmp,false)){
            out.write(meta.toString().getBytes(StandardCharsets.UTF_8));out.flush();out.getFD().sync();
        }catch(Exception e){throw new IllegalStateException("Cannot write migration metadata",e);}
        if(target.exists()&&!target.delete())throw new IllegalStateException("Cannot replace migration metadata");
        if(!tmp.renameTo(target))throw new IllegalStateException("Cannot commit migration metadata");
    }
    private static void deleteTree(File f){
        if(f==null||!f.exists())return;
        if(f.isDirectory()){File[] xs=f.listFiles();if(xs!=null)for(File x:xs)deleteTree(x);}
        if(!f.delete()&&f.exists())throw new IllegalStateException("Cannot clear migration staging");
    }
    private File migrationUpload(){return new File(migrationDir(),"pc_ledger_upload.tmp");}
    synchronized JSONObject migrationBegin(String session,int totalChunks,long totalBytes,String archiveSha,JSONObject source){
        int chunkBytes=source==null?300000:source.optInt("chunk_bytes",300000);
        long requiredParts=chunkBytes>0?(totalBytes+(long)chunkBytes-1)/chunkBytes:0L;
        if(!validSession(session)||totalChunks<1||totalChunks>MIGRATION_MAX_CHUNKS||
           totalBytes<1||totalBytes>MIGRATION_MAX_BYTES||chunkBytes<1||chunkBytes>MIGRATION_UPLOAD_CHUNK_MAX||
           requiredParts!=totalChunks||archiveSha==null||!archiveSha.matches("[0-9a-fA-F]{64}"))
            return obj("ok",false,"error","invalid_migration_manifest","max_bytes",MIGRATION_MAX_BYTES,
                "max_chunks",MIGRATION_MAX_CHUNKS);
        // Never erase a fully received archive that the phone has not imported.
        JSONObject existing=readMigrationMeta();
        if(existing.optBoolean("ready",false)&&migrationArchive().isFile())
            return obj("ok",false,"error","previous_migration_pending",
                "message","Phone must consume pending migration before a new upload.");
        File dir=migrationDir();
        long free=new android.os.StatFs(dir.getAbsolutePath()).getAvailableBytes();
        if(free<totalBytes+MIN_FREE_AFTER_UPLOAD)
            return obj("ok",false,"error","not_enough_phone_storage",
                "required_bytes",totalBytes+MIN_FREE_AFTER_UPLOAD,"available_bytes",free);
        File[] old=dir.listFiles();
        if(old!=null)for(File x:old)deleteTree(x);
        JSONObject meta=obj("session",session,"total_chunks",totalChunks,"total_bytes",totalBytes,
            "chunk_bytes",chunkBytes,"archive_sha256",archiveSha.toLowerCase(java.util.Locale.ROOT),
            "received",0,"ready",false,"created_at",System.currentTimeMillis(),
            "source",source==null?new JSONObject():source);
        writeMigrationMeta(meta);
        return obj("ok",true,"session",session,"total_chunks",totalChunks,"chunk_bytes",chunkBytes);
    }
    synchronized JSONObject migrationPutChunk(String session,int index,String dataB64,String chunkSha){
        JSONObject meta=readMigrationMeta();
        int total=meta.optInt("total_chunks",-1), next=meta.optInt("received",0);
        int chunkBytes=meta.optInt("chunk_bytes",300000);
        if(!validSession(session)||!session.equals(meta.optString("session"))||
            meta.optBoolean("ready")||index<0||index>=total||chunkBytes<1||
            dataB64==null||dataB64.length()>1_400_000||chunkSha==null||
            !chunkSha.matches("[0-9a-fA-F]{64}"))
            return obj("ok",false,"error","invalid_migration_chunk");
        byte[] data;
        try{data=Base64.decode(dataB64,Base64.DEFAULT);}
        catch(Exception e){return obj("ok",false,"error","invalid_chunk_base64");}
        long offset=(long)index*chunkBytes;
        long remaining=meta.optLong("total_bytes",-1)-offset;
        int expected=(int)Math.min(chunkBytes,remaining);
        if(expected<=0||data.length!=expected||data.length>MIGRATION_UPLOAD_CHUNK_MAX)
            return obj("ok",false,"error","invalid_migration_chunk_size",
                "expected_bytes",expected,"got_bytes",data.length);
        if(!sha256(data).equalsIgnoreCase(chunkSha))
            return obj("ok",false,"error","migration_chunk_hash_mismatch");
        if(index>next)
            return obj("ok",false,"error","migration_out_of_order","expected_index",next);
        try(RandomAccessFile file=new RandomAccessFile(migrationUpload(),"rw")){
            if(index<next){
                if(file.length()<offset+data.length)
                    return obj("ok",false,"error","migration_chunk_missing_on_disk");
                byte[] existing=new byte[data.length];
                file.seek(offset);file.readFully(existing);
                if(!sha256(existing).equalsIgnoreCase(chunkSha))
                    return obj("ok",false,"error","migration_chunk_conflict");
                return obj("ok",true,"received",next,"total_chunks",total,"duplicate",true);
            }
            if(file.length()<offset)
                return obj("ok",false,"error","migration_stream_gap");
            if(file.length()!=offset)file.setLength(offset);
            file.seek(offset);file.write(data);file.getFD().sync();
        }catch(Exception e){throw new IllegalStateException("Cannot store migration chunk",e);}
        try{meta.put("received",index+1).put("updated_at",System.currentTimeMillis());}
        catch(Exception e){throw new IllegalStateException(e);}
        writeMigrationMeta(meta);
        return obj("ok",true,"received",index+1,"total_chunks",total);
    }
    synchronized JSONObject migrationCommit(String session){
        JSONObject meta=readMigrationMeta();
        if(!validSession(session)||!session.equals(meta.optString("session")))
            return obj("ok",false,"error","invalid_migration_session");
        if(meta.optBoolean("ready")&&migrationArchive().isFile())
            return obj("ok",true,"ready",true,"duplicate",true,"total_bytes",
                meta.optLong("total_bytes",0),"archive_sha256",meta.optString("archive_sha256"));
        int total=meta.optInt("total_chunks",0),received=meta.optInt("received",0);
        if(total<1||received!=total)
            return obj("ok",false,"error","migration_chunks_missing","received",received,"total",total);
        File tmp=migrationUpload(),target=migrationArchive();
        long bytes=tmp.isFile()?tmp.length():0L;
        if(bytes!=meta.optLong("total_bytes",-1))
            return obj("ok",false,"error","migration_size_mismatch",
                "bytes",bytes,"expected_bytes",meta.optLong("total_bytes",-1));
        String got;
        try{got=sha256File(tmp);}
        catch(Exception e){throw new IllegalStateException("Cannot verify migration archive",e);}
        if(!got.equalsIgnoreCase(meta.optString("archive_sha256")))
            return obj("ok",false,"error","migration_archive_hash_mismatch");
        try{
            java.nio.file.Files.move(tmp.toPath(),target.toPath(),
                java.nio.file.StandardCopyOption.REPLACE_EXISTING,
                java.nio.file.StandardCopyOption.ATOMIC_MOVE);
        }catch(Exception e){throw new IllegalStateException("Cannot commit migration archive",e);}
        int readChunks=(int)((bytes+MIGRATION_READ_CHUNK-1)/MIGRATION_READ_CHUNK);
        try{meta.put("ready",true).put("read_chunk_bytes",MIGRATION_READ_CHUNK)
                .put("read_chunks",readChunks).put("committed_at",System.currentTimeMillis());}
        catch(Exception e){throw new IllegalStateException(e);}
        writeMigrationMeta(meta);
        return obj("ok",true,"ready",true,"archive_sha256",got,
            "total_bytes",bytes,"read_chunks",readChunks);
    }
    synchronized JSONObject migrationStatus(){
        JSONObject meta=readMigrationMeta();
        if(meta.length()==0)return obj("ok",true,"ready",false,"exists",false);
        try{meta.put("ok",true).put("exists",true).put("archive_exists",migrationArchive().isFile());}
        catch(Exception e){throw new IllegalStateException(e);}
        return meta;
    }
    synchronized JSONObject migrationReadChunk(String session,int index){
        JSONObject meta=readMigrationMeta();File archive=migrationArchive();
        if(!meta.optBoolean("ready")||!archive.isFile()||!session.equals(meta.optString("session")))
            return obj("ok",false,"error","migration_not_ready");
        int total=meta.optInt("read_chunks",0);
        if(index<0||index>=total)return obj("ok",false,"error","invalid_migration_read_index");
        long offset=(long)index*MIGRATION_READ_CHUNK;
        int want=(int)Math.min((long)MIGRATION_READ_CHUNK,archive.length()-offset);
        byte[] data=new byte[want];
        try(RandomAccessFile in=new RandomAccessFile(archive,"r")){in.seek(offset);in.readFully(data);}
        catch(Exception e){throw new IllegalStateException("Cannot read migration archive",e);}
        return obj("ok",true,"index",index,"read_chunks",total,"bytes",want,"sha256",sha256(data),
            "data_b64",Base64.encodeToString(data,Base64.NO_WRAP));
    }
    synchronized JSONObject migrationAcknowledge(String session,String archiveSha){
        JSONObject meta=readMigrationMeta();
        if(!session.equals(meta.optString("session"))||!archiveSha.equalsIgnoreCase(meta.optString("archive_sha256")))
            return obj("ok",false,"error","invalid_migration_ack");
        File archive=migrationArchive();boolean deleted=!archive.exists()||archive.delete();
        try{meta.put("acknowledged_at",System.currentTimeMillis()).put("consumed",true).put("ready",false);}
        catch(Exception e){throw new IllegalStateException(e);}
        writeMigrationMeta(meta);
        return obj("ok",deleted,"consumed",true);
    }

    synchronized JSONObject clearExpired(String market){
        if(!validMarket(market))return obj("ok",false,"error","invalid_market");
        JSONObject o=current(getWritableDatabase(),market);
        if(o.optBoolean("lease_active"))
            return obj("ok",false,"error","active_lease_cannot_be_cleared");
        ContentValues cv=new ContentValues();cv.put("device","");cv.put("client_id","");
        cv.put("expires",0);cv.put("review",0);cv.put("epoch",o.optLong("epoch")+1);
        getWritableDatabase().update("owners",cv,"market=?",new String[]{market});
        return owner(market);
    }
}
