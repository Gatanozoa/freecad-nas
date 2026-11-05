"""Catch accidental unsafe code; not an adversarial Python sandbox."""
import ast

MODULES = {'FreeCAD', 'Part', 'Mesh', 'MeshPart', 'Draft', 'math'}
NAMES = {'open', 'exec', 'eval', 'compile', '__import__', 'getattr', 'setattr', 'delattr', 'globals', 'locals', 'vars', 'input', 'breakpoint'}
METHODS = {'open', 'read', 'write', 'save', 'saveAs', 'saveCopy', 'openDocument', 'insert', 'export', 'importBrep', 'exportBrep', 'exportStep', 'exportStl', 'exportIges', 'load', 'restore'}


def validate(script):
    if not isinstance(script, str) or not 1 <= len(script) <= 50000:
        raise ValueError('script must contain 1–50000 characters')
    try:
        tree = ast.parse(script)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ValueError(f'Script syntax rejected: {exc}') from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(item.name not in MODULES for item in node.names):
            raise ValueError('Imports allowed: ' + ', '.join(sorted(MODULES)))
        if isinstance(node, ast.ImportFrom) and (node.level or node.module not in MODULES or any(item.name == '*' for item in node.names)):
            raise ValueError('Unsupported import')
        if isinstance(node, ast.ImportFrom) and any(item.name in NAMES | METHODS or item.name.startswith('_') for item in node.names):
            raise ValueError('Importing file or dynamic execution helpers is forbidden')
        if isinstance(node, ast.Name) and (node.id in NAMES or (node.id.startswith('__') and node.id != '__name__')):
            raise ValueError(f'Forbidden name: {node.id}')
        if isinstance(node, ast.Attribute) and (node.attr.startswith('_') or node.attr in METHODS):
            raise ValueError(f'Forbidden attribute: {node.attr}; the service handles exports')
