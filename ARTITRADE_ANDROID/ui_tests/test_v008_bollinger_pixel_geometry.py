"""NEW LEGACY Bollinger visual angle: signal and renderer share same view."""
import math,unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app/src/main/python"))
import new_legacy_1118_policy as p


class VisualSlope(unittest.TestCase):
    def test_downward_45_degree_screen_line_is_rejected(self):
        prices=[120.-i for i in range(21)]
        decision=p.bollinger(prices)
        self.assertIsNotNone(decision)
        self.assertLess(decision["angle_deg"],-35)
        self.assertFalse(decision["angle_allowed"])
        self.assertEqual(decision["angle_basis"],
                         "PIXEL_PROJECTED_BOLLINGER_MID_21_BAR_VIEWPORT_V008")
        self.assertNotEqual(decision["angle_deg"],
                            math.degrees(math.atan((109.5/110.5-1)*100)))

    def test_upward_45_degree_screen_line_is_rejected(self):
        decision=p.bollinger([100.+i for i in range(21)])
        self.assertGreater(decision["angle_deg"],30)
        self.assertFalse(decision["angle_allowed"])

    def test_flat_mid_is_angle_zero(self):
        decision=p.bollinger([100.]*21)
        self.assertEqual(decision["angle_deg"],0)
        self.assertTrue(decision["angle_allowed"])

    def test_viewport_exactly_determines_pixel_line_angle(self):
        chart={"price_min":90.0,"price_max":110.,
               "plot_height_px":200.,"candle_pitch_px":10.}
        self.assertAlmostEqual(p.projected_chart_angle(100.,99.,chart),-45.)
        self.assertAlmostEqual(p.projected_chart_angle(100.,101.,chart),45.)
        self.assertAlmostEqual(p.projected_chart_angle(100.,100.,chart),0.)
        self.assertIsNone(p.projected_chart_angle(100,99,
            {"price_min":110,"price_max":90,"plot_height_px":200,"candle_pitch_px":10}))

    def test_same_raw_price_slope_different_zoom_is_not_same_visual_angle(self):
        compact={"price_min":80.,"price_max":120.,"plot_height_px":200.,
                 "candle_pitch_px":10.}
        zoom={"price_min":95.,"price_max":105.,"plot_height_px":200.,
              "candle_pitch_px":10.}
        a=p.projected_chart_angle(100.,99.,compact)
        b=p.projected_chart_angle(100.,99.,zoom)
        self.assertNotAlmostEqual(a,b)

if __name__=="__main__":unittest.main()
