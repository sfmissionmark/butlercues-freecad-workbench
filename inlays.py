
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

# Tuple of 3 bright colors as RGBA floats for `ViewObject.NormalColor`
_TOOLPATH_COLORS = (
    (1.0, 0.2, 0.2, 1.0),  # Bright red
    (0.2, 0.8, 0.2, 1.0),  # Bright green
    (1.0, 0.87, 0.0, 1.0),  # Golden Yellow
)

try:
    from PySide import QtGui, QtCore
except Exception:
    QtGui = None
    QtCore = None

def _select_bit_for_profile(profile_data, bit_candidates, min_runtime_threshold=10.0, min_travel_threshold=5.0):
    # Select bit based on runtime/travel thresholds
    for bit in sorted(bit_candidates, reverse=True):
        runtime = profile_data.get(bit, {}).get('runtime', 0)
        travel = profile_data.get(bit, {}).get('travel', 0)
        if runtime >= min_runtime_threshold and travel >= min_travel_threshold:
            return bit
    # Fallback: use smallest bit
    return min(bit_candidates)


import materials
import sketchershapes
import inlays_document as _inlays_document
import inlays_fillet as _inlays_fillet
import inlays_cam_plan as _inlays_cam_plan
import inlays_ui_helpers as _inlays_ui


_butler_managed_container_children = _inlays_ui._butler_managed_container_children
_butler_enable_undo_cleanup_observer = _inlays_ui._butler_enable_undo_cleanup_observer
_butler_enable_deleted_object_cleanup = _inlays_ui._butler_enable_deleted_object_cleanup
_butler_undo_cleanup_bundles = _inlays_ui._butler_undo_cleanup_bundles
_PatternGroupViewProvider = _inlays_ui._PatternGroupViewProvider


def _ensure_butler_container_cleanup_observer():
    return _inlays_ui._ensure_butler_container_cleanup_observer()


def _disable_butler_container_cleanup_observer():
    return _inlays_ui._disable_butler_container_cleanup_observer()


def _is_pattern_group_obj(obj):
    return _inlays_ui._is_pattern_group_obj(obj)


def _attach_pattern_group_view_provider(group_obj):
    return _inlays_ui._attach_pattern_group_view_provider(group_obj)


def _attach_pattern_group_view_providers(doc):
    return _inlays_ui._attach_pattern_group_view_providers(doc)


def _get_inlay_depth_inches(cue_doc, inlay_type, requested_depth_inches=0.2, clearance_inches=0.01):
    return _inlays_document._get_inlay_depth_inches(
        cue_doc,
        inlay_type,
        requested_depth_inches=requested_depth_inches,
        clearance_inches=clearance_inches,
    )


def _get_inlay_source_object(inlay_type):
    return _inlays_document._get_inlay_source_object(inlay_type)


def find_object_by_label(doc, label):
    return _inlays_document.find_object_by_label(doc, label)


def _apply_butler_post_defaults(job_obj, post_name="butler_fluidnc", post_args="--tool_change --inch"):
    return _inlays_document._apply_butler_post_defaults(
        job_obj,
        post_name=post_name,
        post_args=post_args,
    )



def create_inlay_document(inlay_type):
    return _inlays_document.create_inlay_document(inlay_type)



def new_document(doc_name, inlay_type, inlay_depth_inches=0.2):
    return _inlays_document.new_document(doc_name, inlay_type, inlay_depth_inches=inlay_depth_inches)


def draw_stock(cue_document_name="Unnamed", inlay_document_name="butt_sleeve_inlay"):
    return _inlays_document.draw_stock(
        cue_document_name=cue_document_name,
        inlay_document_name=inlay_document_name,
    )

    


def create_sketch(inlay_type="handle", inlay_name=None):
    return _inlays_document.create_sketch(inlay_type=inlay_type, inlay_name=inlay_name)




def fillet_for_cnc(
    noise=None,
    fillet_radius_inch=None,
    final_solid_name=None,
    preserve_unfilleted=None,
    allow_smaller_radius_fallback=None,
    require_full_coverage=None,
    show_dialog=True,
):
    return _inlays_fillet.fillet_for_cnc(
        noise=noise,
        fillet_radius_inch=fillet_radius_inch,
        final_solid_name=final_solid_name,
        preserve_unfilleted=preserve_unfilleted,
        allow_smaller_radius_fallback=allow_smaller_radius_fallback,
        require_full_coverage=require_full_coverage,
        show_dialog=show_dialog,
    )


def prepare_for_inlay():
    return _inlays_fillet.prepare_for_inlay()


def update_all_previews():
    return _inlays_document.update_all_previews()




def create_cam_job():
    return _inlays_cam_plan.create_cam_job()


def edit_pattern():
    return _inlays_cam_plan.edit_pattern()


