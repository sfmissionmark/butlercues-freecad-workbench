import FreeCAD
import FreeCADGui
from PySide import QtGui, QtCore
import math
import Part
import re

TILING_PREFS = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/butlercues/Tiling")
PATTERN_LAB_PREFS = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/butlercues/PatternLab")
DEFAULT_TILE_SIZE_IN = 0.5
DEFAULT_SEGMENTS = 4
DEFAULT_PAD_DEPTH_IN = 0.15
DEFAULT_ROW_OFFSET_SEGMENTS = 0.5
SEGMENT_PREFS = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/butlercues/SegmentPlanes")
DEFAULT_SECTION_SEGMENTS = 6
DEFAULT_CREATE_SEGMENT_SKETCHES = False
DEFAULT_SEGMENT_SURFACE_LIFT_IN = 0.002
QBERT_PREFS = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/butlercues/QbertPattern")
DEFAULT_QBERT_ROWS = 5
DEFAULT_QBERT_COLS = 10
DEFAULT_QBERT_CELL_MM = 6.0
DEFAULT_QBERT_CELL_IN = DEFAULT_QBERT_CELL_MM / 25.4
DEFAULT_QBERT_STAGGER = 0.5
DEFAULT_QBERT_CLEAR_EXISTING = False
DEFAULT_QBERT_DEPTH_IN = 0.15
DEFAULT_QBERT_CORNER_RADIUS_IN = 0.016
DEFAULT_PATTERN_LAB_TILE_GUIDE_WIDTH_IN = 0.250
DEFAULT_PATTERN_LAB_MATERIAL = 'inlay'
DEFAULT_PATTERN_LAB_SOLID_DEPTH_IN = 0.15
# Cube is 2x the segment width so it reads clearly across adjacent segments.
QBERT_SECTION_FILL_RATIO = 2.0


def _find_solid(obj):
    shape = getattr(obj, 'Shape', None)
    if shape and hasattr(shape, 'ShapeType') and shape.ShapeType == 'Solid':
        return obj, shape
    for attr in ['Group', 'OutList', 'GroupObjects', 'OutListRecursive', 'OutListOfType']:
        children = getattr(obj, attr, None)
        if children:
            for child in children:
                found, found_shape = _find_solid(child)
                if found_shape:
                    return found, found_shape
    return None, None


def _max_radial_distance(shape):
    max_radius = 0.0
    for vertex in getattr(shape, 'Vertexes', []):
        point = vertex.Point
        radius = math.sqrt(point.x ** 2 + point.z ** 2)
        max_radius = max(max_radius, radius)
    return max_radius


def _radius_at_y(shape, y_value):
    search_tolerances = [0.01, 0.1, 0.5, 1.0, 2.0]
    for tolerance in search_tolerances:
        max_radius = 0.0
        found = False
        for vertex in getattr(shape, 'Vertexes', []):
            point = vertex.Point
            if abs(point.y - y_value) <= tolerance:
                radius = math.sqrt(point.x ** 2 + point.z ** 2)
                if radius > max_radius:
                    max_radius = radius
                    found = True
        if found:
            return max_radius
    return _max_radial_distance(shape)


def _attach_sketch_to_plane(sketch, plane):
    attached = False
    try:
        sketch.Support = [(plane, "Face1")]
        sketch.MapMode = 'FlatFace'
        attached = True
    except Exception:
        try:
            sketch.AttachmentSupport = [(plane, "Face1")]
            sketch.MapMode = 'FlatFace'
            attached = True
        except Exception:
            pass
    if not attached:
        sketch.MapMode = 'Deactivated'
        sketch.Placement = plane.Placement


def _show_task_panel_safe(panel):
    # FreeCAD only allows one task panel at a time.
    try:
        FreeCADGui.Control.closeDialog()
    except Exception:
        pass
    FreeCADGui.Control.showDialog(panel)


def _open_sketch_edit_deferred(sketch_name, delay_ms=50):
    def _open_edit():
        try:
            FreeCADGui.ActiveDocument.setEdit(sketch_name)
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f'[PatternLab] Could not auto-open sketch editor for {sketch_name}: {exc}\n')
            return

        # Sketcher sometimes opens from the opposite side; force a consistent top view.
        try:
            active_doc = FreeCADGui.ActiveDocument
            if active_doc:
                view = active_doc.ActiveView
                if view:
                    view.viewTop()
                    try:
                        view.fitAll()
                    except Exception:
                        pass
        except Exception as exc:
            FreeCAD.Console.PrintWarning(f'[PatternLab] Could not orient view to top for {sketch_name}: {exc}\n')

    try:
        QtCore.QTimer.singleShot(int(delay_ms), _open_edit)
    except Exception:
        _open_edit()


def _orient_pattern_master_sketch(sketch):
    # Ensure the attached sketch normal is consistently oriented toward the user.
    # This avoids the common "opens from the bottom" behavior on wrapped segment planes.
    try:
        offset = getattr(sketch, 'AttachmentOffset', None)
        base = offset.Base if offset else FreeCAD.Vector(0, 0, 0)
    except Exception:
        base = FreeCAD.Vector(0, 0, 0)

    try:
        sketch.AttachmentOffset = FreeCAD.Placement(
            base,
            FreeCAD.Rotation(FreeCAD.Vector(1, 0, 0), 180),
        )
    except Exception:
        pass


def _segment_container_name(target_name):
    return f"SegmentPlanes_{target_name}"


def _extract_trailing_number(name, fallback=0):
    digits = []
    for char in reversed(name):
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    if not digits:
        return fallback
    return int(''.join(reversed(digits)))


def _resolve_segment_group(obj):
    if not obj:
        return None
    visited = set()
    queue = [obj]
    while queue:
        current = queue.pop(0)
        current_name = getattr(current, 'Name', None)
        if current_name in visited:
            continue
        if current_name:
            visited.add(current_name)
        if getattr(current, 'TypeId', '') == 'App::DocumentObjectGroup' and str(current.Name).startswith('SegmentPlanes_'):
            return current
        for parent in getattr(current, 'InList', []):
            queue.append(parent)
    return None


def _find_group_member(group, prefix):
    members = _find_group_members(group, prefix)
    if members:
        return members[0]
    return None


def _find_group_members(group, prefix):
    members = []
    visited = set()

    def _walk(container):
        if not container:
            return
        container_name = str(getattr(container, 'Name', '') or '')
        if container_name in visited:
            return
        if container_name:
            visited.add(container_name)

        for child in list(getattr(container, 'Group', []) or []):
            child_name = str(getattr(child, 'Name', '') or '')
            if child_name.startswith(prefix):
                members.append(child)
            if getattr(child, 'TypeId', '') == 'App::DocumentObjectGroup':
                _walk(child)

    _walk(group)
    members.sort(key=lambda obj: _extract_trailing_number(str(obj.Name), 0))
    return members


def _remove_group_members(doc, group, prefix):
    for existing in _find_group_members(group, prefix):
        try:
            doc.removeObject(existing.Name)
        except Exception:
            pass


def _segment_group_target_solid(doc, group):
    group_name = str(getattr(group, 'Name', ''))
    prefix = 'SegmentPlanes_'
    if not group_name.startswith(prefix):
        return None, None
    target_name = group_name[len(prefix):]
    if not target_name:
        return None, None
    target_obj = doc.getObject(target_name)
    if not target_obj:
        return None, None
    return _find_solid(target_obj)


def _qbert_preview_group_name(target_name):
    return f"QbertPreview_{target_name}"


def _pattern_lab_group_name(segment_group_name):
    return 'Inlay_sketches'


def _legacy_pattern_lab_group_name(segment_group_name):
    return f"PatternLab_{segment_group_name}"


def _ensure_pattern_lab_group(doc, segment_group):
    group_name = _pattern_lab_group_name(getattr(segment_group, 'Name', 'SegmentGroup'))
    lab_group = doc.getObject(group_name)
    if not lab_group:
        legacy_name = _legacy_pattern_lab_group_name(getattr(segment_group, 'Name', 'SegmentGroup'))
        lab_group = doc.getObject(legacy_name)
    if lab_group:
        try:
            lab_group.Label = 'Inlay_sketches'
        except Exception:
            pass
        try:
            segment_group.addObject(lab_group)
        except Exception:
            pass
        return lab_group

    lab_group = doc.addObject('App::DocumentObjectGroup', group_name)
    lab_group.Label = 'Inlay_sketches'
    try:
        segment_group.addObject(lab_group)
    except Exception:
        pass
    return lab_group


def _ensure_inlay_solids_group(doc, segment_group):
    group_name = 'Inlay_solids'
    solids_group = doc.getObject(group_name)
    if solids_group:
        try:
            segment_group.addObject(solids_group)
        except Exception:
            pass
        return solids_group

    solids_group = doc.addObject('App::DocumentObjectGroup', group_name)
    solids_group.Label = 'Inlay_solids'
    try:
        segment_group.addObject(solids_group)
    except Exception:
        pass
    return solids_group


def _ensure_inlay_pattern_links_group(doc, segment_group):
    group_name = 'Inlay_pattern_links'
    links_group = doc.getObject(group_name)
    if links_group:
        try:
            segment_group.addObject(links_group)
        except Exception:
            pass
        return links_group

    links_group = doc.addObject('App::DocumentObjectGroup', group_name)
    links_group.Label = 'Inlay_pattern_links'
    try:
        segment_group.addObject(links_group)
    except Exception:
        pass
    return links_group


def _ensure_inlay_kites_group(doc, segment_group):
    group_name = 'Inlay_kites'
    kites_group = doc.getObject(group_name)
    if kites_group:
        try:
            segment_group.addObject(kites_group)
        except Exception:
            pass
        return kites_group

    kites_group = doc.addObject('App::DocumentObjectGroup', group_name)
    kites_group.Label = 'Inlay_kites'
    try:
        segment_group.addObject(kites_group)
    except Exception:
        pass
    return kites_group


def _ensure_segment_planes_folder(doc, segment_group):
    segment_name = str(getattr(segment_group, 'Name', 'SegmentPlanes'))
    group_name = f'inlay_segments_{segment_name}'
    segments_group = doc.getObject(group_name)
    if not segments_group:
        segments_group = doc.addObject('App::DocumentObjectGroup', group_name)
    try:
        segments_group.Label = 'inlay_segments'
    except Exception:
        pass
    try:
        segment_group.addObject(segments_group)
    except Exception:
        pass
    return segments_group


def _pattern_lab_default_cell_step_mm(anchor_plane):
    segment_width = _as_float(getattr(anchor_plane, 'Length', 0.0), 0.0)
    guide_width = (segment_width * 2.0) if segment_width > 1e-6 else (DEFAULT_PATTERN_LAB_TILE_GUIDE_WIDTH_IN * 25.4 * 2.0)
    _, half_h, _, _ = _qbert_layout_params(guide_width)
    # Cell height is half of a full cube height so 2x Cell Height equals one tile height.
    return max(0.1, 2.0 * half_h)


def _bbox_corners(bb):
    return [
        FreeCAD.Vector(x, y, z)
        for x in [bb.XMin, bb.XMax]
        for y in [bb.YMin, bb.YMax]
        for z in [bb.ZMin, bb.ZMax]
    ]


def _shape_span_along_plane_y_mm(source_obj, anchor_plane):
    shape = getattr(source_obj, 'Shape', None) if source_obj else None
    if not shape:
        return 0.0
    bb = getattr(shape, 'BoundBox', None)
    if not bb:
        return 0.0
    try:
        if anchor_plane and getattr(anchor_plane, 'Placement', None):
            plane_y = anchor_plane.Placement.Rotation.multVec(FreeCAD.Vector(0, 1, 0))
            try:
                plane_y.normalize()
            except Exception:
                pass
            projs = [plane_y.dot(c) for c in _bbox_corners(bb)]
            return max(0.0, max(projs) - min(projs))
    except Exception:
        pass
    return max(0.0, _as_float(getattr(bb, 'YLength', 0.0), 0.0))


