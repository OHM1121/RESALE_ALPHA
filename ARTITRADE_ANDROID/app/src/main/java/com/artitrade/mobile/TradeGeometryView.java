package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

/**
 * A geometric entry-to-mark / entry-to-exit comparison, not an icon or a
 * fabricated price series. Zero is the fixed center; measured profit extends
 * right and measured loss left. Missing cost/mark draws no performance claim.
 */
final class TradeGeometryView extends View {
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    private double returnPct;
    private boolean known;

    TradeGeometryView(Context context,double percent,boolean hasRate){
        super(context);
        returnPct=percent;
        known=hasRate&&!Double.isNaN(percent)&&!Double.isInfinite(percent);
        setLayerType(View.LAYER_TYPE_SOFTWARE,null);
    }

    private int px(float value){
        return Math.max(1,(int)(getResources().getDisplayMetrics().density*value+.5f));
    }

    @Override protected void onDraw(Canvas canvas){
        super.onDraw(canvas);
        float w=getWidth(),h=getHeight();
        if(w<=0||h<=0)return;
        float left=px(10),right=w-px(10),center=(left+right)*.5f,mid=h*.52f;
        float track=px(5);
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(Color.rgb(40,53,70));
        canvas.drawRoundRect(new RectF(left,mid-track/2,right,mid+track/2),
            track/2,track/2,paint);
        // Symmetric opportunity bounds are geometry, not a time series.
        paint.setStrokeWidth(px(1));paint.setColor(Color.rgb(96,120,145));
        canvas.drawLine(center,px(3),center,h-px(3),paint);
        for(int i=-2;i<=2;i++){
            if(i==0)continue;
            float x=center+(right-left)*i/5f;
            paint.setColor(Color.rgb(62,76,95));
            canvas.drawLine(x,mid-px(3),x,mid+px(3),paint);
        }
        if(!known)return;
        boolean up=returnPct>=0;
        float reach=(float)Math.min(.5,Math.abs(returnPct)/20.0);
        float endpoint=center+(up?1:-1)*reach*(right-left);
        if(Math.abs(endpoint-center)<px(3))endpoint=center+(up?1:-1)*px(3);
        paint.setColor(up?Color.rgb(54,208,166):Color.rgb(250,109,126));
        float start=Math.min(center,endpoint),end=Math.max(center,endpoint);
        canvas.drawRoundRect(new RectF(start,mid-px(5),end,mid+px(5)),
            px(5),px(5),paint);
        paint.setColor(Color.rgb(230,240,251));
        canvas.drawCircle(endpoint,mid,px(3.1f),paint);
    }
}
