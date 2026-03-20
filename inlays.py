
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


_butler_container_cleanup_observer = None
_butler_managed_container_children = {}
_butler_enable_undo_cleanup_observer = True
_butler_enable_deleted_object_cleanup = False
_butler_undo_cleanup_bundles = {}


class _ButlerContainerCleanupObserver:
    def __init__(self):
        self._in_cleanup = False

    def slotDeletedObject(self, obj):
        if not _butler_enable_deleted_object_cleanup:
            return
        if self._in_cleanup:
            return
        try:
            if not obj:
                return

            container_name = str(getattr(obj, "Name", "") or "").strip()
            if not container_name:
                return

            doc = getattr(obj, "Document", None) or App.ActiveDocument
            doc_name = str(getattr(doc, "Name", "") or "").strip()
            if not doc_name:
                return

            direct_key = f"{doc_name}:{container_name}"
            payload = _butler_managed_container_children.get(direct_key)
            _butler_managed_container_children.pop(direct_key, None)

            child_names = []
            if isinstance(payload, dict):
                child_names = list(payload.get("children", []) or [])
            elif isinstance(payload, list):
                child_names = list(payload or [])

            if not child_names:
                return

            self._in_cleanup = True
            for child_name in reversed(child_names):
                try:
                    child = doc.getObject(str(child_name))
                except Exception:
                    child = None
                if not child:
                    continue
                try:
                    doc.removeObject(child.Name)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._in_cleanup = False

    def slotUndoDocument(self, doc):
        if not _butler_enable_undo_cleanup_observer:
            return
        if self._in_cleanup:
            return
        try:
            if not doc:
                return

            doc_name = str(getattr(doc, "Name", "") or "").strip()
            if not doc_name:
                return

            bundles = _butler_undo_cleanup_bundles.get(doc_name)
            if not bundles:
                return

            bundle = None
            while bundles and not bundle:
                candidate = bundles.pop()
                if candidate:
                    bundle = list(candidate)
            if not bundles:
                try:
                    _butler_undo_cleanup_bundles.pop(doc_name, None)
                except Exception:
                    pass
            if not bundle:
                return

            self._in_cleanup = True
            for obj_name in reversed(bundle):
                try:
                    existing = doc.getObject(str(obj_name))
                except Exception:
                    existing = None
                if not existing:
                    continue
                try:
                    doc.removeObject(existing.Name)
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            self._in_cleanup = False

    def slotDeletedDocument(self, doc):
        try:
            doc_name = str(getattr(doc, "Name", "") or "").strip()
            if doc_name:
                _butler_undo_cleanup_bundles.pop(doc_name, None)
        except Exception:
            pass


def _ensure_butler_container_cleanup_observer():
    global _butler_container_cleanup_observer
    if not _butler_enable_undo_cleanup_observer:
        return
    if _butler_container_cleanup_observer is not None:
        return
    try:
        _butler_container_cleanup_observer = _ButlerContainerCleanupObserver()
        App.addDocumentObserver(_butler_container_cleanup_observer)
    except Exception:
        _butler_container_cleanup_observer = None


def _disable_butler_container_cleanup_observer():
    global _butler_container_cleanup_observer
    try:
        if _butler_container_cleanup_observer is not None:
            App.removeDocumentObserver(_butler_container_cleanup_observer)
    except Exception:
        pass
    _butler_container_cleanup_observer = None


class _PatternGroupViewProvider:
    def __init__(self, obj=None):
        if obj is not None:
            self.attach(obj.ViewObject)

    def attach(self, vobj):
        self.Object = vobj.Object
        vobj.Proxy = self

    def doubleClicked(self, vobj):
        return False

    def setEdit(self, vobj, mode=0):
        return False

    def unsetEdit(self, vobj, mode=0):
        return True

    def __getstate__(self):
        return None

    def __setstate__(self, state):
        return None


def _is_pattern_group_obj(obj):
    if not obj:
        return False
    if getattr(obj, "TypeId", "") != "App::DocumentObjectGroup":
        return False
    name = getattr(obj, "Name", "")
    label = getattr(obj, "Label", "")
    if name.startswith("InlayPattern_") or name.startswith("InlayCNCPlan_"):
        return True
    return "Inlay Pattern" in label or "Inlay CNC Plan" in label


def _attach_pattern_group_view_provider(group_obj):
    if not Gui or not group_obj or not hasattr(group_obj, "ViewObject") or group_obj.ViewObject is None:
        return
    try:
        if isinstance(getattr(group_obj.ViewObject, "Proxy", None), _PatternGroupViewProvider):
            return
        _PatternGroupViewProvider(group_obj)
    except Exception:
        pass


def _attach_pattern_group_view_providers(doc):
    if not doc:
        return
    for obj in getattr(doc, "Objects", []) or []:
        if _is_pattern_group_obj(obj):
            _attach_pattern_group_view_provider(obj)


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


def _get_inlay_source_object(inlay_type):
    source_name = f"{inlay_type}_inlay"
    try:
        source_doc = App.getDocument(source_name)
    except Exception:
        source_doc = None
    if not source_doc:
        return None, None

    source_object = find_object_by_label(source_doc, 'final_inlay')
    if not source_object:
        source_object = source_doc.getObject(f'{inlay_type}_pad')
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
    part_object = doc.getObject("handle_part")

    if not doc_name in App.listDocuments().keys():
        depth_inches = _get_inlay_depth_inches(doc, inlay_type)
        new_document(doc_name, inlay_type, depth_inches)

    Gui.setActiveDocument(doc)

    create_sketch(inlay_type)



