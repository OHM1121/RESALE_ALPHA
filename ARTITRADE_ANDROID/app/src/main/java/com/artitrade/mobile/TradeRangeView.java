package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

/** Range of observed post-candidate returns, never a trade-profit plot. */
final class TradeRangeView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final double minPct,maxPct;
    TradeRangeView(Context context,double low,double high){
        super(context);
        minPct=low;
        maxPct=high;
    }
    private float dp(float v){
        return v*getResources().getDisplayMetrics().density;
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth(),h=getHeight();
        if(w<=0||h<=0)return;
        float left=dp(12),right=w-dp(12),center=(left+right)/2f;
        float y=h*.54f;
        p.setColor(Color.rgb(51,63,80));
        c.drawRoundRect(new RectF(left,y-dp(2),right,y+dp(2)),dp(2),dp(2),p);
        p.setColor(Color.rgb(114,133,157));
        p.setStrokeWidth(dp(1));
        c.drawLine(center,dp(2),center,h-dp(2),p);
        boolean lo=!Double.isNaN(minPct)&&!Double.isInfinite(minPct);
        boolean hi=!Double.isNaN(maxPct)&&!Double.isInfinite(maxPct);
        if(!lo&&!hi)return;
        float x1=lo?point(minPct,center,right-left):center;
        float x2=hi?point(maxPct,center,right-left):center;
        float low=Math.min(x1,x2),high=Math.max(x1,x2);
        if(low<center){
            p.setColor(Color.rgb(237,112,135));
            c.drawRoundRect(new RectF(low,y-dp(5),center,y+dp(5)),dp(4),dp(4),p);
        }
        if(high>center){
            p.setColor(Color.rgb(64,210,162));
            c.drawRoundRect(new RectF(center,y-dp(5),high,y+dp(5)),dp(4),dp(4),p);
        }
        if(lo){
            p.setColor(Color.rgb(255,177,186));
            c.drawCircle(x1,y,dp(3),p);
        }
        if(hi){
            p.setColor(Color.rgb(154,255,222));
            c.drawCircle(x2,y,dp(3),p);
        }
    }
    private float point(double value,float center,float span){
        double fraction=Math.max(-.5,Math.min(.5,value/40.0));
        return center+(float)(span*fraction);
    }
}
