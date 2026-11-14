"""Policy rows need the original linked position code to filter by clicked symbol."""
import ast
import unittest
from pathlib import Path

source=Path(__file__).parents[1]/'app/src/main/python/android_runtime_v078.py'
tree=ast.parse(source.read_text(encoding='utf8'))
fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_attach_policy_symbol')
scope={}
exec(compile(ast.Module(body=[fn],type_ignores=[]),str(source),'exec'),scope)

class SourceLink(unittest.TestCase):
    def test_missing_execution_code_is_filled_only_from_same_original_position(self):
        e={'position_id':9,'side':'SELL','price':125}
        scope['_attach_policy_symbol'](e,{'id':9,'code':'005930','name':'삼성전자','tf':'5m'})
        self.assertEqual(e['code'],'005930')
        self.assertEqual(e['name'],'삼성전자')
        self.assertEqual(e['tf'],'5m')
    def test_existing_identity_preserved(self):
        e={'code':'000660','name':'SK하이닉스'}
        scope['_attach_policy_symbol'](e,{'code':'005930','name':'삼성전자','tf':'day'})
        self.assertEqual(e['code'],'000660')

if __name__=='__main__':unittest.main()
