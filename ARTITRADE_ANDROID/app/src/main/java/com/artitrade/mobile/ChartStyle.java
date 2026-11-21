package com.artitrade.mobile;

import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Shader;

/**
 * Shared dark-mode chart look. Presentation only: no data, ledger or order logic.
 * Gradients are a single cheap shader per shape and are created only when a view
 * is invalidated (charts are static between data refreshes), so scrolling and the
 * trading engine are not slowed down. No blur/shadow layers (software layers) are used.
 */
final class ChartStyle {
    static final int UP=Color.rgb(62,224,181);
    static final int DOWN=Color.rgb(255,119,133);
    static final int WARN=Color.rgb(255,201,94);
    static final int TRACK=Color.rgb(26,30,38);

    private ChartStyle(){}

    static int withAlpha(int color,int alpha){
        return Color.argb(alpha,Color.red(color),Color.green(color),Color.blue(color));
    }
    static int lighten(int color,float amount){
        int r=Color.red(color),g=Color.green(color),b=Color.blue(color);
        return Color.rgb(r+(int)((255-r)*amount),g+(int)((255-g)*amount),b+(int)((255-b)*amount));
    }
    static int darken(int color,float amount){
        return Color.rgb((int)(Color.red(color)*(1f-amount)),(int)(Color.green(color)*(1f-amount)),
            (int)(Color.blue(color)*(1f-amount)));
    }
    /** Horizontal bar fill: slightly deeper at the origin, brighter at the tip. */
    static void barPaint(Paint p,RectF bar,int color,boolean growsRight){
        int deep=darken(color,.30f),bright=lighten(color,.18f);
        float x0=growsRight?bar.left:bar.right,x1=growsRight?bar.right:bar.left;
        if(Math.abs(x1-x0)<1f){p.setShader(null);p.setColor(color);return;}
        p.setShader(new LinearGradient(x0,0,x1,0,deep,bright,Shader.TileMode.CLAMP));
    }
    /** Vertical fill used for area charts: translucent at the line, transparent at the baseline. */
    static void areaPaint(Paint p,float top,float bottom,int color){
        p.setShader(new LinearGradient(0,top,0,bottom,withAlpha(color,92),withAlpha(color,0),
            Shader.TileMode.CLAMP));
    }
    /** Area below the zero line: transparent at the zero line, strongest at the curve (mirror of areaPaint). */
    static void areaPaintDown(Paint p,float zero,float bottom,int color){
        p.setShader(new LinearGradient(0,zero,0,bottom,withAlpha(color,0),withAlpha(color,92),
            Shader.TileMode.CLAMP));
    }
    static void clear(Paint p){p.setShader(null);}
    /** Text that never runs past maxWidth: cut with an ellipsis instead of overlapping its neighbour. */
    static String ellipsize(Paint p,String text,float maxWidth){
        if(text==null||text.isEmpty()||maxWidth<=0)return "";
        if(p.measureText(text)<=maxWidth)return text;
        String dots="…";
        int end=text.length();
        while(end>0&&p.measureText(text.substring(0,end)+dots)>maxWidth)end--;
        return end<=0?"":text.substring(0,end)+dots;
    }
    /** Threshold colour shared by every 0..100 gauge (60+ good, 40+ caution). */
    static int zone(double value){
        return value>=60?UP:value>=40?WARN:DOWN;
    }
}
