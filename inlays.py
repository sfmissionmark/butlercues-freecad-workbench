#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
inlays.py
FreeCAD workbench module for ButlerCues
"""

import FreeCAD as App
import Part
import FreeCADGui as Gui
import Draft
import Sketcher

import materials
import part
import sketchershapes


def find_object_by_label(doc, label):
    """Find an object in the document by its label"""
    for obj in doc.Objects:
        if obj.Label == label:
            return obj
    return None



def create_inlay_document(inlay_type):
    """Create a new document for the specified inlay type"""
    doc = App.ActiveDocument
    doc_name = f"{inlay_type}_inlay"

    if not doc_name in App.listDocuments().keys():
        new_document(doc_name, inlay_type)

    Gui.setActiveDocument(doc)

    insert(inlay_type)
    offset_and_trim(inlay_type)



def new_document(doc_name, inlay_type):
        doc = App.newDocument(doc_name)
        doc.Label = doc_name
        Gui.SendMsgToActiveView("Save")

        #create a Body and Sketch
        body = doc.addObject('PartDesign::Body', f"{inlay_type}_body")
        sketch = body.newObject('Sketcher::SketchObject', f"{inlay_type}_sketch")
        sketch.ViewObject.Visibility = False

        if inlay_type == 'handle':
            sketchershapes.diamond(sketch, 0.5, 2)
        elif inlay_type == 'forearm':
            sketchershapes.triangle(sketch)
        elif inlay_type == 'butt_sleeve':
            sketchershapes.rectangle(sketch, 0.5, 2)
        else:
            raise ValueError(f"Invalid inlay type: {inlay_type}")
        
        sketchershapes.pad_sketch(sketch, 0.2)


def insert(inlay_type = "forearm"):
    """Insert an inlay of specified type ('handle', 'forearm', 'butt_sleeve)"""
    Gui.SendMsgToActiveView("Save")
    
    if inlay_type not in ['handle', 'forearm', 'butt_sleeve']:
        print(f"Invalid inlay type: {inlay_type}")
        return
        
    source_name = f"{inlay_type}_inlay"
    link_name = f"linked_{inlay_type}_Inlay"
    part_name = f"{inlay_type}_part"
    datum_name = f"{inlay_type}_end_datumpoint"
    constraint_name = f"{inlay_type}001"
    od_param = f"{inlay_type}_od"
    
    source_doc = App.getDocument(source_name)
    target_doc = App.activeDocument()

    if not source_doc and target_doc:
        raise ValueError("Source or target document not found.")

    source_object = find_object_by_label(source_doc, f"{inlay_type}_body")#'final_inlay')
    if not source_object:
        raise ValueError(f"Inlay object not found in {source_name}. This needs to be named {inlay_type}_body.")

    part_folder = target_doc.getObject(part_name)
    new_group = target_doc.addObject("App::DocumentObjectGroup", f"{inlay_type}_inlay")
    part_folder.addObject(new_group)

    target_doc.addObject('App::Link', link_name).LinkedObject = source_object
    new_group.addObject(target_doc.getObject(link_name))
    linked_obj = target_doc.getObject(link_name)
    linked_obj.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0,0,1), 90))
    linked_obj.setExpression('.Placement.Base.x', f'{datum_name}.Placement.Base.x')
    linked_obj.setExpression('.Placement.Base.z', f'{constraint_name}.Constraints.{od_param}')

    array = Draft.make_polar_array(linked_obj, number=4, angle=360.0, center=App.Vector(0.0, 0.0, 0.0), use_link=True)
    array.Fuse = False
    Draft.autogroup(array)
    array.Axis = (1, 0, 0)
    array.Label = f"{inlay_type}_inlay_array"
    new_group.addObject(array)

    target_doc.recompute()




def offset_and_trim(part_name='forearm'):
    doc = App.activeDocument()

    # Create preview_inlays group if it doesn't exist
    preview_group = doc.getObject('preview_inlays')
    if not preview_group:
        preview_group = doc.addObject("App::DocumentObjectGroup", "preview_inlays")

    start_od = doc.getObject('CueDimensions').finish_size_startod
    end_od = doc.getObject('CueDimensions').finish_size_endod
    offset = App.Units.Quantity('0.008 in')
    doc.getObject('CueDimensions').finish_size_startod = start_od + offset
    doc.getObject('CueDimensions').finish_size_endod = end_od + offset
    App.ActiveDocument.recompute()
    
    part = find_object_by_label(doc, f"{part_name}_part")
    part_solid_name = f"{part_name}_part_solid"
    __s__=part.Shape.Faces
    __s__=Part.Solid(Part.Shell(__s__))
    __o__=App.ActiveDocument.addObject("Part::Feature", part_solid_name)
    __o__.Label=part_solid_name
    __o__.Shape=__s__
    preview_group.addObject(__o__)

    inlay = find_object_by_label(doc, f"{part_name}_inlay_array")
    inlay_solid_name = f"{part_name}_inlay_solid"
    __s__=inlay.Shape.Faces
    __s__=Part.Solid(Part.Shell(__s__))
    __o__=App.ActiveDocument.addObject("Part::Feature", inlay_solid_name)
    __o__.Label=inlay_solid_name
    __o__.Shape=__s__
    preview_group.addObject(__o__)

    from BOPTools import BOPFeatures
    bp = BOPFeatures.BOPFeatures(App.activeDocument())
    common = bp.make_multi_common([part_solid_name, inlay_solid_name, ])
    common.Label = f"{part_name}_inlay_common"
    preview_group.addObject(common)
    inlay.Visibility = False

    materials.setMaterial(optional_parts=[common])
    doc.getObject('CueDimensions').finish_size_startod = start_od
    doc.getObject('CueDimensions').finish_size_endod = end_od

    App.ActiveDocument.recompute()



