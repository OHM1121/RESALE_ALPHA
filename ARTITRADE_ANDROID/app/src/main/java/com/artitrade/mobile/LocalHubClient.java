package com.artitrade.mobile;

import android.content.Context;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/** Local client for ARTITRADE apps accessing the separate MSERVER (mobile server) application. */
final class LocalHubClient {
    private static final String BASE="http://127.0.0.1:8765";
    private final Context context;
    private final SecureBox crypt;
    LocalHubClient(Context ctx){context=ctx.getApplicationContext();crypt=new SecureBox(context);}
    void storePairingToken(String raw){
        if(raw==null || !raw.matches("[0-9a-fA-F]{64}")) throw new IllegalArgumentException("Invalid pairing token");
        if(!context.getSharedPreferences("hub_pairing",Context.MODE_PRIVATE).edit()
                .putString("token",crypt.seal(raw.toLowerCase(java.util.Locale.ROOT))).commit())
            throw new IllegalStateException("Cannot save MSERVER pairing");
    }
    String savedPairingToken(){
        String sealed=context.getSharedPreferences("hub_pairing",Context.MODE_PRIVATE).getString("token",null);
        return sealed==null?"":crypt.unseal(sealed);
    }
    JSONObject pairingStatus(){
        JSONObject out=new JSONObject();
        android.content.SharedPreferences p=context.getSharedPreferences("hub_pairing",Context.MODE_PRIVATE);
        boolean encrypted=p.contains("token");
        try{
            String token=savedPairingToken();
            String masked=token.isEmpty()?"":("••••"+token.substring(Math.max(0,token.length()-6)));
            return out.put("encrypted_present",encrypted).put("saved",!token.isEmpty())
                .put("decrypt_ok",!encrypted||!token.isEmpty()).put("masked",masked)
                .put("storage","ANDROID_KEYSTORE_LOCAL");
        }catch(Exception e){
            try{return out.put("encrypted_present",encrypted).put("saved",false).put("decrypt_ok",false)
                .put("storage","ANDROID_KEYSTORE_LOCAL");}catch(Exception ignored){return new JSONObject();}
        }
    }
    private static String encode(String s){
        try{return URLEncoder.encode(s,"UTF-8");}catch(Exception e){throw new IllegalArgumentException(e);}
    }
    private JSONObject parse(HttpURLConnection c)throws Exception{
        int status=c.getResponseCode();
        InputStream in=(status>=200&&status<300)?c.getInputStream():c.getErrorStream();
        if(in==null)throw new IllegalStateException("Empty server response");
        ByteArrayOutputStream buffer=new ByteArrayOutputStream();
        byte[] chunk=new byte[4096];int n;
        while((n=in.read(chunk))!=-1){
            if(buffer.size()+n>36*1024*1024)throw new IllegalStateException("MSERVER response too large");
            buffer.write(chunk,0,n);
        }
        JSONObject reply=new JSONObject(new String(buffer.toByteArray(),StandardCharsets.UTF_8));
        if(status!=200)reply.put("http_status",status);
        return reply;
    }
    JSONObject request(String path) throws Exception {
        HttpURLConnection c=(HttpURLConnection)new URL(BASE+path).openConnection();
        c.setConnectTimeout(1800);c.setReadTimeout(2200);
        c.setRequestMethod("GET");
        c.setInstanceFollowRedirects(false);
        c.setRequestProperty("Authorization","Bearer "+savedPairingToken());
        c.setRequestProperty("Accept","application/json");
        c.setRequestProperty("Connection","keep-alive");
        try{return parse(c);}finally{c.disconnect();}
    }
    private JSONObject send(String method,String path,JSONObject payload)throws Exception{
        byte[] body=payload.toString().getBytes(StandardCharsets.UTF_8);
        HttpURLConnection c=(HttpURLConnection)new URL(BASE+path).openConnection();
        c.setConnectTimeout(1800);c.setReadTimeout(2600);
        c.setRequestMethod(method);
        c.setInstanceFollowRedirects(false);
        c.setRequestProperty("Authorization","Bearer "+savedPairingToken());
        c.setRequestProperty("Accept","application/json");
        c.setRequestProperty("Content-Type","application/json; charset=utf-8");
        c.setRequestProperty("Connection","keep-alive");
        c.setFixedLengthStreamingMode(body.length);
        c.setDoOutput(true);
        try{
            try(OutputStream out=c.getOutputStream()){out.write(body);}
            return parse(c);
        }finally{c.disconnect();}
    }
    private JSONObject post(String path,JSONObject payload)throws Exception{return send("POST",path,payload);}
    private JSONObject put(String path,JSONObject payload)throws Exception{return send("PUT",path,payload);}
    JSONObject migrationStatus() throws Exception {return request("/v1/migration/status");}
    JSONObject migrationChunk(String session,int index)throws Exception{
        return request("/v1/migration/chunk?session="+encode(session)+"&index="+index);
    }
    JSONObject migrationAck(String session,String archiveSha)throws Exception{
        return post("/v1/migration/ack",new JSONObject().put("session",session).put("archive_sha256",archiveSha));
    }

    JSONObject control(String market,String desired) throws Exception {
        if(!("stock".equals(market)||"coin".equals(market)))throw new IllegalArgumentException("Unsupported market");
        if(!("pc".equals(desired)||"phone".equals(desired)||"off".equals(desired)))throw new IllegalArgumentException("Unsupported device");
        return post("/v1/control",new JSONObject().put("market",market).put("desired",desired));
    }
    JSONObject claim(String market,String clientId)throws Exception{
        return post("/v1/claim",new JSONObject().put("market",market).put("device","phone").put("client_id",clientId));
    }
    JSONObject renew(String market,String clientId,long epoch)throws Exception{
        return post("/v1/renew",new JSONObject().put("market",market).put("device","phone").put("client_id",clientId).put("epoch",epoch));
    }
    JSONObject release(String market,String clientId,long epoch)throws Exception{
        return post("/v1/release",new JSONObject().put("market",market).put("device","phone").put("client_id",clientId).put("epoch",epoch));
    }
    JSONObject check(String market,String clientId,long epoch)throws Exception{
        return request("/v1/check?market="+encode(market)+"&device=phone&client_id="+encode(clientId)+"&epoch="+epoch);
    }
    JSONObject status() throws Exception {return request("/v1/status");}
    JSONObject record(String market,String strategy,String bucket)throws Exception {
        return request("/v1/record?market="+encode(market)+"&strategy="+encode(strategy)+"&bucket="+encode(bucket));
    }
    JSONObject recordMeta(String market,String strategy,String bucket)throws Exception {
        return request("/v1/record/meta?market="+encode(market)+"&strategy="+encode(strategy)+"&bucket="+encode(bucket));
    }
    JSONObject putRecord(String market,String strategy,String bucket,JSONObject data)throws Exception{
        JSONObject current=recordMeta(market,strategy,bucket);
        int revision=current.optBoolean("ok")&&current.optBoolean("exists")?current.optInt("revision",0):0;
        JSONObject payload=new JSONObject().put("market",market).put("strategy",strategy)
            .put("bucket",bucket).put("expected_revision",revision).put("data",data);
        JSONObject reply=put("/v1/record",payload);
        if(reply.optInt("http_status",200)==409 || "revision_conflict".equals(reply.optString("error"))){
            current=recordMeta(market,strategy,bucket);
            revision=current.optBoolean("ok")&&current.optBoolean("exists")?current.optInt("revision",0):0;
            payload.put("expected_revision",revision);
            reply=put("/v1/record",payload);
        }
        return reply;
    }
}
