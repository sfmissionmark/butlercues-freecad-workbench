"""Create a flat image template sized from a selected cylindrical solid.

Workflow:
- select a cylindrical solid, cylindrical face, circular edge, or circular end face
- run the macro
- pick an image
- a flat plane, image plane, and tracing sketch are created using the selected
  cylinder dimensions (circumference x cylinder height)
"""

import math
import os

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


PREFS = App.ParamGet("User parameter:Plugins/ImageWrapTemplate")
MM_PER_IN = 25.4
TOP_CLEARANCE_MM = 0.001 * MM_PER_IN
WRAP_CACHE_DIR = os.path.join(os.path.expanduser("~/Library/Application Support/FreeCAD/v1-2/Macro"), "ImageWrapCache")


def _progress(message):
    try:
        App.Console.PrintMessage("[WrapTemplate] {}\n".format(message))
    except Exception:
        pass


def _normalize(vec, fallback=None):
    v = App.Vector(vec)
    if v.Length < 1e-9:
        return App.Vector(fallback or App.Vector(1, 0, 0))
    v.normalize()
    return v


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
            raise ValueError("Selected object has no cylindrical face.")
        return target_obj, shape, face, edge

    if Gui is None:
        raise ValueError("FreeCAD GUI selection is required.")

    sel_ex = list(Gui.Selection.getSelectionEx() or [])
    if not sel_ex:
        raise ValueError("Select a cylindrical solid, face, circular face, or circular edge first.")

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
            explicit_edge = _best_circular_edge_from_face(sub)

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
    for vertex in getattr(face, "Vertexes", []) or []:
        pt = App.Vector(vertex.Point)
        proj = axis.dot(pt - center)
        projections.append(proj)

    if not projections:
        raise ValueError("Could not determine the cylinder bounds.")

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
        "z_min": z_min,
        "z_max": z_max,
        "circumference": 2.0 * math.pi * radius,
    }


def _next_name(doc, base_name):
    if not doc.getObject(base_name):
        return base_name
    index = 2
    while doc.getObject("{}_{}".format(base_name, index)):
        index += 1
    return "{}_{}".format(base_name, index)


def _image_aspect_ratio(image_file):
    if QtGui is None:
        return 1.0
    try:
        img = QtGui.QImage(image_file)
        if img.width() > 0 and img.height() > 0:
            return float(img.height()) / float(img.width())
    except Exception:
        pass
    return 1.0


def _extract_trailing_number(name, fallback=0):
    digits = []
    for char in reversed(str(name or "")):
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    if not digits:
        return fallback
    return int("".join(reversed(digits)))


def _iter_group_members(group):
    for child in list(getattr(group, "Group", []) or []):
        yield child
        if getattr(child, "TypeId", "") == "App::DocumentObjectGroup":
            for nested in _iter_group_members(child):
                yield nested


def _segment_planes_from_group(group):
    planes = []
    for child in _iter_group_members(group):
        if str(getattr(child, "Name", "") or "").startswith("SegmentPlane_"):
            planes.append(child)
    planes.sort(key=lambda obj: _extract_trailing_number(getattr(obj, "Name", ""), 0))
    return planes


def _resolve_segment_planes(target_obj=None):
    if target_obj is not None:
        candidates = target_obj if isinstance(target_obj, (list, tuple)) else [target_obj]
    elif Gui is not None:
        candidates = list(Gui.Selection.getSelection() or [])
    else:
        candidates = []

    explicit_planes = []
    group = None
    for obj in candidates:
        if obj is None:
            continue
        obj_name = str(getattr(obj, "Name", "") or "")
        if getattr(obj, "TypeId", "") == "App::DocumentObjectGroup" and obj_name.startswith("SegmentPlanes_"):
            group = obj
            break
        if obj_name.startswith("SegmentPlane_"):
            explicit_planes.append(obj)
        for parent in getattr(obj, "InList", []) or []:
            parent_name = str(getattr(parent, "Name", "") or "")
            if getattr(parent, "TypeId", "") == "App::DocumentObjectGroup" and parent_name.startswith("SegmentPlanes_"):
                group = parent
                break
        if group is not None:
            break

    if group is not None:
        planes = _segment_planes_from_group(group)
        if planes:
            return group, planes

    if explicit_planes:
        explicit_planes.sort(key=lambda obj: _extract_trailing_number(getattr(obj, "Name", ""), 0))
        return None, explicit_planes

    return None, []


