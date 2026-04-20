"""Solid cutter-based knurl macro for FreeCAD.

This version creates a real boolean-cut knurl on a cylindrical face or
cylindrical solid. It is designed to stay reasonably fast on simple parts
by generating box cutters and applying them in batches.
"""

import math

import FreeCAD as App
try:
    import FreeCADGui as Gui
except Exception:
    Gui = None
import Part

try:
    from PySide import QtGui
except Exception:
    try:
        from PySide2 import QtWidgets as QtGui
    except Exception:
        QtGui = None


PREFS = App.ParamGet("User parameter:Plugins/KnurlTool")
MM_PER_IN = 25.4


def _progress(message):
    try:
        App.Console.PrintMessage("[Knurl] {}\n".format(message))
    except Exception:
        pass


def _in_to_mm(value):
    return float(value) * MM_PER_IN


def _clamp(value, low, high):
    return max(low, min(high, float(value)))


def _normalize(vec, fallback=None):
    v = App.Vector(vec)
    if v.Length < 1e-9:
        return App.Vector(fallback or App.Vector(1, 0, 0))
    v.normalize()
    return v


def _vector_rotated(vector, axis, angle_deg):
    rotation = App.Rotation(_normalize(axis, App.Vector(0, 0, 1)), float(angle_deg))
    return rotation.multVec(App.Vector(vector))


def _shape_from_object(obj):
    if hasattr(obj, "Shape") and not obj.Shape.isNull():
        return obj.Shape
    raise ValueError("Selected object does not have a valid shape.")


def _largest_cylindrical_face(shape, preferred_face=None):
    if preferred_face is not None:
        surf = getattr(preferred_face, "Surface", None)
        if surf and getattr(surf, "TypeId", "") == "Part::GeomCylinder":
            return preferred_face

    best = None
    best_area = -1.0
    for face in getattr(shape, "Faces", []) or []:
        surf = getattr(face, "Surface", None)
        if surf and getattr(surf, "TypeId", "") == "Part::GeomCylinder":
            area = float(getattr(face, "Area", 0.0) or 0.0)
            if area > best_area:
                best = face
                best_area = area
    return best


def _best_cylindrical_face_for_edge(shape, edge):
    curve = getattr(edge, "Curve", None)
    if curve is None or getattr(curve, "TypeId", "") != "Part::GeomCircle":
        return None

    edge_axis = _normalize(getattr(curve, "Axis", App.Vector(0, 0, 1)), App.Vector(0, 0, 1))
    edge_radius = float(getattr(curve, "Radius", 0.0) or 0.0)

    best = None
    best_score = None
    for face in getattr(shape, "Faces", []) or []:
        surf = getattr(face, "Surface", None)
        if surf is None or getattr(surf, "TypeId", "") != "Part::GeomCylinder":
            continue
        face_axis = _normalize(getattr(surf, "Axis", App.Vector(0, 0, 1)), App.Vector(0, 0, 1))
        axis_score = abs(face_axis.dot(edge_axis))
        radius_score = abs(float(getattr(surf, "Radius", 0.0) or 0.0) - edge_radius)
        score = (1.0 - axis_score) * 1000.0 + radius_score
        if best_score is None or score < best_score:
            best = face
            best_score = score
    return best


def _best_circular_edge_from_face(face):
    best = None
    best_radius = -1.0
    for edge in getattr(face, "Edges", []) or []:
        curve = getattr(edge, "Curve", None)
        if curve is None or getattr(curve, "TypeId", "") != "Part::GeomCircle":
            continue
        radius = float(getattr(curve, "Radius", 0.0) or 0.0)
        if radius > best_radius:
            best = edge
            best_radius = radius
    return best


