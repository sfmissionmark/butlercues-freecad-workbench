#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math

import FreeCAD as App

try:
    import FreeCADGui as Gui
except Exception:
    Gui = None

import Part

try:
    from PySide import QtGui, QtCore
except Exception:
    try:
        from PySide2 import QtWidgets as QtGui
        from PySide2 import QtCore
    except Exception:
        QtGui = None
        QtCore = None

try:
    import materials as _materials
except Exception:
    _materials = None


MACRO_TITLE = "Inlay From Sketch"
MM_PER_IN = 25.4
BORE_RELIEF_CLEARANCE_IN = 0.010
DEFAULT_CORE_RELIEF_STEP_IN = 0.056
DEFAULT_SURFACE_BREAKTHROUGH_IN = 0.002
DEFAULT_MATERIAL_NAME = "Unspecified"
PREFS = App.ParamGet("User parameter:Plugins/InlayFromSketch")


def _message(text, title=MACRO_TITLE, error=False):
    try:
        if error:
            App.Console.PrintError(str(text) + "\n")
        else:
            App.Console.PrintMessage(str(text) + "\n")
    except Exception:
        pass
    if (not error) or QtGui is None or Gui is None or not bool(getattr(App, "GuiUp", False)):
        return
    try:
        QtGui.QMessageBox.warning(None, title, str(text))
    except Exception:
        pass


def _is_sketch(obj):
    return bool(obj) and getattr(obj, "TypeId", "") == "Sketcher::SketchObject"


def _selected_sketches():
    if Gui is None:
        return []
    try:
        sel = list(Gui.Selection.getSelection() or [])
    except Exception:
        sel = []
    return [obj for obj in sel if _is_sketch(obj)]


def _selected_sketch():
    sketches = _selected_sketches()
    return sketches[0] if sketches else None


def _safe_text(value, fallback="Inlay"):
    text = str(value or "").strip()
    return text or str(fallback)


def _safe_name_token(value, fallback="Inlay"):
    text = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in _safe_text(value, fallback))
    text = text.strip("_") or fallback
    if not text[0].isalpha():
        text = "Obj_" + text
    return text


def _next_object_name(doc, base):
    root = _safe_name_token(base, "Inlay")
    if not doc.getObject(root):
        return root
    index = 2
    while doc.getObject(f"{root}_{index}"):
        index += 1
    return f"{root}_{index}"


def _same_parent_containers(source):
    parents = []
    for index, parent in enumerate(list(getattr(source, "InList", []) or [])):
        if not hasattr(parent, "addObject"):
            continue
        type_id = str(getattr(parent, "TypeId", "") or "")
        rank = 50
        if type_id == "PartDesign::Body":
            rank = 0
        elif type_id.startswith("App::Part"):
            rank = 10
        elif "Group" in type_id or type_id.startswith("App::DocumentObjectGroup"):
            rank = 20
        parents.append((rank, index, parent))
    parents.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in parents]


def _add_to_same_parent(source, obj):
    for parent in _same_parent_containers(source):
        try:
            parent.addObject(obj)
            return True
        except Exception:
            continue
    return False


def _group_contains(parent, obj):
    try:
        return obj in list(getattr(parent, "Group", []) or [])
    except Exception:
        return False


def _ensure_doc_group(doc, label, parent=None):
    clean_label = _safe_text(label, "Group")
    group = None
    try:
        search_items = list(getattr(parent, "Group", []) or []) if parent is not None else list(getattr(doc, "Objects", []) or [])
        for item in search_items:
            if getattr(item, "TypeId", "") != "App::DocumentObjectGroup":
                continue
            if _safe_text(getattr(item, "Label", ""), "") == clean_label:
                group = item
                break
    except Exception:
        group = None

    if group is None:
        group = doc.addObject("App::DocumentObjectGroup", _next_object_name(doc, clean_label))
        group.Label = clean_label

    if parent is not None and (not _group_contains(parent, group)):
        try:
            parent.addObject(group)
        except Exception:
            pass
    return group


def _set_material_name(obj, material_name):
    if not obj:
        return
    material_label = _safe_text(material_name, DEFAULT_MATERIAL_NAME)
    try:
        if not hasattr(obj, "MaterialName"):
            obj.addProperty("App::PropertyString", "MaterialName", "Inlay", "Material or wood used for CAM grouping")
        obj.MaterialName = material_label
    except Exception:
        pass


def _available_material_entries():
    entries = []
    seen = set()
    if _materials is None:
        return entries

    try:
        for item in list(_materials.materials() or []):
            name = _safe_text(item.get("name", ""), "")
            if not name or name.lower() in seen:
                continue
            entries.append({"name": name, "kind": "material", "rgb": tuple(item.get("rgb", (0.8, 0.8, 0.8)))})
            seen.add(name.lower())
    except Exception:
        pass

    try:
        for item in list(_materials.get_wood_images() or []):
            name = _safe_text(item.get("name", ""), "")
            if not name or name.lower() in seen:
                continue
            entries.append({"name": name, "kind": "wood", "path": item.get("path", "")})
            seen.add(name.lower())
    except Exception:
        pass

    entries.sort(key=lambda item: str(item.get("name", "")).lower())
    return entries