def _plane_size_mm(plane):
    width_mm = float(getattr(plane, "Length", 0.0) or 0.0)
    height_mm = float(getattr(plane, "Width", 0.0) or 0.0)
    if width_mm > 1e-6 and height_mm > 1e-6:
        return width_mm, height_mm

    shape = getattr(plane, "Shape", None)
    if shape and not shape.isNull():
        bb = shape.BoundBox
        dims = sorted([float(bb.XLength), float(bb.YLength), float(bb.ZLength)], reverse=True)
        if len(dims) >= 2:
            return dims[0], dims[1]
    raise ValueError("Could not determine the selected plane dimensions.")


def _ensure_cache_dir():
    if not os.path.isdir(WRAP_CACHE_DIR):
        os.makedirs(WRAP_CACHE_DIR)
    return WRAP_CACHE_DIR


def _safe_name_token(value):
    text = str(value or "Item").strip()
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in text)
    return safe or "Item"


def _clear_previous_outputs(doc, root_name, parent_group=None):
    prefix = "{}_".format(root_name)
    candidates = []
    if parent_group is not None:
        candidates.extend(list(getattr(parent_group, "Group", []) or []))
    candidates.extend(list(getattr(doc, "Objects", []) or []))

    seen = set()
    groups_to_remove = []
    loose_objects = []
    for obj in candidates:
        try:
            name = str(obj.Name)
            typeid = str(getattr(obj, "TypeId", ""))
        except Exception:
            continue
        if name in seen:
            continue
        seen.add(name)
        if name == root_name or name.startswith(prefix):
            if typeid == "App::DocumentObjectGroup":
                groups_to_remove.append(obj)
            else:
                loose_objects.append(obj)

    for obj in groups_to_remove:
        for child in reversed(list(getattr(obj, "Group", []) or [])):
            try:
                doc.removeObject(child.Name)
            except Exception:
                pass
        try:
            doc.removeObject(obj.Name)
        except Exception:
            pass

    for obj in loose_objects:
        try:
            doc.removeObject(obj.Name)
        except Exception:
            pass


def _prepare_image_file(image_file, base_name, suffix="image", crop_index=None, crop_count=None, mirror_x=False, offset_fraction=0.0):
    if QtGui is None:
        return image_file
    try:
        img = QtGui.QImage(image_file)
        if img.isNull() or img.width() <= 0 or img.height() <= 0:
            return image_file
        if offset_fraction:
            shift = int(round(float(offset_fraction) * img.width()))
            if img.width() > 0:
                shift = shift % img.width()
            if shift:
                shifted = QtGui.QImage(img.size(), img.format())
                shifted.fill(0)
                painter = QtGui.QPainter(shifted)
                painter.drawImage(shift, 0, img)
                painter.drawImage(shift - img.width(), 0, img)
                painter.end()
                img = shifted
        if crop_index is not None and crop_count and crop_count > 1:
            x0 = int(round((float(crop_index) / float(crop_count)) * img.width()))
            x1 = int(round((float(crop_index + 1) / float(crop_count)) * img.width()))
            crop_width = max(1, x1 - x0)
            img = img.copy(x0, 0, crop_width, img.height())
        if mirror_x:
            img = img.mirrored(True, False)
        safe_name = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in str(base_name or "WrapTemplate"))
        out_path = os.path.join(_ensure_cache_dir(), "{}_{}.png".format(safe_name, suffix))
        img.save(out_path, "PNG")
        return out_path
    except Exception:
        return image_file