def _resolve_target(target_obj=None, target_face=None, target_edge=None):
    if target_obj is not None:
        shape = _shape_from_object(target_obj)
        edge = target_edge
        preferred_face = target_face
        if edge is None and preferred_face is not None:
            surf = getattr(preferred_face, "Surface", None)
            if getattr(surf, "TypeId", "") != "Part::GeomCylinder":
                edge = _best_circular_edge_from_face(preferred_face)
        face = None
        if edge is not None:
            face = _best_cylindrical_face_for_edge(shape, edge)
        if face is None:
            face = _largest_cylindrical_face(shape, preferred_face=preferred_face)
        if face is None:
            raise ValueError("Target object has no cylindrical face.")
        return target_obj, shape, face, edge

    if Gui is None:
        raise ValueError("No GUI selection is available.")

    sel_ex = list(Gui.Selection.getSelectionEx() or [])
    if not sel_ex:
        raise ValueError("Select a cylindrical face, circular face, circular edge, or cylinder-like solid first.")

    pick = sel_ex[0]
    obj = pick.Object
    if obj is None:
        raise ValueError("Could not resolve the selected object.")

    explicit_face = None
    explicit_edge = None
    for sub in getattr(pick, "SubObjects", []) or []:
        surf = getattr(sub, "Surface", None)
        if surf and getattr(surf, "TypeId", "") == "Part::GeomCylinder":
            explicit_face = sub
            break
        if explicit_edge is None:
            curve = getattr(sub, "Curve", None)
            if curve and getattr(curve, "TypeId", "") == "Part::GeomCircle":
                explicit_edge = sub
                continue
        if explicit_edge is None and hasattr(sub, "Edges"):
            face_edge = _best_circular_edge_from_face(sub)
            if face_edge is not None:
                explicit_edge = face_edge

    shape = _shape_from_object(obj)
    if explicit_face is not None:
        face = _largest_cylindrical_face(shape, preferred_face=explicit_face)
    elif explicit_edge is not None:
        face = _best_cylindrical_face_for_edge(shape, explicit_edge)
    else:
        face = _largest_cylindrical_face(shape)

    if face is None:
        raise ValueError("No cylindrical face was found.")
    return obj, shape, face, explicit_edge


def _cylinder_frame(face):
    surf = face.Surface
    axis = _normalize(surf.Axis, App.Vector(0, 0, 1))
    center = App.Vector(surf.Center)
    radius = float(surf.Radius)

    projections = []
    radial_seed = None
    for vertex in getattr(face, "Vertexes", []) or []:
        pt = App.Vector(vertex.Point)
        proj = axis.dot(pt - center)
        projections.append(proj)
        axis_pt = center + axis * proj
        radial = pt - axis_pt
        if radial.Length > 1e-7 and radial_seed is None:
            radial_seed = radial

    if not projections:
        raise ValueError("Could not determine the cylinder bounds.")

    if radial_seed is None:
        radial_seed = App.Vector(radius, 0, 0)
        if abs(axis.dot(radial_seed)) > 0.95:
            radial_seed = App.Vector(0, radius, 0)

    radial_dir = _normalize(radial_seed, App.Vector(1, 0, 0))
    tangent_dir = _normalize(axis.cross(radial_dir), App.Vector(0, 1, 0))
    radial_dir = _normalize(tangent_dir.cross(axis), radial_dir)

    z_min = min(projections)
    z_max = max(projections)
    height = z_max - z_min
    if height <= 1e-6:
        raise ValueError("The cylindrical face height is too small.")

    return {
        "axis": axis,
        "center": center,
        "radius": radius,
        "height": height,
        "radial": radial_dir,
        "tangent": tangent_dir,
        "z_min": z_min,
        "z_max": z_max,
    }


def _transform_shape(shape, origin, x_axis, y_axis, z_axis):
    m = App.Matrix()
    m.A11 = x_axis.x
    m.A12 = y_axis.x
    m.A13 = z_axis.x
    m.A14 = origin.x
    m.A21 = x_axis.y
    m.A22 = y_axis.y
    m.A23 = z_axis.y
    m.A24 = origin.y
    m.A31 = x_axis.z
    m.A32 = y_axis.z
    m.A33 = z_axis.z
    m.A34 = origin.z
    m.A44 = 1.0
    return shape.transformGeometry(m)


def _band_limits(frame, edge=None, band_width_mm=None):
    z0 = float(frame["z_min"])
    z1 = float(frame["z_max"])
    surface_height = max(0.0, z1 - z0)
    if surface_height <= 1e-9:
        return z0, z1

    requested_band_mm = max(0.5, float(band_width_mm or 0.0))
    clamped_band_mm = min(requested_band_mm, surface_height)

    if edge is None:
        return z0, z1

    curve = getattr(edge, "Curve", None)
    if curve is None or getattr(curve, "TypeId", "") != "Part::GeomCircle":
        return z0, z1

    edge_center = App.Vector(getattr(curve, "Center", frame["center"]))
    z_center = frame["axis"].dot(edge_center - frame["center"])

    dist_to_min = abs(z_center - z0)
    dist_to_max = abs(z1 - z_center)
    edge_tolerance = max(0.25, min(surface_height * 0.10, clamped_band_mm * 0.50))

    # For the common case of selecting the top or bottom circular edge of the
    # cylinder, treat Band Width as a distance measured inward from that edge.
    # This avoids the confusing "half the requested width" result.
    if dist_to_min <= edge_tolerance and dist_to_min <= dist_to_max:
        return z0, min(z1, z0 + clamped_band_mm)
    if dist_to_max <= edge_tolerance and dist_to_max < dist_to_min:
        return max(z0, z1 - clamped_band_mm), z1

    # For an interior circular edge, center the band on that location.
    half = clamped_band_mm * 0.5
    return max(z0, z_center - half), min(z1, z_center + half)


