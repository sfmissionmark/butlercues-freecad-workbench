#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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


MACRO_TITLE = "Pocket for Segment"
PREFS = App.ParamGet("User parameter:Plugins/PocketForSegment")


def _message(text, error=False):
    try:
        if error:
            App.Console.PrintError(str(text) + "\n")
        else:
            App.Console.PrintMessage(str(text) + "\n")
    except Exception:
        pass
    if error and QtGui is not None and Gui is not None and bool(getattr(App, "GuiUp", False)):
        try:
            QtGui.QMessageBox.warning(None, MACRO_TITLE, str(text))
        except Exception:
            pass


def _safe_text(value, fallback=""):
    text = str(value or "").strip()
    return text or str(fallback)


def _safe_name_token(value, fallback="Pocket"):
    text = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in _safe_text(value, fallback))
    text = text.strip("_") or fallback
    if not text[0].isalpha():
        text = "Obj_" + text
    return text


def _next_object_name(doc, base):
    root = _safe_name_token(base, "Pocket")
    if not doc.getObject(root):
        return root
    index = 2
    while doc.getObject(f"{root}_{index}"):
        index += 1
    return f"{root}_{index}"


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

    if parent is not None and not _group_contains(parent, group):
        try:
            parent.addObject(group)
        except Exception:
            pass
    return group


def _resolve_segment_root(obj):
    if not obj:
        return None
    visited = set()
    queue = [obj]
    while queue:
        current = queue.pop(0)
        name = str(getattr(current, "Name", "") or "")
        if name in visited:
            continue
        if name:
            visited.add(name)
        if getattr(current, "TypeId", "") == "App::DocumentObjectGroup" and name.startswith("SegmentPlanes_"):
            return current
        for parent in list(getattr(current, "InList", []) or []):
            queue.append(parent)
    return None


def _latest_segment_group(doc):
    if doc is None:
        return None
    groups = [
        obj for obj in list(getattr(doc, "Objects", []) or [])
        if getattr(obj, "TypeId", "") == "App::DocumentObjectGroup" and str(getattr(obj, "Name", "") or "").startswith("SegmentPlanes_")
    ]
    if not groups:
        return None
    return groups[-1]


def _resolve_segment_group(obj):
    if not obj:
        return None
    visited = set()
    queue = [obj]
    while queue:
        current = queue.pop(0)
        name = str(getattr(current, "Name", "") or "")
        if name in visited:
            continue
        if name:
            visited.add(name)
        if getattr(current, "TypeId", "") == "App::DocumentObjectGroup":
            if name.startswith("SegmentPlanes_"):
                return current
            if name.startswith("Segment_"):
                return current
            if name.startswith("inlay_segments_"):
                root = _resolve_segment_root(current)
                if root is not None:
                    return root
        for parent in list(getattr(current, "InList", []) or []):
            queue.append(parent)
    root = _resolve_segment_root(obj)
    if root is not None:
        return root
    return None


def _iter_group_members(group):
    for child in list(getattr(group, "Group", []) or []):
        yield child
        if getattr(child, "TypeId", "") == "App::DocumentObjectGroup":
            for nested in _iter_group_members(child):
                yield nested


def _first_segment_plane(segment_group):
    for child in _iter_group_members(segment_group):
        if getattr(child, "TypeId", "") == "PartDesign::Plane" and str(getattr(child, "Name", "") or "").startswith("SegmentPlane_"):
            return child
    return None


def _find_solid_shape(obj):
    shape = getattr(obj, "Shape", None)
    if shape:
        try:
            if not shape.isNull():
                return obj, shape
        except Exception:
            return obj, shape
    for child in list(getattr(obj, "Group", []) or []):
        found_obj, found_shape = _find_solid_shape(child)
        if found_shape:
            return found_obj, found_shape
    return None, None


def _global_placement(obj):
    try:
        gp = obj.getGlobalPlacement()
        if gp:
            return gp
    except Exception:
        pass
    try:
        return obj.Placement
    except Exception:
        return App.Placement()


def _global_shape_copy(obj):
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return None
    try:
        if shape.isNull():
            return None
    except Exception:
        pass
    try:
        copy_shape = shape.copy()
    except Exception:
        copy_shape = shape
    try:
        matrix = _global_placement(obj).toMatrix()
        copy_shape.transformShape(matrix, True)
        return copy_shape
    except Exception:
        try:
            return copy_shape.transformGeometry(matrix)
        except Exception:
            return copy_shape


def _strip_shape_history(shape_obj):
    if not shape_obj:
        return shape_obj
    try:
        brep = shape_obj.exportBrepToString()
        clean = Part.Shape()
        clean.importBrepFromString(brep)
        try:
            clean = clean.removeSplitter()
        except Exception:
            pass
        return clean
    except Exception:
        return shape_obj