def _centered_image_placement(plane_placement, width_mm, height_mm):
    return App.Placement(App.Vector(plane_placement.Base), plane_placement.Rotation)


def _create_segment_plane_images(doc, planes, image_file, base_name, name_root, fit_mode, transparency, host_label, wrap_offset_deg=0.0, parent_group=None, flip_image=True, reverse_segment_order=True):
    group = doc.addObject("App::DocumentObjectGroup", name_root)
    group.Label = "{} {}".format(base_name, host_label)
    if parent_group is not None:
        try:
            parent_group.addObject(group)
        except Exception:
            pass
    offset_fraction = float(wrap_offset_deg or 0.0) / 360.0

    for index, plane in enumerate(planes):
        segment_group = doc.addObject("App::DocumentObjectGroup", _next_name(doc, "{}_SegmentGroup_{}".format(name_root, index + 1)))
        segment_group.Label = "Segment {}".format(index + 1)
        group.addObject(segment_group)

        width_mm, height_mm = _plane_size_mm(plane)
        crop_index = (len(planes) - 1 - index) if bool(reverse_segment_order) else index
        seg_file = _prepare_image_file(
            image_file,
            name_root,
            suffix="segment_{:02d}".format(index + 1),
            crop_index=crop_index,
            crop_count=len(planes),
            mirror_x=bool(flip_image),
            offset_fraction=offset_fraction,
        )
        seg_aspect = _image_aspect_ratio(seg_file)

        image = doc.addObject("Image::ImagePlane", _next_name(doc, "{}_SegmentImage_{}".format(name_root, index + 1)))
        image.Label = "{} Segment {}".format(base_name, index + 1)
        image.ImageFile = seg_file
        try:
            image.ViewObject.Transparency = max(0, min(100, int(transparency)))
        except Exception:
            pass
        if str(fit_mode or "stretch").lower() == "fit":
            fit_width = width_mm
            fit_height = fit_width * seg_aspect
            if fit_height > height_mm:
                fit_height = height_mm
                fit_width = fit_height / max(seg_aspect, 1e-9)
            image.XSize = fit_width
            image.YSize = fit_height
        else:
            image.XSize = width_mm
            image.YSize = height_mm
        image.Placement = _centered_image_placement(plane.Placement, image.XSize, image.YSize)
        segment_group.addObject(image)

        sketch = doc.addObject("Sketcher::SketchObject", _next_name(doc, "{}_SegmentTrace_{}".format(name_root, index + 1)))
        sketch.Label = "{} Trace {}".format(base_name, index + 1)
        _attach_sketch_to_plane(sketch, plane)
        _add_guide_geometry(sketch, width_mm, height_mm)
        segment_group.addObject(sketch)

    return group


def _template_base_placement(shape, width_mm, height_mm):
    bb = shape.BoundBox
    x = float(bb.Center.x)
    y = float(bb.Center.y)
    z = float(bb.ZMax) + TOP_CLEARANCE_MM
    return App.Vector(x, y, z)


def _attach_sketch_to_plane(sketch, plane):
    offset = App.Placement(
        App.Vector(0, 0, TOP_CLEARANCE_MM),
        App.Rotation(App.Vector(0, 1, 0), 180),
    )
    try:
        sketch.Support = [(plane, "Face1")]
        sketch.MapMode = "FlatFace"
        try:
            sketch.AttachmentOffset = offset
        except Exception:
            pass
        return
    except Exception:
        pass
    try:
        sketch.AttachmentSupport = [(plane, "Face1")]
        sketch.MapMode = "FlatFace"
        try:
            sketch.AttachmentOffset = offset
        except Exception:
            pass
        return
    except Exception:
        pass
    sketch.MapMode = "Deactivated"
    placement = App.Placement(App.Vector(plane.Placement.Base), plane.Placement.Rotation)
    try:
        placement = placement.multiply(offset)
    except Exception:
        try:
            normal = plane.Placement.Rotation.multVec(App.Vector(0, 0, 1))
            placement.Base = placement.Base.add(normal.multiply(TOP_CLEARANCE_MM))
        except Exception:
            pass
    sketch.Placement = placement


