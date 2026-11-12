package com.artitrade.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

public final class HubService extends Service {
    static final String START="com.artitrade.mobile.HUB_START";
    static final String STOP="com.artitrade.mobile.HUB_STOP";
    static final String UPDATE_VPN="com.artitrade.mobile.HUB_VPN";
    private static final String CHANNEL="artitrade_hub_active";
    private static final int NOTIFICATION_ID=1147;
    private static volatile boolean active=false;
    private static volatile String bindIp="";
    private HubServer server;
    private SyncStore data;

    static boolean isActive(){return active;}
    static String vpnAddress(){return bindIp;}

    @Override public void onCreate(){
        super.onCreate();
        NotificationManager manager=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        if(Build.VERSION.SDK_INT>=26){
            NotificationChannel channel=new NotificationChannel(CHANNEL,
                "ARTITRADE phone sync server",NotificationManager.IMPORTANCE_LOW);
            channel.setDescription("User-operated private phone synchronization endpoint");
            manager.createNotificationChannel(channel);
        }
    }
    private Notification notification(){
        Intent intent=new Intent(this,MainActivity.class);
        PendingIntent pending=PendingIntent.getActivity(this,0,intent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this,CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("ARTITRADE 폰 서버 실행 중")
            .setContentText("로컬 데이터·실행 권한 서버 | 누르면 설정")
            .setOngoing(true)
            .setContentIntent(pending)
            .build();
    }
    @Override public int onStartCommand(Intent intent,int flags,int startId){
        String action=intent==null?START:intent.getAction();
        if(STOP.equals(action)){stopSelf();return START_NOT_STICKY;}
        if(UPDATE_VPN.equals(action)){
            if(server!=null)server.setTailnetEnabled(getSharedPreferences("hub_ui",MODE_PRIVATE)
                    .getBoolean("tailscale_enabled",false));
            return START_STICKY;
        }
        try{
            startForeground(NOTIFICATION_ID,notification());
            if(server==null){
                SecureBox secrets=new SecureBox(this);
                secrets.token();
                data=new SyncStore(this,secrets);
                server=new HubServer(secrets,data);
                server.start(getSharedPreferences("hub_ui",MODE_PRIVATE)
                    .getBoolean("tailscale_enabled",false));
            }
            active=true;
        }catch(Exception e){
            Log.e("ArtitradeHub","Server startup failed",e);
            stopSelf();
            return START_NOT_STICKY;
        }
        return START_STICKY;
    }
    @Override public void onDestroy(){
        active=false;
        if(server!=null){server.stop();server=null;}
        if(data!=null){data.close();data=null;}
        bindIp="";
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent){return null;}
}