def _iter_tool_shapes(shape_obj):
    try:
        solids = list(getattr(shape_obj, "Solids", []) or [])
    except Exception:
        solids = []

    if len(solids) <= 1:
        return [shape_obj]

    tools = []
    for solid in solids:
        if not solid:
            continue
        tools.append(_strip_shape_history(solid))
    return tools or [shape_obj]


def _aligned_inlay_shape(segment_group, shape_list, align_to_world=True):
    valid_shapes = [shape.copy() for shape in (shape_list or []) if shape and not shape.isNull()]
    if not valid_shapes:
        return None

    try:
        combined = Part.makeCompound(valid_shapes)
    except Exception:
        combined = valid_shapes[0]

    combined = _strip_shape_history(combined)

    if bool(align_to_world):
        plane = _first_segment_plane(segment_group)
        if plane is not None:
            try:
                inverse_matrix = _global_placement(plane).inverse().toMatrix()
                combined.transformShape(inverse_matrix, True)
            except Exception:
                try:
                    combined = combined.transformGeometry(inverse_matrix)
                except Exception:
                    pass

    try:
        bb = combined.BoundBox
        top_breakthrough_mm = 0.002 * 25.4
        combined.translate(App.Vector(-float(bb.Center.x), -float(bb.YMin), float(top_breakthrough_mm) - float(bb.ZMax)))
    except Exception:
        pass

    return _strip_shape_history(combined)


def _build_pocket_model_shape(inlay_shape):
    if not inlay_shape or inlay_shape.isNull():
        return None

    try:
        bb = inlay_shape.BoundBox
    except Exception:
        return None
    if not bb:
        return None

    side_margin = 0.1 * 25.4
    y_extra = 0.1 * 25.4
    z_extra = 0.01 * 25.4

    x_min = float(bb.XMin) - side_margin
    x_len = max(0.01, float(bb.XLength) + (2.0 * side_margin))
    y_min = 0.0
    y_max = float(bb.YMax) + y_extra
    y_len = max(0.01, y_max - y_min)
    z_depth_from_zero = max(abs(float(bb.ZMin)), abs(float(bb.ZMax)))
    z_len = max(0.01, z_depth_from_zero + z_extra)
    z_min = -z_len

    try:
        blank_shape = Part.makeBox(x_len, y_len, z_len, App.Vector(x_min, y_min, z_min), App.Vector(0, 0, 1))
        result_shape = blank_shape
        for tool_part in _iter_tool_shapes(inlay_shape):
            if not tool_part or tool_part.isNull():
                continue
            result_shape = result_shape.cut(tool_part)
        if result_shape.isNull() or not getattr(result_shape, "Solids", None):
            return None
        rotated = result_shape.copy()
        rotated.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 180.0)
        result_shape = _strip_shape_history(rotated)
    except Exception:
        return None

    return result_shape


def _collect_material_groups(segment_group, selected_obj=None):
    groups = []
    explicit = _resolve_material_group(selected_obj) if selected_obj is not None else None
    if explicit is not None:
        return [explicit]

    skip_labels = {"Inlay_sketches", "Inlay_solids", "Pockets"}
    skip_prefixes = ("SegmentPlanes_", "Segment_", "SegmentPlane_", "inlay_segments_")
    for child in list(getattr(segment_group, "Group", []) or []):
        if getattr(child, "TypeId", "") != "App::DocumentObjectGroup":
            continue
        label = _safe_text(getattr(child, "Label", getattr(child, "Name", "")), "")
        name = _safe_text(getattr(child, "Name", ""), "")
        if label in skip_labels or any(name.startswith(prefix) for prefix in skip_prefixes):
            continue
        solids = _collect_cutters(child)
        if solids:
            groups.append(child)
    return groups


def _resolve_material_group(obj):
    if obj is None:
        return None
    segment_group = _resolve_segment_group(obj)
    if segment_group is None:
        return None
    current = obj
    skip_labels = {"Inlay_sketches", "Inlay_solids", "Pockets"}
    skip_prefixes = ("SegmentPlanes_", "Segment_", "SegmentPlane_", "inlay_segments_")
    while current is not None:
        if getattr(current, "TypeId", "") == "App::DocumentObjectGroup":
            try:
                if current in list(getattr(segment_group, "Group", []) or []):
                    label = _safe_text(getattr(current, "Label", getattr(current, "Name", "")), "")
                    name = _safe_text(getattr(current, "Name", ""), "")
                    if label not in skip_labels and not any(name.startswith(prefix) for prefix in skip_prefixes):
                        return current
            except Exception:
                pass
        parents = list(getattr(current, "InList", []) or [])
        current = parents[0] if parents else None
    return None