def _add_guide_geometry(sketch, width_mm, height_mm):
    half_w = 0.5 * float(width_mm)
    half_h = 0.5 * float(height_mm)
    segments = [
        Part.LineSegment(App.Vector(-half_w, -half_h, 0), App.Vector(half_w, -half_h, 0)),
        Part.LineSegment(App.Vector(half_w, -half_h, 0), App.Vector(half_w, half_h, 0)),
        Part.LineSegment(App.Vector(half_w, half_h, 0), App.Vector(-half_w, half_h, 0)),
        Part.LineSegment(App.Vector(-half_w, half_h, 0), App.Vector(-half_w, -half_h, 0)),
        Part.LineSegment(App.Vector(0, -half_h, 0), App.Vector(0, half_h, 0)),
    ]
    for segment in segments:
        try:
            idx = sketch.addGeometry(segment, False)
            try:
                sketch.toggleConstruction(idx)
            except Exception:
                pass
        except Exception:
            pass


def create_image_wrap_template(image_file=None, output_name="WrapTemplate", fit_mode="stretch", transparency=35, wrap_offset_deg=0.0, target_obj=None, target_face=None, target_edge=None, flip_image=True, reverse_segment_order=True):
    doc = App.ActiveDocument
    if doc is None:
        raise ValueError("No active document.")
    if not image_file or not os.path.isfile(image_file):
        raise ValueError("Choose a valid image file first.")

    base_name = str(output_name or "WrapTemplate").strip() or "WrapTemplate"
    segment_group, segment_planes = _resolve_segment_planes(target_obj=target_obj)
    if segment_planes:
        host = segment_group or target_obj or segment_planes[0]
        host_name = getattr(host, "Name", getattr(host, "Label", "Segments"))
        name_root = "{}_{}".format(_safe_name_token(base_name), _safe_name_token(host_name))
        _clear_previous_outputs(doc, name_root, parent_group=segment_group)
        group = _create_segment_plane_images(
            doc,
            segment_planes,
            image_file=image_file,
            base_name=base_name,
            name_root=name_root,
            fit_mode=fit_mode,
            transparency=transparency,
            host_label=getattr(host, "Label", getattr(host, "Name", "Segments")),
            wrap_offset_deg=wrap_offset_deg,
            parent_group=segment_group,
            flip_image=bool(flip_image),
            reverse_segment_order=bool(reverse_segment_order),
        )
        doc.recompute()
        _progress("Placed {} cropped image segments.".format(len(segment_planes)))
        width_mm, height_mm = _plane_size_mm(segment_planes[0])
        _progress("Segment plane width: {:.3f} in".format(width_mm / MM_PER_IN))
        _progress("Segment plane height: {:.3f} in".format(height_mm / MM_PER_IN))
        return group

    obj, shape, face, _edge = _resolve_target(target_obj=target_obj, target_face=target_face, target_edge=target_edge)
    frame = _cylinder_frame(face)
    width_mm = float(frame["circumference"])
    height_mm = float(frame["height"])
    aspect = _image_aspect_ratio(image_file)

    name_root = "{}_{}".format(_safe_name_token(base_name), _safe_name_token(obj.Name))
    _clear_previous_outputs(doc, name_root)
    group = doc.addObject("App::DocumentObjectGroup", name_root)
    group.Label = "{} {}".format(base_name, getattr(obj, "Label", obj.Name))

    placement_base = _template_base_placement(shape, width_mm, height_mm)
    placement = App.Placement(placement_base, App.Rotation())

    plane = doc.addObject("PartDesign::Plane", _next_name(doc, "{}_Plane".format(name_root)))
    plane.Label = "{} Plane".format(base_name)
    plane.MapMode = "Deactivated"
    plane.Placement = placement
    try:
        plane.ResizeMode = "Manual"
    except Exception:
        pass
    try:
        plane.Length = width_mm
        plane.Width = height_mm
    except Exception:
        pass
    group.addObject(plane)

    image = doc.addObject("Image::ImagePlane", _next_name(doc, "{}_Image".format(name_root)))
    image.Label = "{} Image".format(base_name)
    image.ImageFile = _prepare_image_file(
        image_file,
        name_root,
        suffix="top",
        mirror_x=bool(flip_image),
        offset_fraction=float(wrap_offset_deg or 0.0) / 360.0,
    )
    try:
        image.ViewObject.Transparency = max(0, min(100, int(transparency)))
    except Exception:
        pass
    if str(fit_mode or "stretch").lower() == "fit":
        fit_width = width_mm
        fit_height = fit_width * aspect
        if fit_height > height_mm:
            fit_height = height_mm
            fit_width = fit_height / max(aspect, 1e-9)
        image.XSize = fit_width
        image.YSize = fit_height
    else:
        image.XSize = width_mm
        image.YSize = height_mm
    image.Placement = _centered_image_placement(plane.Placement, image.XSize, image.YSize)
    group.addObject(image)

    sketch = doc.addObject("Sketcher::SketchObject", _next_name(doc, "{}_Sketch".format(name_root)))
    sketch.Label = "{} Trace".format(base_name)
    _attach_sketch_to_plane(sketch, plane)
    _add_guide_geometry(sketch, width_mm, height_mm)
    group.addObject(sketch)

    doc.recompute()

    _progress("Template created for {}".format(getattr(obj, "Label", obj.Name)))
    _progress("Cylinder diameter: {:.3f} in".format((2.0 * frame["radius"]) / MM_PER_IN))
    _progress("Wrap width: {:.3f} in".format(width_mm / MM_PER_IN))
    _progress("Wrap height: {:.3f} in".format(height_mm / MM_PER_IN))

    if Gui is not None:
        try:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, sketch.Name)
        except Exception:
            pass
    return group


