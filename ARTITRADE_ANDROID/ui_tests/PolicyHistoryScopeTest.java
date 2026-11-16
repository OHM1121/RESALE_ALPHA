package com.artitrade.mobile;

public final class PolicyHistoryScopeTest {
    private static void check(boolean b,String s){if(!b)throw new AssertionError(s);}
    public static void main(String[] args){
        check(PolicyHistoryScope.matches("stock","at","005930",
            "stock","at","005930"),"exact target");
        check(!PolicyHistoryScope.matches("stock","at","005930",
            "stock","ma","005930"),"different strategy");
        check(!PolicyHistoryScope.matches("stock","at","005930",
            "stock","at","000660"),"different stock");
        check(!PolicyHistoryScope.matches("coin","general","KRW-BTC",
            "coin","general","KRW-ETH"),"different crypto");
        check(!PolicyHistoryScope.matches("stock","at","005930",
            "coin","at","005930"),"different market");
        check(PolicyHistoryScope.matches("coin","general","krw-btc",
            "coin","general","KRW-BTC"),"normalize market");
        check(!PolicyHistoryScope.matches("stock","at","",
            "stock","at",""),"never match unknown codes");
        System.out.println("POLICY_HISTORY_SCOPE_PASS");
    }
}