def _collect_cutters(group):
    cutters = []
    seen = set()
    skip_types = {"App::DocumentObjectGroup", "PartDesign::Plane", "Sketcher::SketchObject", "Image::ImagePlane"}
    for child in _iter_group_members(group):
        if getattr(child, "TypeId", "") in skip_types:
            continue
        name = str(getattr(child, "Name", "") or "")
        if name in seen:
            continue
        seen.add(name)
        shape = _global_shape_copy(child)
        if shape is None:
            continue
        try:
            solid_count = len(list(getattr(shape, "Solids", []) or []))
        except Exception:
            solid_count = 0
        if solid_count <= 0 and str(getattr(shape, "ShapeType", "") or "") != "Solid":
            continue
        cutters.append((child, shape))
    return cutters


def _cam_job_objects(doc):
    return [
        obj for obj in list(getattr(doc, "Objects", []) or [])
        if hasattr(obj, "Operations") and hasattr(obj, "Model") and hasattr(obj, "Stock")
    ]


def _defer_call(callback, delay_ms=25):
    if callback is None:
        return
    if QtCore is not None and bool(getattr(App, "GuiUp", False)):
        try:
            QtCore.QTimer.singleShot(int(delay_ms), callback)
            return
        except Exception:
            pass
    callback()


def pocket_for_segment(segment_group=None, align_to_world=True, selected_obj=None, create_cam_job=True, show_cam_dialog=False):
    doc = App.ActiveDocument
    if doc is None:
        raise ValueError("No active document.")

    anchor = selected_obj
    if segment_group is None:
        if anchor is None and Gui is not None:
            selection = list(Gui.Selection.getSelection() or [])
            anchor = selection[0] if selection else None
        segment_group = _resolve_segment_group(anchor)
        if segment_group is None:
            segment_group = _latest_segment_group(doc)
    if segment_group is None:
        raise ValueError("Select a SegmentPlanes folder, a material folder, or any inlay object under that segment first.")

    segment_groups = [segment_group]
    group_name = str(getattr(segment_group, "Name", "") or "")
    if group_name.startswith("SegmentPlanes_"):
        segment_groups = [
            child for child in list(getattr(segment_group, "Group", []) or [])
            if getattr(child, "TypeId", "") == "App::DocumentObjectGroup" and str(getattr(child, "Name", "") or "").startswith("Segment_")
        ] or [segment_group]

    created = []

    for active_segment in segment_groups:
        material_groups = _collect_material_groups(active_segment, selected_obj=anchor)
        if not material_groups:
            continue

        pockets_group = _ensure_doc_group(doc, "Pockets", parent=active_segment)

        for mat_group in material_groups:
            label = _safe_text(getattr(mat_group, "Label", getattr(mat_group, "Name", "Material")), "Material")
            cutters = _collect_cutters(mat_group)
            if not cutters:
                continue

            inlay_shape = _aligned_inlay_shape(
                active_segment,
                [shape for _obj, shape in cutters],
                align_to_world=bool(align_to_world),
            )
            if inlay_shape is None or inlay_shape.isNull():
                _message(f"Pocket model warning for {label}: no valid inlay shape found.")
                continue

            result_shape = _build_pocket_model_shape(inlay_shape)
            if result_shape is None or result_shape.isNull():
                _message(f"Pocket model warning for {label}: blank cut produced no valid solid.")
                continue

            result_obj = doc.addObject("Part::Feature", _next_object_name(doc, f"{active_segment.Name}_{label}_PocketModel"))
            result_obj.Label = f"{label} Pocket Model"
            result_obj.Shape = result_shape
            try:
                result_obj.Placement = App.Placement()
            except Exception:
                pass
            try:
                if hasattr(result_obj, "MaterialName") is False:
                    result_obj.addProperty("App::PropertyString", "MaterialName", "Pocket", "Material folder used for this pocket")
                result_obj.MaterialName = label
            except Exception:
                pass
            try:
                result_obj.addProperty("App::PropertyBool", "AlignedToWorldXYZ", "Pocket", "Whether the pocket model was aligned to world XYZ")
            except Exception:
                pass
            try:
                result_obj.AlignedToWorldXYZ = bool(align_to_world)
            except Exception:
                pass
            try:
                if not hasattr(result_obj, "ButlerIsPocketModel"):
                    result_obj.addProperty("App::PropertyBool", "ButlerIsPocketModel", "Pocket", "Marks this object as a ready-to-use pocket model")
                result_obj.ButlerIsPocketModel = True
            except Exception:
                pass
            try:
                if not hasattr(result_obj, "ButlerCuesWorkflow"):
                    result_obj.addProperty("App::PropertyString", "ButlerCuesWorkflow", "Pocket", "Workflow tag for Butler Cues objects")
                result_obj.ButlerCuesWorkflow = "Pocket Model"
            except Exception:
                pass
            try:
                if not hasattr(result_obj, "PocketUseTopLevelOnly"):
                    result_obj.addProperty("App::PropertyBool", "PocketUseTopLevelOnly", "Pocket", "Legacy guard for limiting CAM face selection")
                result_obj.PocketUseTopLevelOnly = False
            except Exception:
                pass
            try:
                pockets_group.addObject(result_obj)
            except Exception:
                pass
            created.append((label, len(cutters), result_obj))

    try:
        doc.recompute()
    except Exception:
        pass

    if not created:
        raise RuntimeError("No pocket models were created.")

    created_jobs = []
    opened_job_dialog = []
    if bool(create_cam_job):
        try:
            import inlays
        except Exception as exc:
            _message(f"Pocket CAM warning: failed to load the Pocket Job workflow: {exc}", error=True)
            inlays = None

        if inlays is not None:
            for idx, (label, _count, obj) in enumerate(created):
                before_jobs = len(_cam_job_objects(doc))
                try:
                    if bool(show_cam_dialog and idx == 0) and Gui is not None:
                        try:
                            Gui.Control.closeDialog()
                        except Exception:
                            pass
                    inlays.create_pocket_cnc_job(
                        target=obj,
                        show_dialog=bool(show_cam_dialog and idx == 0),
                        skip_fillet_warning=True,
                    )
                except Exception as exc:
                    _message(f"Pocket CAM warning for {label}: {exc}", error=True)
                    continue
                after_jobs = len(_cam_job_objects(doc))
                if after_jobs > before_jobs:
                    created_jobs.append(label)
                elif bool(show_cam_dialog and idx == 0):
                    opened_job_dialog.append(label)
                    break

    summary = ", ".join(f"{label} ({count})" for label, count, _ in created)
    if created_jobs:
        _message(
            f"Pocket for Segment created {len(created)} pocket model(s): {summary}. CAM jobs created for: {', '.join(created_jobs)}."
        )
    elif opened_job_dialog:
        _message(
            f"Pocket for Segment created {len(created)} pocket model(s): {summary}. Pocket Job options opened for: {', '.join(opened_job_dialog)}."
        )
    else:
        _message(f"Pocket for Segment created {len(created)} pocket model(s): {summary}.")

    if Gui is not None:
        try:
            Gui.Selection.clearSelection()
            for _label, _count, obj in created:
                Gui.Selection.addSelection(doc.Name, obj.Name)
        except Exception:
            pass
    return [item[2] for item in created]


