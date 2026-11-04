package com.resalealpha.mobile;

import android.Manifest;
import android.app.Activity;
import android.app.NotificationManager;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.MediaStore;
import android.provider.Settings;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.view.View;
import android.widget.FrameLayout;
import android.widget.Toast;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public class MainActivity extends Activity {
    private WebView webView;
    private WebView scanWebView;
    private FrameLayout root;
    private String pendingShare = "";
    private ValueCallback<Uri[]> fileCallback;
    private Uri cameraUri;
    private static final int REQ_FILE = 901;
    private static final int REQ_NOTIF = 902;
    private static final String SECURE_ALIAS = "resale_alpha_secure_v1";

    private boolean scanBusy = false;
    private String scanQuery = "";
    private final String[] scanSites = {"당근","중고나라","번개장터"};
    private int scanSiteIndex = 0;
    private JSONArray scanCombined = new JSONArray();

    private final Set<String> allowedHosts = new HashSet<>(Arrays.asList(
            "www.daangn.com", "daangn.com",
            "web.joongna.com", "joongna.com",
            "m.bunjang.co.kr", "bunjang.co.kr",
            "www.bunjang.co.kr"
    ));

    @Override public void onCreate(Bundle b) {
        super.onCreate(b);

        root = new FrameLayout(this);
        webView = new WebView(this);
        scanWebView = new WebView(this);

        root.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT));

        FrameLayout.LayoutParams hidden = new FrameLayout.LayoutParams(2,2);
        hidden.leftMargin = -10;
        hidden.topMargin = -10;
        root.addView(scanWebView, hidden);
        scanWebView.setAlpha(0.01f);
        scanWebView.setVisibility(View.VISIBLE);
        setContentView(root);

        configureMainWebView();
        configureScannerWebView();

        handleShare(getIntent());
        webView.loadUrl("file:///android_asset/index.html");

        if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, REQ_NOTIF);
        }
    }

    private void configureMainWebView() {
        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);
        webView.addJavascriptInterface(new Bridge(), "ResaleAlphaAndroid");

        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest req) {
                Uri u = req.getUrl();
                if ("file".equalsIgnoreCase(u.getScheme())) return false;
                openExternal(u.toString());
                return true;
            }

            @Override public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view,url);
                deliverShare();
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onShowFileChooser(WebView w, ValueCallback<Uri[]> cb, FileChooserParams p) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = cb;

                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.setType("image/*");
                pick.addCategory(Intent.CATEGORY_OPENABLE);

                Intent cam = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
                try {
                    ContentValues cv = new ContentValues();
                    cv.put(MediaStore.Images.Media.DISPLAY_NAME, "resale_alpha_" + System.currentTimeMillis() + ".jpg");
                    cv.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
                    cameraUri = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, cv);
                    if (cameraUri != null) {
                        cam.putExtra(MediaStore.EXTRA_OUTPUT, cameraUri);
                        cam.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION | Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    }
                } catch (Exception ignored) {}

                Intent chooser = Intent.createChooser(pick, "사진 선택 또는 촬영");
                if (cam.resolveActivity(getPackageManager()) != null) {
                    chooser.putExtra(Intent.EXTRA_INITIAL_INTENTS, new Intent[]{cam});
                }
                startActivityForResult(chooser, REQ_FILE);
                return true;
            }
        });
    }

    private void configureScannerWebView() {
        WebSettings s = scanWebView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setLoadsImagesAutomatically(false);
        s.setBlockNetworkImage(true);
        s.setUserAgentString(webView.getSettings().getUserAgentString());

        scanWebView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                return false;
            }

            @Override public void onPageFinished(WebView view, String url) {
                if (!scanBusy) return;
                view.postDelayed(() -> extractCurrentMarket(), 2600);
            }
        });
    }

    private String marketSearchUrl(int index, String q) {
        String e = Uri.encode(q);
        if (index == 0) return "https://www.daangn.com/kr/buy-sell/?search=" + e + "&only_on_sale=true";
        if (index == 1) return "https://web.joongna.com/search/" + e;
        return "https://m.bunjang.co.kr/search/products?q=" + e;
    }

    private void startMarketScan(String query) {
        if (scanBusy) {
            toast("다른 3마켓 스캔이 진행 중입니다.");
            return;
        }
        query = query == null ? "" : query.trim();
        if (query.isEmpty()) {
            toast("검색어를 입력하세요.");
            return;
        }
        scanBusy = true;
        scanQuery = query;
        scanSiteIndex = 0;
        scanCombined = new JSONArray();
        callJs("window.onNativeScanStart&&window.onNativeScanStart(" + JSONObject.quote(query) + ")");
        scanWebView.loadUrl(marketSearchUrl(scanSiteIndex, scanQuery));
    }

    private void extractCurrentMarket() {
        final String site = scanSites[scanSiteIndex];
        final String q = JSONObject.quote(scanQuery);
        String script =
            "(function(){" +
            "const query=" + q + ".toLowerCase();" +
            "const tokens=query.split(/\\s+/).filter(x=>x.length>1);" +
            "const bad=['케이스','필름','강화유리','박스만','박스 단품','충전기만','부품용','부품 판매','삽니다','구해요','매입합니다','교환'];" +
            "function price(s){" +
            " let m=s.match(/([0-9]+(?:\\.[0-9]+)?)\\s*만\\s*원?/); if(m)return Math.round(parseFloat(m[1])*10000);" +
            " m=s.match(/([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})\\s*원/); if(m)return parseInt(m[1].replace(/,/g,''));" +
            " return 0;" +
            "}" +
            "let out=[];" +
            "document.querySelectorAll('a[href]').forEach(a=>{" +
            " const text=(a.innerText||a.textContent||'').replace(/\\s+/g,' ').trim();" +
            " if(text.length<4||text.length>500)return;" +
            " const low=text.toLowerCase();" +
            " if(bad.some(b=>low.includes(b)))return;" +
            " const hits=tokens.filter(t=>low.includes(t)).length;" +
            " if(tokens.length&&hits<Math.max(1,Math.ceil(tokens.length*0.5)))return;" +
            " const p=price(text); if(!p||p<1000)return;" +
            " let href=a.href||''; if(!/^https?:/i.test(href))return;" +
            " out.push({title:text.slice(0,180),price:p,url:href});" +
            "});" +
            "let seen=new Set();" +
            "out=out.filter(x=>{const k=x.url+'|'+x.price;if(seen.has(k))return false;seen.add(k);return true;});" +
            "out.sort((a,b)=>a.price-b.price);" +
            "return JSON.stringify(out.slice(0,80));" +
            "})()";

        scanWebView.evaluateJavascript(script, raw -> {
            try {
                Object decoded = new JSONTokener(raw == null ? ""[]"" : raw).nextValue();
                String json = decoded instanceof String ? (String) decoded : "[]";
                JSONArray arr = new JSONArray(json);
                for (int i=0;i<arr.length();i++) {
                    JSONObject o=arr.getJSONObject(i);
                    o.put("source",site);
                    scanCombined.put(o);
                }
                callJs("window.onNativeScanMarket&&window.onNativeScanMarket(" +
                        JSONObject.quote(scanQuery) + "," +
                        JSONObject.quote(site) + "," + arr.length() + ")");
            } catch (Exception e) {
                callJs("window.onNativeScanMarket&&window.onNativeScanMarket(" +
                        JSONObject.quote(scanQuery) + "," +
                        JSONObject.quote(site) + ",0)");
            }

            scanSiteIndex++;
            if (scanSiteIndex < scanSites.length) {
                scanWebView.loadUrl(marketSearchUrl(scanSiteIndex, scanQuery));
            } else {
                finishMarketScan();
            }
        });
    }

    private void finishMarketScan() {
        String payload = scanCombined.toString();
        String query = scanQuery;
        scanBusy = false;
        scanQuery = "";
        scanSiteIndex = 0;
        scanCombined = new JSONArray();
        callJs("window.onNativeScanComplete&&window.onNativeScanComplete(" +
                JSONObject.quote(query) + "," + JSONObject.quote(payload) + ")");
        scanWebView.loadUrl("about:blank");
    }

    private void callJs(String js) {
        if (webView == null) return;
        webView.post(() -> webView.evaluateJavascript(js, null));
    }

    @Override protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        setIntent(i);
        handleShare(i);
        deliverShare();
    }

    private void handleShare(Intent i) {
        if (i != null && Intent.ACTION_SEND.equals(i.getAction()) && "text/plain".equals(i.getType())) {
            String t = i.getStringExtra(Intent.EXTRA_TEXT);
            String subject = i.getStringExtra(Intent.EXTRA_SUBJECT);
            pendingShare = ((subject == null ? "" : subject + "\n") + (t == null ? "" : t)).trim();
        }
    }

    private void deliverShare() {
        if (webView == null || pendingShare.isEmpty()) return;
        String q = JSONObject.quote(pendingShare);
        webView.postDelayed(() ->
                webView.evaluateJavascript("window.receiveSharedText&&window.receiveSharedText(" + q + ")", null), 150);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_FILE && fileCallback != null) {
            Uri[] result = null;
            if (resultCode == RESULT_OK) {
                if (data != null && data.getData() != null) result = new Uri[]{data.getData()};
                else if (cameraUri != null) result = new Uri[]{cameraUri};
            }
            fileCallback.onReceiveValue(result);
            fileCallback = null;
        }
    }

    private SecretKey secureKey() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (!ks.containsAlias(SECURE_ALIAS)) {
            KeyGenerator kg = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            kg.init(new KeyGenParameterSpec.Builder(
                    SECURE_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .build());
            kg.generateKey();
        }
        return ((KeyStore.SecretKeyEntry) ks.getEntry(SECURE_ALIAS, null)).getSecretKey();
    }

    private String encryptSecure(String plain) throws Exception {
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, secureKey());
        byte[] iv = c.getIV();
        byte[] enc = c.doFinal(plain.getBytes(StandardCharsets.UTF_8));
        return Base64.encodeToString(iv, Base64.NO_WRAP) + ":" + Base64.encodeToString(enc, Base64.NO_WRAP);
    }

    private String decryptSecure(String value) throws Exception {
        if (value == null || value.isEmpty()) return "";
        String[] p = value.split(":", 2);
        if (p.length != 2) return "";
        byte[] iv = Base64.decode(p[0], Base64.NO_WRAP);
        byte[] enc = Base64.decode(p[1], Base64.NO_WRAP);
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.DECRYPT_MODE, secureKey(), new GCMParameterSpec(128, iv));
        return new String(c.doFinal(enc), StandardCharsets.UTF_8);
    }

    private void openExternal(String url) {
        try {
            Uri u = Uri.parse(url);
            String h = u.getHost();
            if (h == null || !allowedHosts.contains(h.toLowerCase(Locale.ROOT))) {
                toast("허용된 중고마켓 링크만 열 수 있습니다.");
                return;
            }
            Intent in = new Intent(Intent.ACTION_VIEW, u);
            in.addFlags(Intent.FLAG_ACTIVITY_NEW_DOCUMENT);
            startActivity(in);
        } catch (Exception e) {
            toast("링크를 열 수 없습니다.");
        }
    }

    private void toast(String s) {
        runOnUiThread(() -> Toast.makeText(this,s,Toast.LENGTH_SHORT).show());
    }

    private boolean notificationAccessEnabled() {
        Set<String> packs = NotificationManager.getEnabledListenerPackages(this);
        return packs.contains(getPackageName());
    }

    public class Bridge {
        @JavascriptInterface public void openUrl(String url) { runOnUiThread(() -> openExternal(url)); }

        @JavascriptInterface public void openAll(String json) {
            runOnUiThread(() -> {
                try {
                    JSONArray a = new JSONArray(json);
                    for (int n=0;n<a.length();n++) {
                        final String u=a.getString(n);
                        webView.postDelayed(() -> openExternal(u),n*300L);
                    }
                } catch (Exception e) { toast("통합 조회 링크 오류"); }
            });
        }

        @JavascriptInterface public void scanMarkets(String query) {
            runOnUiThread(() -> startMarketScan(query));
        }

        @JavascriptInterface public boolean isScanBusy() { return scanBusy; }

        @JavascriptInterface public String getInitialShare() {
            String t=pendingShare; pendingShare=""; return t;
        }

        @JavascriptInterface public void openNotificationAccess() {
            runOnUiThread(() -> {
                try { startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)); }
                catch (Exception e) { toast("알림 접근 설정을 열 수 없습니다."); }
            });
        }

        @JavascriptInterface public boolean notificationAccessEnabled() {
            return MainActivity.this.notificationAccessEnabled();
        }

        @JavascriptInterface public void saveRadarRules(String json) {
            getSharedPreferences("ra_radar",MODE_PRIVATE).edit().putString("rules",json==null?"[]":json).apply();
        }

        @JavascriptInterface public String getCapturedNotifications() {
            SharedPreferences sp=getSharedPreferences("ra_radar",MODE_PRIVATE);
            String s=sp.getString("captured","[]");
            sp.edit().putString("captured","[]").apply();
            return s;
        }

        @JavascriptInterface public void saveSecure(String key, String value) {
            try {
                SharedPreferences sp=getSharedPreferences("ra_secure",MODE_PRIVATE);
                sp.edit().putString(key,encryptSecure(value==null?"":value)).apply();
            } catch (Exception e) { toast("보안정보 저장 실패"); }
        }

        @JavascriptInterface public String getSecure(String key) {
            try {
                String v=getSharedPreferences("ra_secure",MODE_PRIVATE).getString(key,"");
                return decryptSecure(v);
            } catch (Exception e) { return ""; }
        }

        @JavascriptInterface public void deleteSecure(String key) {
            getSharedPreferences("ra_secure",MODE_PRIVATE).edit().remove(key).apply();
        }

        @JavascriptInterface public void saveText(String filename, String content) {
            runOnUiThread(() -> {
                try {
                    ContentValues v=new ContentValues();
                    v.put(MediaStore.Downloads.DISPLAY_NAME,filename);
                    v.put(MediaStore.Downloads.MIME_TYPE,"application/json");
                    Uri u=getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI,v);
                    if(u==null)throw new Exception("insert failed");
                    try(OutputStream os=getContentResolver().openOutputStream(u)){
                        os.write(content.getBytes(StandardCharsets.UTF_8));
                    }
                    toast("다운로드 폴더에 저장했습니다.");
                } catch(Exception e){ toast("저장 실패"); }
            });
        }
    }

    @Override public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }
}
