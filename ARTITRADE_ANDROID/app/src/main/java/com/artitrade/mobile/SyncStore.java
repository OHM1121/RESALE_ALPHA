package com.artitrade.mobile;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.content.ContentValues;
import org.json.JSONObject;

/** Encrypted phone-side data store and single-writer trading-device coordinator.
 *  The API NEVER executes broker orders; existing EXE/APK engines must call check
 *  before each new order, reconcile fills, and fail closed on network loss.
 */
final class SyncStore extends SQLiteOpenHelper {
    private final SecureBox box;
    private static final long LEASE_MS=30000;
    SyncStore(Context context,SecureBox box){
        super(context,"artitrade_phone_sync_v1.db",null,1);
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
    synchronized JSONObject putRecord(String market,String strategy,String bucket,int expected,JSONObject data){
        if(!validStrategy(market,strategy)||!validBucket(bucket)||expected<0 || data==null)
            return obj("ok",false,"error","invalid_record");
        String plain=data.toString();
        if(plain.getBytes(java.nio.charset.StandardCharsets.UTF_8).length>524288)
            return obj("ok",false,"error","data_too_large");
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
