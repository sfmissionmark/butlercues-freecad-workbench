#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
inlays.py
FreeCAD workbench module for ButlerCues
"""

import FreeCAD as App
import FreeCADGui as Gui
import Draft
import os


import materials
import sketchershapes


def find_object_by_label(doc, label):
    """Find an object in the document by its label.

    Returns:
        The object with the specified label if found, otherwise None.
    """
    for obj in doc.Objects:
        if obj.Label == label:
            return obj
    return None



def create_inlay_document(inlay_type):
    """Create a new document for the specified inlay type"""
    doc = App.ActiveDocument
    if not doc:
        print("No active cue document. Please open/create a cue document first.")
        return
        
    doc_name = f"{inlay_type}_inlay"
    part_object = doc.getObject("handle_part")

    if not doc_name in App.listDocuments().keys():
        new_document(doc_name, inlay_type)

    Gui.setActiveDocument(doc)

    create_sketch(inlay_type)



def new_document(doc_name, inlay_type):
    doc = App.newDocument(doc_name)
    doc.Label = doc_name
    Gui.SendMsgToActiveView("Save")

    #create a Body and Sketch
    body = doc.addObject('PartDesign::Body', f"{inlay_type}_body")
    sketch = body.newObject('Sketcher::SketchObject', f"{inlay_type}_sketch")

    if inlay_type == 'handle':
        sketchershapes.rectangle(sketch, 0.5, 2)
    elif inlay_type == 'forearm':
        sketchershapes.triangle(sketch)
    elif inlay_type == 'butt_sleeve':
        sketchershapes.rectangle(sketch, 0.5, 2)
    else:
        raise ValueError(f"Invalid inlay type: {inlay_type}")
    
    sketchershapes.pad_sketch(sketch, 0.2)


def draw_stock(cue_document_name = "Unnamed", 
               inlay_document_name = "butt_sleeve_inlay"):
    ############################################################################### 
    # Draw stock for inlay in inlay document possibly can use for gcode later
    ###############################################################################
    cue_document = App.getDocument(cue_document_name)
    var_set = cue_document.getObject("CueDimensions")
    inlay_document = App.getDocument(inlay_document_name)

    suffix = "_inlay"
    part_name = inlay_document_name[:-len(suffix)] if inlay_document_name.endswith(suffix) else inlay_document_name
    height = getattr(var_set, f'{part_name}_length').Value / 25.4  # Convert mm to inches
    width = getattr(var_set, f'{part_name}_od').Value / 25.4  # Convert mm to inches

    inlay_document.addObject('PartDesign::Body','Pocket')
    inlay_document.getObject('Pocket').newObject('Sketcher::SketchObject','stock_sketch')
    sketch = inlay_document.getObject('stock_sketch')
    sketch.AttachmentSupport = (inlay_document.getObject('XY_Plane001'),[''])
    sketch.MapMode = 'FlatFace'

    sketchershapes.rectangle(sketch, width, height, 0)
    
    inlay_document.getObject('Pocket').newObject('PartDesign::Pad','stock_pad')
    stock_pad = inlay_document.getObject('stock_pad')
    stock_pad.Profile = (sketch, ['',])
    stock_pad.Length = .25 * 25.4
    stock_pad.TaperAngle = 0.000000
    stock_pad.UseCustomVector = 0
    stock_pad.Direction = (0, 0, 1)
    stock_pad.ReferenceAxis = (sketch, ['N_Axis'])
    stock_pad.AlongSketchNormal = 1
    stock_pad.Reversed = 1
    sketch.Visibility = False

    # inlay_document.purgeTouched()
    inlay_document.recompute()

    


def create_sketch(inlay_type = "handle", inlay_name = None):
    Gui.SendMsgToActiveView("Save")
    if not inlay_name:
        if inlay_type not in ['handle', 'forearm', 'butt_sleeve']:
            print(f"Invalid inlay type: {inlay_type}")
            return

    source_name = f"{inlay_type}_inlay"
    link_name = f"linked_{inlay_type}_Inlay"
    group_name = f"{inlay_type}_group_inlay"

    source_doc = App.getDocument(source_name)
    target_doc = App.activeDocument()
    if not source_doc:
        print(f"Inlay source document '{source_name}' was not found.")
        return
    if not target_doc:
        print("No active target document.")
        return

    # Make group for inlay component
    component_group = target_doc.getObject('CueComponents')
    if not component_group:
        print("CueComponents group was not found in the active document.")
        return
    group = target_doc.addObject('App::DocumentObjectGroup',group_name)
    group.Label = group_name.replace('_', ' ').title()
    group_obj = target_doc.getObject(group_name)
    if not group_obj:
        print(f"Failed to create inlay group '{group_name}'.")
        return
    component_group.addObject(group_obj)

    # Move group into component group
    object_names = [obj.Name for obj in component_group.Group]

    # Find the indices of "handle" and "handle_group"
    try:
        index_handle = object_names.index(inlay_type)
        index_handle_group = object_names.index(group_name)
        # Reorder: Remove "inlay_group" and reinsert it after "inlay"
        if index_handle_group != index_handle + 1:
            handle_group_obj = component_group.Group[index_handle_group]
            component_group.removeObject(handle_group_obj)  # Remove "handle_group"
            component_group.addObject(handle_group_obj)     # Add it back at the end
            reordered_list = component_group.Group[:index_handle + 1] + [handle_group_obj] + component_group.Group[index_handle + 1:-1]
            component_group.Group = reordered_list
    except ValueError:
        print(f"Skipping inlay group reorder for '{inlay_type}': expected objects were not found.")
    #target_doc.getObject(group_name).addObject(target_doc.getObject(inlay_type))

    # create link to inlay object and move to group
    source_object = find_object_by_label(source_doc, 'final_inlay')
    if not source_object:
        source_object = source_doc.getObject(f'{inlay_type}_pad')
    if not source_object:
        print(f"No inlay source object found in '{source_name}'.")
        return
    target_doc.addObject('App::Link', link_name).LinkedObject = source_object
    group_obj.addObject(target_doc.getObject(link_name))

    # Position object to part
    lnk = target_doc.getObject(link_name)
    lnk.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0,0,1), 180))
    lnk.setExpression('.Placement.Base.y', f'.Placement.Base.x + {inlay_type}.Placement.Base.y + CueDimensions.{inlay_type}_length')
    lnk.setExpression(
        '.Placement.Base.z',
        f'(CueDimensions.finish_size_startod + ((CueDimensions.finish_size_endod - CueDimensions.finish_size_startod) / CueDimensions.finish_size_length) * ({inlay_type}.Placement.Base.y + CueDimensions.{inlay_type}_length))/2'
    )

    # Create array of inlays
    array = Draft.make_polar_array(lnk, number=4, angle=360.0, center=App.Vector(0.0, 0.0, 0.0), use_link=True)
    array.Fuse = False
    Draft.autogroup(array)
    array.Axis = (0, 1, 0)
    array.Label = f"{inlay_type}_inlay_array"
    group_obj.addObject(array) # cant seem to add name
    target_doc.recompute()

    # create cut component
    obj = target_doc.getObject(inlay_type)
    if not obj:
        print(f"Target cue component '{inlay_type}' was not found in the active document.")
        return
    texture = None
    if "Texture_URL" in obj.PropertiesList:
        texture = obj.Texture_URL
    cut_obj = target_doc.addObject("Part::Cut", f"{inlay_type} with inlay cuts")
    cut_obj.Tool = array
    cut_obj.Base = target_doc.getObject(inlay_type)
    if texture:
        cut_obj.addProperty("App::PropertyString", "Texture_URL", "Texture", "Texture URL or HDD local path.")
        cut_obj.Texture_URL = texture
        materials.restore_wood()
    group_obj.addObject(cut_obj)

    # create preview inlay
    cut_obj = target_doc.addObject("Part::Common", f"{inlay_type} inlay previews")
    cut_obj.Tool = array
    cut_obj.Base = target_doc.getObject(inlay_type)
    group_obj.addObject(cut_obj)

    target_doc.recompute()




def fillet_for_cnc(noise = None):
    fillet_radius_inch = 0.014
    doc = App.ActiveDocument

    selection = Gui.Selection.getSelection()
    if not selection:
        print("No object selected. Please select an object.")
        return

    selected_object = selection[0]

    obj = selected_object
    print(20*"*")
    print(f"obj {obj}")
    print(20*"*")
    obj_label = obj.Label


    if not obj or not hasattr(obj, "Shape"):
        print(f"Object with label '{obj_label}' not found or is not a valid solid.")
        return

    shape = obj.Shape

    # Convert fillet radius from inches to millimeters
    fillet_radius = fillet_radius_inch * 25.4

    # Identify edges parallel to the Z-axis
    edge_indices = []
    for i, edge in enumerate(shape.Edges):
        print(f"i {i}: edge {edge}: ty {edge.Curve.TypeId}")
        if edge.Curve.TypeId == "Part::GeomLine":
            if edge.Curve.Direction.isParallel(App.Vector(0, 0, 1), 1e-6):
                edge_indices.append(i + 1)  # Collect edge index

    if not edge_indices:
        print("No edges parallel to the Z-axis found.")
        return

    # Apply fillet
    # fillet = shape.makeFillet(fillet_radius, [shape.Edges[i - 1] for i in edge_indices])
    try:
        edges_to_fillet = [shape.Edges[i - 1] for i in edge_indices]
        fillet = shape.makeFillet(fillet_radius, edges_to_fillet)
    except Exception as e:
        print(f"Failed to create fillet: {str(e)}")
        return


    # Hide the original object
    obj.Visibility = False

    # Create a new object to display the filleted solid
    filleted_obj = doc.addObject("Part::Feature", f"{obj.Label}_bit_fillet")
    filleted_obj.Shape = fillet
    filleted_obj.Label = f"{obj.Label}_bit_fillet"

    # Recompute document to reflect changes
    doc.recompute()


def prepare_for_inlay():
    doc = App.ActiveDocument
    selection = Gui.Selection.getSelection()
    if not selection:
        print("No object selected. Please select an object.")
        return
    fillet_for_cnc()




def create_cam_job():
    doc = App.ActiveDocument
    selected_objects = Gui.Selection.getSelection()

    if not selected_objects:
        print("No object selected. Please select an object.")
        return

    inlay_face = selected_objects[0].Name
    user_home = os.path.expanduser("~")
    template_path = os.path.join(
        user_home,
        "Library",
        "Application Support",
        "FreeCAD",
        "Macro",
        "job_6090-pocket.json",
    )

    try:
        import Path
        import Path.Main.Job as PathJob
    except Exception as exc:
        print(f"Path workbench is not available: {exc}")
        return
    obj = doc.getObject("CueComponents")

    sub = obj.getSubObject("Face13")
    selected_objects = Gui.Selection.getSelection()
    PathJob.Create("test", selected_objects, template_path)



    job = Path.Main.Gui.Job.Create([inlay_face], template = template_path, openTaskPanel = False)

    pocket = Path.Op.PocketShape.Create("PocketShape")
    pocket.ToolController = job.ToolController[0]


    # pocket.ToolController = job.ToolController


    # Gui.runCommand('CAM_Pocket_Shape',0, openTaskPanel = False)

# # Make sure something is selected
# selection = Gui.Selection.getSelectionEx()
# if not selection:
#     raise ValueError("No selection found. Please select a face or shape.")

# # Get the selected face
# sel_face = selection[0].SubObjects[0]
# sel_obj = selection[0].Object

# # Make sure there's a Job to attach the operation to
# job = None
# for obj in FreeCAD.ActiveDocument.Objects:
#     if obj.TypeId == 'Path::FeatureJob':
#         job = obj
#         break

# if not job:
#     raise ValueError("No Path Job found in the document. Please create a Job first.")

# # Create the Pocket operation
# pocket_op = PathPocketShape.Create("PocketShape")
# job.PathOperations.addObject(pocket_op)

# # Set up the operation
# pocket_op.Base = (sel_obj, [sel_obj.Shape.Faces.index(sel_face) + 1])  # 1-based index

# # Recompute to reflect changes
# FreeCAD.ActiveDocument.recompute()

    