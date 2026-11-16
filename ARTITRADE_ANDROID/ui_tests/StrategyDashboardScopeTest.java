package com.artitrade.mobile;

public final class StrategyDashboardScopeTest {
    private static void check(boolean yes,String msg){if(!yes)throw new AssertionError(msg);}
    public static void main(String[] args){
        check(StrategyDashboardScope.trend("stock","at","all").equals("stock/at/all"),"AT dashboard trend scoped");
        check(StrategyDashboardScope.trend("stock","ma","5m").equals("stock/ma/5m"),"stock MA trend scoped");
        check(StrategyDashboardScope.trend("stock","gmo","all").equals("stock/gmo/all"),"GMO only");
        check(StrategyDashboardScope.trend("stock","close","all").equals("stock/close/all"),"CLOSE only");
        check(StrategyDashboardScope.trend("coin","general","30m").equals("coin/general/30m"),"coin base");
        check(StrategyDashboardScope.trend("coin","ma","30m").equals("coin/ma/30m"),"coin MA");
        check(!StrategyDashboardScope.trend("stock","at","5m").equals(StrategyDashboardScope.trend("stock","ma","5m")),"no cross-strategy mixing");
        check(StrategyDashboardScope.showFrameCharts("at"),"AT frame charts");
        check(StrategyDashboardScope.showFrameCharts("ma"),"MA frame charts");
        check(!StrategyDashboardScope.showFrameCharts("gmo"),"GMO no frame charts");
        check(!StrategyDashboardScope.showFrameCharts("close"),"CLOSE no frame charts");
        check(StrategyDashboardScope.history("stock","at","positions","current").equals("stock/at/positions/all"),"AT default only");
        check(StrategyDashboardScope.history("stock","at","research","current").equals("stock/at/research/all"),"AT research only");
        check(StrategyDashboardScope.history("stock","ma","pending","current").equals("stock/ma/pending/all"),"MA pending only");
        check(StrategyDashboardScope.history("coin","ma","executions","current").equals("coin/ma/executions/all"),"coin MA fills only");
        check(StrategyDashboardScope.history("stock","at","positions","all").equals("stock/all/positions/all"),"all explicitly aggregates");
        check(StrategyDashboardScope.history("stock","at","positions","gmo").equals("stock/gmo/positions/all"),"explicit GMO");
        check(StrategyDashboardScope.history("stock","ma","executions","5m").equals("stock/ma/executions/5m"),"MA 5m excludes AT");
        System.out.println("STRATEGY_DASHBOARD_SCOPE_TEST_PASS");
    }
}