def create_section_cnc_job(
    target=None,
    template_path=None,
    show_dialog=True,
    selected_tool_names_override=None,
    profile_keep_tool_down_override=None,
    profile_min_travel_override=None,
    profile_flip_x_axis_override=None,
):
    doc = App.ActiveDocument
    if not doc:
        print("No active document.")
        return

    _ensure_butler_container_cleanup_observer()

    pre_object_names = set()
    try:
        pre_object_names = {str(getattr(obj, "Name", "") or "") for obj in (getattr(doc, "Objects", []) or [])}
    except Exception:
        pre_object_names = set()

    def _record_undo_cleanup_bundle():
        try:
            if not _butler_enable_undo_cleanup_observer:
                return
            current_names = [
                str(getattr(obj, "Name", "") or "")
                for obj in (getattr(doc, "Objects", []) or [])
                if str(getattr(obj, "Name", "") or "") not in pre_object_names
            ]
            current_names = [name for name in current_names if name]
            if not current_names:
                return
            doc_name = str(getattr(doc, "Name", "") or "").strip()
            if not doc_name:
                return
            _butler_undo_cleanup_bundles.setdefault(doc_name, []).append(current_names)
        except Exception:
            pass

    try:
        import Path.Main.Job as PathJob
        import Path.Main.Gui.Job as PathGuiJob
        import Path.Preferences as PathPreferences
        import Part
        import glob
    except Exception as exc:
        print(f"CAM module unavailable: {exc}")
        print("Open FreeCAD with CAM workbench support enabled.")
        return

    def _next_name(base):
        idx = 1
        while doc.getObject(f"{base}_{idx}"):
            idx += 1
        return f"{base}_{idx}"

    def _safe_mm(value):
        try:
            return float(getattr(value, "Value", value))
        except Exception:
            return 0.0

    def _set_prop_length(obj, prop_name, value_mm):
        if not hasattr(obj, prop_name):
            return
        try:
            setattr(obj, prop_name, max(0.0, float(value_mm)))
        except Exception:
            try:
                getattr(obj, prop_name).Value = max(0.0, float(value_mm))
            except Exception:
                pass

    def _sanitize_job_stock(job_obj, model_obj):
        stock = getattr(job_obj, "Stock", None)
        if not stock or not model_obj:
            return
        try:
            shape = getattr(model_obj, "Shape", None)
            bb = shape.BoundBox if shape and not shape.isNull() else None
        except Exception:
            bb = None
        if not bb:
            return

        min_dim = 0.01
        ext_x_neg = _safe_mm(getattr(stock, "ExtXneg", 0.0))
        ext_x_pos = _safe_mm(getattr(stock, "ExtXpos", 0.0))
        ext_y_neg = _safe_mm(getattr(stock, "ExtYneg", 0.0))
        ext_y_pos = _safe_mm(getattr(stock, "ExtYpos", 0.0))
        ext_z_neg = _safe_mm(getattr(stock, "ExtZneg", 0.0))
        ext_z_pos = _safe_mm(getattr(stock, "ExtZpos", 0.0))

        ext_x_neg = max(0.0, ext_x_neg)
        ext_x_pos = max(0.0, ext_x_pos)
        ext_y_neg = max(0.0, ext_y_neg)
        ext_y_pos = max(0.0, ext_y_pos)
        ext_z_neg = max(0.0, ext_z_neg)
        ext_z_pos = max(0.0, ext_z_pos)

        if bb.XLength + ext_x_neg + ext_x_pos < min_dim:
            ext_x_pos = min_dim - bb.XLength - ext_x_neg
        if bb.YLength + ext_y_neg + ext_y_pos < min_dim:
            ext_y_pos = min_dim - bb.YLength - ext_y_neg
        if bb.ZLength + ext_z_neg + ext_z_pos < min_dim:
            ext_z_pos = min_dim - bb.ZLength - ext_z_neg

        _set_prop_length(stock, "ExtXneg", ext_x_neg)
        _set_prop_length(stock, "ExtXpos", ext_x_pos)
        _set_prop_length(stock, "ExtYneg", ext_y_neg)
        _set_prop_length(stock, "ExtYpos", ext_y_pos)
        _set_prop_length(stock, "ExtZneg", ext_z_neg)
        _set_prop_length(stock, "ExtZpos", ext_z_pos)

    def _inch_to_mm(value_inch):
        try:
            return float(App.Units.Quantity(f"{float(value_inch)} in").Value)
        except Exception:
            return float(value_inch) * 25.4

    def _set_stock_to_model_bounds(job_obj):
        stock = getattr(job_obj, "Stock", None)
        if not stock:
            return
        _set_prop_length(stock, "ExtXneg", 0.0)
        _set_prop_length(stock, "ExtXpos", 0.0)
        _set_prop_length(stock, "ExtYneg", 0.0)
        _set_prop_length(stock, "ExtYpos", 0.0)
        _set_prop_length(stock, "ExtZneg", 0.0)
        _set_prop_length(stock, "ExtZpos", 0.0)

    def _move_job_to_document_root(job_obj):
        if not job_obj:
            return
        for parent in list(getattr(job_obj, "InList", []) or []):
            if parent == job_obj:
                continue
            try:
                if hasattr(parent, "removeObject"):
                    parent.removeObject(job_obj)
            except Exception:
                pass

    def _move_job_to_document_root(job_obj):
        if not job_obj:
            return
        for parent in list(getattr(job_obj, "InList", []) or []):
            if parent == job_obj:
                continue
            try:
                if hasattr(parent, "removeObject"):
                    parent.removeObject(job_obj)
            except Exception:
                pass

    def _move_job_to_document_root(job_obj):
        if not job_obj:
            return
        for parent in list(getattr(job_obj, "InList", []) or []):
            if parent == job_obj:
                continue
            try:
                if hasattr(parent, "removeObject"):
                    parent.removeObject(job_obj)
            except Exception:
                pass


    def _unwrap_candidate(obj):
        if not obj:
            return None
        if str(getattr(obj, "TypeId", "")) == "App::Link":
            linked = getattr(obj, "LinkedObject", None)
            if linked:
                return linked
        return obj

    def _looks_like_fillet_source(obj):
        candidate = _unwrap_candidate(obj)
        if not candidate:
            return False
        for prop_name in (
            "FilletRadiusInch",
            "FilletSettingsSummary",
            "FilletSourceLabels",
            "FilletRequireFullCoverage",
        ):
            try:
                if hasattr(candidate, prop_name):
                    return True
            except Exception:
                pass
        return False

    def _warn_non_fillet_source(obj, workflow_label):
        if _looks_like_fillet_source(obj):
            return True
        obj_label = str(getattr(obj, "Label", getattr(obj, "Name", "selected object")) or "selected object")
        msg = (
            f"Warning: '{obj_label}' does not appear to be created by Fillet for CNC. "
            f"Continuing with {workflow_label} may produce accidental bad fit."
        )
        print(msg)
        try:
            if QtGui is not None and Gui is not None:
                result = QtGui.QMessageBox.warning(
                    None,
                    f"{workflow_label} Safety Warning",
                    msg + "\n\nContinue anyway?",
                    QtGui.QMessageBox.Ok | QtGui.QMessageBox.Cancel,
                    QtGui.QMessageBox.Ok,
                )
                if result != QtGui.QMessageBox.Ok:
                    print(f"{workflow_label} canceled by user.")
                    return False
        except Exception:
            pass
        return True

    def _is_valid_shape_candidate(obj):
        if not obj:
            return False
        try:
            shape = getattr(obj, "Shape", None)
        except Exception:
            return False
        if not shape:
            return False
        try:
            if shape.isNull():
                return False
        except Exception:
            return False
        try:
            if getattr(shape, "Solids", None) and len(shape.Solids) > 0:
                return True
        except Exception:
            pass
        try:
            return bool(getattr(shape, "Faces", None))
        except Exception:
            return False

    def _section_candidates():
        candidates = []
        seen = set()

        def _add_candidate(raw_obj):
            candidate = _unwrap_candidate(raw_obj)
            if not candidate:
                return
            name = getattr(candidate, "Name", "")
            if name and name in seen:
                return
            if not _is_valid_shape_candidate(candidate):
                return
            try:
                if not PathJob.ObjectJob.isBaseCandidate(candidate):
                    return
            except Exception:
                return
            if name:
                seen.add(name)
            candidates.append(candidate)

        selected = Gui.Selection.getSelection() if Gui else []
        for obj in selected:
            _add_candidate(obj)

        cue_components = doc.getObject("CueComponents")
        if cue_components:
            for obj in getattr(cue_components, "Group", []) or []:
                _add_candidate(obj)

        if not candidates:
            for obj in getattr(doc, "Objects", []) or []:
                _add_candidate(obj)

        return candidates

    candidates = _section_candidates()
    if not candidates:
        print("No valid section object found for CAM Job.")
        return

    target_obj = candidates[0]

    scripted_template_path = ""
    if template_path:
        try:
            scripted_template_path = str(template_path).strip()
        except Exception:
            scripted_template_path = ""

    if target is not None:
        resolved_target = None
        if isinstance(target, str):
            resolved_target = doc.getObject(target)
        else:
            resolved_target = target

        if not resolved_target:
            print(f"Target object not found: {target}")
            return
        target_obj = resolved_target

    if not _warn_non_fillet_source(target_obj, "Inlay Job"):
        return

    def _template_files():
        files = []
        try:
            for path in PathPreferences.searchPaths():
                files.extend(glob.glob(os.path.join(path, "job_*.json")))
        except Exception:
            return []

        # Normalize and dedupe while preserving order
        seen = set()
        out = []
        for f in files:
            norm = os.path.normpath(f)
            if norm in seen:
                continue
            seen.add(norm)
            out.append(norm)
        return out

    templates = _template_files()

    def _template_display_name(path):
        base = os.path.splitext(os.path.basename(path))[0]
        if base.lower().startswith("job_"):
            return base[4:]
        return base

    def _template_tool_controller_options(template_path):
        def _to_mm(value):
            try:
                return float(getattr(value, "Value", value))
            except Exception:
                pass
            try:
                return float(App.Units.Quantity(str(value)).Value)
            except Exception:
                pass
            try:
                return float(value)
            except Exception:
                return 0.0

        path = str(template_path or "").strip()
        if not path or not os.path.exists(path):
            return []

        try:
            with open(path, "rb") as fp:
                attrs = json.load(fp)
        except Exception:
            return []

        raw_tcs = attrs.get("ToolController") or []
        if not isinstance(raw_tcs, list):
            return []

        options = []
        for idx, tc in enumerate(raw_tcs, start=1):
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name", "") or "").strip() or f"TC{idx}"
            label = str(tc.get("label", "") or "").strip() or name
            try:
                tool_number = int(tc.get("nr")) if tc.get("nr") is not None else None
            except Exception:
                tool_number = None

            tool_data = tc.get("tool", {}) if isinstance(tc.get("tool", {}), dict) else {}
            tool_label = str(
                tool_data.get("name", "")
                or tool_data.get("label", "")
                or tc.get("toolname", "")
                or tc.get("tool", "")
                or ""
            ).strip()
            diameter_mm = _to_mm(
                tool_data.get("diameter", tool_data.get("Diameter", tc.get("diameter", tc.get("Diameter", 0.0))))
            )

            display = label
            if diameter_mm > 0.0:
                display = f"{display} ({diameter_mm / 25.4:.3f} in)"
            elif tool_label:
                display = f"{display} ({tool_label})"

            options.append(
                {
                    "name": name,
                    "label": label,
                    "tool_label": tool_label,
                    "diameter_mm": diameter_mm,
                    "tool_number": tool_number,
                    "display": display,
                }
            )

        options.sort(
            key=lambda item: (
                -(float(item.get("diameter_mm", 0.0) or 0.0)),
                str(item.get("display", "")).lower(),
            )
        )
        return options

    def _create_auto_container(original_obj, created_items, final_depth_in=None, settings=None):
        def _ensure_parent_group(preferred_label):
            try:
                for obj in (getattr(doc, "Objects", []) or []):
                    if getattr(obj, "TypeId", "") != "App::DocumentObjectGroup":
                        continue
                    if str(getattr(obj, "Label", "") or "").strip() == str(preferred_label):
                        return obj
            except Exception:
                pass
            try:
                existing = doc.getObject(str(preferred_label))
                if existing and getattr(existing, "TypeId", "") == "App::DocumentObjectGroup":
                    return existing
            except Exception:
                pass
            try:
                group = doc.addObject("App::DocumentObjectGroup", _next_name(str(preferred_label)))
                group.Label = str(preferred_label)
                return group
            except Exception:
                return None

        original_name = getattr(original_obj, "Name", "Solid")
        container = doc.addObject("App::DocumentObjectGroup", _next_name("InlayFolder"))
        depth_suffix = ""
        try:
            if final_depth_in is not None:
                depth_suffix = f" (final depth {max(0.0, float(final_depth_in)):.4f} in)"
        except Exception:
            depth_suffix = ""
        container.Label = f"{original_name} inlay{depth_suffix}"
        try:
            parent_group = _ensure_parent_group("Inlays")
            if parent_group:
                parent_group.addObject(container)
        except Exception:
            pass
        for item in created_items:
            if not item:
                continue
            try:
                container.addObject(item)
            except Exception:
                pass

        def _set_container_property(prop_name, prop_type, value):
            try:
                if not hasattr(container, prop_name):
                    container.addProperty(prop_type, prop_name, "ButlerCues Settings")
            except Exception:
                return
            try:
                setattr(container, prop_name, value)
            except Exception:
                pass

        _set_container_property("ButlerCuesWorkflow", "App::PropertyString", "Inlay Job")
        for key, value in dict(settings or {}).items():
            prop_name = f"ButlerCues{str(key)}"
            if isinstance(value, bool):
                _set_container_property(prop_name, "App::PropertyBool", bool(value))
            elif isinstance(value, int) and not isinstance(value, bool):
                _set_container_property(prop_name, "App::PropertyInteger", int(value))
            elif isinstance(value, float):
                _set_container_property(prop_name, "App::PropertyFloat", float(value))
            else:
                _set_container_property(prop_name, "App::PropertyString", str(value or ""))
        return container

    def _expand_container_in_tree(container_obj):
        if not container_obj or not Gui or QtGui is None:
            return
        try:
            from PySide import QtCore

            main_window = Gui.getMainWindow()
            if not main_window:
                return
            targets = {str(getattr(container_obj, "Name", "")), str(getattr(container_obj, "Label", ""))}

            for tree_view in main_window.findChildren(QtGui.QTreeView):
                model = tree_view.model()
                if not model:
                    continue

                def _expand_recursive(parent_index):
                    rows = model.rowCount(parent_index)
                    for row in range(rows):
                        idx = model.index(row, 0, parent_index)
                        text = str(model.data(idx) or "")
                        if text in targets:
                            tree_view.setExpanded(idx, True)
                            return True
                        if _expand_recursive(idx):
                            return True
                    return False

                if _expand_recursive(QtCore.QModelIndex()):
                    return
        except Exception:
            pass

    def _expand_targets_in_tree(target_objects):
        if not target_objects or not Gui or QtGui is None:
            return
        try:
            from PySide import QtCore

            target_tokens = set()
            for obj in list(target_objects or []):
                if not obj:
                    continue
                try:
                    name = str(getattr(obj, "Name", "") or "").strip()
                    if name:
                        target_tokens.add(name)
                except Exception:
                    pass
                try:
                    label = str(getattr(obj, "Label", "") or "").strip()
                    if label:
                        target_tokens.add(label)
                except Exception:
                    pass

            if not target_tokens:
                return

            main_window = Gui.getMainWindow()
            if not main_window:
                return

            for tree_view in main_window.findChildren(QtGui.QTreeView):
                model = tree_view.model()
                if not model:
                    continue

                def _expand_recursive(parent_index):
                    rows = model.rowCount(parent_index)
                    for row in range(rows):
                        idx = model.index(row, 0, parent_index)
                        text = str(model.data(idx) or "")
                        if text in target_tokens:
                            tree_view.setExpanded(idx, True)
                        _expand_recursive(idx)

                _expand_recursive(QtCore.QModelIndex())
        except Exception:
            pass

    def _expand_targets_in_tree(target_objects):
        if not target_objects or not Gui or QtGui is None:
            return
        try:
            from PySide import QtCore

            target_tokens = set()
            for obj in list(target_objects or []):
                if not obj:
                    continue
                try:
                    name = str(getattr(obj, "Name", "") or "").strip()
                    if name:
                        target_tokens.add(name)
                except Exception:
                    pass
                try:
                    label = str(getattr(obj, "Label", "") or "").strip()
                    if label:
                        target_tokens.add(label)
                except Exception:
                    pass

            if not target_tokens:
                return

            main_window = Gui.getMainWindow()
            if not main_window:
                return

            for tree_view in main_window.findChildren(QtGui.QTreeView):
                model = tree_view.model()
                if not model:
                    continue

                def _expand_recursive(parent_index):
                    rows = model.rowCount(parent_index)
                    for row in range(rows):
                        idx = model.index(row, 0, parent_index)
                        text = str(model.data(idx) or "")
                        if text in target_tokens:
                            tree_view.setExpanded(idx, True)
                        _expand_recursive(idx)

                _expand_recursive(QtCore.QModelIndex())
        except Exception:
            pass

    effective_template_path = scripted_template_path
    inlay_prefs = None
    inlay_count = 1
    nest_rotate_180 = True
    inlay_final_depth_in = 0.200
    nest_gap_in = 0.080
    profile_keep_tool_down = True
    profile_min_travel = True
    profile_flip_x_axis = False
    try:
        inlay_prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/ButlerCues/InlayJob")
        inlay_count = max(1, int(inlay_prefs.GetInt("inlay_count", 1)))
        nest_rotate_180 = bool(
            inlay_prefs.GetBool(
                "nest_rotate_180",
                inlay_prefs.GetBool("auto_rotate_xup", True),
            )
        )
        inlay_final_depth_in = max(0.0, float(inlay_prefs.GetFloat("final_depth_in", 0.200)))
        nest_gap_in = max(0.0, float(inlay_prefs.GetFloat("nest_gap_in", 0.080)))
        profile_keep_tool_down = bool(inlay_prefs.GetBool("profile_keep_tool_down", True))
        profile_min_travel = bool(inlay_prefs.GetBool("profile_min_travel", True))
        profile_flip_x_axis = bool(inlay_prefs.GetBool("profile_flip_x_axis", False))
    except Exception:
        inlay_prefs = None
        inlay_count = 1
        nest_rotate_180 = True
        inlay_final_depth_in = 0.200
        nest_gap_in = 0.080
        profile_keep_tool_down = True
        profile_min_travel = True
        profile_flip_x_axis = False

    if profile_keep_tool_down_override is not None:
        try:
            profile_keep_tool_down = bool(profile_keep_tool_down_override)
        except Exception:
            profile_keep_tool_down = True
    if profile_min_travel_override is not None:
        try:
            profile_min_travel = bool(profile_min_travel_override)
        except Exception:
            profile_min_travel = True
    if profile_flip_x_axis_override is not None:
        try:
            profile_flip_x_axis = bool(profile_flip_x_axis_override)
        except Exception:
            profile_flip_x_axis = False

    if show_dialog and QtGui is not None and Gui is not None:
        default_template = ""
        try:
            default_template = PathPreferences.defaultJobTemplate() or ""
        except Exception:
            default_template = ""
        try:
            if inlay_prefs is not None:
                remembered_template = str(inlay_prefs.GetString("last_template_path", "") or "").strip()
                if remembered_template:
                    default_template = remembered_template
        except Exception:
            pass

        class _InlayJobTaskPanel:
            def __init__(self):
                self.form = QtGui.QWidget()
                self.form.setWindowTitle("Inlay Job")
                layout = QtGui.QFormLayout(self.form)
                self._last_run_signature = None

                self.section_combo = QtGui.QComboBox()
                selected_index = 0
                for idx, obj in enumerate(candidates):
                    self.section_combo.addItem(getattr(obj, "Label", obj.Name), obj.Name)
                    if target_obj is not None and obj.Name == getattr(target_obj, "Name", ""):
                        selected_index = idx
                if self.section_combo.count() > 0:
                    self.section_combo.setCurrentIndex(selected_index)

                self.template_combo = QtGui.QComboBox()
                self.template_combo.addItem("<none>", "")
                default_index = 0
                for tpath in sorted(templates, key=lambda p: _template_display_name(p).lower()):
                    idx = self.template_combo.count()
                    self.template_combo.addItem(_template_display_name(tpath), tpath)
                    if default_template and os.path.normpath(default_template) == os.path.normpath(tpath):
                        default_index = idx
                if self.template_combo.count() > 0:
                    self.template_combo.setCurrentIndex(default_index)

                self.template_edit = QtGui.QLineEdit("")
                browse_btn = QtGui.QPushButton("Browse…")

                def _browse_template():
                    try:
                        path, _ = QtGui.QFileDialog.getOpenFileName(
                            self.form,
                            "Select CAM Job Template",
                            "",
                            "Job Templates (*.json);;All Files (*)",
                        )
                        if path:
                            self.template_edit.setText(path)
                    except Exception:
                        pass

                browse_btn.clicked.connect(_browse_template)

                def _on_template_change(index):
                    data = self.template_combo.itemData(index)
                    self.template_edit.setText(str(data or ""))

                self.template_combo.currentIndexChanged.connect(_on_template_change)
                _on_template_change(self.template_combo.currentIndex())

                row = QtGui.QHBoxLayout()
                row.addWidget(self.template_edit)
                row.addWidget(browse_btn)
                template_widget = QtGui.QWidget()
                template_widget.setLayout(row)

                self.inlay_count_spin = QtGui.QSpinBox()
                self.inlay_count_spin.setRange(1, 1000)
                self.inlay_count_spin.setSingleStep(1)
                self.inlay_count_spin.setValue(int(inlay_count))

                self.final_depth_spin = QtGui.QDoubleSpinBox()
                self.final_depth_spin.setDecimals(4)
                self.final_depth_spin.setRange(0.0, 2.0)
                self.final_depth_spin.setSingleStep(0.005)
                self.final_depth_spin.setValue(float(inlay_final_depth_in))

                self.nest_gap_spin = QtGui.QDoubleSpinBox()
                self.nest_gap_spin.setDecimals(4)
                self.nest_gap_spin.setRange(0.0, 1.0)
                self.nest_gap_spin.setSingleStep(0.005)
                self.nest_gap_spin.setValue(float(nest_gap_in))

                self.rotate_xup_check = QtGui.QCheckBox("Alternate 180° rotation for nesting")
                self.rotate_xup_check.setChecked(bool(nest_rotate_180))
                self.keep_tool_down_check = QtGui.QCheckBox("Keep tool down")
                self.keep_tool_down_check.setChecked(bool(profile_keep_tool_down))
                self.min_travel_check = QtGui.QCheckBox("Min travel")
                self.min_travel_check.setChecked(bool(profile_min_travel))
                self.flip_x_axis_check = QtGui.QCheckBox("Flip Z axis")
                self.flip_x_axis_check.setChecked(bool(profile_flip_x_axis))

                self.tool_checkboxes = []
                bit_widget = QtGui.QWidget()
                bit_layout = QtGui.QVBoxLayout(bit_widget)
                bit_layout.setContentsMargins(0, 0, 0, 0)
                self._bit_layout = bit_layout

                persisted_names = []
                try:
                    if inlay_prefs is not None:
                        raw = str(inlay_prefs.GetString("selected_tool_names", "") or "").strip()
                        if raw:
                            loaded = json.loads(raw)
                            if isinstance(loaded, list):
                                persisted_names = list(loaded)
                except Exception:
                    persisted_names = []
                self._fallback_bit_selections = list(persisted_names)
                self._selected_bits_by_template = {}
                try:
                    if inlay_prefs is not None:
                        raw_map = str(inlay_prefs.GetString("selected_bits_by_template_inlay", "") or "").strip()
                        if raw_map:
                            loaded_map = json.loads(raw_map)
                            if isinstance(loaded_map, dict):
                                self._selected_bits_by_template = loaded_map
                except Exception:
                    self._selected_bits_by_template = {}

                def _template_key(path_text):
                    try:
                        text = str(path_text or "").strip()
                        if not text:
                            return ""
                        return os.path.normpath(text)
                    except Exception:
                        return str(path_text or "")

                def _persisted_for_template(path_text):
                    key = _template_key(path_text)
                    if key:
                        try:
                            remembered = self._selected_bits_by_template.get(key, [])
                            if isinstance(remembered, list):
                                return list(remembered)
                        except Exception:
                            pass
                    return list(self._fallback_bit_selections)

                def _clear_bits_ui():
                    try:
                        while self._bit_layout.count() > 0:
                            item = self._bit_layout.takeAt(0)
                            widget = item.widget() if item else None
                            if widget:
                                widget.deleteLater()
                    except Exception:
                        pass
                    self.tool_checkboxes = []

                def _refresh_bits_from_template():
                    _clear_bits_ui()
                    template_path = str(self.template_edit.text() or "").strip()
                    tool_options = _template_tool_controller_options(template_path)
                    if not tool_options:
                        self._bit_layout.addWidget(QtGui.QLabel("No bits found in selected CAM template."))
                        return

                    self._persisted_bit_selections = _persisted_for_template(template_path)

                    def _is_persisted(option):
                        name = str(option.get("name", "") or "").strip().lower()
                        label = str(option.get("label", "") or "").strip().lower()
                        tool_label = str(option.get("tool_label", "") or "").strip().lower()
                        for item in (self._persisted_bit_selections or []):
                            if isinstance(item, dict):
                                i_name = str(item.get("name", "") or "").strip().lower()
                                i_label = str(item.get("label", "") or "").strip().lower()
                                i_tool = str(item.get("tool_label", "") or "").strip().lower()
                                if (name and name == i_name) or (label and label == i_label) or (tool_label and tool_label == i_tool):
                                    return True
                            else:
                                text = str(item or "").strip().lower()
                                if text and (text == name or text == label or text == tool_label):
                                    return True
                        return False

                    for option in tool_options:
                        tc_name = str(option.get("name", "") or "")
                        tc_label = str(option.get("label", "") or "")
                        cb = QtGui.QCheckBox(str(option.get("display", tc_label or tc_name) or tc_name))
                        cb.setChecked(bool(_is_persisted(option)))
                        try:
                            cb.toggled.connect(self._mark_dirty)
                        except Exception:
                            pass
                        self._bit_layout.addWidget(cb)
                        self.tool_checkboxes.append((option, cb))

                self._refresh_bits_from_template = _refresh_bits_from_template

                layout.addRow("Inlay", self.section_combo)
                layout.addRow("CAM template", self.template_combo)
                layout.addRow("Template (optional)", template_widget)
                layout.addRow("Inlay count (X-up)", self.inlay_count_spin)
                layout.addRow("Final depth (in)", self.final_depth_spin)
                layout.addRow("Nesting gap (in)", self.nest_gap_spin)
                layout.addRow("Packing", self.rotate_xup_check)
                layout.addRow("Orientation", self.flip_x_axis_check)
                layout.addRow("Keep Tool Down", self.keep_tool_down_check)
                layout.addRow("Min Travel", self.min_travel_check)
                layout.addRow("Bits", bit_widget)

                self._refresh_bits_from_template()

                try:
                    self.section_combo.currentIndexChanged.connect(self._mark_dirty)
                    self.template_combo.currentIndexChanged.connect(self._mark_dirty)
                    self.template_edit.textChanged.connect(self._mark_dirty)
                    self.inlay_count_spin.valueChanged.connect(self._mark_dirty)
                    self.final_depth_spin.valueChanged.connect(self._mark_dirty)
                    self.nest_gap_spin.valueChanged.connect(self._mark_dirty)
                    self.rotate_xup_check.toggled.connect(self._mark_dirty)
                    self.flip_x_axis_check.toggled.connect(self._mark_dirty)
                    self.keep_tool_down_check.toggled.connect(self._mark_dirty)
                    self.min_travel_check.toggled.connect(self._mark_dirty)
                except Exception:
                    pass

                try:
                    self.template_combo.currentIndexChanged.connect(lambda *_: self._refresh_bits_from_template())
                    self.template_edit.textChanged.connect(lambda *_: self._refresh_bits_from_template())
                except Exception:
                    pass

            def _mark_dirty(self, *args):
                self._last_run_signature = None

            def _current_signature(self):
                try:
                    section_name = str(self.section_combo.currentData() or "").strip()
                except Exception:
                    section_name = ""
                try:
                    template_path = str(self.template_edit.text() or "").strip()
                except Exception:
                    template_path = ""
                try:
                    count = int(self.inlay_count_spin.value())
                except Exception:
                    count = 1
                try:
                    final_depth = round(float(self.final_depth_spin.value()), 6)
                except Exception:
                    final_depth = 0.2
                try:
                    nest_gap = round(float(self.nest_gap_spin.value()), 6)
                except Exception:
                    nest_gap = 0.08
                try:
                    rotate_180 = bool(self.rotate_xup_check.isChecked())
                except Exception:
                    rotate_180 = True
                try:
                    keep_tool_down = bool(self.keep_tool_down_check.isChecked())
                except Exception:
                    keep_tool_down = True
                try:
                    min_travel = bool(self.min_travel_check.isChecked())
                except Exception:
                    min_travel = True
                try:
                    flip_x_axis = bool(self.flip_x_axis_check.isChecked())
                except Exception:
                    flip_x_axis = False
                try:
                    selected_bits = tuple(
                        sorted(
                            [
                                str(option.get("name", "") or "")
                                for option, cb in self.tool_checkboxes
                                if cb.isChecked() and str(option.get("name", "") or "")
                            ]
                        )
                    )
                except Exception:
                    selected_bits = tuple()
                return (
                    section_name,
                    template_path,
                    count,
                    final_depth,
                    nest_gap,
                    rotate_180,
                    flip_x_axis,
                    keep_tool_down,
                    min_travel,
                    selected_bits,
                )

            def _run_creation(self, close_after=True):
                current_signature = self._current_signature()
                if close_after and self._last_run_signature == current_signature:
                    try:
                        Gui.Control.closeDialog()
                    except Exception:
                        pass
                    return True

                selected_name = self.section_combo.currentData()
                selected_obj = doc.getObject(selected_name) if selected_name else None
                if not selected_obj:
                    print("Inlay Job creation canceled: no valid inlay selected.")
                    return False

                selected_template = str(self.template_edit.text() or "").strip()
                try:
                    selected_count = max(1, int(self.inlay_count_spin.value()))
                except Exception:
                    selected_count = 1
                try:
                    selected_final_depth = max(0.0, float(self.final_depth_spin.value()))
                except Exception:
                    selected_final_depth = 0.200
                try:
                    selected_nest_gap = max(0.0, float(self.nest_gap_spin.value()))
                except Exception:
                    selected_nest_gap = 0.080
                selected_rotate = bool(self.rotate_xup_check.isChecked())
                selected_flip_x_axis = bool(self.flip_x_axis_check.isChecked())
                selected_keep_tool_down = bool(self.keep_tool_down_check.isChecked())
                selected_min_travel = bool(self.min_travel_check.isChecked())

                try:
                    if inlay_prefs is not None:
                        inlay_prefs.SetString("last_template_path", str(selected_template or ""))
                        inlay_prefs.SetInt("inlay_count", int(selected_count))
                        inlay_prefs.SetBool("nest_rotate_180", bool(selected_rotate))
                        inlay_prefs.SetFloat("final_depth_in", float(selected_final_depth))
                        inlay_prefs.SetFloat("nest_gap_in", float(selected_nest_gap))
                        inlay_prefs.SetBool("profile_flip_x_axis", bool(selected_flip_x_axis))
                        inlay_prefs.SetBool("profile_keep_tool_down", bool(selected_keep_tool_down))
                        inlay_prefs.SetBool("profile_min_travel", bool(selected_min_travel))
                except Exception:
                    pass

                selected_tool_names = None
                try:
                    selected_tool_names = [
                        {
                            "name": str(option.get("name", "") or "").strip(),
                            "label": str(option.get("label", "") or "").strip(),
                            "tool_label": str(option.get("tool_label", "") or "").strip(),
                            "diameter_mm": float(option.get("diameter_mm", 0.0) or 0.0),
                            "tool_number": option.get("tool_number", None),
                            "display": str(option.get("display", "") or "").strip(),
                        }
                        for option, cb in self.tool_checkboxes
                        if cb.isChecked()
                    ]
                except Exception:
                    selected_tool_names = None
                try:
                    if inlay_prefs is not None and isinstance(selected_tool_names, list):
                        inlay_prefs.SetString("selected_tool_names", json.dumps(selected_tool_names))
                        selected_template_key = ""
                        try:
                            selected_template_key = os.path.normpath(str(selected_template or "").strip()) if str(selected_template or "").strip() else ""
                        except Exception:
                            selected_template_key = str(selected_template or "")
                        if selected_template_key:
                            self._selected_bits_by_template[selected_template_key] = list(selected_tool_names)
                            inlay_prefs.SetString(
                                "selected_bits_by_template_inlay",
                                json.dumps(self._selected_bits_by_template),
                            )
                except Exception:
                    pass

                try:
                    create_section_cnc_job(
                        target=selected_obj,
                        template_path=selected_template or None,
                        show_dialog=False,
                        selected_tool_names_override=selected_tool_names,
                        profile_flip_x_axis_override=selected_flip_x_axis,
                        profile_keep_tool_down_override=selected_keep_tool_down,
                        profile_min_travel_override=selected_min_travel,
                    )
                    self._last_run_signature = current_signature
                finally:
                    if close_after:
                        try:
                            Gui.Control.closeDialog()
                        except Exception:
                            pass
                return True

            def getStandardButtons(self):
                return QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Apply | QtGui.QDialogButtonBox.Cancel

            def clicked(self, button):
                if button == QtGui.QDialogButtonBox.Apply:
                    self._run_creation(close_after=False)
                    return True
                return False

            def accept(self):
                return self._run_creation(close_after=True)

            def reject(self):
                print("CNC Job creation canceled.")
                try:
                    Gui.Control.closeDialog()
                except Exception:
                    pass
                return True

        Gui.Control.showDialog(_InlayJobTaskPanel())
        return

    if effective_template_path and not os.path.exists(effective_template_path):
        print(f"Template not found: {effective_template_path}")
        return

    # Inlay Job uses selected inlay geometry directly (or X-up model when count > 1).

    def _create_flip_x_inlay_model(base_obj, flip_x_axis=False):
        if not base_obj or not bool(flip_x_axis):
            return base_obj, []

        try:
            source_shape = getattr(base_obj, "Shape", None)
            if not source_shape or source_shape.isNull():
                return base_obj, []
            bb = source_shape.BoundBox
            plane_point = App.Vector(float(bb.Center.x), float(bb.Center.y), float(bb.Center.z))
            mirrored_shape = source_shape.mirror(plane_point, App.Vector(0, 0, 1))
            if not mirrored_shape or mirrored_shape.isNull():
                return base_obj, []
        except Exception as exc:
            try:
                print(f"Flip Z axis mirror failed: {exc}")
            except Exception:
                pass
            return base_obj, []

        try:
            flip_obj = doc.addObject("Part::Feature", _next_name("InlayFlipXModel"))
            flip_obj.Label = f"{getattr(base_obj, 'Label', base_obj.Name)} Flip X"
            flip_obj.Shape = mirrored_shape
            print("Applied inlay Flip Z axis orientation (mirrored Z coordinates).")
            return flip_obj, [flip_obj]
        except Exception:
            return base_obj, []

    def _create_x_up_inlay_model(base_obj, count, alternate_rotate_180=True, nesting_gap_in=0.080):
        if not base_obj or int(count) <= 1:
            return base_obj, []

        try:
            shape = getattr(base_obj, "Shape", None)
            if not shape or shape.isNull():
                return base_obj, []
            bb = shape.BoundBox
        except Exception:
            return base_obj, []

        clearance_mm = max(0.05, _inch_to_mm(nesting_gap_in))

        def _bb_distance_mm(bb1, bb2):
            try:
                dx = max(0.0, float(max(bb1.XMin - bb2.XMax, bb2.XMin - bb1.XMax)))
                dy = max(0.0, float(max(bb1.YMin - bb2.YMax, bb2.YMin - bb1.YMax)))
                dz = max(0.0, float(max(bb1.ZMin - bb2.ZMax, bb2.ZMin - bb1.ZMax)))
                return (dx * dx + dy * dy + dz * dz) ** 0.5
            except Exception:
                return 0.0

        def _is_bb_clear_all(candidate_bb, placed_bbs):
            try:
                for placed_bb in placed_bbs:
                    if _bb_distance_mm(candidate_bb, placed_bb) + 1e-6 < clearance_mm:
                        return False
            except Exception:
                return False
            return True

        def _oriented_copy(idx):
            shp = shape.copy()
            did_rotate = False
            if alternate_rotate_180 and (idx % 2 == 1):
                try:
                    shp.rotate(shp.BoundBox.Center, App.Vector(0, 0, 1), 180)
                    did_rotate = True
                except Exception:
                    did_rotate = False
            return shp, did_rotate

        ylen = max(0.0, float(bb.YLength))
        y_candidates = [0.0]
        if ylen > 1e-6:
            y_candidates.extend([
                0.10 * ylen,
                -0.10 * ylen,
                0.20 * ylen,
                -0.20 * ylen,
            ])

        shapes = []
        shape_bbs = []
        rotated_instances = 0
        previous_xmax = None
        search_step_mm = max(0.15, _inch_to_mm(0.01))

        for idx in range(int(count)):
            try:
                seed_shape, did_rotate = _oriented_copy(idx)
                if idx == 0:
                    shapes.append(seed_shape)
                    if did_rotate:
                        rotated_instances += 1
                    first_bb = seed_shape.BoundBox
                    previous_xmax = float(first_bb.XMax)
                    shape_bbs.append(first_bb)
                    continue

                seed_bb = seed_shape.BoundBox
                flush_dx = 0.0
                if previous_xmax is not None:
                    flush_dx = max(0.0, previous_xmax - float(seed_bb.XMin) + clearance_mm)

                start_dx = max(0.0, flush_dx - (0.55 * float(seed_bb.XLength)))
                max_dx = max(flush_dx + _inch_to_mm(12.0), start_dx + float(seed_bb.XLength) * 1.6)
                placed_shape = None
                placed_xmax = None

                for yoff in y_candidates:
                    dx = start_dx
                    while dx <= max_dx:
                        final_candidate = seed_shape.copy()
                        final_candidate.translate(App.Vector(dx, float(yoff), 0.0))
                        candidate_bb = final_candidate.BoundBox
                        if _is_bb_clear_all(candidate_bb, shape_bbs):
                            candidate_xmax = float(candidate_bb.XMax)
                            if placed_shape is None or candidate_xmax < placed_xmax - 1e-6:
                                placed_shape = final_candidate
                                placed_xmax = candidate_xmax
                            break
                        dx += search_step_mm

                if placed_shape is None:
                    fallback = seed_shape.copy()
                    fallback_pitch = max(0.01, float(bb.XLength) + _inch_to_mm(0.1))
                    fallback.translate(App.Vector(float(idx) * fallback_pitch, 0.0, 0.0))
                    placed_shape = fallback
                    placed_xmax = float(placed_shape.BoundBox.XMax)
                    print(f"Nesting fallback used for instance {idx + 1}.")

                shapes.append(placed_shape)
                shape_bbs.append(placed_shape.BoundBox)
                previous_xmax = placed_xmax
                if did_rotate:
                    rotated_instances += 1
                print(f"Nested instance {idx + 1}/{int(count)}.")
            except Exception as exc:
                try:
                    fallback = shape.copy()
                    fallback_pitch = max(0.01, float(bb.XLength) + _inch_to_mm(0.1))
                    fallback.translate(App.Vector(float(idx) * fallback_pitch, 0.0, 0.0))
                    shapes.append(fallback)
                    shape_bbs.append(fallback.BoundBox)
                    previous_xmax = float(fallback.BoundBox.XMax)
                    print(f"Nesting error at instance {idx + 1}; used safe fallback ({exc}).")
                except Exception:
                    print(f"Nesting failed at instance {idx + 1}: {exc}")

        if len(shapes) <= 1:
            return base_obj, []

        try:
            compound_shape = Part.makeCompound(shapes)
            xup_obj = doc.addObject("Part::Feature", _next_name("InlayXUpModel"))
            xup_obj.Label = f"{getattr(base_obj, 'Label', base_obj.Name)} X-up"
            xup_obj.Shape = compound_shape

            try:
                base_pitch = float(bb.XLength) + _inch_to_mm(0.1)
                baseline_span = (base_pitch * max(0, int(count) - 1)) + float(bb.XLength)
                actual_span = float(compound_shape.BoundBox.XLength)
                saved_span = max(0.0, baseline_span - actual_span)
                print(f"Nesting X span: {actual_span / 25.4:.3f} in (saved {saved_span / 25.4:.3f} in vs fixed pitch).")
            except Exception:
                pass

            if rotated_instances > 0:
                print(f"Applied alternating 180° nesting rotation to {rotated_instances} instance(s).")
            print(f"Nesting gap target: {clearance_mm / 25.4:.3f} in.")
            return xup_obj, [xup_obj]
        except Exception:
            return base_obj, []

    target_name = str(getattr(target_obj, "Name", "") or "")
    target_label = str(getattr(target_obj, "Label", "") or "")
    is_prebuilt_xup_target = (
        target_name.startswith("InlayXUpModel")
        or "x-up" in target_label.lower()
        or "x up" in target_label.lower()
    )

    oriented_base_obj, oriented_created_items = _create_flip_x_inlay_model(target_obj, profile_flip_x_axis)
    if bool(profile_flip_x_axis) and oriented_base_obj is target_obj:
        print("Flip Z axis requested but could not be applied; using original orientation.")

    if is_prebuilt_xup_target:
        profile_base_obj, xup_created_items = oriented_base_obj, list(oriented_created_items or [])
        print("Using existing X-up layout target; skipping auto X-up generation.")
    else:
        profile_base_obj, xup_created_items = _create_x_up_inlay_model(
            oriented_base_obj,
            inlay_count,
            nest_rotate_180,
            nest_gap_in,
        )
        if oriented_created_items:
            xup_created_items = list(oriented_created_items) + list(xup_created_items or [])
        if int(inlay_count) > 1 and profile_base_obj is oriented_base_obj:
            print("Failed to create X-up inlay model; falling back to single inlay model.")
        elif int(inlay_count) > 1:
            print(f"Created X-up inlay model with {int(inlay_count)} instance(s).")

    job_models = [profile_base_obj]

    def _as_mm(value):
        try:
            return float(getattr(value, "Value", value))
        except Exception:
            return 0.0

    def _tool_diameter_mm(tc_obj):
        try:
            tool = getattr(tc_obj, "Tool", None)
            d = getattr(tool, "Diameter", None)
            return _as_mm(d)
        except Exception:
            return 0.0

    def _job_tool_controllers_sorted_largest_first(job_obj):
        try:
            tools_group = list(getattr(getattr(job_obj, "Tools", None), "Group", None) or [])
        except Exception:
            tools_group = []

        with_diameter = []
        without_diameter = []
        for tc in tools_group:
            dmm = _tool_diameter_mm(tc)
            if dmm > 0:
                with_diameter.append((dmm, tc))
            else:
                without_diameter.append(tc)

        with_diameter.sort(key=lambda item: item[0], reverse=True)
        ordered = [tc for _, tc in with_diameter]
        ordered.extend(without_diameter)
        return ordered

    def _selected_tool_labels_for_job(job_obj, selected_names):
        selected_set = {str(name) for name in (selected_names or []) if str(name).strip()}
        labels = []
        try:
            for tc in _job_tool_controllers_sorted_largest_first(job_obj):
                tc_name = str(getattr(tc, "Name", "") or "")
                if tc_name and tc_name in selected_set:
                    tc_label = str(getattr(tc, "Label", "") or "").strip()
                    labels.append(tc_label or tc_name)
        except Exception:
            pass
        return labels

    def _select_tool_names_for_inlay(job_obj, selected_override=None):
        ordered_tcs = _job_tool_controllers_sorted_largest_first(job_obj)
        if not ordered_tcs:
            return []

        def _txt(value):
            return str(value or "").strip().lower()

        def _extract_inch_sizes(text):
            values = []
            s = str(text or "")
            try:
                import re

                for m in re.findall(r"(\d+(?:\.\d+)?)\s*in\b", s, flags=re.IGNORECASE):
                    try:
                        values.append(float(m))
                    except Exception:
                        pass

                for m in re.findall(r"(?<!\d)0?(\d{3})(?!\d)", s):
                    try:
                        values.append(float(f"0.{m}"))
                    except Exception:
                        pass
            except Exception:
                pass
            return values

        tc_specs = []
        for tc in ordered_tcs:
            try:
                tc_name = str(getattr(tc, "Name", "") or "").strip()
            except Exception:
                tc_name = ""
            if not tc_name:
                continue
            try:
                tc_label = str(getattr(tc, "Label", "") or "").strip()
            except Exception:
                tc_label = ""
            try:
                tool_obj = getattr(tc, "Tool", None)
                tool_label = str(getattr(tool_obj, "Label", "") or getattr(tool_obj, "Name", "") or "").strip()
            except Exception:
                tool_label = ""
            try:
                tool_number = int(getattr(tc, "ToolNumber", -1))
                if tool_number < 0:
                    tool_number = None
            except Exception:
                tool_number = None
            try:
                diameter_mm = float(_tool_diameter_mm(tc))
            except Exception:
                diameter_mm = 0.0
            tc_specs.append(
                {
                    "name": tc_name,
                    "label": tc_label,
                    "tool_label": tool_label,
                    "diameter_mm": diameter_mm,
                    "tool_number": tool_number,
                }
            )

        def _match_selected_names(raw_items):
            selected_names = []
            used = set()
            for item in list(raw_items or []):
                item_name = ""
                item_label = ""
                item_tool_label = ""
                item_diameter_mm = None
                item_tool_number = None

                if isinstance(item, dict):
                    item_name = str(item.get("name", "") or "").strip()
                    item_label = str(item.get("label", "") or "").strip()
                    item_tool_label = str(item.get("tool_label", "") or "").strip()
                    try:
                        raw_tool_number = item.get("tool_number", None)
                        item_tool_number = int(raw_tool_number) if raw_tool_number is not None else None
                    except Exception:
                        item_tool_number = None
                    try:
                        raw_diameter = item.get("diameter_mm", None)
                        item_diameter_mm = float(raw_diameter) if raw_diameter is not None else None
                    except Exception:
                        item_diameter_mm = None
                else:
                    item_name = str(item or "").strip()

                matched = None
                if item_tool_number is not None:
                    for spec in tc_specs:
                        try:
                            if int(spec.get("tool_number")) == int(item_tool_number):
                                matched = spec
                                break
                        except Exception:
                            pass

                for spec in tc_specs:
                    if matched is None and item_name and _txt(spec.get("name")) == _txt(item_name):
                        matched = spec
                        break

                if matched is None:
                    for spec in tc_specs:
                        if item_label and _txt(spec.get("label")) == _txt(item_label):
                            matched = spec
                            break

                if matched is None:
                    for spec in tc_specs:
                        if item_tool_label and _txt(spec.get("tool_label")) == _txt(item_tool_label):
                            matched = spec
                            break

                if matched is None and item_diameter_mm is not None:
                    for spec in tc_specs:
                        try:
                            if abs(float(spec.get("diameter_mm", 0.0)) - float(item_diameter_mm)) <= 1e-3:
                                matched = spec
                                break
                        except Exception:
                            pass

                if matched is None:
                    item_size_candidates = []
                    for text_part in (item_name, item_label, item_tool_label):
                        item_size_candidates.extend(_extract_inch_sizes(text_part))
                    if item_size_candidates:
                        try:
                            target_in = min(item_size_candidates)
                        except Exception:
                            target_in = None
                        if target_in is not None:
                            for spec in tc_specs:
                                try:
                                    spec_in = float(spec.get("diameter_mm", 0.0)) / 25.4
                                    if spec_in > 0 and abs(spec_in - target_in) <= 0.0025:
                                        matched = spec
                                        break
                                except Exception:
                                    pass

                if matched is not None:
                    name = str(matched.get("name", "") or "")
                    if name and name not in used:
                        used.add(name)
                        selected_names.append(name)

            return selected_names

        persisted_names = []
        try:
            if inlay_prefs is not None:
                raw = str(inlay_prefs.GetString("selected_tool_names", "") or "").strip()
                if raw:
                    loaded = json.loads(raw)
                    if isinstance(loaded, list):
                        persisted_names = list(loaded)
        except Exception:
            persisted_names = []

        if selected_override is not None:
            chosen = _match_selected_names(selected_override or [])
            if chosen:
                try:
                    if inlay_prefs is not None:
                        inlay_prefs.SetString("selected_tool_names", json.dumps(chosen))
                except Exception:
                    pass
                return chosen

            explicit_requested = False
            try:
                explicit_requested = len(list(selected_override or [])) > 0
            except Exception:
                explicit_requested = bool(selected_override)
            if explicit_requested:
                print("Selected Inlay bit(s) could not be matched to template/job tools; no Profile operations created.")
                return []

        default_names = _match_selected_names(persisted_names)
        if not default_names:
            default_names = [str(getattr(tc, "Name", "")) for tc in ordered_tcs[:2] if getattr(tc, "Name", "")]
        if not default_names:
            default_names = [str(getattr(ordered_tcs[0], "Name", ""))]

        if not show_dialog or QtGui is None:
            selected_names = [name for name in default_names if name]
            try:
                if inlay_prefs is not None:
                    inlay_prefs.SetString("selected_tool_names", json.dumps(selected_names))
            except Exception:
                pass
            return selected_names

        dialog = QtGui.QDialog()
        dialog.setWindowTitle("Inlay Bits")
        layout = QtGui.QVBoxLayout(dialog)
        layout.addWidget(QtGui.QLabel("Select bits to use (largest to smallest):"))

        checkbox_rows = []
        for tc in ordered_tcs:
            tc_name = str(getattr(tc, "Name", ""))
            tc_label = str(getattr(tc, "Label", tc_name or "ToolController"))
            dmm = _tool_diameter_mm(tc)

            if dmm > 0:
                din = dmm / 25.4
                text = f"{tc_label} ({din:.3f} in)"
            else:
                text = tc_label

            cb = QtGui.QCheckBox(text)
            cb.setChecked(tc_name in default_names)
            layout.addWidget(cb)
            checkbox_rows.append((tc_name, cb))

        buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() != QtGui.QDialog.Accepted:
            print("Inlay bit selection canceled; using default largest bit(s).")
            selected_names = [name for name in default_names if name]
            try:
                if inlay_prefs is not None:
                    inlay_prefs.SetString("selected_tool_names", json.dumps(selected_names))
            except Exception:
                pass
            return selected_names

        selected = [name for name, cb in checkbox_rows if cb.isChecked() and name]
        if selected:
            try:
                if inlay_prefs is not None:
                    inlay_prefs.SetString("selected_tool_names", json.dumps(selected))
            except Exception:
                pass
            return selected

        fallback = [default_names[0]] if default_names else []
        try:
            if inlay_prefs is not None:
                inlay_prefs.SetString("selected_tool_names", json.dumps(fallback))
        except Exception:
            pass
        return fallback

    def _best_face_subname_for_profile(obj):
        try:
            shp = obj.Shape
        except Exception:
            shp = None
        if not shp or shp.isNull() or not getattr(shp, "Faces", None):
            return None

        best_idx = None
        best_z = None
        for idx, face in enumerate(shp.Faces):
            try:
                surface = getattr(face, "Surface", None)
                if not isinstance(surface, Part.Plane):
                    continue
                normal = face.normalAt(0.0, 0.0)
                if abs(float(getattr(normal, "z", 0.0))) < 0.95:
                    continue
                z_val = float(face.CenterOfMass.z)
            except Exception:
                continue

            if best_z is None or z_val > best_z:
                best_z = z_val
                best_idx = idx

        if best_idx is None:
            return None
        return f"Face{best_idx + 1}"

    def _edge_subnames_for_face(obj, face_subname):
        if not obj or not face_subname:
            return []
        try:
            face_idx = int(str(face_subname).replace("Face", "")) - 1
            shape = getattr(obj, "Shape", None)
            if not shape or shape.isNull():
                return []
            face = shape.Faces[face_idx]
            face_edges = list(getattr(face, "Edges", []) or [])
            all_edges = list(getattr(shape, "Edges", []) or [])
        except Exception:
            return []

        edge_names = []
        used = set()
        for f_edge in face_edges:
            for idx, edge in enumerate(all_edges, start=1):
                try:
                    same = f_edge.isSame(edge)
                except Exception:
                    same = False
                if same and idx not in used:
                    used.add(idx)
                    edge_names.append(f"Edge{idx}")
                    break
        return edge_names

    def _create_profile_ops_for_inlay(
        job_obj,
        inlay_obj,
        selected_tool_names=None,
        final_depth_inch=0.200,
        strict_selection=False,
        keep_tool_down=True,
        min_travel=True,
    ):
        if not job_obj or not inlay_obj:
            return []

        try:
            import Path.Op.Profile as PathProfile
            import Path.Op.Gui.Profile as PathProfileGui
            import Path.Op.Gui.Base as PathOpGuiBase
            import PathScripts.PathUtils as PathUtils
            import Path.Op.PocketShape as PathPocketShape
            import Path.Op.Gui.PocketShape as PathPocketShapeGui
        except Exception as exc:
            print(f"Profile module unavailable: {exc}")
            return []

        try:
            shape = getattr(inlay_obj, "Shape", None)
            if not shape or shape.isNull():
                print("Selected inlay has no valid shape for Inlay Profile operation.")
                return []
        except Exception:
            print("Selected inlay has no valid shape for Inlay Profile operation.")
            return []

        ordered_tcs = _job_tool_controllers_sorted_largest_first(job_obj)
        selected_set = {str(name) for name in (selected_tool_names or []) if str(name).strip()}
        selected_tcs = [tc for tc in ordered_tcs if str(getattr(tc, "Name", "")) in selected_set]
        if not selected_tcs:
            if strict_selection and selected_set:
                print("No matching Inlay tool controllers for selected bit(s); skipping Profile operations.")
                return []
            selected_tcs = ordered_tcs[:2] if len(ordered_tcs) >= 2 else ordered_tcs[:1]
        if not selected_tcs:
            print("No tool controllers available in Inlay Job.")
            return []

        def _sanitize_name_part(text):
            raw = str(text or "").strip()
            cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw)
            cleaned = cleaned.strip("_")
            return cleaned or "bit"

        def _bit_name(tc_obj):
            tool = getattr(tc_obj, "Tool", None)
            dmm = _tool_diameter_mm(tc_obj)
            if dmm > 0:
                return f"{(dmm/25.4):.3f}in"
            for candidate in (
                getattr(tool, "Label", None),
                getattr(tool, "Name", None),
                getattr(tc_obj, "Label", None),
                getattr(tc_obj, "Name", None),
            ):
                if candidate:
                    return _sanitize_name_part(candidate)
            return "bit"

        def _unique_label(base_label):
            existing = {str(getattr(o, "Label", "")) for o in (getattr(doc, "Objects", []) or [])}
            if base_label not in existing:
                return base_label
            idx = 2
            while f"{base_label}_{idx}" in existing:
                idx += 1
            return f"{base_label}_{idx}"

        def _create_profile_with_selected_tc(name, parent_job, selected_tc):
            original_ui = getattr(PathUtils, "UserInput", None)

            class _ToolSelectionShim:
                def selectedToolController(self):
                    return selected_tc

                def chooseToolController(self, controllers):
                    if selected_tc in controllers:
                        return selected_tc
                    return controllers[0] if controllers else None

            try:
                if selected_tc is not None:
                    PathUtils.UserInput = _ToolSelectionShim()
                return PathProfile.Create(name, obj=None, parentJob=parent_job)
            finally:
                PathUtils.UserInput = original_ui

        def _create_pocket_with_selected_tc(name, parent_job, selected_tc):
            original_ui = getattr(PathUtils, "UserInput", None)

            class _ToolSelectionShim:
                def selectedToolController(self):
                    return selected_tc

                def chooseToolController(self, controllers):
                    if selected_tc in controllers:
                        return selected_tc
                    return controllers[0] if controllers else None

            try:
                if selected_tc is not None:
                    PathUtils.UserInput = _ToolSelectionShim()
                return PathPocketShape.Create(name, obj=None, parentJob=parent_job)
            finally:
                PathUtils.UserInput = original_ui

        def _top_wire_edge_name_groups(obj_with_shape):
            try:
                full_shape = getattr(obj_with_shape, "Shape", None)
                faces = list(getattr(full_shape, "Faces", []) or [])
            except Exception:
                return ([], [])
            if not faces:
                return ([], [])

            max_z = None
            top_faces = []
            for face in faces:
                try:
                    surface = getattr(face, "Surface", None)
                    if not isinstance(surface, Part.Plane):
                        continue
                    normal = face.normalAt(0.5, 0.5)
                    if abs(float(getattr(normal, "z", 0.0)) - 1.0) > 1e-3:
                        continue
                    zmax = float(face.BoundBox.ZMax)
                except Exception:
                    continue
                if (max_z is None) or (zmax > max_z + 1e-6):
                    max_z = zmax
                    top_faces = [face]
                elif max_z is not None and abs(zmax - max_z) <= 1e-6:
                    top_faces.append(face)

            if not top_faces:
                return ([], [])

            try:
                all_edges = list(getattr(full_shape, "Edges", []) or [])
            except Exception:
                all_edges = []
            if not all_edges:
                return ([], [])

            def _wire_to_edge_names(wire_obj):
                edge_names_local = []
                for wire_edge in list(getattr(wire_obj, "Edges", []) or []):
                    found = None
                    for edge_idx, shape_edge in enumerate(all_edges, start=1):
                        try:
                            if shape_edge.isSame(wire_edge):
                                found = f"Edge{edge_idx}"
                                break
                        except Exception:
                            continue
                    if found:
                        edge_names_local.append(found)
                return list(dict.fromkeys(edge_names_local))

            outer_groups = []
            inner_groups = []
            for top_face in top_faces:
                try:
                    wires = list(getattr(top_face, "Wires", []) or [])
                except Exception:
                    wires = []
                if not wires:
                    continue

                outer_names = _wire_to_edge_names(wires[0])
                if outer_names:
                    outer_groups.append(outer_names)

                for wire in wires[1:]:
                    inner_names = _wire_to_edge_names(wire)
                    if inner_names:
                        inner_groups.append(inner_names)

            def _dedupe(groups):
                deduped = []
                seen = set()
                for group in groups:
                    key = tuple(sorted(group))
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(group)
                return deduped

            return (_dedupe(outer_groups), _dedupe(inner_groups))

        def _horizontal_face_subnames_below_zero(obj_with_shape, z_tol=1e-6):
            try:
                full_shape = getattr(obj_with_shape, "Shape", None)
                faces = list(getattr(full_shape, "Faces", []) or [])
            except Exception:
                return []
            if not faces:
                return []

            subs = []
            for idx, face in enumerate(faces, start=1):
                try:
                    surface = getattr(face, "Surface", None)
                    if not isinstance(surface, Part.Plane):
                        continue
                    normal = face.normalAt(0.5, 0.5)
                    if float(getattr(normal, "z", 0.0)) < 0.95:
                        continue
                    zmax = float(face.BoundBox.ZMax)
                except Exception:
                    continue
                if zmax < -abs(float(z_tol)):
                    subs.append(f"Face{idx}")
            return list(dict.fromkeys(subs))

        outer_edge_groups, inner_hole_edge_groups = _top_wire_edge_name_groups(inlay_obj)
        if not outer_edge_groups:
            outer_edge_groups = [[]]


        created_ops = []
        # Assign a color to each tool controller, up to 3
        tc_color_map = {}
        tc_name_color_map = {}
        for idx, tc in enumerate(selected_tcs):
            if idx < len(_TOOLPATH_COLORS):
                tc_color_map[tc] = _TOOLPATH_COLORS[idx]
                tc_name = str(getattr(tc, "Name", "") or "")
                if tc_name:
                    tc_name_color_map[tc_name] = _TOOLPATH_COLORS[idx]

        def _apply_toolpath_normal_color(op_obj, selected_tc):
            color = tc_color_map.get(selected_tc)
            if color is None and op_obj is not None:
                try:
                    tc_name = str(getattr(getattr(op_obj, "ToolController", None), "Name", "") or "")
                except Exception:
                    tc_name = ""
                if tc_name:
                    color = tc_name_color_map.get(tc_name)
            view_obj = getattr(op_obj, "ViewObject", None)
            if not color or view_obj is None:
                return
            try:
                rgba = tuple(float(c) for c in color)
                view_obj.NormalColor = rgba
                for child in list(getattr(op_obj, "OutListRecursive", []) or getattr(op_obj, "OutList", []) or []):
                    child_view = getattr(child, "ViewObject", None)
                    if child_view is not None:
                        try:
                            child_view.NormalColor = rgba
                        except Exception:
                            pass
            except Exception:
                pass

        for tc_idx, selected_tc in enumerate(selected_tcs):
            op_specs = []
            for outer_idx, outer_edges in enumerate(outer_edge_groups, start=1):
                outer_suffix = "" if len(outer_edge_groups) == 1 else f"_Outer{outer_idx}"
                op_specs.append(("Outside", outer_edges, outer_suffix))
            # Only apply inside pocketing to detected holes
            if inner_hole_edge_groups:
                for hole_idx, edge_group in enumerate(inner_hole_edge_groups, start=1):
                    op_specs.append(("Inside", edge_group, f"_Hole{hole_idx}"))

            for side_value, base_subs, label_suffix in op_specs:
                profile_op = _create_profile_with_selected_tc(_next_name("Profile"), job_obj, selected_tc)
                if not profile_op:
                    continue

                try:
                    if getattr(profile_op, "ViewObject", None):
                        profile_op.ViewObject.Proxy = PathOpGuiBase.ViewProvider(
                            profile_op.ViewObject,
                            PathProfileGui.Command.res,
                        )
                        profile_op.ViewObject.Visibility = True
                        _apply_toolpath_normal_color(profile_op, selected_tc)
                except Exception:
                    pass

                if selected_tc and hasattr(profile_op, "ToolController"):
                    try:
                        profile_op.ToolController = selected_tc
                    except Exception:
                        pass

                try:
                    profile_op.Label = _unique_label(f"Profile_{_bit_name(selected_tc)}{label_suffix}")
                except Exception:
                    pass

                try:
                    profile_op.Base = [(inlay_obj, list(base_subs or []))]
                except Exception:
                    pass

                if hasattr(profile_op, "Side"):
                    try:
                        profile_op.Side = str(side_value)
                    except Exception:
                        pass

                if hasattr(profile_op, "KeepToolDown"):
                    try:
                        profile_op.KeepToolDown = bool(keep_tool_down)
                    except Exception:
                        pass

                if hasattr(profile_op, "MinTravel"):
                    try:
                        profile_op.MinTravel = bool(min_travel)
                    except Exception:
                        pass

                try:
                    if hasattr(profile_op, "setExpression"):
                        profile_op.setExpression("FinalDepth", None)
                except Exception:
                    pass

                try:
                    target_final_mm = -abs(_inch_to_mm(final_depth_inch))
                    if hasattr(profile_op, "FinalDepth"):
                        try:
                            profile_op.FinalDepth = f"{target_final_mm} mm"
                        except Exception:
                            try:
                                profile_op.FinalDepth.Value = target_final_mm
                            except Exception:
                                pass
                except Exception:
                    pass

                try:
                    doc.recompute()
                except Exception:
                    pass

                _apply_toolpath_normal_color(profile_op, selected_tc)

                try:
                    cmds = getattr(getattr(profile_op, "Path", None), "Commands", None)
                    if not cmds or len(cmds) == 0:
                        print(f"Profile path failed for '{getattr(profile_op, 'Label', profile_op.Name)}'.")
                        continue
                except Exception:
                    pass

                created_ops.append(profile_op)


        lower_xy_face_subs = _horizontal_face_subnames_below_zero(inlay_obj)
        if lower_xy_face_subs:
            lower_xy_pocket_count = 0
            for tc_idx, selected_tc in enumerate(selected_tcs):
                pocket_op = _create_pocket_with_selected_tc(_next_name("PocketShape"), job_obj, selected_tc)
                if not pocket_op:
                    continue

                try:
                    if getattr(pocket_op, "ViewObject", None):
                        pocket_op.ViewObject.Proxy = PathOpGuiBase.ViewProvider(
                            pocket_op.ViewObject,
                            PathPocketShapeGui.Command.res,
                        )
                        pocket_op.ViewObject.Visibility = True
                        _apply_toolpath_normal_color(pocket_op, selected_tc)
                except Exception:
                    pass

                if selected_tc and hasattr(pocket_op, "ToolController"):
                    try:
                        pocket_op.ToolController = selected_tc
                    except Exception:
                        pass

                try:
                    pocket_op.Label = _unique_label(f"PocketShape_{_bit_name(selected_tc)}_LowerXY")
                except Exception:
                    pass

                try:
                    pocket_op.Base = [(inlay_obj, list(lower_xy_face_subs or []))]
                except Exception:
                    pass

                rest_enabled = bool(lower_xy_pocket_count > 0)
                if hasattr(pocket_op, "UseRestMachining"):
                    try:
                        pocket_op.UseRestMachining = rest_enabled
                    except Exception:
                        pass
                if hasattr(pocket_op, "RestMachining"):
                    try:
                        pocket_op.RestMachining = rest_enabled
                    except Exception:
                        pass

                if hasattr(pocket_op, "KeepToolDown"):
                    try:
                        pocket_op.KeepToolDown = bool(keep_tool_down)
                    except Exception:
                        pass

                if hasattr(pocket_op, "MinTravel"):
                    try:
                        pocket_op.MinTravel = bool(min_travel)
                    except Exception:
                        pass

                try:
                    if hasattr(pocket_op, "setExpression"):
                        pocket_op.setExpression("FinalDepth", None)
                except Exception:
                    pass

                try:
                    target_final_mm = -abs(_inch_to_mm(final_depth_inch))
                    if hasattr(pocket_op, "FinalDepth"):
                        try:
                            pocket_op.FinalDepth = f"{target_final_mm} mm"
                        except Exception:
                            try:
                                pocket_op.FinalDepth.Value = target_final_mm
                            except Exception:
                                pass
                except Exception:
                    pass

                try:
                    doc.recompute()
                except Exception:
                    pass

                _apply_toolpath_normal_color(pocket_op, selected_tc)

                try:
                    cmds = getattr(getattr(pocket_op, "Path", None), "Commands", None)
                    if not cmds or len(cmds) == 0:
                        print(f"Pocket path failed for '{getattr(pocket_op, 'Label', pocket_op.Name)}'.")
                        continue
                except Exception:
                    pass

                lower_xy_pocket_count += 1
                created_ops.append(pocket_op)

        try:
            doc.recompute()
        except Exception:
            pass

        for created_op in list(created_ops or []):
            try:
                _apply_toolpath_normal_color(created_op, getattr(created_op, "ToolController", None))
            except Exception:
                pass

        if QtCore is not None and created_ops:
            created_op_names = [str(getattr(op, "Name", "") or "") for op in created_ops if getattr(op, "Name", None)]

            def _reapply_toolpath_colors_later():
                try:
                    for op_name in created_op_names:
                        try:
                            live_op = doc.getObject(op_name)
                        except Exception:
                            live_op = None
                        if live_op is not None:
                            _apply_toolpath_normal_color(live_op, getattr(live_op, "ToolController", None))
                except Exception:
                    pass

            try:
                QtCore.QTimer.singleShot(0, _reapply_toolpath_colors_later)
                QtCore.QTimer.singleShot(200, _reapply_toolpath_colors_later)
            except Exception:
                pass

        return created_ops

    try:
        active_view = None
        previous_body = None
        previous_part = None
        if Gui and getattr(Gui, "ActiveDocument", None):
            try:
                active_view = Gui.ActiveDocument.ActiveView
                previous_body = active_view.getActiveObject("pdbody")
            except Exception:
                previous_body = None
            try:
                previous_part = active_view.getActiveObject("part")
            except Exception:
                previous_part = None
            try:
                active_view.setActiveObject("pdbody", None)
            except Exception:
                pass
            try:
                active_view.setActiveObject("part", None)
            except Exception:
                pass

        try:
            job = PathGuiJob.Create(job_models, effective_template_path or None, openTaskPanel=False)
            job_name = getattr(job, "Name", None)
        finally:
            if active_view is not None:
                try:
                    active_view.setActiveObject("pdbody", previous_body)
                except Exception:
                    pass
                try:
                    active_view.setActiveObject("part", previous_part)
                except Exception:
                    pass

        job_live = doc.getObject(job_name) if job_name else job
        if not job_live:
            print("Inlay Job reference unavailable after creation.")
            return

        job_live.Label = f"{getattr(target_obj, 'Label', target_obj.Name)} Inlay Job"
        try:
            _move_job_to_document_root(job_live)
        except Exception:
            pass

        _apply_butler_post_defaults(job_live)
        _sanitize_job_stock(job_live, profile_base_obj)
        selected_tool_names = _select_tool_names_for_inlay(job_live, selected_tool_names_override)
        created_profile_ops = _create_profile_ops_for_inlay(
            job_live,
            profile_base_obj,
            selected_tool_names,
            inlay_final_depth_in,
            strict_selection=bool(selected_tool_names_override),
            keep_tool_down=bool(profile_keep_tool_down),
            min_travel=bool(profile_min_travel),
        )

        selected_tool_labels = _selected_tool_labels_for_job(job_live, selected_tool_names)
        inlay_settings = {
            "TemplatePath": str(effective_template_path or ""),
            "SelectedTools": ", ".join(str(label) for label in (selected_tool_labels or [])),
            "InlayCount": int(inlay_count),
            "NestRotate180": bool(nest_rotate_180),
            "FinalDepthIn": float(inlay_final_depth_in),
            "NestGapIn": float(nest_gap_in),
            "KeepToolDown": bool(profile_keep_tool_down),
            "MinTravel": bool(profile_min_travel),
            "FlipXAxis": bool(profile_flip_x_axis),
        }

        auto_container = _create_auto_container(
            target_obj,
            xup_created_items + [job_live],
            inlay_final_depth_in,
            settings=inlay_settings,
        )
        _expand_container_in_tree(auto_container)
        _expand_targets_in_tree([job_live] + list(created_profile_ops or []))

        try:
            if target_obj and getattr(target_obj, "ViewObject", None):
                target_obj.ViewObject.Visibility = False
        except Exception:
            pass

        doc.recompute()
    except Exception as exc:
        print(f"Failed to create CAM Job: {exc}")
        try:
            import traceback
            print(traceback.format_exc())
        except Exception:
            pass
        return

    try:
        if Gui:
            Gui.Selection.clearSelection()
            if job_live:
                Gui.Selection.addSelection(job_live)
    except Exception:
        pass

    job_for_msg = doc.getObject(job_name) if 'job_name' in locals() and job_name else job_live
    if not job_for_msg:
        print("Inlay Job creation did not produce a persistent job object.")
        return

    if effective_template_path:
        print(f"Created Inlay Job '{job_for_msg.Label}' using template '{effective_template_path}'.")
    else:
        print(f"Created Inlay Job '{job_for_msg.Label}' with default CAM setup.")

    _record_undo_cleanup_bundle()


