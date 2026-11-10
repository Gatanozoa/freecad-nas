import bootstrap
import ast
import unittest
from mobile.script_contract import prepare_script,TAIL

class ContractTests(unittest.TestCase):
    def test_missing_result(self):
        with self.assertRaisesRegex(ValueError,'顶层'):prepare_script('shape = None')
    def test_function_local_is_not_module_output(self):
        with self.assertRaises(ValueError):prepare_script('def build():\n    result = None\n    return result\nbuild()')
    def test_function_call_assigned(self):
        ast.parse(prepare_script('def build():\n    return None\nresult = build()'))
    def test_existing_result_and_idempotent(self):
        prepared=prepare_script('result = shape')
        self.assertEqual(prepare_script(prepared),prepared)
    def test_adapter_wraps_shape_only(self):
        class Shape: pass
        class Document:
            def addObject(self,*args):return type('Feature',(),{})()
            def recompute(self):pass
        import types
        from unittest.mock import patch
        part=types.SimpleNamespace(Shape=Shape)
        app=types.SimpleNamespace(ActiveDocument=Document())
        shape=Shape();scope={'result':shape}
        with patch.dict('sys.modules',{'FreeCAD':app,'Part':part}):exec(TAIL,scope)
        self.assertIs(scope['result'].Shape,shape)
        original=object();scope={'result':original}
        with patch.dict('sys.modules',{'FreeCAD':app,'Part':part}):exec(TAIL,scope)
        self.assertIs(scope['result'],original)

if __name__=='__main__':unittest.main(verbosity=2)
