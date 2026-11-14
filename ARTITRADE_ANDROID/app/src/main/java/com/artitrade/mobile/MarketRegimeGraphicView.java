package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

import org.json.JSONObject;

/** Geometric research-only market gauge; never a buy/sell recommendation. */
public final class MarketRegimeGraphicView extends View {
    private final Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
    private double score=Double.NaN;
    private double delta=Double.NaN;
    private int samples=0;
    public MarketRegimeGraphicView(Context context){super(context);}
    public void setScore(JSONObject snapshot){
        if(snapshot!=null&&!snapshot.isNull("score"))
            score=snapshot.optDouble("score",Double.NaN);
        else score=Double.NaN;
        delta=snapshot==null||snapshot.isNull("active_avg_change_pp")?
            Double.NaN:snapshot.optDouble("active_avg_change_pp",Double.NaN);
        samples=snapshot==null?0:snapshot.optInt("active_count",0);
        invalidate();
    }
    private int dp(float v){return (int)(v*getResources().getDisplayMetrics().density+.5f);}
    private void text(Canvas canvas,String value,float size,float x,float y,int color,boolean heavy){
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(color);paint.setTextSize(dp(size));
        paint.setFakeBoldText(heavy);
        canvas.drawText(value,x,y,paint);
        paint.setFakeBoldText(false);
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        final float left=dp(13),right=getWidth()-dp(13),w=Math.max(1,right-left);
        final float top=dp(47),height=dp(22);
        paint.setColor(Color.rgb(37,42,59));
        c.drawRoundRect(new RectF(left,top,right,top+height),dp(9),dp(9),paint);
        for(int tick:new int[]{50,60,70,80,90}){
            float tx=left+w*tick/100f;
            paint.setColor(Color.rgb(67,72,91));
            c.drawRect(tx-dp(.5f),top,tx+dp(.5f),top+height,paint);
        }
        text(c,"시장 점수",12,left,dp(23),Color.rgb(177,202,230),false);
        if(Double.isFinite(score)&&score>=0&&score<=100){
            int color=score<=50?Color.rgb(248,116,127):
                score<=70?Color.rgb(236,193,102):Color.rgb(93,218,170);
            paint.setColor(color);
            c.drawRoundRect(new RectF(left,top,left+w*(float)(score/100.0),top+height),
                dp(9),dp(9),paint);
            text(c,String.format(java.util.Locale.KOREA,"%.1f / 100",score),
                18,right-dp(112),dp(26),color,true);
        }else{
            text(c,"자료 미수신",14,right-dp(96),dp(26),Color.rgb(248,180,99),false);
        }
        text(c,"0",10,left,top+height+dp(18),Color.rgb(133,151,174),false);
        text(c,"50",10,left+w*.50f-dp(7),top+height+dp(18),Color.rgb(133,151,174),false);
        text(c,"100",10,right-dp(19),top+height+dp(18),Color.rgb(133,151,174),false);
        paint.setColor(Color.rgb(57,63,85));
        c.drawRect(left,top+height+dp(26),right,top+height+dp(27),paint);
        String movement=Double.isFinite(delta)?
            String.format(java.util.Locale.KOREA,"%+.2f%%p",delta):"미집계";
        text(c,"현 구간 보유수익률 변화",12,left,top+height+dp(49),
            Color.rgb(166,189,219),false);
        int deltaColor=Double.isFinite(delta)?
            delta>0?Color.rgb(92,221,169):delta<0?Color.rgb(250,123,132):
            Color.rgb(200,204,223):Color.rgb(148,156,180);
        text(c,movement,18,right-dp(113),top+height+dp(51),deltaColor,true);
        text(c,"기준점 보유종목 "+samples+"개 · 체결손익과 별개",11,
            left,top+height+dp(72),Color.rgb(147,161,183),false);
    }
}
