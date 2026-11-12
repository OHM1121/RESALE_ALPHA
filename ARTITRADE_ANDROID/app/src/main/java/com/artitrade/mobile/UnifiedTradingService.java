package com.artitrade.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import org.json.JSONObject;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Keeps stock and coin phone execution leases alive in one foreground process.
 *
 * Safety boundary: this service manages ownership only. It does not fabricate the
 * missing Windows strategy engine and therefore does not submit broker orders.
 * Broker order adapters must be connected to the verified strategy state machine
 * before production trading is enabled.
 */
public final class UnifiedTradingService extends Service {
    static final String START="com.artitrade.mobile.UNIFIED_START";
    static final String STOP="com.artitrade.mobile.UNIFIED_STOP";
    private static final String CHANNEL="artitrade_unified_runtime";
    private static final int NOTIFY_ID=5401;
    private final AtomicBoolean running=new AtomicBoolean(false);
    private Thread worker;
    private PowerManager.WakeLock wakeLock;
    private long stockEpoch=0,coinEpoch=0;
    private String clientId;

    @Override public void onCreate(){
        super.onCreate();
        clientId=getSharedPreferences("unified_runtime",MODE_PRIVATE).getString("client_id","");
        if(clientId.isEmpty()){
            clientId="phone-"+UUID.randomUUID().toString().replace("-","").substring(0,20);
            getSharedPreferences("unified_runtime",MODE_PRIVATE).edit().putString("client_id",clientId).apply();
        }
        PowerManager pm=(PowerManager)getSystemService(Context.POWER_SERVICE);
        wakeLock=pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"ARTITRADE:UnifiedRuntime");
    }

    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent!=null&&STOP.equals(intent.getAction())){
            stopSafely();stopSelf();return START_NOT_STICKY;
        }
        ensureChannel();
        startForeground(NOTIFY_ID,notification("주식 · 코인 실행권한 모니터링"));
        if(running.compareAndSet(false,true)){
            if(wakeLock!=null&&!wakeLock.isHeld())wakeLock.acquire();
            worker=new Thread(this::loop,"artitrade-unified-runtime");
            worker.start();
        }
        return START_STICKY;
    }

    private void loop(){
        LocalHubClient hub=new LocalHubClient(this);
        while(running.get()){
            try{
                JSONObject s=hub.status();
                stockEpoch=maintain(hub,"stock",s.optJSONObject("stock"),stockEpoch);
                coinEpoch=maintain(hub,"coin",s.optJSONObject("coin"),coinEpoch);
                updateNotification();
            }catch(Exception ignored){}
            try{Thread.sleep(9000);}catch(InterruptedException e){Thread.currentThread().interrupt();break;}
        }
        releaseQuietly(hub,"stock",stockEpoch);
        releaseQuietly(hub,"coin",coinEpoch);
    }

    private long maintain(LocalHubClient hub,String market,JSONObject status,long epoch){
        if(status==null||!status.optBoolean("ok"))return epoch;
        String desired=status.optString("desired","off");
        if(!"phone".equals(desired)){
            if(epoch>0)releaseQuietly(hub,market,epoch);
            return 0;
        }
        try{
            if(epoch>0){
                JSONObject r=hub.renew(market,clientId,epoch);
                if(r.optBoolean("ok"))return r.optLong("epoch",epoch);
                epoch=0;
            }
            JSONObject c=hub.claim(market,clientId);
            return c.optBoolean("ok")?c.optLong("epoch",0):0;
        }catch(Exception e){return epoch;}
    }

    private void releaseQuietly(LocalHubClient hub,String market,long epoch){
        if(epoch<=0)return;
        try{hub.release(market,clientId,epoch);}catch(Exception ignored){}
    }

    private void updateNotification(){
        String text="실행권한  주식 "+(stockEpoch>0?"PHONE":"—")+" · 코인 "+(coinEpoch>0?"PHONE":"—");
        NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        if(nm!=null)nm.notify(NOTIFY_ID,notification(text));
    }

    private Notification notification(String text){
        Notification.Builder b=Build.VERSION.SDK_INT>=26?new Notification.Builder(this,CHANNEL):new Notification.Builder(this);
        return b.setContentTitle("ARTITRADE 통합 런타임")
            .setContentText(text)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build();
    }

    private void ensureChannel(){
        if(Build.VERSION.SDK_INT>=26){
            NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
            if(nm!=null){
                NotificationChannel c=new NotificationChannel(CHANNEL,"ARTITRADE 통합 런타임",NotificationManager.IMPORTANCE_LOW);
                c.setDescription("주식·코인 실행기기 권한 및 백그라운드 상태 유지");
                nm.createNotificationChannel(c);
            }
        }
    }

    private void stopSafely(){
        running.set(false);
        if(worker!=null)worker.interrupt();
        if(wakeLock!=null&&wakeLock.isHeld())wakeLock.release();
    }

    @Override public void onDestroy(){stopSafely();super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
