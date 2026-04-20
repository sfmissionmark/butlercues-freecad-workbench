import FreeCAD as App

try:
    import FreeCADGui as Gui
except Exception:
    Gui = None


MACRO_TITLE = "Duplicate Attached Sketch"
COPY_GEOMETRY_DEFAULT = False
OPEN_EDITOR_DEFAULT = True


def _qt_message(text, title=MACRO_TITLE):
    try:
        from PySide import QtGui
        QtGui.QMessageBox.information(None, title, text)
        return
    except Exception:
        pass
    try:
        from PySide2 import QtWidgets
        QtWidgets.QMessageBox.information(None, title, text)
        return
    except Exception:
        pass
    print(text)


def _is_sketch(obj):
    return bool(obj) and getattr(obj, "TypeId", "") == "Sketcher::SketchObject"


def _selected_sketch():
    if Gui is None:
        return None
    try:
        sel = list(Gui.Selection.getSelection() or [])
    except Exception:
        sel = []
    for obj in sel:
        if _is_sketch(obj):
            return obj
    return None


def _next_object_name(doc, base):
    root = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(base or "Sketch"))
    root = root.strip("_") or "Sketch"
    if not root[0].isalpha():
        root = "Sketch_" + root
    name = root
    index = 1
    while getattr(doc, "getObject", lambda _name: None)(name) is not None:
        index += 1
        name = f"{root}_{index}"
    return name


def _copy_view(source, target):
    src_view = getattr(source, "ViewObject", None)
    dst_view = getattr(target, "ViewObject", None)
    if src_view is None or dst_view is None:
        return
    for prop in (
        "LineColor",
        "PointColor",
        "ShapeColor",
        "DrawStyle",
        "LineWidth",
        "PointSize",
        "Transparency",
        "Visibility",
    ):
        if hasattr(src_view, prop) and hasattr(dst_view, prop):
            try:
                setattr(dst_view, prop, getattr(src_view, prop))
            except Exception:
                pass


def _copy_attachment(source, target):
    attachment_value = None
    for prop in ("Support", "AttachmentSupport"):
        if not hasattr(source, prop):
            continue
        try:
            value = getattr(source, prop)
            if value:
                attachment_value = value
                break
        except Exception:
            pass

    if attachment_value is not None:
        for prop in ("Support", "AttachmentSupport"):
            if not hasattr(target, prop):
                continue
            try:
                setattr(target, prop, attachment_value)
            except Exception:
                pass

    for prop in ("MapMode", "MapPathParameter", "MapReversed"):
        if hasattr(source, prop) and hasattr(target, prop):
            try:
                setattr(target, prop, getattr(source, prop))
            except Exception:
                pass

    for prop in ("AttachmentOffset", "Placement"):
        if hasattr(source, prop) and hasattr(target, prop):
            try:
                setattr(target, prop, getattr(source, prop))
            except Exception:
                pass

    if hasattr(source, "ExpressionEngine") and hasattr(target, "setExpression"):
        try:
            for expr in list(source.ExpressionEngine or []):
                if isinstance(expr, (list, tuple)) and len(expr) >= 2:
                    target.setExpression(expr[0], expr[1])
        except Exception:
            pass


def _copy_geometry(source, target):
    for index, geo in enumerate(list(getattr(source, "Geometry", []) or [])):
        try:
            new_index = target.addGeometry(geo, False)
            try:
                if source.getConstruction(index):
                    target.toggleConstruction(new_index)
            except Exception:
                pass
        except Exception:
            pass

    for con in list(getattr(source, "Constraints", []) or []):
        try:
            target.addConstraint(con)
        except Exception:
            pass


def _same_parent_containers(source):
    parents = []
    for index, parent in enumerate(list(getattr(source, "InList", []) or [])):
        if not hasattr(parent, "addObject"):
            continue
        type_id = getattr(parent, "TypeId", "")
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


def create_duplicate_attached_sketch(source_sketch=None, copy_geometry=COPY_GEOMETRY_DEFAULT, open_editor=OPEN_EDITOR_DEFAULT):
    source = source_sketch or _selected_sketch()
    if not _is_sketch(source):
        raise ValueError("Select one sketch to duplicate its attachment.")

    doc = source.Document
    if doc is None:
        raise ValueError("The selected sketch is not in an active document.")

    base_label = str(getattr(source, "Label", source.Name) or source.Name)
    new_name = _next_object_name(doc, f"{source.Name}_Copy")
    new_sketch = doc.addObject("Sketcher::SketchObject", new_name)
    new_sketch.Label = f"{base_label} Copy"

    _copy_attachment(source, new_sketch)
    if copy_geometry:
        _copy_geometry(source, new_sketch)
    _copy_view(source, new_sketch)

    for parent in _same_parent_containers(source):
        try:
            parent.addObject(new_sketch)
            break
        except Exception:
            continue

    doc.recompute()

    if Gui is not None:
        try:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(doc.Name, new_sketch.Name)
        except Exception:
            pass
        if open_editor:
            try:
                Gui.activeDocument().setEdit(new_sketch.Name)
            except Exception:
                pass

    return new_sketch


def main():
    try:
        new_sketch = create_duplicate_attached_sketch()
        msg = f"Created {new_sketch.Label} with the same attachment as the selected sketch."
        if Gui is not None:
            try:
                App.Console.PrintMessage(msg + "\n")
            except Exception:
                print(msg)
        else:
            print(msg)
    except Exception as exc:
        _qt_message(str(exc))


if __name__ == "__main__":
    main()