def _pattern_lab_source_tile_height_mm(anchor_plane, source_objs):
    spans = []
    for source_obj in (source_objs or []):
        span_mm = _shape_span_along_plane_y_mm(source_obj, anchor_plane)
        if span_mm > 1e-6:
            spans.append(span_mm)
    if not spans:
        return 0.0
    spans.sort()
    return spans[len(spans) // 2]


def _pattern_lab_combined_sources_height_mm(anchor_plane, source_objs):
    try:
        plane_y = anchor_plane.Placement.Rotation.multVec(FreeCAD.Vector(0, 1, 0))
        try:
            plane_y.normalize()
        except Exception:
            pass
    except Exception:
        plane_y = FreeCAD.Vector(0, 1, 0)

    overall_min = None
    overall_max = None

    for source_obj in (source_objs or []):
        shape = getattr(source_obj, 'Shape', None)
        if not shape:
            continue
        try:
            if shape.isNull():
                continue
        except Exception:
            pass
        bb = getattr(shape, 'BoundBox', None)
        if not bb:
            continue
        try:
            for corner in _bbox_corners(bb):
                p = plane_y.dot(corner)
                if overall_min is None or p < overall_min:
                    overall_min = p
                if overall_max is None or p > overall_max:
                    overall_max = p
        except Exception:
            pass

    if overall_min is None or overall_max is None:
        return 0.0
    return max(0.0, overall_max - overall_min)


def _plane_y_axis(anchor_plane):
    try:
        axis = anchor_plane.Placement.Rotation.multVec(FreeCAD.Vector(0, 1, 0))
        axis.normalize()
        return axis
    except Exception:
        return FreeCAD.Vector(0, 1, 0)


def _selected_two_point_step_mm(anchor_plane):
    if not FreeCADGui:
        return 0.0
    try:
        sel_ex = FreeCADGui.Selection.getSelectionEx() or []
    except Exception:
        return 0.0

    points = []
    for item in sel_ex:
        for sub in list(getattr(item, 'SubObjects', []) or []):
            p = getattr(sub, 'Point', None)
            if p is not None:
                points.append(FreeCAD.Vector(float(p.x), float(p.y), float(p.z)))
                continue
            verts = list(getattr(sub, 'Vertexes', []) or [])
            for v in verts:
                vp = getattr(v, 'Point', None)
                if vp is not None:
                    points.append(FreeCAD.Vector(float(vp.x), float(vp.y), float(vp.z)))
            if len(points) >= 2:
                break
        if len(points) >= 2:
            break

    if len(points) < 2:
        return 0.0

    axis = _plane_y_axis(anchor_plane)
    delta = points[1].sub(points[0])
    return max(0.0, abs(axis.dot(delta)))


def _combined_source_shape(source_objs):
    shapes = []
    for obj in (source_objs or []):
        shape = getattr(obj, 'Shape', None)
        if not shape:
            continue
        try:
            if shape.isNull():
                continue
        except Exception:
            pass
        shapes.append(shape)
    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    try:
        return Part.makeCompound(shapes)
    except Exception:
        return shapes[0]


def _shape_overlap_volume_after_shift(shape, axis, shift_mm):
    if not shape or shift_mm <= 1e-9:
        return float('inf')
    try:
        moved = shape.copy()
        moved.translate(axis.multiply(-float(shift_mm)))
        overlap = shape.common(moved)
        if not overlap:
            return 0.0
        try:
            if overlap.isNull():
                return 0.0
        except Exception:
            pass
        return max(0.0, _as_float(getattr(overlap, 'Volume', 0.0), 0.0))
    except Exception:
        return float('inf')


def _pattern_lab_collision_fit_step_mm(anchor_plane, source_objs):
    shape = _combined_source_shape(source_objs)
    if not shape:
        return 0.0

    axis = _plane_y_axis(anchor_plane)
    base_span = _pattern_lab_combined_sources_height_mm(anchor_plane, source_objs)
    if base_span <= 1e-6:
        base_span = _pattern_lab_source_tile_height_mm(anchor_plane, source_objs)
    if base_span <= 1e-6:
        base_span = 0.1

    tol_vol = 1e-5
    low = 0.0
    high = max(0.1, float(base_span))

    for _ in range(12):
        if _shape_overlap_volume_after_shift(shape, axis, high) <= tol_vol:
            break
        high *= 1.5
        if high > (base_span * 20.0):
            break

    if _shape_overlap_volume_after_shift(shape, axis, high) > tol_vol:
        return max(0.1, float(base_span))

    for _ in range(20):
        mid = (low + high) * 0.5
        if _shape_overlap_volume_after_shift(shape, axis, mid) > tol_vol:
            low = mid
        else:
            high = mid

    return max(0.1, float(high))


def _is_fillet_created_object(obj):
    if not obj:
        return False
    linked = getattr(obj, 'LinkedObject', None)
    if linked and linked is not obj:
        try:
            if _is_fillet_created_object(linked):
                return True
        except Exception:
            pass
    try:
        if hasattr(obj, 'FilletRadiusInch'):
            return True
    except Exception:
        pass

    name = str(getattr(obj, 'Name', '') or '').strip().lower()
    label = str(getattr(obj, 'Label', '') or '').strip().lower()
    if name.endswith('_fillet') or label.endswith('_fillet'):
        return True
    return False


def _collect_shape_sources_from_selection(selection, require_filleted=False):
    sources = []
    seen = set()

    def _walk(obj):
        if not obj:
            return

        name = str(getattr(obj, 'Name', '') or '')
        if name and name in seen:
            return
        if name:
            seen.add(name)

        for child in list(getattr(obj, 'Group', []) or []):
            _walk(child)

        if getattr(obj, 'TypeId', '') == 'PartDesign::Plane':
            return
        shape = getattr(obj, 'Shape', None)
        if not shape:
            return
        try:
            if shape.isNull():
                return
        except Exception:
            pass
        if require_filleted and (not _is_fillet_created_object(obj)):
            return
        sources.append(obj)

    for item in list(selection or []):
        _walk(item)

    return sources


def _copy_view_appearance(source_obj, target_obj):
    source_view = getattr(source_obj, 'ViewObject', None)
    target_view = getattr(target_obj, 'ViewObject', None)
    if not source_view or not target_view:
        return

    is_link = 'Link' in str(getattr(target_obj, 'TypeId', '') or '')

    if is_link:
        # App::Link requires OverrideMaterial=True before colour takes effect.
        try:
            target_view.OverrideMaterial = True
        except Exception:
            pass

        source_color = None
        try:
            source_color = tuple(getattr(source_view, 'ShapeColor', None) or ())
        except Exception:
            pass

        if source_color and len(source_color) >= 3:
            r, g, b = float(source_color[0]), float(source_color[1]), float(source_color[2])
            try:
                mat = target_view.ShapeMaterial
                mat.DiffuseColor = (r, g, b)
                target_view.ShapeMaterial = mat
            except Exception:
                pass

        try:
            target_view.Transparency = source_view.Transparency
        except Exception:
            pass
        return

    # Regular (non-link) objects: copy properties directly.
    source_props = set(getattr(source_view, 'PropertiesList', []) or [])
    target_props = set(getattr(target_view, 'PropertiesList', []) or [])
    if not source_props or not target_props:
        return

    for prop_name in ('DisplayMode', 'ShapeColor', 'LineColor', 'PointColor',
                      'Transparency', 'DiffuseColor', 'ShapeMaterial', 'Material',
                      'LineWidth', 'PointSize'):
        if prop_name not in source_props or prop_name not in target_props:
            continue
        try:
            setattr(target_view, prop_name, getattr(source_view, prop_name))
        except Exception:
            pass


def _summarize_source_labels(source_objs, max_items=3):
    labels = [str(getattr(obj, 'Label', getattr(obj, 'Name', '<unknown>')) or '<unknown>') for obj in (source_objs or [])]
    if not labels:
        return '<select source object(s) first>'
    if len(labels) <= max_items:
        return ', '.join(labels)
    return ', '.join(labels[:max_items]) + f' (+{len(labels) - max_items} more)'


def _default_inlay_fillet_radius_inch():
    default_radius_inch = 0.014
    try:
        fillet_prefs = FreeCAD.ParamGet("User parameter:BaseApp/Preferences/Mod/ButlerCues/FilletForCNC")
        default_radius_inch = max(0.0001, float(fillet_prefs.GetFloat("radius_in", 0.014)))
    except Exception:
        pass
    return default_radius_inch


def _pattern_lab_tile_sketch_name(suffix):
    return f'PatternMasterSketch_{suffix}'


def _pattern_lab_material_token(raw):
    token = str(raw or '').strip().lower().replace(' ', '_')
    token = ''.join(ch for ch in token if ch.isalnum() or ch == '_')
    return token or DEFAULT_PATTERN_LAB_MATERIAL


def _pattern_lab_tile_sketch_name_for_material(suffix, material_token):
    token = _pattern_lab_material_token(material_token)
    return f'PatternMasterSketch_{token}_{suffix}'


def _pattern_lab_material_from_name(name, prefix, suffix):
    raw = str(name or '')
    suffix_text = f'_{int(suffix)}'
    if not raw.startswith(prefix):
        return None
    if raw == f'{prefix}{suffix_text}':
        return DEFAULT_PATTERN_LAB_MATERIAL
    if not raw.endswith(suffix_text):
        return None
    middle = raw[len(prefix):-len(suffix_text)]
    if middle.startswith('_'):
        middle = middle[1:]
    return _pattern_lab_material_token(middle)


def _pattern_lab_find_tile_sketch(doc, group, selection=None, material_token=None):
    material_token = _pattern_lab_material_token(material_token) if material_token else None
    for obj in selection or []:
        if getattr(obj, 'TypeId', '') == 'Sketcher::SketchObject':
            if material_token is not None and material_token != DEFAULT_PATTERN_LAB_MATERIAL:
                suffix = _extract_trailing_number(str(getattr(group, 'Name', '')), 0)
                name = str(getattr(obj, 'Name', '') or '')
                token = _pattern_lab_material_from_name(name, 'PatternMasterSketch', suffix)
                if token and token != material_token:
                    continue
            return obj

    suffix = _extract_trailing_number(str(getattr(group, 'Name', '')), 0)
    if material_token is not None:
        sketch = doc.getObject(_pattern_lab_tile_sketch_name_for_material(suffix, material_token))
        if sketch:
            return sketch
    else:
        sketch = doc.getObject(_pattern_lab_tile_sketch_name(suffix))
        if sketch:
            return sketch
    # Backward compatibility with earlier experimental naming.
    if material_token in (None, DEFAULT_PATTERN_LAB_MATERIAL):
        legacy_sketch = doc.getObject(f'PatternLabTileSketch_{suffix}')
        if legacy_sketch:
            return legacy_sketch

    lab_group = _ensure_pattern_lab_group(doc, group)
    for child in getattr(lab_group, 'Group', []) or []:
        child_name = str(getattr(child, 'Name', '') or '')
        if getattr(child, 'TypeId', '') != 'Sketcher::SketchObject':
            continue
        if material_token is None:
            if child_name.startswith('PatternMasterSketch_') or child_name.startswith('PatternLabTileSketch_'):
                return child
            continue
        token = _pattern_lab_material_from_name(child_name, 'PatternMasterSketch', suffix)
        if token == material_token:
            return child
        if material_token == DEFAULT_PATTERN_LAB_MATERIAL and child_name.startswith('PatternLabTileSketch_'):
            return child
    return None


def _pattern_master_shape_sides(shape_name):
    key = str(shape_name or '').strip().lower()
    if key == 'hexagon':
        return 6
    if key == 'octagon':
        return 8
    return 4


def _pattern_master_polygon_points(shape_name, target_width_mm):
    sides = _pattern_master_shape_sides(shape_name)
    if target_width_mm <= 1e-6:
        target_width_mm = DEFAULT_PATTERN_LAB_TILE_GUIDE_WIDTH_IN * 25.4

    # Start angles chosen to keep square axis-aligned and multi-side polygons flat-ish.
    start_angle = math.pi / 4.0 if sides == 4 else math.pi / float(sides)
    raw = []
    for idx in range(sides):
        angle = start_angle + (2.0 * math.pi * float(idx) / float(sides))
        raw.append(FreeCAD.Vector(math.cos(angle), math.sin(angle), 0))

    min_x = min(point.x for point in raw)
    max_x = max(point.x for point in raw)
    raw_width = max(1e-9, max_x - min_x)
    scale = float(target_width_mm) / raw_width

    return [FreeCAD.Vector(point.x * scale, point.y * scale, 0) for point in raw]


def _clear_non_construction_geometry(sketch):
    geometries = list(getattr(sketch, 'Geometry', []))
    for index in range(len(geometries) - 1, -1, -1):
        try:
            if sketch.getConstruction(index):
                continue
        except Exception:
            pass


def _clear_construction_geometry(sketch):
    geometries = list(getattr(sketch, 'Geometry', []))
    for index in range(len(geometries) - 1, -1, -1):
        is_construction = False
        try:
            is_construction = bool(sketch.getConstruction(index))
        except Exception:
            is_construction = False
        if not is_construction:
            continue
        try:
            sketch.delGeometry(index)
        except Exception:
            pass


def _has_non_construction_geometry(sketch):
    geometries = list(getattr(sketch, 'Geometry', []))
    for index in range(len(geometries)):
        try:
            if sketch.getConstruction(index):
                continue
        except Exception:
            pass
        return True
    return False


def _add_unique_segment_line(sketch, p1, p2, cache):
    if (p2.sub(p1)).Length <= 1e-6:
        return 0
    key = (
        round(min(p1.x, p2.x), 4),
        round(min(p1.y, p2.y), 4),
        round(max(p1.x, p2.x), 4),
        round(max(p1.y, p2.y), 4),
    )
    if key in cache:
        return 0
    cache.add(key)
    try:
        sketch.addGeometry(Part.LineSegment(p1, p2), False)
        return 1
    except Exception:
        return 0


def _add_closed_construction_loop(sketch, points):
    """Add a closed construction polyline and force coincident corners between segments."""
    if not points or len(points) < 3:
        return 0

    geo_indices = []
    count = len(points)
    for i in range(count):
        p1 = points[i]
        p2 = points[(i + 1) % count]
        try:
            geo_index = sketch.addGeometry(Part.LineSegment(p1, p2), False)
            try:
                sketch.toggleConstruction(geo_index)
            except Exception:
                pass
            geo_indices.append(geo_index)
        except Exception:
            continue

    if len(geo_indices) < 2:
        return len(geo_indices)

    try:
        import Sketcher
    except Exception:
        Sketcher = None

    if Sketcher is not None:
        for i in range(len(geo_indices)):
            a = geo_indices[i]
            b = geo_indices[(i + 1) % len(geo_indices)]
            try:
                # Endpoint 2 of current segment coincident with endpoint 1 of next segment.
                sketch.addConstraint(Sketcher.Constraint('Coincident', a, 2, b, 1))
            except Exception:
                pass
        for geo_index in geo_indices:
            try:
                sketch.addConstraint(Sketcher.Constraint('Block', geo_index))
            except Exception:
                pass

    return len(geo_indices)


def _add_blocked_construction_segment(sketch, p1, p2):
    try:
        geo_index = sketch.addGeometry(Part.LineSegment(p1, p2), False)
        try:
            sketch.toggleConstruction(geo_index)
        except Exception:
            pass
    except Exception:
        try:
            geo_index = sketch.addGeometry(Part.LineSegment(p1, p2), True)
        except Exception:
            return None

    try:
        import Sketcher
        sketch.addConstraint(Sketcher.Constraint('Block', geo_index))
    except Exception:
        pass
    return geo_index


def _draw_rhombus(sketch, cx, cy, half_w, half_h, cache):
    top = FreeCAD.Vector(cx, cy + half_h, 0)
    right = FreeCAD.Vector(cx + half_w, cy, 0)
    bottom = FreeCAD.Vector(cx, cy - half_h, 0)
    left = FreeCAD.Vector(cx - half_w, cy, 0)
    added = 0
    added += _add_unique_segment_line(sketch, top, right, cache)
    added += _add_unique_segment_line(sketch, right, bottom, cache)
    added += _add_unique_segment_line(sketch, bottom, left, cache)
    added += _add_unique_segment_line(sketch, left, top, cache)
    return added


def _rotate_point_2d(point, center, angle_rad):
    dx = point.x - center.x
    dy = point.y - center.y
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return FreeCAD.Vector(
        center.x + (dx * cos_a) - (dy * sin_a),
        center.y + (dx * sin_a) + (dy * cos_a),
        0,
    )


def _draw_rotated_rhombus(sketch, cx, cy, half_w, half_h, rotation_deg, cache):
    center = FreeCAD.Vector(cx, cy, 0)
    points = [
        FreeCAD.Vector(cx, cy + half_h, 0),
        FreeCAD.Vector(cx + half_w, cy, 0),
        FreeCAD.Vector(cx, cy - half_h, 0),
        FreeCAD.Vector(cx - half_w, cy, 0),
    ]
    angle_rad = math.radians(rotation_deg)
    if abs(angle_rad) > 1e-9:
        points = [_rotate_point_2d(point, center, angle_rad) for point in points]

    added = 0
    for index in range(len(points)):
        added += _add_unique_segment_line(sketch, points[index], points[(index + 1) % len(points)], cache)
    return added


def _qbert_rhombus_points(cx, cy, half_w, half_h):
    return [
        FreeCAD.Vector(cx, cy + half_h, 0),
        FreeCAD.Vector(cx + half_w, cy, 0),
        FreeCAD.Vector(cx, cy - half_h, 0),
        FreeCAD.Vector(cx - half_w, cy, 0),
        FreeCAD.Vector(cx, cy + half_h, 0),
    ]


def _qbert_face_polygons(cx, cy, half_w, half_h):
    c = FreeCAD.Vector(cx, cy, 0)
    u = FreeCAD.Vector(half_w, half_h, 0)
    v = FreeCAD.Vector(-half_w, half_h, 0)
    w = FreeCAD.Vector(0, -2.0 * half_h, 0)

    # Three isometric-like parallelogram faces sharing a corner.
    top = [c, c.add(u), c.add(u).add(v), c.add(v), c]
    front = [c, c.add(v), c.add(v).add(w), c.add(w), c]
    side = [c, c.add(u), c.add(u).add(w), c.add(w), c]
    return {'top': top, 'front': front, 'side': side}


def _qbert_layout_params(cell_mm):
    half_w = cell_mm * 0.5
    half_h = cell_mm * 0.28
    x_pitch = cell_mm
    # Row-to-row spacing: 1.5x the full cube height (4*half_h).
    y_pitch = 6.0 * half_h
    return half_w, half_h, x_pitch, y_pitch


def _qbert_face_for_token(token):
    """Map a material token to its default Q*bert cube face ('top', 'front', or 'side')."""
    t = str(token or '').lower()
    if 'front' in t:
        return 'front'
    if 'side' in t:
        return 'side'
    return 'top'


def _qbert_section_cell_mm(layout_plane, segment_planes):
    layout_length = _as_float(getattr(layout_plane, 'Length', 0.0), 0.0)
    segment_count = len(segment_planes)
    if layout_length <= 0 or segment_count <= 0:
        return None, 0
    segment_width = layout_length / float(segment_count)
    return segment_width * QBERT_SECTION_FILL_RATIO, 1  # One column per segment


def _qbert_row_shift(stagger, segment_width, cell_mm, row_index):
    offset = stagger * cell_mm
    return -offset if (row_index % 2 == 0) else offset


def _qbert_local_faces(rows, cols, cell_mm, stagger, wood_face='top'):
    half_w, half_h, x_pitch, y_pitch = _qbert_layout_params(cell_mm)
    segment_width = x_pitch / QBERT_SECTION_FILL_RATIO
    col_origin = (cols - 1) / 2.0
    row_origin = (rows - 1) / 2.0

    bucket_a = []
    bucket_b = []
    wood_face = str(wood_face).lower()
    family_a, family_b = _qbert_face_families(wood_face)
    for row in range(rows):
        y = (row_origin - row) * y_pitch
        for col in range(cols):
            cx = 0
            local_map = _qbert_face_polygons(cx, y, half_w, half_h)

            # Keep two inlay families and leave the selected face as natural wood.
            for face_name, points in local_map.items():
                if face_name == wood_face:
                    continue
                wire = Part.makePolygon(points)
                try:
                    face = Part.Face(wire)
                except Exception:
                    continue
                if face_name == family_a:
                    bucket_a.append(face)
                elif face_name == family_b:
                    bucket_b.append(face)

    return bucket_a, bucket_b


def _qbert_segment_faces(rows, cell_mm, stagger, wood_face, segment_width, segment_index=0, forearm_length=None):
    half_w, half_h, _, y_pitch = _qbert_layout_params(cell_mm)
    family_a, family_b = _qbert_face_families(wood_face)

    # Y axis in local segment-plane coords is axial (along cue length).
    # Start from the front edge (+forearm_length/2) and step toward back.
    axial_half = (forearm_length / 2.0) if forearm_length else (segment_width / 2.0)
    y_start = axial_half
    # Auto-fill: override rows to cover the full forearm length.
    if forearm_length and y_pitch > 0:
        rows = max(rows, math.ceil(forearm_length / y_pitch))
    faces_a = []
    faces_b = []

    # Cube centered in the segment.
    cx = 0
    # Odd segments offset down by 3*half_h so the left/right midpoints of the odd
    # column's top face (cy+half_h) align with the bottom of the even column's side (cy-2*half_h).
    y_seg_offset = (3.0 * half_h) if (segment_index % 2 == 1) else 0.0

    for row in range(rows):
        y = y_start - row * y_pitch - y_seg_offset
        face_polys = _qbert_face_polygons(cx, y, half_w, half_h)
        for face_name, points in face_polys.items():
            if face_name == wood_face:
                continue
            try:
                face = Part.Face(Part.makePolygon(points))
            except Exception:
                continue
            if face_name == family_a:
                faces_a.append(face)
            elif face_name == family_b:
                faces_b.append(face)

    return faces_a, faces_b


def _qbert_face_families(wood_face):
    wood_face = str(wood_face).lower()
    # Keep two visibly different families: Top + one vertical face.
    if wood_face == 'front':
        return 'top', 'side'
    return 'top', 'front'


def _qbert_apply_corner_radius(shape, radius_mm, axis_dir=None):
    if not shape or getattr(shape, 'isNull', lambda: True)() or radius_mm <= 0:
        return shape, False

    axis = None
    if axis_dir is not None:
        try:
            axis = FreeCAD.Vector(axis_dir.x, axis_dir.y, axis_dir.z)
            if axis.Length > 1e-6:
                axis.normalize()
            else:
                axis = None
        except Exception:
            axis = None

    edges = []
    for edge in getattr(shape, 'Edges', []):
        if getattr(edge, 'Length', 0.0) <= 1e-6:
            continue
        if axis is None:
            edges.append(edge)
            continue
        verts = getattr(edge, 'Vertexes', [])
        if len(verts) < 2:
            continue
        direction = verts[-1].Point.sub(verts[0].Point)
        if direction.Length <= 1e-6:
            continue
        direction.normalize()
        # Keep only edges parallel to the segment's local Z axis.
        if abs(direction.dot(axis)) >= 0.995:
            edges.append(edge)

    if not edges:
        return shape, False
    try:
        filleted = shape.makeFillet(radius_mm, edges)
        if filleted and not filleted.isNull():
            return filleted, True
    except Exception:
        pass
    return shape, False


def _draw_polygon(sketch, points, cache):
    added = 0
    for i in range(len(points) - 1):
        added += _add_unique_segment_line(sketch, points[i], points[i + 1], cache)
    return added


def _inward_depth_vector(plane, depth_mm):
    axis_target = FreeCAD.Vector(0, plane.Placement.Base.y, 0)
    inward = axis_target.sub(plane.Placement.Base)
    if inward.Length <= 1e-6:
        return None
    inward.normalize()
    return inward.multiply(depth_mm)


def _segment_local_shapes(master_sketch, layout_plane):
    inverse_matrix = layout_plane.Placement.inverse().toMatrix()
    master_shape = master_sketch.Shape.copy()
    return master_shape.transformGeometry(inverse_matrix)


def _latest_segment_group(doc):
    groups = [
        obj for obj in getattr(doc, 'Objects', [])
        if getattr(obj, 'TypeId', '') == 'App::DocumentObjectGroup' and str(obj.Name).startswith('SegmentPlanes_')
    ]
    if not groups:
        return None
    groups.sort(key=lambda obj: _extract_trailing_number(str(obj.Name), 0))
    return groups[-1]


def _first_segment_plane(group):
    planes = _find_group_members(group, 'SegmentPlane_')
    if not planes:
        return None
    return planes[0]


def _segment_plane_from_sketch(sketch, fallback_plane=None):
    for prop in ('AttachmentSupport', 'Support'):
        support = getattr(sketch, prop, None)
        if not support:
            continue
        for entry in support:
            obj = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
            if not obj:
                continue

            name = str(getattr(obj, 'Name', '') or '')
            if name.startswith('SegmentPlane_'):
                return obj

            linked = getattr(obj, 'LinkedObject', None)
            linked_name = str(getattr(linked, 'Name', '') or '')
            if linked and linked_name.startswith('SegmentPlane_'):
                return linked
    return fallback_plane


def _sketch_world_matrix(sketch, fallback_plane=None):
    try:
        global_placement = sketch.getGlobalPlacement()
        if global_placement:
            return global_placement.toMatrix()
    except Exception:
        pass

    try:
        return sketch.Placement.toMatrix()
    except Exception:
        pass

    if fallback_plane:
        try:
            return fallback_plane.Placement.toMatrix()
        except Exception:
            pass
    return None


def _is_cam_job_object(obj):
    if not obj:
        return False
    try:
        return bool(hasattr(obj, 'Operations') and hasattr(obj, 'Model') and hasattr(obj, 'Stock'))
    except Exception:
        return False


def _segment_tile_info_from_name(name):
    raw = str(name or '')
    if not raw:
        return None, None

    # Preferred explicit format:
    #   SegmentDerivedTile_<family>_<segment>_<tile>
    # Accept prefixed variants too (e.g. Link_SegmentDerivedTile_top_1_4).
    tile_match = re.search(r'SegmentDerivedTile_([A-Za-z]+)_(\d+)_(\d+)', raw)
    if tile_match:
        return tile_match.group(1).strip().lower(), int(tile_match.group(2))

    # Legacy label format created by this workbench:
    #   SegmentDerived_<family>_<segment>_<tile>
    legacy_tile_match = re.search(r'SegmentDerived_([A-Za-z]+)_(\d+)_(\d+)', raw)
    if legacy_tile_match:
        return legacy_tile_match.group(1).strip().lower(), int(legacy_tile_match.group(2))

    # Older per-segment solids with no face family:
    #   SegmentDerivedShape_<segment>
    shape_match = re.search(r'SegmentDerivedShape_(\d+)', raw)
    if shape_match:
        return 'unclassified', int(shape_match.group(1))

    return None, None


def _segment_tile_info_from_object(obj):
    candidates = []
    try:
        candidates.append(getattr(obj, 'Name', ''))
    except Exception:
        pass
    try:
        candidates.append(getattr(obj, 'Label', ''))
    except Exception:
        pass

    linked = None
    try:
        linked = getattr(obj, 'LinkedObject', None)
    except Exception:
        linked = None
    if linked:
        try:
            candidates.append(getattr(linked, 'Name', ''))
        except Exception:
            pass
        try:
            candidates.append(getattr(linked, 'Label', ''))
        except Exception:
            pass

    for value in candidates:
        face_family, seg_idx = _segment_tile_info_from_name(value)
        if seg_idx is not None:
            return face_family, seg_idx
    return None, None


def _segment_solids_from_group(group, segment_count, include_families=None):
    include_set = None
    if include_families:
        try:
            include_set = set(str(v).strip().lower() for v in include_families if str(v).strip())
        except Exception:
            include_set = None

    out = {idx: [] for idx in range(1, int(segment_count) + 1)}
    for child in getattr(group, 'Group', []) or []:
        face_family, seg_idx = _segment_tile_info_from_object(child)
        if seg_idx is None or seg_idx < 1:
            continue
        if include_set is not None and face_family not in include_set:
            continue
        shape = getattr(child, 'Shape', None)
        if not shape or getattr(shape, 'isNull', lambda: True)():
            continue
        try:
            has_solids = bool(getattr(shape, 'Solids', None) and len(shape.Solids) > 0)
        except Exception:
            has_solids = False
        if not has_solids:
            continue
        out.setdefault(seg_idx, []).append(child)
    return out


def _segment_solids_from_document(doc, segment_count, include_families=None):
    include_set = None
    if include_families:
        try:
            include_set = set(str(v).strip().lower() for v in include_families if str(v).strip())
        except Exception:
            include_set = None

    out = {idx: [] for idx in range(1, int(segment_count) + 1)}
    for obj in getattr(doc, 'Objects', []) or []:
        face_family, seg_idx = _segment_tile_info_from_object(obj)
        if seg_idx is None or seg_idx < 1:
            continue
        if include_set is not None and face_family not in include_set:
            continue
        shape = getattr(obj, 'Shape', None)
        if not shape or getattr(shape, 'isNull', lambda: True)():
            continue
        try:
            has_solids = bool(getattr(shape, 'Solids', None) and len(shape.Solids) > 0)
        except Exception:
            has_solids = False
        if not has_solids:
            continue
        out.setdefault(seg_idx, []).append(obj)
    return out


def _segment_source_debug_summary(doc, limit=20):
    rows = []
    for obj in getattr(doc, 'Objects', []) or []:
        names = [str(getattr(obj, 'Name', '') or ''), str(getattr(obj, 'Label', '') or '')]
        linked = getattr(obj, 'LinkedObject', None)
        if linked:
            names.append(str(getattr(linked, 'Name', '') or ''))
            names.append(str(getattr(linked, 'Label', '') or ''))
        text_blob = ' | '.join(v for v in names if v)
        if 'SegmentDerived' not in text_blob:
            continue

        family, seg_idx = _segment_tile_info_from_object(obj)
        shape = getattr(obj, 'Shape', None)
        shape_type = ''
        solid_count = 0
        if shape and (not getattr(shape, 'isNull', lambda: True)()):
            shape_type = str(getattr(shape, 'ShapeType', '') or '')
            try:
                solid_count = len(getattr(shape, 'Solids', []) or [])
            except Exception:
                solid_count = 0
        rows.append((str(getattr(obj, 'Name', '') or ''), family, seg_idx, shape_type, solid_count))

    rows.sort(key=lambda item: item[0])
    if len(rows) > limit:
        rows = rows[:limit]
    return rows


def _build_segment_pocket_model_shape(tile_shapes):
    valid_shapes = [shape for shape in (tile_shapes or []) if shape and (not shape.isNull())]
    if not valid_shapes:
        return None

    try:
        tool_shape = Part.makeCompound(valid_shapes)
    except Exception:
        return None

    try:
        bb = tool_shape.BoundBox
    except Exception:
        return None
    if not bb:
        return None

    side_margin = 0.1 * 25.4
    z_extra = 0.01 * 25.4
    x_min = float(bb.XMin) - side_margin
    y_min = float(bb.YMin) - side_margin
    z_min = float(bb.ZMin) - z_extra
    x_len = max(0.01, float(bb.XLength) + (2.0 * side_margin))
    y_len = max(0.01, float(bb.YLength) + (2.0 * side_margin))
    z_len = max(0.01, float(bb.ZLength) + (2.0 * z_extra))

    try:
        blank_shape = Part.makeBox(x_len, y_len, z_len, FreeCAD.Vector(x_min, y_min, z_min), FreeCAD.Vector(0, 0, 1))
        pocket_shape = blank_shape.cut(tool_shape)
    except Exception:
        return None

    if not pocket_shape or pocket_shape.isNull():
        return None

    try:
        if not getattr(pocket_shape, 'Solids', None):
            return None
    except Exception:
        return None

    try:
        pocket_shape = pocket_shape.removeSplitter()
    except Exception:
        pass
    return pocket_shape


class SegmentPocketCAMJobsCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Segment Pocket CAM Jobs',
            'ToolTip': 'Create one pocket CAM job per segment plane and store B-index metadata per segment',
        }

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError('No active document.\n')
            return

        selection = FreeCADGui.Selection.getSelection() if FreeCADGui else []
        group = _resolve_segment_group(selection[0]) if selection else None
        if not group:
            group = _latest_segment_group(doc)
        if not group:
            FreeCAD.Console.PrintError('Could not find a SegmentPlanes_* group. Create section planes first.\n')
            return

        segment_planes = _find_group_members(group, 'SegmentPlane_')
        if not segment_planes:
            FreeCAD.Console.PrintError('Segment group is missing SegmentPlane_* objects.\n')
            return

        segment_count = len(segment_planes)
        target_families = {'top'}
        segment_solids = _segment_solids_from_group(group, segment_count, include_families=target_families)
        total_source_solids = sum(len(values) for values in segment_solids.values())
        if total_source_solids <= 0:
            # Fallback for documents where segment-derived tiles exist but are not direct members
            # of the selected SegmentPlanes_* group.
            segment_solids = _segment_solids_from_document(doc, segment_count, include_families=target_families)
            total_source_solids = sum(len(values) for values in segment_solids.values())
        if total_source_solids <= 0:
            # Last resort for legacy/generated cases where solids exist but do not carry family tags.
            segment_solids = _segment_solids_from_document(doc, segment_count, include_families={'unclassified'})
            total_source_solids = sum(len(values) for values in segment_solids.values())
            if total_source_solids > 0:
                FreeCAD.Console.PrintWarning(
                    '[SegmentCAM] No top-family tiles detected; using unclassified SegmentDerivedShape_* solids.\n'
                )
        if total_source_solids <= 0:
            debug_rows = _segment_source_debug_summary(doc)
            if debug_rows:
                FreeCAD.Console.PrintWarning('[SegmentCAM] Detected segment-like objects (name, family, seg, shape, solids):\n')
                for name, family, seg_idx, shape_type, solid_count in debug_rows:
                    FreeCAD.Console.PrintWarning(
                        f'  - {name}: family={family}, seg={seg_idx}, shape={shape_type}, solids={solid_count}\n'
                    )
            else:
                FreeCAD.Console.PrintWarning('[SegmentCAM] No SegmentDerived* objects found in document.\n')
            FreeCAD.Console.PrintError(
                'No matching segment-derived solids found for top family. Ensure SegmentDerivedTile_top_* solids exist and are recomputed.\n'
            )
            return

        try:
            import inlays
        except Exception as exc:
            FreeCAD.Console.PrintError(f'Failed to load inlays module: {exc}\n')
            return

        suffix = _extract_trailing_number(str(getattr(group, 'Name', '')), 0)
        cam_group_name = f'SegmentCAM_{suffix}'
        cam_group = doc.getObject(cam_group_name)
        if not cam_group:
            cam_group = doc.addObject('App::DocumentObjectGroup', cam_group_name)
            cam_group.Label = f'Segment CAM {getattr(group, "Label", group.Name)}'

        created_models = []
        created_jobs = []
        failed_segments = []

        for seg_idx in range(1, segment_count + 1):
            solids_for_segment = segment_solids.get(seg_idx, []) or []
            if not solids_for_segment:
                continue

            shapes = []
            for obj in solids_for_segment:
                shape = getattr(obj, 'Shape', None)
                if shape and (not shape.isNull()):
                    shapes.append(shape)
            if not shapes:
                failed_segments.append(seg_idx)
                continue

            model_name = f'SegmentPocketModel_{suffix}_{seg_idx}'
            model_obj = doc.getObject(model_name)
            if not model_obj:
                model_obj = doc.addObject('Part::Feature', model_name)
            model_obj.Label = f'Segment {seg_idx} Pocket Model (Top)'
            pocket_shape = _build_segment_pocket_model_shape(shapes)
            if not pocket_shape:
                failed_segments.append(seg_idx)
                continue
            try:
                model_obj.Shape = pocket_shape
            except Exception:
                failed_segments.append(seg_idx)
                continue

            created_models.append(model_obj)
            try:
                cam_group.addObject(model_obj)
            except Exception:
                pass

            before_job_names = set()
            for obj in getattr(doc, 'Objects', []) or []:
                name = str(getattr(obj, 'Name', '') or '')
                if name and _is_cam_job_object(obj):
                    before_job_names.add(name)

            try:
                inlays.create_pocket_cnc_job(
                    target=model_obj,
                    show_dialog=False,
                    skip_fillet_warning=True,
                )
            except Exception as exc:
                FreeCAD.Console.PrintError(f'[SegmentCAM] Segment {seg_idx}: Pocket job failed: {exc}\n')
                failed_segments.append(seg_idx)
                continue

            after_jobs = []
            for obj in getattr(doc, 'Objects', []) or []:
                name = str(getattr(obj, 'Name', '') or '')
                if not name or name in before_job_names:
                    continue
                if _is_cam_job_object(obj):
                    after_jobs.append(obj)

            b_rotate_deg = float(seg_idx - 1) * (360.0 / float(segment_count))
            for job in after_jobs:
                try:
                    if not hasattr(job, 'ButlerSegmentIndex'):
                        job.addProperty('App::PropertyInteger', 'ButlerSegmentIndex', 'ButlerCues Segment CAM', 'Segment index (1-based)')
                    job.ButlerSegmentIndex = int(seg_idx)
                except Exception:
                    pass
                try:
                    if not hasattr(job, 'ButlerBRotateDeg'):
                        job.addProperty('App::PropertyFloat', 'ButlerBRotateDeg', 'ButlerCues Segment CAM', 'B-axis rotation for this segment in degrees')
                    job.ButlerBRotateDeg = float(b_rotate_deg)
                except Exception:
                    pass
                try:
                    job.Label = f'Segment {seg_idx} Top Pocket Job (B={b_rotate_deg:.3f} deg)'
                except Exception:
                    pass
                try:
                    cam_group.addObject(job)
                except Exception:
                    pass
                created_jobs.append(job)

        try:
            doc.recompute()
        except Exception:
            pass

        FreeCAD.Console.PrintMessage(
            f'[SegmentCAM] Built {len(created_jobs)} top-material segment pocket job(s) from {len(created_models)} segment model(s).\n'
        )
        if failed_segments:
            failed_text = ', '.join(str(v) for v in sorted(set(failed_segments)))
            FreeCAD.Console.PrintError(f'[SegmentCAM] Failed segments: {failed_text}\n')

    def IsActive(self):
        return True