def _find_material_entry(material_name):
    token = _safe_text(material_name, "").lower()
    if not token:
        return None
    for item in _available_material_entries():
        if _safe_text(item.get("name", ""), "").lower() == token:
            return item
    return None


def _apply_material_appearance(obj, material_name):
    if obj is None or _materials is None:
        return False
    entry = _find_material_entry(material_name)
    if not entry:
        return False
    try:
        if entry.get("kind") == "wood":
            _materials.set_wood(entry.get("name"), [obj])
        else:
            _materials.setMaterial(color=tuple(entry.get("rgb", (0.8, 0.8, 0.8))), optional_parts=[obj])
        return True
    except Exception:
        return False


def _material_parent_group(doc, source):
    for parent in _same_parent_containers(source):
        if getattr(parent, "TypeId", "") == "App::DocumentObjectGroup":
            return parent
    return None


def _add_to_material_group(doc, obj, material_name, source=None):
    if doc is None or obj is None:
        return False
    material_label = _safe_text(material_name, DEFAULT_MATERIAL_NAME)
    parent_group = _material_parent_group(doc, source) if source is not None else None
    material_group = _ensure_doc_group(doc, material_label, parent=parent_group)
    _set_material_name(obj, material_label)
    if _group_contains(material_group, obj):
        return True
    try:
        material_group.addObject(obj)
        return True
    except Exception:
        return False


def _copy_view(source, target):
    src_view = getattr(source, "ViewObject", None)
    dst_view = getattr(target, "ViewObject", None)
    if src_view is None or dst_view is None:
        return
    try:
        dst_view.ShapeColor = getattr(src_view, "LineColor", (1.0, 0.7, 0.0))
    except Exception:
        pass
    try:
        dst_view.LineColor = getattr(src_view, "LineColor", (1.0, 0.7, 0.0))
    except Exception:
        pass
    try:
        dst_view.Transparency = 0
    except Exception:
        pass


def _global_placement(sketch):
    try:
        gp = sketch.getGlobalPlacement()
        if gp:
            return gp
    except Exception:
        pass
    try:
        return sketch.Placement
    except Exception:
        return App.Placement()


def _global_rotation(sketch):
    try:
        return _global_placement(sketch).Rotation
    except Exception:
        return App.Rotation()


def _reverse_depth_vector(sketch, depth_mm):
    direction = _global_rotation(sketch).multVec(App.Vector(0, 0, -1))
    if float(getattr(direction, "Length", 0.0) or 0.0) <= 1e-9:
        direction = App.Vector(0, 0, -1)
    try:
        direction.normalize()
    except Exception:
        direction = App.Vector(0, 0, -1)
    return direction.multiply(float(depth_mm))


def _attached_segment_plane(sketch):
    for prop in ("AttachmentSupport", "Support"):
        support = getattr(sketch, prop, None)
        if not support:
            continue
        for entry in support:
            obj = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
            if not obj:
                continue
            name = str(getattr(obj, "Name", "") or "")
            if name.startswith("SegmentPlane_"):
                return obj
            linked = getattr(obj, "LinkedObject", None)
            linked_name = str(getattr(linked, "Name", "") or "")
            if linked and linked_name.startswith("SegmentPlane_"):
                return linked
    return None


def _resolve_segment_root(obj):
    if not obj:
        return None
    visited = set()
    queue = [obj]
    while queue:
        current = queue.pop(0)
        current_name = str(getattr(current, "Name", "") or "")
        if current_name in visited:
            continue
        if current_name:
            visited.add(current_name)
        if getattr(current, "TypeId", "") == "App::DocumentObjectGroup" and current_name.startswith("SegmentPlanes_"):
            return current
        for parent in list(getattr(current, "InList", []) or []):
            queue.append(parent)
    return None


def _iter_group_members(group):
    for child in list(getattr(group, "Group", []) or []):
        yield child
        if getattr(child, "TypeId", "") == "App::DocumentObjectGroup":
            for nested in _iter_group_members(child):
                yield nested


def _segment_group_target_object(sketch):
    doc = getattr(sketch, "Document", None)
    if doc is None:
        return None
    root = _resolve_segment_root(_attached_segment_plane(sketch) or sketch)
    if root is None:
        return None

    root_name = str(getattr(root, "Name", "") or "")
    prefix = "SegmentPlanes_"
    target_name = root_name[len(prefix):] if root_name.startswith(prefix) else ""

    candidates = []
    if target_name:
        direct = doc.getObject(target_name)
        if direct is not None:
            candidates.append(direct)

    for child in _iter_group_members(root):
        name = str(getattr(child, "Name", "") or "")
        type_id = str(getattr(child, "TypeId", "") or "")
        if type_id == "App::DocumentObjectGroup" or type_id == "Sketcher::SketchObject" or type_id == "Image::ImagePlane":
            continue
        if name.startswith("SegmentPlane_") or name.startswith("WrapTemplate") or name.startswith("BitFitIssue"):
            continue
        shape = getattr(child, "Shape", None)
        if shape is None or shape.isNull():
            continue
        candidates.append(child)

    best = None
    best_score = None
    seen = set()
    for obj in candidates:
        obj_name = str(getattr(obj, "Name", "") or "")
        if obj_name in seen:
            continue
        seen.add(obj_name)
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            continue
        try:
            volume = abs(float(getattr(shape, "Volume", 0.0) or 0.0))
        except Exception:
            volume = 0.0
        label = str(getattr(obj, "Label", "") or "")
        name_match = 1 if target_name and (obj_name == target_name or label == target_name) else 0
        score = (name_match, volume)
        if best is None or score > best_score:
            best = obj
            best_score = score
    return best


