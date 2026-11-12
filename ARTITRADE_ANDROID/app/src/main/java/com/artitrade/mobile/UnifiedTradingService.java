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
 * S21+ phone-main runtime.
 *
 * The phone receives Kiwoom and Upbit real-time streams directly. The separate
 * Phone Hub remains the authoritative ledger/control server which PC clients pull.
 * Market-data streaming stays alive even when the UI is in the background.
 */
public final class UnifiedTradingService extends Service {
    static final String START="com.artitrade.mobile.UNIFIED_START";
    static final String STOP="com.artitrade.mobile.UNIFIED_STOP";
    static final String RESTART_FEEDS="com.artitrade.mobile.RESTART_FEEDS";
    private static final String CHANNEL="artitrade_phone_main_runtime";
    private static final int NOTIFY_ID=5401;
    private final AtomicBoolean running=new AtomicBoolean(false);
    private Thread worker;
    private PowerManager.WakeLock wakeLock;
    private long stockEpoch=0,coinEpoch=0;
    private String clientId;
    private KiwoomRealtimeClient kiwoom;
    private UpbitRealtimeClient upbit;

    @Override public void onCreate(){
        super.onCreate();
        clientId=getSharedPreferences("unified_runtime",MODE_PRIVATE).getString("client_id","");
        if(clientId.isEmpty()){
            clientId="phone-"+UUID.randomUUID().toString().replace("-","").substring(0,20);
            getSharedPreferences("unified_runtime",MODE_PRIVATE).edit().putString("client_id",clientId).apply();
        }
        PowerManager pm=(PowerManager)getSystemService(Context.POWER_SERVICE);
        wakeLock=pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,"ARTITRADE:PhoneMainRuntime");
    }

    @Override public int onStartCommand(Intent intent,int flags,int startId){
        if(intent!=null&&STOP.equals(intent.getAction())){
            stopSafely();stopSelf();return START_NOT_STICKY;
        }
        ensureChannel();
        startForeground(NOTIFY_ID,notification("주식 · 코인 직접 실시간 API 준비"));
        if(intent!=null&&RESTART_FEEDS.equals(intent.getAction()))restartFeeds();
        if(running.compareAndSet(false,true)){
            if(wakeLock!=null&&!wakeLock.isHeld())wakeLock.acquire();
            startFeeds();
            worker=new Thread(this::controlLoop,"artitrade-phone-main-control");
            worker.start();
        }
        return START_STICKY;
    }

    private synchronized void startFeeds(){
        if(kiwoom==null){kiwoom=new KiwoomRealtimeClient(this);kiwoom.start();}
        if(upbit==null){upbit=new UpbitRealtimeClient(this);upbit.start();}
    }
    private synchronized void restartFeeds(){
        if(kiwoom!=null){kiwoom.stop();kiwoom=null;}
        if(upbit!=null){upbit.stop();upbit=null;}
        startFeeds();
    }

    private void controlLoop(){
        LocalHubClient hub=new LocalHubClient(this);
        RealtimeState live=new RealtimeState(this);
        while(running.get()){
            try{
                JSONObject s=hub.status();
                stockEpoch=maintain(hub,"stock",s.optJSONObject("stock"),stockEpoch);
                coinEpoch=maintain(hub,"coin",s.optJSONObject("coin"),coinEpoch);
            }catch(Exception ignored){}
            try{
                JSONObject stock=live.get("stock"),coin=live.get("coin");
                String text="주식 "+(stock.optBoolean("connected")?"LIVE":"—")+" · 코인 "+(coin.optBoolean("connected")?"LIVE":"—")
                    +" · 권한 "+(stockEpoch>0?"S":"-")+(coinEpoch>0?"C":"-");
                NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
                if(nm!=null)nm.notify(NOTIFY_ID,notification(text));
            }catch(Exception ignored){}
            try{Thread.sleep(5000);}catch(InterruptedException e){Thread.currentThread().interrupt();break;}
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

    private Notification notification(String text){
        Notification.Builder b=Build.VERSION.SDK_INT>=26?new Notification.Builder(this,CHANNEL):new Notification.Builder(this);
        return b.setContentTitle("ARTITRADE S21+ 메인 엔진")
            .setContentText(text).setSmallIcon(android.R.drawable.stat_notify_sync)
            .setOngoing(true).setCategory(Notification.CATEGORY_SERVICE).build();
    }

    private void ensureChannel(){
        if(Build.VERSION.SDK_INT>=26){
            NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
            if(nm!=null){
                NotificationChannel c=new NotificationChannel(CHANNEL,"ARTITRADE S21+ 메인 엔진",NotificationManager.IMPORTANCE_LOW);
                c.setDescription("키움·업비트 직접 실시간 시세와 폰 실행권한 유지");
                nm.createNotificationChannel(c);
            }
        }
    }

    private synchronized void stopFeeds(){
        if(kiwoom!=null){kiwoom.stop();kiwoom=null;}
        if(upbit!=null){upbit.stop();upbit=null;}
    }
    private void stopSafely(){
        running.set(false);stopFeeds();
        if(worker!=null)worker.interrupt();
        if(wakeLock!=null&&wakeLock.isHeld())wakeLock.release();
    }
    @Override public void onDestroy(){stopSafely();super.onDestroy();}
    @Override public IBinder onBind(Intent intent){return null;}
}
