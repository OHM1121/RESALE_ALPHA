package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

import java.util.Locale;

/** Compact numeric score-band map: one strategy, one candle frame, actual vs virtual. */
final class ResearchScoreMatrixView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private ResearchScoreGrid grid=new ResearchScoreGrid();
    private String frame="",title="";
    private float density=1f;
    ResearchScoreMatrixView(Context c){super(c);density=getResources().getDisplayMetrics().density;}
    void setGrid(ResearchScoreGrid src,String frame,String title){
        this.grid=src==null?new ResearchScoreGrid():src;
        this.frame=frame==null?"":frame;
        this.title=title==null?"":title;
        invalidate();
    }
    private float dp(float n){return density*n;}
    private float sp(float n){return n*getResources().getDisplayMetrics().scaledDensity;}
    private void line(Canvas c,String text,float x,float baseline,int size,int color,Paint.Align align,boolean bold){
        p.setStyle(Paint.Style.FILL);p.setTextAlign(align);p.setFakeBoldText(bold);
        p.setTextSize(sp(size));p.setColor(color);c.drawText(text,x,baseline,p);
        p.setFakeBoldText(false);
    }
    private int shade(double rate){
        if(Double.isNaN(rate))return Color.rgb(159,175,198);
        return rate>=60?Color.rgb(74,217,161):rate>=40?Color.rgb(245,186,100):Color.rgb(248,107,123);
    }
    private void rate(Canvas c,ResearchScoreGrid.Cell q,boolean actual,float left,float right,float baseline){
        double value=actual?q.actualRate():q.virtualRate();
        int hit=actual?q.actualHit:q.virtualHit;
        int miss=actual?q.actualMiss:q.virtualMiss;
        int pending=actual?q.actualPending:q.virtualPending;
        String pct=Double.isNaN(value)?"—":String.format(Locale.KOREA,"%.0f%%",value);
        String detail=hit+"/"+(hit+miss)+(pending>0?" · "+pending:"");
        float available=right-left-dp(8);
        p.setTextSize(sp(11));float countWidth=p.measureText(detail);
        p.setTextSize(sp(14));float pctWidth=p.measureText(pct);
        boolean stacked=pctWidth+countWidth>available;
        int accent=shade(value);
        line(c,pct,left+dp(3),stacked?baseline-dp(3):baseline,14,accent,Paint.Align.LEFT,true);
        line(c,detail,right-dp(2),stacked?baseline+dp(9):baseline,
            11,PremiumUi.MUTED,Paint.Align.RIGHT,false);
        float top=baseline+dp(stacked?13:5),height=dp(stacked?3:5);
        p.setStyle(Paint.Style.FILL);p.setColor(PremiumUi.LINE);
        c.drawRoundRect(new RectF(left,top,right,top+height),dp(3),dp(3),p);
        if(!Double.isNaN(value)&&value>0){
            p.setColor(accent);
            float x=left+(right-left)*(float)Math.min(1d,Math.max(0d,value/100d));
            c.drawRoundRect(new RectF(left,top,x,top+height),dp(3),dp(3),p);
        }
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth(),h=getHeight(),pad=dp(10);
        p.setStyle(Paint.Style.FILL);p.setColor(PremiumUi.SURFACE);
        c.drawRoundRect(new RectF(0,0,w,h),dp(14),dp(14),p);
        p.setStyle(Paint.Style.STROKE);p.setColor(PremiumUi.LINE);p.setStrokeWidth(dp(.7f));
        c.drawRoundRect(new RectF(0,0,w,h),dp(14),dp(14),p);
        if(w<dp(190))return;
        ResearchScoreGrid.Cell total=grid.totals();
        line(c,title,pad,dp(24),15,Color.rgb(227,239,255),Paint.Align.LEFT,true);
        line(c,grid.any(frame)?"N="+countFrame(frame):"N=0",w-pad,dp(24),
            12,PremiumUi.MUTED,Paint.Align.RIGHT,false);
        float scoreRight=pad+dp(66);
        float gap=dp(9);
        float colW=(w-scoreRight-pad-gap*2)/2f;
        float actualX=scoreRight+gap,virtualX=actualX+colW+gap;
        line(c,"점수",pad,dp(48),12,PremiumUi.MUTED,Paint.Align.LEFT,false);
        line(c,"실제",actualX+dp(2),dp(48),12,Color.rgb(168,199,236),Paint.Align.LEFT,false);
        line(c,"가상",virtualX+dp(2),dp(48),12,Color.rgb(154,212,189),Paint.Align.LEFT,false);
        int rows=grid.any(frame)&&grid.cell(frame,ResearchScoreGrid.NO_SCORE).signals>0?6:5;
        float rowH=(h-dp(58)-pad)/rows;
        for(int i=0;i<rows;i++){
            float y=dp(59)+i*rowH,mid=y+rowH*.48f;
            String band=ResearchScoreGrid.BANDS[i];
            ResearchScoreGrid.Cell cell=grid.cell(frame,band);
            if(i%2==0){p.setColor(PremiumUi.SURFACE_HIGH);p.setStyle(Paint.Style.FILL);
                c.drawRoundRect(new RectF(pad-dp(2),y,w-pad+dp(2),y+rowH-dp(1)),dp(5),dp(5),p);}
            line(c,band.equals(ResearchScoreGrid.NO_SCORE)?"—":band,pad,mid+dp(3),13,
                Color.rgb(186,201,226),Paint.Align.LEFT,true);
            rate(c,cell,true,actualX,actualX+colW,mid);
            rate(c,cell,false,virtualX,virtualX+colW,mid);
        }
    }
    private int countFrame(String tf){
        int count=0;
        for(String band:ResearchScoreGrid.BANDS)count+=grid.cell(tf,band).signals;
        return count;
    }
}
