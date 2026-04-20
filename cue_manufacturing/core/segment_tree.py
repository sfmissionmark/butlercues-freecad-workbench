#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Core Data Model for Cue Manufacturing

SegmentTree represents the complete structure of a cue's segments and inlays.
Built from a FreeCAD document via scanner, then used by downstream systems
(CAM planner, inlay generator, exporters, etc.)

Immutable after finalization to prevent accidental mutations during workflow.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from .naming_convention import NamingConvention


@dataclass
class Inlay:
    """Represents a single inlay sketch within a segment."""
    
    id: str                      # Object name: 'Inlay_Forearm_01_Walnut_001'
    segment_id: str              # Parent segment: 'Cue_Forearm_Seg_01'
    material: str                # 'Walnut', 'Holly', etc.
    sequence: int                # Order within segment+material: 1, 2, 3...
    
    sketch_ref: Optional[object] = None    # Reference to FreeCAD sketch object
    
    depth_inch: float = 0.125
    fillet_radius_inch: float = 0.014
    relief_type: str = "bore"    # "bore", "stepped", "conical"
    relief_step_height_inch: float = 0.056
    grain_angle: float = 0.0
    
    baxis_angle: float = 0.0     # Computed from parent segment
    
    def validate(self) -> Tuple[bool, List[str]]:
        """
        Validate inlay configuration.
        
        Returns:
            (is_valid, [error messages])
        """
        errors = []
        
        # Check name format
        try:
            component, seg_num, material, seq = NamingConvention.parse_inlay_name(self.id)
            if not NamingConvention.validate_component_name(component):
                errors.append(f"Unknown component: {component}")
            if not NamingConvention.validate_material_name(material):
                errors.append(f"Unknown material: {material}")
        except ValueError as e:
            errors.append(str(e))
        
        # Check values
        if self.depth_inch <= 0:
            errors.append(f"Depth must be positive: {self.depth_inch}")
        if self.fillet_radius_inch < 0:
            errors.append(f"Fillet radius cannot be negative: {self.fillet_radius_inch}")
        if self.relief_type not in {"bore", "stepped", "conical"}:
            errors.append(f"Unknown relief_type: {self.relief_type}")
        
        if self.relief_type == "stepped" and self.relief_step_height_inch <= 0:
            errors.append(
                f"Stepped relief requires positive step height: {self.relief_step_height_inch}"
            )
        
        if self.sketch_ref is None:
            errors.append(f"No sketch reference: {self.id}")
        
        return len(errors) == 0, errors
    
    def full_name(self) -> str:
        """For logging: 'Inlay_Forearm_01_Walnut_001 (Walnut, 0.125in deep, seq 1)'"""
        return f"{self.id} ({self.material}, {self.depth_inch}in deep, seq {self.sequence})"


@dataclass
class Segment:
    """Represents a single segment (one portion of cue to machine as a unit)."""
    
    id: str                      # Object name: 'Cue_Forearm_Seg_01'
    component: str               # 'Forearm', 'Handle', 'ButtSleeve'
    sequence: int                # Order within component: 1, 2, 3...
    
    geometry_ref: Optional[object] = None  # Reference to FreeCAD solid object
    baxis_angle: float = 0.0               # B-axis rotation needed
    
    _inlays: Dict[str, Inlay] = field(default_factory=dict)  # id → Inlay
    
    def add_inlay(self, inlay: Inlay) -> None:
        """
        Add an inlay to this segment.
        
        Args:
            inlay: Inlay object whose segment_id must match this segment's id
        
        Raises:
            ValueError: If inlay's segment_id doesn't match
        """
        if inlay.segment_id != self.id:
            raise ValueError(
                f"Inlay {inlay.id} belongs to {inlay.segment_id}, not {self.id}"
            )
        self._inlays[inlay.id] = inlay
    
    def inlays_for_material(self, material: str) -> List[Inlay]:
        """
        Get all inlays of a specific material, sorted by sequence.
        
        Args:
            material: Material name, e.g., 'Walnut'
        
        Returns:
            Sorted list of Inlay objects
        """
        result = [i for i in self._inlays.values() if i.material == material]
        return sorted(result, key=lambda i: i.sequence)
    
    def all_inlays(self) -> List[Inlay]:
        """Get all inlays for this segment (in any material)."""
        return list(self._inlays.values())
    
    def validate(self) -> Tuple[bool, List[str]]:
        """
        Validate segment and all its inlays.
        
        Returns:
            (is_valid, [error messages])
        """
        errors = []
        
        # Check name format
        try:
            component, seq = NamingConvention.parse_segment_name(self.id)
            if not NamingConvention.validate_component_name(component):
                errors.append(f"Unknown component: {component}")
            if component != self.component:
                errors.append(f"Name says {component}, but component is {self.component}")
        except ValueError as e:
            errors.append(str(e))
        
        if self.geometry_ref is None:
            errors.append(f"No geometry reference: {self.id}")
        
        # Validate all inlays
        for inlay_id, inlay in self._inlays.items():
            valid, inlay_errors = inlay.validate()
            if not valid:
                errors.extend([f"  {inlay_id}: {e}" for e in inlay_errors])
        
        return len(errors) == 0, errors
    
    def full_name(self) -> str:
        """For logging: 'Cue_Forearm_Seg_01 (Forearm, seq 1, 3 inlays)'"""
        return f"{self.id} (component: {self.component}, seq {self.sequence}, {len(self._inlays)} inlays)"