def _axis_from_bbox(shape_obj, longest=True):
    bbox = getattr(shape_obj, "BoundBox", None)
    if bbox is None:
        return App.Vector(0, 1, 0)
    axes = [
        (float(getattr(bbox, "XLength", 0.0) or 0.0), App.Vector(1, 0, 0)),
        (float(getattr(bbox, "YLength", 0.0) or 0.0), App.Vector(0, 1, 0)),
        (float(getattr(bbox, "ZLength", 0.0) or 0.0), App.Vector(0, 0, 1)),
    ]
    axes = [item for item in axes if item[0] > 1e-7]
    if not axes:
        return App.Vector(0, 1, 0)
    axes.sort(key=lambda item: item[0], reverse=bool(longest))
    return App.Vector(axes[0][1])


def _bbox_center_and_length(shape_obj):
    bbox = getattr(shape_obj, "BoundBox", None)
    if bbox is None:
        return App.Vector(0, 0, 0), 1.0
    center = App.Vector(
        0.5 * (float(getattr(bbox, "XMin", 0.0) or 0.0) + float(getattr(bbox, "XMax", 0.0) or 0.0)),
        0.5 * (float(getattr(bbox, "YMin", 0.0) or 0.0) + float(getattr(bbox, "YMax", 0.0) or 0.0)),
        0.5 * (float(getattr(bbox, "ZMin", 0.0) or 0.0) + float(getattr(bbox, "ZMax", 0.0) or 0.0)),
    )
    length = max(
        1.0,
        float(getattr(bbox, "XLength", 0.0) or 0.0),
        float(getattr(bbox, "YLength", 0.0) or 0.0),
        float(getattr(bbox, "ZLength", 0.0) or 0.0),
    )
    return center, length


def _shape_volume(shape_obj):
    try:
        return abs(float(getattr(shape_obj, "Volume", 0.0) or 0.0))
    except Exception:
        return 0.0


def _orthogonal_basis(axis_vec, radial_hint=None):
    axis = App.Vector(axis_vec)
    if float(getattr(axis, "Length", 0.0) or 0.0) <= 1e-9:
        axis = App.Vector(0, 1, 0)
    try:
        axis.normalize()
    except Exception:
        axis = App.Vector(0, 1, 0)

    radial = None
    if radial_hint is not None:
        try:
            radial = App.Vector(radial_hint)
            radial = radial.sub(App.Vector(axis).multiply(float(radial.dot(axis))))
            if float(getattr(radial, "Length", 0.0) or 0.0) > 1e-9:
                radial.normalize()
            else:
                radial = None
        except Exception:
            radial = None

    if radial is None:
        ref = App.Vector(0, 0, 1)
        try:
            if abs(float(axis.dot(ref))) > 0.9:
                ref = App.Vector(1, 0, 0)
        except Exception:
            ref = App.Vector(1, 0, 0)
        radial = axis.cross(ref)
        if float(getattr(radial, "Length", 0.0) or 0.0) <= 1e-9:
            radial = axis.cross(App.Vector(0, 1, 0))
        try:
            radial.normalize()
        except Exception:
            radial = App.Vector(1, 0, 0)

    step_dir = axis.cross(radial)
    try:
        step_dir.normalize()
    except Exception:
        step_dir = App.Vector(0, 0, 1)
    return axis, radial, step_dir


def _make_bore_relief_shape(radius_mm, height_mm, base_point, axis_vec, stepped=False, step_mm=0.0, radial_hint=None):
    axis, radial_vec, step_vec = _orthogonal_basis(axis_vec, radial_hint=radial_hint)
    if (not bool(stepped)) or float(step_mm or 0.0) <= 1e-6:
        return Part.makeCylinder(float(radius_mm), float(height_mm), App.Vector(base_point), axis)

    radius_mm = max(0.1, float(radius_mm))
    step_mm = max(0.1, float(step_mm))
    step_count = max(2, int(math.ceil((2.0 * radius_mm) / step_mm)))
    band = (2.0 * radius_mm) / float(step_count)

    local_points = [(0.0, -radius_mm)]
    for idx in range(step_count):
        z0 = -radius_mm + (float(idx) * band)
        z1 = min(radius_mm, z0 + band)

        if z0 <= 0.0 <= z1:
            z_ref = 0.0
        elif abs(z0) < abs(z1):
            z_ref = z0
        else:
            z_ref = z1

        x_step = math.sqrt(max(0.0, (radius_mm * radius_mm) - (z_ref * z_ref)))
        local_points.append((x_step, z0))
        local_points.append((x_step, z1))

    local_points.append((0.0, radius_mm))

    center = App.Vector(base_point)
    points = []
    for x_val, z_val in local_points:
        pt = center.add(App.Vector(radial_vec).multiply(float(x_val)))
        pt = pt.add(App.Vector(step_vec).multiply(float(z_val)))
        points.append(pt)
    if points:
        points.append(points[0])

    polygon = Part.makePolygon(points)
    face = Part.Face(polygon)
    prism = face.extrude(App.Vector(axis).multiply(float(height_mm)))
    if prism and (not prism.isNull()) and prism.isValid():
        return prism
    return Part.makeCylinder(float(radius_mm), float(height_mm), App.Vector(base_point), axis)


