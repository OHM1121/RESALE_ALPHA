package com.artitrade.mobile;

import java.text.NumberFormat;
import java.util.Locale;

/** Entire KRW amounts; never abbreviate into ten-thousands or hundred-millions. */
final class WonFormat {
    private WonFormat(){}
    static String won(double amount){
        if(Double.isNaN(amount)||Double.isInfinite(amount))return "—";
        NumberFormat f=NumberFormat.getIntegerInstance(Locale.KOREA);
        f.setGroupingUsed(true);
        f.setMaximumFractionDigits(0);
        return f.format(amount)+"원";
    }
    /** A coin/share PRICE is not a whole-KRW cash total (8.2 -> 8.20). */
    static String price(double amount){
        if(Double.isNaN(amount)||Double.isInfinite(amount))return "—";
        if(amount>0 && amount<0.005)return "<0.01원";
        if(amount<0 && amount>-0.005)return ">-0.01원";
        NumberFormat f=NumberFormat.getNumberInstance(Locale.KOREA);
        f.setGroupingUsed(true);
        f.setMaximumFractionDigits(2);
        f.setMinimumFractionDigits(amount==Math.rint(amount)?0:2);
        return f.format(amount)+"원";
    }
    static String signed(double amount){
        if(Double.isNaN(amount)||Double.isInfinite(amount))return "—";
        return amount>0?"+"+won(amount):won(amount);
    }
}
