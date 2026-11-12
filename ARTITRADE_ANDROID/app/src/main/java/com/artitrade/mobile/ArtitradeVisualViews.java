package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.view.View;

import java.text.NumberFormat;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/** Lightweight Canvas visualizations for the native ARTITRADE dashboard. */
final class DonutMetricView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}
    private double value=Double.NaN;
    private String title="",center="—";
    DonutMetricView(Context c){super(c);}
    void setMetric(String title,double value,String center){this.title=title;this.value=value;this.center=center==null?"—":center;invalidate();}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth(),h=getHeight(),size=Math.min(w,h)*.70f,cx=w/2f,cy=h*.48f,sw=Math.max(10f,size*.13f);
        RectF r=new RectF(cx-size/2,cy-size/2,cx+size/2,cy+size/2);
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(sw);p.setStrokeCap(Paint.Cap.ROUND);p.setColor(Color.rgb(34,54,78));c.drawArc(r,-90,360,false,p);
        if(!Double.isNaN(value)){
            float sweep=(float)(Math.max(0,Math.min(100,value))*3.6);
            p.setColor(value>=60?Color.rgb(62,224,181):value>=40?Color.rgb(255,201,94):Color.rgb(255,119,133));
            c.drawArc(r,-90,sweep,false,p);
        }
        p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.CENTER);p.setColor(Color.rgb(240,247,255));p.setTextSize(Math.max(sp(18f),size*.18f));p.setFakeBoldText(true);
        c.drawText(center,cx,cy+sp(3),p);p.setFakeBoldText(false);p.setTextSize(sp(14f));p.setColor(Color.rgb(151,160,181));c.drawText(title,cx,h-sp(6),p);
    }
}

final class HorizontalStrategyBarsView extends View {
    static final class Item{final String label;final double value;Item(String l,double v){label=l;value=v;}}
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}
    private final List<Item> items=new ArrayList<>();
    HorizontalStrategyBarsView(Context c){super(c);}
    void setItems(List<Item> xs){items.clear();if(xs!=null)items.addAll(xs);invalidate();}
    private static String won(double v){NumberFormat f=NumberFormat.getNumberInstance(Locale.KOREA);f.setMaximumFractionDigits(0);return f.format(v)+"원";}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);if(items.isEmpty())return;
        float left=sp(82),right=getWidth()-sp(78),top=sp(8),row=Math.max(sp(34),(getHeight()-sp(16))/items.size()),mid=(left+right)/2;
        double max=1;for(Item i:items)if(!Double.isNaN(i.value))max=Math.max(max,Math.abs(i.value));
        p.setStrokeWidth(1);p.setColor(Color.rgb(46,66,91));c.drawLine(mid,5,mid,getHeight()-5,p);
        for(int n=0;n<items.size();n++){
            Item it=items.get(n);float y=top+n*row+row*.5f;
            p.setTextAlign(Paint.Align.RIGHT);p.setTextSize(sp(14));p.setColor(Color.rgb(200,218,238));p.setFakeBoldText(true);c.drawText(it.label,left-sp(8),y+sp(4),p);p.setFakeBoldText(false);
            if(Double.isNaN(it.value))continue;
            float span=(float)(Math.abs(it.value)/max*((right-left)*.47));
            p.setColor(it.value>=0?Color.rgb(62,224,181):Color.rgb(255,119,133));
            RectF bar=it.value>=0?new RectF(mid,y-sp(7),mid+span,y+sp(7)):new RectF(mid-span,y-sp(7),mid,y+sp(7));c.drawRoundRect(bar,7,7,p);
            p.setTextAlign(it.value>=0?Paint.Align.LEFT:Paint.Align.RIGHT);p.setTextSize(sp(13));p.setColor(Color.rgb(225,236,248));float tx=it.value>=0?Math.min(right-sp(2),mid+span+sp(7)):Math.max(left+sp(2),mid-span-sp(7));c.drawText(won(it.value),tx,y+sp(4),p);
        }
    }
}