@dataclass
class ValidationResult:
    """Result of validating a SegmentTree."""
    
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    segments: Dict[str, Segment] = field(default_factory=dict)
    inlays: Dict[str, Inlay] = field(default_factory=dict)
    
    def summary(self) -> str:
        """Human-readable summary."""
        lines = []
        lines.append(f"Valid: {self.valid}")
        lines.append(f"Segments: {len(self.segments)}, Inlays: {len(self.inlays)}")
        if self.errors:
            lines.append(f"ERRORS ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"  - {e}")
        if self.warnings:
            lines.append(f"Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)


class SegmentTree:
    """
    Immutable data structure representing a complete cue's segmentation and inlays.
    
    Built from a FreeCAD document via scanner, then used by all downstream systems.
    After finalize(), the tree becomes read-only to prevent accidental mutations.
    """
    
    def __init__(self):
        self._segments: Dict[str, Segment] = {}      # id → Segment
        self._inlays: Dict[str, Inlay] = {}          # id → Inlay
        self._materials: set = set()                 # All materials seen
        self._material_order: List[str] = []         # User-defined cutting order
        self._finalized = False
    
    def add_segment(self, segment: Segment) -> None:
        """
        Add a segment to the tree.
        
        Args:
            segment: Segment object
        
        Raises:
            RuntimeError: If tree is finalized
            ValueError: If segment already exists
        """
        if self._finalized:
            raise RuntimeError("Cannot add segments after finalization")
        if segment.id in self._segments:
            raise ValueError(f"Segment already exists: {segment.id}")
        self._segments[segment.id] = segment
    
    def add_inlay(self, inlay: Inlay) -> None:
        """
        Add an inlay to the tree.
        
        Automatically adds to parent segment if it exists.
        
        Args:
            inlay: Inlay object
        
        Raises:
            RuntimeError: If tree is finalized
            ValueError: If inlay already exists or parent segment not found
        """
        if self._finalized:
            raise RuntimeError("Cannot add inlays after finalization")
        if inlay.id in self._inlays:
            raise ValueError(f"Inlay already exists: {inlay.id}")
        
        # Find parent segment
        parent = self._segments.get(inlay.segment_id)
        if not parent:
            raise ValueError(
                f"No segment found for inlay {inlay.id}: {inlay.segment_id}"
            )
        
        parent.add_inlay(inlay)
        self._inlays[inlay.id] = inlay
        self._materials.add(inlay.material)
    
    def set_material_order(self, materials: List[str]) -> None:
        """
        Define the order in which materials should be cut.
        
        E.g., ['Pattern', 'Walnut', 'Holly'] means pattern inlays cut first,
        then walnut, then holly.
        
        Args:
            materials: Ordered list of material names
        
        Raises:
            ValueError: If any material is unknown or not present in tree
        """
        for material in materials:
            if material not in self._materials:
                raise ValueError(f"Unknown material: {material}")
        self._material_order = list(materials)
    
    def finalize(self) -> None:
        """
        Lock the tree. After this, it can only be read, not modified.
        
        Useful to catch bugs where tree is accidentally mutated during workflow.
        """
        self._finalized = True
    
    def is_finalized(self) -> bool:
        """Check if tree is locked."""
        return self._finalized
    
    def segments(self) -> List[Segment]:
        """Get all segments."""
        return list(self._segments.values())
    
    def inlays(self) -> List[Inlay]:
        """Get all inlays."""
        return list(self._inlays.values())
    
    def inlays_for_material(self, material: str) -> List[Inlay]:
        """
        All inlays of a material, sorted by segment then sequence.
        
        This is what drives CAM planning for a material pass.
        
        Args:
            material: Material name, e.g., 'Walnut'
        
        Returns:
            Sorted list of Inlay objects
        """
        result = []
        for segment in self._segments.values():
            result.extend(segment.inlays_for_material(material))
        return sorted(result, key=lambda i: (i.segment_id, i.sequence))
    
    def inlays_for_segment(self, segment_id: str) -> List[Inlay]:
        """
        All inlays within a segment, grouped by material in preferred order.
        
        Args:
            segment_id: Segment name, e.g., 'Cue_Forearm_Seg_01'
        
        Returns:
            Inlays sorted by material order, then sequence
        
        Raises:
            ValueError: If segment not found
        """
        segment = self._segments.get(segment_id)
        if not segment:
            raise ValueError(f"Unknown segment: {segment_id}")
        
        # Return grouped by material in the preferred order
        result = []
        material_order = self._material_order or sorted(self._materials)
        for material in material_order:
            result.extend(segment.inlays_for_material(material))
        return result
    
    def inlays_for_material_and_angle(
        self, material: str, baxis_angle: float, tolerance: float = 0.01
    ) -> List[Inlay]:
        """
        All inlays of a material that use this B-axis angle.
        
        Used for grouping cuts to minimize spindle repositioning.
        
        Args:
            material: Material name
            baxis_angle: B-axis angle in degrees
            tolerance: Floating-point tolerance for angle comparison (degrees)
        
        Returns:
            List of Inlay objects with matching angle
        """
        result = []
        for inlay in self.inlays_for_material(material):
            if abs(inlay.baxis_angle - baxis_angle) < tolerance:
                result.append(inlay)
        return result
    
    def get_unique_baxis_angles(self) -> List[float]:
        """
        Get all unique B-axis angles across the entire cue.
        
        Returns:
            Sorted list of angles in degrees
        """
        angles = set()
        for segment in self._segments.values():
            angles.add(segment.baxis_angle)
        return sorted(angles)
    
    def get_cutting_plan(self) -> List[dict]:
        """
        Generate the complete CAM plan as a sequence of cutting operations.
        
        Respects material order and groups cuts by B-axis angle.
        
        Returns:
            List of operation dicts:
            {
                'material': str,
                'baxis_angle': float,
                'segment': Segment,
                'inlay': Inlay,
                'action': str (always 'cut_pocket' for now)
            }
        """
        plan = []
        
        # Process materials in defined order
        material_order = self._material_order or sorted(self._materials)
        
        for material in material_order:
            # Within each material, group by B-axis angle
            for baxis_angle in self.get_unique_baxis_angles():
                # Get all inlays of this material at this angle
                batch = self.inlays_for_material_and_angle(material, baxis_angle)
                
                for inlay in batch:
                    segment = self._segments[inlay.segment_id]
                    plan.append({
                        'material': material,
                        'baxis_angle': baxis_angle,
                        'segment': segment,
                        'inlay': inlay,
                        'action': 'cut_pocket'
                    })
        
        return plan
    
    def validate(self) -> ValidationResult:
        """
        Full validation of the entire tree.
        
        Returns:
            ValidationResult with errors, warnings, and valid structures
        """
        errors = []
        warnings = []
        valid_segments = {}
        valid_inlays = {}
        
        # Validate all segments
        for seg_id, segment in self._segments.items():
            seg_valid, seg_errors = segment.validate()
            if not seg_valid:
                errors.extend(seg_errors)
            else:
                valid_segments[seg_id] = segment
        
        # Validate all inlays
        for inlay_id, inlay in self._inlays.items():
            inlay_valid, inlay_errors = inlay.validate()
            if not inlay_valid:
                errors.extend(inlay_errors)
            else:
                valid_inlays[inlay_id] = inlay
        
        # Check for segments with no inlays (warning, not error)
        for seg_id, segment in self._segments.items():
            if len(segment.all_inlays()) == 0:
                warnings.append(f"Segment {seg_id} has no inlays")
        
        # Check for materials not in material_order (warning)
        if self._material_order:
            for material in self._materials:
                if material not in self._material_order:
                    warnings.append(f"Material {material} not in material_order")
        
        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            segments=valid_segments,
            inlays=valid_inlays
        )
