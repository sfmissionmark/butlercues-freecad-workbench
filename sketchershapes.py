import FreeCAD as App
import FreeCADGui as Gui
import PartDesignGui
import Part
import Sketcher
import math


def _require_sketch(sketch):
    if sketch is None or getattr(sketch, 'TypeId', '') != 'Sketcher::SketchObject':
        raise ValueError('Input must be a sketch object')


def _add_rectangle(sketch, width_mm, height_mm, construction=False):
    p1 = App.Vector(0, 0)
    p2 = App.Vector(width_mm, 0)
    p3 = App.Vector(width_mm, height_mm)
    p4 = App.Vector(0, height_mm)
    line0 = sketch.addGeometry(Part.LineSegment(p1, p2), construction)
    line1 = sketch.addGeometry(Part.LineSegment(p2, p3), construction)
    line2 = sketch.addGeometry(Part.LineSegment(p3, p4), construction)
    line3 = sketch.addGeometry(Part.LineSegment(p4, p1), construction)
    sketch.addConstraint(Sketcher.Constraint('Coincident', line0, 2, line1, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line1, 2, line2, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line2, 2, line3, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line3, 2, line0, 1))
    sketch.addConstraint(Sketcher.Constraint('Horizontal', line0))
    sketch.addConstraint(Sketcher.Constraint('Horizontal', line2))
    sketch.addConstraint(Sketcher.Constraint('Vertical', line1))
    sketch.addConstraint(Sketcher.Constraint('Vertical', line3))
    sketch.addConstraint(Sketcher.Constraint('DistanceX', line0, 1, line0, 2, width_mm))
    sketch.addConstraint(Sketcher.Constraint('DistanceY', line3, 1, line3, 2, height_mm))
    return (line0, line1, line2, line3)


def _add_bottom_centered_rectangle(sketch, width_mm, height_mm, construction=False):
    half_width = width_mm / 2.0
    left = App.Vector(-half_width, 0)
    origin = App.Vector(0, 0)
    right = App.Vector(half_width, 0)
    top_right = App.Vector(half_width, height_mm)
    top_left = App.Vector(-half_width, height_mm)

    line0 = sketch.addGeometry(Part.LineSegment(left, origin), construction)
    line1 = sketch.addGeometry(Part.LineSegment(origin, right), construction)
    line2 = sketch.addGeometry(Part.LineSegment(right, top_right), construction)
    line3 = sketch.addGeometry(Part.LineSegment(top_right, top_left), construction)
    line4 = sketch.addGeometry(Part.LineSegment(top_left, left), construction)

    sketch.addConstraint(Sketcher.Constraint('Coincident', line0, 2, line1, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line1, 2, line2, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line2, 2, line3, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line3, 2, line4, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line4, 2, line0, 1))
    sketch.addConstraint(Sketcher.Constraint('Horizontal', line0))
    sketch.addConstraint(Sketcher.Constraint('Horizontal', line1))
    sketch.addConstraint(Sketcher.Constraint('Horizontal', line3))
    sketch.addConstraint(Sketcher.Constraint('Vertical', line2))
    sketch.addConstraint(Sketcher.Constraint('Vertical', line4))
    sketch.addConstraint(Sketcher.Constraint('Equal', line0, line1))
    sketch.addConstraint(Sketcher.Constraint('PointOnObject', line0, 2, -1))
    sketch.addConstraint(Sketcher.Constraint('PointOnObject', line0, 2, -2))
    sketch.addConstraint(Sketcher.Constraint('DistanceX', line0, 1, line1, 2, width_mm))
    sketch.addConstraint(Sketcher.Constraint('DistanceY', line2, 1, line2, 2, height_mm))
    return (line0, line1, line2, line3, line4)


def add_outline_box(sketch, outer_width_inches, outer_height_inches):
    _require_sketch(sketch)
    _add_bottom_centered_rectangle(
        sketch,
        outer_width_inches * 25.4,
        outer_height_inches * 25.4,
        construction=True,
    )