def update_all_previews():

    doc = App.ActiveDocument
    group = doc.getObject("preview_inlays")

    if group:
        # Recursively delete children
        for child in group.Group:
            App.ActiveDocument.removeObject(child.Name)

        for obj in App.ActiveDocument.Objects:
            if "_solid" in obj.Label.lower():
                App.ActiveDocument.removeObject(obj.Name)

    group = doc.getObject("inlays")
    if group:
        for child in group.Group:
            offset_and_trim(child.Name.strip("_inlay"))



def getTopSurface(object_name = "Fusion"):
    # Get the active document and the Pad object
    doc = App.ActiveDocument
    pad = doc.getObject(object_name)  # Replace "Pad" with the actual name of your pad

    # Access the shape of the Pad
    shape = pad.Shape

    # Define a tolerance for detecting the "top" face (change as needed)
    z_tolerance = 0.01
    top_faces = []

    # Find the maximum Z value among the faces
    max_z = max(face.BoundBox.Center.z for face in shape.Faces)

    # Select faces close to the maximum Z value
    for face in shape.Faces:
        if abs(face.BoundBox.Center.z - max_z) < z_tolerance:
            top_faces.append(face)

    # Combine the top faces into a single compound shape
    if top_faces:
        compound = Part.Compound(top_faces)

        # Create a refined face from the compound
        refined_face = compound.Faces[0]
        for face in compound.Faces[1:]:
            refined_face = refined_face.fuse(face)

        # Add the compound shape to the document
        compound_obj = doc.addObject("Part::Feature", "TopFaces")
        compound_obj.Label = "Top Faces"
        # Replace the compound with the simplified face
        compound_obj.Shape = refined_face
        upgraded_obj = Draft.upgrade([compound_obj])[0]

        # Convert the upgraded object to a sketch
        sketch = Draft.makeSketch(upgraded_obj, autoconstraints=True)
        sketch.Label = "TopFaceSketch"

        # Recompute the document to reflect the changes
        doc.recompute()

        print("Top faces extracted and filleted successfully.")
    else:
        print("No top faces found.")

    return upgraded_obj




def fillet_for_cnc(selected_object = None, fillet_radius_inch = 0.014):
    doc = App.ActiveDocument

    if not selected_object:
        print("No object selected. Please select an object.")
        return
    obj = selected_object
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
    fillet = shape.makeFillet(fillet_radius, [shape.Edges[i - 1] for i in edge_indices])

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
    fillet_for_cnc(selection[0])



def insert_sketch_link(inlay_type = "forearm"):
    doc = App.ActiveDocument
    link_name = f'{inlay_type}_sketch'
    datum_name = f'{inlay_type}_end_datumpoint'
    part_name = f'{inlay_type}_part'
    constraint_name = f'{inlay_type}001'
    od_param = f'{inlay_type}_od'

    doc.addObject('App::Link', link_name).setLink(App.getDocument(f"{inlay_type}_inlay").forearm_body)
    linked_obj = doc.getObject(link_name)
    linked_obj.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0,0,1), 180))
    linked_obj.setExpression('.Placement.Base.y', f'{datum_name}.Placement.Base.x + {part_name}.Placement.Base.y')
    linked_obj.setExpression('.Placement.Base.z', f'{constraint_name}.Constraints.{od_param}')

    array = Draft.make_polar_array(linked_obj, number=4, angle=360.0, center=App.Vector(0.0, 0.0, 0.0), use_link=True)
    array.Fuse = False
    Draft.autogroup(array)
    array.Axis = (0, 1, 0)
    array.Label = f"{inlay_type}_inlay_array"
    array.Placement = App.Placement(App.Vector(0,0,0),App.Rotation(App.Vector(0,0,1),-90))
    body = doc.getObject(part_name)
    body.addObject(array)

    doc.recompute()