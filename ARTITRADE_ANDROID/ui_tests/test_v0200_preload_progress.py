"""0.2.0: one measured progress for all start-up work, no footnotes."""
import unittest
from pathlib import Path

JAVA = (Path(__file__).resolve().parents[1] /
        'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text(encoding='utf-8')


def body(start, end):
    return JAVA[JAVA.index(start):JAVA.index(end)]


class PreloadProgress(unittest.TestCase):
    def test_boot_screen_has_no_footnotes(self):
        boot = body('private void showBoot()', 'private android.graphics.drawable.Drawable startupBarDrawable()')
        for gone in ('SAFEBOOT', '확정봉 연속성 · 시세', '완료예상', '종목별 로딩 실적 확인 중'):
            self.assertNotIn(gone, boot)
        self.assertIn('startupEta.setVisibility(View.GONE)', boot)   # shown only after a rate is measured

    def test_one_progress_from_all_measured_work(self):
        render = body('private void renderMeasuredStartupProgress', 'private void updateOriginalStockLoadingCounter')
        self.assertIn('Math.min(99L,', render)                       # 100% only from the real completion gate
        self.assertIn('startupDisplayPercent=Math.max(startupDisplayPercent,percent)', render)
        self.assertIn('startupProgress.setProgress(startupDisplayPercent', render)
        self.assertIn('startupPercent.setText(', render)
        self.assertIn('작업', render)
        self.assertIn('남은 시간 약', render)
        tick = body('private void startupReadinessTick()', 'private String readableFeedReadiness()')
        self.assertIn('measuredDone+=units;measuredTotal+=total;', tick)       # official bars / gates
        self.assertIn('measuredDone+=startupStockLoaded;measuredTotal+=startupStockTotal;', tick)
        self.assertIn('renderMeasuredStartupProgress(measuredDone,measuredTotal', tick)

    def test_old_writers_no_longer_fight_over_the_bar(self):
        counter = body('private void updateOriginalStockLoadingCounter', 'private void updateStartupProgress')
        self.assertNotIn('startupProgress.setProgress', counter)
        self.assertNotIn('startupEta.setText', counter)
        verified = body('private void updateVerifiedStartupProgress', 'private void finishPreload')
        self.assertNotIn('startupChecks', verified)

    def test_rotation_keeps_the_measured_progress(self):
        self.assertIn('v0200_units_done', JAVA)
        self.assertIn('v0200_units_total', JAVA)


if __name__ == '__main__':
    unittest.main()
