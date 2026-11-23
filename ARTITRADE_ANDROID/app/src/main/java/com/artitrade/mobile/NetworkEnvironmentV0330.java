package com.artitrade.mobile;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.PowerManager;

/**
 * 0.3.3 (issue #148): read-only Android network-environment probe for the error history.
 *
 * Evidence (error export 2026-11-23 10:31): most Upbit/Kiwoom disconnects were DNS resolution
 * failures ("Failed to resolve" / NameResolutionError) for BOTH brokers at the same moments, i.e.
 * the phone's network or a device policy, not one broker. The export had no way to tell WHICH
 * device setting was involved. This probe records, only when it changes: battery-optimisation
 * exemption, background-data restriction, active transport, validated internet and private-DNS
 * state. It never makes a request, never stores an address, a host name or an SSID.
 */
final class NetworkEnvironmentV0330 {
    private NetworkEnvironmentV0330(){}

    /** Compact one-line description, e.g. "battery=unrestricted bg=allowed net=wifi validated=1 ...". */
    static String describe(Context c){
        StringBuilder sb=new StringBuilder(160);
        try{
            PowerManager pm=(PowerManager)c.getSystemService(Context.POWER_SERVICE);
            boolean exempt=pm!=null&&pm.isIgnoringBatteryOptimizations(c.getPackageName());
            sb.append("battery=").append(exempt?"unrestricted":"OPTIMIZED");
        }catch(Throwable e){sb.append("battery=unknown");}
        ConnectivityManager cm=null;
        try{cm=(ConnectivityManager)c.getSystemService(Context.CONNECTIVITY_SERVICE);}catch(Throwable ignored){}
        try{
            int bg=cm==null?-1:cm.getRestrictBackgroundStatus();
            sb.append(" bg=").append(bg==ConnectivityManager.RESTRICT_BACKGROUND_STATUS_ENABLED?"RESTRICTED"
                :bg==ConnectivityManager.RESTRICT_BACKGROUND_STATUS_WHITELISTED?"saver_exempt"
                :bg==ConnectivityManager.RESTRICT_BACKGROUND_STATUS_DISABLED?"allowed":"unknown");
        }catch(Throwable e){sb.append(" bg=unknown");}
        try{
            Network active=cm==null?null:cm.getActiveNetwork();
            if(active==null){
                sb.append(" net=NONE");
            }else{
                NetworkCapabilities caps=cm.getNetworkCapabilities(active);
                String transport="other";
                if(caps!=null){
                    if(caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN))transport="vpn";
                    else if(caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI))transport="wifi";
                    else if(caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR))transport="cellular";
                    else if(caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))transport="ethernet";
                }
                sb.append(" net=").append(transport);
                sb.append(" validated=").append(caps!=null
                    &&caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)?1:0);
                LinkProperties lp=cm.getLinkProperties(active);
                if(lp!=null){
                    sb.append(" private_dns=").append(lp.isPrivateDnsActive()
                        ?(lp.getPrivateDnsServerName()!=null?"hostname":"opportunistic"):"off");
                    sb.append(" dns_servers=").append(lp.getDnsServers().size());
                }
            }
        }catch(Throwable e){sb.append(" net=unknown");}
        return sb.toString();
    }

    /** Diagnostic entry whose "stack" text is the description (RuntimeCrashStore prints it verbatim). */
    static final class Report extends IllegalStateException{
        private final String body;
        Report(String message,String body){super(message);this.body=body;}
        @Override public void printStackTrace(java.io.PrintWriter writer){writer.print(body);}
    }
}