def create_xup_nesting_layout(target=None, count=None, nesting_gap_in=None, rotate_180=None, show_dialog=True):
    doc = App.ActiveDocument
    if not doc:
        print("No active document.")
        return

    try:
        import Part
    except Exception as exc:
        print(f"Part module unavailable: {exc}")
        return

    def _next_name(base):
        idx = 1
        while doc.getObject(f"{base}_{idx}"):
            idx += 1
        return f"{base}_{idx}"

    def _inch_to_mm(value_inch):
        try:
            return float(App.Units.Quantity(f"{float(value_inch)} in").Value)
        except Exception:
            return float(value_inch) * 25.4

    def _unwrap_candidate(obj):
        if not obj:
            return None
        if str(getattr(obj, "TypeId", "")) == "App::Link":
            linked = getattr(obj, "LinkedObject", None)
            if linked:
                return linked
        return obj

    def _is_valid_shape_candidate(obj):
        if not obj:
            return False
        try:
            shape = getattr(obj, "Shape", None)
        except Exception:
            return False
        if not shape:
            return False
        try:
            if shape.isNull():
                return False
        except Exception:
            return False
        try:
            if getattr(shape, "Solids", None) and len(shape.Solids) > 0:
                return True
        except Exception:
            pass
        try:
            return bool(getattr(shape, "Faces", None))
        except Exception:
            return False

    def _shape_candidates():
        candidates = []
        seen = set()

        def _add_candidate(raw_obj):
            candidate = _unwrap_candidate(raw_obj)
            if not candidate:
                return
            name = getattr(candidate, "Name", "")
            if name and name in seen:
                return
            if not _is_valid_shape_candidate(candidate):
                return
            if name:
                seen.add(name)
            candidates.append(candidate)

        selected = Gui.Selection.getSelection() if Gui else []
        for obj in selected:
            _add_candidate(obj)

        cue_components = doc.getObject("CueComponents")
        if cue_components:
            for obj in getattr(cue_components, "Group", []) or []:
                _add_candidate(obj)

        for obj in getattr(doc, "Objects", []) or []:
            _add_candidate(obj)

        return candidates

    candidates = _shape_candidates()
    if not candidates:
        print("No valid shape object found for X-up nesting.")
        return

    inlay_prefs = None
    inlay_count = 4
    nest_rotate_180 = True
    nest_gap = 0.080
    try:
        inlay_prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/ButlerCues/InlayJob")
        inlay_count = max(1, int(inlay_prefs.GetInt("inlay_count", 4)))
        nest_rotate_180 = bool(
            inlay_prefs.GetBool(
                "nest_rotate_180",
                inlay_prefs.GetBool("auto_rotate_xup", True),
            )
        )
        nest_gap = max(0.0, float(inlay_prefs.GetFloat("nest_gap_in", 0.080)))
    except Exception:
        inlay_prefs = None

    if count is not None:
        try:
            inlay_count = max(1, int(count))
        except Exception:
            pass
    if nesting_gap_in is not None:
        try:
            nest_gap = max(0.0, float(nesting_gap_in))
        except Exception:
            pass
    if rotate_180 is not None:
        try:
            nest_rotate_180 = bool(rotate_180)
        except Exception:
            pass

    default_target = doc.getObject("Body002")
    if not _is_valid_shape_candidate(default_target):
        default_target = candidates[0]

    target_obj = default_target
    if target is not None:
        resolved_target = doc.getObject(target) if isinstance(target, str) else target
        if resolved_target and _is_valid_shape_candidate(resolved_target):
            target_obj = resolved_target

    if show_dialog and QtGui is not None:
        dialog = QtGui.QDialog()
        dialog.setWindowTitle("X-up Nesting")
        layout = QtGui.QFormLayout(dialog)

        target_combo = QtGui.QComboBox()
        target_index = 0
        for idx, obj in enumerate(candidates):
            target_combo.addItem(getattr(obj, "Label", obj.Name), obj.Name)
            if getattr(target_obj, "Name", "") == getattr(obj, "Name", ""):
                target_index = idx
        target_combo.setCurrentIndex(target_index)

        inlay_count_spin = QtGui.QSpinBox()
        inlay_count_spin.setRange(1, 1000)
        inlay_count_spin.setSingleStep(1)
        inlay_count_spin.setValue(int(inlay_count))

        nest_gap_spin = QtGui.QDoubleSpinBox()
        nest_gap_spin.setDecimals(4)
        nest_gap_spin.setRange(0.0, 1.0)
        nest_gap_spin.setSingleStep(0.005)
        nest_gap_spin.setValue(float(nest_gap))

        rotate_xup_check = QtGui.QCheckBox("Alternate 180° rotation")
        rotate_xup_check.setChecked(bool(nest_rotate_180))

        layout.addRow("Target", target_combo)
        layout.addRow("Count (X-up)", inlay_count_spin)
        layout.addRow("Gap (in)", nest_gap_spin)
        layout.addRow("Orientation", rotate_xup_check)

        buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec_() != QtGui.QDialog.Accepted:
            print("X-up nesting canceled.")
            return

        selected_name = target_combo.currentData()
        selected_obj = doc.getObject(selected_name) if selected_name else None
        if selected_obj and _is_valid_shape_candidate(selected_obj):
            target_obj = selected_obj

        inlay_count = max(1, int(inlay_count_spin.value()))
        nest_gap = max(0.0, float(nest_gap_spin.value()))
        nest_rotate_180 = bool(rotate_xup_check.isChecked())

        try:
            if inlay_prefs is not None:
                inlay_prefs.SetInt("inlay_count", int(inlay_count))
                inlay_prefs.SetBool("nest_rotate_180", bool(nest_rotate_180))
                inlay_prefs.SetFloat("nest_gap_in", float(nest_gap))
        except Exception:
            pass

    try:
        shape = getattr(target_obj, "Shape", None)
        if not shape or shape.isNull():
            print("Target has no valid shape for X-up nesting.")
            return
        bb = shape.BoundBox
    except Exception:
        print("Target has no valid shape for X-up nesting.")
        return

    gap_mm = max(0.05, _inch_to_mm(nest_gap))
    x_pitch = max(0.01, float(bb.XLength) + gap_mm)

    def _bb_distance_mm(bb1, bb2):
        try:
            dx = max(0.0, float(max(bb1.XMin - bb2.XMax, bb2.XMin - bb1.XMax)))
            dy = max(0.0, float(max(bb1.YMin - bb2.YMax, bb2.YMin - bb1.YMax)))
            dz = max(0.0, float(max(bb1.ZMin - bb2.ZMax, bb2.ZMin - bb1.ZMax)))
            return (dx * dx + dy * dy + dz * dz) ** 0.5
        except Exception:
            return 0.0

    def _shape_distance_mm(shape_a, shape_b):
        try:
            dist = shape_a.distToShape(shape_b)
            if isinstance(dist, (tuple, list)) and dist:
                return float(dist[0])
        except Exception:
            pass
        return _bb_distance_mm(shape_a.BoundBox, shape_b.BoundBox)

    def _is_clear(candidate_shape, placed_shapes):
        candidate_bb = candidate_shape.BoundBox
        for placed in placed_shapes:
            try:
                placed_bb = placed.BoundBox
                bb_gap = _bb_distance_mm(candidate_bb, placed_bb)
                if bb_gap + 1e-6 >= gap_mm:
                    continue
                if _shape_distance_mm(candidate_shape, placed) + 1e-6 < gap_mm:
                    return False
            except Exception:
                return False
        return True

    ylen = max(0.0, float(bb.YLength))
    y_candidates = [0.0]
    if ylen > 1e-6:
        y_candidates.extend([
            0.12 * ylen,
            -0.12 * ylen,
            0.24 * ylen,
            -0.24 * ylen,
            0.36 * ylen,
            -0.36 * ylen,
        ])

    search_step_mm = max(0.15, min(0.8, gap_mm * 0.6))
    placements = []
    solved_shapes = []
    previous_xmax = None
    for idx in range(int(inlay_count)):
        angle = 180.0 if (nest_rotate_180 and (idx % 2 == 1)) else 0.0
        seed_shape = shape.copy()
        if angle:
            try:
                seed_shape.rotate(seed_shape.BoundBox.Center, App.Vector(0, 0, 1), angle)
            except Exception:
                pass

        if idx == 0:
            solved_shape = seed_shape.copy()
            placements.append((0.0, 0.0, angle))
            solved_shapes.append(solved_shape)
            previous_xmax = float(solved_shape.BoundBox.XMax)
            continue

        seed_bb = seed_shape.BoundBox
        flush_dx = max(0.0, (previous_xmax or 0.0) - float(seed_bb.XMin) + gap_mm)
        start_dx = max(0.0, flush_dx - (0.70 * float(seed_bb.XLength)))
        max_dx = max(flush_dx + _inch_to_mm(20.0), start_dx + float(seed_bb.XLength) * 2.0)

        best_shape = None
        best_dx = None
        best_y = None

        dx = start_dx
        while dx <= max_dx:
            for yoff in y_candidates:
                candidate = seed_shape.copy()
                candidate.translate(App.Vector(dx, float(yoff), 0.0))
                if not _is_clear(candidate, solved_shapes):
                    continue

                if (
                    best_shape is None
                    or dx < best_dx - 1e-6
                    or (abs(dx - best_dx) <= 1e-6 and abs(float(yoff)) < abs(best_y))
                ):
                    best_shape = candidate
                    best_dx = float(dx)
                    best_y = float(yoff)
            if best_shape is not None:
                break
            dx += search_step_mm

        if best_shape is None:
            fallback_dx = float(idx) * x_pitch
            fallback = seed_shape.copy()
            fallback.translate(App.Vector(fallback_dx, 0.0, 0.0))
            best_shape = fallback
            best_dx = fallback_dx
            best_y = 0.0
            print(f"Nesting fallback used for instance {idx + 1}.")

        placements.append((best_dx, best_y, angle))
        solved_shapes.append(best_shape)
        previous_xmax = float(best_shape.BoundBox.XMax)

    container = None
    try:
        container = doc.addObject("App::Part", _next_name("XUpNesting"))
        container.Label = f"{getattr(target_obj, 'Label', target_obj.Name)} X-up Nesting"
    except Exception:
        container = None

    source_body = None
    source_binder = None
    try:
        source_body = doc.addObject("PartDesign::Body", _next_name("XUpNestSource"))
        if container and hasattr(container, "addObject"):
            try:
                container.addObject(source_body)
            except Exception:
                pass
        source_binder = source_body.newObject("PartDesign::SubShapeBinder", "SourceBinder")
        source_binder.Label = f"{getattr(target_obj, 'Label', target_obj.Name)} Source Binder"
        source_binder.Support = [(target_obj, tuple())]
        try:
            source_binder.TraceSupport = False
        except Exception:
            pass
        try:
            if hasattr(source_binder, "MapMode"):
                source_binder.MapMode = "Deactivated"
        except Exception:
            pass
    except Exception as exc:
        print(f"SubShapeBinder source setup failed; using shape copies ({exc}).")
        source_body = None
        source_binder = None

    layout_items = []
    rotated_instances = 0
    center_vec = App.Vector(float(bb.Center.x), float(bb.Center.y), float(bb.Center.z))
    for idx in range(int(inlay_count)):
        dx, dy, angle = placements[idx] if idx < len(placements) else (float(idx) * x_pitch, 0.0, 0.0)
        if angle:
            rotated_instances += 1
        rotation = App.Rotation(App.Vector(0, 0, 1), angle)
        center_after = rotation.multVec(center_vec)
        base_vec = App.Vector(float(dx), float(dy), 0.0).add(center_vec.sub(center_after))
        placement = App.Placement(base_vec, rotation)

        try:
            if source_binder:
                link_obj = doc.addObject("App::Link", _next_name("XUpNest"))
                link_obj.Label = f"{getattr(target_obj, 'Label', target_obj.Name)} Nest {idx + 1}"
                link_obj.LinkedObject = source_binder
                link_obj.Placement = placement
                if container and hasattr(container, "addObject"):
                    try:
                        container.addObject(link_obj)
                    except Exception:
                        pass
                layout_items.append(link_obj)
            else:
                raise RuntimeError("No binder source")
        except Exception:
            try:
                copy_shape = shape.copy()
                if angle:
                    copy_shape.rotate(copy_shape.BoundBox.Center, App.Vector(0, 0, 1), angle)
                copy_shape.translate(App.Vector(float(idx) * x_pitch, 0.0, 0.0))
                fallback = doc.addObject("Part::Feature", _next_name("XUpNestPart"))
                fallback.Label = f"{getattr(target_obj, 'Label', target_obj.Name)} Nest {idx + 1}"
                fallback.Shape = copy_shape
                if container and hasattr(container, "addObject"):
                    try:
                        container.addObject(fallback)
                    except Exception:
                        pass
                layout_items.append(fallback)
            except Exception:
                pass

    if len(layout_items) <= 1:
        print("X-up nesting produced only one instance; nothing created.")
        return

    try:
        xup_obj = doc.addObject("Part::Compound", _next_name("InlayXUpModel"))
        xup_obj.Label = f"{getattr(target_obj, 'Label', target_obj.Name)} X-up"
        xup_obj.Links = list(layout_items)
        if container and hasattr(container, "addObject"):
            try:
                container.addObject(xup_obj)
            except Exception:
                pass
        doc.recompute()

        if Gui:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(xup_obj)

        print(f"Created X-up nesting layout '{xup_obj.Label}' with {int(inlay_count)} instance(s).")
        if source_binder:
            print(f"Created {len(layout_items)} editable nesting link item(s) from a SubShapeBinder source.")
        else:
            print(f"Created {len(layout_items)} editable nesting item(s) as shape copies.")
        print(f"Gap target: {gap_mm / 25.4:.3f} in.")
        if rotated_instances > 0:
            print(f"Applied alternating 180° rotation to {rotated_instances} instance(s).")
        return xup_obj
    except Exception as exc:
        print(f"Failed to create X-up nesting layout: {exc}")
        return


