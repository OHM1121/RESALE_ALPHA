package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.Shader;
import android.view.View;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Presentation-only visuals for the integrated dashboard and integrated holdings.
 * They draw numbers the activity already computed; they never read or write a
 * ledger, quote, order or research value.
 */
final class IntegratedPalette {
    /** One stable accent per strategy so a colour means the same strategy everywhere. */
    static int strategy(String market,String strategy){
        String k=market+"/"+strategy;
        switch(k){
            case "stock/at":return Color.rgb(112,166,255);
            case "stock/ma":return Color.rgb(176,150,255);
            case "stock/close":return Color.rgb(255,170,110);
            case "stock/gmo":return Color.rgb(96,214,226);
            case "stock/trend":return Color.rgb(240,128,196);
            case "coin/general":return Color.rgb(74,222,160);
            case "coin/ma":return Color.rgb(226,206,84);
            case "coin/trend":return Color.rgb(255,132,120);
            default:return Color.rgb(160,172,190);
        }
    }
    private IntegratedPalette(){}
}

/** Stacked proportion bar with a wrapped legend, e.g. holdings per strategy. */
final class SegmentBarView extends View {
    static final class Segment{
        final String label;final double value;final int color;
        Segment(String label,double value,int color){this.label=label;this.value=value;this.color=color;}
    }
    private static final int COLUMNS=3;
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final List<Segment> segments=new ArrayList<>();
    private String title="",total="";
    SegmentBarView(Context c){super(c);}
    void setData(String title,String total,List<Segment> xs){
        this.title=title==null?"":title;this.total=total==null?"":total;
        segments.clear();if(xs!=null)segments.addAll(xs);requestLayout();invalidate();
    }
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}
    private float dp(float v){return v*getResources().getDisplayMetrics().density;}
    /** Height (dp) the caller should reserve for this many legend entries. */
    static int heightDp(int legendCount){
        int rows=Math.max(1,(legendCount+COLUMNS-1)/COLUMNS);
        return 22+14+8+rows*20+4;
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth();if(w<=0)return;
        p.setStyle(Paint.Style.FILL);p.setShader(null);p.setTextAlign(Paint.Align.LEFT);
        p.setTextSize(sp(13));p.setColor(PremiumUi.MUTED);c.drawText(title,0,dp(15),p);
        p.setTextAlign(Paint.Align.RIGHT);p.setFakeBoldText(true);p.setColor(PremiumUi.TEXT);
        c.drawText(total,w,dp(15),p);p.setFakeBoldText(false);
        float top=dp(22),h=dp(14);
        double sum=0;for(Segment s:segments)if(s.value>0)sum+=s.value;
        Path clip=new Path();clip.addRoundRect(new RectF(0,top,w,top+h),h/2,h/2,Path.Direction.CW);
        p.setColor(ChartStyle.TRACK);c.drawPath(clip,p);
        if(sum>0){
            c.save();c.clipPath(clip);
            float x=0;
            for(Segment s:segments){
                if(s.value<=0)continue;
                float width=(float)(s.value/sum*w);
                RectF r=new RectF(x,top,x+width,top+h);
                p.setShader(new LinearGradient(0,top,0,top+h,ChartStyle.lighten(s.color,.16f),
                    ChartStyle.darken(s.color,.22f),Shader.TileMode.CLAMP));
                c.drawRect(r,p);
                x+=width;
                p.setShader(null);p.setColor(PremiumUi.BG);c.drawRect(x-dp(1),top,x,top+h,p);
            }
            c.restore();
        }
        p.setShader(null);
        float colW=w/COLUMNS,y0=top+h+dp(8);
        for(int i=0;i<segments.size();i++){
            Segment s=segments.get(i);
            float lx=(i%COLUMNS)*colW,ly=y0+(i/COLUMNS)*dp(20)+dp(12);
            p.setStyle(Paint.Style.FILL);p.setColor(s.color);
            c.drawCircle(lx+dp(4),ly-dp(4),dp(3.5f),p);
            p.setTextAlign(Paint.Align.LEFT);p.setTextSize(sp(12));
            p.setColor(s.value>0?PremiumUi.TEXT:PremiumUi.MUTED);
            String t=s.label+" "+(int)Math.round(s.value);
            while(p.measureText(t)>colW-dp(14)&&p.getTextSize()>sp(10))p.setTextSize(p.getTextSize()-sp(.5f));
            c.drawText(t,lx+dp(11),ly,p);
        }
    }
    @Override protected void onMeasure(int wSpec,int hSpec){
        int w=View.MeasureSpec.getSize(wSpec);
        setMeasuredDimension(w,(int)dp(heightDp(segments.size())));
    }
}

