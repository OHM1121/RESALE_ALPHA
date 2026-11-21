"""0.2.0: first profit target, nearest stop-loss and concrete buy basis for every holding."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'app/src/main/python'))
import position_view_v0200 as view
from phone_buy_evidence_v035 import attach_buy_evidence


class Formatting(unittest.TestCase):
    def test_kst_text_handles_utc_naive_kst_and_epoch(self):
        self.assertEqual(view.kst_text('2026-11-21T00:00:00+00:00'), '11월 21일 09:00')
        self.assertEqual(view.kst_text('20261121093000'), '11월 21일 09:30')
        self.assertEqual(view.kst_text('2026-11-21T07:30:34.151329+09:00'), '11월 21일 07:30')
        self.assertEqual(view.kst_text(''), '')

    def test_price_text(self):
        self.assertEqual(view.fmt_price(3955.0), '3,955원')
        self.assertEqual(view.fmt_price(7.3), '7.3원')
        self.assertEqual(view.fmt_price(0.012), '0.012원')
        self.assertEqual(view.fmt_price(None), '—')


class Stops(unittest.TestCase):
    def test_trend_near_export_numbers(self):
        # NEAR 30m: buy 3,955, mark 3,829, -15% stop 3,361.75, lookback stop ignored.
        pos = {'entry': 3955.0, 'mark': 3829.0, 'tf': '30m'}
        view.enrich_trend_row(pos, {'stage': 0, 'nl1118_stop': 3361.75, 'target1': 4073.65,
                                    'nl1120_stop_status': 'IGNORED_ABOVE_ENTRY',
                                    'nl1120_stop_price': '4347.1'})
        self.assertAlmostEqual(pos['stop_loss_price'], 3361.75)
        self.assertEqual(pos['stop_loss_pct'], -15.0)
        self.assertEqual(pos['first_profit_target'], 4073.65)

    def test_active_lookback_stop_is_nearer_than_minus_15(self):
        pos = {'entry': 3955.0, 'mark': 3829.0}
        view.enrich_trend_row(pos, {'stage': 0, 'nl1118_stop': 3361.75, 'target1': 4073.65,
                                    'nl1120_stop_status': 'ACTIVE', 'nl1120_stop_price': '3700'})
        self.assertAlmostEqual(pos['stop_loss_price'], 3700.0)
        self.assertEqual(pos['stop_loss_pct'], -6.4)
        self.assertEqual(len(pos['stop_loss_all']), 2)

    def test_triggered_lookback_stop_is_not_listed_as_a_future_stop(self):
        pos = {'entry': 100.0, 'mark': 99.0}
        view.enrich_trend_row(pos, {'stage': 0, 'nl1118_stop': 85.0, 'nl1120_stop_status': 'ACTIVE',
                                    'nl1120_stop_price': '95', 'nl1120_stop_confirmed': True})
        self.assertEqual(pos['stop_loss_price'], 85.0)

    def test_after_first_target_the_breakeven_plus_one_stop_applies(self):
        pos = {'entry': 100.0, 'mark': 103.0}
        view.enrich_trend_row(pos, {'stage': 1, 'nl1118_stop': 85.0})
        self.assertAlmostEqual(pos['stop_loss_price'], 101.0)
        self.assertEqual(pos['stop_loss_pct'], 1.0)

    def test_nearest_picks_highest_stop_below_price_else_lowest_above(self):
        c = [{'price': 90.0, 'label': 'a', 'pct': -10}, {'price': 95.0, 'label': 'b', 'pct': -5}]
        self.assertEqual(view.nearest_stop(c, 100.0, 100.0)['price'], 95.0)
        self.assertEqual(view.nearest_stop(c, 92.0, 100.0)['price'], 90.0)
        self.assertEqual(view.nearest_stop(c, 80.0, 100.0)['price'], 90.0)
        self.assertIsNone(view.nearest_stop([], 80.0, 100.0))

    def test_reference_bar_stop_comes_from_the_saved_plan(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / 'p.sqlite3'
            c = sqlite3.connect(db)
            c.execute("""CREATE TABLE nl1118_reference_positions(position_id INTEGER,policy TEXT,market TEXT,
                frame TEXT,symbol TEXT,intent_json TEXT,updated_at TEXT)""")
            c.execute("INSERT INTO nl1118_reference_positions VALUES(16,'NEW_LEGACY_1118','coin','30m','KRW-BARD',?,'')",
                      (json.dumps({'entry': 131.0, 'stop': 122.0, 'first': 134.93, 'stage': 0}),))
            c.commit(); c.close()
            pos = {'id': 16, 'tf': '30m', 'entry': '131', 'mark': '131.0'}
            view.enrich_reference([pos], db)
            self.assertEqual(pos['stop_loss_price'], 122.0)
            self.assertEqual(pos['stop_loss_pct'], -6.9)
            self.assertEqual(pos['first_profit_target'], 134.93)

    def test_missing_plan_adds_nothing_and_never_raises(self):
        pos = {'id': 1, 'entry': '100', 'mark': '90'}
        view.enrich_reference([pos], '/nonexistent/path.sqlite3')
        view.enrich_ma([pos], '/nonexistent/path.sqlite3')
        self.assertNotIn('stop_loss_price', pos)


class BuyBasis(unittest.TestCase):
    def test_coin_reference_bar_basis_is_concrete(self):
        candidate = {'id': 9, 'tf': '60m', 'market': 'KRW-SNT', 'ref_at': '2026-11-21T00:00:00+00:00',
                     'limit_price': '7.3', 'reason': 'QUEUED',
                     'features': json.dumps({'open': 7.3, 'high': 9.15, 'low': 7.3, 'close': 8.57})}
        position = {'id': 5, 'tf': '60m', 'market': 'KRW-SNT', 'entry': '7.5', 'ref_at': '2026-11-21T00:00:00+00:00',
                    'candidate_id': 9}
        fills = [{'id': 1, 'position_id': 5, 'tf': '60m', 'market': 'KRW-SNT', 'side': 'BUY', 'at': '2026-11-21T10:30:00+09:00',
                  'price': '7.5', 'qty': '10', 'reason': 'Upbit 공개 호가 기반 하단 지정가 가상체결'}]
        attach_buy_evidence([position], fills, [candidate], market='coin', strategy='base')
        text = position['buy_basis']
        self.assertIn('11월 21일 09:00 시작 60분봉 기준봉 발생', text)
        self.assertIn('저가 7.3원', text)
        self.assertIn('11월 21일 10:30', text)
        self.assertIn('7.5원에 매수', text)
        self.assertIn('기준봉 저가보다', text)          # price above the low is stated, not hidden
        self.assertNotIn('하단 지정가', text)

    def test_basis_is_missing_not_invented_without_a_reference_low(self):
        position = {'id': 5, 'tf': '60m', 'market': 'KRW-X', 'entry': '7.5'}
        fills = [{'id': 1, 'position_id': 5, 'tf': '60m', 'market': 'KRW-X', 'side': 'BUY', 'at': '2026-11-21T10:30:00+09:00',
                  'price': '7.5', 'reason': 'x'}]
        attach_buy_evidence([position], fills, [], market='coin', strategy='base')
        self.assertNotIn('buy_basis', position)

    def test_trend_basis_has_signal_bar_rsi_rule_and_buy(self):
        pos = {'tf': '30m', 'entry': 3955.0, 'entry_at': '2026-11-21T07:30:34.151329+09:00'}
        payload = {'entry_rule': 'NL1120_BAR_CLOSE_ABOVE_BOLL_MID', 'rsi20_closed_at_buy': 43.8,
                   'bar_key': '2026-11-21T08:00:00+09:00', 'market_score_at_buy': -12.5,
                   'buy_size_factor': '0.5'}
        text = view.basis_trend(pos, payload)
        self.assertIn('RSI(20)', text)
        self.assertIn('43.8', text)
        self.assertIn('볼린저밴드(20,1.5) 중앙선 위에서 마감', text)
        self.assertIn('3,955원에 매수', text)
        self.assertIn('1종목 한도 1/2', text)
        full = view.basis_trend(pos, dict(payload, market_score_at_buy=5.0, buy_size_factor='1'))
        self.assertNotIn('한도 1/2', full)

    def test_trend_snapshot_exposes_basis_and_stop(self):
        src = (BASE / 'app/src/main/python/market_trend_wallet_v054.py').read_text(encoding='utf-8')
        self.assertIn("p['buy_basis']=basis", src)
        self.assertIn('enrich_trend_row(p,saved)', src)


class UiWiring(unittest.TestCase):
    def test_labels_and_fields_in_every_holding_card(self):
        ui = (BASE / 'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text(encoding='utf-8')
        card = ui[ui.index('private LinearLayout buildHoldingCard'):ui.index('private String positionBuyReason')]
        self.assertIn('"1차 수익목표"', card)
        self.assertNotIn('"1차 목표"', card)
        self.assertIn('"손절가"', card)
        self.assertIn('stop_loss_pct', ui)
        self.assertIn('first_profit_target', ui)
        reason = ui[ui.index('private String positionBuyReason'):ui.index('private void renderIntegratedPositions')]
        self.assertIn('buy_basis', reason)


if __name__ == '__main__':
    unittest.main()