def _profile_width_factor(profile_name):
    profile = str(profile_name or "sharp").strip().lower()
    if profile == "flat":
        return 0.62
    if profile == "rounded":
        return 0.52
    return 0.42


def _make_cutter(frame, theta_deg, z_center, cutter_length_mm, pitch_mm, depth_mm, angle_deg, handedness, tooth_profile):
    axis = frame["axis"]
    radial = _vector_rotated(frame["radial"], axis, theta_deg)
    tangent = _normalize(axis.cross(radial), frame["tangent"])

    if handedness == 0 or abs(float(angle_deg)) < 1e-6:
        groove_dir = App.Vector(axis)
    else:
        radians = math.radians(abs(float(angle_deg)))
        groove_dir = _normalize(axis * math.cos(radians) + tangent * math.sin(radians) * float(handedness), axis)

    across = _normalize(groove_dir.cross(radial), tangent)

    groove_width = max(0.2, float(pitch_mm) * _profile_width_factor(tooth_profile))
    radial_depth = max(0.2, float(depth_mm) * 1.35 + 0.10)
    cutter_length = max(float(cutter_length_mm), groove_width * 2.0, 1.0)

    base_box = Part.makeBox(
        radial_depth,
        groove_width,
        cutter_length,
        App.Vector(-radial_depth * 0.5, -groove_width * 0.5, -cutter_length * 0.5),
    )

    origin = frame["center"] + axis * float(z_center) + radial * (frame["radius"] - radial_depth * 0.5 + 0.02)
    return _transform_shape(base_box, origin, radial, across, groove_dir)


def _pattern_hands(pattern_type):
    pattern = str(pattern_type or "diamond").strip().lower()
    if pattern in ("diamond", "cross", "crosshatch"):
        return [-1, 1]
    if pattern in ("left", "left-hand"):
        return [-1]
    if pattern in ("straight",):
        return [0]
    return [1]


def _build_cutters(frame, z0, z1, pitch_mm, depth_mm, angle_deg, pattern_type, tooth_profile):
    band_height = float(z1) - float(z0)
    if band_height <= 0.0:
        raise ValueError("Knurl band height must be positive.")

    circumference = 2.0 * math.pi * float(frame["radius"])
    groove_width = max(0.2, float(pitch_mm) * _profile_width_factor(tooth_profile))
    groove_spacing = max(float(pitch_mm), groove_width * 1.15)
    axial_step = max(0.75, groove_width * 0.90)
    cutter_length_mm = max(groove_width * 3.0, groove_spacing * 1.8)

    families = []
    hands = _pattern_hands(pattern_type)
    effective_angle = 0.0 if hands == [0] else _clamp(angle_deg, 10.0, 75.0)
    slope = math.tan(math.radians(effective_angle)) if abs(effective_angle) > 1e-6 else 0.0

    groove_count = int(max(6, min(180, math.ceil(circumference / groove_spacing))))
    axial_count = int(max(1, math.ceil(band_height / axial_step))) + 1

    for family_index, handedness in enumerate(hands):
        family = []
        phase = 0.0 if family_index == 0 else groove_spacing * 0.5
        for groove_index in range(groove_count):
            groove_offset = phase + groove_index * groove_spacing
            for axial_index in range(axial_count):
                z_center = float(z0) + ((axial_index + 0.5) * band_height / float(axial_count))
                circum_pos = groove_offset
                if handedness != 0 and slope > 1e-6:
                    circum_pos += (z_center - float(z0)) * slope * float(handedness)
                theta = (circum_pos / circumference) * 360.0
                family.append(
                    _make_cutter(
                        frame=frame,
                        theta_deg=theta,
                        z_center=z_center,
                        cutter_length_mm=cutter_length_mm,
                        pitch_mm=pitch_mm,
                        depth_mm=depth_mm,
                        angle_deg=effective_angle,
                        handedness=handedness,
                        tooth_profile=tooth_profile,
                    )
                )
        families.append(family)
    return families


def _cut_in_batches(shape, cutters, batch_size=20):
    result = shape.copy()
    total = len(cutters)
    for start in range(0, total, max(1, int(batch_size))):
        batch = cutters[start : start + max(1, int(batch_size))]
        _progress("Applying cuts {}-{} of {}".format(start + 1, start + len(batch), total))
        compound = Part.makeCompound(batch)
        result = result.cut(compound)
        try:
            result = result.removeSplitter()
        except Exception:
            pass
    return result


