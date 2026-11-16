package com.artitrade.mobile;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.RectF;
import android.graphics.Shader;
import android.view.View;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.Locale;

/** Read-only vector data visualizations; no broker or strategy dependency. */
final class AiEvidenceFlowV046 extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private int signals=0,positions=0,verified=0,open=0,unverified=0;
    private int virtual=0;
    private boolean virtualAvailable=false;
    AiEvidenceFlowV046(Context c){super(c);}
    private float dp(float n){return n*getResources().getDisplayMetrics().density;}
    private float sp(float n){return n*getResources().getDisplayMetrics().scaledDensity;}
    void setReport(JSONObject report){
        signals=positions=verified=open=unverified=virtual=0;
        virtualAvailable=false;
        JSONArray items=report==null?null:report.optJSONArray("frames");
        if(items!=null)for(int i=0;i<items.length();i++){
            JSONObject frame=items.optJSONObject(i);
            if(frame==null)continue;
            signals+=Math.max(0,frame.optInt("signals",0));
            positions+=Math.max(0,frame.optInt("entries",0));
            verified+=Math.max(0,frame.optInt("evaluated",0));
            open+=Math.max(0,frame.optInt("open",0));
            unverified+=Math.max(0,frame.optInt("closed_missing",0));
        }
        JSONObject trend=report==null?null:report.optJSONObject("market_trend_rsi20");
        if(trend!=null&&trend.has("virtual")){
            virtual=Math.max(0,trend.optInt("virtual",0));
            virtualAvailable=true;
        }
        setContentDescription("증거 흐름, 신호 "+signals+"건, 실제포지션 "+
            positions+"건, 손익검증 "+verified+"건, 미청산 "+open+
            "건, 손익 미확인 종료 "+unverified+"건, 마켓트렌드 가상 "+
            (virtualAvailable?virtual+"건":"해당 없음"));
        invalidate();
    }
    private void fill(Canvas c,RectF r,int color,int border){
        p.setShader(null);p.setColor(color);p.setStyle(Paint.Style.FILL);
        c.drawRoundRect(r,dp(10),dp(10),p);
        p.setStyle(Paint.Style.STROKE);p.setColor(border);p.setStrokeWidth(dp(1));
        c.drawRoundRect(r,dp(10),dp(10),p);p.setStyle(Paint.Style.FILL);
    }
    private void write(Canvas c,String s,float x,float y,float size,int color,boolean heavy){
        p.setShader(null);p.setColor(color);p.setTextAlign(Paint.Align.CENTER);
        p.setTypeface(heavy?android.graphics.Typeface.DEFAULT_BOLD:
                            android.graphics.Typeface.DEFAULT);
        p.setTextSize(sp(size));c.drawText(s,x,y,p);
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth(),h=getHeight();if(w<dp(210))return;
        p.setStyle(Paint.Style.FILL);
        p.setShader(new LinearGradient(0,0,w,h,
            Color.rgb(20,29,43),Color.rgb(12,23,28),Shader.TileMode.CLAMP));
        c.drawRoundRect(new RectF(dp(1),dp(1),w-dp(1),h-dp(1)),dp(14),dp(14),p);
        p.setShader(null);
        float pad=dp(10),gap=dp(11);
        float tile=(w-2*pad-2*gap)/3;
        float top=dp(18),bottom=dp(96);
        String[] names={"관측 신호","실제 포지션","순손익 검증"};
        int[] nums={signals,positions,verified};
        int[] color={Color.rgb(147,191,249),Color.rgb(86,224,182),Color.rgb(242,202,132)};
        for(int i=0;i<3;i++){
            float left=pad+i*(tile+gap);
            RectF rect=new RectF(left,top,left+tile,bottom);
            fill(c,rect,Color.rgb(24,32,43),Color.rgb(51,64,78));
            write(c,String.format(Locale.KOREA,"%,d",nums[i]),left+tile/2,top+dp(34),
                  23,color[i],true);
            write(c,names[i],left+tile/2,top+dp(58),11,Color.rgb(194,207,225),false);
            if(i<2){
                float x=left+tile+dp(2),y=top+dp(39);
                p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(dp(1.4f));
                p.setColor(Color.rgb(131,154,175));
                c.drawLine(x,y,x+gap-dp(4),y,p);
                c.drawLine(x+gap-dp(6),y-dp(3),x+gap-dp(3),y,p);
                c.drawLine(x+gap-dp(6),y+dp(3),x+gap-dp(3),y,p);
                p.setStyle(Paint.Style.FILL);
            }
        }
        float chipY=dp(113);
        String[] second={"보유 중 "+open,"손익 미확인 "+unverified,
            virtualAvailable?"별도 가상 "+virtual:"가상 해당 없음"};
        for(int i=0;i<3;i++){
            float left=pad+i*(tile+gap);
            RectF chip=new RectF(left,chipY,left+tile,chipY+dp(34));
            fill(c,chip,Color.rgb(21,26,33),Color.rgb(43,54,63));
            write(c,second[i],left+tile/2,chipY+dp(22),10,
                  i==2?Color.rgb(182,156,225):Color.rgb(180,191,207),false);
        }
    }
}

