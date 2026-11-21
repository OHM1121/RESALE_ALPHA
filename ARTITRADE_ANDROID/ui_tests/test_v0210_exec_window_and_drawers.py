"""0.2.1: execution window keeps BUY rows of visible positions; drawers never close by themselves."""
import re
import sys
import unittest
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / 'app/src/main/python'))
import ui_exec_window_v0210 as win

JAVA = (BASE / 'app/src/main/java/com/artitrade/mobile/UnifiedVisualActivity.java').read_text(encoding='utf-8')


def _fills():
    rows = [{'id': 114, 'position_id': 50, 'side': 'BUY', 'market': 'KRW-PIEVERSE'},
            {'id': 115, 'position_id': 50, 'side': 'BUY', 'market': 'KRW-PIEVERSE'},
            {'id': 116, 'position_id': 51, 'side': 'BUY', 'market': 'KRW-META2'},
            {'id': 117, 'position_id': 51, 'side': 'SELL', 'market': 'KRW-META2'}]
    rows += [{'id': 200 + i, 'position_id': 50, 'side': 'SELL', 'market': 'KRW-PIEVERSE'}
             for i in range(900)]
    rows += [{'id': 2000 + i, 'position_id': 60 + i % 7, 'side': 'SELL', 'market': 'X'}
             for i in range(40)]
    rows.sort(key=lambda r: r['id'], reverse=True)
    return rows


class ExecutionWindow(unittest.TestCase):
    def test_old_buys_of_visible_positions_survive_the_window(self):
        rows = _fills()
        shown = rows[:300] + win.linked_fills(rows[:300], rows[300:])
        buys = {r['id'] for r in shown if r['side'] == 'BUY'}
        self.assertEqual(buys, {114, 115})          # position 50 is visible, so its BUYs are kept
        self.assertNotIn(116, buys)                  # position 51 has no row in the window
        self.assertLess(len(shown), 1300)            # still a bounded display window

    def test_nothing_to_add_when_nothing_omitted(self):
        self.assertEqual(win.linked_fills(_fills()[:50], []), [])

    def test_uid_links_too(self):
        rows = [{'id': 1, 'position_uid': 'U1', 'side': 'BUY'}] + [
            {'id': 10 + i, 'position_uid': 'U1', 'side': 'SELL'} for i in range(400)]
        rows.sort(key=lambda r: r['id'], reverse=True)
        shown = rows[:300] + win.linked_fills(rows[:300], rows[300:])
        self.assertIn(1, {r['id'] for r in shown})


class WiredIntoUiView(unittest.TestCase):
    def test_real_ui_view_function_keeps_the_buy(self):
        import ast, json
        src = (BASE / 'app/src/main/python/android_runtime_v078.py').read_text(encoding='utf-8')
        fn = [x for x in ast.parse(src).body
              if isinstance(x, ast.FunctionDef) and x.name == '_ui_view_only_v104']
        ns = {'json': json}
        exec(compile(ast.Module(body=fn, type_ignores=[]), 'android_runtime_v078.py', 'exec'), ns)
        view = ns['_ui_view_only_v104']({'coin': {'ma': {'executions': _fills()}}})
        shown = view['coin']['ma']['executions']
        self.assertEqual({r['id'] for r in shown if r['side'] == 'BUY'}, {114, 115})
        self.assertGreater(view['ui_snapshot_v104']['omitted_count_by_path']['coin/ma/executions'], 0)

    def test_ui_view_uses_the_helper(self):
        src = (BASE / 'app/src/main/python/android_runtime_v078.py').read_text(encoding='utf-8')
        self.assertIn('ui_exec_window_v0210', src)
        self.assertIn("label=='executions'", src)


class Drawers(unittest.TestCase):
    def test_buy_reason_state_is_not_view_local(self):
        self.assertIn('expandedBuyReasons', JAVA)
        self.assertNotIn('why.getVisibility()!=View.VISIBLE', JAVA)

    def test_no_drawer_state_is_cleared_by_timers_or_trim(self):
        self.assertNotIn('expandedTradeEvidenceKeys.clear()', JAVA)
        self.assertNotIn('expandedTradeEvidenceKeys.size()>180', JAVA)
        for name in ('expandedTradeGroups', 'expandedBuyReasons'):
            self.assertNotRegex(JAVA, name + r'\.clear\(\)')


if __name__ == '__main__':
    unittest.main()
