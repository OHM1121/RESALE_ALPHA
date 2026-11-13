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
    private double[] values=new double[0];
    private long[] timestamps=new long[0];
    private String label="";
    PnlSparklineView(Context c){super(c);}
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}

    void setSeries(RealizedTrend.Series series,String label){
        values=series==null?new double[0]:series.values;
        timestamps=series==null?new long[0]:series.timestamps;
        this.label=label==null?"":label;
        invalidate();
    }
    void setValues(double[] value,String label){
        values=value==null?new double[0]:value;
        timestamps=new long[0];
        this.label=label==null?"":label;
        invalidate();
    }
    private String compactMoney(double v){return WonFormat.won(v);}
    private String tickDate(long ts){
        if(ts<=0)return "날짜 없음";
        java.text.SimpleDateFormat f=new java.text.SimpleDateFormat("MM.dd",Locale.KOREA);
        f.setTimeZone(java.util.TimeZone.getTimeZone("Asia/Seoul"));
        return f.format(new java.util.Date(ts));
    }
    private float pointX(int index,int count,float left,float right){
        if(count<=1)return left+(right-left)*.5f;
        if(timestamps.length==count){
            long first=timestamps[0],last=timestamps[count-1];
            if(last>first)return left+(right-left)*
                (float)((timestamps[index]-first)/(double)(last-first));
        }
        return left+(right-left)*index/(count-1f);
    }
    private void drawXAxisLabels(Canvas c,float left,float right,float bottom){
        p.setStyle(Paint.Style.FILL);
        p.setTextSize(sp(9));p.setColor(Color.rgb(146,160,184));
        p.setTextAlign(Paint.Align.LEFT);
        String first=timestamps.length>0?tickDate(timestamps[0]):"날짜";
        c.drawText(first,left,bottom+sp(16),p);
        if(timestamps.length>1){
            p.setTextAlign(Paint.Align.RIGHT);
            c.drawText(tickDate(timestamps[timestamps.length-1]),right,bottom+sp(16),p);
        }
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        final float w=getWidth(),h=getHeight();
        if(w<=0||h<=0)return;
        p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.LEFT);
        p.setTextSize(sp(12));p.setColor(Color.rgb(215,226,239));
        c.drawText(label,sp(4),sp(15),p);
        // Allocate y-axis width from the *real entire KRW strings*, not an abbreviated value.
        double axisMin=0d,axisMax=0d;
        for(double v:values)if(Double.isFinite(v)){axisMin=Math.min(axisMin,v);axisMax=Math.max(axisMax,v);}
        p.setTextSize(sp(8.5f));
        float axisText=Math.max(p.measureText(compactMoney(axisMin)),p.measureText(compactMoney(axisMax)));
        float left=Math.min(w*.46f,Math.max(sp(65f),axisText+sp(13f)));
        float right=w-sp(9),top=sp(35),bottom=h-sp(24);
        if(right<=left||bottom<=top)return;
        p.setTextAlign(Paint.Align.RIGHT);p.setTextSize(sp(14));
        String headline=values.length>0?WonFormat.signed(values[values.length-1]):"—";
        while(p.measureText(headline)>w*.57f&&p.getTextSize()>sp(10))p.setTextSize(p.getTextSize()-sp(.5f));
        p.setColor(values.length>0&&values[values.length-1]<0?Color.rgb(255,119,133):Color.rgb(62,224,181));
        c.drawText(headline,w-sp(7),sp(17),p);
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(sp(.7f));p.setColor(Color.rgb(54,67,88));
        c.drawLine(left,top,left,bottom,p);
        c.drawLine(left,bottom,right,bottom,p);
        p.setStyle(Paint.Style.FILL);
        if(values.length==0){
            p.setTextSize(sp(11));p.setColor(Color.rgb(255,194,91));
            c.drawText("실현손익 체결기록 없음",left+sp(3),top+(bottom-top)/2f,p);
            drawXAxisLabels(c,left,right,bottom);
            return;
        }
        double min=Math.min(values[0],0d),max=Math.max(values[0],0d);
        for(double v:values){min=Math.min(min,v);max=Math.max(max,v);}
        if(max<=min){max=min+1;min-=1;}
        for(int k=0;k<3;k++){
            double tick=min+(max-min)*k/2.0;
            float y=(float)(bottom-(tick-min)/(max-min)*(bottom-top));
            p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(sp(.5f));p.setColor(Color.rgb(36,49,69));
            c.drawLine(left,y,right,y,p);
            p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.RIGHT);
            p.setTextSize(sp(8.5f));p.setColor(Color.rgb(143,159,181));
            while(p.measureText(compactMoney(tick))>left-sp(7)&&p.getTextSize()>sp(7))p.setTextSize(p.getTextSize()-sp(.4f));
            c.drawText(compactMoney(tick),left-sp(3),y+sp(3),p);
        }
        int n=values.length;
        Path curve=new Path();
        for(int i=0;i<n;i++){
            float x=pointX(i,n,left,right);
            float y=(float)(bottom-(values[i]-min)/(max-min)*(bottom-top));
            if(i==0)curve.moveTo(x,y);else curve.lineTo(x,y);
        }
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(sp(2.3f));
        p.setStrokeCap(Paint.Cap.ROUND);p.setStrokeJoin(Paint.Join.ROUND);
        p.setColor(values[n-1]<0?Color.rgb(255,119,133):
            values[n-1]>0?Color.rgb(62,224,181):Color.rgb(255,194,91));
        c.drawPath(curve,p);
        float x=pointX(n-1,n,left,right);
        float y=(float)(bottom-(values[n-1]-min)/(max-min)*(bottom-top));
        p.setStyle(Paint.Style.FILL);c.drawCircle(x,y,sp(3),p);
        drawXAxisLabels(c,left,right,bottom);
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
    private String compactWon(double v){return WonFormat.signed(v);}
    private String wonSmall(double v){return WonFormat.won(v);}
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


