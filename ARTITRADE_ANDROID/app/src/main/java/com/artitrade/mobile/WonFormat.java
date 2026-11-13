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
    static String signed(double amount){
        if(Double.isNaN(amount)||Double.isInfinite(amount))return "—";
        return amount>0?"+"+won(amount):won(amount);
    }
}