/** Only evidence coverage percentages, never post-entry indicators as entry causes. */
final class AiFeatureCoverageV046 extends View {
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private static final String[] KEYS={
        "market_score","rsi","atr_pct","volume_ratio",
        "williams_r","spread_pct","gap_pct","score"};
    private static final String[] NAMES={
        "시장점수","RSI","ATR","상대거래량",
        "Williams","호가차이","갭","진입점수"};
    private final float[] rates=new float[KEYS.length];
    AiFeatureCoverageV046(Context c){super(c);}
    private float dp(float v){return v*getResources().getDisplayMetrics().density;}
    private float sp(float v){return v*getResources().getDisplayMetrics().scaledDensity;}
    void setFrame(JSONObject frame){
        JSONObject coverage=frame==null?null:frame.optJSONObject("coverage");
        for(int i=0;i<KEYS.length;i++){
            double v=coverage==null?0:coverage.optDouble(KEYS[i],0);
            rates[i]=(float)(Double.isFinite(v)?Math.max(0,Math.min(100,v)):0);
        }
        invalidate();
    }
    @Override protected void onDraw(Canvas c){
        super.onDraw(c);
        float w=getWidth();if(w<dp(180))return;
        float gap=dp(9),pad=dp(3),cell=(w-2*pad-gap)/2f;
        for(int i=0;i<KEYS.length;i++){
            int col=i%2,row=i/2;
            float left=pad+col*(cell+gap),top=dp(5)+row*dp(32);
            p.setShader(null);p.setStyle(Paint.Style.FILL);
            p.setColor(Color.rgb(22,28,36));
            c.drawRoundRect(new RectF(left,top,left+cell,top+dp(28)),dp(6),dp(6),p);
            p.setTextAlign(Paint.Align.LEFT);
            p.setColor(Color.rgb(207,219,233));p.setTextSize(sp(11));
            c.drawText(NAMES[i],left+dp(6),top+dp(12),p);
            p.setTextAlign(Paint.Align.RIGHT);
            p.setColor(rates[i]>=70?Color.rgb(96,220,183):
                rates[i]>=30?Color.rgb(246,199,109):Color.rgb(250,150,157));
            c.drawText(String.format(Locale.KOREA,"%.0f%%",rates[i]),
                left+cell-dp(6),top+dp(12),p);
            float start=left+dp(6),right=left+cell-dp(6),y=top+dp(19);
            p.setColor(Color.rgb(53,61,73));
            c.drawRoundRect(new RectF(start,y,right,y+dp(3)),dp(2),dp(2),p);
            if(rates[i]>0){
                p.setColor(Color.rgb(83,181,211));
                c.drawRoundRect(new RectF(start,y,
                    start+(right-start)*rates[i]/100f,y+dp(3)),dp(2),dp(2),p);
            }
        }
    }
}
