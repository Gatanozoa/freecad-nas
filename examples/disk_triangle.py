import FreeCAD as App
import Part
import math

diameter = 100.0
thickness = 5.0
hole_diameter = 10.0
hole_spacing = 30.0
radius = hole_spacing / math.sqrt(3.0)
centres = [App.Vector(0, radius, -1),
           App.Vector(-hole_spacing / 2, -radius / 2, -1),
           App.Vector(hole_spacing / 2, -radius / 2, -1)]
doc = App.newDocument("TriangleHoleDisk")
shape = Part.makeCylinder(diameter / 2, thickness)
for centre in centres:
    shape = shape.cut(Part.makeCylinder(hole_diameter / 2, thickness + 2, centre))
result = doc.addObject("Part::Feature", "Disk")
result.Shape = shape
doc.recompute()
expected_volume = math.pi * thickness * ((diameter / 2) ** 2 - 3 * (hole_diameter / 2) ** 2)
assert result.Shape.isValid()
assert len(result.Shape.Solids) == 1
assert abs(result.Shape.Volume - expected_volume) < 0.001
for i in range(3):
    for j in range(i):
        assert abs((centres[i] - centres[j]).Length - hole_spacing) < 0.000001
assert abs(result.Shape.BoundBox.XLength - diameter) < 0.000001
assert abs(result.Shape.BoundBox.YLength - diameter) < 0.000001
assert abs(result.Shape.BoundBox.ZLength - thickness) < 0.000001
