"""v039: audited forward high labels, BTC 14-day RSI, integrated six strategies."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).parents[1]/"app/src/main/python"))
import market_research_v033 as m

ROOT=Path(__file__).parents[1]
JAVA=(ROOT/"app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java").read_text()
RUNTIME=(ROOT/"app/src/main/python/android_runtime_v078.py").read_text()

def tickers():
    return [{"market":f"KRW-TEST{i}", "signed_change_rate":.01,
             "acc_trade_price_24h":1000000+i} for i in range(30)]

def candles(direction):
    # newest first with an unfinished first element
    historical=[1000+direction*(i*12+0.1*i*i) for i in range(34)]
    return [{"trade_price":historical[-1]+12345}]+[
        {"trade_price":x} for x in reversed(historical)]


class IntegratedV039(unittest.TestCase):
    def test_rsi_oversold_weight_15_and_trend_15(self):
        down=m.coin_score(tickers(),candles(-1),candles(-1))
        up=m.coin_score(tickers(),candles(1),candles(1))
        self.assertIsNotNone(down["score"])
        self.assertIsNotNone(up["score"])
        self.assertEqual(len(up["factors"]),6)
        self.assertAlmostEqual(sum(f["weight"] for f in up["factors"]),1.0)
        weights={x["name"]:x["weight"] for x in up["factors"]}
        self.assertEqual(weights["BTC 추세"],.15)
        self.assertEqual(weights["BTC RSI(14) 과매도/과매수"],.15)
        self.assertGreater(down["factors"][-1]["value"],up["factors"][-1]["value"])
        self.assertGreater(down["factors"][-1]["impact"],up["factors"][-1]["impact"])
        self.assertGreater(down["factors"][-1]["value"],95)
        self.assertLess(up["factors"][-1]["value"],5)

    def test_rsi_not_repainted_by_unfinished_today(self):
        origin=candles(-1)
        r1=m.coin_score(tickers(),origin,candles(-1))["btc_rsi14"]
        origin[0]["trade_price"]=50
        r2=m.coin_score(tickers(),origin,candles(-1))["btc_rsi14"]
        self.assertEqual(r1,r2)

    def test_rsi_neutral_flat_prices(self):
        self.assertEqual(m._rsi14_closed([100.]*20),50.)
        self.assertIsNone(m._rsi14_closed([100.]*14))

    def test_original_historical_peak_is_not_current_last_observed_time(self):
        self.assertIn("'observed_max_unverified':trail.get('max_price')",RUNTIME)
        self.assertIn("'observed_max_price':None",RUNTIME)
        self.assertIn("'observed_max_at':None",RUNTIME)
        self.assertIn("'observation_last_at_is_not_peak_at':True",RUNTIME)
        self.assertIn("AGGREGATE_NO_PEAK_TIMESTAMP",RUNTIME)
        body=JAVA[JAVA.index("private String[] researchSellCell("):
                  JAVA.index("private boolean researchGoalVerified(")]
        self.assertIn('independentBarVerified="VERIFIED_MINUTE_BAR"',body)
        self.assertIn('"후속 고가 검증 대기"',body)
        self.assertIn("Object peakAt=firstValue(row,\"observed_max_at\",",body)
        self.assertNotIn('firstValue(row,"last_observed_at","highest_observed_trade_date")',body)
        self.assertIn('!"at".equals(source.source)&&finite(observed)',body)
        self.assertIn('!"at".equals(source.source)&&touched!=null',body)

    def test_four_top_tabs_and_only_two_integrated_bottom_tabs(self):
        for name in ('"시장"','"통합"','"주식"','"코인"'):
            self.assertIn("segmentItem("+name,JAVA)
        shell=JAVA[JAVA.index("private void showShell()"):
                   JAVA.index("private void showBoot()") if
                   "private void showBoot()" in JAVA[JAVA.index("private void showShell()"):] else
                   JAVA.index("private void refresh(")]
        # Non-integrated bottom options are gated.
        self.assertIn('if(!integratedMode){',shell)
        self.assertIn('bottomNavItem("home","⌂","대시보드")',shell)
        self.assertIn('bottomNavItem("positions","◆","보유종목")',shell)
        for name in ("pending","executions","research","analysis"):
            self.assertIn('bottomNavItem("'+name+'"',shell)
        self.assertIn('if(!"market".equals(page)&&!integratedMode)',shell)

    def test_six_strategies_realized_charts_and_single_holdings(self):
        self.assertIn("private void renderIntegratedHome()",JAVA)
        self.assertIn("private void renderIntegratedPositions()",JAVA)
        self.assertIn('for(String m:new String[]{"stock","coin"})',JAVA)
        self.assertIn('"integrated/all"',JAVA)
        self.assertIn("integratedEvents.add(event);",JAVA)
        self.assertIn("RealizedTrend.cumulative(integratedEvents,180)",JAVA)
        self.assertIn("PnlSparklineView chart=new PnlSparklineView(this);",JAVA)
        self.assertIn("String key=m+\"/\"+s+\"/\"+timeFrameOf(r)+\"/\"+id;",JAVA)
        self.assertIn('"CLOSED".equals(status)',JAVA)
        self.assertIn('if(integratedMode){',JAVA)
        self.assertIn("page=\"home\";resetHistoryFilter();persistNavigation();showShell();",JAVA)

    def test_integrated_stats_sum_never_uses_unknown_as_zero(self):
        self.assertIn("boolean complete=knownR==totalScopes&&knownU==totalScopes;",JAVA)
        self.assertIn('complete?WonFormat.signed(aggregate):"일부 원장 미수신"',JAVA)


if __name__=="__main__":
    unittest.main()
