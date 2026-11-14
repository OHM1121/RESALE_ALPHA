package com.artitrade.mobile;

public final class PcLiveSyncPolicyTest {
    static void check(boolean ok,String what){if(!ok)throw new AssertionError(what);}
    public static void main(String[] args) {
        check(!PcLiveSyncPolicy.shouldPublish(false,false,5000L,0L),"default OFF");
        check(PcLiveSyncPolicy.shouldPublish(true,false,5000L,0L),"first ON push");
        check(PcLiveSyncPolicy.shouldPublish(false,true,5000L,0L),"manual one-shot");
        check(!PcLiveSyncPolicy.shouldPublish(true,false,4000L,2000L),"throttle");
        check(PcLiveSyncPolicy.shouldPublish(true,false,7000L,2000L),"change interval");
        check(!PcLiveSyncPolicy.shouldPublish(false,false,7000L,2000L),"OFF persists");
        System.out.println("PC_LIVE_SYNC_POLICY_PASS");
    }
}