def create_pocket_cnc_job(
    target=None,
    template_path=None,
    show_dialog=True,
    selected_tool_names_override=None,
    pocket_final_depth_override=None,
    pocket_keep_tool_down_override=None,
    pocket_min_travel_override=None,
    pocket_skip_large_if_under_minutes_override=None,
    pocket_skip_large_minutes_threshold_override=None,
):
    doc = App.ActiveDocument
    if not doc:
        print("No active document.")
        return

    _ensure_butler_container_cleanup_observer()

    pre_object_names = set()
    try:
        pre_object_names = {str(getattr(obj, "Name", "") or "") for obj in (getattr(doc, "Objects", []) or [])}
    except Exception:
        pre_object_names = set()

    def _record_undo_cleanup_bundle():
        try:
            if not _butler_enable_undo_cleanup_observer:
                return
            current_names = [
                str(getattr(obj, "Name", "") or "")
                for obj in (getattr(doc, "Objects", []) or [])
                if str(getattr(obj, "Name", "") or "") not in pre_object_names
            ]
            current_names = [name for name in current_names if name]
            if not current_names:
                return
            doc_name = str(getattr(doc, "Name", "") or "").strip()
            if not doc_name:
                return
            _butler_undo_cleanup_bundles.setdefault(doc_name, []).append(current_names)
        except Exception:
            pass

    try:
        import Path.Main.Job as PathJob
        import Path.Main.Gui.Job as PathGuiJob
        import Path.Preferences as PathPreferences
        import Part
        import glob
    except Exception as exc:
        print(f"CAM module unavailable: {exc}")
        print("Open FreeCAD with CAM workbench support enabled.")
        return

    def _next_name(base):
        idx = 1
        while doc.getObject(f"{base}_{idx}"):
            idx += 1
        return f"{base}_{idx}"

    def _unwrap_candidate(obj):
        if not obj:
            return None
        if str(getattr(obj, "TypeId", "")) == "App::Link":
            linked = getattr(obj, "LinkedObject", None)
            if linked:
                return linked
        return obj

    def _looks_like_fillet_source(obj):
        candidate = _unwrap_candidate(obj)
        if not candidate:
            return False
        for prop_name in (
            "FilletRadiusInch",
            "FilletSettingsSummary",
            "FilletSourceLabels",
            "FilletRequireFullCoverage",
        ):
            try:
                if hasattr(candidate, prop_name):
                    return True
            except Exception:
                pass
        return False

    def _warn_non_fillet_source(obj, workflow_label):
        if _looks_like_fillet_source(obj):
            return True
        obj_label = str(getattr(obj, "Label", getattr(obj, "Name", "selected object")) or "selected object")
        msg = (
            f"Warning: '{obj_label}' does not appear to be created by Fillet for CNC. "
            f"Continuing with {workflow_label} may produce accidental bad fit."
        )
        print(msg)
        try:
            if QtGui is not None and Gui is not None:
                result = QtGui.QMessageBox.warning(
                    None,
                    f"{workflow_label} Safety Warning",
                    msg + "\n\nContinue anyway?",
                    QtGui.QMessageBox.Ok | QtGui.QMessageBox.Cancel,
                    QtGui.QMessageBox.Ok,
                )
                if result != QtGui.QMessageBox.Ok:
                    print(f"{workflow_label} canceled by user.")
                    return False
        except Exception:
            pass
        return True

    def _safe_mm(value):
        try:
            return float(getattr(value, "Value", value))
        except Exception:
            return 0.0

    def _set_prop_length(obj, prop_name, value_mm):
        if not hasattr(obj, prop_name):
            return
        try:
            setattr(obj, prop_name, max(0.0, float(value_mm)))
        except Exception:
            try:
                getattr(obj, prop_name).Value = max(0.0, float(value_mm))
            except Exception:
                pass

    def _sanitize_job_stock(job_obj, model_obj):
        stock = getattr(job_obj, "Stock", None)
        if not stock or not model_obj:
            return
        try:
            shape = getattr(model_obj, "Shape", None)
            bb = shape.BoundBox if shape and not shape.isNull() else None
        except Exception:
            bb = None
        if not bb:
            return

        min_dim = 0.01
        ext_x_neg = _safe_mm(getattr(stock, "ExtXneg", 0.0))
        ext_x_pos = _safe_mm(getattr(stock, "ExtXpos", 0.0))
        ext_y_neg = _safe_mm(getattr(stock, "ExtYneg", 0.0))
        ext_y_pos = _safe_mm(getattr(stock, "ExtYpos", 0.0))
        ext_z_neg = _safe_mm(getattr(stock, "ExtZneg", 0.0))
        ext_z_pos = _safe_mm(getattr(stock, "ExtZpos", 0.0))

        ext_x_neg = max(0.0, ext_x_neg)
        ext_x_pos = max(0.0, ext_x_pos)
        ext_y_neg = max(0.0, ext_y_neg)
        ext_y_pos = max(0.0, ext_y_pos)
        ext_z_neg = max(0.0, ext_z_neg)
        ext_z_pos = max(0.0, ext_z_pos)

        if bb.XLength + ext_x_neg + ext_x_pos < min_dim:
            ext_x_pos = min_dim - bb.XLength - ext_x_neg
        if bb.YLength + ext_y_neg + ext_y_pos < min_dim:
            ext_y_pos = min_dim - bb.YLength - ext_y_neg
        if bb.ZLength + ext_z_neg + ext_z_pos < min_dim:
            ext_z_pos = min_dim - bb.ZLength - ext_z_neg

        _set_prop_length(stock, "ExtXneg", ext_x_neg)
        _set_prop_length(stock, "ExtXpos", ext_x_pos)
        _set_prop_length(stock, "ExtYneg", ext_y_neg)
        _set_prop_length(stock, "ExtYpos", ext_y_pos)
        _set_prop_length(stock, "ExtZneg", ext_z_neg)
        _set_prop_length(stock, "ExtZpos", ext_z_pos)

    def _inch_to_mm(value_inch):
        try:
            return float(App.Units.Quantity(f"{float(value_inch)} in").Value)
        except Exception:
            return float(value_inch) * 25.4

    def _set_stock_to_model_bounds(job_obj):
        stock = getattr(job_obj, "Stock", None)
        if not stock:
            return
        _set_prop_length(stock, "ExtXneg", 0.0)
        _set_prop_length(stock, "ExtXpos", 0.0)
        _set_prop_length(stock, "ExtYneg", 0.0)
        _set_prop_length(stock, "ExtYpos", 0.0)
        _set_prop_length(stock, "ExtZneg", 0.0)
        _set_prop_length(stock, "ExtZpos", 0.0)

    def _create_pocket_job_model_from_inlay(inlay_obj):
        if not inlay_obj:
            return None

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

        def _iter_cut_tools(shape_obj):
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
                try:
                    cleaned = _strip_shape_history(solid)
                except Exception:
                    cleaned = solid
                tools.append(cleaned)

            return tools or [shape_obj]

        try:
            inlay_shape = getattr(inlay_obj, "Shape", None)
            bb = inlay_shape.BoundBox if inlay_shape and not inlay_shape.isNull() else None
        except Exception:
            bb = None

        if not bb:
            print("Selected inlay has no valid shape.")
            return None

        side_margin = _inch_to_mm(0.1)
        y_extra = _inch_to_mm(0.1)
        z_extra = _inch_to_mm(0.01)

        x_min = float(bb.XMin) - side_margin
        x_len = float(bb.XLength) + (2.0 * side_margin)
        y_min = 0.0
        y_max = float(bb.YMax) + y_extra
        y_len = max(0.01, y_max - y_min)
        z_depth_from_zero = max(abs(float(bb.ZMin)), abs(float(bb.ZMax)))
        z_len = max(0.01, z_depth_from_zero + z_extra)
        z_min = -z_len

        cut_obj = doc.addObject("Part::Feature", _next_name("PocketModel"))
        cut_obj.Label = f"{getattr(inlay_obj, 'Label', inlay_obj.Name)} Pocket Model"

        try:
            if getattr(inlay_obj, "ViewObject", None):
                inlay_obj.ViewObject.Visibility = False
        except Exception:
            pass

        try:
            blank_shape = Part.makeBox(x_len, y_len, z_len, App.Vector(x_min, y_min, z_min), App.Vector(0, 0, 1))
            try:
                tool_shape = _strip_shape_history(inlay_shape.copy())
            except Exception:
                tool_shape = _strip_shape_history(inlay_shape)

            result_shape = blank_shape
            tool_shapes = _iter_cut_tools(tool_shape)
            for tool_part in tool_shapes:
                if not tool_part:
                    continue
                result_shape = result_shape.cut(tool_part)

            if result_shape.isNull() or not getattr(result_shape, "Solids", None):
                print("Pocket cut produced no solid result. Check inlay/blank overlap.")
                return None, []
            try:
                rot_shape = result_shape.copy()
                rot_shape.rotate(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 180)
                result_shape = rot_shape
            except Exception:
                pass
            result_shape = _strip_shape_history(result_shape)
            cut_obj.Shape = result_shape
            if hasattr(cut_obj, "Refine"):
                try:
                    cut_obj.Refine = True
                except Exception:
                    pass
        except Exception as exc:
            print(f"Failed to cut inlay from blank: {exc}")
            return None, []

        doc.recompute()

        if not _is_valid_solid_candidate(cut_obj):
            print("Failed to build valid pocket model from selected inlay.")
            return None, []

        return cut_obj, [cut_obj]

    def _create_auto_container(original_obj, created_items, glue_tolerance_in=None, settings=None):
        def _ensure_parent_group(preferred_label):
            try:
                for obj in (getattr(doc, "Objects", []) or []):
                    if getattr(obj, "TypeId", "") != "App::DocumentObjectGroup":
                        continue
                    if str(getattr(obj, "Label", "") or "").strip() == str(preferred_label):
                        return obj
            except Exception:
                pass
            try:
                existing = doc.getObject(str(preferred_label))
                if existing and getattr(existing, "TypeId", "") == "App::DocumentObjectGroup":
                    return existing
            except Exception:
                pass
            try:
                group = doc.addObject("App::DocumentObjectGroup", _next_name(str(preferred_label)))
                group.Label = str(preferred_label)
                return group
            except Exception:
                return None

        def _collect_job_related_names(seed_items, container_name):
            names = []

            def _add_name(candidate):
                candidate_name = str(getattr(candidate, "Name", "") or "").strip() if candidate else ""
                if candidate_name and candidate_name != container_name and candidate_name not in names:
                    names.append(candidate_name)

            for item in list(seed_items or []):
                if not item:
                    continue
                _add_name(item)

                is_job = False
                try:
                    is_job = hasattr(item, "Operations") and hasattr(item, "Model") and hasattr(item, "Stock")
                except Exception:
                    is_job = False
                if not is_job:
                    continue

                try:
                    _add_name(getattr(item, "Stock", None))
                except Exception:
                    pass

                try:
                    model_obj = getattr(item, "Model", None)
                except Exception:
                    model_obj = None
                _add_name(model_obj)
                try:
                    for model_child in list(getattr(model_obj, "Group", []) or []):
                        _add_name(model_child)
                except Exception:
                    pass

                try:
                    for operation in list(getattr(item, "Operations", []) or []):
                        _add_name(operation)
                except Exception:
                    pass

                for tool_prop in ("ToolController", "ToolControllers", "Tools"):
                    try:
                        tool_value = getattr(item, tool_prop, None)
                    except Exception:
                        tool_value = None
                    if tool_value is None:
                        continue
                    if isinstance(tool_value, (list, tuple)):
                        for tool_obj in tool_value:
                            _add_name(tool_obj)
                    else:
                        _add_name(tool_value)

            return names

        original_name = getattr(original_obj, "Name", "Solid")
        container = doc.addObject("App::DocumentObjectGroup", _next_name("InlayFolder"))
        glue_suffix = ""
        try:
            if glue_tolerance_in is not None:
                glue_suffix = f" (glue tolerance {max(0.0, float(glue_tolerance_in)):.4f} in)"
        except Exception:
            glue_suffix = ""
        container.Label = f"{original_name}{glue_suffix}"
        try:
            parent_group = _ensure_parent_group("Pockets")
            if parent_group:
                parent_group.addObject(container)
        except Exception:
            pass
        for item in created_items:
            if not item:
                continue
            try:
                container.addObject(item)
            except Exception:
                pass

        def _set_container_property(prop_name, prop_type, value):
            try:
                if not hasattr(container, prop_name):
                    container.addProperty(prop_type, prop_name, "ButlerCues Settings")
            except Exception:
                return
            try:
                setattr(container, prop_name, value)
            except Exception:
                pass

        _set_container_property("ButlerCuesWorkflow", "App::PropertyString", "Pocket Job")
        for key, value in dict(settings or {}).items():
            prop_name = f"ButlerCues{str(key)}"
            if isinstance(value, bool):
                _set_container_property(prop_name, "App::PropertyBool", bool(value))
            elif isinstance(value, int) and not isinstance(value, bool):
                _set_container_property(prop_name, "App::PropertyInteger", int(value))
            elif isinstance(value, float):
                _set_container_property(prop_name, "App::PropertyFloat", float(value))
            else:
                _set_container_property(prop_name, "App::PropertyString", str(value or ""))

        try:
            container_name = str(getattr(container, "Name", "") or "").strip()
            doc_name = str(getattr(doc, "Name", "") or "").strip()
            if container_name and doc_name:
                tracked_child_names = _collect_job_related_names(created_items, container_name)
                tracked_job_name = ""
                for item in created_items:
                    try:
                        if item and hasattr(item, "Operations") and hasattr(item, "Model") and hasattr(item, "Stock"):
                            tracked_job_name = str(getattr(item, "Name", "") or "").strip()
                            if tracked_job_name:
                                break
                    except Exception:
                        pass
                _butler_managed_container_children[f"{doc_name}:{container_name}"] = {
                    "children": tracked_child_names,
                    "job": tracked_job_name,
                }
        except Exception:
            pass

        return container

    def _expand_container_in_tree(container_obj):
        if not container_obj or not Gui or QtGui is None:
            return
        try:
            from PySide import QtCore

            main_window = Gui.getMainWindow()
            if not main_window:
                return
            targets = {str(getattr(container_obj, "Name", "")), str(getattr(container_obj, "Label", ""))}

            for tree_view in main_window.findChildren(QtGui.QTreeView):
                model = tree_view.model()
                if not model:
                    continue

                def _expand_recursive(parent_index):
                    rows = model.rowCount(parent_index)
                    for row in range(rows):
                        idx = model.index(row, 0, parent_index)
                        text = str(model.data(idx) or "")
                        if text in targets:
                            tree_view.setExpanded(idx, True)
                            return True
                        if _expand_recursive(idx):
                            return True
                    return False

                if _expand_recursive(QtCore.QModelIndex()):
                    return
        except Exception:
            pass

    def _expand_targets_in_tree(target_objects):
        if not target_objects or not Gui or QtGui is None:
            return
        try:
            from PySide import QtCore

            target_tokens = set()
            for obj in list(target_objects or []):
                if not obj:
                    continue
                try:
                    name = str(getattr(obj, "Name", "") or "").strip()
                    if name:
                        target_tokens.add(name)
                except Exception:
                    pass
                try:
                    label = str(getattr(obj, "Label", "") or "").strip()
                    if label:
                        target_tokens.add(label)
                except Exception:
                    pass

            if not target_tokens:
                return

            main_window = Gui.getMainWindow()
            if not main_window:
                return

            for tree_view in main_window.findChildren(QtGui.QTreeView):
                model = tree_view.model()
                if not model:
                    continue

                def _expand_recursive(parent_index):
                    rows = model.rowCount(parent_index)
                    for row in range(rows):
                        idx = model.index(row, 0, parent_index)
                        text = str(model.data(idx) or "")
                        if text in target_tokens:
                            tree_view.setExpanded(idx, True)
                        _expand_recursive(idx)

                _expand_recursive(QtCore.QModelIndex())
        except Exception:
            pass

    def _non_top_faces_grouped_by_z(model_obj, z_merge_tol=1e-3):
        try:
            shape = getattr(model_obj, "Shape", None)
            faces = list(getattr(shape, "Faces", []) or [])
        except Exception:
            faces = []
        if not faces:
            return []

        tol = 1e-6
        xy_faces = []
        for idx, face in enumerate(faces, start=1):
            try:
                surface = getattr(face, "Surface", None)
                is_planar = isinstance(surface, Part.Plane)
            except Exception:
                is_planar = False
            if not is_planar:
                continue

            try:
                normal = face.normalAt(0.0, 0.0)
                if abs(float(getattr(normal, "z", 0.0))) < 0.95:
                    continue
            except Exception:
                continue

            try:
                center_z = float(face.CenterOfMass.z)
            except Exception:
                continue

            xy_faces.append((idx, center_z))

        if len(xy_faces) <= 2:
            return []

        max_z = max(z for _, z in xy_faces)
        min_z = min(z for _, z in xy_faces)

        middle_faces = [(idx, z) for idx, z in xy_faces if (z < max_z - tol and z > min_z + tol)]
        if not middle_faces:
            return []

        middle_faces.sort(key=lambda item: item[1], reverse=True)
        levels = []
        for face_idx, z_val in middle_faces:
            assigned = False
            for level in levels:
                try:
                    if abs(float(level.get("z_mm", 0.0)) - float(z_val)) <= float(z_merge_tol):
                        level.setdefault("subs", []).append(f"Face{face_idx}")
                        assigned = True
                        break
                except Exception:
                    continue
            if not assigned:
                levels.append({"z_mm": float(z_val), "subs": [f"Face{face_idx}"]})

        for level in levels:
            try:
                level["subs"] = list(dict.fromkeys(level.get("subs", []) or []))
            except Exception:
                pass

        levels.sort(key=lambda item: float(item.get("z_mm", 0.0)), reverse=True)
        cumulative_subs = []
        for level in reversed(levels):
            try:
                new_subs = list(level.get("subs", []) or [])
                cumulative_subs = list(dict.fromkeys(new_subs + cumulative_subs))
                level["cumulative_subs"] = list(cumulative_subs)
            except Exception:
                level["cumulative_subs"] = list(level.get("subs", []) or [])
        return levels

    def _non_top_face_subnames(model_obj):
        out = []
        for level in _non_top_faces_grouped_by_z(model_obj):
            out.extend(list(level.get("subs", []) or []))
        return out

    def _create_pocket_op_non_top_faces(
        job_obj,
        model_obj,
        roughing_undersize_inch,
        glue_oversize_inch,
        final_depth_inch,
        selected_tool_names=None,
        keep_tool_down=True,
        min_travel=True,
        skip_large_if_under_minutes=False,
        skip_large_minutes_threshold=3.0,
    ):
        if not job_obj or not model_obj:
            return []

        def _as_mm(value):
            try:
                return float(getattr(value, "Value", value))
            except Exception:
                return 0.0

        def _sanitize_name_part(text):
            raw = str(text or "").strip()
            cleaned = "".join(ch if ch.isalnum() else "_" for ch in raw)
            cleaned = cleaned.strip("_")
            return cleaned or "bit"

        def _unique_label(base_label):
            existing = {str(getattr(o, "Label", "")) for o in (getattr(doc, "Objects", []) or [])}
            if base_label not in existing:
                return base_label
            idx = 2
            while f"{base_label}_{idx}" in existing:
                idx += 1
            return f"{base_label}_{idx}"

        def _bit_name_from_job_or_op(op_obj, job):
            tc = getattr(op_obj, "ToolController", None)
            if not tc:
                try:
                    tools_group = getattr(getattr(job, "Tools", None), "Group", None) or []
                    if tools_group:
                        tc = tools_group[0]
                except Exception:
                    tc = None

            if tc:
                tool = getattr(tc, "Tool", None)
                try:
                    diameter_mm = _as_mm(getattr(tool, "Diameter", 0.0))
                    if diameter_mm > 0.0:
                        diameter_in = diameter_mm / 25.4
                        return f"{diameter_in:.3f}in"
                except Exception:
                    pass
                for candidate in (
                    getattr(tool, "Label", None),
                    getattr(tool, "Name", None),
                    getattr(tc, "Label", None),
                    getattr(tc, "Name", None),
                ):
                    if candidate:
                        return _sanitize_name_part(candidate)
            return "bit"

        def _tool_diameter_mm(tc_obj):
            try:
                tool = getattr(tc_obj, "Tool", None)
                d = getattr(tool, "Diameter", None)
                return _as_mm(d)
            except Exception:
                return 0.0

        def _tool_controllers_sorted_largest_first(job):
            try:
                tools_group = list(getattr(getattr(job, "Tools", None), "Group", None) or [])
            except Exception:
                tools_group = []

            with_diam = []
            without_diam = []
            for tc in tools_group:
                dmm = _tool_diameter_mm(tc)
                if dmm > 0:
                    with_diam.append((dmm, tc))
                else:
                    without_diam.append(tc)

            with_diam.sort(key=lambda item: item[0], reverse=True)
            ordered = [tc for _, tc in with_diam]
            ordered.extend(without_diam)
            return ordered

        def _selected_tool_controllers(job, selected_names):
            ordered = _tool_controllers_sorted_largest_first(job)
            if not ordered:
                return []
            selected_set = {str(name) for name in (selected_names or []) if str(name).strip()}
            selected_tcs = [tc for tc in ordered if str(getattr(tc, "Name", "")) in selected_set]

            if selected_tcs:
                return selected_tcs

            if selected_set:
                print("No matching Pocket tool controllers for selected bit(s); skipping Pocket operations.")
                return []

            return ordered[:2] if len(ordered) >= 2 else ordered[:1]

        def _create_pocket_with_selected_tc(name, parent_job, selected_tc):
            original_ui = getattr(PathUtils, "UserInput", None)

            class _ToolSelectionShim:
                def selectedToolController(self):
                    return selected_tc

                def chooseToolController(self, controllers):
                    if selected_tc in controllers:
                        return selected_tc
                    return controllers[0] if controllers else None

            try:
                if selected_tc is not None:
                    PathUtils.UserInput = _ToolSelectionShim()
                return PathPocketShape.Create(name, obj=None, parentJob=parent_job)
            finally:
                PathUtils.UserInput = original_ui

        try:
            import Path.Op.PocketShape as PathPocketShape
            import Path.Op.Gui.PocketShape as PathPocketShapeGui
            import Path.Op.Gui.Base as PathOpGuiBase
            import PathScripts.PathUtils as PathUtils
        except Exception as exc:
            print(f"Pocket Shape module unavailable: {exc}")
            return []

        face_levels = _non_top_faces_grouped_by_z(model_obj)
        if not face_levels:
            print("No interior XY-plane faces found for Pocket operation.")
            return []
        face_names = [sub for level in face_levels for sub in (level.get("subs", []) or [])]

        try:
            created_ops = []
            roughing_undersize_mm = _inch_to_mm(max(0.0, float(roughing_undersize_inch)))
            glue_oversize_mm = _inch_to_mm(max(0.0, float(glue_oversize_inch)))
            selected_tcs = _selected_tool_controllers(job_obj, selected_tool_names)

            tc_color_map = {}
            tc_name_color_map = {}
            for idx, tc in enumerate(selected_tcs):
                if idx < len(_TOOLPATH_COLORS):
                    color = _TOOLPATH_COLORS[idx]
                    tc_color_map[tc] = color
                    tc_name = str(getattr(tc, "Name", "") or "")
                    if tc_name:
                        tc_name_color_map[tc_name] = color

            def _apply_toolpath_normal_color(op_obj, selected_tc):
                color = tc_color_map.get(selected_tc)
                if color is None and op_obj is not None:
                    try:
                        tc_name = str(getattr(getattr(op_obj, "ToolController", None), "Name", "") or "")
                    except Exception:
                        tc_name = ""
                    if tc_name:
                        color = tc_name_color_map.get(tc_name)
                view_obj = getattr(op_obj, "ViewObject", None)
                if not color or view_obj is None:
                    return
                try:
                    rgba = tuple(float(c) for c in color)
                    view_obj.NormalColor = rgba
                    for child in list(getattr(op_obj, "OutListRecursive", []) or getattr(op_obj, "OutList", []) or []):
                        child_view = getattr(child, "ViewObject", None)
                        if child_view is not None:
                            try:
                                child_view.NormalColor = rgba
                            except Exception:
                                pass
                except Exception:
                    pass

            def _op_has_cut_motion(op_obj):
                try:
                    cmds_local = list(getattr(getattr(op_obj, "Path", None), "Commands", []) or [])
                except Exception:
                    cmds_local = []
                for cmd_local in cmds_local:
                    try:
                        n_local = str(getattr(cmd_local, "Name", "") or "").upper()
                    except Exception:
                        n_local = ""
                    if n_local in ("G1", "G2", "G3"):
                        return True
                return False

            def _as_mm_float(value, default=0.0):
                try:
                    return float(getattr(value, "Value", value))
                except Exception:
                    return float(default)

            def _estimate_cut_minutes_for_op(op_obj, tc_obj):
                try:
                    cycle_text = str(getattr(op_obj, "CycleTime", "") or "").strip()
                except Exception:
                    cycle_text = ""

                if cycle_text and ":" in cycle_text and "error" not in cycle_text.lower():
                    try:
                        parts = [int(p) for p in cycle_text.split(":")]
                        if len(parts) == 3:
                            h, m, s = parts
                            return (h * 3600.0 + m * 60.0 + s) / 60.0
                        if len(parts) == 2:
                            m, s = parts
                            return (m * 60.0 + s) / 60.0
                    except Exception:
                        pass

                try:
                    cmds_local = list(getattr(getattr(op_obj, "Path", None), "Commands", []) or [])
                except Exception:
                    cmds_local = []

                try:
                    horiz_feed_mm_min = _as_mm_float(getattr(tc_obj, "HorizFeed", None), 0.0)
                except Exception:
                    horiz_feed_mm_min = 0.0
                if horiz_feed_mm_min <= 1e-6:
                    horiz_feed_mm_min = 300.0

                x = y = z = None
                cut_distance_mm = 0.0
                for cmd_local in cmds_local:
                    try:
                        name_local = str(getattr(cmd_local, "Name", "") or "").upper()
                    except Exception:
                        name_local = ""
                    if name_local not in ("G1", "G2", "G3"):
                        continue

                    params = getattr(cmd_local, "Parameters", None)
                    if not isinstance(params, dict):
                        continue

                    nx = x
                    ny = y
                    nz = z
                    if "X" in params:
                        try:
                            nx = float(params.get("X"))
                        except Exception:
                            pass
                    if "Y" in params:
                        try:
                            ny = float(params.get("Y"))
                        except Exception:
                            pass
                    if "Z" in params:
                        try:
                            nz = float(params.get("Z"))
                        except Exception:
                            pass

                    if None not in (x, y, z, nx, ny, nz):
                        dx = float(nx) - float(x)
                        dy = float(ny) - float(y)
                        dz = float(nz) - float(z)
                        cut_distance_mm += (dx * dx + dy * dy + dz * dz) ** 0.5

                    x, y, z = nx, ny, nz

                return float(cut_distance_mm) / float(horiz_feed_mm_min)

            if not selected_tcs:
                print("No tool controllers available in Pocket Job.")
                return []

            try:
                resolved_labels = [
                    str(getattr(tc, "Label", getattr(tc, "Name", "ToolController")) or "ToolController")
                    for tc in selected_tcs
                ]
                print(f"Pocket tool controllers resolved: {', '.join(resolved_labels)}")
            except Exception:
                pass

            requested_final_mm = -abs(_inch_to_mm(final_depth_inch))

            ops_with_cut_motion = 0
            smallest_bit_no_cut_label = None
            total_passes = len(selected_tcs)
            for level_index, level_info in enumerate(face_levels, start=1):
                level_face_names = list(level_info.get("cumulative_subs", []) or level_info.get("subs", []) or [])
                if not level_face_names:
                    continue

                try:
                    level_z_mm = float(level_info.get("z_mm", requested_final_mm))
                except Exception:
                    level_z_mm = float(requested_final_mm)
                level_final_mm = max(float(requested_final_mm), float(level_z_mm))
                kept_pass_count = 0

                try:
                    print(
                        f"Pocket level {level_index}/{len(face_levels)}: {len(level_face_names)} cumulative face(s), target Z={level_final_mm:.4f} mm."
                    )
                except Exception:
                    pass

                for op_index, selected_tc in enumerate(selected_tcs):
                    is_final_pass = (op_index == (total_passes - 1))
                    pocket_op = _create_pocket_with_selected_tc(
                        _next_name("PocketShape"),
                        job_obj,
                        selected_tc,
                    )
                    if not pocket_op:
                        continue

                    try:
                        if getattr(pocket_op, "ViewObject", None):
                            pocket_op.ViewObject.Proxy = PathOpGuiBase.ViewProvider(
                                pocket_op.ViewObject,
                                PathPocketShapeGui.Command.res,
                            )
                            pocket_op.ViewObject.Visibility = True
                            try:
                                pocket_op.ViewObject.Proxy.setDeleteObjectsOnReject(False)
                            except Exception:
                                pass
                            _apply_toolpath_normal_color(pocket_op, selected_tc)
                    except Exception:
                        pass

                    if selected_tc and hasattr(pocket_op, "ToolController"):
                        try:
                            pocket_op.ToolController = selected_tc
                        except Exception:
                            pass

                    try:
                        bit_part = _bit_name_from_job_or_op(pocket_op, job_obj)
                        pocket_op.Label = _unique_label(f"PocketShape_{bit_part}_L{level_index}")
                    except Exception:
                        pass

                    pocket_op.Base = [(model_obj, level_face_names)]

                    if hasattr(pocket_op, "KeepToolDown"):
                        try:
                            pocket_op.KeepToolDown = bool(keep_tool_down)
                        except Exception:
                            pass
                    if hasattr(pocket_op, "MinTravel"):
                        try:
                            pocket_op.MinTravel = bool(min_travel)
                        except Exception:
                            pass

                    try:
                        if hasattr(pocket_op, "setExpression"):
                            try:
                                pocket_op.setExpression("FinalDepth", None)
                            except Exception:
                                pass
                    except Exception:
                        pass

                    try:
                        if hasattr(pocket_op, "FinalDepth"):
                            try:
                                pocket_op.FinalDepth = f"{level_final_mm} mm"
                            except Exception:
                                try:
                                    pocket_op.FinalDepth.Value = float(level_final_mm)
                                except Exception:
                                    pass
                    except Exception:
                        pass

                    rest_enabled = bool(kept_pass_count > 0)
                    if hasattr(pocket_op, "UseRestMachining"):
                        try:
                            pocket_op.UseRestMachining = rest_enabled
                        except Exception:
                            pass
                    if hasattr(pocket_op, "RestMachining"):
                        try:
                            pocket_op.RestMachining = rest_enabled
                        except Exception:
                            pass

                    if hasattr(pocket_op, "ExtraOffset"):
                        try:
                            if hasattr(pocket_op, "setExpression"):
                                try:
                                    pocket_op.setExpression("ExtraOffset", None)
                                except Exception:
                                    pass
                            if (not is_final_pass) and roughing_undersize_mm > 1e-6:
                                pocket_op.ExtraOffset = f"{roughing_undersize_mm} mm"
                                try:
                                    print(
                                        f"Pocket level {level_index} pass {op_index + 1}/{total_passes} ({getattr(pocket_op, 'Label', 'PocketShape')}): "
                                        f"Rest={rest_enabled}, ExtraOffset={roughing_undersize_mm / 25.4:.4f} in (roughing/undersize)."
                                    )
                                except Exception:
                                    pass
                            else:
                                pocket_op.ExtraOffset = f"{-glue_oversize_mm} mm"
                                try:
                                    print(
                                        f"Pocket level {level_index} pass {op_index + 1}/{total_passes} ({getattr(pocket_op, 'Label', 'PocketShape')}): "
                                        f"Rest={rest_enabled}, ExtraOffset={-glue_oversize_mm / 25.4:.4f} in (finish/glue oversize)."
                                    )
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    try:
                        doc.recompute()
                    except Exception:
                        pass

                    _apply_toolpath_normal_color(pocket_op, selected_tc)

                    try:
                        cmds = list(getattr(getattr(pocket_op, "Path", None), "Commands", []) or [])
                    except Exception:
                        cmds = []

                    has_cut_motion = False
                    for cmd in cmds:
                        try:
                            name = str(getattr(cmd, "Name", "") or "").upper()
                        except Exception:
                            name = ""
                        if name in ("G1", "G2", "G3"):
                            has_cut_motion = True
                            break

                    if has_cut_motion:
                        ops_with_cut_motion += 1
                    else:
                        try:
                            op_label = str(getattr(pocket_op, "Label", getattr(pocket_op, "Name", "PocketShape")) or "PocketShape")
                        except Exception:
                            op_label = "PocketShape"
                        print(f"'{op_label}' generated no cut moves.")
                        if is_final_pass:
                            smallest_bit_no_cut_label = op_label
                        else:
                            try:
                                if hasattr(pocket_op, "Active"):
                                    pocket_op.Active = False
                                if getattr(pocket_op, "ViewObject", None):
                                    pocket_op.ViewObject.Visibility = False
                                doc.recompute()
                            except Exception:
                                pass
                            continue

                    skip_this_op = False
                    try:
                        if (
                            bool(skip_large_if_under_minutes)
                            and (not is_final_pass)
                            and has_cut_motion
                        ):
                            estimated_minutes = _estimate_cut_minutes_for_op(pocket_op, selected_tc)
                            threshold_minutes = max(0.0, float(skip_large_minutes_threshold))
                            try:
                                op_label = str(getattr(pocket_op, "Label", getattr(pocket_op, "Name", "PocketShape")) or "PocketShape")
                            except Exception:
                                op_label = "PocketShape"
                            try:
                                cycle_text = str(getattr(pocket_op, "CycleTime", "") or "").strip()
                                has_cycle_estimate = bool(cycle_text and ":" in cycle_text and "error" not in cycle_text.lower())
                            except Exception:
                                has_cycle_estimate = False
                            try:
                                print(
                                    f"Pocket pass runtime estimate for '{op_label}': {estimated_minutes:.2f} min (threshold {threshold_minutes:.2f} min, source={'CycleTime' if has_cycle_estimate else 'distance-feed fallback'})."
                                )
                            except Exception:
                                pass
                            if estimated_minutes < threshold_minutes:
                                skip_this_op = True
                                try:
                                    op_label = str(getattr(pocket_op, "Label", getattr(pocket_op, "Name", "PocketShape")) or "PocketShape")
                                except Exception:
                                    op_label = "PocketShape"
                                print(
                                    f"Skipping larger-bit pass '{op_label}' (est. {estimated_minutes:.2f} min < {threshold_minutes:.2f} min)."
                                )
                    except Exception:
                        skip_this_op = False

                    if skip_this_op:
                        try:
                            if hasattr(pocket_op, "Active"):
                                pocket_op.Active = False
                            if getattr(pocket_op, "ViewObject", None):
                                pocket_op.ViewObject.Visibility = False
                            try:
                                op_label = str(getattr(pocket_op, "Label", getattr(pocket_op, "Name", "PocketShape")) or "PocketShape")
                            except Exception:
                                op_label = "PocketShape"
                            print(f"Marked skipped larger-bit pass '{op_label}' as inactive (Active=False).")
                            doc.recompute()
                        except Exception:
                            pass
                        continue

                    try:
                        if getattr(pocket_op, "ViewObject", None):
                            pocket_op.ViewObject.Visibility = True
                    except Exception:
                        pass

                    created_ops.append(pocket_op)
                    kept_pass_count += 1

            if created_ops:
                try:
                    doc.recompute()
                except Exception:
                    pass

                for created_op in list(created_ops or []):
                    try:
                        _apply_toolpath_normal_color(created_op, getattr(created_op, "ToolController", None))
                    except Exception:
                        pass

                if QtCore is not None:
                    created_op_names = [str(getattr(op, "Name", "") or "") for op in created_ops if getattr(op, "Name", None)]

                    def _reapply_toolpath_colors_later():
                        try:
                            for op_name in created_op_names:
                                try:
                                    live_op = doc.getObject(op_name)
                                except Exception:
                                    live_op = None
                                if live_op is not None:
                                    _apply_toolpath_normal_color(live_op, getattr(live_op, "ToolController", None))
                        except Exception:
                            pass

                    try:
                        QtCore.QTimer.singleShot(0, _reapply_toolpath_colors_later)
                        QtCore.QTimer.singleShot(200, _reapply_toolpath_colors_later)
                    except Exception:
                        pass
                print(
                    f"Created {len(created_ops)} Pocket operation(s) across {len(face_levels)} level(s) and {len(face_names)} interior XY-plane face(s)."
                )
                if ops_with_cut_motion != len(created_ops):
                    print(f"Pocket passes with cutting moves: {ops_with_cut_motion}/{len(created_ops)}.")

                if smallest_bit_no_cut_label and len(created_ops) >= 2:
                    try:
                        print("Final smallest-bit Pocket pass still has no toolpath with current roughing/geometry.")
                    except Exception:
                        pass

                if smallest_bit_no_cut_label:
                    warning_title = "POCKET FAILURE: SMALLEST BIT GENERATED NO CUT MOVES"
                    warning_lines = [
                        "",
                        "============================================================",
                        "!!! CRITICAL POCKET WARNING !!!",
                        f"Smallest bit operation '{smallest_bit_no_cut_label}' generated NO toolpath.",
                        "Final cleanup pass failed to produce any cutting moves.",
                        "Check geometry selection, final depth, and bit/template compatibility.",
                        "============================================================",
                        "",
                    ]
                    warning_message = "\n".join(warning_lines)
                    try:
                        App.Console.PrintError(warning_message + "\n")
                    except Exception:
                        print(warning_message)
                    try:
                        if Gui and QtGui is not None:
                            QtGui.QMessageBox.critical(
                                None,
                                warning_title,
                                f"Smallest bit operation '{smallest_bit_no_cut_label}' generated NO toolpath.\n\n"
                                "Final cleanup pass failed.\n"
                                "Check geometry selection, final depth, and bit/template compatibility.",
                            )
                    except Exception:
                        pass
            else:
                print("Failed to create Pocket operations.")
            return created_ops
        except Exception as exc:
            print(f"Failed to create Pocket operation: {exc}")
            return []

    def _unwrap_candidate(obj):
        if not obj:
            return None
        if str(getattr(obj, "TypeId", "")) == "App::Link":
            linked = getattr(obj, "LinkedObject", None)
            if linked:
                return linked
        return obj

    def _is_valid_solid_candidate(obj):
        if not obj:
            return False
        try:
            shape = getattr(obj, "Shape", None)
        except Exception:
            return False
        if not shape:
            return False
        try:
            if shape.isNull():
                return False
        except Exception:
            return False
        try:
            return bool(getattr(shape, "Solids", None) and len(shape.Solids) > 0)
        except Exception:
            return False

    def _solid_candidates():
        candidates = []
        seen = set()

        def _add_candidate(raw_obj):
            candidate = _unwrap_candidate(raw_obj)
            if not candidate:
                return
            name = getattr(candidate, "Name", "")
            if name and name in seen:
                return
            if not _is_valid_solid_candidate(candidate):
                return
            try:
                if not PathJob.ObjectJob.isBaseCandidate(candidate):
                    return
            except Exception:
                return
            if name:
                seen.add(name)
            candidates.append(candidate)

        selected = Gui.Selection.getSelection() if Gui else []
        for obj in selected:
            _add_candidate(obj)

        for obj in getattr(doc, "Objects", []) or []:
            _add_candidate(obj)

        return candidates

    candidates = []
    target_obj = None

    scripted_template_path = ""
    if template_path:
        try:
            scripted_template_path = str(template_path).strip()
        except Exception:
            scripted_template_path = ""

    if target is not None:
        resolved_target = None
        if isinstance(target, str):
            resolved_target = doc.getObject(target)
        else:
            resolved_target = target

        if not resolved_target:
            print(f"Target solid not found: {target}")
            return
        if not _is_valid_solid_candidate(resolved_target):
            print(f"Target is not a valid solid: {getattr(resolved_target, 'Label', getattr(resolved_target, 'Name', target))}")
            return
        target_obj = resolved_target
        candidates = [target_obj]
    else:
        candidates = _solid_candidates()
        if not candidates:
            print("Select the inlay solid first, then run Pocket Job.")
            return
        target_obj = candidates[0]

    if not _warn_non_fillet_source(target_obj, "Pocket Job"):
        return

    effective_template_path = scripted_template_path
    pocket_prefs = None
    roughing_undersize_in = 0.02
    glue_oversize_in = 0.001
    pocket_final_depth_in = 0.200
    pocket_keep_tool_down = True
    pocket_min_travel = True
    pocket_skip_large_if_under_minutes = False
    pocket_skip_large_minutes_threshold = 3.0
    try:
        pocket_prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/ButlerCues/PocketJob")
        roughing_undersize_in = max(0.0, float(pocket_prefs.GetFloat("roughing_undersize_in", 0.02)))
        glue_oversize_in = max(0.0, float(pocket_prefs.GetFloat("glue_oversize_in", 0.001)))
        pocket_final_depth_in = max(0.0, float(pocket_prefs.GetFloat("final_depth_in", 0.200)))
        pocket_keep_tool_down = bool(pocket_prefs.GetBool("pocket_keep_tool_down", True))
        pocket_min_travel = bool(pocket_prefs.GetBool("pocket_min_travel", True))
        pocket_skip_large_if_under_minutes = bool(pocket_prefs.GetBool("skip_large_if_under_minutes", False))
        pocket_skip_large_minutes_threshold = max(
            0.0,
            float(pocket_prefs.GetFloat("skip_large_minutes_threshold", 3.0)),
        )
    except Exception:
        roughing_undersize_in = 0.02
        glue_oversize_in = 0.001
        pocket_final_depth_in = 0.200
        pocket_keep_tool_down = True
        pocket_min_travel = True
        pocket_skip_large_if_under_minutes = False
        pocket_skip_large_minutes_threshold = 3.0

    if pocket_final_depth_override is not None:
        try:
            pocket_final_depth_in = max(0.0, float(pocket_final_depth_override))
        except Exception:
            pocket_final_depth_in = 0.200

    if pocket_keep_tool_down_override is not None:
        try:
            pocket_keep_tool_down = bool(pocket_keep_tool_down_override)
        except Exception:
            pocket_keep_tool_down = True
    if pocket_min_travel_override is not None:
        try:
            pocket_min_travel = bool(pocket_min_travel_override)
        except Exception:
            pocket_min_travel = True
    if pocket_skip_large_if_under_minutes_override is not None:
        try:
            pocket_skip_large_if_under_minutes = bool(pocket_skip_large_if_under_minutes_override)
        except Exception:
            pocket_skip_large_if_under_minutes = False
    if pocket_skip_large_minutes_threshold_override is not None:
        try:
            pocket_skip_large_minutes_threshold = max(0.0, float(pocket_skip_large_minutes_threshold_override))
        except Exception:
            pocket_skip_large_minutes_threshold = 3.0

    def _template_files():
        files = []
        try:
            for path in PathPreferences.searchPaths():
                files.extend(glob.glob(os.path.join(path, "job_*.json")))
        except Exception:
            return []

        seen = set()
        out = []
        for f in files:
            norm = os.path.normpath(f)
            if norm in seen:
                continue
            seen.add(norm)
            out.append(norm)
        return out

    templates = _template_files()

    def _template_display_name(path):
        base = os.path.splitext(os.path.basename(path))[0]
        if base.lower().startswith("job_"):
            return base[4:]
        return base

    def _template_tool_controller_options(template_path):
        def _to_mm(value):
            try:
                return float(getattr(value, "Value", value))
            except Exception:
                pass
            try:
                return float(App.Units.Quantity(str(value)).Value)
            except Exception:
                pass
            try:
                return float(value)
            except Exception:
                return 0.0

        path = str(template_path or "").strip()
        if not path or not os.path.exists(path):
            return []

        try:
            with open(path, "rb") as fp:
                attrs = json.load(fp)
        except Exception:
            return []

        raw_tcs = attrs.get("ToolController") or []
        if not isinstance(raw_tcs, list):
            return []

        options = []
        for idx, tc in enumerate(raw_tcs, start=1):
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name", "") or "").strip() or f"TC{idx}"
            label = str(tc.get("label", "") or "").strip() or name

            tool_data = tc.get("tool", {}) if isinstance(tc.get("tool", {}), dict) else {}
            tool_label = str(
                tool_data.get("name", "")
                or tool_data.get("label", "")
                or tc.get("toolname", "")
                or tc.get("tool", "")
                or ""
            ).strip()
            diameter_mm = _to_mm(
                tool_data.get("diameter", tool_data.get("Diameter", tc.get("diameter", tc.get("Diameter", 0.0))))
            )

            display = label
            if diameter_mm > 0.0:
                display = f"{display} ({diameter_mm / 25.4:.3f} in)"
            elif tool_label:
                display = f"{display} ({tool_label})"

            options.append(
                {
                    "name": name,
                    "label": label,
                    "tool_label": tool_label,
                    "diameter_mm": diameter_mm,
                    "display": display,
                }
            )

        options.sort(
            key=lambda item: (
                -(float(item.get("diameter_mm", 0.0) or 0.0)),
                str(item.get("display", "")).lower(),
            )
        )
        return options

    if show_dialog and QtGui is not None and Gui is not None:
        default_template = ""
        try:
            default_template = PathPreferences.defaultJobTemplate() or ""
        except Exception:
            default_template = ""
        try:
            if pocket_prefs is not None:
                remembered_template = str(pocket_prefs.GetString("last_template_path", "") or "").strip()
                if remembered_template:
                    default_template = remembered_template
        except Exception:
            pass

        class _PocketJobTaskPanel:
            def __init__(self):
                self.form = QtGui.QWidget()
                self.form.setWindowTitle("Pocket Job")
                layout = QtGui.QFormLayout(self.form)
                self._last_run_signature = None

                self.solid_combo = QtGui.QComboBox()
                selected_index = 0
                for idx, obj in enumerate(candidates):
                    self.solid_combo.addItem(getattr(obj, "Label", obj.Name), obj.Name)
                    if target_obj is not None and obj.Name == getattr(target_obj, "Name", ""):
                        selected_index = idx
                if self.solid_combo.count() > 0:
                    self.solid_combo.setCurrentIndex(selected_index)

                self.template_combo = QtGui.QComboBox()
                self.template_combo.addItem("<none>", "")
                default_index = 0
                for tpath in sorted(templates, key=lambda p: _template_display_name(p).lower()):
                    idx = self.template_combo.count()
                    self.template_combo.addItem(_template_display_name(tpath), tpath)
                    if default_template and os.path.normpath(default_template) == os.path.normpath(tpath):
                        default_index = idx
                if self.template_combo.count() > 0:
                    self.template_combo.setCurrentIndex(default_index)

                self.template_edit = QtGui.QLineEdit("")
                browse_btn = QtGui.QPushButton("Browse…")

                def _browse_template():
                    try:
                        path, _ = QtGui.QFileDialog.getOpenFileName(
                            self.form,
                            "Select CAM Job Template",
                            "",
                            "Job Templates (*.json);;All Files (*)",
                        )
                        if path:
                            self.template_edit.setText(path)
                    except Exception:
                        pass

                browse_btn.clicked.connect(_browse_template)

                def _on_template_change(index):
                    data = self.template_combo.itemData(index)
                    self.template_edit.setText(str(data or ""))

                self.template_combo.currentIndexChanged.connect(_on_template_change)
                _on_template_change(self.template_combo.currentIndex())

                row = QtGui.QHBoxLayout()
                row.addWidget(self.template_edit)
                row.addWidget(browse_btn)
                template_widget = QtGui.QWidget()
                template_widget.setLayout(row)

                self.roughing_undersize_spin = QtGui.QDoubleSpinBox()
                self.roughing_undersize_spin.setDecimals(4)
                self.roughing_undersize_spin.setRange(0.0, 1.0)
                self.roughing_undersize_spin.setSingleStep(0.001)
                self.roughing_undersize_spin.setValue(float(roughing_undersize_in))

                self.glue_oversize_spin = QtGui.QDoubleSpinBox()
                self.glue_oversize_spin.setDecimals(4)
                self.glue_oversize_spin.setRange(0.0, 1.0)
                self.glue_oversize_spin.setSingleStep(0.0005)
                self.glue_oversize_spin.setValue(float(glue_oversize_in))

                self.final_depth_spin = QtGui.QDoubleSpinBox()
                self.final_depth_spin.setDecimals(4)
                self.final_depth_spin.setRange(0.0, 2.0)
                self.final_depth_spin.setSingleStep(0.005)
                self.final_depth_spin.setValue(float(pocket_final_depth_in))

                self.skip_large_bit_check = QtGui.QCheckBox("Skip larger bit when runtime is short")
                self.skip_large_bit_check.setChecked(bool(pocket_skip_large_if_under_minutes))
                self.skip_large_minutes_spin = QtGui.QDoubleSpinBox()
                self.skip_large_minutes_spin.setDecimals(2)
                self.skip_large_minutes_spin.setRange(0.0, 120.0)
                self.skip_large_minutes_spin.setSingleStep(0.25)
                self.skip_large_minutes_spin.setValue(float(pocket_skip_large_minutes_threshold))
                self.skip_large_minutes_spin.setEnabled(bool(self.skip_large_bit_check.isChecked()))
                try:
                    self.skip_large_bit_check.toggled.connect(self.skip_large_minutes_spin.setEnabled)
                except Exception:
                    pass

                self.keep_tool_down_check = QtGui.QCheckBox("Keep tool down")
                self.keep_tool_down_check.setChecked(bool(pocket_keep_tool_down))
                self.min_travel_check = QtGui.QCheckBox("Min travel")
                self.min_travel_check.setChecked(bool(pocket_min_travel))

                self.tool_checkboxes = []
                bit_widget = QtGui.QWidget()
                bit_layout = QtGui.QVBoxLayout(bit_widget)
                bit_layout.setContentsMargins(0, 0, 0, 0)
                self._bit_layout = bit_layout
                persisted_names = []
                try:
                    if pocket_prefs is not None:
                        raw = str(pocket_prefs.GetString("selected_tool_names", "") or "").strip()
                        if raw:
                            loaded = json.loads(raw)
                            if isinstance(loaded, list):
                                persisted_names = [str(item) for item in loaded if str(item).strip()]
                except Exception:
                    persisted_names = []
                self._fallback_bit_selections = list(persisted_names)
                self._selected_bits_by_template = {}
                try:
                    if pocket_prefs is not None:
                        raw_map = str(pocket_prefs.GetString("selected_bits_by_template", "") or "").strip()
                        if raw_map:
                            loaded_map = json.loads(raw_map)
                            if isinstance(loaded_map, dict):
                                self._selected_bits_by_template = loaded_map
                except Exception:
                    self._selected_bits_by_template = {}

                def _template_key(path_text):
                    try:
                        text = str(path_text or "").strip()
                        if not text:
                            return ""
                        return os.path.normpath(text)
                    except Exception:
                        return str(path_text or "")

                def _persisted_for_template(path_text):
                    key = _template_key(path_text)
                    if key:
                        try:
                            remembered = self._selected_bits_by_template.get(key, [])
                            if isinstance(remembered, list):
                                return list(remembered)
                        except Exception:
                            pass
                    return list(self._fallback_bit_selections)

                def _clear_bits_ui():
                    try:
                        while self._bit_layout.count() > 0:
                            item = self._bit_layout.takeAt(0)
                            widget = item.widget() if item else None
                            if widget:
                                widget.deleteLater()
                    except Exception:
                        pass
                    self.tool_checkboxes = []

                def _refresh_bits_from_template():
                    _clear_bits_ui()
                    template_path = str(self.template_edit.text() or "").strip()
                    tool_options = _template_tool_controller_options(template_path)
                    if not tool_options:
                        self._bit_layout.addWidget(QtGui.QLabel("No bits found in selected CAM template."))
                        return

                    self._persisted_bit_selections = _persisted_for_template(template_path)

                    def _is_persisted(option):
                        name = str(option.get("name", "") or "").strip().lower()
                        label = str(option.get("label", "") or "").strip().lower()
                        tool_label = str(option.get("tool_label", "") or "").strip().lower()
                        for item in (self._persisted_bit_selections or []):
                            if isinstance(item, dict):
                                i_name = str(item.get("name", "") or "").strip().lower()
                                i_label = str(item.get("label", "") or "").strip().lower()
                                i_tool = str(item.get("tool_label", "") or "").strip().lower()
                                if (name and name == i_name) or (label and label == i_label) or (tool_label and tool_label == i_tool):
                                    return True
                            else:
                                text = str(item or "").strip().lower()
                                if text and (text == name or text == label or text == tool_label):
                                    return True
                        return False

                    for option in tool_options:
                        tc_name = str(option.get("name", "") or "")
                        tc_label = str(option.get("label", "") or "")
                        cb = QtGui.QCheckBox(str(option.get("display", tc_label or tc_name) or tc_name))
                        checked = _is_persisted(option)
                        cb.setChecked(bool(checked))
                        try:
                            cb.toggled.connect(self._mark_dirty)
                        except Exception:
                            pass
                        self._bit_layout.addWidget(cb)
                        self.tool_checkboxes.append((option, cb))

                self._refresh_bits_from_template = _refresh_bits_from_template

                layout.addRow("Solid", self.solid_combo)
                layout.addRow("CAM template", self.template_combo)
                layout.addRow("Template (optional)", template_widget)
                layout.addRow("Roughing undersize (in)", self.roughing_undersize_spin)
                layout.addRow("Glue tolerance oversize (in)", self.glue_oversize_spin)
                layout.addRow("Final depth (in)", self.final_depth_spin)
                layout.addRow("Skip larger bit", self.skip_large_bit_check)
                layout.addRow("Skip if runtime under (min)", self.skip_large_minutes_spin)
                layout.addRow("Keep Tool Down", self.keep_tool_down_check)
                layout.addRow("Min Travel", self.min_travel_check)
                layout.addRow("Bits", bit_widget)

                self._refresh_bits_from_template()

                try:
                    self.solid_combo.currentIndexChanged.connect(self._mark_dirty)
                    self.template_combo.currentIndexChanged.connect(self._mark_dirty)
                    self.template_edit.textChanged.connect(self._mark_dirty)
                    self.roughing_undersize_spin.valueChanged.connect(self._mark_dirty)
                    self.glue_oversize_spin.valueChanged.connect(self._mark_dirty)
                    self.final_depth_spin.valueChanged.connect(self._mark_dirty)
                    self.skip_large_bit_check.toggled.connect(self._mark_dirty)
                    self.skip_large_minutes_spin.valueChanged.connect(self._mark_dirty)
                    self.keep_tool_down_check.toggled.connect(self._mark_dirty)
                    self.min_travel_check.toggled.connect(self._mark_dirty)
                except Exception:
                    pass

                try:
                    self.template_combo.currentIndexChanged.connect(lambda *_: self._refresh_bits_from_template())
                    self.template_edit.textChanged.connect(lambda *_: self._refresh_bits_from_template())
                except Exception:
                    pass

            def _mark_dirty(self, *args):
                self._last_run_signature = None

            def _current_signature(self):
                try:
                    solid_name = str(self.solid_combo.currentData() or "").strip()
                except Exception:
                    solid_name = ""
                try:
                    template_path = str(self.template_edit.text() or "").strip()
                except Exception:
                    template_path = ""
                try:
                    roughing = round(float(self.roughing_undersize_spin.value()), 6)
                except Exception:
                    roughing = 0.02
                try:
                    glue = round(float(self.glue_oversize_spin.value()), 6)
                except Exception:
                    glue = 0.001
                try:
                    final_depth = round(float(self.final_depth_spin.value()), 6)
                except Exception:
                    final_depth = 0.200
                try:
                    skip_large_bit = bool(self.skip_large_bit_check.isChecked())
                except Exception:
                    skip_large_bit = False
                try:
                    skip_large_minutes = round(float(self.skip_large_minutes_spin.value()), 6)
                except Exception:
                    skip_large_minutes = 3.0
                try:
                    keep_tool_down = bool(self.keep_tool_down_check.isChecked())
                except Exception:
                    keep_tool_down = True
                try:
                    min_travel = bool(self.min_travel_check.isChecked())
                except Exception:
                    min_travel = True
                try:
                    selected_bits = tuple(
                        sorted(
                            [
                                str(option.get("name", "") or "")
                                for option, cb in self.tool_checkboxes
                                if cb.isChecked() and str(option.get("name", "") or "")
                            ]
                        )
                    )
                except Exception:
                    selected_bits = tuple()
                return (solid_name, template_path, roughing, glue, final_depth, skip_large_bit, skip_large_minutes, keep_tool_down, min_travel, selected_bits)

            def _run_creation(self, close_after=True):
                current_signature = self._current_signature()
                if close_after and self._last_run_signature == current_signature:
                    try:
                        Gui.Control.closeDialog()
                    except Exception:
                        pass
                    return True

                selected_name = self.solid_combo.currentData()
                selected_obj = doc.getObject(selected_name) if selected_name else None
                if not selected_obj:
                    print("Pocket Job creation canceled: no valid solid selected.")
                    return False

                selected_template = self.template_edit.text().strip()
                try:
                    selected_roughing = max(0.0, float(self.roughing_undersize_spin.value()))
                except Exception:
                    selected_roughing = 0.02
                try:
                    selected_glue = max(0.0, float(self.glue_oversize_spin.value()))
                except Exception:
                    selected_glue = 0.001
                try:
                    selected_final_depth = max(0.0, float(self.final_depth_spin.value()))
                except Exception:
                    selected_final_depth = 0.200
                selected_skip_large_bit = bool(self.skip_large_bit_check.isChecked())
                try:
                    selected_skip_large_minutes = max(0.0, float(self.skip_large_minutes_spin.value()))
                except Exception:
                    selected_skip_large_minutes = 3.0
                selected_keep_tool_down = bool(self.keep_tool_down_check.isChecked())
                selected_min_travel = bool(self.min_travel_check.isChecked())

                try:
                    if pocket_prefs is not None:
                        pocket_prefs.SetFloat("roughing_undersize_in", float(selected_roughing))
                        pocket_prefs.SetFloat("glue_oversize_in", float(selected_glue))
                        pocket_prefs.SetFloat("final_depth_in", float(selected_final_depth))
                        pocket_prefs.SetBool("skip_large_if_under_minutes", bool(selected_skip_large_bit))
                        pocket_prefs.SetFloat("skip_large_minutes_threshold", float(selected_skip_large_minutes))
                        pocket_prefs.SetString("last_template_path", str(selected_template or ""))
                        pocket_prefs.SetBool("pocket_keep_tool_down", bool(selected_keep_tool_down))
                        pocket_prefs.SetBool("pocket_min_travel", bool(selected_min_travel))
                except Exception:
                    pass

                selected_tool_names = None
                try:
                    selected_tool_names = [
                        {
                            "name": str(option.get("name", "") or "").strip(),
                            "label": str(option.get("label", "") or "").strip(),
                            "tool_label": str(option.get("tool_label", "") or "").strip(),
                            "diameter_mm": float(option.get("diameter_mm", 0.0) or 0.0),
                        }
                        for option, cb in self.tool_checkboxes
                        if cb.isChecked()
                    ]
                except Exception:
                    selected_tool_names = None
                try:
                    if pocket_prefs is not None and isinstance(selected_tool_names, list):
                        pocket_prefs.SetString("selected_tool_names", json.dumps(selected_tool_names))
                        selected_template_key = ""
                        try:
                            selected_template_key = os.path.normpath(str(selected_template or "").strip()) if str(selected_template or "").strip() else ""
                        except Exception:
                            selected_template_key = str(selected_template or "")
                        if selected_template_key:
                            self._selected_bits_by_template[selected_template_key] = list(selected_tool_names)
                            pocket_prefs.SetString(
                                "selected_bits_by_template",
                                json.dumps(self._selected_bits_by_template),
                            )
                except Exception:
                    pass

                try:
                    create_pocket_cnc_job(
                        target=selected_obj,
                        template_path=selected_template or None,
                        show_dialog=False,
                        selected_tool_names_override=selected_tool_names,
                        pocket_final_depth_override=selected_final_depth,
                        pocket_keep_tool_down_override=selected_keep_tool_down,
                        pocket_min_travel_override=selected_min_travel,
                        pocket_skip_large_if_under_minutes_override=selected_skip_large_bit,
                        pocket_skip_large_minutes_threshold_override=selected_skip_large_minutes,
                    )
                    self._last_run_signature = current_signature
                finally:
                    if close_after:
                        try:
                            Gui.Control.closeDialog()
                        except Exception:
                            pass
                return True

            def getStandardButtons(self):
                return QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Apply | QtGui.QDialogButtonBox.Cancel

            def clicked(self, button):
                if button == QtGui.QDialogButtonBox.Apply:
                    self._run_creation(close_after=False)
                    return True
                return False

            def accept(self):
                return self._run_creation(close_after=True)

            def reject(self):
                print("Pocket Job creation canceled.")
                try:
                    Gui.Control.closeDialog()
                except Exception:
                    pass
                return True

        Gui.Control.showDialog(_PocketJobTaskPanel())
        return

    if effective_template_path and not os.path.exists(effective_template_path):
        print(f"Template not found: {effective_template_path}")
        return

    def _as_mm(value):
        try:
            return float(getattr(value, "Value", value))
        except Exception:
            return 0.0

    def _job_tool_controllers_sorted_largest_first(job_obj):
        try:
            tools_group = list(getattr(getattr(job_obj, "Tools", None), "Group", None) or [])
        except Exception:
            tools_group = []

        with_diameter = []
        without_diameter = []
        for tc in tools_group:
            try:
                tool = getattr(tc, "Tool", None)
                dmm = _as_mm(getattr(tool, "Diameter", 0.0))
            except Exception:
                dmm = 0.0
            if dmm > 0:
                with_diameter.append((dmm, tc))
            else:
                without_diameter.append(tc)

        with_diameter.sort(key=lambda item: item[0], reverse=True)
        ordered = [tc for _, tc in with_diameter]
        ordered.extend(without_diameter)
        return ordered

    def _selected_tool_labels_for_job(job_obj, selected_names):
        selected_set = {str(name) for name in (selected_names or []) if str(name).strip()}
        labels = []
        try:
            for tc in _job_tool_controllers_sorted_largest_first(job_obj):
                tc_name = str(getattr(tc, "Name", "") or "")
                if tc_name and tc_name in selected_set:
                    tc_label = str(getattr(tc, "Label", "") or "").strip()
                    labels.append(tc_label or tc_name)
        except Exception:
            pass
        return labels

    def _select_tool_names_for_pocket(job_obj, selected_override=None):
        ordered_tcs = _job_tool_controllers_sorted_largest_first(job_obj)
        if not ordered_tcs:
            return []

        def _txt(value):
            return str(value or "").strip().lower()

        def _extract_inch_sizes(text):
            values = []
            s = str(text or "")
            try:
                import re

                for m in re.findall(r"(\d+(?:\.\d+)?)\s*in\b", s, flags=re.IGNORECASE):
                    try:
                        values.append(float(m))
                    except Exception:
                        pass

                for m in re.findall(r"(?<!\d)0?(\d{3})(?!\d)", s):
                    try:
                        values.append(float(f"0.{m}"))
                    except Exception:
                        pass
            except Exception:
                pass
            return values

        tc_specs = []
        for tc in ordered_tcs:
            try:
                tc_name = str(getattr(tc, "Name", "") or "").strip()
            except Exception:
                tc_name = ""
            if not tc_name:
                continue
            try:
                tc_label = str(getattr(tc, "Label", "") or "").strip()
            except Exception:
                tc_label = ""
            try:
                tool_obj = getattr(tc, "Tool", None)
                tool_label = str(getattr(tool_obj, "Label", "") or getattr(tool_obj, "Name", "") or "").strip()
            except Exception:
                tool_label = ""
            try:
                tool_number = int(getattr(tc, "ToolNumber", -1))
                if tool_number < 0:
                    tool_number = None
            except Exception:
                tool_number = None
            try:
                diameter_mm = float(_as_mm(getattr(getattr(tc, "Tool", None), "Diameter", 0.0)))
            except Exception:
                diameter_mm = 0.0
            tc_specs.append(
                {
                    "name": tc_name,
                    "label": tc_label,
                    "tool_label": tool_label,
                    "diameter_mm": diameter_mm,
                    "tool_number": tool_number,
                }
            )

        def _match_selected_names(raw_items):
            selected_names = []
            used = set()
            for item in list(raw_items or []):
                item_name = ""
                item_label = ""
                item_tool_label = ""
                item_diameter_mm = None
                item_tool_number = None

                if isinstance(item, dict):
                    item_name = str(item.get("name", "") or "").strip()
                    item_label = str(item.get("label", "") or "").strip()
                    item_tool_label = str(item.get("tool_label", "") or "").strip()
                    try:
                        raw_tool_number = item.get("tool_number", None)
                        item_tool_number = int(raw_tool_number) if raw_tool_number is not None else None
                    except Exception:
                        item_tool_number = None
                    try:
                        raw_diameter = item.get("diameter_mm", None)
                        item_diameter_mm = float(raw_diameter) if raw_diameter is not None else None
                    except Exception:
                        item_diameter_mm = None
                else:
                    item_name = str(item or "").strip()

                matched = None
                if item_tool_number is not None:
                    for spec in tc_specs:
                        try:
                            if int(spec.get("tool_number")) == int(item_tool_number):
                                matched = spec
                                break
                        except Exception:
                            pass

                for spec in tc_specs:
                    if matched is None and item_name and _txt(spec.get("name")) == _txt(item_name):
                        matched = spec
                        break

                if matched is None:
                    for spec in tc_specs:
                        if item_label and _txt(spec.get("label")) == _txt(item_label):
                            matched = spec
                            break

                if matched is None:
                    for spec in tc_specs:
                        if item_tool_label and _txt(spec.get("tool_label")) == _txt(item_tool_label):
                            matched = spec
                            break

                if matched is None and item_diameter_mm is not None:
                    for spec in tc_specs:
                        try:
                            if abs(float(spec.get("diameter_mm", 0.0)) - float(item_diameter_mm)) <= 1e-3:
                                matched = spec
                                break
                        except Exception:
                            pass

                if matched is None:
                    item_size_candidates = []
                    for text_part in (item_name, item_label, item_tool_label):
                        item_size_candidates.extend(_extract_inch_sizes(text_part))
                    if item_size_candidates:
                        try:
                            target_in = min(item_size_candidates)
                        except Exception:
                            target_in = None
                        if target_in is not None:
                            for spec in tc_specs:
                                try:
                                    spec_in = float(spec.get("diameter_mm", 0.0)) / 25.4
                                    if spec_in > 0 and abs(spec_in - target_in) <= 0.0025:
                                        matched = spec
                                        break
                                except Exception:
                                    pass

                if matched is not None:
                    name = str(matched.get("name", "") or "")
                    if name and name not in used:
                        used.add(name)
                        selected_names.append(name)

            return selected_names

        persisted_names = []
        try:
            if pocket_prefs is not None:
                raw = str(pocket_prefs.GetString("selected_tool_names", "") or "").strip()
                if raw:
                    loaded = json.loads(raw)
                    if isinstance(loaded, list):
                        persisted_names = list(loaded)
        except Exception:
            persisted_names = []

        if selected_override is not None:
            chosen = _match_selected_names(selected_override or [])
            if chosen:
                try:
                    if pocket_prefs is not None:
                        pocket_prefs.SetString("selected_tool_names", json.dumps(chosen))
                except Exception:
                    pass
                return chosen

            print("Selected pocket bits did not match loaded job tool controllers; no fallback tools will be auto-selected.")
            return []

        default_names = _match_selected_names(persisted_names)
        if not default_names:
            default_names = [str(getattr(tc, "Name", "")) for tc in ordered_tcs[:2] if getattr(tc, "Name", "")]
        if not default_names:
            default_names = [str(getattr(ordered_tcs[0], "Name", ""))]

        if not show_dialog or QtGui is None:
            selected_names = [name for name in default_names if name]
            try:
                if pocket_prefs is not None:
                    pocket_prefs.SetString("selected_tool_names", json.dumps(selected_names))
            except Exception:
                pass
            return selected_names

        dialog = QtGui.QDialog()
        dialog.setWindowTitle("Pocket Bits")
        layout = QtGui.QVBoxLayout(dialog)
        layout.addWidget(QtGui.QLabel("Select bits to use (largest to smallest):"))

        checkbox_rows = []
        for tc in ordered_tcs:
            tc_name = str(getattr(tc, "Name", ""))
            tc_label = str(getattr(tc, "Label", tc_name or "ToolController"))
            try:
                dmm = _as_mm(getattr(getattr(tc, "Tool", None), "Diameter", 0.0))
            except Exception:
                dmm = 0.0

            if dmm > 0:
                din = dmm / 25.4
                text = f"{tc_label} ({din:.3f} in)"
            else:
                text = tc_label

            cb = QtGui.QCheckBox(text)
            cb.setChecked(tc_name in default_names)
            layout.addWidget(cb)
            checkbox_rows.append((tc_name, cb))

        buttons = QtGui.QDialogButtonBox(QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() != QtGui.QDialog.Accepted:
            print("Bit selection canceled; using default largest bit(s).")
            selected_names = [name for name in default_names if name]
            try:
                if pocket_prefs is not None:
                    pocket_prefs.SetString("selected_tool_names", json.dumps(selected_names))
            except Exception:
                pass
            return selected_names

        selected = [name for name, cb in checkbox_rows if cb.isChecked() and name]
        if selected:
            try:
                if pocket_prefs is not None:
                    pocket_prefs.SetString("selected_tool_names", json.dumps(selected))
            except Exception:
                pass
            return selected

        print("No bits selected; using largest bit.")
        fallback = [default_names[0]] if default_names else []
        try:
            if pocket_prefs is not None:
                pocket_prefs.SetString("selected_tool_names", json.dumps(fallback))
        except Exception:
            pass
        return fallback

    pocket_job_model, created_items = _create_pocket_job_model_from_inlay(target_obj)
    if not pocket_job_model:
        return

    job_models = [pocket_job_model]

    def _set_pocket_job_label(job_obj):
        if not job_obj:
            return
        try:
            base_label = str(getattr(target_obj, "Label", getattr(target_obj, "Name", "Pocket")) or "Pocket").strip()
            if not base_label:
                base_label = "Pocket"
            job_obj.Label = f"{base_label} Pocket Job"
        except Exception:
            pass

    try:
        active_view = None
        previous_body = None
        previous_part = None
        if Gui and getattr(Gui, "ActiveDocument", None):
            try:
                active_view = Gui.ActiveDocument.ActiveView
                previous_body = active_view.getActiveObject("pdbody")
            except Exception:
                previous_body = None
            try:
                previous_part = active_view.getActiveObject("part")
            except Exception:
                previous_part = None
            try:
                active_view.setActiveObject("pdbody", None)
            except Exception:
                pass
            try:
                active_view.setActiveObject("part", None)
            except Exception:
                pass

        try:
            job = PathGuiJob.Create(job_models, effective_template_path or None, openTaskPanel=False)
            job_name = getattr(job, "Name", None)
            _apply_butler_post_defaults(job)
            _set_pocket_job_label(job)
        finally:
            if active_view is not None:
                try:
                    active_view.setActiveObject("pdbody", previous_body)
                except Exception:
                    pass
                try:
                    active_view.setActiveObject("part", previous_part)
                except Exception:
                    pass

        _set_stock_to_model_bounds(job)
        _sanitize_job_stock(job, pocket_job_model)
        if selected_tool_names_override is not None:
            requested_tool_items = list(selected_tool_names_override or [])
            if not requested_tool_items:
                print("Pocket Job aborted: no bits were checked.")
                return
            try:
                selected_labels = []
                for item in requested_tool_items:
                    if isinstance(item, dict):
                        lbl = str(item.get("label", "") or item.get("name", "") or item.get("tool_label", "") or "").strip()
                    else:
                        lbl = str(item or "").strip()
                    if lbl:
                        selected_labels.append(lbl)
                if selected_labels:
                    print(f"Pocket bits requested (checked): {', '.join(selected_labels)}")
            except Exception:
                pass

            selected_tool_names = _select_tool_names_for_pocket(job, requested_tool_items)
            if not selected_tool_names:
                print("Pocket Job aborted: checked bits could not be resolved to created job tool controllers.")
                return
        else:
            selected_tool_names = _select_tool_names_for_pocket(job, None)
            if selected_tool_names:
                try:
                    print(f"Pocket tool controllers selected: {', '.join(str(name) for name in selected_tool_names)}")
                except Exception:
                    pass

        model_for_ops = pocket_job_model
        created_pocket_ops = _create_pocket_op_non_top_faces(
            job,
            model_for_ops,
            roughing_undersize_in,
            glue_oversize_in,
            pocket_final_depth_in,
            selected_tool_names,
            keep_tool_down=bool(pocket_keep_tool_down),
            min_travel=bool(pocket_min_travel),
            skip_large_if_under_minutes=bool(pocket_skip_large_if_under_minutes),
            skip_large_minutes_threshold=float(pocket_skip_large_minutes_threshold),
        )
        job_live = doc.getObject(job_name) if job_name else job
        _set_pocket_job_label(job_live)
        if not job_live:
            try:
                if created_pocket_ops:
                    import PathScripts.PathUtils as PathUtils

                    parent_job = PathUtils.findParentJob(created_pocket_ops[-1])
                    if parent_job:
                        job_live = parent_job
                        job_name = getattr(parent_job, "Name", job_name)
            except Exception:
                pass
        if not job_live:
            try:
                all_jobs = [
                    obj
                    for obj in (getattr(doc, "Objects", []) or [])
                    if hasattr(obj, "Operations") and hasattr(obj, "Model") and hasattr(obj, "Stock")
                ]
                if all_jobs:
                    job_live = all_jobs[-1]
                    job_name = getattr(job_live, "Name", job_name)
                    _set_pocket_job_label(job_live)
            except Exception:
                job_live = None
        if not job_live:
            print("Pocket Job reference unavailable after operation creation; recreating job.")
            try:
                recovered_job = PathGuiJob.Create(job_models, effective_template_path or None, openTaskPanel=False)
                if recovered_job:
                    job_live = recovered_job
                    job_name = getattr(recovered_job, "Name", job_name)
                    _apply_butler_post_defaults(job_live)
                    _set_pocket_job_label(job_live)
                    _set_stock_to_model_bounds(job_live)
                    _sanitize_job_stock(job_live, pocket_job_model)
                    recovered_model = pocket_job_model
                    try:
                        recovered_group = getattr(getattr(job_live, "Model", None), "Group", None) or []
                        if recovered_group:
                            recovered_model = recovered_group[0]
                    except Exception:
                        pass
                    _create_pocket_op_non_top_faces(
                        job_live,
                        recovered_model,
                        roughing_undersize_in,
                        glue_oversize_in,
                        pocket_final_depth_in,
                        selected_tool_names,
                        keep_tool_down=bool(pocket_keep_tool_down),
                        min_travel=bool(pocket_min_travel),
                        skip_large_if_under_minutes=bool(pocket_skip_large_if_under_minutes),
                        skip_large_minutes_threshold=float(pocket_skip_large_minutes_threshold),
                    )
                    doc.recompute()
            except Exception as rec_exc:
                print(f"Failed to recreate Pocket Job: {rec_exc}")
                job_live = None

        if not job_live:
            print("Pocket Job unavailable; continuing without job container link.")
            selected_tool_labels = _selected_tool_labels_for_job(job, selected_tool_names)
            pocket_settings = {
                "TemplatePath": str(effective_template_path or ""),
                "SelectedTools": ", ".join(str(label) for label in (selected_tool_labels or [])),
                "RoughingUndersizeIn": float(roughing_undersize_in),
                "GlueOversizeIn": float(glue_oversize_in),
                "FinalDepthIn": float(pocket_final_depth_in),
                "SkipLargeBitIfUnderMin": bool(pocket_skip_large_if_under_minutes),
                "SkipLargeBitMinMinutes": float(pocket_skip_large_minutes_threshold),
                "KeepToolDown": bool(pocket_keep_tool_down),
                "MinTravel": bool(pocket_min_travel),
            }
            auto_container = _create_auto_container(
                target_obj,
                created_items,
                glue_oversize_in,
                settings=pocket_settings,
            )
            _expand_container_in_tree(auto_container)
            try:
                if target_obj and getattr(target_obj, "ViewObject", None):
                    target_obj.ViewObject.Visibility = False
            except Exception:
                pass
            doc.recompute()
            _record_undo_cleanup_bundle()
            return

        selected_tool_labels = _selected_tool_labels_for_job(job_live, selected_tool_names)
        pocket_settings = {
            "TemplatePath": str(effective_template_path or ""),
            "SelectedTools": ", ".join(str(label) for label in (selected_tool_labels or [])),
            "RoughingUndersizeIn": float(roughing_undersize_in),
            "GlueOversizeIn": float(glue_oversize_in),
            "FinalDepthIn": float(pocket_final_depth_in),
            "SkipLargeBitIfUnderMin": bool(pocket_skip_large_if_under_minutes),
            "SkipLargeBitMinMinutes": float(pocket_skip_large_minutes_threshold),
            "KeepToolDown": bool(pocket_keep_tool_down),
            "MinTravel": bool(pocket_min_travel),
        }
        auto_container = _create_auto_container(
            target_obj,
            created_items + [job_live],
            glue_oversize_in,
            settings=pocket_settings,
        )
        _expand_container_in_tree(auto_container)
        _expand_targets_in_tree([job_live] + list(created_pocket_ops or []))
        try:
            if target_obj and getattr(target_obj, "ViewObject", None):
                target_obj.ViewObject.Visibility = False
        except Exception:
            pass
        doc.recompute()
    except Exception as exc:
        print(f"Failed to create Pocket Job: {exc}")
        try:
            import traceback
            print(traceback.format_exc())
        except Exception:
            pass
        return

    try:
        if Gui:
            Gui.Selection.clearSelection()
            if job_name and doc.getObject(job_name):
                Gui.Selection.addSelection(doc.getObject(job_name))
    except Exception:
        pass

    job_live = doc.getObject(job_name) if job_name else None
    job_label = getattr(job_live, "Label", job_name or "Job")

    if effective_template_path:
        print(f"Created Pocket Job '{job_label}' using template '{effective_template_path}'.")
    else:
        print(f"Created Pocket Job '{job_label}' with default CAM setup.")

    try:
        if Gui:
            Gui.SendMsgToActiveView("ViewFit")
    except Exception:
        pass

    _record_undo_cleanup_bundle()

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

    