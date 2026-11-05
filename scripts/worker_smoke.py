"""Executed inside the built image using FreeCAD's own Python, without networking."""
import math
from pathlib import Path
from cad_service.worker import Executor

script='''import FreeCAD as App
import Part
doc = App.newDocument("ReleaseSmoke")
result = doc.addObject("Part::Feature", "Plate")
result.Shape = Part.makeBox(60,30,5).cut(Part.makeCylinder(3,7,App.Vector(30,15,-1)))
doc.recompute()
'''
folder=Path('/artifacts/00000000-0000-4000-8000-000000000001')
status,report,error=Executor(timeout=90).run(folder,script)
assert status=='succeeded',error
assert all(abs(a-b)<1e-5 for a,b in zip(report['dimensions'],[60,30,5]))
assert abs(report['volume']-(9000-math.pi*9*5))<1e-4
assert report['solids']==1
for name in ('model.FCStd','model.step','model.stl','report.json'):
    assert (folder/name).stat().st_size>0,name
print('Actual FreeCAD solid, dimensions, volume and FCStd/STEP/STL exports verified')
