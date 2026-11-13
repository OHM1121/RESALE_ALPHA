package com.artitrade.mobile;

import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RectF;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.provider.Settings;
import android.view.MotionEvent;
import android.view.View;
import android.view.animation.DecelerateInterpolator;

/** OLED-black finance UI: one outline icon family and short reduced-motion-safe feedback.
 * Pure presentation, never submits orders, edits ledgers, or changes research data.
 */
final class PremiumUi {
    static final int BG=Color.BLACK;
    static final int SURFACE=Color.rgb(12,14,18);
    static final int SURFACE_HIGH=Color.rgb(20,23,29);
    static final int LINE=Color.rgb(40,45,54);
    static final int TEXT=Color.rgb(243,247,251);
    static final int MUTED=Color.rgb(177,188,202);
    static final int STOCK=Color.rgb(130,165,255);
    static final int COIN=Color.rgb(63,222,176);
    static final int PROFIT=Color.rgb(67,223,175);
    static final int LOSS=Color.rgb(255,123,143);
    static final int WAIT=Color.rgb(245,195,108);
    private PremiumUi(){}

    static Drawable rounded(Context context,int fill,int border,float radiusDp){
        float dp=context.getResources().getDisplayMetrics().density;
        GradientDrawable base=new GradientDrawable();
        base.setColor(fill);base.setCornerRadius(radiusDp*dp);
        if(border!=Color.TRANSPARENT)base.setStroke(Math.max(1,(int)(dp+.5f)),border);
        GradientDrawable mask=new GradientDrawable();
        mask.setColor(Color.WHITE);mask.setCornerRadius(radiusDp*dp);
        return new RippleDrawable(ColorStateList.valueOf(Color.argb(39,207,220,242)),base,mask);
    }

    static void tapMotion(View view){
        view.setOnTouchListener((v,e)->{
            if(!v.isEnabled()||!v.isClickable())return false;
            int action=e.getActionMasked();
            if(action!=MotionEvent.ACTION_DOWN&&action!=MotionEvent.ACTION_UP
                &&action!=MotionEvent.ACTION_CANCEL&&action!=MotionEvent.ACTION_OUTSIDE)return false;
            float scale;
            try{
                scale=Settings.Global.getFloat(v.getContext().getContentResolver(),
                    Settings.Global.ANIMATOR_DURATION_SCALE,1f);
            }catch(Exception ignored){scale=1f;}
            if(scale<=0)return false;
            boolean pressed=action==MotionEvent.ACTION_DOWN;
            v.animate().cancel();
            v.animate().scaleX(pressed?.975f:1f).scaleY(pressed?.975f:1f)
                .alpha(pressed?.91f:1f)
                .setInterpolator(new DecelerateInterpolator())
                .setDuration(pressed?85:155).start();
            return false; // Preserve normal click, long-click, scrolling and 4-tap symbols.
        });
    }

    static void reveal(View v){
        float scale;
        try{
            scale=Settings.Global.getFloat(v.getContext().getContentResolver(),
                Settings.Global.ANIMATOR_DURATION_SCALE,1f);
        }catch(Exception ignored){scale=1f;}
        if(scale<=0)return;
        v.setAlpha(.90f);
        v.setTranslationY(v.getResources().getDisplayMetrics().density*5f);
        v.animate().alpha(1f).translationY(0f).setDuration(165)
            .setInterpolator(new DecelerateInterpolator()).start();
    }
}

