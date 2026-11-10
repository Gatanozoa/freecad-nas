"""Check explicit output intent; wrap only an explicitly returned FreeCAD Shape."""
import ast

MARKER='# mobile-result-contract-v1'
TAIL='''
# mobile-result-contract-v1
import FreeCAD as _mobile_App
import Part as _mobile_Part
if isinstance(result, _mobile_Part.Shape):
    _mobile_shape = result
    _mobile_doc = _mobile_App.ActiveDocument
    if _mobile_doc is None:
        _mobile_doc = _mobile_App.newDocument("MobileModel")
    result = _mobile_doc.addObject("Part::Feature", "MobileResult")
    result.Shape = _mobile_shape
    _mobile_doc.recompute()
'''

def prepare_script(script):
    try: tree=ast.parse(script)
    except SyntaxError as exc: raise ValueError('脚本语法错误：'+str(exc)) from exc
    class OutputVisitor(ast.NodeVisitor):
        found=False
        def visit_FunctionDef(self,node): pass
        def visit_AsyncFunctionDef(self,node): pass
        def visit_ClassDef(self,node): pass
        def visit_Lambda(self,node): pass
        def visit_Assign(self,node):
            if any(isinstance(n,ast.Name) and n.id=='result' for target in node.targets for n in ast.walk(target)): self.found=True
            self.generic_visit(node)
        def visit_AnnAssign(self,node):
            if node.value is not None and isinstance(node.target,ast.Name) and node.target.id=='result': self.found=True
            self.generic_visit(node)
    visitor=OutputVisitor();visitor.visit(tree)
    if not visitor.found:
        raise ValueError('脚本必须在模块顶层明确赋值 result；若使用建模函数，应写 result = build_model()。result 应是含 Shape 的 FreeCAD 文档对象。')
    if MARKER in script.splitlines(): return script
    final=script.rstrip()+'\n'+TAIL
    if len(final)>50000: raise ValueError('脚本加上结果适配后超过 50000 字符，请简化脚本')
    return final