def _cylinders_from_shape(shape_obj, preferred_axis=None):
    cylinders = []
    if not shape_obj or shape_obj.isNull():
        return cylinders
    axis_ref = App.Vector(preferred_axis) if preferred_axis is not None else None
    if axis_ref is not None and float(getattr(axis_ref, "Length", 0.0) or 0.0) > 1e-9:
        try:
            axis_ref.normalize()
        except Exception:
            axis_ref = None
    for face in list(getattr(shape_obj, "Faces", []) or []):
        surf = getattr(face, "Surface", None)
        if surf is None or getattr(surf, "TypeId", "") != "Part::GeomCylinder":
            continue
        try:
            axis = App.Vector(surf.Axis)
            axis.normalize()
            if axis_ref is not None and not axis.isParallel(axis_ref, 1e-2):
                continue
            radius = float(getattr(surf, "Radius", 0.0) or 0.0)
            center = App.Vector(surf.Center)
        except Exception:
            continue
        if radius <= 1e-6:
            continue
        projections = []
        for vertex in list(getattr(face, "Vertexes", []) or []):
            try:
                projections.append(axis.dot(App.Vector(vertex.Point).sub(center)))
            except Exception:
                pass
        if projections:
            z_min = min(projections)
            z_max = max(projections)
            length = max(0.0, z_max - z_min)
        else:
            z_min = 0.0
            z_max = float(getattr(face.BoundBox, "DiagonalLength", 0.0) or 0.0)
            length = z_max
        cylinders.append({
            "radius": radius,
            "axis": axis,
            "center": center,
            "z_min": z_min,
            "z_max": z_max,
            "length": max(length, 1.0),
            "area": abs(float(getattr(face, "Area", 0.0) or 0.0)),
        })
    return cylinders


def _find_inner_bore(target_obj, bore_diameter_in=0.0):
    shape_candidates = []
    tool_obj = getattr(target_obj, "Tool", None) if target_obj is not None else None
    tool_shape = getattr(tool_obj, "Shape", None) if tool_obj is not None else None
    if tool_shape is not None and not tool_shape.isNull():
        shape_candidates.append(tool_shape)
    shape_obj = getattr(target_obj, "Shape", target_obj)
    if shape_obj is not None and not shape_obj.isNull():
        shape_candidates.append(shape_obj)

    try:
        manual_radius = max(0.0, float(bore_diameter_in or 0.0) * MM_PER_IN * 0.5)
    except Exception:
        manual_radius = 0.0
    if manual_radius <= 1e-6:
        manual_radius = 0.0

    for shape in shape_candidates:
        preferred_axis = _axis_from_bbox(shape, longest=True)
        center, bbox_length = _bbox_center_and_length(shape)
        cylinders = _cylinders_from_shape(shape, preferred_axis=preferred_axis)
        if not cylinders:
            cylinders = _cylinders_from_shape(shape, preferred_axis=None)

        if manual_radius > 0.0:
            if cylinders:
                cylinders.sort(key=lambda item: (float(item.get("length", 0.0) or 0.0), -float(item.get("area", 0.0) or 0.0)), reverse=True)
                manual_bore = dict(cylinders[0])
                manual_bore["radius"] = manual_radius
                return manual_bore
            return {
                "radius": manual_radius,
                "axis": preferred_axis,
                "center": center,
                "z_min": -0.5 * bbox_length,
                "z_max": 0.5 * bbox_length,
                "length": max(bbox_length, 1.0),
                "area": 0.0,
            }

        if not cylinders:
            continue
        max_length = max(float(item.get("length", 0.0) or 0.0) for item in cylinders)
        ranked = [item for item in cylinders if float(item.get("length", 0.0) or 0.0) >= max(2.0, 0.50 * max_length)] or cylinders
        ranked.sort(key=lambda item: (float(item["radius"]), -float(item["length"]), -float(item["area"])))
        if ranked:
            return ranked[0]
    return None


