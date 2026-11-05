"""Executed by the Python interpreter bundled with FreeCAD, one process per job."""
import glob
import json
import math
import os
from pathlib import Path
import resource
import sys
import traceback

from cad_service.policy import validate


def audit_guard(work, home):
    roots = (work.resolve(), home.resolve())
    state_root = Path('/state').resolve()
    artifact_root = Path('/artifacts').resolve()
    temporary_root = Path('/tmp/cad').resolve()
    def within(path, root):
        return path == root or root in path.parents
    def path_check(value, write=False):
        if value is None or isinstance(value, int):
            return
        path = Path(os.fsdecode(value)).resolve()
        if within(path, state_root):
            raise PermissionError('CAD scripts cannot access queue state')
        if (within(path, artifact_root) or within(path, temporary_root)) and not any(within(path, root) for root in roots):
            raise PermissionError('CAD scripts cannot access other jobs')
        if write and not any(within(path, root) for root in roots):
            raise PermissionError('CAD script writes are restricted to its job directory')
    def hook(event, args):
        if event.startswith('socket.') or event in {'subprocess.Popen', 'os.system', 'os.exec', 'os.fork', 'os.posix_spawn', 'ctypes.dlopen', 'ctypes.dlsym'}:
            raise PermissionError('CAD scripts cannot create network connections or processes')
        if event == 'open':
            path, mode, flags = args
            writing = any(letter in (mode or '') for letter in 'wax+') or bool((flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            path_check(path, writing)
        elif event in {'os.link', 'os.symlink'}:
            raise PermissionError('File links are not allowed')
        elif event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.chown', 'os.truncate', 'os.utime'}:
            path_check(args[0], True)
        elif event == 'os.rename':
            path_check(args[0], True)
            path_check(args[1], True)
        elif event in {'os.listdir', 'os.scandir'}:
            path_check(args[0])
    sys.addaudithook(hook)


def build(folder):
    folder = Path(folder).resolve()
    script = (folder / 'source.py').read_text(encoding='utf-8')
    validate(script)
    bundle = '/opt/freecad/usr'
    sys.path += [bundle + '/lib', bundle + '/Mod'] + glob.glob(bundle + '/Mod/*')
    import FreeCAD as App
    import Part
    import MeshPart
    os.chdir(folder)
    audit_guard(folder, Path(os.environ['HOME']))
    scope = {'App': App, 'Part': Part, '__name__': '__main__'}
    exec(compile(script, 'source.py', 'exec'), scope)
    obj = scope.get('result')
    if obj is None or not hasattr(obj, 'Shape') or not hasattr(obj, 'Document'):
        raise ValueError('Define result as the final FreeCAD document object with Shape')
    doc = obj.Document
    doc.recompute()
    if any('Invalid' in item.State for item in doc.Objects):
        raise ValueError('Invalid feature in document')
    shape = obj.Shape
    if shape.isNull() or not shape.isValid() or not shape.Solids or not math.isfinite(shape.Volume) or shape.Volume <= 0:
        raise ValueError('Result must contain valid solids with positive volume')
    dimensions = [shape.BoundBox.XLength, shape.BoundBox.YLength, shape.BoundBox.ZLength]
    if not all(math.isfinite(value) and 0 < value <= 2000 for value in dimensions):
        raise ValueError('Bounding dimensions must be within (0, 2000] mm')
    doc.saveAs(str(folder / 'model.FCStd'))
    Part.export([obj], str(folder / 'model.step'))
    mesh = MeshPart.meshFromShape(Shape=shape, LinearDeflection=0.15, AngularDeflection=0.35, Relative=False)
    if mesh.CountFacets > 500000:
        raise ValueError('Mesh exceeds 500000 triangles; simplify geometry')
    mesh.write(str(folder / 'model.stl'))
    report = {'unit': 'mm', 'dimensions': dimensions, 'volume': shape.Volume, 'solids': len(shape.Solids),
              'triangles': mesh.CountFacets, 'freecad_version': '.'.join(App.Version()[:3]),
              'files': ['source.py', 'model.FCStd', 'model.step', 'model.stl', 'report.json']}
    (folder / 'report.json').write_text(json.dumps(report), encoding='utf-8')
    App.closeDocument(doc.Name)


if __name__ == '__main__':
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024,) * 2)
        cpu = int(os.getenv('CAD_TIMEOUT', '180')) + 10
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        build(sys.argv[1])
    except Exception:
        traceback.print_exc()
        sys.exit(1)
