package com.artitrade.mobile;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import android.util.Base64;

/** Broker credential diagnostics. Never returns or logs raw credentials/tokens. */
final class BrokerApiProbe {
    private static byte[] read(InputStream in)throws Exception{
        if(in==null)return new byte[0];
        ByteArrayOutputStream out=new ByteArrayOutputStream();
        byte[] b=new byte[4096];int n;
        while((n=in.read(b))!=-1){
            if(out.size()+n>262144)throw new IllegalStateException("broker response too large");
            out.write(b,0,n);
        }
        return out.toByteArray();
    }
    private static JSONObject response(HttpURLConnection c)throws Exception{
        int code=c.getResponseCode();
        InputStream in=(code>=200&&code<300)?c.getInputStream():c.getErrorStream();
        String raw=new String(read(in),StandardCharsets.UTF_8);
        JSONObject o;
        try{o=raw.trim().startsWith("[")?new JSONObject().put("array",new JSONArray(raw)):new JSONObject(raw);}
        catch(Exception e){o=new JSONObject().put("raw",raw.length()>500?raw.substring(0,500):raw);}
        o.put("http_status",code);
        return o;
    }
    static JSONObject kiwoom(BrokerSecretStore store){
        JSONObject out=new JSONObject();
        try{
            String key=store.kiwoomKey(),secret=store.kiwoomSecret();
            if(key.isEmpty()||secret.isEmpty())return out.put("ok",false).put("stage","storage").put("message","키움 App Key/App Secret 저장값 없음");
            JSONObject body=new JSONObject().put("grant_type","client_credentials").put("appkey",key).put("secretkey",secret);
            byte[] bytes=body.toString().getBytes(StandardCharsets.UTF_8);
            HttpURLConnection c=(HttpURLConnection)new URL("https://api.kiwoom.com/oauth2/token").openConnection();
            c.setConnectTimeout(5000);c.setReadTimeout(7000);c.setRequestMethod("POST");c.setDoOutput(true);
            c.setRequestProperty("Content-Type","application/json;charset=UTF-8");c.setRequestProperty("Accept","application/json");
            c.setFixedLengthStreamingMode(bytes.length);
            try(OutputStream os=c.getOutputStream()){os.write(bytes);}
            JSONObject r=response(c);c.disconnect();
            boolean ok=r.optInt("http_status",0)==200&&r.optInt("return_code",-1)==0&&!r.optString("token","").isEmpty();
            String msg=ok?"접근토큰 발급 정상":r.optString("return_msg",r.optString("message",r.optString("raw","키움 인증 실패")));
            return out.put("ok",ok).put("stage","oauth").put("http_status",r.optInt("http_status",0))
                    .put("expires_dt",r.optString("expires_dt","")).put("message",msg);
        }catch(Throwable e){
            try{return out.put("ok",false).put("stage","network").put("message",e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()));}
            catch(Exception ignored){return new JSONObject();}
        }
    }
    private static String b64(byte[] raw){return Base64.encodeToString(raw,Base64.URL_SAFE|Base64.NO_WRAP|Base64.NO_PADDING);}
    private static String jwt(String access,String secret)throws Exception{
        String h=b64("{\"alg\":\"HS256\",\"typ\":\"JWT\"}".getBytes(StandardCharsets.UTF_8));
        String p=b64(new JSONObject().put("access_key",access).put("nonce",UUID.randomUUID().toString()).toString().getBytes(StandardCharsets.UTF_8));
        String msg=h+"."+p;
        Mac mac=Mac.getInstance("HmacSHA256");mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8),"HmacSHA256"));
        return msg+"."+b64(mac.doFinal(msg.getBytes(StandardCharsets.US_ASCII)));
    }
    static JSONObject upbit(BrokerSecretStore store){
        JSONObject out=new JSONObject();
        try{
            String access=store.upbitAccess(),secret=store.upbitSecret();
            if(access.isEmpty()||secret.isEmpty())return out.put("ok",false).put("stage","storage").put("message","업비트 Access/Secret 저장값 없음");
            HttpURLConnection c=(HttpURLConnection)new URL("https://api.upbit.com/v1/accounts").openConnection();
            c.setConnectTimeout(5000);c.setReadTimeout(7000);c.setRequestMethod("GET");
            c.setRequestProperty("Authorization","Bearer "+jwt(access,secret));c.setRequestProperty("Accept","application/json");
            JSONObject r=response(c);c.disconnect();int code=r.optInt("http_status",0);
            boolean ok=code==200&&r.has("array");
            String name="",msg="";
            JSONObject err=r.optJSONObject("error");
            if(err!=null){name=err.optString("name","");msg=err.optString("message","");}
            if(msg.isEmpty())msg=ok?"인증 REST 정상 · Private WS 사용 가능 여부 확인 준비":r.optString("raw","업비트 인증 실패");
            boolean ip=(name.toLowerCase().contains("ip")||msg.toLowerCase().contains("ip")||code==403);
            return out.put("ok",ok).put("stage","auth").put("http_status",code).put("error_name",name)
                    .put("ip_hint",ip).put("message",msg);
        }catch(Throwable e){
            try{return out.put("ok",false).put("stage","network").put("message",e.getClass().getSimpleName()+": "+String.valueOf(e.getMessage()));}
            catch(Exception ignored){return new JSONObject();}
        }
    }
    static JSONObject all(BrokerSecretStore store){
        try{return new JSONObject().put("kiwoom",kiwoom(store)).put("upbit",upbit(store)).put("checked_at",System.currentTimeMillis());}
        catch(Exception e){return new JSONObject();}
    }
}