def _apply_bore_relief(
    sketch,
    solid_shape,
    clearance_in=BORE_RELIEF_CLEARANCE_IN,
    bore_diameter_in=0.0,
    stepped=False,
    relief_step_in=DEFAULT_CORE_RELIEF_STEP_IN,
):
    try:
        target_obj = _segment_group_target_object(sketch)
        if target_obj is None:
            return solid_shape, False
        bore = _find_inner_bore(target_obj, bore_diameter_in=bore_diameter_in)
        if not bore:
            return solid_shape, False
        clearance_mm = max(0.0, float(clearance_in or 0.0) * MM_PER_IN)
        relief_radius = max(0.1, float(bore["radius"]) + clearance_mm)
        margin = max(2.0, clearance_mm * 4.0)
        relief_height = max(2.0, float(bore["length"]) + (2.0 * margin))
        step_mm = max(0.0, float(relief_step_in or 0.0) * MM_PER_IN)
        axis_vec = App.Vector(bore["axis"])
        try:
            axis_vec.normalize()
        except Exception:
            pass
        relief_base = App.Vector(bore["center"]).add(App.Vector(axis_vec).multiply(float(bore["z_min"]) - margin))

        radial_hint = None
        try:
            shape_center = App.Vector(getattr(solid_shape, "CenterOfMass", App.Vector(0, 0, 0)))
            radial_hint = shape_center.sub(App.Vector(bore["center"]))
        except Exception:
            radial_hint = None

        raw_volume = _shape_volume(solid_shape)
        for radius_bump, base_shift in ((0.0, 0.0), (0.05, 0.25), (0.10, 0.50)):
            relief = _make_bore_relief_shape(
                relief_radius + radius_bump,
                relief_height + (2.0 * base_shift),
                App.Vector(relief_base).add(App.Vector(axis_vec).multiply(-base_shift)),
                App.Vector(axis_vec),
                stepped=bool(stepped),
                step_mm=step_mm,
                radial_hint=radial_hint,
            )
            before_overlap = _shape_volume(solid_shape.common(relief))
            if before_overlap <= 1e-7:
                continue
            candidate = solid_shape.cut(relief)
            if candidate and (not candidate.isNull()) and candidate.isValid():
                candidate_volume = _shape_volume(candidate)
                if candidate_volume < (raw_volume - 1e-6):
                    return candidate, True
    except Exception:
        pass
    return solid_shape, False


def _non_construction_edges(sketch):
    edges = []
    for index, geometry in enumerate(list(getattr(sketch, "Geometry", []) or [])):
        try:
            if sketch.getConstruction(index):
                continue
        except Exception:
            pass
        try:
            shape = geometry.toShape()
        except Exception:
            continue
        if not shape:
            continue
        for edge in list(getattr(shape, "Edges", []) or []):
            edges.append(edge)
    return edges


def _set_preferred_fillet_axis(obj, axis_vec):
    if not obj:
        return
    try:
        axis = App.Vector(axis_vec)
    except Exception:
        return
    if float(getattr(axis, "Length", 0.0) or 0.0) <= 1e-9:
        return
    try:
        axis.normalize()
    except Exception:
        return
    try:
        if not hasattr(obj, "PreferredFilletAxis"):
            obj.addProperty("App::PropertyVector", "PreferredFilletAxis", "FilletForCNC", "Preferred seam axis for CNC fillet detection")
        obj.PreferredFilletAxis = axis
    except Exception:
        pass


def _faces_from_sketch(sketch, transform_matrix=None):
    faces = []
    edge_shapes = _non_construction_edges(sketch)
    if edge_shapes:
        try:
            sorted_groups = Part.sortEdges(edge_shapes)
        except Exception:
            sorted_groups = [edge_shapes]
        for group in sorted_groups:
            try:
                wire = Part.Wire(group)
            except Exception:
                continue
            if not wire.isClosed():
                continue
            try:
                face = Part.Face(wire)
                if transform_matrix is not None:
                    try:
                        face.transformShape(transform_matrix, True)
                    except Exception:
                        face = face.transformGeometry(transform_matrix)
                if abs(float(getattr(face, "Area", 0.0) or 0.0)) > 1e-9:
                    faces.append(face)
            except Exception:
                continue
    if faces:
        return faces

    try:
        sketch_shape = sketch.Shape.copy()
        if transform_matrix is not None:
            try:
                sketch_shape.transformShape(transform_matrix, True)
            except Exception:
                sketch_shape = sketch_shape.transformGeometry(transform_matrix)
    except Exception:
        sketch_shape = None
    if not sketch_shape:
        return faces

    for face in list(getattr(sketch_shape, "Faces", []) or []):
        try:
            if abs(float(getattr(face, "Area", 0.0) or 0.0)) > 1e-9:
                faces.append(face)
        except Exception:
            continue
    if faces:
        return faces

    for wire in list(getattr(sketch_shape, "Wires", []) or []):
        try:
            if not wire.isClosed():
                continue
            face = Part.Face(wire)
            if abs(float(getattr(face, "Area", 0.0) or 0.0)) > 1e-9:
                faces.append(face)
        except Exception:
            continue
    return faces


