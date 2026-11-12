package com.artitrade.mobile;

import android.util.Log;
import org.json.JSONObject;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;

/** Private HTTP sync transport.
 *  Binds only to phone loopback and (if explicitly enabled) a Tailscale 100.64/10
 *  address on a VPN/tun interface, never Wi-Fi or public cellular interfaces.
 *  PC traffic MUST use Tailscale or another encrypted VPN. Never port-forward.
 */
final class HubServer {
    private static final int PORT=8765, MAX_BODY=600000;
    private static final String TAG="ArtitradeHub";
    private final SecureBox secrets;
    private final SyncStore store;
    private final ExecutorService clients=Executors.newFixedThreadPool(4);
    private volatile boolean running=false;
    private volatile boolean tailnetEnabled=false;
    private volatile ServerSocket loopSocket, vpnSocket;
    private volatile String vpnBoundIp="";
    private Thread loopThread, vpnThread;

    HubServer(SecureBox secrets, SyncStore store){this.secrets=secrets;this.store=store;}
    boolean isRunning(){return running;}
    String tailnetIp(){return vpnBoundIp;}
    void setTailnetEnabled(boolean value){
        tailnetEnabled=value;
        if(!value){close(vpnSocket);vpnBoundIp="";}
    }
    synchronized void start(boolean allowTailnet){
        if(running)return;
        running=true;tailnetEnabled=allowTailnet;
        loopThread=new Thread(()->listenLoopback(),"hub-loopback");
        vpnThread=new Thread(()->monitorVpn(),"hub-vpn");
        loopThread.start();vpnThread.start();
    }
    synchronized void stop(){
        running=false;
        close(loopSocket);close(vpnSocket);
        vpnBoundIp="";
        clients.shutdownNow();
    }
    private void listenLoopback(){
        try(ServerSocket ss=new ServerSocket(PORT,16,InetAddress.getByName("127.0.0.1"))){
            loopSocket=ss;
            serve(ss);
        }catch(Exception e){if(running)Log.e(TAG,"Loopback bind failed",e);}
    }
    private void monitorVpn(){
        while(running){
            if(!tailnetEnabled){pause();continue;}
            InetAddress target=findTailnetAddress();
            if(target==null){pause();continue;}
            try(ServerSocket ss=new ServerSocket(PORT,16,target)){
                vpnSocket=ss;vpnBoundIp=target.getHostAddress();
                Log.i(TAG,"Private VPN endpoint enabled");
                ss.setSoTimeout(10000);
                while(running && tailnetEnabled && !ss.isClosed()){
                    if(findTailnetAddress()==null)break;
                    try{Socket client=ss.accept();enqueue(client);}
                    catch(SocketTimeoutException ignored){}
                }
            }catch(Exception e){if(running&&tailnetEnabled)Log.w(TAG,"VPN bind unavailable");}
            finally{close(vpnSocket);vpnSocket=null;vpnBoundIp="";}
            pause();
        }
    }
    private static InetAddress findTailnetAddress(){
        try{
            Enumeration<NetworkInterface> interfaces=NetworkInterface.getNetworkInterfaces();
            while(interfaces.hasMoreElements()){
                NetworkInterface iface=interfaces.nextElement();
                String name=iface.getName().toLowerCase(Locale.ROOT);
                if(!iface.isUp() || !(name.startsWith("tun")||name.contains("tailscale")))continue;
                Enumeration<InetAddress> addresses=iface.getInetAddresses();
                while(addresses.hasMoreElements()){
                    InetAddress addr=addresses.nextElement();
                    if(!(addr instanceof Inet4Address))continue;
                    byte[] b=addr.getAddress();
                    if((b[0]&255)==100 && ((b[1]&255)>=64) && ((b[1]&255)<=127))
                        return addr;
                }
            }
        }catch(Exception ignored){}
        return null;
    }
    private static void pause(){
        try{Thread.sleep(5000);}catch(InterruptedException e){Thread.currentThread().interrupt();}
    }
    private static void close(ServerSocket socket){
        if(socket!=null)try{socket.close();}catch(Exception ignored){}
    }
    private void serve(ServerSocket server) throws IOException{
        while(running){
            try{Socket socket=server.accept();enqueue(socket);}
            catch(SocketException e){if(running)throw e;break;}
        }
    }
    private void enqueue(Socket socket){
        try{clients.execute(()->handle(socket));}
        catch(RejectedExecutionException e){try{socket.close();}catch(Exception ignored){}}
    }
    private static String readLine(InputStream in) throws IOException{
        ByteArrayOutputStream b=new ByteArrayOutputStream();
        for(int i=0;i<8192;i++){
            int c=in.read();
            if(c==-1)throw new EOFException();
            if(c==10)break;
            if(c!=13)b.write(c);
            if(i==8191)throw new IOException("Line too long");
        }
        return b.toString("UTF-8");
    }
    private static byte[] readBody(InputStream in,int length)throws IOException{
        byte[] body=new byte[length];int offset=0;
        while(offset<length){
            int n=in.read(body,offset,length-offset);
            if(n<0)throw new EOFException();
            offset+=n;
        }
        return body;
    }
    private void send(OutputStream out,int status,JSONObject data)throws IOException{
        byte[] body=data.toString().getBytes(StandardCharsets.UTF_8);
        String reason=status==200?"OK":status==400?"Bad Request":status==401?"Unauthorized":
            status==404?"Not Found":status==405?"Method Not Allowed":
            status==409?"Conflict":status==413?"Payload Too Large":
            status==423?"Locked":"Internal Server Error";
        String header="HTTP/1.1 "+status+" "+reason+"\r\n"
            +"Content-Type: application/json; charset=utf-8\r\n"
            +"Cache-Control: no-store\r\n"
            +"X-Content-Type-Options: nosniff\r\n"
            +"Connection: close\r\n"
            +"Content-Length: "+body.length+"\r\n\r\n";
        out.write(header.getBytes(StandardCharsets.US_ASCII));
        out.write(body);out.flush();
    }
    private static JSONObject json(Object... pairs){
        JSONObject result=new JSONObject();
        try{for(int i=0;i<pairs.length;i+=2)result.put((String)pairs[i],pairs[i+1]);}
        catch(Exception e){throw new IllegalStateException(e);}
        return result;
    }
    private static Map<String,String> query(String raw)throws Exception{
        Map<String,String> map=new HashMap<>();
        if(raw==null || raw.isEmpty())return map;
        for(String part:raw.split("&")){
            int equals=part.indexOf('=');
            if(equals<=0)continue;
            String key=URLDecoder.decode(part.substring(0,equals),"UTF-8");
            String value=URLDecoder.decode(part.substring(equals+1),"UTF-8");
            if(map.containsKey(key))throw new IllegalArgumentException("Duplicate parameter");
            map.put(key,value);
        }
        return map;
    }
    private static String val(Map<String,String> params,String key){
        return params.getOrDefault(key,"");
    }
    private void handle(Socket socket){
        try(Socket client=socket){
            client.setSoTimeout(7000);
            InputStream in=new BufferedInputStream(client.getInputStream());
            OutputStream out=client.getOutputStream();
            String request=readLine(in);
            String[] line=request.split(" ");
            if(line.length!=3 || !line[2].startsWith("HTTP/1.")){
                send(out,400,json("ok",false,"error","bad_request"));return;
            }
            String method=line[0],target=line[1];
            if(target.length()>2000 || !(method.equals("GET")||method.equals("PUT")||method.equals("POST"))){
                send(out,405,json("ok",false,"error","method_not_allowed"));return;
            }
            Map<String,String> headers=new HashMap<>();
            for(int i=0;i<50;i++){
                String header=readLine(in);
                if(header.isEmpty())break;
                int p=header.indexOf(':');
                if(p<=0){send(out,400,json("ok",false,"error","bad_header"));return;}
                String key=header.substring(0,p).trim().toLowerCase(Locale.ROOT);
                if(headers.containsKey(key)){send(out,400,json("ok",false,"error","duplicate_header"));return;}
                headers.put(key,header.substring(p+1).trim());
                if(i==49){send(out,400,json("ok",false,"error","too_many_headers"));return;}
            }
            if(headers.containsKey("transfer-encoding")){
                send(out,400,json("ok",false,"error","chunked_not_supported"));return;
            }
            URI uri;
            try{uri=new URI(target);}catch(Exception e){send(out,400,json("ok",false,"error","bad_uri"));return;}
            String path=uri.getPath();
            if("/health".equals(path) && method.equals("GET")){
                send(out,200,json("ok",true,"name","ARTITRADE phone hub","api",1));return;
            }
            String auth=headers.getOrDefault("authorization","");
            if(!auth.startsWith("Bearer ") || !secrets.compareToken(auth.substring(7))){
                send(out,401,json("ok",false,"error","unauthorized"));return;
            }
            int length=0;
            if(method.equals("POST")||method.equals("PUT")){
                try{length=Integer.parseInt(headers.getOrDefault("content-length","-1"));}
                catch(NumberFormatException e){length=-1;}
                if(length<0){send(out,400,json("ok",false,"error","content_length_required"));return;}
                if(length>MAX_BODY){send(out,413,json("ok",false,"error","too_large"));return;}
                String type=headers.getOrDefault("content-type","");
                if(!type.startsWith("application/json")){
                    send(out,400,json("ok",false,"error","json_content_required"));return;
                }
            }
            JSONObject body;
            try{body=length>0?new JSONObject(new String(readBody(in,length),StandardCharsets.UTF_8)):new JSONObject();}
            catch(Exception e){send(out,400,json("ok",false,"error","invalid_json"));return;}
            JSONObject result=route(method,path,query(uri.getRawQuery()),body);
            String error=result.optString("error","");
            int status="revision_conflict".equals(error)?409:
                    "previous_device_still_active".equals(error)||"manual_order_review_required".equals(error)?423:
                    error.isEmpty()?200:"device_is_read_only".equals(error)||"switch_requested_stop_orders".equals(error)||"lease_invalid_or_expired".equals(error)?409:400;
            send(out,status,result);
        }catch(Exception e){Log.w(TAG,"Request aborted (no credentials logged)");}
    }
    private JSONObject route(String method,String path,Map<String,String> q,JSONObject b){
        if("GET".equals(method)&&"/v1/status".equals(path)){
            return json("ok",true,"api",1,"owner","phone","stock",store.owner("stock"),"coin",store.owner("coin"),"vpn_endpoint",vpnBoundIp);
        }
        if("GET".equals(method)&&"/v1/record".equals(path)){
            return store.getRecord(val(q,"market"),val(q,"strategy"),val(q,"bucket"));
        }
        if("PUT".equals(method)&&"/v1/record".equals(path)){
            JSONObject data=b.optJSONObject("data");
            return store.putRecord(b.optString("market"),b.optString("strategy"),
                b.optString("bucket"),b.optInt("expected_revision",-1),data);
        }
        if("GET".equals(method)&&"/v1/control".equals(path)){
            return store.owner(val(q,"market"));
        }
        if("POST".equals(method)&&"/v1/control".equals(path)){
            return store.prefer(b.optString("market"),b.optString("desired"));
        }
        if("POST".equals(method)&&"/v1/claim".equals(path)){
            return store.claim(b.optString("market"),b.optString("device"),b.optString("client_id"));
        }
        if("POST".equals(method)&&"/v1/renew".equals(path)){
            return store.renew(b.optString("market"),b.optString("device"),b.optString("client_id"),b.optLong("epoch"),false);
        }
        if("POST".equals(method)&&"/v1/release".equals(path)){
            return store.renew(b.optString("market"),b.optString("device"),b.optString("client_id"),b.optLong("epoch"),true);
        }
        if("GET".equals(method)&&"/v1/check".equals(path)){
            long epoch=0;
            try{epoch=Long.parseLong(val(q,"epoch"));}catch(Exception ignored){}
            return store.check(val(q,"market"),val(q,"device"),val(q,"client_id"),epoch);
        }
        return json("ok",false,"error","unknown_endpoint");
    }
}