final class TimeframeAccountFlowView extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private String label="";private double total=Double.NaN,realized=Double.NaN,unrealized=Double.NaN,win=Double.NaN,target=Double.NaN;
    private int pending=0,bought=0,holding=0,sold=0;
    TimeframeAccountFlowView(Context c){super(c);}
    void setData(String label,double total,double realized,double unrealized,double win,double target,
                 int pending,int bought,int holding,int sold){
        this.label=label;this.total=total;this.realized=realized;this.unrealized=unrealized;this.win=win;this.target=target;
        this.pending=Math.max(0,pending);this.bought=Math.max(0,bought);this.holding=Math.max(0,holding);this.sold=Math.max(0,sold);invalidate();
    }
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}
    private float dp(float v){return v*getResources().getDisplayMetrics().density;}
    private int pnlColor(double v){return v>0?Color.rgb(75,222,171):v<0?Color.rgb(255,111,139):Color.rgb(236,239,247);}
    // Keep the method name for compatibility with older visual tests; never abbreviate money.
    private String compact(double v){return WonFormat.signed(v);}
    private void fit(String value,float maxWidth,float preferred,float minimum){
        float size=sp(preferred);p.setTextSize(size);
        while(size>sp(minimum)&&p.measureText(value)>maxWidth){
            size-=sp(.5f);p.setTextSize(size);
        }
    }
    private void amountTile(Canvas c,String icon,String label,double value,
                            float left,float top,float width,float height,int accent){
        p.setTextAlign(Paint.Align.LEFT);p.setStyle(Paint.Style.FILL);
        p.setColor(Color.rgb(24,36,54));
        c.drawRoundRect(new RectF(left,top,left+width,top+height),dp(9),dp(9),p);
        p.setTextSize(sp(11));p.setColor(accent);p.setFakeBoldText(true);
        c.drawText(icon,left+dp(8),top+dp(15),p);
        p.setFakeBoldText(false);p.setTextSize(sp(10));p.setColor(Color.rgb(158,182,208));
        c.drawText(label,left+dp(25),top+dp(15),p);
        String amount=compact(value);
        p.setTextAlign(Paint.Align.RIGHT);p.setFakeBoldText(true);
        fit(amount,width-dp(16),22,12);
        p.setColor(pnlColor(value));
        c.drawText(amount,left+width-dp(8),top+height-dp(5),p);
        p.setFakeBoldText(false);
    }
    private void progress(Canvas c,String name,double value,float left,float top,float width,int accent){
        p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.LEFT);
        p.setTextSize(sp(10));p.setColor(Color.rgb(165,186,212));
        c.drawText(name,left,top,p);
        p.setTextAlign(Paint.Align.RIGHT);p.setFakeBoldText(true);
        p.setColor(Double.isNaN(value)?Color.rgb(154,165,185):accent);
        c.drawText(Double.isNaN(value)?"—":String.format(Locale.KOREA,"%.0f%%",value),
            left+width,top,p);p.setFakeBoldText(false);
        float y=top+dp(5),gh=dp(5);
        p.setColor(Color.rgb(40,53,72));
        c.drawRoundRect(new RectF(left,y,left+width,y+gh),gh,gh,p);
        if(Double.isFinite(value)&&value>0){
            p.setColor(accent);float part=(float)Math.max(0,Math.min(100,value))/100f;
            c.drawRoundRect(new RectF(left,y,left+width*part,y+gh),gh,gh,p);
        }
    }
    private void step(Canvas c,float left,float top,float width,float height,
                      String icon,String name,int count,int accent){
        p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(23,31,45));
        c.drawRoundRect(new RectF(left,top,left+width,top+height),dp(9),dp(9),p);
        p.setColor(Color.rgb(56,69,88));p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(dp(.6f));
        c.drawRoundRect(new RectF(left,top,left+width,top+height),dp(9),dp(9),p);
        p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.LEFT);p.setFakeBoldText(true);
        p.setTextSize(sp(11));p.setColor(accent);c.drawText(icon,left+dp(7),top+dp(15),p);
        p.setTextSize(sp(10));p.setColor(Color.rgb(173,184,204));
        c.drawText(name,left+dp(24),top+dp(15),p);
        p.setTextAlign(Paint.Align.RIGHT);p.setTextSize(sp(21));p.setColor(accent);
        c.drawText(String.valueOf(count),left+width-dp(8),top+height-dp(6),p);
        p.setFakeBoldText(false);
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth(),h=getHeight(),pad=dp(11);
        if(w<dp(250)||h<dp(146))return;
        p.setStyle(Paint.Style.FILL);p.setColor(Color.rgb(18,24,35));
        c.drawRoundRect(new RectF(0,0,w,h),dp(13),dp(13),p);
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(dp(.8f));p.setColor(Color.rgb(47,64,86));
        c.drawRoundRect(new RectF(0,0,w,h),dp(13),dp(13),p);p.setStyle(Paint.Style.FILL);
        float rightLeft=w*.52f,rightWidth=w-rightLeft-pad,halfLimit=rightLeft-pad*2;
        p.setTextAlign(Paint.Align.LEFT);p.setFakeBoldText(true);
        p.setTextSize(sp(17));p.setColor(Color.rgb(209,217,255));
        c.drawText(label,pad,dp(22),p);
        p.setTextSize(sp(10));p.setColor(Color.rgb(117,145,180));
        c.drawText("◆  누적손익",pad,dp(37),p);
        String pnl=compact(total);fit(pnl,halfLimit,32,13);
        p.setTextAlign(Paint.Align.LEFT);p.setFakeBoldText(true);p.setColor(pnlColor(total));
        c.drawText(pnl,pad,dp(66),p);p.setFakeBoldText(false);
        amountTile(c,"●","실현",realized,rightLeft,dp(5),rightWidth,dp(37),Color.rgb(79,210,247));
        amountTile(c,"◇","평가",unrealized,rightLeft,dp(45),rightWidth,dp(37),Color.rgb(175,156,255));
        float gaugeGap=dp(14),gaugeWidth=(w-pad*2-gaugeGap)/2f;
        progress(c,"◉  승률",win,pad,dp(94),gaugeWidth,Color.rgb(93,150,255));
        progress(c,"◎  목표",target,pad+gaugeWidth+gaugeGap,dp(94),gaugeWidth,Color.rgb(68,224,182));
        float stageTop=dp(119),stageHeight=h-stageTop-dp(6);
        float gap=dp(5),stepWidth=(w-2*pad-3*gap)/4f;
        String[] icons={"◷","↘","◆","↗"},names={"대기","매수","보유","매도"};
        int[] counts={pending,bought,holding,sold};
        int[] accent={Color.rgb(255,196,102),Color.rgb(95,154,253),
                      Color.rgb(177,152,255),Color.rgb(74,224,180)};
        for(int i=0;i<4;i++)
            step(c,pad+i*(stepWidth+gap),stageTop,stepWidth,stageHeight,
                 icons[i],names[i],counts[i],accent[i]);
    }

}
