package com.artitrade.mobile;

/** Separates local ledger collection from optional phone-to-PC publishing. */
final class PcLiveSyncPolicy {
    static final long PUBLISH_INTERVAL_MS=4500L;
    private PcLiveSyncPolicy(){}
    static boolean shouldPublish(boolean enabled,boolean oneShot,long now,long lastPublish){
        return (enabled||oneShot) && (lastPublish<=0L || now-lastPublish>=PUBLISH_INTERVAL_MS);
    }
}