class WrapTemplateTaskPanel:
    def __init__(self):
        if QtGui is None:
            raise RuntimeError("Qt is not available.")

        self.segment_group, self.segment_planes = _resolve_segment_planes()
        if self.segment_planes:
            self.target_obj = self.segment_group or self.segment_planes[0]
            self.target_shape = None
            self.target_face = None
            self.target_edge = None
            self.frame = None
        else:
            self.target_obj, self.target_shape, self.target_face, self.target_edge = _resolve_target()
            self.frame = _cylinder_frame(self.target_face)

        self.form = QtGui.QWidget()
        self.form.setWindowTitle("Image Wrap Template")
        layout = QtGui.QFormLayout(self.form)

        self.output_name = QtGui.QLineEdit(PREFS.GetString("output_name", "WrapTemplate"))
        self.image_path = QtGui.QLineEdit(PREFS.GetString("image_file", ""))

        browse_row = QtGui.QWidget()
        browse_layout = QtGui.QHBoxLayout(browse_row)
        browse_layout.setContentsMargins(0, 0, 0, 0)
        browse_layout.addWidget(self.image_path, 1)
        browse_button = QtGui.QPushButton("Browse")
        browse_layout.addWidget(browse_button)
        browse_button.clicked.connect(self._browse)

        self.fit_mode = QtGui.QComboBox()
        self.fit_mode.addItems(["stretch", "fit"])
        saved_fit = PREFS.GetString("fit_mode", "stretch") or "stretch"
        self.fit_mode.setCurrentIndex(max(0, self.fit_mode.findText(saved_fit)))

        self.transparency = QtGui.QSpinBox()
        self.transparency.setRange(0, 100)
        self.transparency.setSuffix(" %")
        self.transparency.setValue(PREFS.GetInt("transparency", 35))

        self.wrap_offset = QtGui.QDoubleSpinBox()
        self.wrap_offset.setRange(-180.0, 180.0)
        self.wrap_offset.setDecimals(1)
        self.wrap_offset.setSingleStep(1.0)
        self.wrap_offset.setSuffix(" deg")
        self.wrap_offset.setValue(PREFS.GetFloat("offset_degrees", 0.0))

        self.flip_image = QtGui.QCheckBox()
        self.flip_image.setChecked(True)

        self.reverse_segment_order = QtGui.QCheckBox()
        self.reverse_segment_order.setChecked(True)

        if self.segment_planes:
            plane_width_mm, plane_height_mm = _plane_size_mm(self.segment_planes[0])
            info_text = "Selected segment planes: {}\nPlane width: {:.3f} in\nPlane height: {:.3f} in".format(
                len(self.segment_planes),
                plane_width_mm / MM_PER_IN,
                plane_height_mm / MM_PER_IN,
            )
            note_text = "This will place one cropped image slice on each segment plane and add a tracing sketch for each, grouped by segment. Use Apply to preview offset changes without closing the panel."
        else:
            info_text = "Selected diameter: {:.3f} in\nWrap width from circumference: {:.3f} in\nWrap height from face: {:.3f} in".format(
                (2.0 * self.frame["radius"]) / MM_PER_IN,
                self.frame["circumference"] / MM_PER_IN,
                self.frame["height"] / MM_PER_IN,
            )
            note_text = "This uses the selected cylinder dimensions automatically. It creates a flat plane, an image reference, and a tracing sketch. Use Apply to preview offset changes without closing the panel."

        self.info = QtGui.QLabel(info_text)
        self.info.setWordWrap(True)

        note = QtGui.QLabel(note_text)
        note.setWordWrap(True)

        layout.addRow("Output name", self.output_name)
        layout.addRow("Image file", browse_row)
        layout.addRow("Image fit", self.fit_mode)
        layout.addRow("Transparency", self.transparency)
        layout.addRow("Wrap offset", self.wrap_offset)
        layout.addRow("Flip image", self.flip_image)
        layout.addRow("Reverse segment order", self.reverse_segment_order)
        layout.addRow("Selected solid", self.info)
        layout.addRow("", note)

    def _browse(self):
        if QtGui is None:
            return
        try:
            filename, _ = QtGui.QFileDialog.getOpenFileName(
                None,
                "Select image",
                self.image_path.text().strip() or os.path.expanduser("~"),
                "Image files (*.png *.jpg *.jpeg *.bmp *.gif *.webp)",
            )
        except Exception:
            filename = ""
        if filename:
            self.image_path.setText(filename)

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
        PREFS.SetString("output_name", self.output_name.text().strip() or "WrapTemplate")
        PREFS.SetString("image_file", self.image_path.text().strip())
        PREFS.SetString("fit_mode", self.fit_mode.currentText())
        PREFS.SetInt("transparency", int(self.transparency.value()))
        PREFS.SetFloat("offset_degrees", float(self.wrap_offset.value()))
        PREFS.SetBool("flip_image", bool(self.flip_image.isChecked()))
        PREFS.SetBool("reverse_segment_order", bool(self.reverse_segment_order.isChecked()))

        create_image_wrap_template(
            image_file=self.image_path.text().strip(),
            output_name=self.output_name.text().strip() or "WrapTemplate",
            fit_mode=self.fit_mode.currentText(),
            transparency=int(self.transparency.value()),
            wrap_offset_deg=float(self.wrap_offset.value()),
            target_obj=self.target_obj,
            target_face=self.target_face,
            target_edge=self.target_edge,
            flip_image=bool(self.flip_image.isChecked()),
            reverse_segment_order=bool(self.reverse_segment_order.isChecked()),
        )

        if close_after and Gui is not None:
            try:
                Gui.Control.closeDialog()
            except Exception:
                pass
        return True


def show_wrap_template_dialog():
    if Gui is None or QtGui is None:
        raise RuntimeError("FreeCAD GUI is required to show the task panel.")
    Gui.Control.showDialog(WrapTemplateTaskPanel())


if App.GuiUp and __name__ == "__main__":
    show_wrap_template_dialog()
