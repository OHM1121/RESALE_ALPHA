package com.artitrade.mobile;

/** Explicit market/strategy boundary for all mobile ledger views and P/L charts. */
final class StrategyDashboardScope {
    static final String CURRENT="current";
    static final String ALL="all";
    private StrategyDashboardScope(){}

    static String trend(String market,String strategy,String frame){
        return market+"/"+strategy+"/"+frame;
    }

    static boolean showFrameCharts(String strategy){
        return !"close".equals(strategy)&&!"gmo".equals(strategy);
    }

    static String history(String market,String strategy,String kind,String filter){
        if(filter==null||filter.isEmpty()||CURRENT.equals(filter))
            return market+"/"+strategy+"/"+kind+"/all";
        if(ALL.equals(filter))return market+"/all/"+kind+"/all";
        if("gmo".equals(filter)||"close".equals(filter))
            return market+"/"+filter+"/"+kind+"/all";
        if("at".equals(filter)||"ma".equals(filter)||"general".equals(filter))
            return market+"/"+filter+"/"+kind+"/all";
        String timeframe=HistorySelection.frame(filter);
        return market+"/"+strategy+"/"+kind+"/"+(timeframe.isEmpty()?"all":timeframe);
    }
}
