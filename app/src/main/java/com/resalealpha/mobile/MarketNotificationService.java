package com.resalealpha.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Build;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MarketNotificationService extends NotificationListenerService {
    private static final String CHANNEL = "resale_alpha_opportunity";
    private static final Set<String> MARKET_PACKAGES = new HashSet<>(Arrays.asList(
            "com.towneers.www",
            "com.elz.secondhandstore",
            "kr.co.quicket"
    ));

    @Override public void onNotificationPosted(StatusBarNotification sbn) {
        if (sbn == null || !MARKET_PACKAGES.contains(sbn.getPackageName())) return;
        Bundle e = sbn.getNotification().extras;
        String title = text(e.getCharSequence(Notification.EXTRA_TITLE));
        String body = text(e.getCharSequence(Notification.EXTRA_BIG_TEXT));
        if (body.isEmpty()) body = text(e.getCharSequence(Notification.EXTRA_TEXT));
        String all = (title + " " + body).trim();
        if (all.isEmpty()) return;

        String source = sourceName(sbn.getPackageName());
        int price = parsePrice(all);
        storeCaptured(source, sbn.getPackageName(), title, body, price);

        try {
            JSONArray rules = new JSONArray(getSharedPreferences("ra_radar",MODE_PRIVATE)
                    .getString("rules","[]"));
            for (int i=0;i<rules.length();i++) {
                JSONObject r = rules.optJSONObject(i);
                if (r == null) continue;
                String q = r.optString("query","");
                int maxBuy = r.optInt("maxBuy",0);
                if (q.isEmpty() || maxBuy <= 0) continue;
                if (matches(all,q) && price > 0 && price <= maxBuy) {
                    notifyOpportunity(source,q,all,price,maxBuy);
                    break;
                }
            }
        } catch (Exception ignored) {}
    }

    private String text(CharSequence c) { return c == null ? "" : c.toString().trim(); }

    private String sourceName(String pkg) {
        if ("com.towneers.www".equals(pkg)) return "당근";
        if ("com.elz.secondhandstore".equals(pkg)) return "중고나라";
        if ("kr.co.quicket".equals(pkg)) return "번개장터";
        return pkg;
    }

    private int parsePrice(String s) {
        Matcher m = Pattern.compile("([0-9]+(?:\\.[0-9]+)?)\\s*만\\s*원?").matcher(s);
        if (m.find()) {
            try { return (int)Math.round(Double.parseDouble(m.group(1))*10000d); } catch(Exception ignored){}
        }
        m = Pattern.compile("([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})\\s*원").matcher(s);
        if (m.find()) {
            try { return Integer.parseInt(m.group(1).replace(",","")); } catch(Exception ignored){}
        }
        return 0;
    }

    private boolean matches(String text, String query) {
        String t = text.toLowerCase(Locale.ROOT);
        String[] tokens = query.toLowerCase(Locale.ROOT).split("\\s+");
        int needed = Math.max(1,(int)Math.ceil(tokens.length*0.5));
        int hits = 0;
        for (String x:tokens) if (x.length()>1 && t.contains(x)) hits++;
        return hits >= needed;
    }

    private void storeCaptured(String source,String pkg,String title,String body,int price) {
        try {
            SharedPreferences sp=getSharedPreferences("ra_radar",MODE_PRIVATE);
            JSONArray old=new JSONArray(sp.getString("captured","[]"));
            JSONArray next=new JSONArray();
            JSONObject n=new JSONObject();
            n.put("source",source);
            n.put("package",pkg);
            n.put("title",title);
            n.put("body",body);
            n.put("price",price);
            n.put("at",System.currentTimeMillis());
            next.put(n);
            for(int i=0;i<old.length()&&i<199;i++) next.put(old.get(i));
            sp.edit().putString("captured",next.toString()).apply();
        } catch(Exception ignored){}
    }

    private void notifyOpportunity(String source,String query,String all,int price,int maxBuy) {
        NotificationManager nm=(NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch=new NotificationChannel(
                    CHANNEL,"저평가 매물 감지",NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("RESALE ALPHA가 시세 기준 이하 매물을 감지했을 때 알림");
            nm.createNotificationChannel(ch);
        }

        Intent open=new Intent(this,MainActivity.class);
        open.setAction(Intent.ACTION_SEND);
        open.setType("text/plain");
        open.putExtra(Intent.EXTRA_SUBJECT,source+" 저평가 후보");
        open.putExtra(Intent.EXTRA_TEXT,all);
        open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK|Intent.FLAG_ACTIVITY_SINGLE_TOP);

        PendingIntent pi=PendingIntent.getActivity(
                this,(query+price+System.currentTimeMillis()).hashCode(),open,
                PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);

        String text=source+" · "+format(price)+" / 기준 "+format(maxBuy);
        Notification.Builder b=Build.VERSION.SDK_INT>=26
                ? new Notification.Builder(this,CHANNEL)
                : new Notification.Builder(this);
        b.setSmallIcon(R.drawable.ic_launcher)
         .setContentTitle("RESALE ALPHA · 저평가 후보")
         .setContentText(text)
         .setStyle(new Notification.BigTextStyle().bigText(query+"\n"+text+"\n"+all))
         .setContentIntent(pi)
         .setAutoCancel(true);
        nm.notify((query+all).hashCode(),b.build());
    }

    private String format(int n) { return String.format(Locale.KOREA,"%,d원",n); }
}
