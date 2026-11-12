package com.artitrade.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.content.Intent;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Insets;
import android.os.Build;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.net.Uri;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.view.ViewGroup;
import android.view.Gravity;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.FrameLayout;
import android.widget.ScrollView;
import android.webkit.JavascriptInterface;
import android.os.Handler;
import android.os.Looper;
import org.json.JSONObject;
import org.json.JSONArray;
import android.provider.DocumentsContract;
import java.io.OutputStream;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.TimeZone;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;

import java.net.URI;
import java.util.Locale;

/** Landscape/DeX HTTPS cloud UI client.
 *  This is NOT the Python trading engine and does not send orders by itself.
 *  User must provide their own trusted deployed server URL.
 */
public final class MainActivity extends Activity {
    private static final String PREF = "artitrade_cloud";
    private static final String K_URL = "endpoint";
    private FrameLayout root;
    private WebView web;
    private String trustedHost;
    private String homeUrl;
    private final AtomicBoolean dashboardLoading=new AtomicBoolean(false);
    private final Handler dashboardHandler=new Handler(Looper.getMainLooper());
    private final Runnable periodicDashboardRefresh=new Runnable(){
        @Override public void run(){
            if (!"hub".equals(BuildConfig.MARKET) && dashboardVisible) loadDashboardRecords();
            dashboardHandler.postDelayed(this,20000);
        }
    };
    private boolean dashboardVisible=false;
    private static final int REQUEST_EXPORT_RECORD=4912;
    private byte[] pendingRecordExport=null;

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(10,17,30));
        getWindow().setNavigationBarColor(Color.rgb(10,17,30));
        // API 35+ lays apps out behind the status/navigation bars by default.
        // Use the OS-provided insets, not a hard-coded status bar height.
        getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(11,18,32));
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController controller=getWindow().getInsetsController();
            if(controller!=null)controller.setSystemBarsAppearance(0,
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
            root.setOnApplyWindowInsetsListener((v,insets)->{
                Insets safe=insets.getInsets(
                    WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                v.setPadding(safe.left,safe.top,safe.right,safe.bottom);
                return insets;
            });
        }
        setContentView(root);
        if (Build.VERSION.SDK_INT >= 30) root.requestApplyInsets();
        if ("hub".equals(BuildConfig.MARKET)) {
            try{
                Intent server=new Intent(this,HubService.class);
                server.setAction(HubService.START);
                if(Build.VERSION.SDK_INT>=26)startForegroundService(server);else startService(server);
            }catch(Exception ignored){}
            new Handler(getMainLooper()).postDelayed(this::showHub,350);
        } else showModernDashboard();
    }

    private int dp(float value) { return (int)(value * getResources().getDisplayMetrics().density + 0.5f); }
    private TextView label(String value, int sp) {
        TextView text = new TextView(this);
        text.setText(value);
        text.setTextColor(Color.WHITE);
        text.setTextSize(sp);
        text.setPadding(dp(8),dp(6),dp(8),dp(6));
        return text;
    }
    private Button button(String value, Runnable onClick) {
        Button button = new Button(this);
        button.setText(value);
        button.setOnClickListener(v -> onClick.run());
        return button;
    }

    /** Number-first landscape UI. All finance figures come exclusively from the
     *  authenticated local MSERVER; missing records render as dashes.
     *  No trading buttons or order execution are exposed by this client.
     */
    private void showModernDashboard() {
        dashboardVisible=true;
        if (web != null) { web.destroy(); web = null; }
        root.removeAllViews();
        WebView dashboard = new WebView(this);
        web = dashboard;
        WebSettings settings = dashboard.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        WebView.setWebContentsDebuggingEnabled(false);
        dashboard.setBackgroundColor(Color.rgb(8,15,26));
        dashboard.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return true;
            }
        });
        dashboard.addJavascriptInterface(new MobileBridge(), "Artitrade");
        root.addView(dashboard,new FrameLayout.LayoutParams(-1,-1));
        try(InputStream input=getAssets().open("dashboard.html")) {
            ByteArrayOutputStream buffer=new ByteArrayOutputStream();
            byte[] chunk=new byte[8192];
            int count;
            while((count=input.read(chunk))!=-1)buffer.write(chunk,0,count);
            String html=new String(buffer.toByteArray(), StandardCharsets.UTF_8);
            dashboard.loadDataWithBaseURL("https://appassets.androidplatform.net/",
                html,"text/html","UTF-8",null);
        }catch(Exception ex) {
            root.removeAllViews();
            LinearLayout fallback=new LinearLayout(this);
            fallback.setOrientation(LinearLayout.VERTICAL);
            fallback.addView(label("ARTITRADE 대시보드 로드 실패",17));
            fallback.addView(button("기본 연결 화면",this::showWelcome));
            root.addView(fallback);
        }
    }

    private final class MobileBridge {
        @JavascriptInterface public String market() {
            return "stock".equals(BuildConfig.MARKET)?"stock":"coin";
        }
        @JavascriptInterface public void refresh() {
            runOnUiThread(()->loadDashboardRecords());
        }
        @JavascriptInterface public void action(String id) {
            runOnUiThread(()->{
                if("pair".equals(id)) configurePhoneHub();
                else if("status".equals(id)) readPhoneHubStatus();
                else if("records".equals(id)) selectHubRecord();
                else if("control".equals(id)) selectHubExecutionMode();
                else if("cloud".equals(id)) configure();
                else if("logs".equals(id)) showStoredErrors();
                else if("refresh".equals(id)) loadDashboardRecords();
                else if(id!=null && id.startsWith("export:")) exportHubRecord(id);
            });
        }
    }

    private void loadDashboardRecords() {
        final WebView receiver=web;
        if(receiver==null || !dashboardLoading.compareAndSet(false,true))return;
        new Thread(()->{
            JSONObject result=new JSONObject();
            JSONObject records=new JSONObject();
            try{
                String market="stock".equals(BuildConfig.MARKET)?"stock":"coin";
                result.put("market",market);
                LocalHubClient client=new LocalHubClient(this);
                try{result.put("status",client.status());}
                catch(Exception ignored){result.put("status",new JSONObject().put("ok",false));}
                for(String[] key:new String[][]{
                        {"shared","ledger","sharedLedger"},
                        {"shared","research","sharedResearch"},
                        {market.equals("stock")?"at":"general","ledger",market.equals("stock")?"atLedger":"generalLedger"},
                        {market.equals("stock")?"cw":"ma","ledger",market.equals("stock")?"cwLedger":"maLedger"},
                        {market.equals("stock")?"at":"general","research",market.equals("stock")?"atResearch":"generalResearch"},
                        {market.equals("stock")?"cw":"ma","research",market.equals("stock")?"cwResearch":"maResearch"},
                        {market.equals("stock")?"at":"general","errors",market.equals("stock")?"atErrors":"generalErrors"},
                        {market.equals("stock")?"cw":"ma","errors",market.equals("stock")?"cwErrors":"maErrors"}
                    }){
                    try{records.put(key[2],client.record(market,key[0],key[1]));}
                    catch(Exception ignored){records.put(key[2],new JSONObject().put("ok",false));}
                }
                result.put("records",records);
            }catch(Exception ignored){}
            String json=JSONObject.quote(result.toString());
            runOnUiThread(()->{
                try{
                    if(web==receiver)receiver.evaluateJavascript("window.__ARTI_UPDATE("+json+");",null);
                }finally{dashboardLoading.set(false);}
            });
        },"artitrade-ui-snapshot").start();
    }

    private void showStoredErrors() {
        new Thread(()->{
            String market="stock".equals(BuildConfig.MARKET)?"stock":"coin";
            try{
                JSONObject record=new LocalHubClient(this).record(market,"shared","errors");
                runOnUiThread(()->showHubResult("오류 기록",record.toString()));
            }catch(Exception ignored){
                runOnUiThread(()->showHubResult("오류 기록","연결할 수 없음"));
            }
        },"artitrade-ui-errors").start();
    }

    private void showWelcome() {
        dashboardVisible=false;
        if (web != null) { web.destroy(); web = null; }
        root.removeAllViews();
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(18),dp(10),dp(18),dp(10));
        panel.setGravity(Gravity.CENTER_VERTICAL);
        root.addView(panel,new FrameLayout.LayoutParams(-1,-1));
        String market = BuildConfig.MARKET.equals("stock") ? "주식 AT / CW" : "코인 B / MA";
        panel.addView(label("ARTITRADE  |  " + market, 22));
        panel.addView(label("Galaxy S21+ 가로 화면 / Samsung DeX",14));
        panel.addView(label("클라우드 서버 URL 설정이 필요합니다.\n현재 설치파일은 연결용 시험 앱이며 거래 엔진이 아닙니다.",14));
        panel.addView(button("MSERVER 연결 토큰 입력",this::configurePhoneHub));
        panel.addView(button("MSERVER 상태 조회",this::readPhoneHubStatus));
        panel.addView(button("동기화 기록 확인",this::selectHubRecord));
        panel.addView(button("기존 HTTPS 서버 연결 설정",this::configure));
        panel.addView(button("저장된 서버에 연결",this::connectSaved));
    }
    private void configure() {
        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        input.setText(getPreferences(MODE_PRIVATE).getString(K_URL,""));
        input.setHint("https://your-server.example.com");
        new AlertDialog.Builder(this)
            .setTitle("ARTITRADE 서버 HTTPS 주소")
            .setMessage("직접 관리하는 신뢰할 수 있는 서버만 연결하십시오. 비밀번호·API 비밀키를 주소에 넣지 마십시오.")
            .setView(input)
            .setNegativeButton("취소",null)
            .setPositiveButton("연결",(d,w)->{
                String url=input.getText().toString().trim();
                if (!validUrl(url)) {
                    new AlertDialog.Builder(this).setMessage("HTTPS 주소만 허용합니다. IP 주소, localhost, 사용자 정보 및 URL 쿼리는 지원하지 않습니다.").setPositiveButton("확인",null).show();
                    return;
                }
                getPreferences(MODE_PRIVATE).edit().putString(K_URL,url).apply();
                openCloud(url);
            }).show();
    }
    private boolean validUrl(String url) {
        try {
            URI uri=new URI(url);
            String host=uri.getHost();
            return "https".equalsIgnoreCase(uri.getScheme()) && host!=null
                && !host.isEmpty() && host.contains(".")
                && uri.getUserInfo()==null && uri.getQuery()==null
                && uri.getFragment()==null && (uri.getPort()==-1 || uri.getPort()==443)
                && !host.equalsIgnoreCase("localhost");
        } catch(Exception ex){ return false; }
    }
    private void connectSaved() {
        String url=getPreferences(MODE_PRIVATE).getString(K_URL,"");
        if (validUrl(url)) openCloud(url);
        else configure();
    }
    private void openCloud(String url) {
        dashboardVisible=false;
        try { trustedHost = new URI(url).getHost().toLowerCase(Locale.ROOT); }
        catch (Exception ex) { showWelcome(); return; }
        homeUrl=url;
        root.removeAllViews();
        if(web!=null) {web.destroy(); web=null;}
        LinearLayout content=new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        root.addView(content,new FrameLayout.LayoutParams(-1,-1));
        LinearLayout nav=new LinearLayout(this);
        nav.setOrientation(LinearLayout.HORIZONTAL);
        nav.setGravity(Gravity.CENTER_VERTICAL);
        nav.addView(button("홈",()->web.loadUrl(homeUrl)));
        nav.addView(button("서버 변경",this::showWelcome));
        nav.addView(button("새로고침",()->web.reload()));
        content.addView(nav,new LinearLayout.LayoutParams(-1,dp(54)));
        web=new WebView(this);
        WebSettings settings=web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setAllowFileAccessFromFileURLs(false);
        settings.setAllowUniversalAccessFromFileURLs(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUseWideViewPort(true);
        settings.setLoadWithOverviewMode(true);
        WebView.setWebContentsDebuggingEnabled(false);
        web.setWebViewClient(new WebViewClient(){
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                Uri uri=request.getUrl();
                boolean inside="https".equalsIgnoreCase(uri.getScheme())
                    && trustedHost.equalsIgnoreCase(uri.getHost())
                    && (uri.getPort()==-1 || uri.getPort()==443);
                if(inside) return false;
                if(request.isForMainFrame() && "https".equalsIgnoreCase(uri.getScheme())) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW,uri)); }
                    catch(Exception ignored) {}
                }
                return true;
            }
        });
        content.addView(web,new LinearLayout.LayoutParams(-1,0,1));
        web.loadUrl(url);
    }

    // The phone server runs in its own dedicated APK flavor; stock and coin retain
    // their separate package IDs and cannot directly read the hub's SQLite DB.
    private void showHub() {
        if(web!=null){web.destroy();web=null;}
        root.removeAllViews();
        ScrollView scroll=new ScrollView(this);
        scroll.setFillViewport(true);
        root.addView(scroll,new FrameLayout.LayoutParams(-1,-1));
        LinearLayout panel=new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(18),dp(8),dp(18),dp(20));
        scroll.addView(panel);
        panel.addView(label("ARTITRADE | MSERVER(모바일 서버)",21));
        panel.addView(label("S21+ 기준원장 · PC 조회 동기화 · 실행권한 관리",12));
        panel.addView(label("서버 상태: "+(HubService.isActive()?"실행 중":"중지됨"),15));
        LinearLayout actions=new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        actions.addView(button("서버 시작",()->{
            if(android.os.Build.VERSION.SDK_INT>=33 &&
                checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=android.content.pm.PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS},1001);
            android.content.Intent i=new android.content.Intent(this,HubService.class);
            i.setAction(HubService.START);
            if(android.os.Build.VERSION.SDK_INT>=26) startForegroundService(i);
            else startService(i);
            new android.os.Handler(getMainLooper()).postDelayed(this::showHub,700);
        }));
        actions.addView(button("서버 중지",()->{
            stopService(new android.content.Intent(this,HubService.class));
            new android.os.Handler(getMainLooper()).postDelayed(this::showHub,500);
        }));
        actions.addView(button("새로고침",this::showHub));
        panel.addView(actions);
        panel.addView(label("같은 휴대폰 앱에서 접속: http://127.0.0.1:8765",13));
        boolean vpn=getSharedPreferences("hub_ui",MODE_PRIVATE).getBoolean("tailscale_enabled",false);
        panel.addView(label("Tailscale 전용 접속: "+(vpn?"사용 설정":"사용 안 함")+" (공유기 포트 개방 금지)",12));
        panel.addView(button(vpn?"Tailscale 접속 끄기":"Tailscale 접속 켜기",()->{
            boolean next=!getSharedPreferences("hub_ui",MODE_PRIVATE).getBoolean("tailscale_enabled",false);
            getSharedPreferences("hub_ui",MODE_PRIVATE).edit().putBoolean("tailscale_enabled",next).apply();
            if(HubService.isActive()){
                android.content.Intent i=new android.content.Intent(this,HubService.class);
                i.setAction(HubService.UPDATE_VPN);startService(i);
            }
            showHub();
        }));
        panel.addView(button("MSERVER 연결 토큰 복사",()->{
            try{
                String token=new SecureBox(this).token();
                ClipboardManager cb=(ClipboardManager)getSystemService(Context.CLIPBOARD_SERVICE);
                if(cb==null)throw new IllegalStateException("클립보드 사용 불가");
                cb.setPrimaryClip(ClipData.newPlainText("ARTITRADE MSERVER token",token));
                new AlertDialog.Builder(this).setTitle("복사 완료")
                    .setMessage("MSERVER 64자리 연결 토큰을 클립보드에 복사했습니다. ARTITRADE 통합앱 설정에 붙여넣으십시오.")
                    .setPositiveButton("확인",null).show();
            }catch(Exception e){
                new AlertDialog.Builder(this).setTitle("토큰 복사 실패").setMessage(String.valueOf(e.getMessage())).setPositiveButton("확인",null).show();
            }
        }));
        panel.addView(button("MSERVER 연결 토큰 보기",()->{
            try {
                android.widget.EditText token=new android.widget.EditText(this);
                token.setSingleLine(true);token.setText(new SecureBox(this).token());
                token.setSelectAllOnFocus(true);
                new android.app.AlertDialog.Builder(this).setTitle("MSERVER 연결 토큰")
                    .setMessage("ARTITRADE 통합앱과 PC 원장 승계 도구에만 입력하십시오. 외부에 공유하지 마십시오.")
                    .setView(token).setPositiveButton("닫기",null).show();
            }catch(Exception e){new android.app.AlertDialog.Builder(this)
                .setMessage("Keystore 오류: 앱 저장공간 상태를 확인하십시오.").setPositiveButton("확인",null).show();}
        }));
        panel.addView(button("연결 토큰 재발급 (기존 연결 해제)",()->{
            new android.app.AlertDialog.Builder(this)
                .setTitle("기존 연결이 모두 끊어집니다")
                .setMessage("연동 기기에 새 토큰을 다시 입력해야 합니다.")
                .setNegativeButton("취소",null)
                .setPositiveButton("재발급",(d,w)->{
                    new SecureBox(this).rotateToken();
                    new android.app.AlertDialog.Builder(this).setMessage("재발급 완료. 새 토큰을 확인하십시오.")
                        .setPositiveButton("확인",null).show();
                }).show();
        }));
        try{
            SyncStore store=new SyncStore(this,new SecureBox(this));
            for(String market:new String[]{"stock","coin"}){
                org.json.JSONObject owner=store.owner(market);
                String display=market.equals("stock")?"주식 AT/CW":"코인 일반/MA";
                String desired=owner.optString("desired");
                String active=owner.optString("active_device");
                long expiry=owner.optLong("expires_at");
                String state=owner.optBoolean("lease_active")?"실행 권한: "+active+" (만료까지 "+Math.max(0,(expiry-System.currentTimeMillis())/1000)+"초)":"실행 권한 미사용";
                if(owner.optBoolean("review_required"))state+=" / 미체결 확인 필요";
                panel.addView(label(display+" | 선택: "+("pc".equals(desired)?"PC":"phone".equals(desired)?"폰":"정지"),17));
                panel.addView(label(state,12));
                LinearLayout choices=new LinearLayout(this);
                for(String val:new String[]{"pc","phone","off"}){
                    String title="pc".equals(val)?"PC 실행":"phone".equals(val)?"폰 실행":"전체 정지";
                    choices.addView(button(title,()->{
                        // A selection is a request, not evidence of broker order reconciliation.
                        store.prefer(market,val);
                        showHub();
                    }));
                }
                panel.addView(choices);
                if(owner.optBoolean("review_required") || (!active.isEmpty() && !owner.optBoolean("lease_active"))){
                    panel.addView(button("미체결·잔고 확인 후 잠금 해제",()->{
                        new android.app.AlertDialog.Builder(this)
                            .setTitle("실제 주문 상태 확인 필요")
                            .setMessage("증권사/거래소에서 미체결 주문·보유 수량을 직접 대조한 후에만 진행하십시오.")
                            .setNegativeButton("취소",null)
                            .setPositiveButton("확인 완료",(d,w)->{store.clearExpired(market);showHub();}).show();
                    }));
                }
            }
            store.close();
        }catch(Exception e){
            panel.addView(label("로컬 DB/Keystore 연결 오류. Android 로그를 확인하십시오.",14));
        }
        panel.addView(label("주의: 기존 EXE 주문 코드가 실행 권한 확인을 아직 수행하지 않음. 실거래 연결 전 검증 필요.",12));
        panel.addView(label("배터리 최적화 제외를 설정하더라도 Android가 서버를 중단할 수 있음.",12));
    }


    private void configurePhoneHub() {
        android.widget.EditText input=new android.widget.EditText(this);
        input.setSingleLine(true);
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT |
            android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        input.setHint("MSERVER 앱에서 표시한 64자리 연결 토큰");
        new android.app.AlertDialog.Builder(this).setTitle("MSERVER(모바일 서버) 연결")
            .setMessage("같은 S21+에 설치한 ARTITRADE MSERVER 앱의 64자리 토큰을 붙여넣으십시오.")
            .setView(input).setNegativeButton("취소",null)
            .setPositiveButton("저장",(d,w)->{
                try{
                    new LocalHubClient(this).storePairingToken(input.getText().toString().trim());
                    new android.app.AlertDialog.Builder(this).setMessage("연결 토큰 저장 완료")
                        .setPositiveButton("확인",null).show();
                }catch(Exception e){
                    new android.app.AlertDialog.Builder(this).setMessage("토큰 형식을 확인하십시오.")
                        .setPositiveButton("확인",null).show();
                }
            }).show();
    }
    private void readPhoneHubStatus() {
        new Thread(()->{
            try{
                org.json.JSONObject status=new LocalHubClient(this).status();
                runOnUiThread(()->showHubResult("MSERVER 상태",status.toString()));
            }catch(Exception e){runOnUiThread(()->showHubResult("MSERVER 연결 오류",
                "MSERVER 앱이 실행 중이고 연결 토큰이 맞는지 확인하십시오."));}
        },"hub-client-status").start();
    }
    /** Download a bounded JSON snapshot through the Android system file picker.
     *  Stock's MA/GMO are filtered views of the existing AT hub strategy.
     *  This does not export credentials or perform any trading operation.
     */
    private void exportHubRecord(String command) {
        String[] tokens=command.split(":",-1);
        if(tokens.length!=3) return;
        final String bucket=tokens[1];
        final String pane=tokens[2];
        final String market="stock".equals(BuildConfig.MARKET)?"stock":"coin";
        if(!("research".equals(bucket)||"errors".equals(bucket))) return;
        if("stock".equals(market)) {
            if(!("at".equals(pane)||"ma".equals(pane)||"close".equals(pane)||"gmo".equals(pane))) return;
        }else {
            if(!("general".equals(pane)||"ma".equals(pane))) return;
        }
        final String remote="stock".equals(market)
                ?("close".equals(pane)?"cw":"at"):pane;
        new Thread(()->{
            try {
                JSONObject record=new LocalHubClient(this).record(market,remote,bucket);
                if(!record.optBoolean("ok") || !record.optBoolean("exists")) {
                    runOnUiThread(()->showHubResult("내보낼 기록 없음","서버에 해당 기록이 없습니다."));
                    return;
                }
                JSONObject source=record.optJSONObject("data");
                if(source==null) return;
                JSONObject copy=new JSONObject(source.toString());
                JSONArray items=source.optJSONArray("items");
                if(items!=null && "stock".equals(market)&&!"close".equals(pane)){
                    JSONArray selected=new JSONArray();
                    for(int i=0;i<items.length();i++){
                        JSONObject item=items.optJSONObject(i);
                        if(item==null)continue;
                        String tag=item.optString("tf",item.optString("strategy","")).toLowerCase(java.util.Locale.ROOT);
                        boolean ma=tag.contains("ma")||tag.contains("이평");
                        boolean gmo=tag.contains("gmo")||tag.contains("수급");
                        if(("ma".equals(pane)&&ma)||("gmo".equals(pane)&&gmo)
                                ||("at".equals(pane)&&!ma&&!gmo))selected.put(item);
                    }
                    copy.put("items",selected);
                    copy.put("count",selected.length());
                }
                JSONObject output=new JSONObject();
                output.put("market",market);
                output.put("strategy",pane);
                output.put("bucket",bucket);
                output.put("revision",record.optInt("revision",0));
                output.put("data",copy);
                final byte[] bytes=output.toString(2).getBytes(StandardCharsets.UTF_8);
                SimpleDateFormat fmt=new SimpleDateFormat("yyyyMMdd_HHmm",java.util.Locale.KOREA);
                fmt.setTimeZone(TimeZone.getTimeZone("Asia/Seoul"));
                String filename="ARTITRADE_"+market+"_"+pane+"_"+bucket+"_"+fmt.format(new Date())+".json";
                runOnUiThread(()->{
                    pendingRecordExport=bytes;
                    Intent request=new Intent(Intent.ACTION_CREATE_DOCUMENT);
                    request.addCategory(Intent.CATEGORY_OPENABLE);
                    request.setType("application/json");
                    request.putExtra(Intent.EXTRA_TITLE,filename);
                    try{startActivityForResult(request,REQUEST_EXPORT_RECORD);}
                    catch(Exception ex){pendingRecordExport=null;showHubResult("내보내기 오류","저장할 파일 위치를 선택할 수 없습니다.");}
                });
            }catch(Exception ex){
                runOnUiThread(()->showHubResult("내보내기 오류","MSERVER 연결 및 원장을 확인하십시오."));
            }
        },"artitrade-export").start();
    }
    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data) {
        super.onActivityResult(requestCode,resultCode,data);
        if(requestCode!=REQUEST_EXPORT_RECORD)return;
        final byte[] contents=pendingRecordExport;
        pendingRecordExport=null;
        if(resultCode!=RESULT_OK||contents==null||data==null||data.getData()==null)return;
        final Uri target=data.getData();
        new Thread(()->{
            try(OutputStream out=getContentResolver().openOutputStream(target,"w")){
                if(out==null)throw new IllegalStateException("Storage provider refused write");
                out.write(contents);
                out.flush();
                runOnUiThread(()->showHubResult("내보내기 완료","선택한 위치에 기록을 저장했습니다."));
            }catch(Exception ex) {
                runOnUiThread(()->showHubResult("내보내기 실패","기기 저장소 권한 또는 남은 용량을 확인하십시오."));
            }
        },"artitrade-write-export").start();
    }

    private void selectHubExecutionMode() {
        if ("hub".equals(BuildConfig.MARKET)) return;
        final String market=BuildConfig.MARKET;
        final String[] values={"pc","phone","off"};
        final String[] choices={"PC 선택 (실거래 미연동)","휴대폰 선택 (실거래 미연동)","전체 정지"};
        new AlertDialog.Builder(this)
            .setTitle("매매 실행 기기")
            .setItems(choices,(dialog,index)->{
                final String selection=values[index];
                String message="off".equals(selection)?
                    "서버의 실행 기기 선택을 정지 상태로 변경합니다.":
                    "실행 기기 선택만 서버에 저장합니다. 현재 PC EXE 및 휴대폰 앱에는 실제 주문 권한 연동이 완료되지 않아 자동매매는 시작되지 않습니다.";
                new AlertDialog.Builder(this)
                    .setTitle(choices[index])
                    .setMessage(message)
                    .setNegativeButton("취소",null)
                    .setPositiveButton("확인",(d,w)->{
                        new Thread(()->{
                            try {
                                JSONObject reply=new LocalHubClient(this).control(market,selection);
                                runOnUiThread(()->{
                                    showHubResult("실행 기기 선택",reply.optBoolean("ok")?
                                        "선택 상태가 저장되었습니다. 실제 주문 실행 기능은 아직 미연결입니다.":
                                        "서버에서 변경을 거부했습니다.");
                                    loadDashboardRecords();
                                });
                            }catch(Exception e){
                                runOnUiThread(()->showHubResult("변경 실패","MSERVER 연결 상태와 인증 토큰을 확인하십시오."));
                            }
                        },"hub-device-control").start();
                    }).show();
            }).show();
    }

    private void selectHubRecord() {
        String market=BuildConfig.MARKET;
        final String[] strategies="stock".equals(market)?new String[]{"at","cw","shared"}:
            new String[]{"general","ma","shared"};
        new android.app.AlertDialog.Builder(this).setTitle("동기화 전략")
            .setItems(strategies,(d,i)->{
                final String strategy=strategies[i];
                final String[] buckets={"settings","ledger","research","errors"};
                new android.app.AlertDialog.Builder(this).setTitle("조회할 데이터")
                    .setItems(buckets,(dialog,j)->{
                        new Thread(()->{
                            try{
                                org.json.JSONObject record=new LocalHubClient(this).record(market,strategy,buckets[j]);
                                runOnUiThread(()->showHubResult(strategy+" / "+buckets[j],record.toString()));
                            }catch(Exception e){runOnUiThread(()->showHubResult("조회 실패",
                                "서버 실행 상태와 연결 토큰을 확인하십시오."));}
                        },"hub-client-record").start();
                    }).show();
            }).show();
    }
    private void showHubResult(String title,String message){
        ScrollView scroll=new ScrollView(this);
        TextView text=label(message,13);text.setTextIsSelectable(true);
        scroll.addView(text);scroll.setPadding(dp(12),dp(8),dp(12),dp(8));
        new android.app.AlertDialog.Builder(this).setTitle(title).setView(scroll)
            .setPositiveButton("닫기",null).show();
    }
    @Override public void onBackPressed() {
        if("hub".equals(BuildConfig.MARKET)){super.onBackPressed();return;}
        if (web!=null && web.canGoBack()) web.goBack();
        else showModernDashboard();
    }
    @Override protected void onResume(){
        super.onResume();
        if(!"hub".equals(BuildConfig.MARKET)){
            dashboardHandler.removeCallbacks(periodicDashboardRefresh);
            dashboardHandler.postDelayed(periodicDashboardRefresh,20000);
        }
    }
    @Override protected void onPause(){
        dashboardHandler.removeCallbacks(periodicDashboardRefresh);
        super.onPause();
    }
    @Override public void onDestroy() {
        if(web!=null){web.destroy(); web=null;}
        super.onDestroy();
    }
}
