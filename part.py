#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
part.py
FreeCAD workbench module for ButlerCues
"""

__title__ = "part"
__author__ = "Mark Butler"
__version__ = "0.1.0"
__date__ = "2025"

import FreeCAD as App
import Part
import Sketcher
import common
import FreeCADGui as Gui
import dimensions

def drawLine(sketch, horizontal = True):
    if horizontal:
        p1 = App.Vector(-1, 10, 0)
        p2 = App.Vector(1, 10, 0)
    else:
        p1 = App.Vector(10, -1, 0)
        p2 = App.Vector(10, 1, 0)
    line_segment = sketch.addGeometry(Part.LineSegment(p1, p2))
    line_number = (line_segment,) # This is a tuple
    start_vertex = (line_segment, 1)
    end_vertex = (line_segment, 2)
    sketch.recompute()
    return line_number, start_vertex, end_vertex



def var_set():
    doc = App.activeDocument()
    if not doc:
        App.newDocument()
        doc = App.activeDocument()
    
    Gui.runCommand('Std_ViewGroup',3) # View from y,x plane
    # Switch to the 3D view in FreeCAD
    Gui.runCommand('Std_ViewGroup',3)
    var_set_name = "CueDimensions"

    dimensions_dict = {name: value for name, value in vars(dimensions).items() 
                      if not name.startswith('__')}

    var_set = doc.getObject(var_set_name)
    if not var_set:
        var_set = doc.addObject("App::VarSet", var_set_name)
        for section, values in dimensions_dict.items():
            for key, value in values.items():
                property_name = section + "_" + key
                var_set.addProperty("App::PropertyLength", property_name, group=section, doc=f"{property_name} Length")
                setattr(var_set, property_name, inchToMM(value))

    return var_set


def inchToMM(num):
    if type(num) == str:
        num = float(num.split()[0])
    else:
        num = float(num)
    return round(num*25.4, 2)		


def find_last_part_position():
    doc = App.activeDocument()
    last_part = None
    for obj in doc.Objects:
        if obj.TypeId == "PartDesign::Point":
            last_part = obj
    if not last_part: return 0
    return last_part.getGlobalPlacement().Base.y


def drawJointCap():
    v = var_set()
    
    doc = App.activeDocument()
    section_name = "joint_cap"
    container_folder = doc.getObject('Cue_Parts')
    if not container_folder:
        container_folder = doc.addObject("App::DocumentObjectGroup", "Cue_Parts")
    part = doc.addObject('App::Part', section_name + "_" + "part")
    container_folder.addObject(part)
    cue_dimensions = doc.getObject("CueDimensions")
    part.Placement = App.Placement(App.Vector(0,0,0),App.Rotation(App.Vector(0,0,1),90))
 
    body = part.newObject('PartDesign::Body', section_name)
    sketch = body.newObject('Sketcher::SketchObject', section_name)
    part_length = getattr(cue_dimensions, section_name + "_length")
    start_length = App.Units.Quantity(str(find_last_part_position()) + " mm")
    end_length = start_length + part_length

    # DRAW LEFT SIDE 
    left_side_line_number, left_side_start_vertex, left_side_end_vertex = drawLine(sketch, horizontal=False)
    sketch.addConstraint(Sketcher.Constraint('Vertical', *left_side_line_number))
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceY', *left_side_end_vertex, 10))
    left_formula = f"((((CueDimensions.finish_size_endod - CueDimensions.finish_size_startod) / CueDimensions.finish_size_length) * {start_length}) + CueDimensions.finish_size_startod) /2"
    sketch.setExpression(f'Constraints[{constraint}]', left_formula)
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceX', *left_side_end_vertex, 0))

    # DRAW TOP SIDE 
    top_side_line_number, top_side_start_vertex, top_side_end_vertex = drawLine(sketch, horizontal=True)
    sketch.addConstraint(Sketcher.Constraint('Coincident', *left_side_end_vertex, *top_side_start_vertex))
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceX', -1, 1, *top_side_end_vertex, 2))
    sketch.renameConstraint(constraint, f"{section_name}_length")
    sketch.setExpression(f'Constraints[{constraint}]', u'CueDimensions.joint_cap_length')
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceY', -1, 1, *top_side_end_vertex, 2))
    sketch.renameConstraint(constraint, f"{section_name}_od")
    top_formula = f"((((CueDimensions.finish_size_endod - CueDimensions.finish_size_startod) / CueDimensions.finish_size_length) * {end_length}) + CueDimensions.finish_size_startod) /2"
    sketch.setExpression(f'Constraints[{constraint}]', top_formula) 

    # DRAW RIGHT SIDE 
    right_side_line_number, right_side_start_vertex, right_side_end_vertex = drawLine(sketch, horizontal=False)
    sketch.addConstraint(Sketcher.Constraint('Vertical', *right_side_line_number))
    sketch.addConstraint(Sketcher.Constraint('Coincident', *right_side_end_vertex, *top_side_end_vertex))
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceY', -1, 1, *right_side_start_vertex ,10))
    sketch.setExpression(f'Constraints[{constraint}]', u'CueDimensions.joint_cap_id /2')

    # DRAW INSIDE TOP
    inside_top_line_number, inside_top_start_vertex, inside_top_end_vertex = drawLine(sketch, horizontal=True)
    sketch.addConstraint(Sketcher.Constraint('Horizontal', *inside_top_line_number))
    sketch.addConstraint(Sketcher.Constraint('Coincident', *right_side_start_vertex, *inside_top_end_vertex))
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceX', *inside_top_start_vertex, *inside_top_end_vertex,10))
    sketch.setExpression(f'Constraints[{constraint}]', u'CueDimensions.joint_cap_bore_depth')

    # DRAW INSIDE LEFT
    inside_left_line_number, inside_left_start_vertex, inside_left_end_vertex = drawLine(sketch, horizontal=False)
    sketch.addConstraint(Sketcher.Constraint('Vertical',   *inside_left_line_number))
    sketch.addConstraint(Sketcher.Constraint('Coincident', *inside_left_start_vertex, *inside_top_start_vertex))
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceY', -1, 1, *inside_left_end_vertex,-1))
    sketch.setExpression(f'Constraints[{constraint}]', u'CueDimensions.joint_cap_bleedhole_id')

    # DRAW BOTTOM
    bottom_line_number, bottom_start_vertex, bottom_end_vertex = drawLine(sketch, horizontal=True)
    sketch.addConstraint(Sketcher.Constraint('Horizontal', *bottom_line_number))
    sketch.addConstraint(Sketcher.Constraint('Coincident', *bottom_start_vertex, *inside_left_end_vertex))
    sketch.addConstraint(Sketcher.Constraint('Coincident', *bottom_end_vertex, *left_side_start_vertex))
    sketch.recompute()

    revolution_name = 'Revolution_' + section_name
    revolution = body.newObject('PartDesign::Revolution',revolution_name)
    revolution.Profile = sketch
    revolution.Angle = 360.0
    revolution.Reversed = 1
    revolution.ReferenceAxis = (sketch, ['H_Axis'])
    revolution.Midplane = 0
    sketch.Visibility = False
    App.activeDocument().recompute()

    d = body.newObject('PartDesign::Point', f'{section_name}_end_datumpoint')
    d.AttachmentSupport = [(revolution,'Edge3')]
    d.MapMode = 'CenterOfCurvature'
    doc.getObject(d.Label).Label = f'{section_name}_end_datumpoint'
    App.activeDocument().recompute()

    Gui.SendMsgToActiveView("ViewFit")
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(part)


def cue_part(section_name = "joint_ring_pad"):
    v = var_set()

    if section_name == "joint_cap":
        drawJointCap()
        return  

    if not hasattr(v, f'{section_name}_length'):
        raise ValueError(f"dimension for {section_name} not in varset")

    doc = App.activeDocument()
    container_folder = doc.getObject('Cue_Parts')
    if not container_folder:
        container_folder = doc.addObject("App::DocumentObjectGroup", "Cue_Parts")
    for obj in reversed(doc.Objects):
        if obj.TypeId == 'PartDesign::Point':
            last_part = obj.Name
            break

    part = doc.addObject('App::Part', section_name + "_" + "part")
    container_folder.addObject(part)
    start_length = App.Units.Quantity(str(find_last_part_position()) + " mm")
    cue_dimensions = doc.getObject("CueDimensions")
    part_length = getattr(cue_dimensions, section_name + "_length")
    end_length = start_length + part_length
    part.Placement = App.Placement(App.Vector(0, start_length, 0),App.Rotation(App.Vector(0,0,1),90))

    body = part.newObject('PartDesign::Body', section_name)
    sketch = body.newObject('Sketcher::SketchObject', section_name)

    # DRAW LEFT SIDE
    left_side_line_number, left_side_start_vertex, left_side_end_vertex = drawLine(sketch, horizontal=False)
    sketch.addConstraint(Sketcher.Constraint('Vertical', *left_side_line_number))
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceY', *left_side_end_vertex, 10))
    left_formula = f"((((CueDimensions.finish_size_endod - CueDimensions.finish_size_startod) / CueDimensions.finish_size_length) * {start_length}) + CueDimensions.finish_size_startod) /2"
    sketch.setExpression(f'Constraints[{constraint}]', left_formula)
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceY', *left_side_start_vertex, 20))
    sketch.setExpression(f'Constraints[{constraint}]', f'CueDimensions.{section_name}_id/2')
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceX', *left_side_start_vertex, 0))

    # DRAW TOP SIDE
    top_side_line_number, top_side_start_vertex, top_side_end_vertex = drawLine(sketch, horizontal=True)
    sketch.recompute()
    sketch.addConstraint(Sketcher.Constraint('Coincident', *top_side_start_vertex, *left_side_end_vertex))
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceX', *top_side_end_vertex, -10))
    sketch.renameConstraint(constraint, f"{section_name}_length")
    sketch.setExpression(f'Constraints[{constraint}]', f'CueDimensions.{section_name}_length')
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceY', *top_side_end_vertex, -10))
    sketch.renameConstraint(constraint, f"{section_name}_od")
    top_formula = f"((((CueDimensions.finish_size_endod - CueDimensions.finish_size_startod) / CueDimensions.finish_size_length) * {end_length}) + CueDimensions.finish_size_startod) /2"
    sketch.setExpression(f'Constraints[{constraint}]', top_formula)


    # DRAW RIGHT SIDE
    right_side_line_number, right_side_start_vertex, right_side_end_vertex = drawLine(sketch, horizontal=False)
    sketch.addConstraint(Sketcher.Constraint('Coincident', *top_side_end_vertex, *right_side_start_vertex))
    sketch.addConstraint(Sketcher.Constraint('Vertical', *right_side_line_number))
    constraint = sketch.addConstraint(Sketcher.Constraint('DistanceY', *right_side_end_vertex, 2))
    sketch.setExpression(f'Constraints[{constraint}]', f'CueDimensions.{section_name}_id/2')

    # DRAW BOTTOM
    bottom_side_line_number, bottom_side_start_vertex, bottom_side_end_vertex = drawLine(sketch, horizontal=True)
    sketch.addConstraint(Sketcher.Constraint('Coincident', *bottom_side_start_vertex, *left_side_start_vertex))
    sketch.addConstraint(Sketcher.Constraint('Coincident', *bottom_side_end_vertex, *right_side_end_vertex))
    sketch.recompute()
    App.activeDocument().recompute()

    # REVOLVE THE SKETCH
    revolution_name = 'Revolution_' + section_name
    revolution = body.newObject('PartDesign::Revolution',revolution_name)
    revolution.Profile = sketch
    revolution.Angle = 360.0
    revolution.Reversed = 1
    revolution.ReferenceAxis = (sketch, ['H_Axis'])
    revolution.Midplane = 0
    sketch.Visibility = False
    App.activeDocument().recompute()

    d = body.newObject('PartDesign::Point',f'{section_name}_start_datumpoint')
    doc.getObject(d.Label).Label = f'{section_name}_start_datumpoint'
    d.AttachmentSupport = [(revolution,'Edge1')]
    d.MapMode = 'CenterOfCurvature'

    App.activeDocument().recompute()
    d = body.newObject('PartDesign::Point',f'{section_name}_end_datumpoint')
    d.AttachmentSupport = [(revolution,'Edge5')]
    d.MapMode = 'CenterOfCurvature'
    doc.getObject(d.Label).Label = f'{section_name}_end_datumpoint'

    App.activeDocument().recompute()
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(part)
