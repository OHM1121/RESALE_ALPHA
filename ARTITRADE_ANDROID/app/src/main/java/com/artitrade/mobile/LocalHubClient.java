package com.artitrade.mobile;

import android.content.Context;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/** Local client for Stock/Coin APKs accessing the separate Phone Hub application. */
final class LocalHubClient {
    private static final String BASE="http://127.0.0.1:8765";
    private final Context context;
    private final SecureBox crypt;
    LocalHubClient(Context ctx){context=ctx.getApplicationContext();crypt=new SecureBox(context);}
    void storePairingToken(String raw){
        if(raw==null || !raw.matches("[0-9a-fA-F]{64}")) throw new IllegalArgumentException("Invalid pairing token");
        if(!context.getSharedPreferences("hub_pairing",Context.MODE_PRIVATE).edit()
                .putString("token",crypt.seal(raw.toLowerCase(java.util.Locale.ROOT))).commit())
            throw new IllegalStateException("Cannot save phone server pairing");
    }
    String savedPairingToken(){
        String sealed=context.getSharedPreferences("hub_pairing",Context.MODE_PRIVATE).getString("token",null);
        return sealed==null?"":crypt.unseal(sealed);
    }
    private static String encode(String s){
        try{return URLEncoder.encode(s,"UTF-8");}catch(Exception e){throw new IllegalArgumentException(e);}
    }
    JSONObject request(String path) throws Exception {
        HttpURLConnection c=(HttpURLConnection)new URL(BASE+path).openConnection();
        c.setConnectTimeout(4000);c.setReadTimeout(4000);
        c.setRequestMethod("GET");
        c.setInstanceFollowRedirects(false);
        c.setRequestProperty("Authorization","Bearer "+savedPairingToken());
        c.setRequestProperty("Accept","application/json");
        try{
            int code=c.getResponseCode();
            InputStream in=(code>=200 && code<300)?c.getInputStream():c.getErrorStream();
            if(in==null)throw new IllegalStateException("Empty server response");
            ByteArrayOutputStream out=new ByteArrayOutputStream();
            byte[] buffer=new byte[4096];int n;
            while((n=in.read(buffer))!=-1){
                if(out.size()+n>524288)throw new IllegalStateException("Response too large");
                out.write(buffer,0,n);
            }
            JSONObject body=new JSONObject(new String(out.toByteArray(),StandardCharsets.UTF_8));
            if(code!=200)body.put("http_status",code);
            return body;
        }finally{c.disconnect();}
    }
    JSONObject status() throws Exception {return request("/v1/status");}
    JSONObject record(String market,String strategy,String bucket)throws Exception {
        return request("/v1/record?market="+encode(market)+"&strategy="+encode(strategy)+"&bucket="+encode(bucket));
    }
}
