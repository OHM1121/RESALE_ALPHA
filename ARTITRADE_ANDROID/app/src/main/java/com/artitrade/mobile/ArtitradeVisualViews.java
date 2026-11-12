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
        p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.CENTER);p.setColor(Color.rgb(240,247,255));p.setTextSize(Math.max(18f,size*.18f));p.setFakeBoldText(true);
        c.drawText(center,cx,cy+7,p);p.setFakeBoldText(false);p.setTextSize(12f);p.setColor(Color.rgb(137,163,194));c.drawText(title,cx,h-8,p);
    }
}

final class HorizontalStrategyBarsView extends View {
    static final class Item{final String label;final double value;Item(String l,double v){label=l;value=v;}}
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final List<Item> items=new ArrayList<>();
    HorizontalStrategyBarsView(Context c){super(c);}
    void setItems(List<Item> xs){items.clear();if(xs!=null)items.addAll(xs);invalidate();}
    private static String won(double v){NumberFormat f=NumberFormat.getNumberInstance(Locale.KOREA);f.setMaximumFractionDigits(0);return f.format(v)+"원";}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);if(items.isEmpty())return;
        float left=88,right=getWidth()-70,top=12,row=Math.max(30,(getHeight()-20f)/items.size()),mid=(left+right)/2;
        double max=1;for(Item i:items)if(!Double.isNaN(i.value))max=Math.max(max,Math.abs(i.value));
        p.setStrokeWidth(1);p.setColor(Color.rgb(46,66,91));c.drawLine(mid,5,mid,getHeight()-5,p);
        for(int n=0;n<items.size();n++){
            Item it=items.get(n);float y=top+n*row+row*.5f;
            p.setTextAlign(Paint.Align.RIGHT);p.setTextSize(12);p.setColor(Color.rgb(200,218,238));p.setFakeBoldText(true);c.drawText(it.label,left-10,y+4,p);p.setFakeBoldText(false);
            if(Double.isNaN(it.value))continue;
            float span=(float)(Math.abs(it.value)/max*((right-left)*.47));
            p.setColor(it.value>=0?Color.rgb(62,224,181):Color.rgb(255,119,133));
            RectF bar=it.value>=0?new RectF(mid,y-8,mid+span,y+8):new RectF(mid-span,y-8,mid,y+8);c.drawRoundRect(bar,7,7,p);
            p.setTextAlign(it.value>=0?Paint.Align.LEFT:Paint.Align.RIGHT);p.setTextSize(11);p.setColor(Color.rgb(225,236,248));float tx=it.value>=0?Math.min(right-2,mid+span+7):Math.max(left+2,mid-span-7);c.drawText(won(it.value),tx,y+4,p);
        }
    }
}

final class PnlSparklineView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private double[] values=new double[0];
    private String label="누적 실현손익 추세";
    PnlSparklineView(Context c){super(c);}
    void setValues(double[] v,String label){values=v==null?new double[0]:v;this.label=label==null?"":label;invalidate();}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.LEFT);p.setTextSize(11);p.setColor(Color.rgb(137,163,194));c.drawText(label,8,15,p);
        if(values.length<2){p.setColor(Color.rgb(100,126,156));p.setTextSize(12);c.drawText("체결 데이터 축적 중",8,getHeight()/2f,p);return;}
        double min=values[0],max=values[0];for(double v:values){min=Math.min(min,v);max=Math.max(max,v);}if(max==min){max+=1;min-=1;}
        float l=8,r=getWidth()-8,t=26,b=getHeight()-12;
        if(min<=0&&max>=0){float zy=(float)(b-(0-min)/(max-min)*(b-t));p.setColor(Color.rgb(45,64,88));p.setStrokeWidth(1);c.drawLine(l,zy,r,zy,p);}
        Path path=new Path();for(int i=0;i<values.length;i++){float x=l+(r-l)*i/(values.length-1f);float y=(float)(b-(values[i]-min)/(max-min)*(b-t));if(i==0)path.moveTo(x,y);else path.lineTo(x,y);}
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(3);p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);p.setColor(values[values.length-1]>=0?Color.rgb(62,224,181):Color.rgb(255,119,133));c.drawPath(path,p);p.setStyle(Paint.Style.FILL);
    }
}

final class MetricProgressView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private String label="";private double value=Double.NaN;
    MetricProgressView(Context c){super(c);}
    void setMetric(String label,double value){this.label=label;this.value=value;invalidate();}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);float w=getWidth(),h=getHeight();p.setTextSize(10);p.setTextAlign(Paint.Align.LEFT);p.setColor(Color.rgb(145,170,199));c.drawText(label,0,11,p);
        p.setColor(Color.rgb(35,54,77));RectF bg=new RectF(0,h-12,w,h-4);c.drawRoundRect(bg,5,5,p);
        if(!Double.isNaN(value)){float f=(float)Math.max(0,Math.min(100,value))/100f;p.setColor(value>=60?Color.rgb(62,224,181):value>=40?Color.rgb(255,201,94):Color.rgb(255,119,133));RectF fg=new RectF(0,h-12,w*f,h-4);c.drawRoundRect(fg,5,5,p);}
        p.setTextAlign(Paint.Align.RIGHT);p.setTextSize(10);p.setColor(Color.rgb(224,236,248));c.drawText(Double.isNaN(value)?"—":String.format(Locale.KOREA,"%.1f%%",value),w,11,p);
    }
}