def _show_debug_cutters(doc, base_name, cutters, color):
    if not cutters:
        return None
    feature = doc.addObject("Part::Feature", base_name)
    feature.Shape = Part.makeCompound(cutters)
    try:
        feature.ViewObject.ShapeColor = color
        feature.ViewObject.Transparency = 75
    except Exception:
        pass
    return feature


def create_knurl(
    pitch_in=0.08,
    depth_in=0.02,
    angle_deg=35.0,
    band_width_in=0.50,
    pattern_type="diamond",
    tooth_profile="sharp",
    output_name="Knurled",
    hide_original=True,
    batch_size=20,
    debug=False,
    starts=None,
    target_obj=None,
    target_face=None,
    target_edge=None,
):
    doc = App.ActiveDocument
    if doc is None:
        raise ValueError("No active document.")

    _progress("Resolving selected cylinder")
    obj, shape, face, edge = _resolve_target(target_obj=target_obj, target_face=target_face, target_edge=target_edge)
    frame = _cylinder_frame(face)

    pitch_mm = max(_in_to_mm(pitch_in), 0.30)
    depth_mm = max(_in_to_mm(depth_in), 0.08)
    band_width_mm = max(_in_to_mm(band_width_in), 1.0)
    pattern_type = str(pattern_type or "diamond").strip().lower()
    tooth_profile = str(tooth_profile or "sharp").strip().lower()
    base_name = str(output_name or "Knurled").strip() or "Knurled"

    z0, z1 = _band_limits(frame, edge=edge, band_width_mm=band_width_mm)
    band_height = z1 - z0
    if band_height <= 0.5:
        raise ValueError("The computed knurl band is too small.")

    if edge is not None:
        _progress("Using edge band height {:.3f} in (clamped to surface as needed)".format(band_height / MM_PER_IN))
    else:
        _progress("Using full cylindrical face height {:.3f} in".format(band_height / MM_PER_IN))

    _progress("Building cutter pattern")
    families = _build_cutters(
        frame=frame,
        z0=z0,
        z1=z1,
        pitch_mm=pitch_mm,
        depth_mm=depth_mm,
        angle_deg=angle_deg,
        pattern_type=pattern_type,
        tooth_profile=tooth_profile,
    )

    _progress("Prepared {} cutter families / {} total cutters".format(len(families), sum(len(f) for f in families)))

    if debug:
        for idx, family in enumerate(families):
            color = (1.0, 0.2, 0.2) if idx == 0 else (0.2, 0.4, 1.0)
            _show_debug_cutters(doc, "{}_Debug_{}".format(base_name, idx + 1), family, color)

    result_shape = shape.copy()
    for idx, family in enumerate(families):
        _progress("Cutting family {} of {}".format(idx + 1, len(families)))
        result_shape = _cut_in_batches(result_shape, family, batch_size=batch_size)

    try:
        result_shape = result_shape.removeSplitter()
    except Exception:
        pass

    if result_shape.isNull():
        raise ValueError("Knurl generation failed and produced an empty result.")

    result_obj = doc.addObject("Part::Feature", "{}_{}".format(base_name, obj.Name))
    result_obj.Label = "{} {}".format(base_name, getattr(obj, "Label", obj.Name))
    result_obj.Shape = result_shape

    try:
        if hide_original and hasattr(obj, "ViewObject"):
            obj.ViewObject.Visibility = False
    except Exception:
        pass

    doc.recompute()

    if Gui is not None:
        try:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, result_obj.Name)
        except Exception:
            pass

    App.Console.PrintMessage("Knurl created: {}\n".format(result_obj.Label))
    return result_obj


def create_knurl_guides(**kwargs):
    return create_knurl(**kwargs)