def new_document(doc_name, inlay_type, inlay_depth_inches=0.2):
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
    
    sketchershapes.pad_sketch(sketch, inlay_depth_inches)


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

    anchor_name = f"{inlay_type}_outer" if target_doc.getObject(f"{inlay_type}_outer") else inlay_type
    if not target_doc.getObject(anchor_name):
        print(f"Anchor object for '{inlay_type}' was not found.")
        return

    lnk.setExpression('.Placement.Base.y', f'{anchor_name}.Placement.Base.y + CueDimensions.{inlay_type}_length')
    lnk.setExpression(
        '.Placement.Base.z',
        f'(CueDimensions.finish_size_startod + ((CueDimensions.finish_size_endod - CueDimensions.finish_size_startod) / CueDimensions.finish_size_length) * ({anchor_name}.Placement.Base.y + CueDimensions.{inlay_type}_length))/2'
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




def fillet_for_cnc(
    noise=None,
    fillet_radius_inch=None,
    final_solid_name=None,
    preserve_unfilleted=None,
    allow_smaller_radius_fallback=None,
    require_full_coverage=None,
    show_dialog=True,
):
    def _ensure_property(obj, prop_type, prop_name, group_name, doc_text):
        try:
            if hasattr(obj, prop_name):
                return True
            obj.addProperty(prop_type, prop_name, group_name, doc_text)
            return True
        except Exception:
            return False

    def _settings_from_object(obj):
        try:
            if not obj or (not hasattr(obj, "FilletRadiusInch")):
                return None
            radius_in = max(0.0001, float(getattr(obj, "FilletRadiusInch", 0.014) or 0.014))
            preserve = bool(getattr(obj, "FilletPreserveUnfilleted", False))
            allow_smaller = bool(getattr(obj, "FilletAllowSmallerRadiusFallback", False))
            require_full = bool(getattr(obj, "FilletRequireFullCoverage", True))
            final_name = str(getattr(obj, "Label", getattr(obj, "Name", "final_inlay")) or "final_inlay").strip() or "final_inlay"
            return {
                "radius_in": radius_in,
                "preserve_unfilleted": preserve,
                "allow_smaller_radius_fallback": allow_smaller,
                "require_full_coverage": require_full,
                "final_name": final_name,
            }
        except Exception:
            return None

    def _load_saved_fillet_settings(preferred_label=None):
        selection = []
        if Gui is not None:
            try:
                selection = list(Gui.Selection.getSelection() or [])
            except Exception:
                selection = []

        for obj in selection:
            settings = _settings_from_object(obj)
            if settings is not None:
                return settings, obj

        doc_local = App.ActiveDocument
        objects = list(getattr(doc_local, "Objects", []) or []) if doc_local else []

        preferred = str(preferred_label or "").strip()
        if preferred:
            for obj in objects:
                try:
                    if str(getattr(obj, "Label", "") or "").strip() != preferred:
                        continue
                except Exception:
                    continue
                settings = _settings_from_object(obj)
                if settings is not None:
                    return settings, obj

        for obj in objects:
            settings = _settings_from_object(obj)
            if settings is not None:
                return settings, obj

        return None, None

    fillet_prefs = None
    default_radius_inch = 0.014
    default_final_name = "final_inlay"
    default_preserve_unfilleted = False
    default_allow_smaller_radius_fallback = False
    default_require_full_coverage = True
    try:
        fillet_prefs = App.ParamGet("User parameter:BaseApp/Preferences/Mod/ButlerCues/FilletForCNC")
        default_radius_inch = max(0.0001, float(fillet_prefs.GetFloat("radius_in", 0.014)))
        default_final_name = str(fillet_prefs.GetString("final_name", "final_inlay") or "").strip() or "final_inlay"
        default_preserve_unfilleted = bool(fillet_prefs.GetBool("preserve_unfilleted", False))
        default_allow_smaller_radius_fallback = bool(fillet_prefs.GetBool("allow_smaller_radius_fallback", False))
        default_require_full_coverage = bool(fillet_prefs.GetBool("require_full_coverage", True))
    except Exception:
        fillet_prefs = None

    selected_settings, selected_settings_obj = _load_saved_fillet_settings(preferred_label=default_final_name)
    if selected_settings:
        try:
            default_radius_inch = max(0.0001, float(selected_settings.get("radius_in", default_radius_inch)))
        except Exception:
            pass
        try:
            default_preserve_unfilleted = bool(selected_settings.get("preserve_unfilleted", default_preserve_unfilleted))
        except Exception:
            pass
        try:
            default_allow_smaller_radius_fallback = bool(
                selected_settings.get("allow_smaller_radius_fallback", default_allow_smaller_radius_fallback)
            )
        except Exception:
            pass
        try:
            default_require_full_coverage = bool(selected_settings.get("require_full_coverage", default_require_full_coverage))
        except Exception:
            pass
        try:
            default_final_name = str(selected_settings.get("final_name", default_final_name) or default_final_name).strip() or default_final_name
        except Exception:
            pass
        try:
            selected_label = str(getattr(selected_settings_obj, "Label", getattr(selected_settings_obj, "Name", "")) or "").strip()
            if selected_label:
                print(f"Fillet for CNC: loaded saved settings from '{selected_label}'.")
                print(
                    f"  settings: radius={float(default_radius_inch):.4f} in, "
                    f"preserve_unfilleted={'yes' if bool(default_preserve_unfilleted) else 'no'}, "
                    f"allow_smaller_fallback={'yes' if bool(default_allow_smaller_radius_fallback) else 'no'}, "
                    f"require_full_coverage={'yes' if bool(default_require_full_coverage) else 'no'}"
                )
        except Exception:
            pass

    if fillet_radius_inch is None:
        fillet_radius_inch = default_radius_inch
    try:
        fillet_radius_inch = max(0.0001, float(fillet_radius_inch))
    except Exception:
        fillet_radius_inch = default_radius_inch

    def _ensure_fillet_suffix(label_text):
        base = str(label_text or "").strip()
        if not base:
            base = "final_inlay"
        if base.lower().endswith("_fillet"):
            return base
        return f"{base}_fillet"

    user_provided_output_name = bool(str(final_solid_name or "").strip())
    output_label = _ensure_fillet_suffix(str(final_solid_name or default_final_name or "final_inlay"))

    if not user_provided_output_name and Gui is not None:
        try:
            current_selection = list(Gui.Selection.getSelection() or [])
        except Exception:
            current_selection = []
        if len(current_selection) == 1:
            try:
                selected_label = str(
                    getattr(current_selection[0], "Label", getattr(current_selection[0], "Name", "")) or ""
                ).strip()
            except Exception:
                selected_label = ""
            if selected_label:
                output_label = _ensure_fillet_suffix(selected_label)

    if preserve_unfilleted is None:
        preserve_unfilleted = default_preserve_unfilleted
    try:
        preserve_unfilleted = bool(preserve_unfilleted)
    except Exception:
        preserve_unfilleted = True

    if allow_smaller_radius_fallback is None:
        allow_smaller_radius_fallback = default_allow_smaller_radius_fallback
    try:
        allow_smaller_radius_fallback = bool(allow_smaller_radius_fallback)
    except Exception:
        allow_smaller_radius_fallback = False

    if require_full_coverage is None:
        require_full_coverage = default_require_full_coverage
    try:
        require_full_coverage = bool(require_full_coverage)
    except Exception:
        require_full_coverage = True

    if show_dialog and QtGui is not None and Gui is not None:
        class _FilletForCNCTaskPanel:
            def __init__(self):
                self.form = QtGui.QWidget()
                self.form.setWindowTitle("Fillet for CNC")
                self._last_run_signature = None

                layout = QtGui.QFormLayout(self.form)

                self.final_name_edit = QtGui.QLineEdit(str(output_label or "final_inlay"))

                self.radius_spin = QtGui.QDoubleSpinBox()
                self.radius_spin.setDecimals(4)
                self.radius_spin.setRange(0.0001, 0.5000)
                self.radius_spin.setSingleStep(0.001)
                self.radius_spin.setValue(float(fillet_radius_inch))
                self.radius_spin.setSuffix(" in")

                self.preserve_check = QtGui.QCheckBox("Retain solids that cannot be filleted")
                self.preserve_check.setChecked(bool(preserve_unfilleted))

                self.fallback_radius_check = QtGui.QCheckBox("Allow smaller fallback radius")
                self.fallback_radius_check.setChecked(bool(allow_smaller_radius_fallback))

                self.full_coverage_check = QtGui.QCheckBox("Require full miter coverage (fail otherwise)")
                self.full_coverage_check.setChecked(bool(require_full_coverage))

                note = QtGui.QLabel("Fillet targets Z-axis edges only.")

                layout.addRow("Final solid name", self.final_name_edit)
                layout.addRow("Fillet radius", self.radius_spin)
                layout.addRow("Fallback", self.preserve_check)
                layout.addRow("Radius behavior", self.fallback_radius_check)
                layout.addRow("Fit requirement", self.full_coverage_check)
                layout.addRow("", note)

                try:
                    self.final_name_edit.textChanged.connect(self._mark_dirty)
                    self.radius_spin.valueChanged.connect(self._mark_dirty)
                    self.preserve_check.toggled.connect(self._mark_dirty)
                    self.fallback_radius_check.toggled.connect(self._mark_dirty)
                    self.full_coverage_check.toggled.connect(self._mark_dirty)
                except Exception:
                    pass

            def _mark_dirty(self, *args):
                self._last_run_signature = None

            def _signature(self):
                return (
                    str(self.final_name_edit.text() or "").strip().lower(),
                    round(float(self.radius_spin.value()), 6),
                    bool(self.preserve_check.isChecked()),
                    bool(self.fallback_radius_check.isChecked()),
                    bool(self.full_coverage_check.isChecked()),
                )

            def _run_creation(self, close_after=True):
                current_signature = self._signature()
                if close_after and self._last_run_signature == current_signature:
                    try:
                        Gui.Control.closeDialog()
                    except Exception:
                        pass
                    return True

                selected_name = str(self.final_name_edit.text() or "").strip() or "final_inlay"
                selected_radius = max(0.0001, float(self.radius_spin.value()))
                selected_preserve = bool(self.preserve_check.isChecked())
                selected_allow_smaller = bool(self.fallback_radius_check.isChecked())
                selected_require_full = bool(self.full_coverage_check.isChecked())

                try:
                    if fillet_prefs is not None:
                        fillet_prefs.SetString("final_name", str(selected_name))
                        fillet_prefs.SetFloat("radius_in", float(selected_radius))
                        fillet_prefs.SetBool("preserve_unfilleted", bool(selected_preserve))
                        fillet_prefs.SetBool("allow_smaller_radius_fallback", bool(selected_allow_smaller))
                        fillet_prefs.SetBool("require_full_coverage", bool(selected_require_full))
                except Exception:
                    pass

                try:
                    fillet_for_cnc(
                        noise=noise,
                        fillet_radius_inch=selected_radius,
                        final_solid_name=selected_name,
                        preserve_unfilleted=selected_preserve,
                        allow_smaller_radius_fallback=selected_allow_smaller,
                        require_full_coverage=selected_require_full,
                        show_dialog=False,
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
                try:
                    Gui.Control.closeDialog()
                except Exception:
                    pass
                return True

        Gui.Control.showDialog(_FilletForCNCTaskPanel())
        return

    doc = App.ActiveDocument
    try:
        import Part
    except Exception as exc:
        print(f"Part module unavailable: {exc}")
        return

    def _axis_vector_for_bbox(shape_obj):
        bbox = shape_obj.BoundBox
        axes = [
            (bbox.XLength, App.Vector(1, 0, 0)),
            (bbox.YLength, App.Vector(0, 1, 0)),
            (bbox.ZLength, App.Vector(0, 0, 1)),
        ]
        axes = [item for item in axes if item[0] > 1e-7]
        if not axes:
            return App.Vector(0, 0, 1)
        axes.sort(key=lambda item: item[0])
        return axes[0][1]

    def _edges_parallel_to_axis(shape_obj, axis, tol=1e-4):
        matches = []
        for edge in shape_obj.Edges:
            curve = getattr(edge, "Curve", None)
            if curve and getattr(curve, "TypeId", "") == "Part::GeomLine":
                try:
                    if curve.Direction.isParallel(axis, max(1e-3, tol * 10.0)):
                        matches.append(edge)
                        continue
                except Exception:
                    pass

            try:
                vertices = list(getattr(edge, "Vertexes", []) or [])
                if len(vertices) != 2:
                    continue
                p0 = getattr(vertices[0], "Point", None)
                p1 = getattr(vertices[1], "Point", None)
                if p0 is None or p1 is None:
                    continue
                delta = p1.sub(p0)
                if float(getattr(delta, "Length", 0.0) or 0.0) <= tol:
                    continue
                if delta.isParallel(axis, max(1e-3, tol * 10.0)):
                    matches.append(edge)
            except Exception:
                continue
        return matches

    def _find_matching_edge(shape_obj, reference_edge, axis, length_tol=0.03, center_tol=0.2):
        ref_len = reference_edge.Length
        ref_center = reference_edge.CenterOfMass
        candidates = _edges_parallel_to_axis(shape_obj, axis)
        if not candidates:
            return None

        best = None
        best_score = None
        for candidate in candidates:
            if abs(candidate.Length - ref_len) > max(length_tol, ref_len * 0.02):
                continue
            center_dist = candidate.CenterOfMass.sub(ref_center).Length
            if center_dist > center_tol:
                continue
            score = abs(candidate.Length - ref_len) + center_dist
            if best is None or score < best_score:
                best = candidate
                best_score = score

        return best

    def _is_valid_shape(shape_obj):
        return bool(shape_obj) and not shape_obj.isNull() and shape_obj.isValid()

    def _shape_fillet_debug(shape_obj):
        try:
            edges = list(getattr(shape_obj, "Edges", []) or [])
        except Exception:
            edges = []
        total_edges = len(edges)
        linear_edges = []
        for edge in edges:
            try:
                curve = getattr(edge, "Curve", None)
                if curve and getattr(curve, "TypeId", "") == "Part::GeomLine":
                    linear_edges.append(edge)
            except Exception:
                pass

        def _axis_count(axis):
            try:
                return len(_edges_parallel_to_axis(shape_obj, axis))
            except Exception:
                return 0

        try:
            lengths = [float(getattr(edge, "Length", 0.0) or 0.0) for edge in edges]
            lengths = [value for value in lengths if value > 1e-9]
            min_len = min(lengths) if lengths else 0.0
        except Exception:
            min_len = 0.0

        return {
            "total_edges": total_edges,
            "linear_edges": len(linear_edges),
            "z_parallel": _axis_count(App.Vector(0, 0, 1)),
            "x_parallel": _axis_count(App.Vector(1, 0, 0)),
            "y_parallel": _axis_count(App.Vector(0, 1, 0)),
            "min_edge_len_mm": min_len,
        }

    def _bit_fit_unfit_z_edges(shape_obj, bit_radius_mm, target_axis=None, edge_candidates=None):
        if edge_candidates is not None:
            try:
                z_edges = list(edge_candidates or [])
            except Exception:
                z_edges = []
        else:
            axis_vec = target_axis if target_axis is not None else App.Vector(0, 0, 1)
            try:
                z_edges = list(_edges_parallel_to_axis(shape_obj, axis_vec) or [])
            except Exception:
                z_edges = []

        unfit = []
        try:
            test_radius = max(0.0, float(bit_radius_mm or 0.0))
        except Exception:
            test_radius = 0.0
        if test_radius <= 1e-9:
            return []
        tool_dia = 2.0 * test_radius

        def _is_horizontal_face(face_obj):
            try:
                com = face_obj.CenterOfMass
                u, v = face_obj.Surface.parameter(com)
                n = face_obj.normalAt(u, v)
                return bool(n.isParallel(App.Vector(0, 0, 1), 1e-4))
            except Exception:
                return False

        def _edge_is_sharp_corner(edge_obj):
            try:
                face_anc = list(shape_obj.ancestorsOfType(edge_obj, Part.Face) or [])
            except Exception:
                face_anc = []
            if not face_anc:
                return False

            side_faces = []
            for face_obj in face_anc:
                if _is_horizontal_face(face_obj):
                    continue
                side_faces.append(face_obj)

            if len(side_faces) < 2:
                return False

            try:
                p = edge_obj.valueAt((float(edge_obj.FirstParameter) + float(edge_obj.LastParameter)) * 0.5)
            except Exception:
                try:
                    p = edge_obj.CenterOfMass
                except Exception:
                    return True

            normals = []
            for face_obj in side_faces[:3]:
                try:
                    u, v = face_obj.Surface.parameter(p)
                    n = face_obj.normalAt(u, v)
                    if float(getattr(n, "Length", 0.0) or 0.0) > 1e-9:
                        normals.append(n)
                except Exception:
                    continue

            if len(normals) < 2:
                return True

            try:
                angle_deg = float(normals[0].getAngle(normals[1]) or 0.0) * (180.0 / 3.141592653589793)
            except Exception:
                return True

            # Smooth/tangent seam (eg line-to-arc tangent transition on capsule profile)
            # should not be treated as an unfit sharp corner.
            return bool(angle_deg >= 5.0)

        def _face_normal(face_obj):
            try:
                com = face_obj.CenterOfMass
                u, v = face_obj.Surface.parameter(com)
                n = face_obj.normalAt(u, v)
                if float(getattr(n, "Length", 0.0) or 0.0) <= 1e-9:
                    return None
                return n
            except Exception:
                return None

        def _is_vertical_side_face(face_obj):
            n = _face_normal(face_obj)
            if n is None:
                return False
            try:
                return not bool(n.isParallel(App.Vector(0, 0, 1), 1e-4))
            except Exception:
                return False

        def _edge_key(edge_obj):
            try:
                c = edge_obj.CenterOfMass
                l = float(getattr(edge_obj, "Length", 0.0) or 0.0)
                return (round(float(c.x), 5), round(float(c.y), 5), round(float(c.z), 5), round(l, 5))
            except Exception:
                try:
                    return (id(edge_obj),)
                except Exception:
                    return (0,)

        for edge in z_edges:
            try:
                edge_len = float(getattr(edge, "Length", 0.0) or 0.0)
            except Exception:
                edge_len = 0.0
            if edge_len <= 1e-7:
                continue

            # Bit-fit test: if this edge cannot be filleted at the requested tool radius,
            # then the cutter cannot realize the required corner clearance here.
            try:
                curve = getattr(edge, "Curve", None)
                curve_type = str(getattr(curve, "TypeId", "") or "")
            except Exception:
                curve = None
                curve_type = ""

            requires_clearance = False

            if curve_type == "Part::GeomLine":
                if _edge_is_sharp_corner(edge):
                    requires_clearance = True
            else:
                # Already-rounded features can still be unmachinable if their local
                # radius is tighter than the cutter radius.
                local_radius = None
                try:
                    local_radius = float(getattr(curve, "Radius", 0.0) or 0.0)
                except Exception:
                    local_radius = None
                if local_radius is not None and local_radius > 1e-9 and local_radius < (test_radius - 1e-6):
                    requires_clearance = True

            if not requires_clearance:
                continue

            try:
                candidate = shape_obj.makeFillet(test_radius, [edge])
                if candidate and (not candidate.isNull()) and candidate.isValid():
                    continue
            except Exception:
                pass

            unfit.append(edge)

        # Thin-region check: opposite side faces closer than tool diameter indicate
        # regions where cutter cannot physically fit.
        try:
            side_faces = [face for face in list(getattr(shape_obj, "Faces", []) or []) if _is_vertical_side_face(face)]
        except Exception:
            side_faces = []

        if len(side_faces) >= 2:
            thin_edges = []
            for idx_a in range(len(side_faces)):
                face_a = side_faces[idx_a]
                normal_a = _face_normal(face_a)
                if normal_a is None:
                    continue

                for idx_b in range(idx_a + 1, len(side_faces)):
                    face_b = side_faces[idx_b]
                    normal_b = _face_normal(face_b)
                    if normal_b is None:
                        continue

                    try:
                        angle = float(normal_a.getAngle(normal_b) or 0.0) * (180.0 / 3.141592653589793)
                    except Exception:
                        angle = 0.0
                    # Opposing faces are most indicative of local thickness.
                    if angle < 150.0:
                        continue

                    try:
                        dist_val = float(face_a.distToShape(face_b)[0])
                    except Exception:
                        continue

                    if dist_val <= 1e-5:
                        continue
                    if dist_val >= (tool_dia - 1e-6):
                        continue

                    try:
                        thin_edges.extend(list(getattr(face_a, "Edges", []) or []))
                    except Exception:
                        pass
                    try:
                        thin_edges.extend(list(getattr(face_b, "Edges", []) or []))
                    except Exception:
                        pass

            if thin_edges:
                seen = {_edge_key(edge) for edge in list(unfit or [])}
                for edge in thin_edges:
                    key = _edge_key(edge)
                    if key in seen:
                        continue
                    seen.add(key)
                    unfit.append(edge)

        return unfit

    def _create_bitfit_issue_markers(issue_infos):
        if not issue_infos:
            return None
        try:
            group_name = "BitFitIssues"
            idx = 1
            while doc.getObject(f"{group_name}_{idx}"):
                idx += 1
            issue_group = doc.addObject("App::DocumentObjectGroup", f"{group_name}_{idx}")
            issue_group.Label = "Bit-Fit Issues"
        except Exception:
            return None

        created = 0
        for issue_idx, (label, issue_shape, issue_edges, before_count) in enumerate(issue_infos, start=1):
            try:
                edge_compound = Part.makeCompound(list(issue_edges or []))
                if not edge_compound or edge_compound.isNull():
                    continue
                marker_name = f"BitFitIssue_{issue_idx}"
                marker = doc.addObject("Part::Feature", marker_name)
                marker.Shape = edge_compound
                marker.Label = f"bitfit_{label}_remaining_{len(issue_edges)}"
                try:
                    view_obj = getattr(marker, "ViewObject", None)
                    if view_obj:
                        view_obj.LineColor = (1.0, 0.1, 0.1)
                        view_obj.LineWidth = 4.0
                        view_obj.PointColor = (1.0, 0.1, 0.1)
                        view_obj.PointSize = 6.0
                except Exception:
                    pass
                try:
                    issue_group.addObject(marker)
                except Exception:
                    pass
                created += 1
            except Exception:
                continue

        if created <= 0:
            try:
                doc.removeObject(issue_group.Name)
            except Exception:
                pass
            return None

        try:
            doc.recompute()
        except Exception:
            pass

        try:
            if Gui:
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(doc.Name, issue_group.Name)
        except Exception:
            pass

        return issue_group

    selection = Gui.Selection.getSelection()
    if not selection:
        print("No object selected. Please select an object.")
        return

    selected_roots = list(selection or [])

    def _gather_solidish_objects(seed_objects):
        gathered = []
        seen = set()

        def _add(obj):
            if not obj:
                return
            name = str(getattr(obj, "Name", "") or "").strip()
            if name and name in seen:
                return
            if name:
                seen.add(name)
            gathered.append(obj)

        def _walk_group_members(group_obj):
            try:
                members = list(getattr(group_obj, "Group", []) or [])
            except Exception:
                members = []
            for member in members:
                _add(member)
                _walk_group_members(member)

        for item in list(seed_objects or []):
            _add(item)
            _walk_group_members(item)

        return gathered

    selection = _gather_solidish_objects(selection)

    source_entries = []
    for obj in selection:
        obj_label = str(getattr(obj, "Label", getattr(obj, "Name", "")) or "")
        if not obj or not hasattr(obj, "Shape"):
            print(f"Skipping '{obj_label}': not a valid solid object.")
            continue

        shape = getattr(obj, "Shape", None)
        if not shape or shape.isNull():
            print(f"Skipping '{obj_label}': shape is null.")
            continue

        try:
            solids_now = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids_now = []
        if not solids_now:
            print(f"Skipping '{obj_label}': shape has no solids (build Face/Extrude first).")
            continue

        try:
            shape = shape.removeSplitter()
        except Exception:
            pass

        try:
            solids = list(getattr(shape, "Solids", []) or [])
        except Exception:
            solids = []

        if solids and len(solids) > 1:
            for idx, solid in enumerate(solids, start=1):
                try:
                    if not solid or solid.isNull():
                        continue
                except Exception:
                    continue
                source_entries.append((obj, solid, f"{obj_label}[{idx}]"))
        else:
            source_entries.append((obj, shape, obj_label or str(getattr(obj, "Name", ""))))

    if not source_entries:
        print("No valid solids found in current selection.")
        return

    effective_output_label = output_label
    if not user_provided_output_name:
        try:
            if len(selected_roots) == 1:
                root_label = str(
                    getattr(selected_roots[0], "Label", getattr(selected_roots[0], "Name", "")) or ""
                ).strip()
                if root_label:
                    effective_output_label = root_label
            elif len(source_entries) == 1:
                source_label_for_name = str(
                    getattr(source_entries[0][0], "Label", getattr(source_entries[0][0], "Name", "")) or ""
                ).strip()
                if source_label_for_name:
                    effective_output_label = source_label_for_name
        except Exception:
            effective_output_label = output_label

    effective_output_label = _ensure_fillet_suffix(effective_output_label)

    print(f"Fillet for CNC: evaluating {len(source_entries)} solid(s) from {len(selection)} selected/group object(s).")

    # Convert fillet radius from inches to millimeters
    fillet_radius = fillet_radius_inch * 25.4

    axis_candidates = [App.Vector(0, 0, 1)]

    unique_axes = []
    for axis in axis_candidates:
        if not any(axis.isEqual(existing, 1e-7) for existing in unique_axes):
            unique_axes.append(axis)

    def _axis_key(vec_obj):
        try:
            vec = App.Vector(float(vec_obj.x), float(vec_obj.y), float(vec_obj.z))
        except Exception:
            try:
                vec = App.Vector(vec_obj)
            except Exception:
                vec = App.Vector(0, 0, 1)
        try:
            if vec.z < 0.0 or (abs(vec.z) < 1e-9 and vec.y < 0.0) or (abs(vec.z) < 1e-9 and abs(vec.y) < 1e-9 and vec.x < 0.0):
                vec = App.Vector(-vec.x, -vec.y, -vec.z)
        except Exception:
            pass
        return (round(float(vec.x), 3), round(float(vec.y), 3), round(float(vec.z), 3))

    def _candidate_axes_for_shape(shape_obj):
        return [App.Vector(0, 0, 1)]

    def _preferred_axis_for_shape(shape_obj):
        candidate_axes = [App.Vector(0, 0, 1)]
        ranked = []
        for axis in candidate_axes:
            try:
                count = len(_edges_parallel_to_axis(shape_obj, axis))
            except Exception:
                count = 0
            ranked.append((int(count), axis))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if ranked and ranked[0][0] > 0:
            return ranked[0][1]
        return App.Vector(0, 0, 1)

    def _axis_order_for_shape(shape_obj):
        return [App.Vector(0, 0, 1)]

    def _collect_target_edges(shape_obj, axis_order):
        target_edges_local = []
        seen_target_keys_local = set()
        for axis in list(axis_order or []):
            try:
                axis_edges = list(_edges_parallel_to_axis(shape_obj, axis) or [])
            except Exception:
                continue
            for edge in axis_edges:
                key = _axis_key(getattr(edge, "CenterOfMass", App.Vector(0, 0, 0)))
                try:
                    key = key + (round(float(getattr(edge, "Length", 0.0) or 0.0), 4),)
                except Exception:
                    key = key + (0.0,)
                if key in seen_target_keys_local:
                    continue
                seen_target_keys_local.add(key)
                target_edges_local.append(edge)

        return target_edges_local

    def _radius_attempts_for_shape(shape_obj, base_radius):
        try:
            base_radius = max(0.005, float(base_radius))
        except Exception:
            base_radius = 0.005

        if not allow_smaller_radius_fallback:
            return [base_radius]
        attempts = [
            base_radius,
            base_radius * 0.9,
            base_radius * 0.8,
            base_radius * 0.7,
            base_radius * 0.6,
            base_radius * 0.5,
            base_radius * 0.4,
            base_radius * 0.3,
            base_radius * 0.2,
            base_radius * 0.1,
        ]
        try:
            edge_lengths = [float(getattr(edge, "Length", 0.0) or 0.0) for edge in (getattr(shape_obj, "Edges", []) or [])]
            edge_lengths = [length for length in edge_lengths if length > 1e-6]
            if edge_lengths:
                min_len = min(edge_lengths)
                attempts.extend([min_len * 0.45, min_len * 0.35, min_len * 0.25, min_len * 0.15])
        except Exception:
            pass
        dedup = []
        for value in attempts:
            try:
                radius_value = float(value)
            except Exception:
                continue
            if radius_value <= 0.005:
                continue
            if radius_value > base_radius:
                continue
            if any(abs(radius_value - existing) < 1e-6 for existing in dedup):
                continue
            dedup.append(radius_value)
        if not dedup:
            dedup = [base_radius]
        dedup.sort(reverse=True)
        return dedup

    def _refine_z_edges_with_radii(shape_obj, radii_mm):
        working = shape_obj
        updates = 0
        radius_list = []
        try:
            radius_list = [float(value) for value in list(radii_mm or []) if float(value) > 0.0]
        except Exception:
            radius_list = []
        if not radius_list:
            return working, updates

        for radius_mm in sorted(radius_list, reverse=True):
            for _ in range(4):
                round_updates = 0
                try:
                    edges_now = _edges_parallel_to_axis(working, App.Vector(0, 0, 1))
                except Exception:
                    edges_now = []
                if not edges_now:
                    break
                for edge in list(edges_now):
                    try:
                        candidate = working.makeFillet(radius_mm, [edge])
                    except Exception:
                        continue
                    if _is_valid_shape(candidate):
                        working = candidate
                        round_updates += 1
                        updates += 1
                if round_updates == 0:
                    break
        return working, updates

    filleted_shapes = []
    successful_count = 0
    no_fillet_needed_count = 0
    preserved_unfilleted_count = 0
    failed_labels = []
    failed_details = []
    no_fillet_needed_labels = []
    bitfit_unfit_total = 0
    bitfit_issue_infos = []
    sample_radius = None
    sample_axis = None

    print(
        f"Fillet target radius: {float(fillet_radius_inch):.4f} in "
        f"({'fallback enabled' if allow_smaller_radius_fallback else 'strict'})"
    )
    if require_full_coverage:
        print("Full miter coverage is required.")

    for source_obj, source_shape, source_label in source_entries:
        fillet = None
        successful_radius = None
        used_axis = None
        source_radius_attempts = _radius_attempts_for_shape(source_shape, fillet_radius)
        axis_order = _axis_order_for_shape(source_shape)
        preferred_axis = axis_order[0] if axis_order else App.Vector(0, 0, 1)

        target_edges = _collect_target_edges(source_shape, axis_order)

        if not target_edges:
            msg = (
                f"Bit-fit check for '{source_label}': unable to detect seam edges for evaluation; "
                "cannot claim cutter fit."
            )
            print(msg)
            failed_labels.append(source_label)
            try:
                dbg = _shape_fillet_debug(source_shape)
            except Exception:
                dbg = {}
            failed_details.append((source_label, dbg, source_radius_attempts[:8]))
            if preserve_unfilleted:
                try:
                    filleted_shapes.append(source_shape)
                    preserved_unfilleted_count += 1
                except Exception:
                    pass
            continue

        unfit_z_edges = list(_bit_fit_unfit_z_edges(source_shape, fillet_radius, edge_candidates=target_edges) or [])
        bitfit_unfit_total += int(len(unfit_z_edges))
        if unfit_z_edges:
            print(
                f"Bit-fit check for '{source_label}': {len(unfit_z_edges)} unfit area(s) (sharp corners/thin regions) "
                f"for tool diameter {(2.0 * float(fillet_radius) / 25.4):.4f} in."
            )
        else:
            print(
                f"Bit-fit check for '{source_label}': no obvious unfit areas at tool diameter "
                f"{(2.0 * float(fillet_radius) / 25.4):.4f} in; verifying with fillet attempt."
            )

        for attempt_radius in source_radius_attempts:
            try:
                candidate = source_shape.makeFillet(attempt_radius, target_edges)
            except Exception:
                continue
            if _is_valid_shape(candidate):
                fillet = candidate
                successful_radius = attempt_radius
                used_axis = None
                break

        for axis in axis_order:
            if fillet:
                break
            edges_to_fillet = _edges_parallel_to_axis(source_shape, axis)
            if not edges_to_fillet:
                continue

            for attempt_radius in source_radius_attempts:
                try:
                    candidate = source_shape.makeFillet(attempt_radius, edges_to_fillet)
                except Exception:
                    continue

                if _is_valid_shape(candidate):
                    fillet = candidate
                    successful_radius = attempt_radius
                    used_axis = axis
                    break

            if fillet:
                break

        if (not fillet) and (not require_full_coverage):
            for axis in axis_order:
                reference_edges = _edges_parallel_to_axis(source_shape, axis)
                if not reference_edges:
                    continue

                for attempt_radius in source_radius_attempts:
                    working = source_shape
                    success_count = 0

                    for reference_edge in reference_edges:
                        target_edge = _find_matching_edge(working, reference_edge, axis)
                        if not target_edge:
                            continue

                        try:
                            candidate = working.makeFillet(attempt_radius, [target_edge])
                        except Exception:
                            continue

                        if _is_valid_shape(candidate):
                            working = candidate
                            success_count += 1

                    if success_count:
                        fillet = working
                        successful_radius = attempt_radius
                        used_axis = axis
                        break

                if fillet:
                    break

        if (not fillet) and (not require_full_coverage):
            initial_z_edges = []
            try:
                initial_z_edges = _edges_parallel_to_axis(source_shape, App.Vector(0, 0, 1))
            except Exception:
                initial_z_edges = []

            if initial_z_edges:
                best_shape = None
                best_radius = None
                best_success_count = -1

                for attempt_radius in source_radius_attempts:
                    working = source_shape
                    success_count = 0
                    max_rounds = 6

                    for _ in range(max_rounds):
                        round_progress = 0
                        current_z_edges = []
                        try:
                            current_z_edges = _edges_parallel_to_axis(working, App.Vector(0, 0, 1))
                        except Exception:
                            current_z_edges = []
                        if not current_z_edges:
                            break

                        for edge in list(current_z_edges):
                            try:
                                candidate = working.makeFillet(attempt_radius, [edge])
                            except Exception:
                                continue
                            if _is_valid_shape(candidate):
                                working = candidate
                                round_progress += 1
                                success_count += 1

                        if round_progress == 0:
                            break

                    if success_count > best_success_count:
                        best_success_count = success_count
                        best_shape = working
                        best_radius = attempt_radius

                if best_shape is not None and best_success_count > 0:
                    fillet = best_shape
                    successful_radius = best_radius
                    used_axis = App.Vector(0, 0, 1)
                    try:
                        total_z_edges = len(initial_z_edges)
                        print(
                            f"Z-edge fillet coverage for '{source_label}': "
                            f"{best_success_count}/{total_z_edges} edge updates at radius {float(best_radius) / 25.4:.4f} in."
                        )
                    except Exception:
                        pass

        if not fillet:
            if not unfit_z_edges:
                filleted_shapes.append(source_shape)
                no_fillet_needed_count += 1
                no_fillet_needed_labels.append(source_label)
                try:
                    source_obj.Visibility = False
                except Exception:
                    pass
                continue

            if unfit_z_edges:
                bitfit_issue_infos.append((source_label, source_shape, list(unfit_z_edges), len(unfit_z_edges)))
            failed_labels.append(source_label)
            try:
                dbg = _shape_fillet_debug(source_shape)
            except Exception:
                dbg = {}
            failed_details.append((source_label, dbg, source_radius_attempts[:8]))
            if preserve_unfilleted:
                try:
                    filleted_shapes.append(source_shape)
                    preserved_unfilleted_count += 1
                except Exception:
                    pass
            continue

        filleted_shapes.append(fillet)
        successful_count += 1

        # In strict full-coverage mode, a successful exact-radius fillet operation is
        # considered authoritative for cutter fit at that radius.
        if require_full_coverage:
            if sample_radius is None and successful_radius is not None:
                sample_radius = successful_radius
                sample_axis = used_axis
            try:
                source_obj.Visibility = False
            except Exception:
                pass
            continue

        post_axis_order = _axis_order_for_shape(fillet)
        post_target_edges = _collect_target_edges(fillet, post_axis_order)
        remaining_unfit = list(_bit_fit_unfit_z_edges(fillet, fillet_radius, edge_candidates=post_target_edges) or [])
        if remaining_unfit:
            bitfit_issue_infos.append((source_label, fillet, list(remaining_unfit), len(unfit_z_edges)))
            if source_label not in failed_labels:
                failed_labels.append(source_label)
            print(
                f"Bit-fit recheck for '{source_label}': still has {len(remaining_unfit)} unfit area(s) "
                f"after fillet attempt."
            )
            try:
                dbg = _shape_fillet_debug(source_shape)
            except Exception:
                dbg = {}
            failed_details.append((source_label, dbg, source_radius_attempts[:8]))
            if preserve_unfilleted:
                try:
                    filleted_shapes[-1] = source_shape
                    preserved_unfilleted_count += 1
                except Exception:
                    pass
            else:
                try:
                    filleted_shapes.pop()
                except Exception:
                    pass
                successful_count = max(0, successful_count - 1)
            continue

        if not require_full_coverage:
            try:
                extra_radii = [successful_radius]
                if allow_smaller_radius_fallback:
                    try:
                        extra_radii.extend(
                            [
                                value
                                for value in source_radius_attempts
                                if successful_radius is not None and float(value) < float(successful_radius)
                            ]
                        )
                    except Exception:
                        pass

                refined_shape, refine_updates = _refine_z_edges_with_radii(
                    fillet,
                    extra_radii,
                )
                if refine_updates > 0 and _is_valid_shape(refined_shape):
                    filleted_shapes[-1] = refined_shape
                    fillet = refined_shape
                    print(
                        f"Additional Z-edge refinement for '{source_label}': {refine_updates} extra edge update(s)."
                    )
            except Exception:
                pass

        if sample_radius is None and successful_radius is not None:
            sample_radius = successful_radius
            sample_axis = used_axis

        try:
            source_obj.Visibility = False
        except Exception:
            pass

    bitfit_issue_group = None
    if bitfit_issue_infos:
        try:
            bitfit_issue_group = _create_bitfit_issue_markers(bitfit_issue_infos)
        except Exception:
            bitfit_issue_group = None
        print("Bit-fit failure details:")
        for name, _, remaining_edges, before_count in bitfit_issue_infos[:50]:
            print(
                f"  - {name}: remaining unfit corner areas={len(remaining_edges)} "
                f"(before fillet={int(before_count)})"
            )
        if bitfit_issue_group:
            print(f"Created '{bitfit_issue_group.Label}' marker group showing failed locations.")

    if require_full_coverage and failed_labels:
        failure_lines = []
        if failed_labels:
            msg = f"Fillet aborted: could not fillet solid(s): {', '.join(str(name) for name in failed_labels)}"
            print(msg)
            failure_lines.append(msg)
        if bitfit_issue_infos:
            failure_lines.append(
                f"Bit-fit recheck failed on {len(bitfit_issue_infos)} solid(s): cutter still cannot fit some corner areas."
            )
        print("No final inlay generated because full miter coverage is required for fit.")
        try:
            if QtGui is not None and show_dialog:
                detail_text = "\n".join(failure_lines[:200]) if failure_lines else "One or more solids failed strict filleting."
                QtGui.QMessageBox.critical(
                    None,
                    "Fillet for CNC Failed",
                    "Strict fillet failed: the requested radius still leaves unfit areas for the cutter.\n"
                    "No final inlay was generated.\n"
                    "See console output and 'Bit-Fit Issues' markers for locations.",
                    QtGui.QMessageBox.Ok,
                )
                try:
                    FreeCAD.Console.PrintError(detail_text + "\n")
                except Exception:
                    pass
        except Exception:
            pass
        return

    if not filleted_shapes:
        print("Failed to create fillet: no valid edge/radius combination found.")
        return

    def _normalize_shapes_for_compound(shape_list):
        normalized = []
        for shape_obj in list(shape_list or []):
            try:
                if not shape_obj or shape_obj.isNull():
                    continue
            except Exception:
                continue
            try:
                solids = list(getattr(shape_obj, "Solids", []) or [])
            except Exception:
                solids = []
            if solids:
                for solid in solids:
                    try:
                        if solid and not solid.isNull():
                            normalized.append(solid)
                    except Exception:
                        continue
            else:
                normalized.append(shape_obj)
        return normalized

    normalized_shapes = _normalize_shapes_for_compound(filleted_shapes)
    if not normalized_shapes:
        print("Failed to create fillet result: no valid output shapes available.")
        return

    if len(normalized_shapes) == 1:
        final_shape = normalized_shapes[0]
    else:
        try:
            final_shape = Part.makeCompound(normalized_shapes)
        except Exception as comp_exc:
            try:
                valid_subset = [s for s in normalized_shapes if _is_valid_shape(s)]
            except Exception:
                valid_subset = list(normalized_shapes)
            if len(valid_subset) > 1:
                try:
                    final_shape = Part.makeCompound(valid_subset)
                except Exception:
                    final_shape = valid_subset[0]
            else:
                final_shape = normalized_shapes[0]
            print(f"Warning: could not compound all solids ({comp_exc}); using best-effort result.")

    try:
        if len(normalized_shapes) <= 1:
            final_shape = final_shape.removeSplitter()
    except Exception:
        pass

    def _safe_object_name(text):
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(text or "").strip())
        cleaned = cleaned.strip("_")
        return cleaned or "final_inlay"

    def _find_by_label(label_text):
        target = str(label_text or "").strip()
        if not target:
            return None
        try:
            for item in (getattr(doc, "Objects", []) or []):
                if str(getattr(item, "Label", "") or "").strip() == target:
                    return item
        except Exception:
            return None
        return None

    source_obj_names = {
        str(getattr(src_obj, "Name", "") or "").strip()
        for src_obj, _, _ in list(source_entries or [])
        if src_obj
    }

    final_obj = _find_by_label(effective_output_label)
    if final_obj and str(getattr(final_obj, "Name", "") or "").strip() in source_obj_names:
        final_obj = None

    if not final_obj:
        candidate_obj = doc.getObject(_safe_object_name(effective_output_label))
        candidate_name = str(getattr(candidate_obj, "Name", "") or "").strip() if candidate_obj else ""
        if candidate_obj and candidate_name not in source_obj_names:
            final_obj = candidate_obj

    if not final_obj:
        final_obj = doc.addObject("Part::Feature", _safe_object_name(effective_output_label))
    final_obj.Shape = final_shape
    final_obj.Label = effective_output_label

    _ensure_property(
        final_obj,
        "App::PropertyFloat",
        "FilletRadiusInch",
        "FilletForCNC",
        "Fillet radius in inches used to create this inlay",
    )
    _ensure_property(
        final_obj,
        "App::PropertyBool",
        "FilletPreserveUnfilleted",
        "FilletForCNC",
        "Whether unfilleted solids were preserved during this run",
    )
    _ensure_property(
        final_obj,
        "App::PropertyBool",
        "FilletAllowSmallerRadiusFallback",
        "FilletForCNC",
        "Whether smaller fallback radius was allowed during this run",
    )
    _ensure_property(
        final_obj,
        "App::PropertyBool",
        "FilletRequireFullCoverage",
        "FilletForCNC",
        "Whether strict full miter coverage was required during this run",
    )
    _ensure_property(
        final_obj,
        "App::PropertyString",
        "FilletSourceLabels",
        "FilletForCNC",
        "Comma-separated source object labels used for this fillet run",
    )
    _ensure_property(
        final_obj,
        "App::PropertyString",
        "FilletSettingsSummary",
        "FilletForCNC",
        "Human-readable summary of fillet settings used for this inlay",
    )

    try:
        final_obj.FilletRadiusInch = float(fillet_radius_inch)
    except Exception:
        pass
    try:
        final_obj.FilletPreserveUnfilleted = bool(preserve_unfilleted)
    except Exception:
        pass
    try:
        final_obj.FilletAllowSmallerRadiusFallback = bool(allow_smaller_radius_fallback)
    except Exception:
        pass
    try:
        final_obj.FilletRequireFullCoverage = bool(require_full_coverage)
    except Exception:
        pass
    try:
        source_label_values = [str(label or "").strip() for _, _, label in list(source_entries or [])]
        source_label_values = [value for value in source_label_values if value]
        final_obj.FilletSourceLabels = ", ".join(source_label_values)
    except Exception:
        pass
    try:
        final_obj.FilletSettingsSummary = (
            f"radius={float(fillet_radius_inch):.4f} in; "
            f"preserve_unfilleted={'yes' if bool(preserve_unfilleted) else 'no'}; "
            f"allow_smaller_fallback={'yes' if bool(allow_smaller_radius_fallback) else 'no'}; "
            f"require_full_coverage={'yes' if bool(require_full_coverage) else 'no'}"
        )
    except Exception:
        pass

    # Do not alter tree visibility of source objects.

    axis_name = "Z"
    if sample_radius is not None:
        print(
            f"Fillet created for CNC on {successful_count}/{len(source_entries)} solid(s) "
            f"using {axis_name}-parallel edges at radius {sample_radius / 25.4:.4f} in."
        )
    else:
        print(f"Fillet created for CNC on {successful_count}/{len(source_entries)} solid(s).")
    if no_fillet_needed_count > 0:
        print(
            f"No fillet needed for {no_fillet_needed_count} solid(s) "
            f"(already bit-fit on Z-axis seams at requested radius): {', '.join(str(name) for name in no_fillet_needed_labels[:20])}"
        )
    if bitfit_unfit_total <= 0:
        print(
            f"Bit-fit check: no unfit sharp corner areas found for tool diameter "
            f"{(2.0 * float(fillet_radius) / 25.4):.4f} in."
        )
    if failed_labels:
        print(f"Fillet skipped for: {', '.join(str(name) for name in failed_labels)}")
        if preserved_unfilleted_count > 0:
            print(f"Retained {preserved_unfilleted_count} unfilleted solid(s) unchanged in final_inlay.")
        for name, dbg, tried in failed_details:
            try:
                print(
                    "Fillet debug for '{name}': edges={edges}, linear={linear}, Z={z}, X={x}, Y={y}, "
                    "minEdge={min_edge:.4f} mm, triedR(mm)={radii}".format(
                        name=name,
                        edges=int(dbg.get("total_edges", 0)),
                        linear=int(dbg.get("linear_edges", 0)),
                        z=int(dbg.get("z_parallel", 0)),
                        x=int(dbg.get("x_parallel", 0)),
                        y=int(dbg.get("y_parallel", 0)),
                        min_edge=float(dbg.get("min_edge_len_mm", 0.0) or 0.0),
                        radii=", ".join(f"{float(v):.3f}" for v in (tried or [])),
                    )
                )
            except Exception:
                pass

    # Recompute document to reflect changes
    doc.recompute()


def prepare_for_inlay():
    doc = App.ActiveDocument
    selection = Gui.Selection.getSelection()
    if not selection:
        print("No object selected. Please select an object.")
        return
    fillet_for_cnc()


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
        for selected_tc in selected_tcs:
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
            for selected_tc in selected_tcs:
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

                try:
                    cmds = getattr(getattr(pocket_op, "Path", None), "Commands", None)
                    if not cmds or len(cmds) == 0:
                        print(f"Pocket path failed for '{getattr(pocket_op, 'Label', pocket_op.Name)}'.")
                        continue
                except Exception:
                    pass

                lower_xy_pocket_count += 1
                created_ops.append(pocket_op)

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

    def _non_top_face_subnames(model_obj):
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

        middle_ids = [idx for idx, z in xy_faces if (z < max_z - tol and z > min_z + tol)]
        return [f"Face{i}" for i in middle_ids]

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

        face_names = _non_top_face_subnames(model_obj)
        if not face_names:
            print("No interior XY-plane faces found for Pocket operation.")
            return []

        try:
            created_ops = []
            roughing_undersize_mm = _inch_to_mm(max(0.0, float(roughing_undersize_inch)))
            glue_oversize_mm = _inch_to_mm(max(0.0, float(glue_oversize_inch)))
            selected_tcs = _selected_tool_controllers(job_obj, selected_tool_names)

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

            ops_with_cut_motion = 0
            smallest_bit_no_cut_label = None
            total_passes = len(selected_tcs)
            kept_pass_count = 0
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
                except Exception:
                    pass

                if selected_tc and hasattr(pocket_op, "ToolController"):
                    try:
                        pocket_op.ToolController = selected_tc
                    except Exception:
                        pass

                try:
                    bit_part = _bit_name_from_job_or_op(pocket_op, job_obj)
                    pocket_op.Label = _unique_label(f"PocketShape_{bit_part}")
                except Exception:
                    pass

                pocket_op.Base = [(model_obj, face_names)]

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

                is_first_pass = (op_index == 0)
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
                                    f"Pocket pass {op_index + 1}/{total_passes} ({getattr(pocket_op, 'Label', 'PocketShape')}): "
                                    f"Rest={rest_enabled}, ExtraOffset={roughing_undersize_mm / 25.4:.4f} in (roughing/undersize)."
                                )
                            except Exception:
                                pass
                        else:
                            pocket_op.ExtraOffset = f"{-glue_oversize_mm} mm"
                            try:
                                print(
                                    f"Pocket pass {op_index + 1}/{total_passes} ({getattr(pocket_op, 'Label', 'PocketShape')}): "
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

                skip_this_op = False
                try:
                    if (
                        bool(skip_large_if_under_minutes)
                        and is_first_pass
                        and (not is_final_pass)
                        and has_cut_motion
                    ):
                        estimated_minutes = _estimate_cut_minutes_for_op(pocket_op, selected_tc)
                        threshold_minutes = max(0.0, float(skip_large_minutes_threshold))
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
                print(
                    f"Created {len(created_ops)} Pocket operation(s) on {len(face_names)} interior XY-plane face(s)."
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

    