package com.artitrade.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.graphics.Color;
import android.graphics.Insets;
import android.os.Build;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.content.Intent;
import android.net.Uri;
import android.provider.Settings;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * ARTITRADE unified mobile console.
 *
 * Fast-path design:
 * 1) preload every phone-hub snapshot for stock + coin in parallel,
 * 2) keep a single in-memory JSON cache,
 * 3) switch market/strategy/pages entirely inside WebView without network waits,
 * 4) refresh the cache in the background and push one atomic packet to the UI.
 *
 * This class intentionally contains no broker order endpoint. The original Windows
 * strategy engine is not embedded in this public repository, so order execution is
 * kept disabled rather than re-created from guesses.
 */
public final class UnifiedActivity extends Activity {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService io = Executors.newFixedThreadPool(8);
    private final Object cacheLock = new Object();
    private FrameLayout root;
    private WebView web;
    private JSONObject cache = new JSONObject();
    private volatile boolean dashboardReady = false;
    private volatile boolean refreshRunning = false;
    private long preloadStarted;
    private static final int TOTAL_PRELOAD = 25;
    private static final long REFRESH_MS = 3500L;

    private final Runnable periodic = new Runnable() {
        @Override public void run() {
            refreshAll(false);
            main.postDelayed(this, REFRESH_MS);
        }
    };

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.rgb(8,15,26));
        getWindow().setNavigationBarColor(Color.rgb(8,15,26));
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(8,15,26));
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController c=getWindow().getInsetsController();
            if(c!=null)c.setSystemBarsAppearance(0,
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS |
                WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
            root.setOnApplyWindowInsetsListener((v,insets)->{
                Insets safe=insets.getInsets(WindowInsets.Type.systemBars()|WindowInsets.Type.displayCutout());
                v.setPadding(safe.left,safe.top,safe.right,safe.bottom);
                return insets;
            });
        }
        setContentView(root);
        if(Build.VERSION.SDK_INT>=30) root.requestApplyInsets();
        showPreloader();
        startRuntimeService();
        refreshAll(true);
    }

    private int dp(float v){return (int)(v*getResources().getDisplayMetrics().density+.5f);}
    private TextView text(String value,int sp){
        TextView t=new TextView(this);t.setText(value);t.setTextColor(Color.WHITE);t.setTextSize(sp);return t;
    }

    private void startRuntimeService(){
        try{
            Intent i=new Intent(this,UnifiedTradingService.class);
            i.setAction(UnifiedTradingService.START);
            if(Build.VERSION.SDK_INT>=26)startForegroundService(i); else startService(i);
        }catch(Exception ignored){}
    }

    private void showPreloader(){
        root.removeAllViews();
        LinearLayout box=new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_VERTICAL);
        box.setPadding(dp(54),dp(28),dp(54),dp(28));
        root.addView(box,new FrameLayout.LayoutParams(-1,-1));

        TextView brand=text("ARTITRADE",24);
        brand.setLetterSpacing(.12f);
        brand.setTypeface(null,android.graphics.Typeface.BOLD);
        box.addView(brand);
        TextView title=text("주식 · 코인 통합 사전로딩",15);
        title.setTextColor(Color.rgb(154,178,209));
        title.setPadding(0,dp(9),0,dp(20));
        box.addView(title);

        ProgressBar bar=new ProgressBar(this,null,android.R.attr.progressBarStyleHorizontal);
        bar.setId(0x61710001);bar.setMax(100);bar.setProgress(0);
        box.addView(bar,new LinearLayout.LayoutParams(-1,dp(12)));

        TextView percent=text("0%",27);
        percent.setId(0x61710002);
        percent.setPadding(0,dp(15),0,0);
        percent.setTypeface(null,android.graphics.Typeface.BOLD);
        box.addView(percent);

        TextView status=text("폰 서버 연결 준비",13);
        status.setId(0x61710003);status.setTextColor(Color.rgb(173,197,224));
        status.setPadding(0,dp(6),0,0);box.addView(status);

        TextView eta=text("예상 완료시간 계산 중",11);
        eta.setId(0x61710004);eta.setTextColor(Color.rgb(116,143,178));
        eta.setPadding(0,dp(5),0,0);box.addView(eta);
        preloadStarted=System.currentTimeMillis();
    }

    private void updatePreload(int done,String label){
        if(!(!dashboardReady))return;
        int p=Math.min(100,(int)Math.round(done*100.0/TOTAL_PRELOAD));
        ProgressBar b=findViewById(0x61710001);
        TextView pct=findViewById(0x61710002),status=findViewById(0x61710003),eta=findViewById(0x61710004);
        if(b!=null)b.setProgress(p);
        if(pct!=null)pct.setText(p+"%");
        if(status!=null)status.setText(label);
        if(eta!=null){
            long elapsed=Math.max(1,System.currentTimeMillis()-preloadStarted);
            if(done>0&&done<TOTAL_PRELOAD){
                long remain=(long)((elapsed/(double)done)*(TOTAL_PRELOAD-done));
                eta.setText("예상 완료 "+Math.max(1,remain/1000)+"초");
            }else if(done>=TOTAL_PRELOAD)eta.setText("사전로딩 완료");
        }
    }

    private static final String[][] RECORDS={
        {"stock","shared","ledger","stockSharedLedger"},
        {"stock","shared","research","stockSharedResearch"},
        {"stock","shared","errors","stockSharedErrors"},
        {"stock","shared","settings","stockSharedSettings"},
        {"stock","at","ledger","stockAtLedger"},
        {"stock","at","research","stockAtResearch"},
        {"stock","at","errors","stockAtErrors"},
        {"stock","at","settings","stockAtSettings"},
        {"stock","cw","ledger","stockCwLedger"},
        {"stock","cw","research","stockCwResearch"},
        {"stock","cw","errors","stockCwErrors"},
        {"stock","cw","settings","stockCwSettings"},
        {"coin","shared","ledger","coinSharedLedger"},
        {"coin","shared","research","coinSharedResearch"},
        {"coin","shared","errors","coinSharedErrors"},
        {"coin","shared","settings","coinSharedSettings"},
        {"coin","general","ledger","coinGeneralLedger"},
        {"coin","general","research","coinGeneralResearch"},
        {"coin","general","errors","coinGeneralErrors"},
        {"coin","general","settings","coinGeneralSettings"},
        {"coin","ma","ledger","coinMaLedger"},
        {"coin","ma","research","coinMaResearch"},
        {"coin","ma","errors","coinMaErrors"},
        {"coin","ma","settings","coinMaSettings"}
    };

    private void refreshAll(boolean first){
        if(refreshRunning)return;
        refreshRunning=true;
        io.execute(()->{
            JSONObject packet=new JSONObject();
            JSONObject records=new JSONObject();
            LocalHubClient client=new LocalHubClient(this);
            AtomicInteger done=new AtomicInteger();
            try{
                JSONObject status;
                try{status=client.status();}catch(Exception e){status=new JSONObject().put("ok",false);}
                packet.put("status",status);
                int n=done.incrementAndGet();
                if(first)main.post(()->updatePreload(n,"서버 상태 확인"));

                CountDownLatch latch=new CountDownLatch(RECORDS.length);
                for(String[] key:RECORDS){
                    io.execute(()->{
                        try{
                            JSONObject value;
                            try{value=client.record(key[0],key[1],key[2]);}
                            catch(Exception e){value=new JSONObject().put("ok",false);}
                            synchronized(records){records.put(key[3],value);}
                        }catch(Exception ignored){}
                        int d=done.incrementAndGet();
                        if(first)main.post(()->updatePreload(d,key[0].equals("stock")?"주식 원장 사전로딩":"코인 원장 사전로딩"));
                        latch.countDown();
                    });
                }
                latch.await(8,TimeUnit.SECONDS);
                packet.put("records",records);
                packet.put("loaded_at",System.currentTimeMillis());
            }catch(Exception ignored){}
            synchronized(cacheLock){cache=packet;}
            refreshRunning=false;
            main.post(()->{
                if(first&&!dashboardReady){
                    updatePreload(TOTAL_PRELOAD,"화면 구성");
                    main.postDelayed(this::showDashboard,120);
                }else if(dashboardReady){
                    pushCache();
                }
            });
        });
    }

    private void showDashboard(){
        dashboardReady=true;
        root.removeAllViews();
        web=new WebView(this);
        WebSettings s=web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(false);
        s.setAllowContentAccess(false);
        s.setAllowFileAccess(false);
        s.setAllowFileAccessFromFileURLs(false);
        s.setAllowUniversalAccessFromFileURLs(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setJavaScriptCanOpenWindowsAutomatically(false);
        WebView.setWebContentsDebuggingEnabled(false);
        web.setBackgroundColor(Color.rgb(8,15,26));
        web.addJavascriptInterface(new Bridge(),"Artitrade");
        web.setWebViewClient(new WebViewClient(){
            @Override public void onPageFinished(WebView view,String url){pushCache();}
        });
        root.addView(web,new FrameLayout.LayoutParams(-1,-1));
        try(InputStream in=getAssets().open("unified_dashboard.html")){
            ByteArrayOutputStream out=new ByteArrayOutputStream();
            byte[] b=new byte[8192];int n;
            while((n=in.read(b))!=-1)out.write(b,0,n);
            String html=new String(out.toByteArray(),StandardCharsets.UTF_8);
            web.loadDataWithBaseURL("https://appassets.androidplatform.net/",html,"text/html","UTF-8",null);
        }catch(Exception e){showFatal("통합 대시보드 로드 실패");}
    }

    private void pushCache(){
        if(web==null)return;
        JSONObject copy;
        synchronized(cacheLock){try{copy=new JSONObject(cache.toString());}catch(Exception e){copy=new JSONObject();}}
        String quoted=JSONObject.quote(copy.toString());
        web.evaluateJavascript("window.__ARTI_SNAPSHOT("+quoted+");",null);
    }

    private void showFatal(String msg){
        root.removeAllViews();
        LinearLayout p=new LinearLayout(this);p.setOrientation(LinearLayout.VERTICAL);p.setGravity(Gravity.CENTER);
        p.addView(text(msg,18));root.addView(p,new FrameLayout.LayoutParams(-1,-1));
    }

    private final class Bridge {
        @JavascriptInterface public void refresh(){refreshAll(false);}
        @JavascriptInterface public void pair(){
            main.post(()->{
                EditText input=new EditText(UnifiedActivity.this);
                input.setSingleLine(true);
                input.setInputType(android.text.InputType.TYPE_CLASS_TEXT|android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
                input.setHint("폰 서버 64자리 연결 토큰");
                new AlertDialog.Builder(UnifiedActivity.this).setTitle("폰 서버 연결")
                    .setView(input).setNegativeButton("취소",null)
                    .setPositiveButton("저장",(d,w)->{
                        try{
                            new LocalHubClient(UnifiedActivity.this).storePairingToken(input.getText().toString().trim());
                            dashboardReady=false;showPreloader();refreshAll(true);
                        }catch(Exception e){new AlertDialog.Builder(UnifiedActivity.this).setMessage("64자리 연결 토큰을 확인하십시오.").setPositiveButton("확인",null).show();}
                    }).show();
            });
        }
        @JavascriptInterface public void runtimeSettings(){
            main.post(()->{
                Intent i=new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
                try{startActivity(i);}catch(Exception ignored){}
            });
        }
        @JavascriptInterface public void setDesired(String market,String desired){
            if(!("stock".equals(market)||"coin".equals(market)))return;
            if(!("pc".equals(desired)||"phone".equals(desired)||"off".equals(desired)))return;
            io.execute(()->{
                try{new LocalHubClient(UnifiedActivity.this).control(market,desired);}catch(Exception ignored){}
                refreshAll(false);
            });
        }
    }

    @Override protected void onResume(){
        super.onResume();
        main.removeCallbacks(periodic);
        main.postDelayed(periodic,REFRESH_MS);
    }
    @Override protected void onPause(){
        main.removeCallbacks(periodic);
        super.onPause();
    }
    @Override public void onDestroy(){
        main.removeCallbacks(periodic);
        io.shutdownNow();
        if(web!=null){web.destroy();web=null;}
        super.onDestroy();
    }
}
