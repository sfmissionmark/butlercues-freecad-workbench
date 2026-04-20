#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FreeCAD Document Validation

Validates a FreeCAD document structure before building a SegmentTree.
Checks for correct naming, required properties, and cross-references.
"""

from typing import Dict, List
from .naming_convention import NamingConvention
from .segment_tree import Inlay, Segment


class DocumentValidator:
    """Validates FreeCAD document structure before building SegmentTree."""
    
    def __init__(self, freecad_doc):
        """
        Initialize validator with a FreeCAD document.
        
        Args:
            freecad_doc: FreeCAD document object (App.ActiveDocument)
        """
        self.doc = freecad_doc
        self.errors: List[str] = []
        self.warnings: List[str] = []
    
    def validate_all(self) -> dict:
        """
        Run all validation checks.
        
        Returns:
            {
                'valid': bool,
                'errors': [str],
                'warnings': [str],
                'segments': {name: obj},
                'inlays': {name: obj}
            }
        """
        segments_dict = self._find_segments()
        inlays_dict = self._find_inlays()
        
        self._validate_segment_objects(segments_dict)
        self._validate_inlay_objects(inlays_dict)
        self._validate_cross_references(segments_dict, inlays_dict)
        
        return {
            'valid': len(self.errors) == 0,
            'errors': self.errors,
            'warnings': self.warnings,
            'segments': segments_dict,
            'inlays': inlays_dict
        }
    
    def _find_segments(self) -> Dict[str, object]:
        """Find all objects matching segment naming pattern."""
        segments = {}
        for obj in self.doc.Objects:
            try:
                component, seq = NamingConvention.parse_segment_name(obj.Name)
                segments[obj.Name] = obj
            except ValueError:
                pass  # Not a segment
        return segments
    
    def _find_inlays(self) -> Dict[str, object]:
        """Find all objects matching inlay naming pattern."""
        inlays = {}
        for obj in self.doc.Objects:
            try:
                component, seg_num, material, seq = NamingConvention.parse_inlay_name(obj.Name)
                inlays[obj.Name] = obj
            except ValueError:
                pass  # Not an inlay
        return inlays
    
    def _validate_segment_objects(self, segments_dict: Dict[str, object]) -> None:
        """Check segment objects have required attributes."""
        for seg_name, seg_obj in segments_dict.items():
            try:
                component, seq = NamingConvention.parse_segment_name(seg_name)
                
                if not NamingConvention.validate_component_name(component):
                    self.errors.append(
                        f"Segment {seg_name}: unknown component '{component}'"
                    )
                
                # Check it's actually a solid/shape
                if not hasattr(seg_obj, 'Shape'):
                    self.errors.append(
                        f"Segment {seg_name}: no Shape attribute (not a body?)"
                    )
                
            except ValueError as e:
                self.errors.append(f"Segment {seg_name}: {str(e)}")
    
    def _validate_inlay_objects(self, inlays_dict: Dict[str, object]) -> None:
        """Check inlay objects have required properties."""
        for inlay_name, inlay_obj in inlays_dict.items():
            try:
                component, seg_num, material, seq = NamingConvention.parse_inlay_name(inlay_name)
                
                if not NamingConvention.validate_component_name(component):
                    self.errors.append(
                        f"Inlay {inlay_name}: unknown component '{component}'"
                    )
                
                if not NamingConvention.validate_material_name(material):
                    self.errors.append(
                        f"Inlay {inlay_name}: unknown material '{material}'"
                    )
                
                # Check required properties exist
                for prop_name in NamingConvention.REQUIRED_INLAY_PROPS:
                    if not hasattr(inlay_obj, prop_name):
                        self.errors.append(
                            f"Inlay {inlay_name}: missing property '{prop_name}'"
                        )
                    else:
                        # Try to get value (type check)
                        try:
                            val = getattr(inlay_obj, prop_name)
                            if prop_name in {"InlayDepth", "FilletRadius"}:
                                if not isinstance(val, (int, float)):
                                    self.errors.append(
                                        f"Inlay {inlay_name}: {prop_name} must be numeric "
                                        f"(got {type(val).__name__})"
                                    )
                        except Exception as e:
                            self.errors.append(
                                f"Inlay {inlay_name}: error reading {prop_name}: {str(e)}"
                            )
                
                # Check it's a sketch
                if not hasattr(inlay_obj, 'TypeId'):
                    self.errors.append(
                        f"Inlay {inlay_name}: no TypeId (not a valid object?)"
                    )
                elif inlay_obj.TypeId != 'Sketcher::SketchObject':
                    self.errors.append(
                        f"Inlay {inlay_name}: not a sketch (TypeId: {inlay_obj.TypeId})"
                    )
                
            except ValueError as e:
                self.errors.append(f"Inlay {inlay_name}: {str(e)}")
    
    def _validate_cross_references(
        self, segments_dict: Dict[str, object], inlays_dict: Dict[str, object]
    ) -> None:
        """Check inlays reference valid parent segments."""
        for inlay_name in inlays_dict.keys():
            try:
                component, seg_num, material, seq = NamingConvention.parse_inlay_name(inlay_name)
                expected_parent = NamingConvention.generate_segment_name(component, seg_num)
                
                if expected_parent not in segments_dict:
                    self.errors.append(
                        f"Inlay {inlay_name}: parent segment {expected_parent} not found"
                    )
                
            except ValueError:
                pass  # Already reported above
