#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import FreeCAD as App
import FreeCADGui as Gui
import Draft

import dimensions
import materials
import sketchershapes


def _get_inlay_depth_inches(cue_doc, inlay_type, requested_depth_inches=0.2, clearance_inches=0.01):
    var_set = cue_doc.getObject("CueDimensions") if cue_doc else None
    if not var_set:
        return requested_depth_inches

    try:
        od_mm = getattr(var_set, f"{inlay_type}_od").Value
        id_mm = getattr(var_set, f"{inlay_type}_id").Value
    except Exception:
        return requested_depth_inches

    wall_thickness_inches = max(0.0, (od_mm - id_mm) / 2.0 / 25.4)
    max_depth_inches = max(0.01, wall_thickness_inches - clearance_inches)
    clamped_depth = min(requested_depth_inches, max_depth_inches)

    if clamped_depth < requested_depth_inches:
        print(
            f"Clamped {inlay_type} inlay depth from {requested_depth_inches:.3f}in to {clamped_depth:.3f}in "
            f"to stay within wall thickness."
        )

    return clamped_depth


def _get_inlay_outline_size_inches(cue_doc, inlay_type):
    defaults = dimensions.cue_dimensions().get(inlay_type, {})

    def _quantity_to_inches(value, fallback):
        try:
            return float(value.Value) / 25.4
        except Exception:
            pass
        try:
            return float(App.Units.Quantity(str(value)).Value) / 25.4
        except Exception:
            pass
        return float(fallback)

    default_width = _quantity_to_inches(defaults.get('od', 1.0), 1.0)
    default_height = _quantity_to_inches(defaults.get('length', 1.0), 1.0)

    var_set = cue_doc.getObject("CueDimensions") if cue_doc else None
    if not var_set:
        return default_width, default_height

    try:
        width_inches = getattr(var_set, f"{inlay_type}_od").Value / 25.4
        height_inches = getattr(var_set, f"{inlay_type}_length").Value / 25.4
        return float(width_inches), float(height_inches)
    except Exception:
        return default_width, default_height


def _get_inlay_source_object(inlay_type):
    source_name = f"{inlay_type}_inlay"
    try:
        source_doc = App.getDocument(source_name)
    except Exception:
        source_doc = None
    if not source_doc:
        return None, None

    source_object = find_object_by_label(source_doc, "final_inlay")
    if not source_object:
        source_object = source_doc.getObject(f"{inlay_type}_pad")
    return source_doc, source_object


def find_object_by_label(doc, label):
    """Find an object in the document by its label.

    Returns:
        The object with the specified label if found, otherwise None.
    """
    for obj in doc.Objects:
        if obj.Label == label:
            return obj
    return None


def _apply_butler_post_defaults(job_obj, post_name="butler_fluidnc", post_args="--tool_change --inch"):
    if not job_obj:
        return
    try:
        if hasattr(job_obj, "PostProcessor"):
            job_obj.PostProcessor = post_name
        if hasattr(job_obj, "PostProcessorArgs"):
            job_obj.PostProcessorArgs = post_args
    except Exception as exc:
        print(f"Warning: unable to set CAM post processor defaults: {exc}")


def create_inlay_document(inlay_type):
    """Create a new document for the specified inlay type"""
    doc = App.ActiveDocument
    if not doc:
        print("No active cue document. Please open/create a cue document first.")
        return

    doc_name = f"{inlay_type}_inlay"

    if doc_name not in App.listDocuments().keys():
        depth_inches = _get_inlay_depth_inches(doc, inlay_type)
        outline_width_inches, outline_height_inches = _get_inlay_outline_size_inches(doc, inlay_type)
        new_document(doc_name, inlay_type, depth_inches, outline_width_inches, outline_height_inches)

    Gui.setActiveDocument(doc)
    create_sketch(inlay_type)


def new_document(doc_name, inlay_type, inlay_depth_inches=0.2, outline_width_inches=None, outline_height_inches=None):
    doc = App.newDocument(doc_name)
    doc.Label = doc_name
    Gui.SendMsgToActiveView("Save")

    body = doc.addObject("PartDesign::Body", f"{inlay_type}_body")
    sketch = body.newObject("Sketcher::SketchObject", f"{inlay_type}_sketch")
    outline_width_inches = outline_width_inches or 1.0
    outline_height_inches = outline_height_inches or 1.0

    if inlay_type == "handle":
        sketchershapes.handle(
            sketch,
            min(1.0, outline_width_inches * 0.8),
            0.5,
            outline_width_inches,
            outline_height_inches,
        )
    elif inlay_type == "forearm":
        sketchershapes.triangle(sketch, 0.5, 9)
        sketchershapes.add_outline_box(sketch, outline_width_inches, outline_height_inches)
    elif inlay_type == "butt_sleeve":
        sketchershapes.butt_sleeve(
            sketch,
            0.5,
            2,
            outline_width_inches,
            outline_height_inches,
        )
    else:
        raise ValueError(f"Invalid inlay type: {inlay_type}")

    sketchershapes.pad_sketch(sketch, inlay_depth_inches)


