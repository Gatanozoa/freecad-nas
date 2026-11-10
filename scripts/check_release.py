"""Reject private runtime files and credential values from the Git-tracked release."""
from pathlib import Path
import re
import subprocess

root=Path(__file__).resolve().parents[1]
tracked=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
for name in filter(None,tracked):
    path=Path(name)
    assert path.name!='.env' and '.qa' not in path.parts,name
    assert not path.name.endswith('.private.json'),name
    assert not name.startswith(('benchmarks/','live-results/')),name
    assert path.suffix.lower() not in ('.zip','.sqlite','.fcstd','.stl','.step','.glb'),name
    if path.suffix in ('.py','.yml','.yaml','.md','.json','.example','.js','.html','.css','.txt') or path.name in ('Dockerfile','.gitignore','.dockerignore'):
        text=(root/path).read_text(encoding='utf-8')
        assert not re.search(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{24,})',text), 'Possible credential in '+name
print('Tracked release scope and private credential exclusion verified')