def create_inlay_from_sketch(
    source_sketch=None,
    depth_inch=0.125,
    fillet_radius_inch=0.014,
    output_name=None,
    keep_raw_solid=False,
    apply_core_relief=True,
    bore_diameter_in=0.0,
    stepped_core_relief=False,
    relief_step_in=DEFAULT_CORE_RELIEF_STEP_IN,
    material_name=DEFAULT_MATERIAL_NAME,
    open_result=True,
):
    source = source_sketch or _selected_sketch()
    if not _is_sketch(source):
        raise ValueError("Select one closed sketch first.")

    doc = source.Document
    if doc is None:
        raise ValueError("The selected sketch is not in an active document.")

    try:
        depth_inch = max(0.0001, float(depth_inch))
    except Exception:
        depth_inch = 0.125
    try:
        fillet_radius_inch = max(0.0001, float(fillet_radius_inch))
    except Exception:
        fillet_radius_inch = 0.014

    depth_mm = depth_inch * MM_PER_IN
    transform_matrix = None
    try:
        transform_matrix = _global_placement(source).toMatrix()
    except Exception:
        transform_matrix = None
    faces = _faces_from_sketch(source, transform_matrix=transform_matrix)
    if not faces:
        raise ValueError("The sketch does not contain a closed face. Close the profile and try again.")

    depth_vec = _reverse_depth_vector(source, depth_mm)
    try:
        surface_breakthrough_mm = max(0.0, float(DEFAULT_SURFACE_BREAKTHROUGH_IN) * MM_PER_IN)
    except Exception:
        surface_breakthrough_mm = 0.0
    lift_vec = App.Vector(0, 0, 0)
    try:
        lift_vec = App.Vector(-float(depth_vec.x), -float(depth_vec.y), -float(depth_vec.z))
        if lift_vec.Length > 1e-9 and surface_breakthrough_mm > 0.0:
            lift_vec.normalize()
            lift_vec = lift_vec.multiply(float(surface_breakthrough_mm))
        else:
            lift_vec = App.Vector(0, 0, 0)
    except Exception:
        lift_vec = App.Vector(0, 0, 0)

    solids = []
    for face in faces:
        try:
            face_for_extrude = face.copy()
        except Exception:
            face_for_extrude = face
        try:
            if surface_breakthrough_mm > 0.0:
                face_for_extrude.translate(lift_vec)
        except Exception:
            pass
        try:
            solids.append(face_for_extrude.extrude(depth_vec))
        except Exception:
            continue
    if not solids:
        raise RuntimeError("The sketch could not be extruded into a solid.")

    raw_shape = solids[0] if len(solids) == 1 else Part.makeCompound(solids)
    if bool(apply_core_relief):
        raw_shape, _bore_relief_applied = _apply_bore_relief(
            source,
            raw_shape,
            bore_diameter_in=bore_diameter_in,
            stepped=bool(stepped_core_relief),
            relief_step_in=relief_step_in,
        )
    base_label = _safe_text(getattr(source, "Label", getattr(source, "Name", "Inlay")), "Inlay")

    raw_obj = doc.addObject("Part::Feature", _next_object_name(doc, f"{source.Name}_Solid"))
    raw_obj.Label = f"{base_label} Solid"
    raw_obj.Shape = raw_shape
    _set_preferred_fillet_axis(raw_obj, depth_vec)
    _set_material_name(raw_obj, material_name)
    _copy_view(source, raw_obj)
    if not _add_to_material_group(doc, raw_obj, material_name, source=source):
        _add_to_same_parent(source, raw_obj)
    _apply_material_appearance(raw_obj, material_name)

    try:
        doc.recompute()
    except Exception:
        pass

    fillet_obj = None
    try:
        fillet_base_name = _safe_text(output_name, base_label)
        try:
            import inlays_fillet as _inlays_fillet
            fillet_obj = _inlays_fillet.fillet_for_cnc(
                target=raw_obj,
                fillet_radius_inch=fillet_radius_inch,
                final_solid_name=fillet_base_name,
                preserve_unfilleted=True,
                allow_smaller_radius_fallback=True,
                require_full_coverage=False,
                show_dialog=False,
            )
        except Exception:
            import inlays
            fillet_obj = inlays.fillet_for_cnc(
                target=raw_obj,
                fillet_radius_inch=fillet_radius_inch,
                final_solid_name=fillet_base_name,
                preserve_unfilleted=True,
                allow_smaller_radius_fallback=True,
                require_full_coverage=False,
                show_dialog=False,
            )
    except Exception as exc:
        _message(f"Fillet step failed: {exc}", error=True)

    if fillet_obj:
        _set_preferred_fillet_axis(fillet_obj, depth_vec)
        _set_material_name(fillet_obj, material_name)
        if not _add_to_material_group(doc, fillet_obj, material_name, source=source):
            _add_to_same_parent(source, fillet_obj)
        _apply_material_appearance(fillet_obj, material_name)
        try:
            if hasattr(fillet_obj, "ViewObject"):
                fillet_obj.ViewObject.Visibility = True
        except Exception:
            pass

    if fillet_obj and not bool(keep_raw_solid):
        try:
            doc.removeObject(raw_obj.Name)
            raw_obj = None
        except Exception:
            try:
                raw_obj.ViewObject.Visibility = False
            except Exception:
                pass

    try:
        doc.recompute()
    except Exception:
        pass

    result_obj = fillet_obj or raw_obj
    if result_obj is not None:
        try:
            if hasattr(source, "ViewObject") and source.ViewObject is not None:
                source.ViewObject.Visibility = False
        except Exception:
            pass
    if Gui is not None and result_obj is not None:
        try:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, result_obj.Name)
        except Exception:
            pass
        if bool(open_result):
            try:
                Gui.SendMsgToActiveView("ViewFit")
            except Exception:
                pass

    return result_obj