class KnurlTaskPanel:
    def __init__(self):
        if QtGui is None:
            raise RuntimeError("Qt is not available.")

        self.form = QtGui.QWidget()
        self.form.setWindowTitle("Solid Knurl")
        layout = QtGui.QFormLayout(self.form)

        self.output_name = QtGui.QLineEdit(PREFS.GetString("output_name", "Knurled"))

        self.pattern = QtGui.QComboBox()
        self.pattern.addItems(["diamond", "right", "left", "straight"])
        saved_pattern = PREFS.GetString("pattern", "diamond") or "diamond"
        self.pattern.setCurrentIndex(max(0, self.pattern.findText(saved_pattern)))

        self.profile = QtGui.QComboBox()
        self.profile.addItems(["sharp", "rounded", "flat"])
        saved_profile = PREFS.GetString("profile", "sharp") or "sharp"
        self.profile.setCurrentIndex(max(0, self.profile.findText(saved_profile)))

        self.pitch = QtGui.QDoubleSpinBox()
        self.pitch.setDecimals(4)
        self.pitch.setRange(0.01, 1.00)
        self.pitch.setSingleStep(0.01)
        self.pitch.setSuffix(" in")
        self.pitch.setValue(PREFS.GetFloat("pitch_in", 0.08))

        self.depth = QtGui.QDoubleSpinBox()
        self.depth.setDecimals(4)
        self.depth.setRange(0.001, 0.250)
        self.depth.setSingleStep(0.002)
        self.depth.setSuffix(" in")
        self.depth.setValue(PREFS.GetFloat("depth_in", 0.02))

        self.angle = QtGui.QDoubleSpinBox()
        self.angle.setDecimals(1)
        self.angle.setRange(0.0, 75.0)
        self.angle.setSingleStep(1.0)
        self.angle.setSuffix("°")
        self.angle.setValue(PREFS.GetFloat("angle_deg", 35.0))

        self.band_width = QtGui.QDoubleSpinBox()
        self.band_width.setDecimals(3)
        self.band_width.setRange(0.05, 12.00)
        self.band_width.setSingleStep(0.05)
        self.band_width.setSuffix(" in")
        self.band_width.setToolTip("For edge selections, band width grows inward from the selected edge and is limited to the face height.")
        self.band_width.setValue(PREFS.GetFloat("band_width_in", 0.50))

        self.hide_original = QtGui.QCheckBox("Hide original object")
        self.hide_original.setChecked(PREFS.GetBool("hide_original", True))

        self.debug = QtGui.QCheckBox("Show cutter debug geometry")
        self.debug.setChecked(PREFS.GetBool("debug_mode", False))

        note = QtGui.QLabel(
            "Select a cylindrical face for full coverage, or a circular edge/end face to start a limited knurl band from that end.\n"
            "Band width is automatically capped to the surface height. Start with coarse pitch and modest depth."
        )
        note.setWordWrap(True)

        layout.addRow("Output name", self.output_name)
        layout.addRow("Pattern", self.pattern)
        layout.addRow("Tooth profile", self.profile)
        layout.addRow("Pitch", self.pitch)
        layout.addRow("Depth", self.depth)
        layout.addRow("Angle", self.angle)
        layout.addRow("Band width", self.band_width)
        layout.addRow("", self.hide_original)
        layout.addRow("", self.debug)
        layout.addRow("", note)

    def getStandardButtons(self):
        return QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel | QtGui.QDialogButtonBox.Apply

    def clicked(self, button):
        if button == QtGui.QDialogButtonBox.Apply:
            return self._run(close_after=False)
        return False

    def accept(self):
        return self._run(close_after=True)

    def reject(self):
        if Gui is not None:
            try:
                Gui.Control.closeDialog()
            except Exception:
                pass
        return True

    def _run(self, close_after):
        PREFS.SetString("output_name", self.output_name.text().strip() or "Knurled")
        PREFS.SetString("pattern", self.pattern.currentText())
        PREFS.SetString("profile", self.profile.currentText())
        PREFS.SetFloat("pitch_in", float(self.pitch.value()))
        PREFS.SetFloat("depth_in", float(self.depth.value()))
        PREFS.SetFloat("angle_deg", float(self.angle.value()))
        PREFS.SetFloat("band_width_in", float(self.band_width.value()))
        PREFS.SetBool("hide_original", bool(self.hide_original.isChecked()))
        PREFS.SetBool("debug_mode", bool(self.debug.isChecked()))

        create_knurl(
            pitch_in=float(self.pitch.value()),
            depth_in=float(self.depth.value()),
            angle_deg=float(self.angle.value()),
            band_width_in=float(self.band_width.value()),
            pattern_type=str(self.pattern.currentText()),
            tooth_profile=str(self.profile.currentText()),
            output_name=str(self.output_name.text().strip() or "Knurled"),
            hide_original=bool(self.hide_original.isChecked()),
            debug=bool(self.debug.isChecked()),
        )

        if close_after and Gui is not None:
            try:
                Gui.Control.closeDialog()
            except Exception:
                pass
        return True


def show_knurl_dialog():
    if Gui is None or QtGui is None:
        raise RuntimeError("FreeCAD GUI is required to show the task panel.")
    Gui.Control.showDialog(KnurlTaskPanel())


if App.GuiUp and __name__ == "__main__":
    show_knurl_dialog()
