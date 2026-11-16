package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;
import org.json.JSONObject;
import java.util.Locale;

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