class InlayFromSketchTaskPanel:
    def __init__(self, source_sketches=None):
        selected = list(source_sketches or _selected_sketches())
        self.source_sketches = [obj for obj in selected if _is_sketch(obj)]
        if not self.source_sketches:
            raise ValueError("Select one or more sketches first.")
        self.source_sketch = self.source_sketches[0]
        if QtGui is None:
            raise RuntimeError("Qt is not available.")

        self.form = QtGui.QWidget()
        self.form.setWindowTitle(MACRO_TITLE)
        layout = QtGui.QFormLayout(self.form)

        source_label = _safe_text(getattr(self.source_sketch, "Label", self.source_sketch.Name), self.source_sketch.Name)
        self.info = QtGui.QLabel(f"Selected sketches: {len(self.source_sketches)}\nFirst sketch: {source_label}\nCreates reverse extrusions and runs Fillet for CNC on each.")
        self.info.setWordWrap(True)

        default_output = source_label
        default_depth = PREFS.GetFloat("depth_inch", 0.125)
        default_fillet = PREFS.GetFloat("fillet_radius_inch", 0.014)
        default_keep = PREFS.GetBool("keep_raw_solid", False)
        default_core_relief = PREFS.GetBool("apply_core_relief", True)
        default_bore_diameter = PREFS.GetFloat("bore_diameter_in", 0.0)
        default_stepped_core_relief = PREFS.GetBool("stepped_core_relief", False)
        default_relief_step = PREFS.GetFloat("core_relief_step_in", DEFAULT_CORE_RELIEF_STEP_IN)
        default_material_name = PREFS.GetString("material_name", DEFAULT_MATERIAL_NAME)
        known_material_names = [item.get("name", "") for item in _available_material_entries()]

        self.output_name = QtGui.QLineEdit(default_output)
        self.material_name = QtGui.QComboBox()
        self.material_name.setEditable(False)
        self.material_name.addItem("Custom")
        for item_name in known_material_names:
            if item_name:
                self.material_name.addItem(item_name)
        self.custom_material_name = QtGui.QLineEdit("")
        self.custom_material_name.setToolTip("Enter a custom material name when you do not want or need an automatic preview.")
        self.material_preview = QtGui.QLabel("")
        self.material_preview.setWordWrap(True)

        if default_material_name in known_material_names:
            self.material_name.setCurrentIndex(self.material_name.findText(default_material_name))
            self.custom_material_name.setText("")
        else:
            self.material_name.setCurrentIndex(0)
            self.custom_material_name.setText(default_material_name if default_material_name != DEFAULT_MATERIAL_NAME else "")

        self.depth_in = QtGui.QDoubleSpinBox()
        self.depth_in.setDecimals(4)
        self.depth_in.setRange(0.0010, 2.0000)
        self.depth_in.setSingleStep(0.0100)
        self.depth_in.setSuffix(" in")
        self.depth_in.setValue(float(default_depth))

        self.fillet_radius = QtGui.QDoubleSpinBox()
        self.fillet_radius.setDecimals(4)
        self.fillet_radius.setRange(0.0001, 0.2500)
        self.fillet_radius.setSingleStep(0.0010)
        self.fillet_radius.setSuffix(" in")
        self.fillet_radius.setValue(float(default_fillet))

        self.keep_raw = QtGui.QCheckBox("Keep raw extruded solid (debug)")
        self.keep_raw.setChecked(bool(default_keep))

        self.core_relief = QtGui.QCheckBox("Core relief")
        self.core_relief.setChecked(bool(default_core_relief))
        self.core_relief.setToolTip("Subtract a slightly oversized center bore from the inlay so it clears hollow cue cores.")

        self.bore_diameter = QtGui.QDoubleSpinBox()
        self.bore_diameter.setDecimals(4)
        self.bore_diameter.setRange(0.0000, 2.0000)
        self.bore_diameter.setSingleStep(0.0100)
        self.bore_diameter.setSuffix(" in")
        self.bore_diameter.setValue(float(default_bore_diameter))
        self.bore_diameter.setToolTip("Set the actual cue core bore diameter. Leave at 0 to auto-detect from the component.")

        self.stepped_core_relief = QtGui.QCheckBox("Stepped core relief")
        self.stepped_core_relief.setChecked(bool(default_stepped_core_relief))
        self.stepped_core_relief.setToolTip("Approximate the bore with flat steps instead of a smooth round surface for endmill-friendly cutting.")

        self.relief_step = QtGui.QDoubleSpinBox()
        self.relief_step.setDecimals(4)
        self.relief_step.setRange(0.0010, 0.5000)
        self.relief_step.setSingleStep(0.0010)
        self.relief_step.setSuffix(" in")
        self.relief_step.setValue(float(default_relief_step))
        self.relief_step.setToolTip("Approximate facet width for stepped core relief. Smaller values are closer to round.")

        try:
            self.core_relief.toggled.connect(self._sync_relief_controls)
            self.stepped_core_relief.toggled.connect(self._sync_relief_controls)
            self.material_name.currentIndexChanged.connect(self._sync_material_controls)
            self.custom_material_name.textChanged.connect(self._sync_material_controls)
        except Exception:
            pass
        self._sync_relief_controls()
        self._sync_material_controls()

        layout.addRow("", self.info)
        layout.addRow("Material / wood", self.material_name)
        layout.addRow("Custom material", self.custom_material_name)
        layout.addRow("Material preview", self.material_preview)
        layout.addRow("Final solid name", self.output_name)
        layout.addRow("Extrude depth", self.depth_in)
        layout.addRow("Fillet radius", self.fillet_radius)
        layout.addRow("", self.core_relief)
        layout.addRow("Bore diameter", self.bore_diameter)
        layout.addRow("", self.stepped_core_relief)
        layout.addRow("Relief step", self.relief_step)
        layout.addRow("", self.keep_raw)

    def _selected_material_name(self):
        choice = str(self.material_name.currentText() or "").strip()
        if choice == "Custom":
            custom_name = str(self.custom_material_name.text() or "").strip()
            return custom_name or DEFAULT_MATERIAL_NAME
        return choice or DEFAULT_MATERIAL_NAME

    def _sync_material_controls(self):
        is_custom = str(self.material_name.currentText() or "").strip() == "Custom"
        try:
            self.custom_material_name.setEnabled(is_custom)
        except Exception:
            pass

        material_name = self._selected_material_name()
        entry = _find_material_entry(material_name)
        if entry is None:
            self.material_preview.setText("Custom name — no automatic preview")
        elif entry.get("kind") == "wood":
            self.material_preview.setText(f"Wood texture preview: {material_name}")
        else:
            self.material_preview.setText(f"Material color preview: {material_name}")

    def _sync_relief_controls(self):
        enabled = bool(self.core_relief.isChecked())
        stepped_enabled = enabled and bool(self.stepped_core_relief.isChecked())
        try:
            self.bore_diameter.setEnabled(enabled)
        except Exception:
            pass
        try:
            self.stepped_core_relief.setEnabled(enabled)
        except Exception:
            pass
        try:
            self.relief_step.setEnabled(stepped_enabled)
        except Exception:
            pass

    def accept(self):
        try:
            PREFS.SetString("material_name", self._selected_material_name())
            PREFS.SetFloat("depth_inch", float(self.depth_in.value()))
            PREFS.SetFloat("fillet_radius_inch", float(self.fillet_radius.value()))
            PREFS.SetFloat("bore_diameter_in", float(self.bore_diameter.value()))
            PREFS.SetFloat("core_relief_step_in", float(self.relief_step.value()))
            PREFS.SetBool("keep_raw_solid", bool(self.keep_raw.isChecked()))
            PREFS.SetBool("apply_core_relief", bool(self.core_relief.isChecked()))
            PREFS.SetBool("stepped_core_relief", bool(self.stepped_core_relief.isChecked()))
        except Exception:
            pass

        try:
            for index, source_sketch in enumerate(self.source_sketches):
                create_inlay_from_sketch(
                    source_sketch=source_sketch,
                    depth_inch=float(self.depth_in.value()),
                    fillet_radius_inch=float(self.fillet_radius.value()),
                    output_name=str(self.output_name.text() or "").strip() if len(self.source_sketches) == 1 else "",
                    keep_raw_solid=bool(self.keep_raw.isChecked()),
                    apply_core_relief=bool(self.core_relief.isChecked()),
                    bore_diameter_in=float(self.bore_diameter.value()) if bool(self.core_relief.isChecked()) else 0.0,
                    stepped_core_relief=bool(self.stepped_core_relief.isChecked()) if bool(self.core_relief.isChecked()) else False,
                    relief_step_in=float(self.relief_step.value()),
                    material_name=self._selected_material_name(),
                    open_result=bool(index == (len(self.source_sketches) - 1)),
                )
        except Exception as exc:
            _message(str(exc), error=True)
            return False

        if Gui is not None:
            try:
                Gui.Control.closeDialog()
            except Exception:
                pass
        return True

    def reject(self):
        if Gui is not None:
            try:
                Gui.Control.closeDialog()
            except Exception:
                pass
        return True