final class PnlSparklineView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}
    private double[] values=new double[0];
    private String label="누적 실현손익 추세";
    PnlSparklineView(Context c){super(c);}
    void setValues(double[] v,String label){values=v==null?new double[0]:v;this.label=label==null?"":label;invalidate();}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.LEFT);p.setTextSize(sp(14));p.setColor(Color.rgb(151,160,181));c.drawText(label,sp(6),sp(16),p);
        if(values.length<2){p.setColor(Color.rgb(116,126,148));p.setTextSize(sp(14));c.drawText("체결 데이터 축적 중",sp(6),getHeight()/2f,p);return;}
        double min=values[0],max=values[0];for(double v:values){min=Math.min(min,v);max=Math.max(max,v);}if(max==min){max+=1;min-=1;}
        float l=sp(6),r=getWidth()-sp(6),t=sp(30),b=getHeight()-sp(10);
        if(min<=0&&max>=0){float zy=(float)(b-(0-min)/(max-min)*(b-t));p.setColor(Color.rgb(45,64,88));p.setStrokeWidth(1);c.drawLine(l,zy,r,zy,p);}
        Path path=new Path();for(int i=0;i<values.length;i++){float x=l+(r-l)*i/(values.length-1f);float y=(float)(b-(values[i]-min)/(max-min)*(b-t));if(i==0)path.moveTo(x,y);else path.lineTo(x,y);}
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(3);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);p.setColor(values[values.length-1]>=0?Color.rgb(62,224,181):Color.rgb(255,119,133));c.drawPath(path,p);p.setStyle(Paint.Style.FILL);
    }
}

final class MetricProgressView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}
    private String label="";private double value=Double.NaN;
    MetricProgressView(Context c){super(c);}
    void setMetric(String label,double value){this.label=label;this.value=value;invalidate();}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);float w=getWidth(),h=getHeight();p.setTextSize(sp(13));p.setTextAlign(Paint.Align.LEFT);p.setColor(Color.rgb(154,164,185));c.drawText(label,0,sp(13),p);
        float by=h-sp(11),bh=sp(6);p.setColor(Color.rgb(43,48,63));RectF bg=new RectF(0,by,w,by+bh);c.drawRoundRect(bg,bh,bh,p);
        if(!Double.isNaN(value)){float f=(float)Math.max(0,Math.min(100,value))/100f;p.setColor(value>=60?Color.rgb(79,219,170):value>=40?Color.rgb(255,194,91):Color.rgb(255,119,142));RectF fg=new RectF(0,by,w*f,by+bh);c.drawRoundRect(fg,bh,bh,p);}
        p.setTextAlign(Paint.Align.RIGHT);p.setTextSize(sp(13));p.setColor(Color.rgb(232,235,244));c.drawText(Double.isNaN(value)?"—":String.format(Locale.KOREA,"%.1f%%",value),w,sp(13),p);
    }
}


