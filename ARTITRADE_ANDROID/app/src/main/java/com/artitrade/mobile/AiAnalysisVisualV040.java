package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;
import org.json.JSONObject;
import java.util.Locale;

/** Native 0..100 evidence coverage chart; never turns missing into zero-market scores. */
final class AiEvidenceMeterV040 extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private JSONObject values=new JSONObject();
    AiEvidenceMeterV040(Context c){super(c);}
    private float s(float dp){return dp*getResources().getDisplayMetrics().density;}
    private float font(float sp){return sp*getResources().getDisplayMetrics().scaledDensity;}
    void setEvidence(JSONObject coverage){values=coverage==null?new JSONObject():coverage;invalidate();}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth();
        if(w<20)return;
        String[] keys={"atr_pct","volume_ratio","williams_r","market_score"};
        String[] names={"ATR","거래량","Williams","시장점수"};
        int[] cols={Color.rgb(79,189,226),Color.rgb(118,201,176),
                    Color.rgb(174,155,242),Color.rgb(242,190,101)};
        float labelX=s(4),barX=s(93),maxX=w-s(55);
        for(int i=0;i<4;i++){
            float center=s(17+i*34),pct=(float)Math.min(100,Math.max(0,values.optDouble(keys[i],0)));
            p.setStyle(Paint.Style.FILL);
            p.setColor(Color.rgb(206,222,238));
            p.setTextSize(font(14));p.setTextAlign(Paint.Align.LEFT);
            c.drawText(names[i],labelX,center+s(5),p);
            RectF track=new RectF(barX,center-s(5),Math.max(barX+1,maxX),center+s(5));
            p.setColor(Color.rgb(48,57,70));c.drawRoundRect(track,s(5),s(5),p);
            p.setColor(cols[i]);
            float width=(track.width()*pct)/100f;
            if(width>0)c.drawRoundRect(new RectF(barX,track.top,barX+width,track.bottom),s(5),s(5),p);
            p.setColor(Color.rgb(227,234,245));p.setTextAlign(Paint.Align.RIGHT);
            p.setTextSize(font(15));c.drawText(Math.round(pct)+"%",w-s(4),center+s(5),p);
        }
    }
}

/** Clear two-bar observed vs avoided-entry scenario; never paints unverified forecast. */
final class AiReturnCompareV040 extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private Double before=null,scenario=null;
    private boolean verified=false;
    AiReturnCompareV040(Context c){super(c);}
    private float s(float dp){return dp*getResources().getDisplayMetrics().density;}
    private float f(float sp){return sp*getResources().getDisplayMetrics().scaledDensity;}
    void setResult(JSONObject effect){
        if(effect==null){before=null;scenario=null;verified=false;}
        else {
            before=effect.isNull("baseline_pct")?null:effect.optDouble("baseline_pct");
            scenario=effect.isNull("scenario_pct")?null:effect.optDouble("scenario_pct");
            verified=effect.optBoolean("validated",false);
        }
        invalidate();
    }
    private String sign(double v){return String.format(Locale.KOREA,"%+.2f%%",v);}
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth();if(w<40)return;
        if(before==null||scenario==null){
            p.setColor(PremiumUi.MUTED);p.setTextSize(f(15));p.setTextAlign(Paint.Align.LEFT);
            c.drawText("종료 손익 축적 후 비교 가능",s(9),s(43),p);
            return;
        }
        double max=Math.max(.5,Math.max(Math.abs(before),Math.abs(scenario)));
        String[] name={"현재 성과","조건 제외"};
        double[] nums={before,scenario};
        int[] tint={Color.rgb(134,168,200),verified?Color.rgb(78,220,176):Color.rgb(251,190,110)};
        float left=s(91),right=w-s(82),size=Math.max(20,right-left);
        for(int i=0;i<2;i++){
            float y=s(22+i*40);
            p.setStyle(Paint.Style.FILL);p.setTextAlign(Paint.Align.LEFT);
            p.setTextSize(f(14));p.setColor(PremiumUi.TEXT);
            c.drawText(name[i],s(4),y+s(4),p);
            p.setColor(Color.rgb(48,57,70));
            c.drawRoundRect(new RectF(left,y-s(6),left+size,y+s(7)),s(5),s(5),p);
            double ratio=Math.abs(nums[i])/max;
            p.setColor(tint[i]);
            if(ratio>0)c.drawRoundRect(new RectF(left,y-s(6),left+(float)(ratio*size),y+s(7)),s(5),s(5),p);
            p.setTextAlign(Paint.Align.RIGHT);
            p.setTextSize(f(15));p.setColor(nums[i]<0?Color.rgb(255,135,151):Color.rgb(90,222,179));
            c.drawText(sign(nums[i]),w-s(3),y+s(4),p);
        }
    }
}