/** Small, consistent stroked portfolio glyphs, drawn at 24dp regardless of Android fonts. */
final class PremiumIconView extends View {
    private final String kind;
    private final int tint;
    private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path path=new Path();
    PremiumIconView(Context context,String kind,int tint){
        super(context);this.kind=kind;this.tint=tint;
        setImportantForAccessibility(IMPORTANT_FOR_ACCESSIBILITY_NO);
    }
    private void stroke(Canvas c,float... points){
        if(points.length<4)return;
        path.reset();path.moveTo(points[0],points[1]);
        for(int i=2;i+1<points.length;i+=2)path.lineTo(points[i],points[i+1]);
        c.drawPath(path,p);
    }
    @Override protected void onDraw(Canvas raw){
        super.onDraw(raw);
        int checkpoint=raw.save();
        float side=Math.min(getWidth(),getHeight())*.74f;
        if(side<=0){raw.restoreToCount(checkpoint);return;}
        raw.translate((getWidth()-side)/2f,(getHeight()-side)/2f);
        raw.scale(side/24f,side/24f);
        p.setStyle(Paint.Style.STROKE);p.setStrokeWidth(1.8f);
        p.setStrokeJoin(Paint.Join.ROUND);p.setStrokeCap(Paint.Cap.ROUND);
        p.setColor(tint);
        switch(kind){
            case "close":
                stroke(raw,6,6,18,18);
                stroke(raw,18,6,6,18);
                break;
            case "home":
                stroke(raw,3,11,12,3,21,11);
                stroke(raw,5.5f,10,5.5f,21,18.5f,21,18.5f,10);
                stroke(raw,10,21,10,14,14,14,14,21);
                break;
            case "positions":
                raw.drawRoundRect(new RectF(4,6,20,20),2.3f,2.3f,p);
                stroke(raw,4,11,20,11);
                stroke(raw,8,3.5f,16,3.5f);
                stroke(raw,8,15.5f,11,18.5f,16.5f,13);
                break;
            case "pending":
                raw.drawCircle(12,12,9,p);
                stroke(raw,12,6.5f,12,12,16.5f,15);
                break;
            case "buy":
                stroke(raw,4,18,10,12,14,15,20,7);
                stroke(raw,15,7,20,7,20,12);
                break;
            case "sell":
                stroke(raw,4,6,10,12,14,9,20,17);
                stroke(raw,15,17,20,17,20,12);
                break;
            case "executions":
                stroke(raw,4,7,19,7,15,3);
                stroke(raw,19,7,15,11);
                stroke(raw,20,17,5,17,9,13);
                stroke(raw,5,17,9,21);
                break;
            case "research":
                stroke(raw,3,20,3,5);
                stroke(raw,3,20,22,20);
                stroke(raw,5.5f,15,10,11,14,13,20,5);
                raw.drawCircle(10,11,1.3f,p);
                raw.drawCircle(20,5,1.3f,p);
                break;
            case "settings":
                stroke(raw,4,6,20,6);
                stroke(raw,4,12,20,12);
                stroke(raw,4,18,20,18);
                p.setStyle(Paint.Style.FILL);
                p.setColor(PremiumUi.SURFACE_HIGH);
                raw.drawCircle(9,6,3.4f,p);
                raw.drawCircle(16,12,3.4f,p);
                raw.drawCircle(11,18,3.4f,p);
                p.setStyle(Paint.Style.STROKE);p.setColor(tint);
                raw.drawCircle(9,6,3.2f,p);
                raw.drawCircle(16,12,3.2f,p);
                raw.drawCircle(11,18,3.2f,p);
                break;
            case "profit":
                stroke(raw,4,18,9,13,12,15,20,6);
                stroke(raw,15,6,20,6,20,11);
                break;
            case "loss":
                stroke(raw,4,6,9,11,12,9,20,18);
                stroke(raw,15,18,20,18,20,13);
                break;
            case "archive":
                raw.drawRoundRect(new RectF(4,4,20,8),1.8f,1.8f,p);
                raw.drawRoundRect(new RectF(5.5f,8,18.5f,21),1.5f,1.5f,p);
                stroke(raw,10,13,14,13);
                break;
            case "wallet":
                raw.drawRoundRect(new RectF(3,7,21,20),2.4f,2.4f,p);
                stroke(raw,4,7,4,5,18,5);
                raw.drawRoundRect(new RectF(14,11,22,17),1.8f,1.8f,p);
                break;
            case "refresh":
                raw.drawArc(new RectF(4,4,20,20),-75,278,false,p);
                stroke(raw,19,3,20,9,14,8);
                break;
            default:
                raw.drawCircle(12,12,7.5f,p);
                raw.drawCircle(12,12,1.6f,p);
                break;
        }
        raw.restoreToCount(checkpoint);
    }
}
