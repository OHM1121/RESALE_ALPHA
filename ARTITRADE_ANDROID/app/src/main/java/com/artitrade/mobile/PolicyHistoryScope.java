package com.artitrade.mobile;

import java.util.Locale;

/** Stable, exact policy-history filtering by market + strategy + asset key. */
final class PolicyHistoryScope {
    private PolicyHistoryScope(){}

    static String symbol(String raw){
        if(raw==null)return "";
        String candidate=raw.trim().toUpperCase(Locale.ROOT);
        return candidate.equals("NULL")||candidate.equals("NONE")?"":candidate;
    }

    static boolean matches(String wantedMarket,String wantedStrategy,String wantedCode,
                           String rowMarket,String rowStrategy,String rowCode){
        String wanted=symbol(wantedCode), found=symbol(rowCode);
        return !wanted.isEmpty()&&!found.isEmpty()
            &&wanted.equals(found)
            &&symbol(wantedMarket).equals(symbol(rowMarket))
            &&symbol(wantedStrategy).equals(symbol(rowStrategy));
    }
}
