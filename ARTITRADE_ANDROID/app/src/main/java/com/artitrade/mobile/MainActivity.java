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
        showWelcome();
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
        panel.addView(button("서버 연결 설정",this::configure));
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
    @Override public void onBackPressed() {
        if (web!=null && web.canGoBack()) web.goBack();
        else showWelcome();
    }
    @Override public void onDestroy() {
        if(web!=null){web.destroy(); web=null;}
        super.onDestroy();
    }
}
