#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import FreeCAD as App
import FreeCADGui as Gui


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