/**
 * Entry-to-now return gauge. Fixed centre = break-even, bar to the right = profit,
 * bar to the left = loss, scale ±20 %. Unknown return draws only the empty track.
 */
final class ReturnGaugeView extends View {
    private static final double SCALE=20d;
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private double pct=Double.NaN;
    ReturnGaugeView(Context c){super(c);}
    void setReturn(double pct){this.pct=pct;invalidate();}
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}
    private float dp(float v){return v*getResources().getDisplayMetrics().density;}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth(),h=getHeight();if(w<=0||h<=0)return;
        float left=dp(2),right=w-dp(2),mid=(left+right)/2f,y=h*.40f,th=dp(8);
        p.setShader(null);p.setStyle(Paint.Style.FILL);p.setColor(ChartStyle.TRACK);
        c.drawRoundRect(new RectF(left,y-th/2,right,y+th/2),th/2,th/2,p);
        p.setStrokeWidth(dp(1));p.setColor(PremiumUi.LINE);
        for(int i=-2;i<=2;i++){
            float x=mid+(right-left)/2f*i/2f;
            c.drawLine(x,y+th/2+dp(2),x,y+th/2+dp(5),p);
        }
        boolean known=!Double.isNaN(pct)&&!Double.isInfinite(pct);
        if(known){
            float reach=(float)(Math.min(SCALE,Math.abs(pct))/SCALE)*(right-left)/2f;
            if(reach<dp(3))reach=dp(3);
            boolean up=pct>=0;
            int col=up?ChartStyle.UP:ChartStyle.DOWN;
            RectF bar=up?new RectF(mid,y-th/2,mid+reach,y+th/2):new RectF(mid-reach,y-th/2,mid,y+th/2);
            ChartStyle.barPaint(p,bar,col,up);
            c.drawRoundRect(bar,th/2,th/2,p);
            ChartStyle.clear(p);
            float ex=up?bar.right:bar.left;
            p.setColor(PremiumUi.BG);c.drawCircle(ex,y,th*.72f,p);
            p.setColor(col);c.drawCircle(ex,y,th*.46f,p);
        }
        p.setColor(Color.rgb(120,132,150));p.setStrokeWidth(dp(1.4f));
        c.drawLine(mid,y-th/2-dp(3),mid,y+th/2+dp(3),p);
        p.setStyle(Paint.Style.FILL);p.setTextSize(sp(10));p.setColor(PremiumUi.MUTED);
        float base=h-dp(1);
        p.setTextAlign(Paint.Align.LEFT);c.drawText("-20%",left,base,p);
        p.setTextAlign(Paint.Align.CENTER);c.drawText("매수가",mid,base,p);
        p.setTextAlign(Paint.Align.RIGHT);c.drawText("+20%",right,base,p);
    }
}

/** Tiny profit/loss bar used in per-strategy section headers: signed value against a shared maximum. */
final class SignedAmountBarView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private double value=Double.NaN,max=1;
    SignedAmountBarView(Context c){super(c);}
    void set(double value,double max){this.value=value;this.max=max>0?max:1;invalidate();}
    private float dp(float v){return v*getResources().getDisplayMetrics().density;}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth(),h=getHeight();if(w<=0||h<=0)return;
        float mid=w/2f,th=Math.min(h,dp(6)),y=(h-th)/2f;
        p.setShader(null);p.setStyle(Paint.Style.FILL);p.setColor(ChartStyle.TRACK);
        c.drawRoundRect(new RectF(0,y,w,y+th),th/2,th/2,p);
        if(!Double.isNaN(value)&&!Double.isInfinite(value)&&value!=0){
            float span=(float)Math.min(1d,Math.abs(value)/max)*(mid-dp(1));
            if(span<dp(2))span=dp(2);
            boolean up=value>0;
            RectF bar=up?new RectF(mid,y,mid+span,y+th):new RectF(mid-span,y,mid,y+th);
            ChartStyle.barPaint(p,bar,up?ChartStyle.UP:ChartStyle.DOWN,up);
            c.drawRoundRect(bar,th/2,th/2,p);ChartStyle.clear(p);
        }
        p.setColor(Color.rgb(120,132,150));c.drawRect(mid-dp(.5f),y-dp(1),mid+dp(.5f),y+th+dp(1),p);
    }
}