def _master_sketch_non_construction_shape(master_sketch):
    edges = []
    for index, geometry in enumerate(getattr(master_sketch, 'Geometry', [])):
        try:
            if master_sketch.getConstruction(index):
                continue
        except Exception:
            pass
        shape = None
        try:
            shape = geometry.toShape()
        except Exception:
            shape = None
        if not shape:
            continue
        if hasattr(shape, 'Edges') and shape.Edges:
            edges.extend(shape.Edges)
        elif getattr(shape, 'ShapeType', '') == 'Edge':
            edges.append(shape)
    if not edges:
        return None
    return Part.makeCompound(edges)


def _master_sketch_poly_segments(master_sketch, transform_matrix=None):
    segments = []
    for index, geometry in enumerate(getattr(master_sketch, 'Geometry', [])):
        try:
            if master_sketch.getConstruction(index):
                continue
        except Exception:
            pass
        try:
            shape = geometry.toShape()
        except Exception:
            continue
        if not shape:
            continue
        if transform_matrix is not None:
            try:
                shape = shape.transformGeometry(transform_matrix)
            except Exception:
                pass
        try:
            points = shape.discretize(0.4)
        except Exception:
            points = [v.Point for v in getattr(shape, 'Vertexes', [])]
        if len(points) < 2:
            continue
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            if (p2.sub(p1)).Length > 1e-6:
                segments.append((p1, p2))
    return segments


def _master_shape_local_poly_segments(master_sketch, layout_plane):
    inv = layout_plane.Placement.inverse().toMatrix()
    return _master_sketch_poly_segments(master_sketch, transform_matrix=inv)


def _as_float(value, default=0.0):
    try:
        return float(getattr(value, 'Value', value))
    except Exception:
        return default


def _count_points_in_rect(segments, x_min, x_max, y_min, y_max):
    count = 0
    for p1, p2 in segments:
        if x_min <= p1.x <= x_max and y_min <= p1.y <= y_max:
            count += 1
        if x_min <= p2.x <= x_max and y_min <= p2.y <= y_max:
            count += 1
    return count


def _add_line_segments_to_sketch(sketch, segments):
    seen = set()
    added = 0
    for p1, p2 in segments:
        if (p2.sub(p1)).Length <= 1e-5:
            continue
        key = (
            round(min(p1.x, p2.x), 5),
            round(min(p1.y, p2.y), 5),
            round(max(p1.x, p2.x), 5),
            round(max(p1.y, p2.y), 5),
        )
        if key in seen:
            continue
        seen.add(key)
        try:
            sketch.addGeometry(Part.LineSegment(p1, p2), False)
            added += 1
        except Exception:
            continue
    return added


def _clip_segment_to_rect(p1, p2, x_min, x_max, y_min, y_max):
    # Liang-Barsky clipping in layout XY coordinates.
    dx = p2.x - p1.x
    dy = p2.y - p1.y
    t0 = 0.0
    t1 = 1.0
    checks = [
        (-dx, p1.x - x_min),
        (dx, x_max - p1.x),
        (-dy, p1.y - y_min),
        (dy, y_max - p1.y),
    ]
    for p, q in checks:
        if abs(p) < 1e-12:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return None
            if t > t0:
                t0 = t
        else:
            if t < t0:
                return None
            if t < t1:
                t1 = t
    c1 = FreeCAD.Vector(p1.x + t0 * dx, p1.y + t0 * dy, 0)
    c2 = FreeCAD.Vector(p1.x + t1 * dx, p1.y + t1 * dy, 0)
    if (c2.sub(c1)).Length <= 1e-6:
        return None
    return c1, c2


def _build_faces_from_edges(edge_shapes):
    faces = []
    if not edge_shapes:
        return faces
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
            faces.append(face)
        except Exception:
            continue
    return faces


def _master_faces_from_sketch(master_sketch, transform_matrix=None):
    local_edges = []
    for index, geometry in enumerate(getattr(master_sketch, 'Geometry', [])):
        try:
            if master_sketch.getConstruction(index):
                continue
        except Exception:
            pass
        try:
            shape = geometry.toShape()
        except Exception:
            continue
        if not shape:
            continue
        if transform_matrix is not None:
            try:
                shape = shape.transformGeometry(transform_matrix)
            except Exception:
                pass
        for edge in getattr(shape, 'Edges', []):
            local_edges.append(edge)

    faces = _build_faces_from_edges(local_edges)
    if faces:
        return faces

    # Fallback path: Sketcher can still report closed wires/faces in Shape even when
    # per-geometry edge stitching above fails due to tiny tolerances.
    try:
        sketch_shape = master_sketch.Shape.copy()
        if transform_matrix is not None:
            sketch_shape = sketch_shape.transformGeometry(transform_matrix)
    except Exception:
        sketch_shape = None

    if not sketch_shape:
        return faces

    try:
        shape_faces = list(getattr(sketch_shape, 'Faces', []) or [])
    except Exception:
        shape_faces = []
    for face in shape_faces:
        try:
            if abs(float(getattr(face, 'Area', 0.0) or 0.0)) > 1e-9:
                faces.append(face)
        except Exception:
            continue
    if faces:
        return faces

    try:
        shape_wires = list(getattr(sketch_shape, 'Wires', []) or [])
    except Exception:
        shape_wires = []
    for wire in shape_wires:
        try:
            if not wire.isClosed():
                continue
            face = Part.Face(wire)
            if abs(float(getattr(face, 'Area', 0.0) or 0.0)) > 1e-9:
                faces.append(face)
        except Exception:
            continue

    return faces


def _non_construction_geometry_count(sketch):
    count = 0
    for index, _ in enumerate(getattr(sketch, 'Geometry', [])):
        try:
            if sketch.getConstruction(index):
                continue
        except Exception:
            pass
        count += 1
    return count


def _face_overlap_area(faces, rect_face):
    total = 0.0
    for face in faces:
        try:
            inter = face.common(rect_face)
        except Exception:
            continue
        for f in getattr(inter, 'Faces', []):
            total += abs(getattr(f, 'Area', 0.0))
    return total


def _extrude_local_faces_to_solids(local_faces, plane):
    if not local_faces:
        return None
    axis_target = FreeCAD.Vector(0, plane.Placement.Base.y, 0)
    inward_vec = axis_target.sub(plane.Placement.Base)
    if inward_vec.Length <= 1e-6:
        return None
    solids = []
    world_matrix = plane.Placement.toMatrix()
    for face in local_faces:
        try:
            world_face = face.transformGeometry(world_matrix)
            solids.append(world_face.extrude(inward_vec))
        except Exception:
            continue
    if not solids:
        return None
    return Part.makeCompound(solids)


def _build_segment_solid(edge_shapes, plane, boundary_edges=None, strip_area=None):
    if not edge_shapes:
        return None

    faces = _build_faces_from_edges(edge_shapes)

    # Forced-closure fallback: use segment boundary edges to cap open contours.
    if not faces and boundary_edges:
        augmented_faces = _build_faces_from_edges(edge_shapes + boundary_edges)
        if strip_area and strip_area > 1e-6:
            # Exclude the full strip rectangle while keeping valid enclosed regions.
            max_allowed = strip_area * 0.98
            faces = [f for f in augmented_faces if 1e-6 < abs(f.Area) < max_allowed]
        else:
            faces = augmented_faces

    if not faces:
        return None

    # Extrude toward the global Y axis (radial inward), not toward world origin.
    axis_target = FreeCAD.Vector(0, plane.Placement.Base.y, 0)
    origin_vector = axis_target.sub(plane.Placement.Base)
    if origin_vector.Length <= 1e-6:
        return None

    solids = []
    for face in faces:
        try:
            solids.append(face.extrude(origin_vector))
        except Exception:
            continue
    if not solids:
        return None
    return Part.makeCompound(solids)


def _build_segment_boundary_solid(plane, segment_width, y_min_local, y_max_local):
    axis_target = FreeCAD.Vector(0, plane.Placement.Base.y, 0)
    inward_vec = axis_target.sub(plane.Placement.Base)
    if inward_vec.Length <= 1e-6:
        return None

    rect_local = [
        FreeCAD.Vector(-segment_width / 2.0, y_min_local, 0),
        FreeCAD.Vector(segment_width / 2.0, y_min_local, 0),
        FreeCAD.Vector(segment_width / 2.0, y_max_local, 0),
        FreeCAD.Vector(-segment_width / 2.0, y_max_local, 0),
        FreeCAD.Vector(-segment_width / 2.0, y_min_local, 0),
    ]
    rect_world = [plane.Placement.multVec(p) for p in rect_local]
    try:
        rect_face = Part.Face(Part.makePolygon(rect_world))
    except Exception:
        return None
    try:
        return rect_face.extrude(inward_vec)
    except Exception:
        return None


def _clip_solid_to_boundary(candidate_solid, boundary_solid):
    if not candidate_solid or not boundary_solid:
        return candidate_solid
    try:
        clipped = candidate_solid.common(boundary_solid)
    except Exception:
        return candidate_solid
    if getattr(clipped, 'isNull', lambda: True)():
        return candidate_solid
    return clipped

class TilingTileArrayCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Tile Polar Array',
            'ToolTip': 'Create a polar array of tiles with user settings.'
        }

    def Activated(self):
        panel = TilingTaskPanel()
        FreeCADGui.Control.showDialog(panel)

    def IsActive(self):
        return True


class SegmentSectionPlanesCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Segment Section Planes',
            'ToolTip': 'Create tangent planes around a cue section plus one flat layout sketch for wrapped artwork.'
        }

    def Activated(self):
        panel = SegmentSectionPlanesTaskPanel()
        FreeCADGui.Control.showDialog(panel)

    def IsActive(self):
        return True


class SplitSegmentMasterSketchCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Split Master Sketch To Segments',
            'ToolTip': 'Clip the flat master sketch into one derived shape per wrapped segment plane.'
        }

    def Activated(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError("No active document!\n")
            return

        sel = FreeCADGui.Selection.getSelection()
        group = _resolve_segment_group(sel[0]) if sel else None
        if not group:
            group = _latest_segment_group(doc)
        if not group:
            FreeCAD.Console.PrintError("Could not find a segment plane group. Select one or create segment planes first.\n")
            return
        FreeCAD.Console.PrintMessage(f"[Split] Using group: {group.Name}\n")

        layout_plane = _find_group_member(group, 'SegmentLayoutPlane')
        master_sketch = _find_group_member(group, 'SegmentMasterSketch')
        segment_planes = _find_group_members(group, 'SegmentPlane_')
        if not layout_plane or not master_sketch or not segment_planes:
            FreeCAD.Console.PrintError("Segment group is missing its layout plane, master sketch, or wrapped planes.\n")
            return

        layout_length = _as_float(getattr(layout_plane, 'Length', 0.0), 0.0)
        layout_width = _as_float(getattr(layout_plane, 'Width', 0.0), 0.0)
        if layout_length <= 0.0 or layout_width <= 0.0:
            FreeCAD.Console.PrintError("Layout plane has invalid dimensions. Recreate segment planes.\n")
            return

        x_min_all = -layout_length / 2.0
        x_max_all = layout_length / 2.0
        y_min_all = -layout_width / 2.0
        y_max_all = layout_width / 2.0

        inv = layout_plane.Placement.inverse().toMatrix()
        raw_segments = _master_sketch_poly_segments(master_sketch)
        transformed_segments = _master_shape_local_poly_segments(master_sketch, layout_plane)

        raw_faces = _master_faces_from_sketch(master_sketch)
        transformed_faces = _master_faces_from_sketch(master_sketch, transform_matrix=inv)

        full_rect_wire = Part.makePolygon([
            FreeCAD.Vector(x_min_all, y_min_all, 0),
            FreeCAD.Vector(x_max_all, y_min_all, 0),
            FreeCAD.Vector(x_max_all, y_max_all, 0),
            FreeCAD.Vector(x_min_all, y_max_all, 0),
            FreeCAD.Vector(x_min_all, y_min_all, 0),
        ])
        full_rect_face = Part.Face(full_rect_wire)

        raw_hits = _count_points_in_rect(raw_segments, x_min_all, x_max_all, y_min_all, y_max_all)
        transformed_hits = _count_points_in_rect(transformed_segments, x_min_all, x_max_all, y_min_all, y_max_all)
        raw_face_overlap = _face_overlap_area(raw_faces, full_rect_face)
        transformed_face_overlap = _face_overlap_area(transformed_faces, full_rect_face)

        use_transformed = (transformed_hits + transformed_face_overlap) > (raw_hits + raw_face_overlap)
        local_segments = transformed_segments if use_transformed else raw_segments
        local_master_faces = transformed_faces if use_transformed else raw_faces

        FreeCAD.Console.PrintMessage(
            f"[Split] Local closed faces: {len(local_master_faces)} (raw_face_overlap={raw_face_overlap:.3f}, transformed_face_overlap={transformed_face_overlap:.3f})\n"
        )

        if not local_segments:
            FreeCAD.Console.PrintWarning("Master sketch has no non-construction geometry to split.\n")
            return
        FreeCAD.Console.PrintMessage(
            f"[Split] Source segment count: {len(local_segments)} (raw_hits={raw_hits}, transformed_hits={transformed_hits}, mode={'transformed' if use_transformed else 'raw'})\n"
        )

        segment_count = len(segment_planes)
        segment_width = (x_max_all - x_min_all) / float(segment_count)
        created = []

        for existing in _find_group_members(group, 'SegmentDerivedSketch_'):
            try:
                doc.removeObject(existing.Name)
            except Exception:
                pass
        for existing in _find_group_members(group, 'SegmentDerivedShape_'):
            try:
                doc.removeObject(existing.Name)
            except Exception:
                pass

        for index, plane in enumerate(segment_planes):
            x_min = x_min_all + index * segment_width
            x_max = x_min + segment_width
            epsilon = 1e-4
            # Avoid duplicate/overlap on shared strip boundaries.
            x_clip_min = x_min + (epsilon if index > 0 else 0.0)
            x_clip_max = x_max - (epsilon if index < (segment_count - 1) else 0.0)
            shift_x = -((x_min + x_max) / 2.0)
            clipped_segments = []
            for p1, p2 in local_segments:
                clipped = _clip_segment_to_rect(p1, p2, x_clip_min, x_clip_max, y_min_all, y_max_all)
                if not clipped:
                    continue
                c1, c2 = clipped
                clipped_segments.append((
                    FreeCAD.Vector(c1.x + shift_x, c1.y, 0),
                    FreeCAD.Vector(c2.x + shift_x, c2.y, 0),
                ))
            shape_name = f'SegmentDerivedShape_{index + 1}'
            derived_shape = doc.addObject('Part::Feature', shape_name)
            boundary_solid = _build_segment_boundary_solid(plane, segment_width, y_min_all, y_max_all)

            # Preferred path: clip true closed faces in layout space, then extrude.
            segment_solid = None
            if local_master_faces:
                try:
                    strip_wire = Part.makePolygon([
                        FreeCAD.Vector(x_clip_min, y_min_all, 0),
                        FreeCAD.Vector(x_clip_max, y_min_all, 0),
                        FreeCAD.Vector(x_clip_max, y_max_all, 0),
                        FreeCAD.Vector(x_clip_min, y_max_all, 0),
                        FreeCAD.Vector(x_clip_min, y_min_all, 0),
                    ])
                    strip_face = Part.Face(strip_wire)
                    clipped_local_faces = []
                    for local_face in local_master_faces:
                        inter = local_face.common(strip_face)
                        for f in getattr(inter, 'Faces', []):
                            if abs(getattr(f, 'Area', 0.0)) > 1e-6:
                                ff = f.copy()
                                ff.translate(FreeCAD.Vector(shift_x, 0, 0))
                                clipped_local_faces.append(ff)
                    if clipped_local_faces:
                        segment_solid = _extrude_local_faces_to_solids(clipped_local_faces, plane)
                        segment_solid = _clip_solid_to_boundary(segment_solid, boundary_solid)
                except Exception:
                    segment_solid = None

            if segment_solid:
                derived_shape.Shape = segment_solid
                FreeCAD.Console.PrintMessage(f"[Split] Segment {index + 1}: solid built from face clip\n")
                created.append(derived_shape)
                continue

            if clipped_segments:
                edge_shapes = []
                for p1, p2 in clipped_segments:
                    gp1 = plane.Placement.multVec(p1)
                    gp2 = plane.Placement.multVec(p2)
                    if (gp2.sub(gp1)).Length <= 1e-5:
                        continue
                    edge_shapes.append(Part.LineSegment(gp1, gp2).toShape())

                strip_corners_local = [
                    FreeCAD.Vector(-segment_width / 2.0, y_min_all, 0),
                    FreeCAD.Vector(segment_width / 2.0, y_min_all, 0),
                    FreeCAD.Vector(segment_width / 2.0, y_max_all, 0),
                    FreeCAD.Vector(-segment_width / 2.0, y_max_all, 0),
                ]
                boundary_edges = []
                for i in range(4):
                    c1 = strip_corners_local[i]
                    c2 = strip_corners_local[(i + 1) % 4]
                    gc1 = plane.Placement.multVec(c1)
                    gc2 = plane.Placement.multVec(c2)
                    boundary_edges.append(Part.LineSegment(gc1, gc2).toShape())

                strip_area = segment_width * (y_max_all - y_min_all)
                segment_solid = _build_segment_solid(
                    edge_shapes,
                    plane,
                    boundary_edges=boundary_edges,
                    strip_area=strip_area,
                )
                segment_solid = _clip_solid_to_boundary(segment_solid, boundary_solid)
                if segment_solid:
                    derived_shape.Shape = segment_solid
                    FreeCAD.Console.PrintMessage(f"[Split] Segment {index + 1}: {len(edge_shapes)} clipped edges, solid built and clipped to plane boundary\n")
                elif edge_shapes:
                    derived_shape.Shape = Part.makeCompound(edge_shapes)
                    FreeCAD.Console.PrintMessage(f"[Split] Segment {index + 1}: {len(edge_shapes)} clipped edges, open-wire fallback\n")
                else:
                    derived_shape.Shape = Part.Shape()
            else:
                derived_shape.Shape = Part.Shape()
                FreeCAD.Console.PrintMessage(f"[Split] Segment {index + 1}: 0 clipped edges\n")

            created.append(derived_shape)

        doc.recompute()
        if created:
            group.addObjects(created)
            FreeCAD.Console.PrintMessage(f"Created {len(created)} segment shapes from the master sketch.\n")
        else:
            FreeCAD.Console.PrintWarning("No non-construction geometry was found inside the segment boundaries.\n")

    def IsActive(self):
        return True


class QbertPatternToMasterCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Qbert Pattern To Master Sketch',
            'ToolTip': 'Generate a Q*bert-inspired isometric cube pattern on SegmentMasterSketch.'
        }

    def Activated(self):
        panel = QbertPatternTaskPanel()
        FreeCADGui.Control.showDialog(panel)

    def IsActive(self):
        return True


class QbertPocketSolidsCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Qbert Pocket Solids',
            'ToolTip': 'Generate clipped Q*bert pocket solids as two inlay families, leaving one face as natural wood.'
        }

    def Activated(self):
        panel = QbertPocketSolidsTaskPanel()
        FreeCADGui.Control.showDialog(panel)

    def IsActive(self):
        return True

class TilingTaskPanel:
    def __init__(self):
        self.form = QtGui.QWidget()
        layout = QtGui.QFormLayout(self.form)
        saved_tile_size = TILING_PREFS.GetFloat('tile_size_in', DEFAULT_TILE_SIZE_IN)
        saved_segments = TILING_PREFS.GetInt('segments', DEFAULT_SEGMENTS)
        saved_pad_depth = TILING_PREFS.GetFloat('pad_depth_in', DEFAULT_PAD_DEPTH_IN)
        saved_row_offset = TILING_PREFS.GetFloat('row_offset_segments', DEFAULT_ROW_OFFSET_SEGMENTS)
        self.tile_size = QtGui.QDoubleSpinBox()
        self.tile_size.setMinimum(0.01)
        self.tile_size.setValue(saved_tile_size)
        self.tile_size.setSuffix(' in')
        layout.addRow('Tile Size (in):', self.tile_size)
        self.segments = QtGui.QSpinBox()
        self.segments.setMinimum(2)
        self.segments.setMaximum(360)
        self.segments.setValue(saved_segments)
        layout.addRow('Number of Segments:', self.segments)
        self.pad_depth = QtGui.QDoubleSpinBox()
        self.pad_depth.setMinimum(0.01)
        self.pad_depth.setValue(saved_pad_depth)
        self.pad_depth.setSuffix(' in')
        layout.addRow('Pad Depth (in):', self.pad_depth)
        self.row_offset = QtGui.QDoubleSpinBox()
        self.row_offset.setMinimum(0.0)
        self.row_offset.setMaximum(2.0)
        self.row_offset.setSingleStep(0.05)
        self.row_offset.setDecimals(2)
        self.row_offset.setValue(saved_row_offset)
        self.row_offset.setSuffix(' seg')
        layout.addRow('Row Offset (segments):', self.row_offset)

        self.form.setLayout(layout)


    def accept(self):

        tile_size_in = self.tile_size.value()
        segments = self.segments.value()
        pad_depth_in = self.pad_depth.value()
        row_offset_segments = self.row_offset.value()

        TILING_PREFS.SetFloat('tile_size_in', tile_size_in)
        TILING_PREFS.SetInt('segments', segments)
        TILING_PREFS.SetFloat('pad_depth_in', pad_depth_in)
        TILING_PREFS.SetFloat('row_offset_segments', row_offset_segments)

        tile_size = tile_size_in * 25.4  # inches to mm
        pad_depth = pad_depth_in * 25.4  # inches to mm
        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError("No active document!\n")
            return False

        sel = FreeCADGui.Selection.getSelection()
        if not sel or len(sel) != 1:
            FreeCAD.Console.PrintError("Select one object to tile around.\n")
            return False
        target = sel[0]



        solid_obj, shape = _find_solid(target)
        if not shape:
            FreeCAD.Console.PrintError("Selected object or its children are not a solid.\n")
            return False
        target = solid_obj


        import PartDesign
        bounds = shape.BoundBox
        top_z = bounds.ZMax
        center_y = (bounds.YMin + bounds.YMax) / 2.0
        start_y = bounds.YMax - (tile_size / 2.0)
        section_length = max(bounds.YLength, tile_size)
        section_span = max(bounds.XLength, bounds.ZLength, tile_size)
        length_occurrences = max(1, int(math.floor(section_length / tile_size)))
        length_span = max(0.0, section_length - tile_size)
        if top_z <= 0 and not shape.Vertexes:
            FreeCAD.Console.PrintError("Could not determine the selected solid's top Z value.\n")
            return False

        # Seed plane at the solid's highest Z value, parallel to XY.
        base_plane = doc.addObject('PartDesign::Plane', 'TilingBasePlane')
        base_plane.MapMode = 'Deactivated'
        base_plane.Placement.Base = FreeCAD.Vector(0, center_y, top_z)
        base_plane.Placement.Rotation = FreeCAD.Rotation()
        try:
            base_plane.ResizeMode = 'Manual'
        except Exception:
            pass
        try:
            base_plane.Length = section_span
            base_plane.Width = section_length
        except Exception:
            pass
        doc.recompute()


        s = tile_size / 2.0
        sketch_rotation = FreeCAD.Rotation(FreeCAD.Vector(0, 0, 1), 180)
        def build_tile_sketch(sketch):
            try:
                sketch.addRectangle(-s, -s, s, s)
            except Exception:
                lines = [
                    Part.LineSegment(FreeCAD.Vector(-s, -s, 0), FreeCAD.Vector(s, -s, 0)),
                    Part.LineSegment(FreeCAD.Vector(s, -s, 0), FreeCAD.Vector(s, s, 0)),
                    Part.LineSegment(FreeCAD.Vector(s, s, 0), FreeCAD.Vector(-s, s, 0)),
                    Part.LineSegment(FreeCAD.Vector(-s, s, 0), FreeCAD.Vector(-s, -s, 0)),
                ]
                for line in lines:
                    sketch.addGeometry(line, False)


        # Create a group/container for all tiling objects
        container_name = f"Tiling_{target.Name}"
        if hasattr(doc, 'addObject'):
            group = doc.addObject('App::DocumentObjectGroup', container_name)
        else:
            group = None

        tiling_body = doc.addObject('PartDesign::Body', 'TilingBody')
        created_objs = [base_plane, tiling_body]

        sketch = tiling_body.newObject('Sketcher::SketchObject', 'TilingSketch')
        sketch.MapMode = 'Deactivated'
        sketch.Placement = FreeCAD.Placement(
            FreeCAD.Vector(0, start_y, top_z),
            sketch_rotation
        )
        build_tile_sketch(sketch)
        created_objs.append(sketch)
        doc.recompute()

        pad = tiling_body.newObject('PartDesign::Pad', 'TilingPad')
        pad.Profile = sketch
        pad.Length = pad_depth
        try:
            pad.Reversed = True
        except Exception:
            pass
        created_objs.append(pad)
        doc.recompute()

        segment_angle = 360.0 / float(segments)
        row_phase = segment_angle * row_offset_segments
        pattern_shapes = []
        for row_index in range(length_occurrences):
            y_offset = -row_index * tile_size
            angle_offset = row_phase if (row_index % 2) else 0.0
            for segment_index in range(segments):
                placed_shape = pad.Shape.copy()
                placed_shape.translate(FreeCAD.Vector(0, y_offset, 0))
                placed_shape.rotate(FreeCAD.Vector(0, 0, 0), FreeCAD.Vector(0, 1, 0), segment_index * segment_angle + angle_offset)
                pattern_shapes.append(placed_shape)

        pattern_feature = doc.addObject('Part::Feature', 'TilingPattern')
        pattern_feature.Shape = Part.makeCompound(pattern_shapes)
        created_objs.append(pattern_feature)
        try:
            tiling_body.ViewObject.Visibility = False
        except Exception:
            pass
        doc.recompute()

        # Add all created objects to the group/container
        if group:
            group.addObjects(created_objs)

        FreeCAD.Console.PrintMessage(f"Created {segments} tiles of size {tile_size/25.4:.3f} in and depth {pad_depth/25.4:.3f} in.\n")
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True


class SegmentSectionPlanesTaskPanel:
    def __init__(self):
        self.form = QtGui.QWidget()
        layout = QtGui.QFormLayout(self.form)
        saved_segments = SEGMENT_PREFS.GetInt('segments', DEFAULT_SECTION_SEGMENTS)
        saved_surface_lift = SEGMENT_PREFS.GetFloat('surface_lift_in', DEFAULT_SEGMENT_SURFACE_LIFT_IN)

        self.segments = QtGui.QSpinBox()
        self.segments.setMinimum(2)
        self.segments.setMaximum(64)
        self.segments.setValue(saved_segments)
        layout.addRow('Section Segments:', self.segments)

        self.surface_lift = QtGui.QDoubleSpinBox()
        self.surface_lift.setMinimum(0.0)
        self.surface_lift.setMaximum(0.1000)
        self.surface_lift.setDecimals(4)
        self.surface_lift.setSingleStep(0.0010)
        self.surface_lift.setValue(saved_surface_lift)
        self.surface_lift.setSuffix(' in')
        layout.addRow('Plane Lift:', self.surface_lift)

        self.form.setLayout(layout)

    def accept(self):
        segments = self.segments.value()
        create_sketches = False
        surface_lift_in = self.surface_lift.value()
        surface_lift_mm = float(surface_lift_in) * 25.4

        SEGMENT_PREFS.SetInt('segments', segments)
        SEGMENT_PREFS.SetBool('create_sketches', create_sketches)
        SEGMENT_PREFS.SetFloat('surface_lift_in', surface_lift_in)

        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError("No active document!\n")
            return False

        sel = FreeCADGui.Selection.getSelection()
        if not sel or len(sel) != 1:
            FreeCAD.Console.PrintError("Select one cue section to segment.\n")
            return False
        target = sel[0]

        solid_obj, shape = _find_solid(target)
        if not shape:
            FreeCAD.Console.PrintError("Selected object or its children are not a solid.\n")
            return False
        target = solid_obj

        bounds = shape.BoundBox
        y_min = bounds.YMin
        y_max = bounds.YMax
        if y_max <= y_min:
            FreeCAD.Console.PrintError("Selected section has no usable Y length.\n")
            return False

        radius_min = _radius_at_y(shape, y_min)
        radius_max = _radius_at_y(shape, y_max)
        tangent_radius = max(radius_min, radius_max, 0.01)
        section_length = max(float(y_max - y_min), 0.01)
        segment_width = max((2.0 * math.pi * tangent_radius) / float(segments), 0.01)
        layout_width = segment_width * segments
        segment_angle = 360.0 / float(segments)

        container_name = _segment_container_name(target.Name)
        group = doc.addObject('App::DocumentObjectGroup', container_name)
        segment_planes_folder = _ensure_segment_planes_folder(doc, group)
        created_objs = [group]

        if create_sketches:
            layout_plane = doc.addObject('PartDesign::Plane', 'SegmentLayoutPlane')
            layout_plane.MapMode = 'Deactivated'
            layout_plane.Placement.Base = FreeCAD.Vector(0, (y_min + y_max) / 2.0, bounds.ZMax + max(bounds.XLength, bounds.ZLength, segment_width))
            layout_plane.Placement.Rotation = FreeCAD.Rotation()
            try:
                layout_plane.ResizeMode = 'Manual'
            except Exception:
                pass
            try:
                layout_plane.Length = layout_width
                layout_plane.Width = section_length
            except Exception:
                pass
            created_objs.append(layout_plane)

            master_sketch = doc.addObject('Sketcher::SketchObject', 'SegmentMasterSketch')
            _attach_sketch_to_plane(master_sketch, layout_plane)
            created_objs.append(master_sketch)

            half_width = layout_width / 2.0
            half_length = section_length / 2.0
            guide_segments = [
                Part.LineSegment(FreeCAD.Vector(-half_width, -half_length, 0), FreeCAD.Vector(half_width, -half_length, 0)),
                Part.LineSegment(FreeCAD.Vector(half_width, -half_length, 0), FreeCAD.Vector(half_width, half_length, 0)),
                Part.LineSegment(FreeCAD.Vector(half_width, half_length, 0), FreeCAD.Vector(-half_width, half_length, 0)),
                Part.LineSegment(FreeCAD.Vector(-half_width, half_length, 0), FreeCAD.Vector(-half_width, -half_length, 0)),
            ]
            for divider_index in range(1, segments):
                x_pos = -half_width + divider_index * segment_width
                guide_segments.append(
                    Part.LineSegment(
                        FreeCAD.Vector(x_pos, -half_length, 0),
                        FreeCAD.Vector(x_pos, half_length, 0)
                    )
                )
            for geometry in guide_segments:
                try:
                    geo_index = master_sketch.addGeometry(geometry, False)
                    master_sketch.toggleConstruction(geo_index)
                except Exception:
                    master_sketch.addGeometry(geometry, True)

        for index in range(segments):
            angle_deg = index * segment_angle
            angle_rad = math.radians(angle_deg)
            radial = FreeCAD.Vector(math.sin(angle_rad), 0, math.cos(angle_rad))
            tangent = FreeCAD.Vector(math.cos(angle_rad), 0, -math.sin(angle_rad))
            try:
                radial.normalize()
            except Exception:
                pass
            try:
                tangent.normalize()
            except Exception:
                pass

            length_axis = FreeCAD.Vector(0, -1, 0)
            normal_axis = FreeCAD.Vector(radial.x, radial.y, radial.z)
            if normal_axis.Length == 0:
                continue
            try:
                normal_axis.normalize()
            except Exception:
                pass

            center_point = FreeCAD.Vector(
                radial.x * tangent_radius,
                (y_min + y_max) / 2.0,
                radial.z * tangent_radius,
            )
            try:
                if surface_lift_mm > 0.0:
                    center_point = center_point.add(normal_axis.multiply(surface_lift_mm))
            except Exception:
                pass

            rotation = FreeCAD.Rotation(tangent, length_axis, normal_axis, "XYZ")

            plane = doc.addObject('PartDesign::Plane', f'SegmentPlane_{index + 1}')
            plane.MapMode = 'Deactivated'
            plane.Placement = FreeCAD.Placement(center_point, rotation)
            try:
                plane.ResizeMode = 'Manual'
            except Exception:
                pass
            try:
                plane.Length = segment_width
                plane.Width = section_length
            except Exception:
                pass
            created_objs.append(plane)

        doc.recompute()
        for created in created_objs[1:]:
            created_name = str(getattr(created, 'Name', '') or '')
            try:
                if created_name.startswith('SegmentPlane_'):
                    segment_planes_folder.addObject(created)
                else:
                    group.addObject(created)
            except Exception:
                pass
        FreeCAD.Console.PrintMessage(
            f"Created {segments} segment planes for {target.Name} with one flat master sketch and {surface_lift_in:.4f} in outward lift.\n"
        )
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True


