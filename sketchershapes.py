import FreeCAD as App
import FreeCADGui as Gui
import PartDesignGui
import Part
import Sketcher


def pad_sketch(sketch, length_inches):
    doc = App.ActiveDocument
    body = doc.getObject(sketch.Name.replace("_sketch", "_body"))
    pad_name = 'pad'    
    if "_sketch" in sketch.Name:
        pad_name = sketch.Name.replace("_sketch", "_pad")
    pad = body.newObject('PartDesign::Pad', pad_name)
    pad.Profile = sketch
    pad.Length = length_inches * 25.4  # Convert inches to mm for extrusion length
    pad.Reversed = True
    doc.recompute()


def rectangle(sketch = None, width_inches = 0.5, height_inches = 2):
    if sketch.TypeId != 'Sketcher::SketchObject':
        raise ValueError('Input must be a sketch object')

    # Convert inches to mm (FreeCAD uses mm internally)
    width = width_inches * 25.4
    height = height_inches * 25.4

    # Add a rectangle to the sketch by drawing four lines
    p1 = App.Vector(0, 0)
    p2 = App.Vector(width, 0)
    p3 = App.Vector(width, height)
    p4 = App.Vector(0, height)
    sketch.addGeometry(Part.LineSegment(p1, p2), False)
    sketch.addGeometry(Part.LineSegment(p2, p3), False)
    sketch.addGeometry(Part.LineSegment(p3, p4), False)
    sketch.addGeometry(Part.LineSegment(p4, p1), False)

    # Fully constrain the rectangle
    sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))  # Top-right to bottom-right
    sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))  # Bottom-right to bottom-left
    sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))  # Bottom-left to top-left
    sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))  # Top-left to top-right
    # sketch.addConstraint(Sketcher.Constraint('Horizontal', 0))  # Bottom edge horizontal
    sketch.addConstraint(Sketcher.Constraint('Horizontal', 2))  # Top edge horizontal
    sketch.addConstraint(Sketcher.Constraint('Vertical', 1))  # Right edge vertical
    sketch.addConstraint(Sketcher.Constraint('Vertical', 3))  # Left edge vertical
    sketch.addConstraint(Sketcher.Constraint('DistanceX', 0, 1, 0, 2, width))  # Width of rectangle
    sketch.addConstraint(Sketcher.Constraint('DistanceY', 0, 1, 3, 1, height))  # Height of rectangle
    sketch.addConstraint(Sketcher.Constraint('DistanceY',-1,1,0,2,22.530000))
    sketch.addConstraint(Sketcher.Constraint('Symmetric',0,1,0,2,-2))

    print(f"Part created successfully ({width_inches}\" x {height_inches}\")")


def diamond(sketch = None, width_inches = 0.5, height_inches = 2):
    if sketch.TypeId != 'Sketcher::SketchObject':
        raise ValueError('Input must be a sketch object')

    # Convert inches to mm (FreeCAD uses mm internally)
    width = width_inches * 25.4 / 2
    height = height_inches * 25.4 / 2

    # Create diamond points from center
    p1 = App.Vector(0, height/2)  # top
    p2 = App.Vector(width/2, 0)   # right
    p3 = App.Vector(0, -height/2) # bottom
    p4 = App.Vector(-width/2, 0)  # left

    # Add four lines to form diamond
    sketch.addGeometry(Part.LineSegment(p1, p2), False)
    sketch.addGeometry(Part.LineSegment(p2, p3), False)
    sketch.addGeometry(Part.LineSegment(p3, p4), False)
    sketch.addGeometry(Part.LineSegment(p4, p1), False)

    # Add constraints to ensure diamond shape
    sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 3, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', 3, 2, 0, 1))
    sketch.addConstraint(Sketcher.Constraint('Equal', 0, 1))  # All sides equal
    sketch.addConstraint(Sketcher.Constraint('Equal', 1, 2))
    sketch.addConstraint(Sketcher.Constraint('Equal', 2, 3))
    sketch.addConstraint(Sketcher.Constraint('DistanceX', 0, 1, 0, 2, width))  # Width constraint
    sketch.addConstraint(Sketcher.Constraint('DistanceY', 0, 1, 2, 1, height)) # Height constraint
    sketch.addConstraint(Sketcher.Constraint('Vertical',1,2,0,1))
    sketch.addConstraint(Sketcher.Constraint('Vertical',0,1,-1,1))
    sketch.addConstraint(Sketcher.Constraint('DistanceY',0,1,-1,1,-22.994079))

    print(f"Diamond created successfully ({width_inches}\" x {height_inches}\")")



def triangle(sketch = None, width_inches = 0.5, height_inches = 4):
    if sketch.TypeId != 'Sketcher::SketchObject':
        raise ValueError('Input must be a sketch object')

    # Convert inches to mm (FreeCAD uses mm internally)
    width = width_inches * 25.4/2
    height = height_inches * 25.4

    # Create triangle points
    p1 = App.Vector(0, height)  # top
    p2 = App.Vector(width/2, 0)  # right
    p3 = App.Vector(-width/2, 0)  # left

    # Add three lines to form triangle
    sketch.addGeometry(Part.LineSegment(p1, p2), False)
    sketch.addGeometry(Part.LineSegment(p2, p3), False)
    sketch.addGeometry(Part.LineSegment(p3, p1), False)

    # Add constraints to ensure triangle shape
    sketch.addConstraint(Sketcher.Constraint('Coincident', 0, 2, 1, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', 1, 2, 2, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', 2, 2, 0, 1))
    sketch.addConstraint(Sketcher.Constraint('DistanceX', 0, 1, 0, 2, width))  # Width constraint
    sketch.addConstraint(Sketcher.Constraint('DistanceY', 0, 1, 2, 1, height))  # Height constraint
    sketch.addConstraint(Sketcher.Constraint('Vertical',1,2,-1,1))
    sketch.addConstraint(Sketcher.Constraint('Symmetric',0,2,0,1,-2))
    sketch.addConstraint(Sketcher.Constraint('DistanceY',0,1,0))

    print(f"Triangle created successfully ({width_inches}\" x {height_inches}\")")