final class TimeframePerformanceGridView extends View {
    static final class Cell {
        final String label;
        final double total,realized,unrealized,win,target;
        final int positions,pending;
        Cell(String label,double total,double realized,double unrealized,double win,double target,int positions,int pending){
            this.label=label;this.total=total;this.realized=realized;this.unrealized=unrealized;this.win=win;this.target=target;this.positions=positions;this.pending=pending;
        }
    }
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final List<Cell> cells=new ArrayList<>();
    TimeframePerformanceGridView(Context c){super(c);}
    void setCells(List<Cell> xs){cells.clear();if(xs!=null)cells.addAll(xs);invalidate();}
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}
    private String compactWon(double v){
        if(Double.isNaN(v))return "—";
        double a=Math.abs(v);String sign=v>0?"+":v<0?"−":"";
        if(a>=100000000)return sign+String.format(Locale.KOREA,"%.1f억",a/100000000d);
        if(a>=10000)return sign+String.format(Locale.KOREA,"%.1f만",a/10000d);
        NumberFormat f=NumberFormat.getNumberInstance(Locale.KOREA);f.setMaximumFractionDigits(0);
        return sign+f.format(a)+"원";
    }
    private String wonSmall(double v){
        if(Double.isNaN(v))return "—";
        NumberFormat f=NumberFormat.getNumberInstance(Locale.KOREA);f.setMaximumFractionDigits(0);
        return f.format(v)+"원";
    }
    private void fitText(String text,float max,float preferred){
        float size=sp(preferred);p.setTextSize(size);
        while(size>sp(13)&&p.measureText(text)>max){size-=sp(.7f);p.setTextSize(size);}
    }
    private int pnlColor(double v){return v>0?Color.rgb(79,219,170):v<0?Color.rgb(255,119,142):Color.rgb(238,240,247);}
    private void metricBar(Canvas c,String label,double value,float left,float top,float right){
        p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.LEFT);p.setTextSize(sp(11));p.setColor(Color.rgb(143,153,174));c.drawText(label,left,top,p);
        p.setTextAlign(Paint.Align.RIGHT);p.setColor(Color.rgb(220,224,235));c.drawText(Double.isNaN(value)?"—":String.format(Locale.KOREA,"%.0f%%",value),right,top,p);
        float by=top+sp(7),bh=sp(5);p.setColor(Color.rgb(43,48,63));c.drawRoundRect(new RectF(left,by,right,by+bh),bh,bh,p);
        if(!Double.isNaN(value)){
            float f=(float)Math.max(0,Math.min(100,value))/100f;
            p.setColor(value>=60?Color.rgb(79,219,170):value>=40?Color.rgb(255,194,91):Color.rgb(255,119,142));
            c.drawRoundRect(new RectF(left,by,left+(right-left)*f,by+bh),bh,bh,p);
        }
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        if(cells.isEmpty())return;
        float gap=sp(8),pad=sp(4),cw=(getWidth()-gap-2*pad)/2f,ch=(getHeight()-gap-2*pad)/2f;
        for(int i=0;i<Math.min(4,cells.size());i++){
            Cell x=cells.get(i);int col=i%2,row=i/2;
            float l=pad+col*(cw+gap),t=pad+row*(ch+gap),r=l+cw,b=t+ch;
            p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(18,21,31));c.drawRoundRect(new RectF(l,t,r,b),sp(16),sp(16),p);
            p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(sp(.7f));p.setColor(Color.rgb(43,48,66));c.drawRoundRect(new RectF(l,t,r,b),sp(16),sp(16),p);p.setStyle(Paint.Style.FILL);

            float x0=l+sp(14),x1=r-sp(14),y=t+sp(25);
            p.setTextAlign(Paint.Align.LEFT);p.setFakeBoldText(true);p.setTextSize(sp(18));p.setColor(Color.rgb(211,207,255));c.drawText(x.label,x0,y,p);p.setFakeBoldText(false);
            p.setTextAlign(Paint.Align.RIGHT);p.setTextSize(sp(12));p.setColor(Color.rgb(130,140,161));c.drawText("보유 "+x.positions+"  ·  대기 "+x.pending,x1,y,p);

            y+=sp(31);String total=compactWon(x.total);p.setTextAlign(Paint.Align.LEFT);p.setFakeBoldText(true);fitText(total,cw-sp(28),23);p.setColor(pnlColor(x.total));c.drawText(total,x0,y,p);p.setFakeBoldText(false);

            y+=sp(25);p.setTextSize(sp(12));p.setTextAlign(Paint.Align.LEFT);p.setColor(Color.rgb(142,152,173));c.drawText("실현",x0,y,p);
            p.setTextAlign(Paint.Align.RIGHT);p.setColor(pnlColor(x.realized));c.drawText(compactWon(x.realized),x1,y,p);
            y+=sp(21);p.setTextAlign(Paint.Align.LEFT);p.setColor(Color.rgb(142,152,173));c.drawText("평가",x0,y,p);
            p.setTextAlign(Paint.Align.RIGHT);p.setColor(pnlColor(x.unrealized));c.drawText(compactWon(x.unrealized),x1,y,p);

            y+=sp(25);metricBar(c,"승률",x.win,x0,y,x1);
            y+=sp(31);metricBar(c,"목표도달",x.target,x0,y,x1);
        }
    }
}