class QbertPatternTaskPanel:
    def __init__(self):
        self.form = QtGui.QWidget()
        layout = QtGui.QFormLayout(self.form)

        self.rows = QtGui.QSpinBox()
        self.rows.setMinimum(1)
        self.rows.setMaximum(100)
        self.rows.setValue(QBERT_PREFS.GetInt('rows', DEFAULT_QBERT_ROWS))
        layout.addRow('Rows:', self.rows)

        self.cols_info = QtGui.QLabel('Auto from segment count')
        layout.addRow('Columns:', self.cols_info)

        self.cell_size_info = QtGui.QLabel('Auto from section width')
        layout.addRow('Cube Width:', self.cell_size_info)

        self.stagger = QtGui.QDoubleSpinBox()
        self.stagger.setMinimum(0.0)
        self.stagger.setMaximum(2.0)
        self.stagger.setDecimals(2)
        self.stagger.setSingleStep(0.05)
        self.stagger.setValue(QBERT_PREFS.GetFloat('stagger', DEFAULT_QBERT_STAGGER))
        self.stagger.setSuffix(' cell')
        layout.addRow('Row Stagger:', self.stagger)

        self.clear_existing = QtGui.QCheckBox()
        self.clear_existing.setChecked(QBERT_PREFS.GetBool('clear_existing', DEFAULT_QBERT_CLEAR_EXISTING))
        layout.addRow('Clear Existing Non-Construction:', self.clear_existing)

        self.form.setLayout(layout)

    def accept(self):
        rows = self.rows.value()
        stagger = self.stagger.value()
        clear_existing = self.clear_existing.isChecked()

        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError("No active document!\n")
            return False

        sel = FreeCADGui.Selection.getSelection()
        group = _resolve_segment_group(sel[0]) if sel else None
        if not group:
            group = _latest_segment_group(doc)
        if not group:
            FreeCAD.Console.PrintError("Could not find a SegmentPlanes_* group. Create section planes first.\n")
            return False

        master_sketch = _find_group_member(group, 'SegmentMasterSketch')
        if not master_sketch:
            FreeCAD.Console.PrintError("SegmentMasterSketch not found in group. Recreate segment planes with master sketch enabled.\n")
            return False

        layout_plane = _find_group_member(group, 'SegmentLayoutPlane')
        segment_planes = _find_group_members(group, 'SegmentPlane_')
        if not layout_plane or not segment_planes:
            FreeCAD.Console.PrintError("Segment group is missing SegmentLayoutPlane or SegmentPlane_* objects.\n")
            return False

        cell_mm, cols = _qbert_section_cell_mm(layout_plane, segment_planes)
        if not cell_mm or cols <= 0:
            FreeCAD.Console.PrintError("Could not derive cube width from segment layout.\n")
            return False

        QBERT_PREFS.SetInt('rows', rows)
        QBERT_PREFS.SetInt('cols', cols)
        QBERT_PREFS.SetFloat('cell_in', cell_mm / 25.4)
        QBERT_PREFS.SetFloat('cell_mm', cell_mm)
        QBERT_PREFS.SetFloat('stagger', stagger)
        QBERT_PREFS.SetBool('clear_existing', clear_existing)

        if clear_existing:
            _clear_non_construction_geometry(master_sketch)

        half_w, half_h, x_pitch, y_pitch = _qbert_layout_params(cell_mm)
        segment_width = cell_mm / QBERT_SECTION_FILL_RATIO
        edge_cache = set()
        added = 0

        col_origin = (cols - 1) / 2.0
        row_origin = (rows - 1) / 2.0

        for row in range(rows):
            y = (row_origin - row) * y_pitch
            for col in range(cols):
                phase = -1.0 if (col % 2) else 1.0
                row_shift = _qbert_row_shift(stagger, segment_width, cell_mm, row) * phase
                cx = (col - col_origin) * x_pitch + row_shift
                # Draw a cube-like tri-face motif where top/front/side are different orientations.
                polys = _qbert_face_polygons(cx, y, half_w, half_h)
                added += _draw_polygon(master_sketch, polys['top'], edge_cache)
                added += _draw_polygon(master_sketch, polys['front'], edge_cache)
                added += _draw_polygon(master_sketch, polys['side'], edge_cache)

        doc.recompute()
        FreeCAD.Console.PrintMessage(f"[Qbert] Added {added} pattern edges to {master_sketch.Name}.\n")
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True


class QbertPocketSolidsTaskPanel:
    def __init__(self):
        self.form = QtGui.QWidget()
        layout = QtGui.QFormLayout(self.form)

        self.rows = QtGui.QSpinBox()
        self.rows.setMinimum(1)
        self.rows.setMaximum(100)
        self.rows.setValue(QBERT_PREFS.GetInt('rows', DEFAULT_QBERT_ROWS))
        layout.addRow('Rows:', self.rows)

        self.cols_info = QtGui.QLabel('Auto from segment count')
        layout.addRow('Columns:', self.cols_info)

        self.cell_size_info = QtGui.QLabel('Auto from section width')
        layout.addRow('Cube Width:', self.cell_size_info)

        self.stagger = QtGui.QDoubleSpinBox()
        self.stagger.setMinimum(0.0)
        self.stagger.setMaximum(2.0)
        self.stagger.setDecimals(2)
        self.stagger.setSingleStep(0.05)
        self.stagger.setValue(QBERT_PREFS.GetFloat('stagger', DEFAULT_QBERT_STAGGER))
        self.stagger.setSuffix(' cell')
        layout.addRow('Row Stagger:', self.stagger)

        self.depth_in = QtGui.QDoubleSpinBox()
        self.depth_in.setMinimum(0.005)
        self.depth_in.setMaximum(1.0)
        self.depth_in.setDecimals(3)
        self.depth_in.setValue(QBERT_PREFS.GetFloat('depth_in', DEFAULT_QBERT_DEPTH_IN))
        self.depth_in.setSuffix(' in')
        layout.addRow('Pocket Depth:', self.depth_in)

        self.corner_radius_in = QtGui.QDoubleSpinBox()
        self.corner_radius_in.setMinimum(0.0)
        self.corner_radius_in.setMaximum(0.250)
        self.corner_radius_in.setDecimals(4)
        self.corner_radius_in.setSingleStep(0.001)
        self.corner_radius_in.setValue(
            QBERT_PREFS.GetFloat('corner_radius_in', DEFAULT_QBERT_CORNER_RADIUS_IN)
        )
        self.corner_radius_in.setSuffix(' in')
        layout.addRow('Corner Radius:', self.corner_radius_in)

        self.replace_existing = QtGui.QCheckBox()
        self.replace_existing.setChecked(True)
        layout.addRow('Replace Existing Derived Shapes:', self.replace_existing)

        self.preview_clipped = QtGui.QCheckBox()
        self.preview_clipped.setChecked(True)
        layout.addRow('Create Clipped Preview:', self.preview_clipped)

        self.wood_face = QtGui.QComboBox()
        self.wood_face.addItems(['Front', 'Side'])
        saved_wood_face = str(QBERT_PREFS.GetString('wood_face', 'side')).lower()
        if saved_wood_face == 'left':
            saved_wood_face = 'front'
        elif saved_wood_face == 'right':
            saved_wood_face = 'side'
        if saved_wood_face not in ('front', 'side'):
            saved_wood_face = 'side'
        idx = {'front': 0, 'side': 1}.get(saved_wood_face, 1)
        self.wood_face.setCurrentIndex(idx)
        layout.addRow('Natural Wood Face:', self.wood_face)

        self.form.setLayout(layout)

    def accept(self):
        rows = self.rows.value()
        stagger = self.stagger.value()
        depth_mm = self.depth_in.value() * 25.4
        corner_radius_in = self.corner_radius_in.value()
        replace_existing = self.replace_existing.isChecked()
        preview_clipped = self.preview_clipped.isChecked()
        wood_face = self.wood_face.currentText().lower()
        face_a, face_b = _qbert_face_families(wood_face)

        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError("No active document!\n")
            return False

        sel = FreeCADGui.Selection.getSelection()
        group = _resolve_segment_group(sel[0]) if sel else None
        if not group:
            group = _latest_segment_group(doc)
        if not group:
            FreeCAD.Console.PrintError("Could not find a SegmentPlanes_* group. Create section planes first.\n")
            return False

        layout_plane = _find_group_member(group, 'SegmentLayoutPlane')
        segment_planes = _find_group_members(group, 'SegmentPlane_')
        if not layout_plane or not segment_planes:
            FreeCAD.Console.PrintError("Segment group is missing SegmentLayoutPlane or SegmentPlane_* objects.\n")
            return False

        layout_length = _as_float(getattr(layout_plane, 'Length', 0.0), 0.0)
        layout_width = _as_float(getattr(layout_plane, 'Width', 0.0), 0.0)
        if layout_length <= 0 or layout_width <= 0:
            FreeCAD.Console.PrintError("Layout plane has invalid dimensions.\n")
            return False

        cell_mm, cols = _qbert_section_cell_mm(layout_plane, segment_planes)
        if not cell_mm or cols <= 0:
            FreeCAD.Console.PrintError("Could not derive cube width from segment layout.\n")
            return False

        QBERT_PREFS.SetInt('rows', rows)
        QBERT_PREFS.SetInt('cols', cols)
        QBERT_PREFS.SetFloat('cell_in', cell_mm / 25.4)
        QBERT_PREFS.SetFloat('cell_mm', cell_mm)
        QBERT_PREFS.SetFloat('stagger', stagger)
        QBERT_PREFS.SetFloat('depth_in', self.depth_in.value())
        QBERT_PREFS.SetFloat('corner_radius_in', corner_radius_in)
        QBERT_PREFS.SetString('wood_face', wood_face)

        if replace_existing:
            _remove_group_members(doc, group, 'SegmentDerivedShape_')
            _remove_group_members(doc, group, 'SegmentDerivedShapeA_')
            _remove_group_members(doc, group, 'SegmentDerivedShapeB_')
            _remove_group_members(doc, group, 'SegmentDerivedTile_')

        segment_count = len(segment_planes)
        segment_width = layout_length / float(segment_count)
        fillet_radius_mm = max(0.0, corner_radius_in) * 25.4
        do_fillet = fillet_radius_mm > 1e-6
        fillet_applied = 0
        fillet_skipped = 0
        created = []
        created_family_a = []
        created_family_b = []

        for index, plane in enumerate(segment_planes):
            local_faces_a, local_faces_b = _qbert_segment_faces(
                rows,
                cell_mm,
                stagger,
                wood_face,
                segment_width,
                segment_index=index,
                forearm_length=layout_width,
            )

            depth_vec = _inward_depth_vector(plane, depth_mm)
            if not depth_vec:
                continue

            world_matrix = plane.Placement.toMatrix()
            segment_z_axis = plane.Placement.Rotation.multVec(FreeCAD.Vector(0, 0, 1))
            created_a = 0
            for tile_index, face in enumerate(local_faces_a, start=1):
                try:
                    wf = face.transformGeometry(world_matrix)
                    solid = wf.extrude(depth_vec)
                except Exception:
                    continue
                if do_fillet:
                    solid, filleted = _qbert_apply_corner_radius(solid, fillet_radius_mm, axis_dir=segment_z_axis)
                    if filleted:
                        fillet_applied += 1
                    else:
                        fillet_skipped += 1
                obj_name = f'SegmentDerivedTile_{face_a}_{index + 1}_{tile_index}'
                tile_obj = doc.addObject('Part::Feature', obj_name)
                tile_obj.Label = f'SegmentDerived_{face_a}_{index + 1}_{tile_index}'
                tile_obj.Shape = solid
                created.append(tile_obj)
                created_family_a.append(tile_obj)
                created_a += 1
            created_b = 0
            for tile_index, face in enumerate(local_faces_b, start=1):
                try:
                    wf = face.transformGeometry(world_matrix)
                    solid = wf.extrude(depth_vec)
                except Exception:
                    continue
                if do_fillet:
                    solid, filleted = _qbert_apply_corner_radius(solid, fillet_radius_mm, axis_dir=segment_z_axis)
                    if filleted:
                        fillet_applied += 1
                    else:
                        fillet_skipped += 1
                obj_name = f'SegmentDerivedTile_{face_b}_{index + 1}_{tile_index}'
                tile_obj = doc.addObject('Part::Feature', obj_name)
                tile_obj.Label = f'SegmentDerived_{face_b}_{index + 1}_{tile_index}'
                tile_obj.Shape = solid
                created.append(tile_obj)
                created_family_b.append(tile_obj)
                created_b += 1
            FreeCAD.Console.PrintMessage(
                f"[QbertSolid] Segment {index + 1}: created {created_a} {face_a} tiles and {created_b} {face_b} tiles, wood={wood_face}\n"
            )

        doc.recompute()
        if created:
            group.addObjects(created)
            FreeCAD.Console.PrintMessage(f"[QbertSolid] Created {len(created)} individual face solids.\n")
            if do_fillet:
                FreeCAD.Console.PrintMessage(
                    f"[QbertSolid] Vertical-edge radius {corner_radius_in:.4f} in applied to {fillet_applied}/{len(created)} tiles"
                    + (f" ({fillet_skipped} skipped).\n" if fillet_skipped else ".\n")
                )
            else:
                FreeCAD.Console.PrintMessage("[QbertSolid] Corner radius disabled (0.0000 in).\n")

            if preview_clipped:
                target_obj, target_shape = _segment_group_target_solid(doc, group)
                if target_shape and getattr(target_shape, 'ShapeType', '') == 'Solid':
                    try:
                        preview_group_name = _qbert_preview_group_name(target_obj.Name)
                        preview_group = doc.getObject(preview_group_name)
                        if not preview_group:
                            preview_group = doc.addObject('App::DocumentObjectGroup', preview_group_name)

                        suffix = _extract_trailing_number(group.Name, 0)
                        clip_a_name = f'QbertPreviewClip_{face_a}_{suffix}'
                        clip_b_name = f'QbertPreviewClip_{face_b}_{suffix}'

                        if replace_existing:
                            for existing_name in (clip_a_name, clip_b_name):
                                existing_obj = doc.getObject(existing_name)
                                if existing_obj:
                                    try:
                                        doc.removeObject(existing_obj.Name)
                                    except Exception:
                                        pass

                        def _clip_family(tile_objects):
                            clipped = []
                            for tile_obj in tile_objects:
                                tile_shape = getattr(tile_obj, 'Shape', None)
                                if not tile_shape or tile_shape.isNull():
                                    continue
                                try:
                                    cut_shape = tile_shape.common(target_shape)
                                except Exception:
                                    continue
                                if not cut_shape or cut_shape.isNull():
                                    continue
                                if getattr(cut_shape, 'Volume', 0.0) <= 1e-9:
                                    continue
                                clipped.append(cut_shape)
                            return clipped

                        clipped_a = _clip_family(created_family_a)
                        clipped_b = _clip_family(created_family_b)

                        preview_count = 0
                        if clipped_a:
                            clip_a_obj = doc.addObject('Part::Feature', clip_a_name)
                            clip_a_obj.Label = f'Qbert Flush Preview {face_a} {target_obj.Name}'
                            clip_a_obj.Shape = Part.makeCompound(clipped_a)
                            preview_group.addObject(clip_a_obj)
                            try:
                                # Top/readable family color
                                clip_a_obj.ViewObject.ShapeColor = (0.85, 0.88, 0.92)
                            except Exception:
                                pass
                            preview_count += len(clipped_a)

                        if clipped_b:
                            clip_b_obj = doc.addObject('Part::Feature', clip_b_name)
                            clip_b_obj.Label = f'Qbert Flush Preview {face_b} {target_obj.Name}'
                            clip_b_obj.Shape = Part.makeCompound(clipped_b)
                            preview_group.addObject(clip_b_obj)
                            try:
                                # Side/front family color
                                clip_b_obj.ViewObject.ShapeColor = (0.45, 0.55, 0.70)
                            except Exception:
                                pass
                            preview_count += len(clipped_b)

                        if preview_count > 0:
                            doc.recompute()
                            FreeCAD.Console.PrintMessage(
                                f"[QbertSolid] Created flush preview against {target_obj.Name} ({preview_count} tiles clipped across {face_a}/{face_b}).\n"
                            )
                        else:
                            FreeCAD.Console.PrintMessage(
                                f"[QbertSolid] Flush preview empty against {target_obj.Name}; no clipped tiles.\n"
                            )
                    except Exception as exc:
                        FreeCAD.Console.PrintError(
                            f"[QbertSolid] Failed to create clipped preview: {exc}\n"
                        )
                else:
                    FreeCAD.Console.PrintMessage(
                        "[QbertSolid] Could not resolve a target solid for clipped preview.\n"
                    )
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True


