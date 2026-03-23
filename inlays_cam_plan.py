#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import FreeCAD as App
import FreeCADGui as Gui
import Draft
import Part
import math
import json
import time
import os

try:
    from PySide import QtGui, QtCore
except Exception:
    QtGui = None
    QtCore = None

import inlays_document as _inlays_document
import inlays_ui_helpers as _inlays_ui


def _attach_pattern_group_view_provider(group_obj):
    return _inlays_ui._attach_pattern_group_view_provider(group_obj)


def _attach_pattern_group_view_providers(doc):
    return _inlays_ui._attach_pattern_group_view_providers(doc)


def _get_inlay_source_object(inlay_type):
    return _inlays_document._get_inlay_source_object(inlay_type)


def create_cam_job():
    doc = App.ActiveDocument
    if not doc:
        print("No active document.")
        return

    _attach_pattern_group_view_providers(doc)

    def _next_name(base):
        idx = 1
        while doc.getObject(f"{base}_{idx}"):
            idx += 1
        return f"{base}_{idx}"

    def _ensure_pocket_container(model_obj):
        base = getattr(model_obj, "Name", "Model")
        group_name = _next_name(f"PocketCAM_{base}")
        group = doc.addObject("App::DocumentObjectGroup", group_name)
        group.Label = f"{getattr(model_obj, 'Label', base)} Pocket CAM"
        return group

    def _add_model_reference(container_obj, model_obj):
        if not container_obj or not model_obj:
            return
        try:
            link_name = _next_name(f"PocketModel_{getattr(model_obj, 'Name', 'Model')}")
            model_link = doc.addObject("App::Link", link_name)
            model_link.LinkedObject = model_obj
            model_link.Label = f"{getattr(model_obj, 'Label', getattr(model_obj, 'Name', 'Model'))} (Source)"
            container_obj.addObject(model_link)
        except Exception:
            try:
                container_obj.addObject(model_obj)
            except Exception:
                pass

    def _ensure_cam_output_group(base_obj):
        base_name = getattr(base_obj, "Name", "Section")
        group_name = _next_name(f"SectionCAM_{base_name}")
        group = doc.addObject("App::DocumentObjectGroup", group_name)
        group.Label = f"{getattr(base_obj, 'Label', base_name)} CAM"
        return group

    def _prefs():
        return App.ParamGet("User parameter:BaseApp/Preferences/Mod/ButlerCues/InlayCNCPlan")

    def _is_plan_group(obj):
        if not obj:
            return False
        if getattr(obj, "TypeId", "") != "App::DocumentObjectGroup":
            return False
        if hasattr(obj, "CNCPlanSettings"):
            return True
        obj_name = getattr(obj, "Name", "")
        return obj_name.startswith("InlayCNCPlan_") or obj_name.startswith("InlayPattern_")

    def _selected_plan_group():
        def _find_plan_group_in_ancestors(start_obj):
            if not start_obj:
                return None
            seen = set()
            stack = [start_obj]
            while stack:
                current = stack.pop()
                current_name = getattr(current, "Name", None)
                if current_name and current_name in seen:
                    continue
                if current_name:
                    seen.add(current_name)
                for parent in getattr(current, "InList", []) or []:
                    if _is_plan_group(parent):
                        return parent
                    stack.append(parent)
            return None

        selected = Gui.Selection.getSelection() if Gui else []
        for obj in selected:
            if _is_plan_group(obj):
                return obj
            parent_group = _find_plan_group_in_ancestors(obj)
            if parent_group:
                return parent_group
        return None

    def _ensure_group_settings_property(group_obj):
        if not hasattr(group_obj, "CNCPlanSettings"):
            group_obj.addProperty(
                "App::PropertyString",
                "CNCPlanSettings",
                "Inlay CNC",
                "Serialized inlay CNC plan dialog settings",
            )

    def _read_group_settings(group_obj):
        try:
            if hasattr(group_obj, "CNCPlanSettings") and group_obj.CNCPlanSettings:
                parsed = json.loads(group_obj.CNCPlanSettings)
                return parsed if isinstance(parsed, dict) else {}
        except Exception:
            pass
        return {}

    def _write_group_settings(group_obj, values):
        _ensure_group_settings_property(group_obj)
        try:
            group_obj.CNCPlanSettings = json.dumps(values)
        except Exception:
            group_obj.CNCPlanSettings = ""

    def _snapshot_visible_object_names():
        names = []
        for obj in getattr(doc, "Objects", []) or []:
            try:
                if hasattr(obj, "ViewObject") and obj.ViewObject is not None and bool(obj.ViewObject.Visibility):
                    names.append(obj.Name)
            except Exception:
                continue
        return names

    def _restore_visible_object_names(names):
        wanted = set(names or [])
        for obj in getattr(doc, "Objects", []) or []:
            try:
                if hasattr(obj, "ViewObject") and obj.ViewObject is not None:
                    obj.ViewObject.Visibility = (obj.Name in wanted)
            except Exception:
                continue

    def _is_inside_partdesign_body(obj):
        if not obj:
            return False
        try:
            if str(getattr(obj, "TypeId", "")).startswith("PartDesign::"):
                return True
        except Exception:
            pass

        seen = set()
        stack = [obj]
        while stack:
            current = stack.pop()
            current_name = getattr(current, "Name", "")
            if current_name and current_name in seen:
                continue
            if current_name:
                seen.add(current_name)

            if str(getattr(current, "TypeId", "")) == "PartDesign::Body":
                return True
            for parent in getattr(current, "InList", []) or []:
                stack.append(parent)
        return False

    def _clear_plan_group(group_obj):
        def _is_generated_pattern_object(obj):
            if not obj:
                return False
            name = getattr(obj, "Name", "")
            label = getattr(obj, "Label", "")
            if name.startswith("Inlays") or name.startswith("InlayHelpers"):
                return True
            if name.startswith("Inlay_R"):
                return True
            if name.startswith("Trim_Inlay_R") or name.startswith("Trim_"):
                return True
            if name.startswith("InlayTrimPreview"):
                return True
            if isinstance(label, str) and (
                label.startswith("Inlay R")
                or label.startswith("Trim Inlay R")
                or label in ["Inlay Trim Preview", "Inlays", "Inlay Helpers"]
            ):
                return True
            return False

        def _collect_descendants_postorder(parent_obj):
            collected = []
            for child in list(getattr(parent_obj, "Group", []) or []):
                collected.extend(_collect_descendants_postorder(child))
                collected.append(child)
            return collected

        direct_children = [
            child for child in list(getattr(group_obj, "Group", []) or [])
            if _is_generated_pattern_object(child)
        ]
        descendants = []
        for child in direct_children:
            descendants.extend(_collect_descendants_postorder(child))
            descendants.append(child)

        # First detach direct children from the container group.
        for child in direct_children:
            try:
                group_obj.removeObject(child)
            except Exception:
                pass

        # Then delete all descendants from document, deepest-first.
        removed = 0
        for child in descendants:
            if _is_inside_partdesign_body(child):
                continue
            try:
                if doc.getObject(child.Name):
                    doc.removeObject(child.Name)
                    removed += 1
            except Exception:
                pass

        if removed:
            print(f"Cleared {removed} previous pattern object(s) from '{group_obj.Label}'.")

    def _is_in_group_tree(obj, group_obj):
        if not obj or not group_obj:
            return False
        if obj == group_obj:
            return True

        seen = set()
        stack = [obj]
        while stack:
            current = stack.pop()
            current_name = getattr(current, "Name", None)
            if current_name and current_name in seen:
                continue
            if current_name:
                seen.add(current_name)

            for parent in getattr(current, "InList", []) or []:
                if parent == group_obj:
                    return True
                stack.append(parent)
        return False

    def _source_from_plan_group(group_obj):
        if not group_obj:
            return None, None

        stack = list(getattr(group_obj, "Group", []) or [])
        seen = set()
        while stack:
            child = stack.pop(0)
            child_name = getattr(child, "Name", "")
            if child_name in seen:
                continue
            seen.add(child_name)

            if getattr(child, "TypeId", "") == "App::Link" and hasattr(child, "LinkedObject") and child.LinkedObject:
                linked = child.LinkedObject
                try:
                    if hasattr(linked, "Shape") and linked.Shape and not linked.Shape.isNull():
                        return linked.Shape.copy(), linked
                except Exception:
                    pass

            for nested in getattr(child, "Group", []) or []:
                stack.append(nested)
        return None, None

    def _load_persistent_defaults():
        p = _prefs()
        stagger_even_rows = p.GetBool("stagger_even_rows", p.GetBool("stagger_even_columns", False))
        return {
            "source_mode": p.GetString("source_mode", "selected"),
            "target_section": p.GetString("target_section", "handle"),
            "pattern_mode": p.GetString("pattern_mode", "polar"),
            "inlay_scale_mode": p.GetString("inlay_scale_mode", "fixed"),
            "replace_selected_pattern": p.GetBool("replace_selected_pattern", True),
            "rows": p.GetInt("rows", 2),
            "columns": p.GetInt("columns", 2),
            "row_spacing_in": p.GetFloat("row_spacing_in", 3.1115 / 25.4),
            "column_spacing_in": p.GetFloat("column_spacing_in", 3.1115 / 25.4),
            "grid_start_angle_deg": p.GetFloat("grid_start_angle_deg", 0.0),
            "grid_angle_step_deg": p.GetFloat("grid_angle_step_deg", 180.0),
            "stagger_even_rows": stagger_even_rows,
            "y_offset_in": p.GetFloat("y_offset_in", 0.5),
            "b_rotate_deg": p.GetFloat("b_rotate_deg", 180.0),
            "polar_count": p.GetInt("polar_count", 4),
            "polar_sweep_deg": p.GetFloat("polar_sweep_deg", 360.0),
            "polar_start_deg": p.GetFloat("polar_start_deg", 0.0),
            "surface_axis": p.GetString("surface_axis", "z"),
            "surface_axis_flip": p.GetBool("surface_axis_flip", False),
            "trim_preview_to_section": p.GetBool("trim_preview_to_section", True),
            "isolate_trim_preview": p.GetBool("isolate_trim_preview", True),
        }

    def _save_persistent_defaults(values):
        p = _prefs()
        p.SetString("source_mode", str(values["source_mode"]))
        p.SetString("target_section", str(values["target_section"]))
        p.SetString("pattern_mode", str(values["pattern_mode"]))
        p.SetString("inlay_scale_mode", str(values.get("inlay_scale_mode", "fixed")))
        p.SetBool("replace_selected_pattern", bool(values.get("replace_selected_pattern", True)))
        p.SetInt("rows", int(values["rows"]))
        p.SetInt("columns", int(values["columns"]))
        p.SetFloat("row_spacing_in", float(values["row_spacing_in"]))
        p.SetFloat("column_spacing_in", float(values["column_spacing_in"]))
        p.SetFloat("grid_start_angle_deg", float(values["grid_start_angle_deg"]))
        p.SetFloat("grid_angle_step_deg", float(values["grid_angle_step_deg"]))
        p.SetBool("stagger_even_rows", bool(values["stagger_even_rows"]))
        p.SetFloat("y_offset_in", float(values["y_offset_in"]))
        p.SetFloat("b_rotate_deg", float(values["b_rotate_deg"]))
        p.SetInt("polar_count", int(values["polar_count"]))
        p.SetFloat("polar_sweep_deg", float(values["polar_sweep_deg"]))
        p.SetFloat("polar_start_deg", float(values["polar_start_deg"]))
        p.SetString("surface_axis", str(values["surface_axis"]))
        p.SetBool("surface_axis_flip", bool(values["surface_axis_flip"]))
        p.SetBool("trim_preview_to_section", bool(values["trim_preview_to_section"]))
        p.SetBool("isolate_trim_preview", bool(values["isolate_trim_preview"]))

    def _best_inlay_face_from_shape(shape_obj):
        if not shape_obj or shape_obj.isNull() or not getattr(shape_obj, "Faces", None):
            return None

        planar_candidates = []
        any_candidates = []
        for face in shape_obj.Faces:
            area = getattr(face, "Area", 0.0)
            any_candidates.append((area, face))

            try:
                if not isinstance(face.Surface, Part.Plane):
                    continue
                normal = _face_normal(face)
                if normal is None:
                    continue
                x_alignment = abs(normal.x)
                planar_candidates.append((x_alignment, area, face))
            except Exception:
                continue

        if planar_candidates:
            planar_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return planar_candidates[0][2].copy()

        any_candidates.sort(key=lambda item: item[0], reverse=True)
        return any_candidates[0][1].copy() if any_candidates else None

    def _shape_from_obj(obj):
        if not obj:
            return None
        try:
            if hasattr(obj, "LinkedObject") and obj.LinkedObject:
                obj = obj.LinkedObject
        except Exception:
            pass

        try:
            if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                return obj.Shape.copy()
        except Exception:
            pass

        try:
            if hasattr(obj, "Tip") and obj.Tip and hasattr(obj.Tip, "Shape"):
                tip_shape = obj.Tip.Shape
                if tip_shape and not tip_shape.isNull():
                    return tip_shape.copy()
        except Exception:
            pass

        return None

    def _axis_vector_from_obj(obj, axis_name="z"):
        if not obj:
            return None

        axis_map = {
            "x": App.Vector(1.0, 0.0, 0.0),
            "y": App.Vector(0.0, 1.0, 0.0),
            "z": App.Vector(0.0, 0.0, 1.0),
        }
        local_axis = axis_map.get(str(axis_name).lower(), App.Vector(0.0, 0.0, 1.0))

        try:
            if hasattr(obj, "LinkedObject") and obj.LinkedObject:
                obj = obj.LinkedObject
        except Exception:
            pass

        try:
            if hasattr(obj, "Placement") and hasattr(obj.Placement, "Rotation"):
                axis = obj.Placement.Rotation.multVec(local_axis)
                if axis.Length > 1e-9:
                    return axis.normalize()
        except Exception:
            pass

        try:
            if hasattr(obj, "Tip") and obj.Tip and hasattr(obj.Tip, "Placement"):
                axis = obj.Tip.Placement.Rotation.multVec(local_axis)
                if axis.Length > 1e-9:
                    return axis.normalize()
        except Exception:
            pass

        return None

    def _selected_inlay_shape(exclude_group=None):
        selection_ex = Gui.Selection.getSelectionEx() if Gui else []
        for sel in selection_ex:
            obj = getattr(sel, "Object", None)
            if _is_in_group_tree(obj, exclude_group):
                continue
            shape = _shape_from_obj(obj)
            if shape is not None:
                return shape, obj

        selection = Gui.Selection.getSelection() if Gui else []
        for obj in selection:
            if _is_in_group_tree(obj, exclude_group):
                continue
            shape = _shape_from_obj(obj)
            if shape is not None:
                return shape, obj

        return None, None

    def _selected_inlay_face(exclude_group=None):
        def _face_from_obj(obj):
            if not obj:
                return None
            try:
                # Resolve link objects if present
                if hasattr(obj, "LinkedObject") and obj.LinkedObject:
                    obj = obj.LinkedObject
            except Exception:
                pass

            try:
                if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                    face = _best_inlay_face_from_shape(obj.Shape)
                    if face is not None:
                        return face
            except Exception:
                pass

            try:
                # PartDesign Body often exposes result via Tip
                if hasattr(obj, "Tip") and obj.Tip and hasattr(obj.Tip, "Shape"):
                    tip_shape = obj.Tip.Shape
                    if tip_shape and not tip_shape.isNull():
                        face = _best_inlay_face_from_shape(tip_shape)
                        if face is not None:
                            return face
            except Exception:
                pass

            return None

        selection_ex = Gui.Selection.getSelectionEx() if Gui else []
        for sel in selection_ex:
            obj = getattr(sel, "Object", None)
            if _is_in_group_tree(obj, exclude_group):
                continue
            for sub in getattr(sel, "SubObjects", []) or []:
                if hasattr(sub, "Surface"):
                    return sub.copy()
            face = _face_from_obj(obj)
            if face is not None:
                return face

        # Also accept simple tree selection (object, no sub-face pick)
        selection = Gui.Selection.getSelection() if Gui else []
        for obj in selection:
            if _is_in_group_tree(obj, exclude_group):
                continue
            face = _face_from_obj(obj)
            if face is not None:
                return face

        return None

    def _inlay_face_from_type(inlay_type):
        link_name = f"linked_{inlay_type}_Inlay"
        link_obj = doc.getObject(link_name)
        if link_obj and hasattr(link_obj, "Shape") and link_obj.Shape and not link_obj.Shape.isNull():
            face = _best_inlay_face_from_shape(link_obj.Shape)
            if face is not None:
                return face

        _, source_obj = _get_inlay_source_object(inlay_type)
        if source_obj and hasattr(source_obj, "Shape") and source_obj.Shape and not source_obj.Shape.isNull():
            face = _best_inlay_face_from_shape(source_obj.Shape)
            if face is not None:
                return face

        return None

    def _inlay_shape_from_type(inlay_type):
        link_name = f"linked_{inlay_type}_Inlay"
        link_obj = doc.getObject(link_name)
        if link_obj and hasattr(link_obj, "Shape") and link_obj.Shape and not link_obj.Shape.isNull():
            return link_obj.Shape.copy(), link_obj

        _, source_obj = _get_inlay_source_object(inlay_type)
        if source_obj and hasattr(source_obj, "Shape") and source_obj.Shape and not source_obj.Shape.isNull():
            return source_obj.Shape.copy(), source_obj

        return None, None

    def _resolve_base_face(source_mode, exclude_group=None):
        if source_mode == "selected":
            return _selected_inlay_face(exclude_group=exclude_group), "selected"
        if source_mode in ["forearm", "handle", "butt_sleeve"]:
            return _inlay_face_from_type(source_mode), source_mode
        return None, source_mode

    def _resolve_target_section_object(section_mode):
        if not section_mode or section_mode == "selected":
            return None

        candidate_names = [section_mode, f"{section_mode}_outer"]
        for name in candidate_names:
            obj = doc.getObject(name)
            if obj and hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                return obj

        cue_components = doc.getObject("CueComponents")
        if cue_components:
            for obj in getattr(cue_components, "Group", []) or []:
                if getattr(obj, "Name", "") == section_mode or getattr(obj, "Label", "") == section_mode:
                    if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                        return obj
        return None

    def _infer_target_section_from_object(start_obj):
        cue_components = doc.getObject("CueComponents")
        if not cue_components or not start_obj:
            return None

        section_candidates = []
        for obj in getattr(cue_components, "Group", []) or []:
            if hasattr(obj, "Shape") and obj.Shape and not obj.Shape.isNull():
                section_candidates.append(obj)

        if not section_candidates:
            return None

        def _norm(value):
            return str(value or "").strip().lower().replace(" ", "_")

        seen = set()
        stack = [start_obj]
        while stack:
            current = stack.pop()
            current_name = getattr(current, "Name", None)
            if current_name and current_name in seen:
                continue
            if current_name:
                seen.add(current_name)

            cur_name = _norm(getattr(current, "Name", ""))
            cur_label = _norm(getattr(current, "Label", ""))

            for section_obj in section_candidates:
                sec_name_raw = getattr(section_obj, "Name", "")
                sec_label_raw = getattr(section_obj, "Label", sec_name_raw)
                sec_name = _norm(sec_name_raw)
                sec_label = _norm(sec_label_raw)

                if current == section_obj:
                    return sec_name_raw

                if cur_name == sec_name or cur_name == sec_label or cur_label == sec_name or cur_label == sec_label:
                    return sec_name_raw

                if sec_name and (sec_name in cur_name or sec_name in cur_label):
                    return sec_name_raw
                if sec_label and (sec_label in cur_name or sec_label in cur_label):
                    return sec_name_raw

            try:
                if hasattr(current, "LinkedObject") and current.LinkedObject:
                    stack.append(current.LinkedObject)
            except Exception:
                pass

            for parent in getattr(current, "InList", []) or []:
                stack.append(parent)

        return None

    def _infer_target_section_from_selection():
        selection_ex = Gui.Selection.getSelectionEx() if Gui else []
        for sel in selection_ex:
            obj = getattr(sel, "Object", None)
            inferred = _infer_target_section_from_object(obj)
            if inferred:
                return inferred

        selection = Gui.Selection.getSelection() if Gui else []
        for obj in selection:
            inferred = _infer_target_section_from_object(obj)
            if inferred:
                return inferred
        return None

    def _available_target_sections():
        items = [("selected", "Selected geometry axis")]
        seen = {"selected"}

        preferred = [
            ("forearm", "Forearm section"),
            ("handle", "Handle section"),
            ("butt_sleeve", "Butt sleeve section"),
        ]
        for key, label in preferred:
            obj = _resolve_target_section_object(key)
            if obj and key not in seen:
                items.append((key, label))
                seen.add(key)

        cue_components = doc.getObject("CueComponents")
        if cue_components:
            for obj in getattr(cue_components, "Group", []) or []:
                if not hasattr(obj, "Shape") or not obj.Shape or obj.Shape.isNull():
                    continue
                value = getattr(obj, "Name", "")
                if not value or value in seen:
                    continue
                label = getattr(obj, "Label", value)
                items.append((value, f"{label} section"))
                seen.add(value)

        return items

    def _segment_token_and_label(section_mode):
        token_map = {
            "forearm": ("Forearm", "Forearm"),
            "handle": ("Handle", "Handle"),
            "butt_sleeve": ("ButtSleeve", "Butt Sleeve"),
            "selected": ("Selected", "Selected"),
        }
        if section_mode in token_map:
            return token_map[section_mode]

        section_obj = _resolve_target_section_object(section_mode)
        if section_obj:
            raw_name = getattr(section_obj, "Name", section_mode)
            token = "".join(ch if ch.isalnum() else "_" for ch in raw_name).strip("_") or "Selected"
            label = getattr(section_obj, "Label", raw_name)
            return token, label

        raw_mode = str(section_mode or "Selected")
        token = "".join(ch if ch.isalnum() else "_" for ch in raw_mode).strip("_") or "Selected"
        return token, raw_mode

    def _section_group_label(section_mode):
        label_map = {
            "forearm": "forearm",
            "handle": "handle",
            "butt_sleeve": "butt sleeve",
        }
        if section_mode in label_map:
            return label_map[section_mode]
        section_obj = _resolve_target_section_object(section_mode)
        if section_obj:
            return str(getattr(section_obj, "Label", getattr(section_obj, "Name", "selected"))).lower()
        return "selected"

    def _ensure_section_group_under_components(section_mode):
        cue_components = doc.getObject("CueComponents")
        if cue_components is None:
            cue_components = doc.addObject("App::DocumentObjectGroup", "CueComponents")

        section_label = _section_group_label(section_mode)
        section_token, _ = _segment_token_and_label(section_mode)
        section_name = f"Inlays_{section_token}"

        for child in getattr(cue_components, "Group", []) or []:
            if getattr(child, "TypeId", "") == "App::DocumentObjectGroup":
                if getattr(child, "Name", "") == section_name or getattr(child, "Label", "") == section_label:
                    return cue_components, child

        section_group = doc.addObject("App::DocumentObjectGroup", section_name)
        section_group.Label = section_label
        cue_components.addObject(section_group)
        return cue_components, section_group

    def _remove_from_other_groups(obj, keep_group=None):
        for parent in list(getattr(obj, "InList", []) or []):
            if parent == keep_group:
                continue
            if getattr(parent, "TypeId", "") == "App::DocumentObjectGroup":
                try:
                    parent.removeObject(obj)
                except Exception:
                    pass

    def _is_section_related_object(section_mode, obj):
        if not obj:
            return False
        name = getattr(obj, "Name", "")
        label = getattr(obj, "Label", "")

        section_obj = _resolve_target_section_object(section_mode)
        section_obj_name = getattr(section_obj, "Name", "") if section_obj else ""
        section_obj_label = str(getattr(section_obj, "Label", "")).lower() if section_obj else ""
        section_token, _ = _segment_token_and_label(section_mode)

        section_name_prefixes = [
            section_mode,
            f"{section_mode}_",
            f"linked_{section_mode}",
            f"InlayPattern_{section_token}",
            f"Inlays_{section_token}",
        ]

        if section_obj_name:
            section_name_prefixes.extend([
                section_obj_name,
                f"{section_obj_name}_",
                f"linked_{section_obj_name}",
            ])

        section_label_map = {
            "forearm": "forearm",
            "handle": "handle",
            "butt_sleeve": "butt sleeve",
        }
        section_label = section_label_map.get(section_mode, section_obj_label or "selected")

        if any(name.startswith(prefix) for prefix in section_name_prefixes):
            return True
        if isinstance(label, str) and section_label in label.lower():
            return True
        return False

    def _ordered_unique(objs):
        seen = set()
        out = []
        for obj in objs:
            if not obj:
                continue
            obj_name = getattr(obj, "Name", None)
            if not obj_name or obj_name in seen:
                continue
            seen.add(obj_name)
            out.append(obj)
        return out

    def _organize_pattern_objects(section_mode, plan_obj, source_obj):
        if section_mode == "selected":
            cue_components = doc.getObject("CueComponents")
            if cue_components is None:
                cue_components = doc.addObject("App::DocumentObjectGroup", "CueComponents")

            try:
                _remove_from_other_groups(plan_obj, keep_group=cue_components)
                cue_components.addObject(plan_obj)
            except Exception:
                pass

            print("Organized pattern into CueComponents (selected target).")
            return

        cue_components, section_group = _ensure_section_group_under_components(section_mode)

        cue_components_group = list(getattr(cue_components, "Group", []) or [])
        section_group_existing = list(getattr(section_group, "Group", []) or [])

        ordered_candidates = []
        for obj in cue_components_group:
            if obj == section_group:
                continue
            if _is_section_related_object(section_mode, obj):
                ordered_candidates.append(obj)

        for obj in section_group_existing:
            if _is_section_related_object(section_mode, obj):
                ordered_candidates.append(obj)

        candidate_source = source_obj
        try:
            if candidate_source and hasattr(candidate_source, "LinkedObject") and candidate_source.LinkedObject:
                linked = candidate_source.LinkedObject
                if linked and getattr(linked, "Document", None) == doc:
                    candidate_source = linked
        except Exception:
            pass

        if plan_obj:
            ordered_candidates.append(plan_obj)

        moved_source = False
        if candidate_source and getattr(candidate_source, "Document", None) == doc:
            ordered_candidates.append(candidate_source)
            moved_source = True

        final_order = _ordered_unique(ordered_candidates)

        for obj in final_order:
            _remove_from_other_groups(obj, keep_group=section_group)

        # Rebuild section group order to match current model ordering as closely as possible.
        for obj in list(getattr(section_group, "Group", []) or []):
            try:
                section_group.removeObject(obj)
            except Exception:
                pass

        for obj in final_order:
            try:
                section_group.addObject(obj)
            except Exception:
                pass

        # Reposition the section subgroup in CueComponents where the section object should sit.
        top_level = list(getattr(cue_components, "Group", []) or [])
        related_top_level = [obj for obj in top_level if obj != section_group and _is_section_related_object(section_mode, obj)]
        related_names = {getattr(obj, "Name", "") for obj in related_top_level}

        # Preferred anchor: immediately after the segment's predecessor from AttachmentSupport.
        anchor_index = None
        section_obj = _resolve_target_section_object(section_mode)
        predecessor_obj = None
        try:
            support = getattr(section_obj, "AttachmentSupport", None)
            if support and isinstance(support, (list, tuple)) and len(support) > 0:
                first = support[0]
                if isinstance(first, (list, tuple)) and len(first) > 0:
                    predecessor_obj = first[0]
        except Exception:
            predecessor_obj = None

        if predecessor_obj is not None:
            pred_name = getattr(predecessor_obj, "Name", "")
            for idx, obj in enumerate(top_level):
                if getattr(obj, "Name", "") == pred_name:
                    anchor_index = idx + 1
                    break

        # Fallback anchor: where first related top-level object currently appears.
        if anchor_index is None:
            for idx, obj in enumerate(top_level):
                obj_name = getattr(obj, "Name", "")
                if obj_name in related_names:
                    anchor_index = idx
                    break

        # Build new top-level order: remove old related objects + section group, then insert section group at anchor.
        filtered_top_level = [
            obj for obj in top_level
            if obj != section_group and getattr(obj, "Name", "") not in related_names
        ]
        if anchor_index is None:
            anchor_index = len(filtered_top_level)
        else:
            # Convert original top-level index to filtered index.
            before_count = 0
            for idx, obj in enumerate(top_level):
                if idx >= anchor_index:
                    break
                obj_name = getattr(obj, "Name", "")
                if obj != section_group and obj_name not in related_names:
                    before_count += 1
            anchor_index = max(0, min(before_count, len(filtered_top_level)))

        new_top_level = filtered_top_level[:anchor_index] + [section_group] + filtered_top_level[anchor_index:]

        try:
            for obj in list(getattr(cue_components, "Group", []) or []):
                cue_components.removeObject(obj)
            for obj in new_top_level:
                cue_components.addObject(obj)
        except Exception:
            pass

        if moved_source:
            print(f"Organized pattern, source, and section objects into CueComponents/{section_group.Label} (order preserved).")
        else:
            print(f"Organized pattern and section objects into CueComponents/{section_group.Label} (order preserved). Source object is external and was not moved.")

    def _place_pattern_group_without_reorg(plan_obj):
        cue_components = doc.getObject("CueComponents")
        if cue_components is None:
            print("Created pattern group without reorganizing document tree.")
            return
        try:
            cue_components.addObject(plan_obj)
            print("Placed new pattern group under CueComponents without reorganizing other objects.")
        except Exception:
            print("Created pattern group without reorganizing document tree.")

    def _preserve_existing_group_placement(plan_obj):
        if not plan_obj:
            return
        parents = [p for p in (getattr(plan_obj, "InList", []) or []) if getattr(p, "TypeId", "") == "App::DocumentObjectGroup"]
        if parents:
            try:
                print(f"Preserving existing pattern group placement under '{parents[0].Label}'.")
            except Exception:
                pass
            return
        try:
            cue_components = doc.getObject("CueComponents")
            if cue_components is None:
                cue_components = doc.addObject("App::DocumentObjectGroup", "CueComponents")
            cue_components.addObject(plan_obj)
            print("Pattern group had no parent; placed under CueComponents.")
        except Exception:
            pass

    def _axis_center_for_section(section_mode, fallback_center, y_offset_mm):
        target_obj = _resolve_target_section_object(section_mode)
        if target_obj:
            bb = target_obj.Shape.BoundBox
            return App.Vector(bb.Center.x, bb.Center.y + y_offset_mm, bb.Center.z)
        return App.Vector(fallback_center.x, fallback_center.y + y_offset_mm, fallback_center.z)

    def _surface_radius_for_section(section_mode, y_value, axis_center):
        target_obj = _resolve_target_section_object(section_mode)
        if not target_obj or not hasattr(target_obj, "Shape"):
            return None

        shape = target_obj.Shape
        if not shape or shape.isNull():
            return None

        # Try an actual Y-slice first for better taper handling.
        try:
            bb = shape.BoundBox
            plane_size = max(bb.XLength, bb.YLength, bb.ZLength) * 2.0 + 10.0
            plane = Part.makePlane(
                plane_size,
                plane_size,
                App.Vector(axis_center.x - plane_size * 0.5, y_value, axis_center.z - plane_size * 0.5),
                App.Vector(0, 1, 0),
            )
            section = shape.section(plane)
            radial = []
            for edge in getattr(section, "Edges", []):
                for vertex in edge.Vertexes:
                    point = vertex.Point
                    radial.append(((point.x - axis_center.x) ** 2 + (point.z - axis_center.z) ** 2) ** 0.5)
            if radial:
                return max(radial)
        except Exception:
            pass

        # Fallback: bound-box based radius estimate.
        bb = shape.BoundBox
        return max(bb.XLength, bb.ZLength) * 0.5

    def _max_surface_radius_for_section(section_mode, axis_center):
        target_obj = _resolve_target_section_object(section_mode)
        if not target_obj or not hasattr(target_obj, "Shape"):
            return None
        shape = target_obj.Shape
        if not shape or shape.isNull():
            return None

        try:
            radial = []
            for vertex in getattr(shape, "Vertexes", []) or []:
                point = vertex.Point
                radial.append(((point.x - axis_center.x) ** 2 + (point.z - axis_center.z) ** 2) ** 0.5)
            if radial:
                return max(radial)
        except Exception:
            pass

        bb = shape.BoundBox
        return max(bb.XLength, bb.ZLength) * 0.5

    def _section_y_bounds(section_mode):
        target_obj = _resolve_target_section_object(section_mode)
        if not target_obj or not hasattr(target_obj, "Shape"):
            return None, None
        shape = target_obj.Shape
        if not shape or shape.isNull():
            return None, None
        bb = shape.BoundBox
        return bb.YMin, bb.YMax

    def _face_normal(face_obj):
        try:
            u_min, u_max, v_min, v_max = face_obj.ParameterRange
            u_mid = 0.5 * (u_min + u_max)
            v_mid = 0.5 * (v_min + v_max)
            normal = face_obj.normalAt(u_mid, v_mid)
            if normal.Length > 1e-9:
                return normal.normalize()
        except Exception:
            pass
        return None

    def _show_cnc_plan_dialog(defaults, selected_plan_group=None, prefer_task_panel=False):
        if QtGui is None:
            return defaults

        class _SelectAllDoubleSpin(QtGui.QDoubleSpinBox):
            def _select_all_now(self):
                try:
                    editor = self.lineEdit()
                    if editor is not None:
                        editor.selectAll()
                except Exception:
                    pass

            def focusInEvent(self, event):
                super().focusInEvent(event)
                self._select_all_now()

            def mouseReleaseEvent(self, event):
                super().mouseReleaseEvent(event)
                self._select_all_now()

        class _SelectAllSpin(QtGui.QSpinBox):
            def _select_all_now(self):
                try:
                    editor = self.lineEdit()
                    if editor is not None:
                        editor.selectAll()
                except Exception:
                    pass

            def focusInEvent(self, event):
                super().focusInEvent(event)
                self._select_all_now()

            def mouseReleaseEvent(self, event):
                super().mouseReleaseEvent(event)
                self._select_all_now()

        captured_selected_shape = None
        captured_selected_obj = None
        try:
            captured_selected_shape, captured_selected_obj = _selected_inlay_shape(exclude_group=selected_plan_group)
        except Exception:
            captured_selected_shape, captured_selected_obj = None, None
        captured_selected_name = ""
        if captured_selected_obj is not None:
            captured_selected_name = str(getattr(captured_selected_obj, "Name", "") or "")
        if not captured_selected_name:
            captured_selected_name = str(defaults.get("selected_source_name", "") or "")
        if not captured_selected_name and selected_plan_group is not None:
            try:
                recovered_shape, recovered_obj = _source_from_plan_group(selected_plan_group)
                if recovered_shape is not None and recovered_obj is not None:
                    captured_selected_name = str(getattr(recovered_obj, "Name", "") or "")
                    captured_selected_obj = recovered_obj
            except Exception:
                pass

        def _combo_value(combo, fallback=None):
            value = None
            try:
                value = combo.currentData()
            except Exception:
                value = None
            if value is None:
                try:
                    value = combo.itemData(combo.currentIndex())
                except Exception:
                    value = None
            if hasattr(value, "toString"):
                try:
                    value = value.toString()
                except Exception:
                    pass
            try:
                value = str(value)
            except Exception:
                value = fallback
            if value in [None, "", "None"]:
                return fallback
            return value

        use_task_panel = bool(QtCore is not None and Gui and hasattr(Gui, "Control"))

        if use_task_panel:
            form_widget = QtGui.QWidget()
            layout = QtGui.QFormLayout(form_widget)
        else:
            dialog = QtGui.QDialog()
            dialog.setWindowTitle("Pattern (Cues Workbench)")
            layout = QtGui.QFormLayout(dialog)

        target_combo = QtGui.QComboBox()
        for value, text in _available_target_sections():
            target_combo.addItem(text, value)

        mode_combo = QtGui.QComboBox()
        mode_combo.addItem("Polar (around section)", "polar")
        mode_combo.addItem("Grid (rows/columns)", "grid")

        inlay_scale_combo = QtGui.QComboBox()
        inlay_scale_combo.addItem("Fixed inlay size", "fixed")
        inlay_scale_combo.addItem("Cone-scaled inlay size (trapezoidal)", "cone_scaled")

        row_spacing_spin = _SelectAllDoubleSpin()
        row_spacing_spin.setDecimals(4)
        row_spacing_spin.setRange(-10000.0, 10000.0)
        row_spacing_spin.setSingleStep(0.01)
        row_spacing_spin.setValue(float(defaults["row_spacing_in"]))

        col_spacing_spin = _SelectAllDoubleSpin()
        col_spacing_spin.setDecimals(4)
        col_spacing_spin.setRange(0.0, 10000.0)
        col_spacing_spin.setSingleStep(0.01)
        col_spacing_spin.setValue(float(defaults["column_spacing_in"]))

        grid_start_angle_spin = _SelectAllDoubleSpin()
        grid_start_angle_spin.setDecimals(3)
        grid_start_angle_spin.setRange(-3600.0, 3600.0)
        grid_start_angle_spin.setSingleStep(1.0)
        grid_start_angle_spin.setValue(float(defaults["grid_start_angle_deg"]))

        grid_angle_spin = _SelectAllDoubleSpin()
        grid_angle_spin.setDecimals(3)
        grid_angle_spin.setRange(0.0, 3600.0)
        grid_angle_spin.setSingleStep(1.0)
        grid_angle_spin.setValue(float(defaults["grid_angle_step_deg"]))

        stagger_even_check = QtGui.QCheckBox("Offset even rows")
        stagger_even_check.setChecked(bool(defaults["stagger_even_rows"]))
        y_offset_spin = _SelectAllDoubleSpin()
        y_offset_spin.setDecimals(4)
        y_offset_spin.setRange(-10000.0, 10000.0)
        y_offset_spin.setSingleStep(0.01)
        y_offset_spin.setValue(float(defaults["y_offset_in"]))

        b_rotate_spin = _SelectAllDoubleSpin()
        b_rotate_spin.setDecimals(3)
        b_rotate_spin.setRange(-3600.0, 3600.0)
        b_rotate_spin.setSingleStep(1.0)
        b_rotate_spin.setValue(float(defaults["b_rotate_deg"]))

        polar_count_spin = _SelectAllSpin()
        polar_count_spin.setRange(1, 100000)
        polar_count_spin.setSingleStep(1)
        polar_count_spin.setValue(int(defaults["polar_count"]))

        polar_sweep_spin = _SelectAllDoubleSpin()
        polar_sweep_spin.setDecimals(3)
        polar_sweep_spin.setRange(0.0, 3600.0)
        polar_sweep_spin.setSingleStep(1.0)
        polar_sweep_spin.setValue(float(defaults["polar_sweep_deg"]))

        polar_start_spin = _SelectAllDoubleSpin()
        polar_start_spin.setDecimals(3)
        polar_start_spin.setRange(-3600.0, 3600.0)
        polar_start_spin.setSingleStep(1.0)
        polar_start_spin.setValue(float(defaults["polar_start_deg"]))
        surface_axis_combo = QtGui.QComboBox()
        surface_axis_combo.addItem("X axis", "x")
        surface_axis_combo.addItem("Y axis", "y")
        surface_axis_combo.addItem("Z axis", "z")
        surface_axis_flip_check = QtGui.QCheckBox("Flip inward/outward")
        trim_preview_check = QtGui.QCheckBox("Trim preview to section surface")
        trim_preview_check.setChecked(bool(defaults.get("trim_preview_to_section", True)))
        replace_selected_check = QtGui.QCheckBox("Replace selected pattern group")
        replace_selected_check.setChecked(bool(defaults.get("replace_selected_pattern", True)))
        replace_selected_check.setEnabled(bool(selected_plan_group is not None))

        target_index = target_combo.findData(defaults["target_section"])
        if target_index >= 0:
            target_combo.setCurrentIndex(target_index)

        mode_index = mode_combo.findData(defaults["pattern_mode"])
        if mode_index >= 0:
            mode_combo.setCurrentIndex(mode_index)

        scale_mode_index = inlay_scale_combo.findData(defaults.get("inlay_scale_mode", "fixed"))
        if scale_mode_index >= 0:
            inlay_scale_combo.setCurrentIndex(scale_mode_index)

        axis_index = surface_axis_combo.findData(defaults["surface_axis"])
        if axis_index >= 0:
            surface_axis_combo.setCurrentIndex(axis_index)
        surface_axis_flip_check.setChecked(bool(defaults["surface_axis_flip"]))

        source_label_text = "<none selected>"
        if captured_selected_obj is not None:
            source_label_text = str(getattr(captured_selected_obj, "Label", captured_selected_name))
        elif captured_selected_name:
            source_label_text = captured_selected_name
        layout.addRow("Inlay source (captured)", QtGui.QLabel(source_label_text))
        layout.addRow("Target section", target_combo)
        layout.addRow("Pattern mode", mode_combo)
        layout.addRow("Inlay size mode", inlay_scale_combo)
        layout.addRow("Row spacing (in)", row_spacing_spin)
        layout.addRow("Column spacing (in)", col_spacing_spin)
        layout.addRow("Grid start angle (deg)", grid_start_angle_spin)
        layout.addRow("Grid angle step (deg)", grid_angle_spin)
        layout.addRow("Grid stagger", stagger_even_check)
        layout.addRow("Row stagger amount", QtGui.QLabel("Half of effective column spacing"))
        layout.addRow("Y offset (in)", y_offset_spin)
        layout.addRow("B rotate between rows (deg)", b_rotate_spin)
        layout.addRow("Polar count", polar_count_spin)
        layout.addRow("Polar sweep (deg)", polar_sweep_spin)
        layout.addRow("Polar start angle (deg)", polar_start_spin)
        layout.addRow("Surface-facing axis", surface_axis_combo)
        layout.addRow("Axis flip", surface_axis_flip_check)
        layout.addRow("Trim preview", trim_preview_check)
        if selected_plan_group is not None:
            layout.addRow("Edit", replace_selected_check)

        def _safe_float(widget, fallback):
            try:
                return float(widget.text().strip())
            except Exception:
                return fallback

        def _safe_int(widget, fallback, minimum=1):
            try:
                value = int(float(widget.text().strip()))
            except Exception:
                value = fallback
            return max(minimum, value)

        def _update_mode_state(*args):
            mode = _combo_value(mode_combo, defaults.get("pattern_mode", "polar"))
            is_grid = mode == "grid"
            for w in [row_spacing_spin, col_spacing_spin, grid_start_angle_spin, grid_angle_spin,
                    stagger_even_check, b_rotate_spin]:
                w.setEnabled(is_grid)
            for w in [polar_count_spin, polar_sweep_spin, polar_start_spin]:
                w.setEnabled(not is_grid)

        mode_combo.currentIndexChanged.connect(_update_mode_state)
        mode_combo.activated.connect(_update_mode_state)
        _update_mode_state()

        def _collect_values():
            values = dict(defaults)
            values["source_mode"] = "selected"
            values["selected_source_name"] = captured_selected_name
            values["target_section"] = _combo_value(target_combo, defaults.get("target_section", "handle"))
            values["pattern_mode"] = _combo_value(mode_combo, defaults.get("pattern_mode", "polar"))
            values["inlay_scale_mode"] = _combo_value(inlay_scale_combo, defaults.get("inlay_scale_mode", "fixed"))
            values["rows"] = 1
            values["columns"] = 1
            values["row_spacing_in"] = float(row_spacing_spin.value())
            values["column_spacing_in"] = float(col_spacing_spin.value())
            values["grid_start_angle_deg"] = float(grid_start_angle_spin.value())
            values["grid_angle_step_deg"] = float(grid_angle_spin.value())
            values["stagger_even_rows"] = stagger_even_check.isChecked()
            values["y_offset_in"] = float(y_offset_spin.value())
            values["b_rotate_deg"] = float(b_rotate_spin.value())
            values["polar_count"] = max(1, int(polar_count_spin.value()))
            values["polar_sweep_deg"] = float(polar_sweep_spin.value())
            values["polar_start_deg"] = float(polar_start_spin.value())
            values["surface_axis"] = _combo_value(surface_axis_combo, defaults.get("surface_axis", "z"))
            values["surface_axis_flip"] = surface_axis_flip_check.isChecked()
            values["trim_preview_to_section"] = trim_preview_check.isChecked()
            values["isolate_trim_preview"] = False
            values["replace_selected_pattern"] = bool(replace_selected_check.isChecked())
            return values

        if use_task_panel:
            class _PatternTaskPanel(QtCore.QObject):
                done = QtCore.Signal()

                def __init__(self, form, collector):
                    super().__init__()
                    self.form = form
                    self._collector = collector
                    self.result = None
                    self.action = "cancel"

                def getStandardButtons(self):
                    return QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Apply | QtGui.QDialogButtonBox.Cancel

                def clicked(self, button):
                    if button == QtGui.QDialogButtonBox.Apply:
                        try:
                            self.result = self._collector()
                        except Exception as exc:
                            print(f"Invalid pattern settings: {exc}")
                            self.result = None
                            return False
                        self.action = "apply"
                        try:
                            Gui.Control.closeDialog()
                        except Exception:
                            pass
                        self.done.emit()
                        return True
                    return False

                def accept(self):
                    try:
                        self.result = self._collector()
                    except Exception as exc:
                        print(f"Invalid pattern settings: {exc}")
                        self.result = None
                        return False
                    self.action = "ok"
                    try:
                        Gui.Control.closeDialog()
                    except Exception:
                        pass
                    self.done.emit()
                    return True

                def reject(self):
                    self.result = None
                    self.action = "cancel"
                    try:
                        Gui.Control.closeDialog()
                    except Exception:
                        pass
                    self.done.emit()
                    return True

            panel = _PatternTaskPanel(form_widget, _collect_values)
            Gui.Control.showDialog(panel)
            event_loop = QtCore.QEventLoop()
            panel.done.connect(event_loop.quit)
            event_loop.exec_()
            return panel.result, panel.action

        buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec_() != QtGui.QDialog.Accepted:
            return None, "cancel"
        return _collect_values(), "ok"

    selected_plan_group = _selected_plan_group()
    defaults = _load_persistent_defaults()
    prefs_obj = _prefs()
    reopen_from_apply = bool(prefs_obj.GetBool("pattern_apply_reopen", False))
    if reopen_from_apply:
        prefs_obj.SetBool("pattern_apply_reopen", False)
    if selected_plan_group is not None:
        selected_settings = _read_group_settings(selected_plan_group)
        if isinstance(selected_settings, dict) and selected_settings:
            defaults.update(selected_settings)
            print(f"Loaded settings from selected pattern group '{selected_plan_group.Label}'.")
    params, dialog_action = _show_cnc_plan_dialog(defaults, selected_plan_group=selected_plan_group, prefer_task_panel=True)
    if params is None:
        if reopen_from_apply:
            try:
                visible_json = prefs_obj.GetString("pattern_apply_visible_names", "")
                if visible_json:
                    _restore_visible_object_names(json.loads(visible_json))
            except Exception:
                pass
            try:
                if hasattr(doc, "undo"):
                    apply_undo_count = int(prefs_obj.GetInt("pattern_apply_undo_count", 0))
                    undone = 0
                    for _ in range(max(0, apply_undo_count)):
                        try:
                            doc.undo()
                            undone += 1
                        except Exception:
                            break
                    if undone > 0:
                        print(f"Canceled pattern edit; undid {undone} Apply transaction(s).")
            except Exception:
                pass
            prefs_obj.SetBool("pattern_apply_session_active", False)
            prefs_obj.SetString("pattern_apply_visible_names", "")
            prefs_obj.SetInt("pattern_apply_undo_count", 0)
        print("Pattern canceled.")
        return

    tx_open = False

    _save_persistent_defaults(params)

    source_mode = params["source_mode"]
    target_section = params["target_section"]

    rebuilding_selected_group = bool(params.get("replace_selected_pattern", True) and selected_plan_group is not None)

    # When rebuilding from Apply/Edit flow, keep target section stable and do not
    # let transient GUI selection changes redirect the section.
    if rebuilding_selected_group and target_section == "selected":
        remembered_target = str(params.get("target_section_resolved", "")).strip()
        if remembered_target in ["forearm", "handle", "butt_sleeve"]:
            target_section = remembered_target
            print(f"Using remembered target section '{target_section}' for selected pattern rebuild.")

    if target_section == "selected":
        inferred_target = _infer_target_section_from_selection()
        if inferred_target:
            target_section = inferred_target
            print(f"Inferred target section '{target_section}' from current selection.")
        else:
            print("Could not infer target section from selection; using selected-axis mode.")

    params["target_section_resolved"] = target_section

    pattern_mode = params["pattern_mode"]
    inlay_scale_mode = str(params.get("inlay_scale_mode", "fixed"))
    selected_source_name = str(params.get("selected_source_name", "") or "")

    if source_mode == "selected":
        if selected_source_name:
            selected_source_obj = doc.getObject(selected_source_name)
            selected_source_shape = _shape_from_obj(selected_source_obj)
            if selected_source_shape is not None and not selected_source_shape.isNull():
                base_shape, source_axis_obj = selected_source_shape, selected_source_obj
                print(f"Using stored selected geometry source '{getattr(selected_source_obj, 'Label', selected_source_name)}'.")
            else:
                base_shape, source_axis_obj = None, None
        else:
            base_shape, source_axis_obj = None, None

        if (base_shape is None or base_shape.isNull()) and rebuilding_selected_group:
            base_shape, source_axis_obj = _source_from_plan_group(selected_plan_group)
            if base_shape is not None and not base_shape.isNull():
                print("Using source geometry from selected pattern group for rebuild.")

        if base_shape is None or base_shape.isNull():
            print("No captured inlay source is available. Select the inlay geometry before opening Pattern.")
            if reopen_from_apply:
                try:
                    visible_json = prefs_obj.GetString("pattern_apply_visible_names", "")
                    if visible_json:
                        _restore_visible_object_names(json.loads(visible_json))
                except Exception:
                    pass
                prefs_obj.SetBool("pattern_apply_session_active", False)
                prefs_obj.SetString("pattern_apply_visible_names", "")
                prefs_obj.SetBool("pattern_apply_reopen", False)
                prefs_obj.SetInt("pattern_apply_undo_count", 0)
            if tx_open:
                try:
                    doc.abortTransaction()
                except Exception:
                    pass
            return
    else:
        base_shape, source_axis_obj = _inlay_shape_from_type(source_mode)

    if source_mode == "selected" and rebuilding_selected_group and base_shape is not None and not base_shape.isNull():
        base_face = _best_inlay_face_from_shape(base_shape)
        base_source = "selected-pattern-source"
    else:
        base_face, base_source = _resolve_base_face(source_mode, exclude_group=None)
    if (base_shape is None or base_shape.isNull()) and base_face is not None and not base_face.isNull():
        base_shape = base_face.copy()

    surface_axis = str(params.get("surface_axis", "z")).lower()
    source_top_dir = _axis_vector_from_obj(source_axis_obj, surface_axis)
    if source_top_dir is None or source_top_dir.Length <= 1e-9:
        axis_fallback = {
            "x": App.Vector(1.0, 0.0, 0.0),
            "y": App.Vector(0.0, 1.0, 0.0),
            "z": App.Vector(0.0, 0.0, 1.0),
        }
        source_top_dir = axis_fallback.get(surface_axis, App.Vector(0.0, 0.0, 1.0))
    if params.get("surface_axis_flip", False):
        source_top_dir = App.Vector(-source_top_dir.x, -source_top_dir.y, -source_top_dir.z)
    source_top_dir = source_top_dir.normalize()

    if base_shape is None or base_shape.isNull():
        print(f"No valid inlay source found for '{base_source}'.")
        print("Select an inlay face/object in the tree or ensure linked inlay exists for the target section.")
        return

    if base_face is None or base_face.isNull():
        base_face = _best_inlay_face_from_shape(base_shape)
    if base_face is None or base_face.isNull():
        print("Could not determine a reference face for orientation from selected shape.")
        return

    row_spacing_mm = params["row_spacing_in"] * 25.4
    column_spacing_mm = params["column_spacing_in"] * 25.4
    grid_start_angle_deg = params["grid_start_angle_deg"]
    grid_angle_step_deg = params["grid_angle_step_deg"]
    stagger_even_rows = bool(params.get("stagger_even_rows", params.get("stagger_even_columns", False)))
    y_offset_mm = params["y_offset_in"] * 25.4
    b_rotate_deg = params["b_rotate_deg"]
    polar_count = params["polar_count"]
    polar_sweep_deg = params["polar_sweep_deg"]
    polar_start_deg = params["polar_start_deg"]
    segment_token, segment_label = _segment_token_and_label(target_section)

    try:
        if hasattr(doc, "openTransaction"):
            doc.openTransaction("Create Inlay Pattern")
            tx_open = True
    except Exception:
        tx_open = False

    if bool(params.get("replace_selected_pattern", True)) and selected_plan_group is not None:
        plan_group = selected_plan_group
        _clear_plan_group(plan_group)
        if target_section == "selected":
            plan_group.Label = "Inlay Pattern"
        else:
            plan_group.Label = f"{segment_label} Inlay Pattern"
        print(f"Rebuilding selected pattern group '{plan_group.Label}'.")
    else:
        plan_group = doc.addObject("App::DocumentObjectGroup", _next_name(f"InlayPattern_{segment_token}"))
        if target_section == "selected":
            plan_group.Label = "Inlay Pattern"
        else:
            plan_group.Label = f"{segment_label} Inlay Pattern"
        _attach_pattern_group_view_provider(plan_group)
        _place_pattern_group_without_reorg(plan_group)

    _write_group_settings(plan_group, params)

    inlays_group = doc.addObject("App::DocumentObjectGroup", _next_name("Inlays"))
    inlays_group.Label = "Inlays"
    plan_group.addObject(inlays_group)
    try:
        if hasattr(inlays_group, "ViewObject") and inlays_group.ViewObject is not None:
            inlays_group.ViewObject.Visibility = False
    except Exception:
        pass

    target_section_obj = _resolve_target_section_object(target_section)
    trim_preview_enabled = bool(params.get("trim_preview_to_section", True))
    trim_preview_group = None
    if trim_preview_enabled:
        trim_preview_group = doc.addObject("App::DocumentObjectGroup", _next_name("InlayTrimPreview"))
        trim_preview_group.Label = "Inlay Trim Preview"
        plan_group.addObject(trim_preview_group)

    helper_group = None

    def _collect_helper_objects_into_folder():
        nonlocal helper_group
        try:
            direct_children = list(getattr(plan_group, "Group", []) or [])
            for child in direct_children:
                if child == inlays_group:
                    continue
                if trim_preview_group is not None and child == trim_preview_group:
                    continue
                if getattr(child, "TypeId", "") == "App::DocumentObjectGroup":
                    continue

                if helper_group is None:
                    helper_group = doc.addObject("App::DocumentObjectGroup", _next_name("InlayHelpers"))
                    helper_group.Label = "Inlay Helpers"
                    plan_group.addObject(helper_group)

                helper_group.addObject(child)
        except Exception:
            pass

    base_center = base_shape.BoundBox.Center
    axis_center = _axis_center_for_section(target_section, base_center, y_offset_mm)
    section_max_radius = _max_surface_radius_for_section(target_section, axis_center)
    placements = []

    source_link_target = source_axis_obj
    try:
        if source_link_target and hasattr(source_link_target, "LinkedObject") and source_link_target.LinkedObject:
            source_link_target = source_link_target.LinkedObject
    except Exception:
        pass

    use_link_instances = bool(
        source_link_target
        and hasattr(source_link_target, "Placement")
        and hasattr(source_link_target, "Shape")
        and source_link_target.Shape
        and not source_link_target.Shape.isNull()
    )
    source_base_placement = App.Placement(source_link_target.Placement) if use_link_instances else None

    radial_vec_seed = App.Vector(base_center.x - axis_center.x, 0.0, base_center.z - axis_center.z)
    radial_len_seed = (radial_vec_seed.x ** 2 + radial_vec_seed.z ** 2) ** 0.5
    if radial_len_seed < 1e-6:
        radial_dir_seed = App.Vector(0.0, 0.0, 1.0)
    else:
        radial_dir_seed = App.Vector(radial_vec_seed.x / radial_len_seed, 0.0, radial_vec_seed.z / radial_len_seed)

    def _rotation_from_to(src_vec, dst_vec):
        src = App.Vector(src_vec.x, src_vec.y, src_vec.z)
        dst = App.Vector(dst_vec.x, dst_vec.y, dst_vec.z)
        if src.Length <= 1e-9 or dst.Length <= 1e-9:
            return App.Rotation()
        src = src.normalize()
        dst = dst.normalize()
        dot = max(-1.0, min(1.0, src.dot(dst)))
        if abs(dot - 1.0) <= 1e-9:
            return App.Rotation()
        if abs(dot + 1.0) <= 1e-9:
            fallback_axis = App.Vector(0.0, 1.0, 0.0).cross(src)
            if fallback_axis.Length <= 1e-9:
                fallback_axis = App.Vector(1.0, 0.0, 0.0).cross(src)
            return App.Rotation(fallback_axis, 180.0)
        rot_axis = src.cross(dst)
        return App.Rotation(rot_axis, math.degrees(math.acos(dot)))

    def _placement_rotated_about_point(placement, rotation, point):
        p = App.Placement(placement)
        if rotation.Angle <= 1e-12:
            return p
        p.Base = rotation.multVec(p.Base - point) + point
        p.Rotation = rotation.multiply(p.Rotation)
        return p

    def _create_plan_instance(base_name, label, shape_obj=None, placement=None, scale_factor=1.0):
        sf = float(scale_factor) if scale_factor is not None else 1.0
        if sf <= 1e-9:
            sf = 1.0
        if use_link_instances and placement is not None:
            inst = doc.addObject("App::Link", _next_name(base_name))
            inst.LinkedObject = source_link_target
            if hasattr(inst, "LinkTransform"):
                inst.LinkTransform = True
            inst.Placement = placement
            if abs(sf - 1.0) > 1e-9:
                try:
                    if hasattr(inst, "ScaleVector"):
                        inst.ScaleVector = App.Vector(sf, sf, sf)
                    elif hasattr(inst, "Scale"):
                        inst.Scale = sf
                    elif hasattr(inst, "ScaleList"):
                        inst.ScaleList = [sf, sf, sf]
                except Exception:
                    pass
        else:
            inst = doc.addObject("Part::Feature", _next_name(base_name))
            shape_for_instance = shape_obj.copy() if shape_obj is not None else None
            if shape_for_instance is not None and abs(sf - 1.0) > 1e-9:
                try:
                    scale_center = shape_for_instance.BoundBox.Center
                    shape_for_instance.scale(scale_center, sf)
                except Exception:
                    pass
            inst.Shape = shape_for_instance
        inst.Label = label
        inlays_group.addObject(inst)

        if trim_preview_group and target_section_obj and hasattr(target_section_obj, "Shape") and target_section_obj.Shape and not target_section_obj.Shape.isNull():
            try:
                inst_shape = inst.Shape
                if inst_shape and not inst_shape.isNull():
                    trimmed = inst_shape.common(target_section_obj.Shape)
                    if trimmed and not trimmed.isNull() and len(getattr(trimmed, "Solids", []) or []) > 0:
                        trim_feat = doc.addObject("Part::Feature", _next_name(f"Trim_{base_name}"))
                        trim_feat.Shape = trimmed
                        trim_feat.Label = f"Trim {label}"
                        trim_preview_group.addObject(trim_feat)
            except Exception:
                pass
        return inst

    def _align_shape_to_surface(shape_obj, reference_face, target_y):
        local_axis_center = App.Vector(axis_center.x, target_y, axis_center.z)
        aligned = shape_obj.copy()
        aligned_center = aligned.BoundBox.Center

        # Align solid local Z as "top" normal onto cue radial surface normal.
        # User convention: inlay Z axis is the face-up/top direction.
        source_top = source_top_dir
        align_rotation = _rotation_from_to(source_top, radial_dir_seed)
        if align_rotation.Angle > 1e-12:
            aligned.rotate(aligned_center, align_rotation.Axis, math.degrees(align_rotation.Angle))

        aligned_center = aligned.BoundBox.Center
        local_radius = _surface_radius_for_section(target_section, target_y, local_axis_center)
        if local_radius is None or local_radius <= 1e-6:
            local_radius = max(radial_len_seed, 1.0)

        # On tapered sections, centerline radius can leave a sliver where the inlay spans
        # toward larger diameters. Use the largest local radius covered by the inlay's
        # Y-span so the whole patch stays flush.
        flush_radius = local_radius
        try:
            half_span_y = 0.5 * aligned.BoundBox.YLength
            sample_ys = [target_y - half_span_y, target_y, target_y + half_span_y]
            sample_radii = []
            for sample_y in sample_ys:
                sample_axis_center = App.Vector(local_axis_center.x, sample_y, local_axis_center.z)
                sample_radius = _surface_radius_for_section(target_section, sample_y, sample_axis_center)
                if sample_radius is not None and sample_radius > 1e-6:
                    sample_radii.append(sample_radius)
            if sample_radii:
                flush_radius = max(sample_radii)
        except Exception:
            flush_radius = local_radius

        outward_extent = 0.0
        try:
            projections = []
            for vertex in getattr(aligned, "Vertexes", []) or []:
                point = vertex.Point
                rel = App.Vector(point.x - aligned_center.x, point.y - aligned_center.y, point.z - aligned_center.z)
                projections.append(rel.dot(radial_dir_seed))
            if projections:
                outward_extent = max(projections)
        except Exception:
            outward_extent = 0.0

        center_radius = max(0.0, flush_radius - max(0.0, outward_extent))

        target_center = App.Vector(
            local_axis_center.x + radial_dir_seed.x * center_radius,
            target_y,
            local_axis_center.z + radial_dir_seed.z * center_radius,
        )

        aligned.translate(
            App.Vector(
                target_center.x - aligned_center.x,
                target_center.y - aligned_center.y,
                target_center.z - aligned_center.z,
            )
        )
        return aligned, local_axis_center, local_radius, align_rotation, target_center

    def _shape_span_along_direction(shape_obj, direction_vec):
        try:
            direction = App.Vector(direction_vec.x, direction_vec.y, direction_vec.z)
            if direction.Length <= 1e-9:
                return 0.0
            direction = direction.normalize()
            dots = []

            # Vertex-only projection can underestimate curved geometry (e.g. arcs),
            # which then allows overlap. Include BB corners for a conservative span.
            bb = shape_obj.BoundBox
            bb_points = [
                App.Vector(bb.XMin, bb.YMin, bb.ZMin),
                App.Vector(bb.XMin, bb.YMin, bb.ZMax),
                App.Vector(bb.XMin, bb.YMax, bb.ZMin),
                App.Vector(bb.XMin, bb.YMax, bb.ZMax),
                App.Vector(bb.XMax, bb.YMin, bb.ZMin),
                App.Vector(bb.XMax, bb.YMin, bb.ZMax),
                App.Vector(bb.XMax, bb.YMax, bb.ZMin),
                App.Vector(bb.XMax, bb.YMax, bb.ZMax),
            ]
            for point in bb_points:
                dots.append(point.x * direction.x + point.y * direction.y + point.z * direction.z)

            for vertex in getattr(shape_obj, "Vertexes", []) or []:
                point = vertex.Point
                dots.append(point.x * direction.x + point.y * direction.y + point.z * direction.z)
            if not dots:
                return max(bb.XLength, bb.YLength, bb.ZLength)
            return max(dots) - min(dots)
        except Exception:
            return 0.0

    reference_scale_radius = None

    def _row_scale_factor(row_radius):
        if inlay_scale_mode != "cone_scaled":
            return 1.0
        rr = float(row_radius or 0.0)
        ref = float(reference_scale_radius or 0.0)
        if rr <= 1e-9 or ref <= 1e-9:
            return 1.0
        factor = rr / ref
        return max(0.2, min(5.0, factor))

    if pattern_mode == "polar":
        angle_step = (polar_sweep_deg / float(polar_count)) if polar_count > 0 else 0.0
        aligned_shape, _, surface_radius, align_rotation, aligned_target_center = _align_shape_to_surface(
            base_shape, base_face, axis_center.y
        )
        reference_scale_radius = surface_radius
        polar_scale = _row_scale_factor(surface_radius)

        polar_row = []
        for idx in range(polar_count):
            angle = polar_start_deg + (idx * angle_step)
            if use_link_instances:
                instance_placement = App.Placement(source_base_placement)
                instance_placement = _placement_rotated_about_point(instance_placement, align_rotation, base_center)
                instance_placement.Base = instance_placement.Base + (aligned_target_center - base_center)
                polar_rotation = App.Rotation(App.Vector(0, 1, 0), angle)
                instance_placement = _placement_rotated_about_point(instance_placement, polar_rotation, axis_center)
                _create_plan_instance(
                    f"Inlay_R1_C{idx+1}",
                    f"Inlay R1 C{idx+1}",
                    placement=instance_placement,
                    scale_factor=polar_scale,
                )
            else:
                shape_copy = aligned_shape.copy()
                shape_copy.rotate(axis_center, App.Vector(0, 1, 0), angle)
                _create_plan_instance(
                    f"Inlay_R1_C{idx+1}",
                    f"Inlay R1 C{idx+1}",
                    shape_obj=shape_copy,
                    scale_factor=polar_scale,
                )
            polar_row.append((idx + 1, angle, axis_center.y))

        placements.append(polar_row)
    else:
        surface_radius = None
        section_y_min, section_y_max = _section_y_bounds(target_section)
        effective_row_spacing_mm = row_spacing_mm

        # Derive a safe minimum row pitch from inlay Y-span to prevent row overlap.
        probe_seed, _, _, _, _ = _align_shape_to_surface(base_shape, base_face, axis_center.y)
        probe_row_span_y = max(0.0, probe_seed.BoundBox.YLength)
        min_row_pitch_mm = probe_row_span_y * 1.02 if probe_row_span_y > 1e-9 else 0.0
        if inlay_scale_mode == "cone_scaled" and min_row_pitch_mm > 1e-9:
            try:
                r_ref = _surface_radius_for_section(
                    target_section,
                    axis_center.y,
                    App.Vector(axis_center.x, axis_center.y, axis_center.z),
                )
            except Exception:
                r_ref = None
            if r_ref and r_ref > 1e-9:
                try:
                    if section_y_min is not None and section_y_max is not None:
                        r_min = _surface_radius_for_section(
                            target_section,
                            section_y_min,
                            App.Vector(axis_center.x, section_y_min, axis_center.z),
                        )
                        r_max = _surface_radius_for_section(
                            target_section,
                            section_y_max,
                            App.Vector(axis_center.x, section_y_max, axis_center.z),
                        )
                        radii = [r for r in [r_min, r_max, r_ref] if r is not None and r > 1e-9]
                        if radii:
                            max_scale = max(radii) / r_ref
                            min_row_pitch_mm *= max(1.0, max_scale)
                except Exception:
                    pass
        if min_row_pitch_mm > 1e-9:
            spacing_sign = -1.0 if row_spacing_mm < 0.0 else 1.0
            if abs(effective_row_spacing_mm) < min_row_pitch_mm:
                old_spacing = effective_row_spacing_mm
                effective_row_spacing_mm = spacing_sign * min_row_pitch_mm
                print(
                    f"Raised row spacing from {old_spacing:.3f} mm to {effective_row_spacing_mm:.3f} mm to prevent overlap."
                )

        # Keep all row centers (including inlay Y half-span) inside section length.
        # Grid mode auto-fills the full usable section based on effective row spacing.
        if section_y_min is not None and section_y_max is not None:
            probe_half_span_y = 0.5 * probe_seed.BoundBox.YLength
            usable_y_min = section_y_min + probe_half_span_y
            usable_y_max = section_y_max - probe_half_span_y

            if usable_y_min > usable_y_max:
                print("Section is too short for this inlay size; no rows can be placed within section length.")
                rows = 0
            else:
                if abs(effective_row_spacing_mm) <= 1e-9:
                    max_fit_rows = 1
                else:
                    max_fit_rows = int(math.floor((usable_y_max - usable_y_min) / abs(effective_row_spacing_mm))) + 1

                max_fit_rows = max(0, max_fit_rows)
                rows = max_fit_rows
                print(
                    f"Auto-filled {rows} row(s) from spacing over section length (Rows input ignored in fill mode)."
                )
                if rows <= 1:
                    print(
                        "Row spacing has no visible effect because only one row fits within section length."
                    )

                # Center fitted rows so end margins are balanced.
                if rows > 0:
                    if abs(effective_row_spacing_mm) <= 1e-9:
                        centered_start_y = 0.5 * (usable_y_min + usable_y_max)
                    else:
                        placed_span = (rows - 1) * abs(effective_row_spacing_mm)
                        usable_span = max(0.0, usable_y_max - usable_y_min)
                        slack = max(0.0, usable_span - placed_span)
                        if effective_row_spacing_mm >= 0.0:
                            centered_start_y = usable_y_min + (0.5 * slack)
                        else:
                            centered_start_y = usable_y_max - (0.5 * slack)

                    if abs(axis_center.y - centered_start_y) > 1e-6:
                        axis_center = App.Vector(axis_center.x, centered_start_y, axis_center.z)
                        print("Centered row pattern within usable section length.")
        else:
            rows = 1
            print("Section bounds unavailable; using a single row (auto-fill requires section bounds).")

        try:
            reference_scale_radius = _surface_radius_for_section(
                target_section,
                axis_center.y,
                App.Vector(axis_center.x, axis_center.y, axis_center.z),
            )
        except Exception:
            reference_scale_radius = None

        if rows > 1 and abs(effective_row_spacing_mm) <= 1e-9:
            rows = 1
            print("Row spacing is zero; limited to one row to avoid duplicate overlap.")

        previous_row_y = None
        previous_row_span_y = None

        for row in range(rows):
            row_items = []
            row_y = axis_center.y + row * effective_row_spacing_mm
            row_seed, row_axis_center, row_radius, align_rotation, row_target_center = _align_shape_to_surface(
                base_shape, base_face, row_y
            )
            if surface_radius is None:
                surface_radius = row_radius
            row_scale_factor = _row_scale_factor(row_radius)
            row_span_y = max(0.0, row_seed.BoundBox.YLength) * row_scale_factor

            if previous_row_y is not None and previous_row_span_y is not None and row_span_y > 1e-9:
                min_row_sep = 0.5 * (previous_row_span_y + row_span_y) * 1.01
                if abs(row_y - previous_row_y) + 1e-6 < min_row_sep:
                    print(
                        f"Row {row+1}: skipped (row spacing too tight for non-overlap; required {min_row_sep:.3f} mm)."
                    )
                    continue

            # Convert optional linear column spacing (in) to additional angular step on this row radius.
            # This keeps all copies on-cylinder instead of translating them off the surface.
            spacing_angle_step_deg = 0.0
            if row_radius > 1e-9 and abs(column_spacing_mm) > 1e-9:
                spacing_angle_step_deg = (column_spacing_mm / (2.0 * math.pi * row_radius)) * 360.0

            tangent_dir = App.Vector(-radial_dir_seed.z, 0.0, radial_dir_seed.x)
            if tangent_dir.Length <= 1e-9:
                tangent_dir = App.Vector(1.0, 0.0, 0.0)
            tangent_span_mm = _shape_span_along_direction(row_seed, tangent_dir)
            tangent_span_mm *= row_scale_factor
            min_step_deg = 0.0
            if row_radius > 1e-9 and tangent_span_mm > 1e-9:
                min_step_deg = math.degrees((tangent_span_mm * 1.02) / row_radius)

            if min_step_deg > 1e-9:
                max_fit_columns = max(1, int(math.floor(360.0 / min_step_deg)))
            else:
                max_fit_columns = max(1, int(math.floor(360.0 / max(1e-6, grid_angle_step_deg)))) if abs(grid_angle_step_deg) > 1e-9 else max_fit_columns

            circumference_mm = 2.0 * math.pi * row_radius if row_radius > 1e-9 else 0.0
            target_spacing_mm = abs(column_spacing_mm)
            if target_spacing_mm <= 1e-9:
                target_spacing_mm = max(1e-6, tangent_span_mm * 1.02)
                print(
                    f"Row {row+1}: column spacing was zero; using auto spacing {target_spacing_mm:.3f} mm from inlay footprint."
                )

            if circumference_mm <= 1e-9:
                row_columns = 1
            else:
                spacing_fill_columns = max(1, int(math.floor(circumference_mm / target_spacing_mm)))
                row_columns = min(max_fit_columns, spacing_fill_columns)

            row_step_deg = 360.0 / float(max(1, row_columns))
            actual_spacing_mm = circumference_mm / float(max(1, row_columns)) if circumference_mm > 1e-9 else 0.0
            print(
                f"Row {row+1}: spacing-filled {row_columns} column(s), target {target_spacing_mm:.3f} mm, actual {actual_spacing_mm:.3f} mm."
            )

            auto_row_offset_deg = 0.5 * row_step_deg

            seen_angle_bins = set()
            placed_angles_mod = []
            for col in range(row_columns):
                row_angle_offset = auto_row_offset_deg if (stagger_even_rows and ((row + 1) % 2 == 0)) else 0.0
                angle = grid_start_angle_deg + row_angle_offset + (col * row_step_deg)
                angle_mod = angle % 360.0
                angle_bin = round(angle_mod, 6)
                if angle_bin in seen_angle_bins:
                    print(f"Row {row+1}: skipped duplicate angle {angle_mod:.6f}°.")
                    continue

                # Keep a final min-separation guard.
                too_close = False
                if min_step_deg > 1e-9:
                    for placed in placed_angles_mod:
                        d = abs(angle_mod - placed)
                        d = min(d, 360.0 - d)
                        if d + 1e-6 < min_step_deg:
                            too_close = True
                            break
                if too_close:
                    print(
                        f"Row {row+1}: skipped angle {angle_mod:.3f}° (below min separation {min_step_deg:.3f}°)."
                    )
                    continue

                seen_angle_bins.add(angle_bin)
                placed_angles_mod.append(angle_mod)
                if use_link_instances:
                    instance_placement = App.Placement(source_base_placement)
                    instance_placement = _placement_rotated_about_point(instance_placement, align_rotation, base_center)
                    instance_placement.Base = instance_placement.Base + (row_target_center - base_center)
                    row_rotation = App.Rotation(App.Vector(0, 1, 0), angle)
                    instance_placement = _placement_rotated_about_point(instance_placement, row_rotation, row_axis_center)
                    _create_plan_instance(
                        f"Inlay_R{row+1}_C{col+1}",
                        f"Inlay R{row+1} C{col+1}",
                        placement=instance_placement,
                        scale_factor=row_scale_factor,
                    )
                else:
                    shape_copy = row_seed.copy()
                    shape_copy.rotate(row_axis_center, App.Vector(0, 1, 0), angle)
                    _create_plan_instance(
                        f"Inlay_R{row+1}_C{col+1}",
                        f"Inlay R{row+1} C{col+1}",
                        shape_obj=shape_copy,
                        scale_factor=row_scale_factor,
                    )
                row_items.append((angle, row_y))
            if row_items:
                placements.append(row_items)
                previous_row_y = row_y
                previous_row_span_y = row_span_y

    if trim_preview_group and bool(params.get("isolate_trim_preview", False)):
        try:
            preview_names = {trim_preview_group.Name}
            for preview_child in getattr(trim_preview_group, "Group", []) or []:
                preview_names.add(preview_child.Name)

            # Only affect objects inside this pattern group.
            for child in getattr(plan_group, "Group", []) or []:
                if not hasattr(child, "ViewObject") or child.ViewObject is None:
                    continue
                child.ViewObject.Visibility = (child.Name in preview_names)

                # For nested groups under this plan group, keep only preview descendants visible.
                for nested in getattr(child, "Group", []) or []:
                    if hasattr(nested, "ViewObject") and nested.ViewObject is not None:
                        nested.ViewObject.Visibility = (nested.Name in preview_names)

            if hasattr(trim_preview_group, "ViewObject") and trim_preview_group.ViewObject is not None:
                trim_preview_group.ViewObject.Visibility = True
            for preview_child in getattr(trim_preview_group, "Group", []) or []:
                if hasattr(preview_child, "ViewObject") and preview_child.ViewObject is not None:
                    preview_child.ViewObject.Visibility = True

            print("Trim preview visibility applied within current pattern group only.")
        except Exception:
            pass

    _collect_helper_objects_into_folder()

    doc.recompute()
    action = "Created"
    print(
        f"{action} Cues-native Inlay pattern ({pattern_mode}) from '{base_source}' source around '{target_section}'."
    )
    if trim_preview_enabled:
        if target_section_obj and hasattr(target_section_obj, "Shape") and target_section_obj.Shape and not target_section_obj.Shape.isNull():
            print("Trim preview generated by clipping inlays to the section surface.")
        else:
            print("Trim preview requested, but no valid target section geometry was available.")
    if use_link_instances:
        print("Instances were created as App::Link objects and stay associative to the source inlay.")
    else:
        print("Instances were created as static shape copies (no associative source object available).")
    if pattern_mode == "polar":
        try:
            print(f"Polar surface radius used: {surface_radius:.3f} mm")
        except Exception:
            pass

    if tx_open:
        try:
            doc.commitTransaction()
        except Exception:
            pass

    if dialog_action == "apply":
        try:
            if Gui and QtCore is not None:
                try:
                    if not prefs_obj.GetBool("pattern_apply_session_active", False):
                        prefs_obj.SetString("pattern_apply_visible_names", json.dumps(_snapshot_visible_object_names()))
                        prefs_obj.SetBool("pattern_apply_session_active", True)
                        prefs_obj.SetInt("pattern_apply_undo_count", 0)
                    prefs_obj.SetInt(
                        "pattern_apply_undo_count",
                        int(prefs_obj.GetInt("pattern_apply_undo_count", 0)) + 1,
                    )
                except Exception:
                    pass
                try:
                    def _collect_related_visibility_names(seed_obj):
                        names = set()
                        if seed_obj is None:
                            return names
                        queue = [seed_obj]
                        seen = set()
                        while queue:
                            current = queue.pop(0)
                            current_name = getattr(current, "Name", "")
                            if not current_name or current_name in seen:
                                continue
                            seen.add(current_name)
                            names.add(current_name)
                            for child in getattr(current, "Group", []) or []:
                                queue.append(child)
                            for parent in getattr(current, "InList", []) or []:
                                queue.append(parent)
                        return names

                    visible_names = set()
                    for obj in getattr(doc, "Objects", []) or []:
                        name = getattr(obj, "Name", "")
                        if name.startswith("InlayPattern_"):
                            visible_names.add(name)
                            visible_names.update(_collect_related_visibility_names(obj))
                    if plan_group is not None:
                        visible_names.add(plan_group.Name)
                        visible_names.update(_collect_related_visibility_names(plan_group))
                    if target_section_obj is not None:
                        visible_names.update(_collect_related_visibility_names(target_section_obj))

                    source_obj = doc.getObject(selected_source_name) if selected_source_name else None
                    if source_obj is not None:
                        visible_names.update(_collect_related_visibility_names(source_obj))

                    for obj in getattr(doc, "Objects", []) or []:
                        if not hasattr(obj, "ViewObject") or obj.ViewObject is None:
                            continue
                        if _is_inside_partdesign_body(obj):
                            continue
                        obj.ViewObject.Visibility = (getattr(obj, "Name", "") in visible_names)

                    # Ensure descendants of visible pattern groups remain visible.
                    for obj in getattr(doc, "Objects", []) or []:
                        name = getattr(obj, "Name", "")
                        if not name.startswith("InlayPattern_"):
                            continue
                        for child in getattr(obj, "Group", []) or []:
                            if hasattr(child, "ViewObject") and child.ViewObject is not None:
                                child.ViewObject.Visibility = True
                            for nested in getattr(child, "Group", []) or []:
                                if hasattr(nested, "ViewObject") and nested.ViewObject is not None:
                                    nested.ViewObject.Visibility = True
                except Exception:
                    pass

                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(plan_group)

                def _reopen_pattern_dialog():
                    try:
                        prefs_obj.SetBool("pattern_apply_reopen", True)
                        Gui.runCommand("Cues_Pattern")
                    except Exception:
                        pass

                QtCore.QTimer.singleShot(0, _reopen_pattern_dialog)
                print("Applied pattern settings. Reopening Pattern panel for further changes.")
                return
        except Exception:
            pass

    if dialog_action == "ok":
        try:
            prefs_obj.SetBool("pattern_apply_session_active", False)
            prefs_obj.SetString("pattern_apply_visible_names", "")
            prefs_obj.SetBool("pattern_apply_reopen", False)
            prefs_obj.SetInt("pattern_apply_undo_count", 0)
        except Exception:
            pass


def edit_pattern():
    print("Edit Pattern has been disabled. Use Pattern to create a new pattern group.")


