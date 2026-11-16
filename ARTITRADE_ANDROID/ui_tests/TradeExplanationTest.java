package com.artitrade.mobile;

/** Real/virtual UI text must be comprehensible and timestamps truly in KST. */
public final class TradeExplanationTest {
    private static void eq(String wanted,String got){
        if(!wanted.equals(got))throw new AssertionError("expected="+wanted+" got="+got);
    }
    public static void main(String[] args){
        eq("매수",TradeExplanation.side("BUY"));
        eq("매도",TradeExplanation.side("SELL"));
        eq("매도",TradeExplanation.side("S"));
        eq("1차 목표가 도달 · 40% 분할",
            TradeExplanation.shortReason("FIRST_TARGET_40PCT","매도"));
        eq("1차 목표가 도달",TradeExplanation.shortReason("TP_FIRST_PARTIAL","매도"));
        eq("2차 목표가 도달",TradeExplanation.shortReason("SECOND_TARGET","매도"));
        eq("3차 목표가 도달",TradeExplanation.shortReason("THIRD_TARGET","매도"));
        eq("기준봉 중간가격 이탈",TradeExplanation.shortReason("MIDPOINT","매도"));
        eq("고점 이후 하락 · 추적매도",TradeExplanation.shortReason("TRAILING_STOP","매도"));
        eq("손절 기준 도달",TradeExplanation.shortReason("STOP_LOSS","매도"));
        eq("보유시간 종료",TradeExplanation.shortReason("TIME_EXIT","매도"));
        eq("2차 매도 · 1차 매도가보다 3% 상승, 잔량 절반",
            TradeExplanation.shortReason("MA_SECOND_3PCT_HALF_REMAINING","매도"));
        eq("3차 매도 · 고점 이후 하락으로 잔량 정리",
            TradeExplanation.shortReason("MA_THIRD_PEAK_TRAIL_EXIT","매도"));
        eq("큰 폭 하락 후 손실 회복 시 매도",
            TradeExplanation.shortReason("MA_V26_RECOVERY_2","매도"));
        eq("보유기간 만료 · 수익 미발생으로 매도",
            TradeExplanation.shortReason("MA_HOLD_EXPIRE_NONPROFIT_REG","매도"));
        eq("매수 이후 단기 반등 부족",
            TradeExplanation.shortReason("NO_IMMEDIATE_REACTION","매도"));
        eq("본전 가격으로 되돌아와 매도",
            TradeExplanation.shortReason("BREAK_EVEN_RETURN","매도"));
        eq("최종 목표가 도달",
            TradeExplanation.shortReason("FINAL_TARGET","매도"));
        eq("고점에서 내려와 추적매도",
            TradeExplanation.shortReason("TRAIL_EXIT","매도"));

        eq("Upbit 공개 호가 기반 하단 지정가 가상체결",
            TradeExplanation.shortReason("Upbit 공개 호가 기반 하단 지정가 가상체결","매수"));
        eq("AT DIVERSE 1.1 순수 점수 | 50만원 상한 | 호가 기반 가상체결",
            TradeExplanation.shortReason("AT DIVERSE 1.1 순수 점수 | 50만원 상한 | 호가 기반 가상체결","매수"));
        eq("수동 매도",TradeExplanation.shortReason("MANUAL_SELL","매도"));
        eq("주문호가 부족 · 체결 대기",TradeExplanation.shortReason("NO_BID","매수"));
        eq("수급매매 점수 조건 충족",TradeExplanation.shortReason("GMO_SCORE","매수"));
        eq("매도 사유가 기록되지 않음",TradeExplanation.shortReason("","매도").replace("원장에 ",""));
        eq("2026-11-13 09:00:00 KST",TradeExplanation.kst("2026-11-13T00:00:00Z",false));
        eq("2026-11-13 09:00:00 KST",TradeExplanation.kst("2026-11-13T00:00:00+00:00",false));
        eq("2026-11-13 09:00:00 KST",TradeExplanation.kst("2026-11-13 00:00:00+00:00",false));
        eq("2026-11-13 09:00:00 KST",TradeExplanation.kst("2026-11-13T09:00:00+09:00",false));
        eq("2026-11-13 09:00:00 KST",TradeExplanation.kst("2026-11-13 00:00:00",true));
        eq("2026-11-13 00:00:00 KST",TradeExplanation.kst("2026-11-13 00:00:00",false));
        eq("2026-11-13 09:00:00 KST",TradeExplanation.kst("20261113000000",true));
        eq("체결시각 미기록",TradeExplanation.kst("",false));
        eq("시각 형식 확인 필요",TradeExplanation.kst("bad timestamp",false));
        System.out.println("KOREAN_REASON_AND_KST_EXECUTION_20_CASES_PASS");
    }
}