class PatternMasterSketchTaskPanel:
    def __init__(self):
        self.form = QtGui.QWidget()
        layout = QtGui.QFormLayout(self.form)

        self.material = QtGui.QLineEdit()
        saved_material = str(PATTERN_LAB_PREFS.GetString('material_token', DEFAULT_PATTERN_LAB_MATERIAL))
        self.material.setText(saved_material)
        layout.addRow('Pattern Name:', self.material)

        self.shape = QtGui.QComboBox()
        self.shape.addItems(['Square', 'Hexagon', 'Octagon'])
        saved_shape = str(PATTERN_LAB_PREFS.GetString('pattern_master_shape', 'Square'))
        saved_index = self.shape.findText(saved_shape)
        self.shape.setCurrentIndex(saved_index if saved_index >= 0 else 0)
        layout.addRow('Construction Shape:', self.shape)

        saved_draw_cube = PATTERN_LAB_PREFS.GetBool(
            'pattern_master_draw_isometric_cube',
            PATTERN_LAB_PREFS.GetBool('pattern_master_draw_rhombus', False),
        )
        self.draw_rhombus = QtGui.QCheckBox()
        self.draw_rhombus.setChecked(saved_draw_cube)
        layout.addRow('Draw Isometric Cube Guide:', self.draw_rhombus)

        try:
            self.draw_rhombus.toggled.connect(self._sync_guide_mode)
        except Exception:
            pass
        self._sync_guide_mode(self.draw_rhombus.isChecked())

        self.form.setLayout(layout)

    def _sync_guide_mode(self, cube_enabled):
        # Cube mode is mutually exclusive with polygon guide selection.
        self.shape.setEnabled(not bool(cube_enabled))

    def accept(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError('No active document.\n')
            return False

        material_token = _pattern_lab_material_token(self.material.text())

        selection = FreeCADGui.Selection.getSelection() if FreeCADGui else []
        group = _resolve_segment_group(selection[0]) if selection else None
        if not group:
            group = _latest_segment_group(doc)
        if not group:
            FreeCAD.Console.PrintError('Could not find a SegmentPlanes_* group. Create section planes first.\n')
            return False

        anchor_plane = _first_segment_plane(group)
        if not anchor_plane:
            FreeCAD.Console.PrintError('Segment group is missing SegmentPlane_* objects.\n')
            return False

        selected_shape = self.shape.currentText()
        draw_cube = self.draw_rhombus.isChecked()
        material_token = _pattern_lab_material_token(self.material.text())
        PATTERN_LAB_PREFS.SetString('pattern_master_shape', selected_shape)
        PATTERN_LAB_PREFS.SetBool('pattern_master_draw_isometric_cube', draw_cube)
        PATTERN_LAB_PREFS.SetBool('pattern_master_draw_rhombus', draw_cube)
        PATTERN_LAB_PREFS.SetString('material_token', material_token)

        anchor_width = _as_float(getattr(anchor_plane, 'Width', 0.0), 0.0)
        segment_width = _as_float(getattr(anchor_plane, 'Length', 0.0), 0.0)
        guide_width = (segment_width * 2.0) if segment_width > 1e-6 else (DEFAULT_PATTERN_LAB_TILE_GUIDE_WIDTH_IN * 25.4 * 2.0)

        def _build_guides():
            points = _pattern_master_polygon_points(selected_shape, guide_width)
            min_y = min(point.y for point in points)
            max_y = max(point.y for point in points)
            poly_height = max(1e-6, max_y - min_y)
            half_w = guide_width / 2.0
            half_h = poly_height / 2.0
            cube_half_w, cube_half_h, _, _ = _qbert_layout_params(guide_width)
            cube_polys = _qbert_face_polygons(0.0, 0.0, cube_half_w, cube_half_h)
            cube_points = []
            for poly in cube_polys.values():
                cube_points.extend(poly[:-1])
            cube_min_y = min(point.y for point in cube_points)
            cube_max_y = max(point.y for point in cube_points)
            guide_half_h = max(1e-6, max(abs(cube_min_y), abs(cube_max_y))) if draw_cube else half_h

            # Place the seed guide at +Y (front side) of the segment layout instead of center.
            if anchor_width > 1e-6:
                guide_center_y = max(guide_half_h, (anchor_width / 2.0) - (cube_max_y if draw_cube else guide_half_h))
            else:
                guide_center_y = guide_half_h if not draw_cube else -cube_min_y

            guides = []
            shifted_cube_polys = None
            non_cube_guide = None
            if draw_cube:
                seen = set()
                shifted_cube_polys = {}
                for face_key in ('top', 'front', 'side'):
                    shifted = [FreeCAD.Vector(point.x, point.y + guide_center_y, 0) for point in cube_polys[face_key][:-1]]
                    shifted_cube_polys[face_key] = shifted
                    for idx in range(len(shifted)):
                        p1 = shifted[idx]
                        p2 = shifted[(idx + 1) % len(shifted)]
                        key = (
                            round(min(p1.x, p2.x), 4),
                            round(min(p1.y, p2.y), 4),
                            round(max(p1.x, p2.x), 4),
                            round(max(p1.y, p2.y), 4),
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        guides.append(Part.LineSegment(p1, p2))
            else:
                shifted = [FreeCAD.Vector(point.x, point.y + guide_center_y, 0) for point in points]
                non_cube_guide = {
                    'shifted_points': shifted,
                    'half_w': half_w,
                    'guide_center_y': guide_center_y,
                    'guide_half_h': guide_half_h,
                }
                for idx in range(len(shifted)):
                    guides.append(Part.LineSegment(shifted[idx], shifted[(idx + 1) % len(shifted)]))
                guides.extend([
                    Part.LineSegment(FreeCAD.Vector(-half_w, guide_center_y, 0), FreeCAD.Vector(half_w, guide_center_y, 0)),
                    Part.LineSegment(FreeCAD.Vector(0, guide_center_y - guide_half_h, 0), FreeCAD.Vector(0, guide_center_y + guide_half_h, 0)),
                ])
            return guides, shifted_cube_polys, non_cube_guide

        def _apply_guides(target_sketch):
            _clear_construction_geometry(target_sketch)
            guides, shifted_cube_polys, non_cube_guide = _build_guides()
            if draw_cube and shifted_cube_polys:
                for face_key in ('top', 'front', 'side'):
                    _add_closed_construction_loop(target_sketch, shifted_cube_polys.get(face_key, []))
                return
            shifted = list((non_cube_guide or {}).get('shifted_points', []) or [])
            half_w = float((non_cube_guide or {}).get('half_w', 0.0) or 0.0)
            guide_center_y = float((non_cube_guide or {}).get('guide_center_y', 0.0) or 0.0)
            guide_half_h = float((non_cube_guide or {}).get('guide_half_h', 0.0) or 0.0)
            if not shifted:
                return
            _add_closed_construction_loop(target_sketch, shifted)
            _add_blocked_construction_segment(
                target_sketch,
                FreeCAD.Vector(-half_w, guide_center_y, 0),
                FreeCAD.Vector(half_w, guide_center_y, 0),
            )
            _add_blocked_construction_segment(
                target_sketch,
                FreeCAD.Vector(0, guide_center_y - guide_half_h, 0),
                FreeCAD.Vector(0, guide_center_y + guide_half_h, 0),
            )

        def _seed_default_cube_profile_if_needed(target_sketch):
            if not draw_cube:
                return False
            if _has_non_construction_geometry(target_sketch):
                return False

            _, shifted_cube_polys, _ = _build_guides()
            face_key = _qbert_face_for_token(material_token)
            face_pts = (shifted_cube_polys or {}).get(face_key, [])
            if len(face_pts) < 3:
                return False

            added = 0
            for i in range(len(face_pts)):
                p1 = face_pts[i]
                p2 = face_pts[(i + 1) % len(face_pts)]
                try:
                    target_sketch.addGeometry(Part.LineSegment(p1, p2), False)
                    added += 1
                except Exception:
                    pass
            return added > 0

        lab_group = _ensure_pattern_lab_group(doc, group)
        suffix = _extract_trailing_number(str(getattr(group, 'Name', '')), 0)
        sketch_name = _pattern_lab_tile_sketch_name_for_material(suffix, material_token)
        existing = doc.getObject(sketch_name)
        if existing:
            _apply_guides(existing)
            seeded = _seed_default_cube_profile_if_needed(existing)
            try:
                doc.recompute()
            except Exception:
                pass
            try:
                FreeCADGui.Selection.clearSelection()
                FreeCADGui.Selection.addSelection(doc.Name, existing.Name)
            except Exception:
                pass
            _orient_pattern_master_sketch(existing)
            FreeCADGui.Control.closeDialog()
            _open_sketch_edit_deferred(existing.Name)
            if seeded:
                FreeCAD.Console.PrintMessage(
                    f'[PatternLab] Reusing existing tile sketch {existing.Name}. Added default { _qbert_face_for_token(material_token) } face profile for cube mode.\n'
                )
            else:
                FreeCAD.Console.PrintMessage(f'[PatternLab] Reusing existing tile sketch {existing.Name}. Draw your motif there.\n')
            return True

        sketch = doc.addObject('Sketcher::SketchObject', sketch_name)
        sketch.Label = f'PatternMasterSketch {material_token} {suffix}'
        _attach_sketch_to_plane(sketch, anchor_plane)
        _orient_pattern_master_sketch(sketch)
        _apply_guides(sketch)
        _seed_default_cube_profile_if_needed(sketch)

        try:
            lab_group.addObject(sketch)
        except Exception:
            pass
        doc.recompute()
        try:
            FreeCADGui.Selection.clearSelection()
            FreeCADGui.Selection.addSelection(doc.Name, sketch.Name)
        except Exception:
            pass
        FreeCADGui.Control.closeDialog()
        _open_sketch_edit_deferred(sketch.Name)
        guide_mode = 'isometric cube' if draw_cube else selected_shape
        FreeCAD.Console.PrintMessage(
            f'[PatternLab] Created PatternMasterSketch ({material_token}) with {guide_mode}'
            + ' construction guides at +Y (2x segment width). Draw your motif, then run Pattern Lab Create Solids.\n'
        )
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True


class PatternLabTileSketchCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'PatternMasterSketch',
            'ToolTip': 'Create PatternMasterSketch with square/hexagon/octagon guides, or an isometric cube guide, at +Y on SegmentPlane_1.',
        }

    def Activated(self):
        _show_task_panel_safe(PatternMasterSketchTaskPanel())

    def IsActive(self):
        return True


def _pattern_lab_output_config(output_mode):
    return {
        'group_getter': _ensure_inlay_pattern_links_group,
        'root_prefix': 'PatternDownSet',
        'set_label': 'Pattern Down Set',
        'replace_label': 'Replace Existing Pattern Links:',
        'item_label': 'pattern link',
        'summary_label': 'binder-linked pattern instance(s)',
    }


def _pattern_lab_target_planes(group, anchor_plane, copy_to_other_segments):
    all_planes = _find_group_members(group, 'SegmentPlane_')
    target_planes = list(all_planes) if (copy_to_other_segments and all_planes) else [anchor_plane]
    if not target_planes:
        target_planes = [anchor_plane]
    return target_planes


def _pattern_lab_collect_source_array_specs(
    source_obj,
    anchor_plane,
    target_planes,
    step_mm,
    offset_phase_mm,
    offset_even_segments_only,
    auto_fill_count_fn,
):
    specs = []
    source_span_mm = _shape_span_along_plane_y_mm(source_obj, anchor_plane)

    for plane_idx, target_plane in enumerate(target_planes, start=1):
        try:
            plane_rel = target_plane.Placement.multiply(anchor_plane.Placement.inverse())
        except Exception:
            plane_rel = FreeCAD.Placement()

        segment_offset_mm = 0.0
        if offset_phase_mm > 1e-9:
            if (not offset_even_segments_only) or ((plane_idx % 2) == 0):
                segment_offset_mm = offset_phase_mm

        plane_row_count = auto_fill_count_fn(
            step_mm,
            leading_offset_mm=segment_offset_mm,
            source_span_mm=source_span_mm,
        )

        if plane_row_count <= 0:
            continue

        start_local = FreeCAD.Placement(FreeCAD.Vector(0.0, -segment_offset_mm, 0.0), FreeCAD.Rotation())
        start_placement = plane_rel.multiply(start_local)
        dir_vec = start_placement.Rotation.multVec(FreeCAD.Vector(0.0, -1.0, 0.0))
        try:
            dir_vec.normalize()
        except Exception:
            pass

        specs.append({
            'plane_index': plane_idx,
            'count': int(plane_row_count),
            'step_mm': float(step_mm),
            'plane_placement': plane_rel,
            'start_placement': start_placement,
            'dir_vec': dir_vec,
            'segment_offset_mm': float(segment_offset_mm),
        })

    return specs


def _pattern_lab_expand_array_specs(array_specs):
    placements = []
    for spec in list(array_specs or []):
        start_placement = spec.get('start_placement', FreeCAD.Placement())
        dir_vec = spec.get('dir_vec', FreeCAD.Vector(0.0, -1.0, 0.0))
        step_mm = float(spec.get('step_mm', 0.0) or 0.0)
        count = int(spec.get('count', 0) or 0)
        for idx in range(count):
            base = start_placement.Base + (dir_vec * (step_mm * float(idx)))
            placements.append(FreeCAD.Placement(base, start_placement.Rotation))

    return placements


def _ensure_pattern_link_meta_properties(link_obj):
    prop_defs = [
        ('App::PropertyInteger', 'PatternPlaneIndex', 'PatternLab Link Meta', 'Segment plane index for parametric spacing updates.'),
        ('App::PropertyInteger', 'PatternRowIndex', 'PatternLab Link Meta', 'Row index in the source array for parametric spacing updates.'),
        ('App::PropertyFloat', 'PatternPlaneBaseX', 'PatternLab Link Meta', 'Stored plane-relative base X.'),
        ('App::PropertyFloat', 'PatternPlaneBaseY', 'PatternLab Link Meta', 'Stored plane-relative base Y.'),
        ('App::PropertyFloat', 'PatternPlaneBaseZ', 'PatternLab Link Meta', 'Stored plane-relative base Z.'),
        ('App::PropertyFloat', 'PatternDirX', 'PatternLab Link Meta', 'Stored direction X.'),
        ('App::PropertyFloat', 'PatternDirY', 'PatternLab Link Meta', 'Stored direction Y.'),
        ('App::PropertyFloat', 'PatternDirZ', 'PatternLab Link Meta', 'Stored direction Z.'),
        ('App::PropertyFloat', 'PatternRotQ0', 'PatternLab Link Meta', 'Stored placement rotation quaternion scalar.'),
        ('App::PropertyFloat', 'PatternRotQ1', 'PatternLab Link Meta', 'Stored placement rotation quaternion x.'),
        ('App::PropertyFloat', 'PatternRotQ2', 'PatternLab Link Meta', 'Stored placement rotation quaternion y.'),
        ('App::PropertyFloat', 'PatternRotQ3', 'PatternLab Link Meta', 'Stored placement rotation quaternion z.'),
    ]
    for type_id, prop_name, group_name, doc_text in prop_defs:
        if prop_name in list(getattr(link_obj, 'PropertiesList', []) or []):
            continue
        try:
            link_obj.addProperty(type_id, prop_name, group_name, doc_text)
        except Exception:
            pass


def _set_pattern_link_meta(link_obj, plane_index, row_index, plane_placement, dir_vec):
    _ensure_pattern_link_meta_properties(link_obj)

    base = getattr(plane_placement, 'Base', FreeCAD.Vector(0.0, 0.0, 0.0))
    rot = getattr(plane_placement, 'Rotation', FreeCAD.Rotation())
    q = rot.Q

    try:
        link_obj.PatternPlaneIndex = int(plane_index)
        link_obj.PatternRowIndex = int(row_index)
        link_obj.PatternPlaneBaseX = float(base.x)
        link_obj.PatternPlaneBaseY = float(base.y)
        link_obj.PatternPlaneBaseZ = float(base.z)
        link_obj.PatternDirX = float(getattr(dir_vec, 'x', 0.0))
        link_obj.PatternDirY = float(getattr(dir_vec, 'y', -1.0))
        link_obj.PatternDirZ = float(getattr(dir_vec, 'z', 0.0))
        link_obj.PatternRotQ0 = float(q[0])
        link_obj.PatternRotQ1 = float(q[1])
        link_obj.PatternRotQ2 = float(q[2])
        link_obj.PatternRotQ3 = float(q[3])
    except Exception:
        pass


def _pattern_set_length_mm(container_obj, prop_name, fallback_mm):
    raw = getattr(container_obj, prop_name, None)
    if raw is None:
        return float(fallback_mm)
    return max(0.0, _as_float(raw, float(fallback_mm)))


def _update_pattern_links_from_container(container_obj):
    if not container_obj:
        return

    props = set(getattr(container_obj, 'PropertiesList', []) or [])
    if 'PatternStep' not in props:
        return

    step_mm = _pattern_set_length_mm(container_obj, 'PatternStep', 0.0)
    offset_mm = _pattern_set_length_mm(container_obj, 'PatternSegmentOffset', 0.0)
    try:
        offset_even_only = bool(getattr(container_obj, 'PatternOffsetEvenSegmentsOnly', False))
    except Exception:
        offset_even_only = False

    for child in list(getattr(container_obj, 'Group', []) or []):
        if 'PatternRowIndex' not in list(getattr(child, 'PropertiesList', []) or []):
            continue
        try:
            plane_index = int(getattr(child, 'PatternPlaneIndex', 1) or 1)
            row_index = int(getattr(child, 'PatternRowIndex', 0) or 0)

            plane_base = FreeCAD.Vector(
                float(getattr(child, 'PatternPlaneBaseX', 0.0) or 0.0),
                float(getattr(child, 'PatternPlaneBaseY', 0.0) or 0.0),
                float(getattr(child, 'PatternPlaneBaseZ', 0.0) or 0.0),
            )
            dir_vec = FreeCAD.Vector(
                float(getattr(child, 'PatternDirX', 0.0) or 0.0),
                float(getattr(child, 'PatternDirY', -1.0) or -1.0),
                float(getattr(child, 'PatternDirZ', 0.0) or 0.0),
            )
            try:
                dir_vec.normalize()
            except Exception:
                pass

            rot = FreeCAD.Rotation(
                float(getattr(child, 'PatternRotQ0', 1.0) or 1.0),
                float(getattr(child, 'PatternRotQ1', 0.0) or 0.0),
                float(getattr(child, 'PatternRotQ2', 0.0) or 0.0),
                float(getattr(child, 'PatternRotQ3', 0.0) or 0.0),
            )

            segment_offset_mm = 0.0
            if offset_mm > 1e-9:
                if (not offset_even_only) or ((plane_index % 2) == 0):
                    segment_offset_mm = offset_mm

            base = plane_base + (dir_vec * (segment_offset_mm + (step_mm * float(row_index))))
            child.Placement = FreeCAD.Placement(base, rot)
        except Exception:
            continue


class PatternLabPatternSetProxy:
    def __init__(self, obj):
        obj.Proxy = self

    def onChanged(self, obj, prop):
        if prop not in ('PatternStep', 'PatternSegmentOffset', 'PatternOffsetEvenSegmentsOnly'):
            return
        _update_pattern_links_from_container(obj)

    def execute(self, obj):
        _update_pattern_links_from_container(obj)


def _is_pattern_set_container(obj):
    if not obj:
        return False
    obj_name = str(getattr(obj, 'Name', '') or '')
    if not obj_name.startswith('PatternDownSet_'):
        return False
    props = set(getattr(obj, 'PropertiesList', []) or [])
    return ('PatternStep' in props) and ('PatternSegmentOffset' in props)


class PatternLabSetControlsObserver:
    def slotChangedObject(self, obj, prop):
        if prop not in ('PatternStep', 'PatternSegmentOffset', 'PatternOffsetEvenSegmentsOnly'):
            return
        if not _is_pattern_set_container(obj):
            return
        _update_pattern_links_from_container(obj)


_PATTERN_LAB_SET_CONTROLS_OBSERVER = None


def _ensure_pattern_lab_set_controls_observer():
    global _PATTERN_LAB_SET_CONTROLS_OBSERVER
    if _PATTERN_LAB_SET_CONTROLS_OBSERVER is not None:
        return
    try:
        _PATTERN_LAB_SET_CONTROLS_OBSERVER = PatternLabSetControlsObserver()
        FreeCAD.addDocumentObserver(_PATTERN_LAB_SET_CONTROLS_OBSERVER)
    except Exception:
        _PATTERN_LAB_SET_CONTROLS_OBSERVER = None


def _attach_pattern_set_proxy(container_obj, step_mm, offset_phase_mm, offset_even_segments_only):
    if not container_obj:
        return

    prop_defs = [
        ('App::PropertyLength', 'PatternStep', 'PatternLab Controls', 'Global step distance between patterned links.'),
        ('App::PropertyLength', 'PatternSegmentOffset', 'PatternLab Controls', 'Global segment-start offset for patterned links.'),
        ('App::PropertyBool', 'PatternOffsetEvenSegmentsOnly', 'PatternLab Controls', 'Apply segment offset to even segments only.'),
    ]
    for type_id, prop_name, group_name, doc_text in prop_defs:
        if prop_name in list(getattr(container_obj, 'PropertiesList', []) or []):
            continue
        try:
            container_obj.addProperty(type_id, prop_name, group_name, doc_text)
        except Exception:
            pass

    try:
        container_obj.PatternStep = f'{float(step_mm)} mm'
    except Exception:
        pass
    try:
        container_obj.PatternSegmentOffset = f'{float(offset_phase_mm)} mm'
    except Exception:
        pass
    try:
        container_obj.PatternOffsetEvenSegmentsOnly = bool(offset_even_segments_only)
    except Exception:
        pass

    try:
        if not isinstance(getattr(container_obj, 'Proxy', None), PatternLabPatternSetProxy):
            PatternLabPatternSetProxy(container_obj)
    except Exception:
        pass


def _pattern_lab_backfill_set_controls(doc, group):
    if not doc or not group:
        return 0

    config = _pattern_lab_output_config('pattern_links')
    output_group = config['group_getter'](doc, group)
    suffix = _extract_trailing_number(str(getattr(group, 'Name', '')), 0)
    root_name = f"{config['root_prefix']}_{suffix}"

    step_mm = max(0.001, float(PATTERN_LAB_PREFS.GetFloat('pattern_down_step_in', 0.25) or 0.25) * 25.4)
    offset_mm = max(0.0, float(PATTERN_LAB_PREFS.GetFloat('pattern_down_offset_in', 0.0) or 0.0) * 25.4)
    offset_even_only = bool(PATTERN_LAB_PREFS.GetBool('pattern_down_offset_even_segments_only', True))

    patched = 0
    for existing in list(getattr(output_group, 'Group', []) or []):
        existing_name = str(getattr(existing, 'Name', '') or '')
        if not existing_name.startswith(root_name):
            continue
        props = set(getattr(existing, 'PropertiesList', []) or [])
        if 'PatternStep' in props and 'PatternSegmentOffset' in props and 'PatternOffsetEvenSegmentsOnly' in props:
            continue
        _attach_pattern_set_proxy(existing, step_mm, offset_mm, offset_even_only)
        patched += 1
    return patched


def _create_lattice2_kite_preview_set(doc, container, source_obj, array_specs, source_token, suffix):
    import lattice2Executer
    import lattice2JoinArrays
    import lattice2LinearArray
    import lattice2Placement
    import lattice2PopulateCopies

    if not array_specs:
        return []

    source_link_obj = doc.addObject('App::Link', f'KiteSource_{source_token}_{suffix}')
    source_link_obj.Label = f'EDIT Kite Source {getattr(source_obj, "Label", source_obj.Name)}'
    source_link_obj.LinkedObject = source_obj
    try:
        source_shape = getattr(source_obj, 'Shape', None)
        source_bb = getattr(source_shape, 'BoundBox', None)
        marker_size = max(2.0, float(max(getattr(source_bb, 'XLength', 0.0), getattr(source_bb, 'YLength', 0.0), getattr(source_bb, 'ZLength', 0.0), 0.0)) * 0.35)
    except Exception:
        marker_size = 4.0
    try:
        pass
    except Exception:
        pass
    # Keep the editable source transform at identity initially.
    # Source geometry already carries its own placement, so copying it here can double-transform the preview.
    source_link_obj.Placement = FreeCAD.Placement()
    try:
        container.addObject(source_link_obj)
    except Exception:
        pass
    _copy_view_appearance(source_obj, source_link_obj)
    source_link_view = getattr(source_link_obj, 'ViewObject', None)
    if source_link_view:
        try:
            source_link_view.Visibility = True
        except Exception:
            pass
        try:
            source_link_view.LineWidth = 3.0
        except Exception:
            pass
        try:
            source_link_view.PointSize = 8.0
        except Exception:
            pass

    odd_specs = [s for s in array_specs if (int(s.get('plane_index', 0) or 0) % 2) == 1]
    even_specs = [s for s in array_specs if (int(s.get('plane_index', 0) or 0) % 2) == 0]

    step_mm = float((array_specs[0].get('step_mm', 0.0) if array_specs else 0.0) or 0.0)
    odd_count = max([int(s.get('count', 0) or 0) for s in odd_specs] + [0])
    even_count = max([int(s.get('count', 0) or 0) for s in even_specs] + [0])
    odd_offset = float((odd_specs[0].get('segment_offset_mm', 0.0) if odd_specs else 0.0) or 0.0)
    even_offset = float((even_specs[0].get('segment_offset_mm', 0.0) if even_specs else 0.0) or 0.0)

    odd_array = None
    if odd_count > 0:
        odd_array = lattice2LinearArray.makeLinearArray(f'KiteArrayOdd_{source_token}_{suffix}')
        odd_array.Label = f'EDIT Kite Array Odd {getattr(source_obj, "Label", source_obj.Name)}'
        odd_array.Dir = FreeCAD.Vector(0.0, -1.0, 0.0)
        odd_array.Point = FreeCAD.Vector(0.0, -odd_offset, 0.0)
        odd_array.GeneratorMode = 'StepN'
        odd_array.Step = step_mm
        odd_array.Count = float(odd_count)
        odd_array.OrientMode = 'None'
        try:
            odd_array.MarkerSize = max(1.0, marker_size * 0.1)
        except Exception:
            pass
        try:
            container.addObject(odd_array)
        except Exception:
            pass
        lattice2Executer.executeFeature(odd_array)

    even_array = None
    if even_count > 0:
        even_array = lattice2LinearArray.makeLinearArray(f'KiteArrayEven_{source_token}_{suffix}')
        even_array.Label = f'EDIT Kite Array Even {getattr(source_obj, "Label", source_obj.Name)}'
        even_array.Dir = FreeCAD.Vector(0.0, -1.0, 0.0)
        even_array.Point = FreeCAD.Vector(0.0, -even_offset, 0.0)
        even_array.GeneratorMode = 'StepN'
        even_array.Step = step_mm
        even_array.Count = float(even_count)
        even_array.OrientMode = 'None'
        try:
            even_array.MarkerSize = max(1.0, marker_size * 0.1)
        except Exception:
            pass
        try:
            container.addObject(even_array)
        except Exception:
            pass
        lattice2Executer.executeFeature(even_array)

    distributed_arrays = []
    def _distribute_to_segments(array_obj, specs, kind_token):
        for spec in list(specs or []):
            plane_index = int(spec.get('plane_index', 0) or 0)
            origin_placement = spec.get('plane_placement', spec.get('start_placement', FreeCAD.Placement()))

            origin_obj = lattice2Placement.makeLatticePlacement(f'Kite{kind_token}Origin_{source_token}_{suffix}_{plane_index}')
            origin_obj.Label = f'Kite {kind_token} Origin Seg{plane_index} {getattr(source_obj, "Label", source_obj.Name)}'
            origin_obj.PlacementChoice = 'Custom'
            origin_obj.Placement = origin_placement
            try:
                origin_obj.MarkerSize = max(0.6, marker_size * 0.07)
            except Exception:
                pass
            try:
                container.addObject(origin_obj)
            except Exception:
                pass
            lattice2Executer.executeFeature(origin_obj)
            origin_view = getattr(origin_obj, 'ViewObject', None)
            if origin_view:
                try:
                    origin_view.Visibility = False
                except Exception:
                    pass

            distributed = lattice2PopulateCopies.makeLatticePopulateCopies(
                f'Kite{kind_token}Distributed_{source_token}_{suffix}_{plane_index}'
            )
            distributed.Label = f'Kite {kind_token} Placements Seg{plane_index} {getattr(source_obj, "Label", source_obj.Name)}'
            distributed.Object = array_obj
            distributed.PlacementsTo = origin_obj
            distributed.Referencing = 'Origin'
            try:
                container.addObject(distributed)
            except Exception:
                pass
            lattice2Executer.executeFeature(distributed)
            distributed_view = getattr(distributed, 'ViewObject', None)
            if distributed_view:
                try:
                    distributed_view.Visibility = False
                except Exception:
                    pass
            distributed_arrays.append(distributed)

    if odd_array and odd_specs:
        _distribute_to_segments(odd_array, odd_specs, 'Odd')
    if even_array and even_specs:
        _distribute_to_segments(even_array, even_specs, 'Even')

    join_obj = lattice2JoinArrays.makeJoinArrays(f'KitePlacements_{source_token}_{suffix}')
    join_obj.Label = f'Kite Placements {getattr(source_obj, "Label", source_obj.Name)}'
    join_obj.Links = list(distributed_arrays)
    join_obj.Interleave = False
    try:
        container.addObject(join_obj)
    except Exception:
        pass
    lattice2Executer.executeFeature(join_obj)

    preview_obj = lattice2PopulateCopies.makeLatticePopulateCopies(f'KitePreview_{source_token}_{suffix}')
    preview_obj.Label = f'Kite Preview {getattr(source_obj, "Label", source_obj.Name)} (edit source + odd/even arrays in tree)'
    preview_obj.Object = source_obj
    preview_obj.PlacementsTo = join_obj
    preview_obj.Referencing = 'Origin'
    preview_obj.OutputCompounding = 'only if many'
    try:
        preview_obj.Copying = 'Transformed deep copy'
    except Exception:
        pass
    try:
        container.addObject(preview_obj)
    except Exception:
        pass
    lattice2Executer.executeFeature(preview_obj)
    _copy_view_appearance(source_obj, preview_obj)

    join_view = getattr(join_obj, 'ViewObject', None)
    if join_view:
        try:
            join_view.Visibility = False
        except Exception:
            pass

    for array_obj in [odd_array, even_array]:
        if not array_obj:
            continue
        array_view = getattr(array_obj, 'ViewObject', None)
        if array_view:
            try:
                array_view.Visibility = False
            except Exception:
                pass

    preview_view = getattr(preview_obj, 'ViewObject', None)
    if preview_view:
        try:
            preview_view.Visibility = True
        except Exception:
            pass

    # Fallback: if Lattice2 preview resolves empty, create direct App::Link instances
    # from the same computed placements so users always get visible/editable results.
    preview_has_shape = False
    try:
        preview_shape = getattr(preview_obj, 'Shape', None)
        if preview_shape and (not preview_shape.isNull()):
            if getattr(preview_shape, 'Volume', 0.0) > 1e-9 or len(getattr(preview_shape, 'Solids', []) or []) > 0:
                preview_has_shape = True
    except Exception:
        preview_has_shape = False

    fallback_links = []
    if not preview_has_shape:
        placements = _pattern_lab_expand_array_specs(array_specs)
        for idx, placement in enumerate(placements, start=1):
            link_name = f'KiteFallback_{source_token}_{suffix}_{idx}'
            link_obj = doc.addObject('App::Link', link_name)
            link_obj.Label = f'Kite Instance {getattr(source_obj, "Label", source_obj.Name)} #{idx}'
            link_obj.LinkedObject = source_obj
            link_obj.Placement = placement
            _copy_view_appearance(source_obj, link_obj)
            try:
                container.addObject(link_obj)
            except Exception:
                pass
            fallback_links.append(link_obj)

        try:
            preview_obj.ViewObject.Visibility = False
        except Exception:
            pass

    created = []
    if odd_array:
        created.append(odd_array)
    if even_array:
        created.append(even_array)
    if fallback_links:
        created.extend(fallback_links)
    else:
        created.append(preview_obj)
    return created


def _create_pattern_lab_segment_instances(
    doc,
    group,
    anchor_plane,
    source_objs,
    step_mm,
    offset_phase_mm,
    offset_even_segments_only,
    copy_to_other_segments,
    replace_existing,
    auto_fill_count_fn,
    output_mode='pattern_links',
):
    config = _pattern_lab_output_config(output_mode)
    target_planes = _pattern_lab_target_planes(group, anchor_plane, copy_to_other_segments)

    output_group = config['group_getter'](doc, group)
    suffix = _extract_trailing_number(str(getattr(group, 'Name', '')), 0)
    root_name = f"{config['root_prefix']}_{suffix}"

    if replace_existing:
        for existing in list(getattr(output_group, 'Group', []) or []):
            existing_name = str(getattr(existing, 'Name', '') or '')
            if existing_name.startswith(root_name):
                try:
                    doc.removeObject(existing.Name)
                except Exception:
                    pass

    container = doc.addObject('App::Part', root_name)
    container.Label = f"{config['set_label']} {suffix}"
    _attach_pattern_set_proxy(container, step_mm, offset_phase_mm, offset_even_segments_only)

    created_links = []
    for source_index, source_obj in enumerate(source_objs, start=1):
        source_token = _pattern_lab_material_token(getattr(source_obj, 'Name', f'source_{source_index}'))
        array_specs = _pattern_lab_collect_source_array_specs(
            source_obj,
            anchor_plane,
            target_planes,
            step_mm,
            offset_phase_mm,
            offset_even_segments_only,
            auto_fill_count_fn,
        )
        placement_index = 0
        for spec in list(array_specs or []):
            plane_index = int(spec.get('plane_index', 1) or 1)
            plane_placement = spec.get('plane_placement', FreeCAD.Placement())
            dir_vec = spec.get('dir_vec', FreeCAD.Vector(0.0, -1.0, 0.0))
            count = int(spec.get('count', 0) or 0)
            for row_index in range(count):
                placement_index += 1
                segment_offset_mm = float(spec.get('segment_offset_mm', 0.0) or 0.0)
                base = plane_placement.Base + (dir_vec * (segment_offset_mm + (float(step_mm) * float(row_index))))
                placement = FreeCAD.Placement(base, plane_placement.Rotation)
                link_name = f'{root_name}_{source_token}_P{placement_index}'
                link_obj = doc.addObject('App::Link', link_name)
                link_obj.Label = f'{getattr(source_obj, "Label", source_obj.Name)} Pos {placement_index}'
                link_obj.LinkedObject = source_obj
                link_obj.Placement = placement
                _set_pattern_link_meta(link_obj, plane_index, row_index, plane_placement, dir_vec)
                _copy_view_appearance(source_obj, link_obj)
                try:
                    container.addObject(link_obj)
                except Exception:
                    pass
                created_links.append(link_obj)

    try:
        output_group.addObject(container)
    except Exception:
        pass

    try:
        doc.recompute()
    except Exception:
        pass

    return created_links, target_planes


class PatternLabPatternDownSegmentTaskPanel:
    def __init__(self, output_mode='pattern_links'):
        self.output_mode = output_mode
        self.output_config = _pattern_lab_output_config(output_mode)
        self.form = QtGui.QWidget()
        layout = QtGui.QFormLayout(self.form)

        doc = FreeCAD.ActiveDocument
        selection = FreeCADGui.Selection.getSelection() if FreeCADGui else []

        self.source_objs = _collect_shape_sources_from_selection(selection, require_filleted=True)
        self.source_info = QtGui.QLabel(_summarize_source_labels(self.source_objs))
        layout.addRow('Source Object(s) (Filleted):', self.source_info)

        group = None
        for obj in selection:
            group = _resolve_segment_group(obj)
            if group:
                break
        if not group and doc:
            group = _latest_segment_group(doc)
        if doc and group:
            _pattern_lab_backfill_set_controls(doc, group)
        self.segment_planes = _find_group_members(group, 'SegmentPlane_') if group else []
        self.anchor_plane = _first_segment_plane(group) if group else None

        source_tile_height_mm = _pattern_lab_combined_sources_height_mm(self.anchor_plane, self.source_objs)
        if source_tile_height_mm <= 1e-6:
            source_tile_height_mm = _pattern_lab_source_tile_height_mm(self.anchor_plane, self.source_objs)
        if source_tile_height_mm > 1e-6:
            # 1x Cell Height = full combined source-set height.
            self.cell_step_mm = max(0.1, source_tile_height_mm)
        else:
            self.cell_step_mm = _pattern_lab_default_cell_step_mm(self.anchor_plane) if self.anchor_plane else (0.25 * 25.4)
        default_step_in = self.cell_step_mm / 25.4

        self.step_mode = QtGui.QComboBox()
        self.step_mode.addItems(['1x Cell Height', '2x Cell Height', '3x Cell Height', 'Fixed Step'])
        saved_mode = str(PATTERN_LAB_PREFS.GetString('pattern_down_step_mode', '1x Cell Height') or '1x Cell Height')
        mode_index = self.step_mode.findText(saved_mode)
        self.step_mode.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        layout.addRow('Step Mode:', self.step_mode)

        self.step_in = QtGui.QDoubleSpinBox()
        self.step_in.setMinimum(0.001)
        self.step_in.setMaximum(10.0)
        self.step_in.setDecimals(4)
        self.step_in.setSingleStep(0.005)
        self.step_in.setValue(PATTERN_LAB_PREFS.GetFloat('pattern_down_step_in', default_step_in))
        self.step_in.setSuffix(' in')
        layout.addRow('Fixed Step (Y):', self.step_in)

        self.copy_to_other_segments = QtGui.QCheckBox()
        self.copy_to_other_segments.setChecked(PATTERN_LAB_PREFS.GetBool('pattern_down_copy_to_other_segments', True))
        layout.addRow('Copy To Other Segments:', self.copy_to_other_segments)

        self.offset_mode = QtGui.QComboBox()
        self.offset_mode.addItems(['No Offset', '1x Cell Height', '2x Cell Height', '3x Cell Height', 'Fixed Offset', 'Pick 2 Points'])
        saved_offset_mode = str(PATTERN_LAB_PREFS.GetString('pattern_down_offset_mode', 'No Offset') or 'No Offset')
        offset_mode_index = self.offset_mode.findText(saved_offset_mode)
        self.offset_mode.setCurrentIndex(offset_mode_index if offset_mode_index >= 0 else 0)
        layout.addRow('Segment Offset Mode:', self.offset_mode)

        self.offset_in = QtGui.QDoubleSpinBox()
        self.offset_in.setMinimum(0.0)
        self.offset_in.setMaximum(10.0)
        self.offset_in.setDecimals(4)
        self.offset_in.setSingleStep(0.005)
        self.offset_in.setValue(PATTERN_LAB_PREFS.GetFloat('pattern_down_offset_in', default_step_in))
        self.offset_in.setSuffix(' in')
        layout.addRow('Fixed Offset (Y):', self.offset_in)

        self.offset_even_segments_only = QtGui.QCheckBox()
        self.offset_even_segments_only.setChecked(
            PATTERN_LAB_PREFS.GetBool(
                'pattern_down_offset_even_segments_only',
                PATTERN_LAB_PREFS.GetBool('pattern_down_offset_even_rows_only', True),
            )
        )
        layout.addRow('Offset Even Segments Only:', self.offset_even_segments_only)

        self.preview = QtGui.QLabel('')
        layout.addRow('Preview:', self.preview)

        self.replace_existing = QtGui.QCheckBox()
        self.replace_existing.setChecked(PATTERN_LAB_PREFS.GetBool('pattern_down_replace_existing', True))
        layout.addRow(self.output_config['replace_label'], self.replace_existing)

        try:
            self.step_mode.currentIndexChanged.connect(self._refresh_preview)
        except Exception:
            pass
        try:
            self.step_in.valueChanged.connect(self._refresh_preview)
        except Exception:
            pass
        try:
            self.copy_to_other_segments.toggled.connect(self._refresh_preview)
        except Exception:
            pass
        try:
            self.offset_mode.currentIndexChanged.connect(self._refresh_preview)
        except Exception:
            pass
        try:
            self.offset_in.valueChanged.connect(self._refresh_preview)
        except Exception:
            pass
        try:
            self.offset_even_segments_only.toggled.connect(self._refresh_preview)
        except Exception:
            pass

        self._refresh_preview()

        self.form.setLayout(layout)

    def _effective_step_mm(self):
        mode = str(self.step_mode.currentText() or '').strip().lower()
        if mode.startswith('2x'):
            return max(0.1, self.cell_step_mm * 2.0)
        if mode.startswith('3x'):
            return max(0.1, self.cell_step_mm * 3.0)
        if mode.startswith('fixed'):
            return max(0.1, float(self.step_in.value()) * 25.4)
        return max(0.1, self.cell_step_mm)

    def _effective_offset_mm(self):
        mode = str(self.offset_mode.currentText() or '').strip().lower()
        if mode.startswith('no offset'):
            return 0.0
        if mode.startswith('2x'):
            return max(0.0, self.cell_step_mm * 2.0)
        if mode.startswith('3x'):
            return max(0.0, self.cell_step_mm * 3.0)
        if mode.startswith('fixed'):
            return max(0.0, float(self.offset_in.value()) * 25.4)
        if mode.startswith('pick 2 points'):
            picked_mm = _selected_two_point_step_mm(self.anchor_plane)
            if picked_mm > 1e-6:
                return max(0.0, picked_mm)
        return max(0.0, self.cell_step_mm)

    def _offset_phase_mm(self, offset_mm, step_mm):
        offset_val = max(0.0, float(offset_mm or 0.0))
        step_val = max(0.0, float(step_mm or 0.0))
        if offset_val <= 1e-9 or step_val <= 1e-9:
            return offset_val
        phase = math.fmod(offset_val, step_val)
        if phase < 0.0:
            phase += step_val
        if phase <= 1e-9 or abs(phase - step_val) <= 1e-9:
            return 0.0
        return phase

    def _target_plane_count(self):
        if self.copy_to_other_segments.isChecked() and self.segment_planes:
            return len(self.segment_planes)
        return 1

    def _auto_fill_count(self, step_mm, leading_offset_mm=0.0, source_span_mm=0.0):
        if step_mm <= 1e-6:
            return 1
        if not self.anchor_plane:
            return max(1, int(PATTERN_LAB_PREFS.GetInt('pattern_down_count', 6)))
        span_mm = max(0.0, _as_float(getattr(self.anchor_plane, 'Width', 0.0), 0.0))
        if span_mm <= 1e-6:
            return max(1, int(PATTERN_LAB_PREFS.GetInt('pattern_down_count', 6)))
        usable_span_mm = max(0.0, span_mm - max(0.0, float(source_span_mm or 0.0)))
        available_mm = usable_span_mm - max(0.0, float(leading_offset_mm or 0.0))
        if available_mm < 0.0:
            return 0
        return max(1, int(math.floor(available_mm / step_mm)) + 1)

    def _refresh_preview(self, *_args):
        is_fixed = str(self.step_mode.currentText() or '').strip().lower().startswith('fixed')
        self.step_in.setEnabled(is_fixed)
        is_fixed_offset = str(self.offset_mode.currentText() or '').strip().lower().startswith('fixed')
        self.offset_in.setEnabled(is_fixed_offset)

        step_mm = self._effective_step_mm()
        offset_mm = self._effective_offset_mm()
        offset_phase_mm = self._offset_phase_mm(offset_mm, step_mm)
        count = self._auto_fill_count(step_mm)
        span_in = 0.0
        if self.anchor_plane:
            span_in = max(0.0, _as_float(getattr(self.anchor_plane, 'Width', 0.0), 0.0)) / 25.4
        source_count = len(self.source_objs)
        target_count = self._target_plane_count()
        first_segment_shift_in = (offset_phase_mm / 25.4) if (offset_phase_mm > 1e-9 and self.offset_even_segments_only.isChecked()) else 0.0

        if offset_phase_mm > 1e-9 and target_count > 1:
            if self.offset_even_segments_only.isChecked():
                even_seg_count = target_count // 2
                odd_seg_count = target_count - even_seg_count
                rows_even = self._auto_fill_count(step_mm, leading_offset_mm=offset_phase_mm)
                rows_odd = self._auto_fill_count(step_mm, leading_offset_mm=0.0)
                est_rows_total = even_seg_count * rows_even + odd_seg_count * rows_odd
            else:
                rows_all = self._auto_fill_count(step_mm, leading_offset_mm=offset_phase_mm)
                est_rows_total = target_count * rows_all
        else:
            est_rows_total = target_count * count

        if source_count <= 0:
            self.preview.setText(
                f'{step_mm / 25.4:.4f} in step, {count} row(s), {target_count} segment(s), '
                f'first-segment shift {first_segment_shift_in:.4f} in, 0 valid filleted sources selected'
            )
        else:
            self.preview.setText(
                f'{step_mm / 25.4:.4f} in step, {count} row(s), {target_count} segment(s) across {span_in:.4f} in span, '
                f'first-segment shift {first_segment_shift_in:.4f} in, {source_count} source(s) => {source_count * est_rows_total} link(s)'
            )

    def accept(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError('No active document.\n')
            return False

        selection = FreeCADGui.Selection.getSelection() if FreeCADGui else []

        group = None
        for obj in selection:
            group = _resolve_segment_group(obj)
            if group:
                break
        if not group:
            group = _latest_segment_group(doc)
        if not group:
            FreeCAD.Console.PrintError('Could not find a SegmentPlanes_* group. Create section planes first.\n')
            return False

        anchor_plane = _first_segment_plane(group)
        if not anchor_plane:
            FreeCAD.Console.PrintError('Segment group is missing SegmentPlane_* objects.\n')
            return False

        source_objs = list(self.source_objs or [])
        if not source_objs:
            source_objs = _collect_shape_sources_from_selection(selection, require_filleted=True)
        if not source_objs:
            any_shape_sources = _collect_shape_sources_from_selection(selection, require_filleted=False)
            if any_shape_sources:
                FreeCAD.Console.PrintError(
                    'No filleted source objects found in selection. Select filleted inlay solids '
                    '(created by Fillet for CNC, usually named *_fillet).\n'
                )
            else:
                FreeCAD.Console.PrintError(
                    'Select one or more filleted source object(s) with shape before running Pattern Down Segment.\n'
                )
            return False

        step_mode = str(self.step_mode.currentText() or '1x Cell Height')
        step_mm = self._effective_step_mm()
        offset_mode = str(self.offset_mode.currentText() or 'No Offset')
        offset_mm = self._effective_offset_mm()
        if str(offset_mode).strip().lower().startswith('pick 2 points'):
            offset_mm = _selected_two_point_step_mm(anchor_plane)
            if offset_mm <= 1e-6:
                FreeCAD.Console.PrintError(
                    'Pick 2 points first (Ctrl-select two vertices/points), then run Pattern Down Segment with Segment Offset Mode = Pick 2 Points.\n'
                )
                return False
            offset_mm = max(0.0, offset_mm)
        offset_phase_mm = self._offset_phase_mm(offset_mm, step_mm)
        offset_even_segments_only = self.offset_even_segments_only.isChecked()
        copy_to_other_segments = self.copy_to_other_segments.isChecked()
        count = self._auto_fill_count(step_mm)
        step_in = step_mm / 25.4
        offset_in = offset_mm / 25.4
        replace_existing = self.replace_existing.isChecked()

        PATTERN_LAB_PREFS.SetString('pattern_down_step_mode', step_mode)
        PATTERN_LAB_PREFS.SetFloat('pattern_down_step_in', step_in)
        PATTERN_LAB_PREFS.SetBool('pattern_down_copy_to_other_segments', copy_to_other_segments)
        PATTERN_LAB_PREFS.SetString('pattern_down_offset_mode', offset_mode)
        PATTERN_LAB_PREFS.SetFloat('pattern_down_offset_in', offset_in)
        PATTERN_LAB_PREFS.SetBool('pattern_down_offset_even_segments_only', offset_even_segments_only)
        PATTERN_LAB_PREFS.SetBool('pattern_down_offset_even_rows_only', offset_even_segments_only)
        PATTERN_LAB_PREFS.SetInt('pattern_down_count', count)
        PATTERN_LAB_PREFS.SetBool('pattern_down_replace_existing', replace_existing)

        if step_mm <= 1e-6:
            FreeCAD.Console.PrintError('Step must be greater than 0.\n')
            return False

        created_links, target_planes = _create_pattern_lab_segment_instances(
            doc,
            group,
            anchor_plane,
            source_objs,
            step_mm,
            offset_phase_mm,
            offset_even_segments_only,
            copy_to_other_segments,
            replace_existing,
            self._auto_fill_count,
            output_mode=self.output_mode,
        )

        created_count = len(created_links)

        segment_offset_scope = 'even segments only' if offset_even_segments_only else 'all segments'
        FreeCAD.Console.PrintMessage(
            f'[PatternLab] Created {created_count} {self.output_config["summary_label"]} from {len(source_objs)} source object(s) '
            f'down segment Y with step {step_in:.4f} in ({step_mode}, auto-fill), '
            f'offset {offset_in:.4f} in ({offset_mode}, {segment_offset_scope}), '
            f'target segments={len(target_planes)}.\n'
        )
        FreeCAD.Console.PrintMessage(
            '[PatternLab] Edit set properties on the created PatternDown/Kite set: '
            'PatternStep, PatternSegmentOffset, PatternOffsetEvenSegmentsOnly for live spacing updates.\n'
        )
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True


class PatternLabPatternDownSegmentCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Pattern Lab Pattern Down Segment',
            'ToolTip': 'Create parametric binder-based links from filleted source solids only; pattern down segment Y with 1x/2x/3x/fixed step, optional copy to all segments, and optional even-segment offset.',
        }

    def Activated(self):
        _show_task_panel_safe(PatternLabPatternDownSegmentTaskPanel())

    def IsActive(self):
        return True


class PatternLabPocketComponentTaskPanel:
    def __init__(self):
        self.form = QtGui.QWidget()
        layout = QtGui.QFormLayout(self.form)

        self.info = QtGui.QLabel()
        self.info.setWordWrap(True)
        layout.addRow(self.info)

        self.update_info()

    def update_info(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            self.info.setText('No active document.')
            return

        selection = FreeCADGui.Selection.getSelection() if FreeCADGui else []
        if not selection:
            self.info.setText('Select a PatternDownSet_* container to pocket.')
            return

        selected_obj = selection[0]
        selected_name = str(getattr(selected_obj, 'Name', ''))
        selected_label = str(getattr(selected_obj, 'Label', selected_name))

        if not selected_name.startswith('PatternDownSet_'):
            self.info.setText(f'Selected "{selected_label}" is not a PatternDownSet_* container.')
            return

        segment_group = _resolve_segment_group(selected_obj)
        if not segment_group:
            self.info.setText('Could not find parent segment group.')
            return

        target_obj, target_shape = _segment_group_target_solid(doc, segment_group)
        if not target_shape:
            self.info.setText('Could not find target component for pocketing.')
            return

        links = list(getattr(selected_obj, 'Group', []) or [])
        link_count = len(links)

        target_label = str(getattr(target_obj, 'Label', target_obj.Name))
        self.info.setText(
            f'Selected: {selected_label} ({link_count} link(s))\n'
            f'Target: {target_label}\n\n'
            f'Click OK to subtract all pattern geometry from the target component.'
        )

    def accept(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError('No active document.\n')
            return False

        selection = FreeCADGui.Selection.getSelection() if FreeCADGui else []
        if not selection:
            FreeCAD.Console.PrintError('Select a PatternDownSet_* container.\n')
            return False

        selected_obj = selection[0]
        selected_name = str(getattr(selected_obj, 'Name', ''))

        if not selected_name.startswith('PatternDownSet_'):
            FreeCAD.Console.PrintError(f'Selected object "{selected_name}" is not a PatternDownSet_* container.\n')
            return False

        segment_group = _resolve_segment_group(selected_obj)
        if not segment_group:
            FreeCAD.Console.PrintError('Could not find parent segment group.\n')
            return False

        target_obj, target_shape = _segment_group_target_solid(doc, segment_group)
        if not target_shape:
            FreeCAD.Console.PrintError('Could not find target component for pocketing.\n')
            return False

        links = list(getattr(selected_obj, 'Group', []) or [])
        if not links:
            FreeCAD.Console.PrintError(f'PatternDownSet "{selected_name}" has no links to subtract.\n')
            return False

        try:
            result = target_shape.copy()
            links_cut = 0
            for link_obj in links:
                link_obj_found, link_shape = _find_solid(link_obj)
                if link_shape:
                    try:
                        result = result.cut(link_shape)
                        links_cut += 1
                    except Exception as e:
                        FreeCAD.Console.PrintWarning(f'Failed to cut link {getattr(link_obj, "Name", "?")}: {e}\n')

            result_obj = doc.addObject('Part::Feature', f'{selected_name}_Pocketed')
            result_obj.Shape = result
            result_obj.Label = f'{getattr(target_obj, "Label", target_obj.Name)} Pocketed'
            _copy_view_appearance(target_obj, result_obj)

            FreeCAD.Console.PrintMessage(
                f'[PatternLab] Pocketed {links_cut}/{len(links)} inlay link(s) into component. '
                f'Created "{result_obj.Label}". Replace original or delete pattern links as needed.\n'
            )

        except Exception as e:
            FreeCAD.Console.PrintError(f'Boolean subtraction failed: {e}\n')
            return False

        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True


class PatternLabPocketComponentCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Pattern Lab Pocket Component',
            'ToolTip': 'Subtract pattern link geometry from component using boolean cut. Select a PatternDownSet_* container.',
        }

    def Activated(self):
        _show_task_panel_safe(PatternLabPocketComponentTaskPanel())

    def IsActive(self):
        return True


class PatternLabCreateSolidsTaskPanel:
    def __init__(self):
        self.form = QtGui.QWidget()
        layout = QtGui.QFormLayout(self.form)

        self.depth_in = QtGui.QDoubleSpinBox()
        self.depth_in.setMinimum(0.001)
        self.depth_in.setMaximum(1.000)
        self.depth_in.setDecimals(4)
        self.depth_in.setSingleStep(0.001)
        self.depth_in.setValue(PATTERN_LAB_PREFS.GetFloat('solid_depth_in', DEFAULT_PATTERN_LAB_SOLID_DEPTH_IN))
        self.depth_in.setSuffix(' in')
        layout.addRow('Depth:', self.depth_in)

        self.apply_fillet = QtGui.QCheckBox()
        self.apply_fillet.setChecked(PATTERN_LAB_PREFS.GetBool('solid_apply_fillet', True))
        layout.addRow('Apply Fillet for CNC:', self.apply_fillet)

        self.fillet_radius_in = QtGui.QDoubleSpinBox()
        self.fillet_radius_in.setMinimum(0.0001)
        self.fillet_radius_in.setMaximum(0.2500)
        self.fillet_radius_in.setDecimals(4)
        self.fillet_radius_in.setSingleStep(0.001)
        self.fillet_radius_in.setValue(PATTERN_LAB_PREFS.GetFloat('solid_fillet_radius_in', _default_inlay_fillet_radius_inch()))
        self.fillet_radius_in.setSuffix(' in')
        layout.addRow('Fillet Radius:', self.fillet_radius_in)

        self.replace_existing = QtGui.QCheckBox()
        self.replace_existing.setChecked(PATTERN_LAB_PREFS.GetBool('solid_replace_existing', True))
        layout.addRow('Replace Existing Inlay Solids:', self.replace_existing)

        self.form.setLayout(layout)

    def accept(self):
        doc = FreeCAD.ActiveDocument
        if not doc:
            FreeCAD.Console.PrintError('No active document.\n')
            return False

        depth_in = self.depth_in.value()
        apply_fillet = self.apply_fillet.isChecked()
        fillet_radius_in = self.fillet_radius_in.value()
        replace_existing = self.replace_existing.isChecked()

        PATTERN_LAB_PREFS.SetFloat('solid_depth_in', depth_in)
        PATTERN_LAB_PREFS.SetBool('solid_apply_fillet', apply_fillet)
        PATTERN_LAB_PREFS.SetFloat('solid_fillet_radius_in', fillet_radius_in)
        PATTERN_LAB_PREFS.SetBool('solid_replace_existing', replace_existing)

        selection = FreeCADGui.Selection.getSelection() if FreeCADGui else []
        group = None
        for obj in selection:
            group = _resolve_segment_group(obj)
            if group:
                break
        if not group:
            group = _latest_segment_group(doc)
        if not group:
            FreeCAD.Console.PrintError('Could not find a SegmentPlanes_* group. Create section planes first.\n')
            return False

        anchor_plane = _first_segment_plane(group)
        if not anchor_plane:
            FreeCAD.Console.PrintError('Segment group is missing SegmentPlane_* objects.\n')
            return False

        inlay_group = _ensure_pattern_lab_group(doc, group)
        source_sketches = []
        suffix = _extract_trailing_number(str(getattr(group, 'Name', '')), 0)
        for child in getattr(inlay_group, 'Group', []) or []:
            if getattr(child, 'TypeId', '') != 'Sketcher::SketchObject':
                continue
            child_name = str(getattr(child, 'Name', '') or '')
            token = _pattern_lab_material_from_name(child_name, 'PatternMasterSketch', suffix)
            if token is None:
                token = _pattern_lab_material_token(child_name)
            source_sketches.append((token, child))

        if not source_sketches:
            FreeCAD.Console.PrintError('No sketches found in Inlay_sketches.\n')
            return False

        solids_group = _ensure_inlay_solids_group(doc, group)
        if replace_existing:
            for existing in list(getattr(solids_group, 'Group', []) or []):
                if str(getattr(existing, 'Name', '') or '').startswith('InlaySolid_'):
                    try:
                        doc.removeObject(existing.Name)
                    except Exception:
                        pass

        depth_mm = depth_in * 25.4

        try:
            import inlays
        except Exception as exc:
            FreeCAD.Console.PrintError(f'Failed to load inlays module: {exc}\n')
            return False

        created_count = 0
        filleted_count = 0
        for token, sketch in source_sketches:
            source_plane = _segment_plane_from_sketch(sketch, anchor_plane)
            depth_vec = _inward_depth_vector(source_plane, depth_mm) if source_plane else None
            if not depth_vec:
                FreeCAD.Console.PrintWarning(
                    f'[PatternLab] Could not compute inward depth vector for {getattr(sketch, "Name", "<unknown>")}; skipping.\n'
                )
                continue

            world_matrix = _sketch_world_matrix(sketch, source_plane)
            if world_matrix is None:
                FreeCAD.Console.PrintWarning(
                    f'[PatternLab] Could not resolve placement for {getattr(sketch, "Name", "<unknown>")}; skipping.\n'
                )
                continue

            non_construction_count = _non_construction_geometry_count(sketch)
            local_faces = _master_faces_from_sketch(sketch, transform_matrix=world_matrix)
            if not local_faces:
                if non_construction_count <= 0:
                    FreeCAD.Console.PrintWarning(
                        f'[PatternLab] Sketch {getattr(sketch, "Name", "<unknown>")} has no non-construction geometry; skipping. '
                        'Open the sketch and draw your inlay profile, then run Create Solids again.\n'
                    )
                else:
                    FreeCAD.Console.PrintWarning(
                        f'[PatternLab] Sketch {getattr(sketch, "Name", "<unknown>")} has {non_construction_count} non-construction geometry item(s) '
                        'but no closed face loops could be built; skipping. Ensure the profile forms a closed region.\n'
                    )
                continue

            solids = []
            for local_face in local_faces:
                try:
                    solids.append(local_face.extrude(depth_vec))
                except Exception:
                    continue
            if not solids:
                continue

            try:
                result_shape = Part.makeCompound(solids)
            except Exception:
                continue
            if not result_shape or result_shape.isNull():
                continue

            solid_name = f'InlaySolid_{token}_{suffix}'
            solid_obj = doc.getObject(solid_name)
            if not solid_obj:
                solid_obj = doc.addObject('Part::Feature', solid_name)
            solid_obj.Label = f'Inlay Solid {token} {suffix}'
            try:
                solid_obj.Shape = result_shape
            except Exception:
                continue

            try:
                solids_group.addObject(solid_obj)
            except Exception:
                pass
            created_count += 1

            if apply_fillet:
                try:
                    fillet_obj = inlays.fillet_for_cnc(
                        target=solid_obj,
                        fillet_radius_inch=fillet_radius_in,
                        final_solid_name=solid_obj.Label,
                        preserve_unfilleted=True,
                        allow_smaller_radius_fallback=True,
                        require_full_coverage=False,
                        show_dialog=False,
                    )
                    if fillet_obj:
                        try:
                            solids_group.addObject(fillet_obj)
                        except Exception:
                            pass
                        filleted_count += 1
                except Exception as exc:
                    FreeCAD.Console.PrintWarning(f'[PatternLab] Fillet failed for {solid_obj.Name}: {exc}\n')

        try:
            doc.recompute()
        except Exception:
            pass

        FreeCAD.Console.PrintMessage(
            f'[PatternLab] Created {created_count} inlay solid(s) in Inlay_solids at depth {depth_in:.4f} in'
            + (f'; filleted {filleted_count} with radius {fillet_radius_in:.4f} in.\n' if apply_fillet else '.\n')
        )
        FreeCADGui.Control.closeDialog()
        return True

    def reject(self):
        FreeCADGui.Control.closeDialog()
        return True


class PatternLabCreateSolidsCommand:
    def GetResources(self):
        return {
            'Pixmap': '',
            'MenuText': 'Pattern Lab Create Solids',
            'ToolTip': 'Pad all sketches in Inlay_sketches into inlay solids and optionally run Fillet for CNC.',
        }

    def Activated(self):
        _show_task_panel_safe(PatternLabCreateSolidsTaskPanel())

    def IsActive(self):
        return True

FreeCADGui.addCommand('Tiling_TileArray', TilingTileArrayCommand())
FreeCADGui.addCommand('Tiling_SegmentSections', SegmentSectionPlanesCommand())
FreeCADGui.addCommand('Tiling_SplitSegmentMasterSketch', SplitSegmentMasterSketchCommand())
FreeCADGui.addCommand('Tiling_QbertPatternToMaster', QbertPatternToMasterCommand())
FreeCADGui.addCommand('Tiling_QbertPocketSolids', QbertPocketSolidsCommand())
FreeCADGui.addCommand('Tiling_SegmentPocketCAMJobs', SegmentPocketCAMJobsCommand())
FreeCADGui.addCommand('Tiling_PatternLabTileSketch', PatternLabTileSketchCommand())
FreeCADGui.addCommand('Tiling_PatternLabPatternDownSegment', PatternLabPatternDownSegmentCommand())
FreeCADGui.addCommand('Tiling_PatternLabPocketComponent', PatternLabPocketComponentCommand())
FreeCADGui.addCommand('Tiling_PatternLabCreateSolids', PatternLabCreateSolidsCommand())

_ensure_pattern_lab_set_controls_observer()
