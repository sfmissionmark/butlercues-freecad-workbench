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


def rectangle(sketch = None, width_inches = 0.5, height_inches = 2, distance_y = 0.5):
    if sketch.TypeId != 'Sketcher::SketchObject':
        raise ValueError('Input must be a sketch object')

    # Convert inches to mm (FreeCAD uses mm internally)
    width = width_inches * 25.4
    height = height_inches * 25.4
    y_distance = distance_y * 25.4

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
    sketch.addConstraint(Sketcher.Constraint('DistanceY',-1,1,0,2,distance_y))
    sketch.addConstraint(Sketcher.Constraint('Symmetric',0,1,0,2,-2))

    print(f"Part created successfully ({width_inches}\" x {height_inches}\")")





def diamond(sketch = None, width_inches = 0.5, height_inches = 2):
    if sketch.TypeId != 'Sketcher::SketchObject':
        raise ValueError('Input must be a sketch object')

    # Convert inches to mm (FreeCAD uses mm internally)
    width = width_inches * 25.4 / 2
    height = height_inches * 25.4 / 2

     # Define diamond points (1 inch tall, proportional width)
    top = App.Base.Vector(0, 0.5, 0)   # Top point
    right = App.Base.Vector(0.5, 0, 0) # Right point
    bottom = App.Base.Vector(0, -0.5, 0) # Bottom point
    left = App.Base.Vector(-0.5, 0, 0)  # Left point

      # Add the lines to form the diamond
    line1 = Part.LineSegment(top, right)
    line2 = Part.LineSegment(right, bottom)
    line3 = Part.LineSegment(bottom, left)
    line4 = Part.LineSegment(left, top)
    
    line_ids = [
        sketch.addGeometry(line1, False),
        sketch.addGeometry(line2, False),
        sketch.addGeometry(line3, False),
        sketch.addGeometry(line4, False)
    ]

    # Apply constraints to make sure it's symmetric and properly defined
    # Add the distance constraints for height and width
    sketch.addConstraint(Sketcher.Constraint('DistanceY', line1, 0, 0.5))  # top to center (vertical distance)
    sketch.addConstraint(Sketcher.Constraint('DistanceY', line3, 1, -0.5)) # bottom to center (vertical distance)
    
    sketch.addConstraint(Sketcher.Constraint('DistanceX', line2, 0, 0.5))  # right to center (horizontal distance)
    sketch.addConstraint(Sketcher.Constraint('DistanceX', line4, 1, -0.5)) # left to center (horizontal distance)

    # Add symmetry constraints along both axes to ensure the diamond stays centered
    sketch.addConstraint(Sketcher.Constraint('Symmetric', line1, 0, line3, 0))  # vertical symmetry (top-bottom)
    sketch.addConstraint(Sketcher.Constraint('Symmetric', line2, 0, line4, 0))  # horizontal symmetry (left-right)

    # Recompute and display
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