def main():
    sketches = _selected_sketches()
    if not sketches:
        _message("Select one or more closed sketches first.", error=True)
        return

    if Gui is not None and QtGui is not None:
        try:
            Gui.Control.showDialog(InlayFromSketchTaskPanel(sketches))
            return
        except Exception:
            pass

    try:
        apply_core_relief = PREFS.GetBool("apply_core_relief", True)
        bore_diameter_in = PREFS.GetFloat("bore_diameter_in", 0.0)
        stepped_core_relief = PREFS.GetBool("stepped_core_relief", False)
        relief_step_in = PREFS.GetFloat("core_relief_step_in", DEFAULT_CORE_RELIEF_STEP_IN)
        material_name = PREFS.GetString("material_name", DEFAULT_MATERIAL_NAME)
        for index, source in enumerate(sketches):
            create_inlay_from_sketch(
                source_sketch=source,
                apply_core_relief=bool(apply_core_relief),
                bore_diameter_in=float(bore_diameter_in) if bool(apply_core_relief) else 0.0,
                stepped_core_relief=bool(stepped_core_relief) if bool(apply_core_relief) else False,
                relief_step_in=float(relief_step_in),
                material_name=str(material_name or "").strip() or DEFAULT_MATERIAL_NAME,
                open_result=bool(index == (len(sketches) - 1)),
            )
    except Exception as exc:
        _message(str(exc), error=True)


if __name__ == "__main__":
    main()
