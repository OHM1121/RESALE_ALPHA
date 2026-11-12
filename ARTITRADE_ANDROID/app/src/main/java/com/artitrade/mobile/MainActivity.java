package com.artitrade.mobile;

import android.app.Activity;
import android.app.AlertDialog;
import android.os.Bundle;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.res.Configuration;
import android.graphics.Color;
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

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().setStatusBarColor(Color.rgb(10,17,30));
        getWindow().setNavigationBarColor(Color.rgb(10,17,30));
        root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(11,18,32));
        setContentView(root);
        if ("hub".equals(BuildConfig.MARKET)) showHub();
        else showWelcome();
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
    private void showWelcome() {
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
        panel.addView(button("갤럭시 데이터 서버 연결 토큰 입력",this::configurePhoneHub));
        panel.addView(button("갤럭시 데이터 서버 상태 조회",this::readPhoneHubStatus));
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
        panel.addView(label("ARTITRADE | 갤럭시 S21+ 데이터 서버",21));
        panel.addView(label("PC와 휴대폰의 설정·장부 동기화 및 실행 기기 권한 관리 (시험용)",12));
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
        panel.addView(button("PC 연결용 토큰 보기",()->{
            try {
                android.widget.EditText token=new android.widget.EditText(this);
                token.setSingleLine(true);token.setText(new SecureBox(this).token());
                token.setSelectAllOnFocus(true);
                new android.app.AlertDialog.Builder(this).setTitle("민감한 연결 토큰")
                    .setMessage("PC 동기화 도구에만 입력하십시오. GitHub나 채팅에 절대로 올리지 마십시오.")
                    .setView(token).setPositiveButton("닫기",null).show();
            }catch(Exception e){new android.app.AlertDialog.Builder(this)
                .setMessage("Keystore 오류: 앱 저장공간 상태를 확인하십시오.").setPositiveButton("확인",null).show();}
        }));
        panel.addView(button("연결 토큰 재발급 (기존 PC 연결 해제)",()->{
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
        input.setHint("허브 APK에서 표시한 64자리 연결 토큰");
        new android.app.AlertDialog.Builder(this).setTitle("폰 서버 페어링")
            .setMessage("같은 S21+에 설치한 'ARTITRADE 폰 서버' 앱의 토큰을 붙여넣으십시오.")
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
                runOnUiThread(()->showHubResult("폰 서버 상태",status.toString()));
            }catch(Exception e){runOnUiThread(()->showHubResult("폰 서버 연결 오류",
                "폰 서버 앱이 실행 중이고 페어링 토큰이 맞는지 확인하십시오."));}
        },"hub-client-status").start();
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
        else showWelcome();
    }
    @Override public void onDestroy() {
        if(web!=null){web.destroy(); web=null;}
        super.onDestroy();
    }
}