def _add_horizontal_barbell(sketch, width_mm, height_mm, center_y_mm, construction=False):
    radius = height_mm / 2.0
    half_width = width_mm / 2.0
    straight_half = max(0.0, half_width - radius)
    left_x = -straight_half
    right_x = straight_half
    top_y = center_y_mm + radius
    bottom_y = center_y_mm - radius

    top_line = sketch.addGeometry(
        Part.LineSegment(App.Vector(left_x, top_y), App.Vector(right_x, top_y)),
        construction,
    )
    right_arc = sketch.addGeometry(
        Part.ArcOfCircle(
            Part.Circle(App.Vector(right_x, center_y_mm), App.Vector(0, 0, 1), radius),
            math.pi / 2.0,
            -math.pi / 2.0,
        ),
        construction,
    )
    bottom_line = sketch.addGeometry(
        Part.LineSegment(App.Vector(right_x, bottom_y), App.Vector(left_x, bottom_y)),
        construction,
    )
    left_arc = sketch.addGeometry(
        Part.ArcOfCircle(
            Part.Circle(App.Vector(left_x, center_y_mm), App.Vector(0, 0, 1), radius),
            -math.pi / 2.0,
            math.pi / 2.0,
        ),
        construction,
    )

    sketch.addConstraint(Sketcher.Constraint('Coincident', top_line, 2, right_arc, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', right_arc, 2, bottom_line, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', bottom_line, 2, left_arc, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', left_arc, 2, top_line, 1))
    sketch.addConstraint(Sketcher.Constraint('Horizontal', top_line))
    sketch.addConstraint(Sketcher.Constraint('Horizontal', bottom_line))
    return (top_line, right_arc, bottom_line, left_arc)


def _add_centered_diamond(sketch, width_mm, height_mm, center_y_mm, construction=False):
    half_width = width_mm / 2.0
    half_height = height_mm / 2.0
    bottom_y = center_y_mm - half_height
    top_y = center_y_mm + half_height

    top = App.Vector(0, top_y)
    right = App.Vector(half_width, center_y_mm)
    bottom = App.Vector(0, bottom_y)
    left = App.Vector(-half_width, center_y_mm)

    anchor = sketch.addGeometry(Part.LineSegment(App.Vector(0, 0), App.Vector(0, bottom_y)), True)
    line0 = sketch.addGeometry(Part.LineSegment(top, right), construction)
    line1 = sketch.addGeometry(Part.LineSegment(right, bottom), construction)
    line2 = sketch.addGeometry(Part.LineSegment(bottom, left), construction)
    line3 = sketch.addGeometry(Part.LineSegment(left, top), construction)

    sketch.addConstraint(Sketcher.Constraint('Vertical', anchor))
    sketch.addConstraint(Sketcher.Constraint('PointOnObject', anchor, 1, -1))
    sketch.addConstraint(Sketcher.Constraint('PointOnObject', anchor, 1, -2))
    sketch.addConstraint(Sketcher.Constraint('DistanceY', anchor, 1, anchor, 2, bottom_y))

    sketch.addConstraint(Sketcher.Constraint('Coincident', anchor, 2, line1, 2))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line0, 2, line1, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line1, 2, line2, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line2, 2, line3, 1))
    sketch.addConstraint(Sketcher.Constraint('Coincident', line3, 2, line0, 1))
    sketch.addConstraint(Sketcher.Constraint('PointOnObject', line0, 1, -2))
    sketch.addConstraint(Sketcher.Constraint('Equal', line0, line1))
    sketch.addConstraint(Sketcher.Constraint('Equal', line1, line2))
    sketch.addConstraint(Sketcher.Constraint('Equal', line2, line3))
    sketch.addConstraint(Sketcher.Constraint('DistanceX', line3, 1, line0, 2, width_mm))
    sketch.addConstraint(Sketcher.Constraint('DistanceY', line1, 2, line0, 1, height_mm))
    return (line0, line1, line2, line3)


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
    _require_sketch(sketch)

    width = width_inches * 25.4
    height = height_inches * 25.4

    _add_rectangle(sketch, width, height, construction=False)
    print(f"Part created successfully ({width_inches}\" x {height_inches}\")")


def handle(sketch=None, width_inches=1.0, height_inches=0.5, outer_width_inches=1.25, outer_height_inches=12.25):
    _require_sketch(sketch)
    add_outline_box(sketch, outer_width_inches, outer_height_inches)
    _add_horizontal_barbell(
        sketch,
        width_inches * 25.4,
        height_inches * 25.4,
        (outer_height_inches * 25.4) / 2.0,
        construction=False,
    )
    print(f"Part created successfully ({width_inches}\" x {height_inches}\")")


def butt_sleeve(sketch=None, width_inches=0.5, height_inches=2, outer_width_inches=1.3, outer_height_inches=3.25):
    _require_sketch(sketch)
    add_outline_box(sketch, outer_width_inches, outer_height_inches)
    _add_centered_diamond(
        sketch,
        width_inches * 25.4,
        height_inches * 25.4,
        (outer_height_inches * 25.4) / 2.0,
        construction=False,
    )
    print(f"Part created successfully ({width_inches}\" x {height_inches}\")")





def diamond(sketch = None, width_inches = 0.5, height_inches = 2):
    _require_sketch(sketch)

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
    _require_sketch(sketch)

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
