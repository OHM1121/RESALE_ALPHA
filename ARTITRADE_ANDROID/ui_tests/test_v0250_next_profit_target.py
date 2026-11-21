"""Unreleased: after the first profit stage is SOLD the holding card shows the second target."""
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'app/src/main/python'))
import position_view_v0200 as view

UI = (BASE / 'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text(encoding='utf-8')


class NextTarget(unittest.TestCase):
    def test_trend_after_first_sell_exposes_stage_and_second_target(self):
        pos = {'entry': 93550.0, 'mark': 96450.0, 'tf': '30m'}
        view.enrich_trend_row(pos, {'stage': 1, 'target1': 96356.5, 'target2': 98283.63, 'nl1118_stop': 79517.5})
        self.assertEqual(pos['profit_stage'], 1)
        self.assertAlmostEqual(pos['second_profit_target'], 98283.63)
        self.assertAlmostEqual(pos['stop_loss_price'], 93550.0 * 1.01)

    def test_before_first_sell_stage_is_zero(self):
        pos = {'entry': 100.0, 'mark': 100.0, 'tf': '30m'}
        view.enrich_trend_row(pos, {'stage': 0, 'target1': 103.0, 'target2': 105.06})
        self.assertEqual(pos['profit_stage'], 0)

    def test_card_labels_the_next_target(self):
        card = UI[UI.index('private LinearLayout buildHoldingCard'):UI.index('private String positionBuyReason')]
        for text in ('▲ 2차 수익목표', '▲ 1차 수익목표', '1차 매도 완료', 'second_profit_target', 'profit_stage'):
            self.assertIn(text, card)


if __name__ == '__main__':
    unittest.main()