class PocketForSegmentTaskPanel:
    def __init__(self):
        if QtGui is None:
            raise RuntimeError("Qt is not available.")
        self.form = QtGui.QWidget()
        self.form.setWindowTitle(MACRO_TITLE)
        layout = QtGui.QFormLayout(self.form)

        default_align = PREFS.GetBool("align_to_world_xyz", True)
        default_create_job = PREFS.GetBool("create_cam_job", True)

        self.info = QtGui.QLabel(
            "Build a CAM-style pocket model from the selected segment material folder(s). The result is flattened to world XYZ, top at Z0, and centered on X."
        )
        self.info.setWordWrap(True)
        self.align_to_world = QtGui.QCheckBox("Align inlay to world XYZ before building pocket")
        self.align_to_world.setChecked(bool(default_align))
        self.create_cam_job = QtGui.QCheckBox("Create Pocket CAM job with the same bit/template options")
        self.create_cam_job.setChecked(bool(default_create_job))

        layout.addRow("", self.info)
        layout.addRow("", self.align_to_world)
        layout.addRow("", self.create_cam_job)

    def accept(self):
        align_to_world = bool(self.align_to_world.isChecked())
        create_cam_job = bool(self.create_cam_job.isChecked())
        try:
            PREFS.SetBool("align_to_world_xyz", align_to_world)
            PREFS.SetBool("create_cam_job", create_cam_job)
        except Exception:
            pass
        if Gui is not None:
            try:
                Gui.Control.closeDialog()
            except Exception:
                pass

        def _run_after_close():
            try:
                pocket_for_segment(
                    align_to_world=align_to_world,
                    create_cam_job=create_cam_job,
                    show_cam_dialog=bool(create_cam_job and Gui is not None and getattr(App, "GuiUp", False)),
                )
            except Exception as exc:
                _message(str(exc), error=True)

        _defer_call(_run_after_close)
        return True

    def reject(self):
        if Gui is not None:
            try:
                Gui.Control.closeDialog()
            except Exception:
                pass
        return True


def main():
    if Gui is not None and QtGui is not None and bool(getattr(App, "GuiUp", False)):
        try:
            Gui.Control.showDialog(PocketForSegmentTaskPanel())
            return
        except Exception:
            pass
    pocket_for_segment(
        align_to_world=bool(PREFS.GetBool("align_to_world_xyz", True)),
        create_cam_job=bool(PREFS.GetBool("create_cam_job", True)),
        show_cam_dialog=False,
    )


if __name__ == "__main__":
    main()
