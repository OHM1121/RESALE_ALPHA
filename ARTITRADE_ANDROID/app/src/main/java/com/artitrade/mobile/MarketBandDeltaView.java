package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

/** Centered signed movement bar: +6pp is not a +6% price return. */
final class MarketBandDeltaView extends View {
    private final Paint brush=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Double delta;
    MarketBandDeltaView(Context c,Double value){super(c);delta=value;}
    private int dp(float v){return (int)(v*getResources().getDisplayMetrics().density+.5f);}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float cx=getWidth()/2f,w=Math.max(dp(7),cx-dp(8));
        float cy=getHeight()/2f,h=dp(11);
        brush.setColor(Color.rgb(39,44,58));
        c.drawRoundRect(new RectF(cx-w,cy-h/2,cx+w,cy+h/2),dp(3),dp(3),brush);
        brush.setColor(Color.rgb(125,133,151));
        c.drawRect(cx-dp(.7f),cy-h/2-dp(2),cx+dp(.7f),cy+h/2+dp(2),brush);
        if(delta==null||!Double.isFinite(delta))return;
        float v=(float)Math.min(1.0,Math.abs(delta)/10.0);
        float reach=w*v;
        brush.setColor(delta>=0?Color.rgb(85,212,169):Color.rgb(244,111,129));
        c.drawRoundRect(new RectF(
            delta>=0?cx:cx-reach,cy-h/2,
            delta>=0?cx+reach:cx,cy+h/2),dp(3),dp(3),brush);
    }
}