def draw_stock(cue_document_name="Unnamed", inlay_document_name="butt_sleeve_inlay"):
    cue_document = App.getDocument(cue_document_name)
    var_set = cue_document.getObject("CueDimensions")
    inlay_document = App.getDocument(inlay_document_name)

    suffix = "_inlay"
    part_name = inlay_document_name[:-len(suffix)] if inlay_document_name.endswith(suffix) else inlay_document_name
    height = getattr(var_set, f"{part_name}_length").Value / 25.4
    width = getattr(var_set, f"{part_name}_od").Value / 25.4

    inlay_document.addObject("PartDesign::Body", "Pocket")
    inlay_document.getObject("Pocket").newObject("Sketcher::SketchObject", "stock_sketch")
    sketch = inlay_document.getObject("stock_sketch")
    sketch.AttachmentSupport = (inlay_document.getObject("XY_Plane001"), [""])
    sketch.MapMode = "FlatFace"

    sketchershapes.rectangle(sketch, width, height, 0)

    inlay_document.getObject("Pocket").newObject("PartDesign::Pad", "stock_pad")
    stock_pad = inlay_document.getObject("stock_pad")
    stock_pad.Profile = (sketch, ["", ])
    stock_pad.Length = 0.25 * 25.4
    stock_pad.TaperAngle = 0.000000
    stock_pad.UseCustomVector = 0
    stock_pad.Direction = (0, 0, 1)
    stock_pad.ReferenceAxis = (sketch, ["N_Axis"])
    stock_pad.AlongSketchNormal = 1
    stock_pad.Reversed = 1
    sketch.Visibility = False

    inlay_document.recompute()


def create_sketch(inlay_type="handle", inlay_name=None):
    Gui.SendMsgToActiveView("Save")
    if not inlay_name:
        if inlay_type not in ["handle", "forearm", "butt_sleeve"]:
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

    component_group = target_doc.getObject("CueComponents")
    if not component_group:
        print("CueComponents group was not found in the active document.")
        return
    group = target_doc.addObject("App::DocumentObjectGroup", group_name)
    group.Label = group_name.replace("_", " ").title()
    group_obj = target_doc.getObject(group_name)
    if not group_obj:
        print(f"Failed to create inlay group '{group_name}'.")
        return
    component_group.addObject(group_obj)

    object_names = [obj.Name for obj in component_group.Group]

    try:
        index_handle = object_names.index(inlay_type)
        index_handle_group = object_names.index(group_name)
        if index_handle_group != index_handle + 1:
            handle_group_obj = component_group.Group[index_handle_group]
            component_group.removeObject(handle_group_obj)
            component_group.addObject(handle_group_obj)
            reordered_list = component_group.Group[:index_handle + 1] + [handle_group_obj] + component_group.Group[index_handle + 1:-1]
            component_group.Group = reordered_list
    except ValueError:
        print(f"Skipping inlay group reorder for '{inlay_type}': expected objects were not found.")

    source_object = find_object_by_label(source_doc, "final_inlay")
    if not source_object:
        source_object = source_doc.getObject(f"{inlay_type}_pad")
    if not source_object:
        print(f"No inlay source object found in '{source_name}'.")
        return
    target_doc.addObject("App::Link", link_name).LinkedObject = source_object
    group_obj.addObject(target_doc.getObject(link_name))

    lnk = target_doc.getObject(link_name)
    lnk.Placement = App.Placement(App.Vector(0, 0, 0), App.Rotation(App.Vector(0, 0, 1), 180))

    anchor_name = f"{inlay_type}_outer" if target_doc.getObject(f"{inlay_type}_outer") else inlay_type
    if not target_doc.getObject(anchor_name):
        print(f"Anchor object for '{inlay_type}' was not found.")
        return

    lnk.setExpression(".Placement.Base.y", f"{anchor_name}.Placement.Base.y + CueDimensions.{inlay_type}_length")
    lnk.setExpression(
        ".Placement.Base.z",
        f"(CueDimensions.finish_size_startod + ((CueDimensions.finish_size_endod - CueDimensions.finish_size_startod) / CueDimensions.finish_size_length) * ({anchor_name}.Placement.Base.y + CueDimensions.{inlay_type}_length))/2",
    )

    array = Draft.make_polar_array(lnk, number=4, angle=360.0, center=App.Vector(0.0, 0.0, 0.0), use_link=True)
    array.Fuse = False
    Draft.autogroup(array)
    array.Axis = (0, 1, 0)
    array.Label = f"{inlay_type}_inlay_array"
    group_obj.addObject(array)
    target_doc.recompute()

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

    cut_obj = target_doc.addObject("Part::Common", f"{inlay_type} inlay previews")
    cut_obj.Tool = array
    cut_obj.Base = target_doc.getObject(inlay_type)
    group_obj.addObject(cut_obj)

    target_doc.recompute()


def update_all_previews():
    target_doc = App.ActiveDocument
    if not target_doc:
        print("No active cue document to update inlays.")
        return

    updated = 0
    for inlay_type in ["forearm", "handle", "butt_sleeve"]:
        link_name = f"linked_{inlay_type}_Inlay"
        link_obj = target_doc.getObject(link_name)
        if not link_obj:
            continue

        _, source_object = _get_inlay_source_object(inlay_type)
        if not source_object:
            print(f"No source inlay object available for '{inlay_type}'.")
            continue

        link_obj.LinkedObject = source_object
        updated += 1

    if updated:
        target_doc.recompute()
    print(f"Updated {updated} inlay link(s).")
