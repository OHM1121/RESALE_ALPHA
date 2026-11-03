package com.resalealpha.mobile;

import android.app.Activity;
import android.content.ContentValues;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.provider.MediaStore;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import org.json.JSONArray;

import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public class MainActivity extends Activity {
    private WebView webView;
    private String pendingShare = "";
    private ValueCallback<Uri[]> fileCallback;
    private Uri cameraUri;
    private static final int REQ_FILE = 901;
    private static final String SECURE_ALIAS = "resale_alpha_secure_v1";

    private final Set<String> allowedHosts = new HashSet<>(Arrays.asList(
            "www.daangn.com", "daangn.com",
            "web.joongna.com", "joongna.com",
            "m.bunjang.co.kr", "bunjang.co.kr"
    ));

    @Override public void onCreate(Bundle b) {
        super.onCreate(b);
        webView = new WebView(this);
        setContentView(webView);

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

        handleShare(getIntent());
        webView.loadUrl("file:///android_asset/index.html");
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
        String q = org.json.JSONObject.quote(pendingShare);
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
            if (h == null || !allowedHosts.contains(h.toLowerCase())) {
                Toast.makeText(this, "허용된 중고마켓 링크만 열 수 있습니다.", Toast.LENGTH_SHORT).show();
                return;
            }
            Intent in = new Intent(Intent.ACTION_VIEW, u);
            in.addFlags(Intent.FLAG_ACTIVITY_NEW_DOCUMENT);
            startActivity(in);
        } catch (Exception e) {
            Toast.makeText(this, "링크를 열 수 없습니다.", Toast.LENGTH_SHORT).show();
        }
    }

    public class Bridge {
        @JavascriptInterface public void openUrl(String url) {
            runOnUiThread(() -> openExternal(url));
        }

        @JavascriptInterface public void openAll(String json) {
            runOnUiThread(() -> {
                try {
                    JSONArray a = new JSONArray(json);
                    for (int n = 0; n < a.length(); n++) {
                        final String u = a.getString(n);
                        webView.postDelayed(() -> openExternal(u), n * 300L);
                    }
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "통합 조회 링크 오류", Toast.LENGTH_SHORT).show();
                }
            });
        }

        @JavascriptInterface public String getInitialShare() {
            String t = pendingShare;
            pendingShare = "";
            return t;
        }

        @JavascriptInterface public void saveSecure(String key, String value) {
            try {
                SharedPreferences sp = getSharedPreferences("ra_secure", MODE_PRIVATE);
                sp.edit().putString(key, encryptSecure(value == null ? "" : value)).apply();
            } catch (Exception e) {
                runOnUiThread(() ->
                        Toast.makeText(MainActivity.this, "보안정보 저장 실패", Toast.LENGTH_SHORT).show());
            }
        }

        @JavascriptInterface public String getSecure(String key) {
            try {
                String v = getSharedPreferences("ra_secure", MODE_PRIVATE).getString(key, "");
                return decryptSecure(v);
            } catch (Exception e) {
                return "";
            }
        }

        @JavascriptInterface public void deleteSecure(String key) {
            getSharedPreferences("ra_secure", MODE_PRIVATE).edit().remove(key).apply();
        }

        @JavascriptInterface public void saveText(String filename, String content) {
            runOnUiThread(() -> {
                try {
                    ContentValues v = new ContentValues();
                    v.put(MediaStore.Downloads.DISPLAY_NAME, filename);
                    v.put(MediaStore.Downloads.MIME_TYPE, "application/json");
                    Uri u = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                    if (u == null) throw new Exception("insert failed");
                    try (OutputStream os = getContentResolver().openOutputStream(u)) {
                        os.write(content.getBytes(StandardCharsets.UTF_8));
                    }
                    Toast.makeText(MainActivity.this, "다운로드 폴더에 저장했습니다.", Toast.LENGTH_SHORT).show();
                } catch (Exception e) {
                    Toast.makeText(MainActivity.this, "저장 실패", Toast.LENGTH_SHORT).show();
                }
            });
        }
    }

    @Override public void onBackPressed() {
        if (webView != null && webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }
}
