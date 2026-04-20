#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import FreeCAD as App
import FreeCADGui as Gui
import math

try:
    from PySide import QtGui, QtCore
except Exception:
    QtGui = None
    QtCore = None

def fillet_for_cnc(
    target=None,
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

                note = QtGui.QLabel("Fillet targets seam edges parallel to the part's extrusion axis.")

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

    def _normalized_axis(vec_obj, fallback=None):
        fallback_vec = App.Vector(fallback or App.Vector(0, 0, 1))
        try:
            axis = App.Vector(vec_obj)
        except Exception:
            try:
                axis = App.Vector(
                    float(getattr(vec_obj, "x", 0.0)),
                    float(getattr(vec_obj, "y", 0.0)),
                    float(getattr(vec_obj, "z", 0.0)),
                )
            except Exception:
                axis = App.Vector(fallback_vec)
        if float(getattr(axis, "Length", 0.0) or 0.0) <= 1e-9:
            axis = App.Vector(fallback_vec)
        try:
            axis.normalize()
        except Exception:
            axis = App.Vector(fallback_vec)
            try:
                axis.normalize()
            except Exception:
                return App.Vector(0, 0, 1)
        try:
            if axis.z < 0.0 or (abs(axis.z) < 1e-9 and axis.y < 0.0) or (abs(axis.z) < 1e-9 and abs(axis.y) < 1e-9 and axis.x < 0.0):
                axis = App.Vector(-axis.x, -axis.y, -axis.z)
        except Exception:
            pass
        return axis

    def _safe_face_normal(face_obj):
        try:
            com = face_obj.CenterOfMass
            u, v = face_obj.Surface.parameter(com)
            n = face_obj.normalAt(u, v)
            if float(getattr(n, "Length", 0.0) or 0.0) <= 1e-9:
                return None
            return _normalized_axis(n)
        except Exception:
            return None

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
        return _normalized_axis(axes[0][1])

    def _edges_parallel_to_axis(shape_obj, axis, tol=1e-4):
        axis = _normalized_axis(axis)
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
        axis_vec = _normalized_axis(target_axis if target_axis is not None else _preferred_axis_for_shape(shape_obj))
        if edge_candidates is not None:
            try:
                z_edges = list(edge_candidates or [])
            except Exception:
                z_edges = []
        else:
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
                return bool(n.isParallel(axis_vec, 1e-4))
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
                return _normalized_axis(n)
            except Exception:
                return None

        def _is_vertical_side_face(face_obj):
            n = _face_normal(face_obj)
            if n is None:
                return False
            try:
                return not bool(n.isParallel(axis_vec, 1e-4))
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

    selection = []
    if target is not None:
        if isinstance(target, (list, tuple)):
            selection = [obj for obj in target if obj is not None]
        else:
            selection = [target]
    elif Gui is not None:
        try:
            selection = list(Gui.Selection.getSelection() or [])
        except Exception:
            selection = []
    if not selection:
        print("No object selected. Please select an object.")
        return None

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

    def _axis_key(vec_obj):
        vec = _normalized_axis(vec_obj)
        return (round(float(vec.x), 4), round(float(vec.y), 4), round(float(vec.z), 4))

    def _preferred_axis_from_object(obj):
        if not obj:
            return None
        for prop_name in ("PreferredFilletAxis", "FilletPreferredAxis"):
            if not hasattr(obj, prop_name):
                continue
            try:
                axis = _normalized_axis(getattr(obj, prop_name))
                if float(getattr(axis, "Length", 0.0) or 0.0) > 1e-9:
                    return axis
            except Exception:
                continue
        return None

    def _candidate_axes_for_shape(shape_obj, source_obj=None):
        candidates = []
        seen = set()

        def _add(axis_obj):
            axis_norm = _normalized_axis(axis_obj)
            key = _axis_key(axis_norm)
            if key in seen:
                return
            seen.add(key)
            candidates.append(axis_norm)

        explicit_axis = _preferred_axis_from_object(source_obj)
        if explicit_axis is not None:
            _add(explicit_axis)

        _add(_axis_vector_for_bbox(shape_obj))

        try:
            planar_faces = []
            for face in list(getattr(shape_obj, "Faces", []) or []):
                surf = getattr(face, "Surface", None)
                if getattr(surf, "TypeId", "") != "Part::GeomPlane":
                    continue
                normal = _safe_face_normal(face)
                if normal is None:
                    continue
                planar_faces.append((float(getattr(face, "Area", 0.0) or 0.0), normal))
            planar_faces.sort(key=lambda item: item[0], reverse=True)
            for _area, normal in planar_faces[:8]:
                _add(normal)
        except Exception:
            pass

        try:
            direction_scores = {}
            direction_axes = {}
            for edge in list(getattr(shape_obj, "Edges", []) or []):
                curve = getattr(edge, "Curve", None)
                if getattr(curve, "TypeId", "") != "Part::GeomLine":
                    continue
                try:
                    direction = curve.Direction
                except Exception:
                    verts = list(getattr(edge, "Vertexes", []) or [])
                    if len(verts) != 2:
                        continue
                    direction = verts[1].Point.sub(verts[0].Point)
                axis_norm = _normalized_axis(direction)
                key = _axis_key(axis_norm)
                direction_axes[key] = axis_norm
                direction_scores[key] = direction_scores.get(key, 0.0) + float(getattr(edge, "Length", 0.0) or 0.0)
            for key, _score in sorted(direction_scores.items(), key=lambda item: item[1], reverse=True):
                _add(direction_axes[key])
        except Exception:
            pass

        _add(App.Vector(0, 0, 1))
        _add(App.Vector(1, 0, 0))
        _add(App.Vector(0, 1, 0))
        return candidates or [App.Vector(0, 0, 1)]

    def _preferred_axis_for_shape(shape_obj, source_obj=None):
        explicit_axis = _preferred_axis_from_object(source_obj)
        if explicit_axis is not None:
            return explicit_axis
        bbox_axis = _axis_vector_for_bbox(shape_obj)
        ranked = []
        for axis in _candidate_axes_for_shape(shape_obj, source_obj=source_obj):
            try:
                axis_edges = list(_edges_parallel_to_axis(shape_obj, axis) or [])
            except Exception:
                axis_edges = []
            total_length = sum(float(getattr(edge, "Length", 0.0) or 0.0) for edge in axis_edges)
            try:
                alignment = abs(_normalized_axis(axis).dot(_normalized_axis(bbox_axis)))
            except Exception:
                alignment = 0.0
            ranked.append((len(axis_edges), total_length, alignment, axis))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        if ranked and ranked[0][0] > 0:
            return ranked[0][3]
        return bbox_axis

    def _axis_order_for_shape(shape_obj, source_obj=None):
        preferred = _preferred_axis_for_shape(shape_obj, source_obj=source_obj)
        ordered = [preferred]
        for axis in _candidate_axes_for_shape(shape_obj, source_obj=source_obj):
            if not any(_axis_key(axis) == _axis_key(existing) for existing in ordered):
                ordered.append(axis)
        return ordered

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

    def _refine_z_edges_with_radii(shape_obj, radii_mm, target_axis=None):
        working = shape_obj
        updates = 0
        axis_ref = _normalized_axis(target_axis if target_axis is not None else _preferred_axis_for_shape(shape_obj))
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
                    edges_now = _edges_parallel_to_axis(working, axis_ref)
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
        axis_order = _axis_order_for_shape(source_shape, source_obj=source_obj)
        preferred_axis = axis_order[0] if axis_order else App.Vector(0, 0, 1)
        try:
            print(
                f"Fillet axis for '{source_label}': "
                f"({float(preferred_axis.x):.3f}, {float(preferred_axis.y):.3f}, {float(preferred_axis.z):.3f})"
            )
        except Exception:
            pass

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

        unfit_z_edges = list(_bit_fit_unfit_z_edges(source_shape, fillet_radius, target_axis=preferred_axis, edge_candidates=target_edges) or [])
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
                initial_z_edges = _edges_parallel_to_axis(source_shape, preferred_axis)
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
                            current_z_edges = _edges_parallel_to_axis(working, preferred_axis)
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
                    used_axis = preferred_axis
                    try:
                        total_z_edges = len(initial_z_edges)
                        print(
                            f"Axis-edge fillet coverage for '{source_label}': "
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

        post_axis_order = _axis_order_for_shape(fillet, source_obj=source_obj)
        post_target_edges = _collect_target_edges(fillet, post_axis_order)
        remaining_unfit = list(_bit_fit_unfit_z_edges(fillet, fillet_radius, target_axis=used_axis or preferred_axis, edge_candidates=post_target_edges) or [])
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
                    target_axis=used_axis or preferred_axis,
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
                    App.Console.PrintError(detail_text + "\n")
                except Exception:
                    pass
        except Exception:
            pass
        return

    if not filleted_shapes:
        print("Failed to create fillet: no valid edge/radius combination found.")
        return None

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
    _ensure_property(
        final_obj,
        "App::PropertyVector",
        "PreferredFilletAxis",
        "FilletForCNC",
        "Preferred seam axis for CNC fillet detection",
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
        preferred_axis_value = _preferred_axis_from_object(source_entries[0][0]) if source_entries else None
        if preferred_axis_value is not None:
            final_obj.PreferredFilletAxis = preferred_axis_value
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

    axis_name = "part extrusion axis"
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
            f"(already bit-fit on the part-axis seams at requested radius): {', '.join(str(name) for name in no_fillet_needed_labels[:20])}"
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
    return final_obj


def prepare_for_inlay():
    doc = App.ActiveDocument
    selection = Gui.Selection.getSelection()
    if not selection:
        print("No object selected. Please select an object.")
        return
    fillet_for_cnc()


