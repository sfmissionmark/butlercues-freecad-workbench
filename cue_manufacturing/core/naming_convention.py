#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Naming Convention Enforcement for Cue Manufacturing

Defines and validates the naming schema for all FreeCAD objects involved in cue design.
All segments and inlays MUST conform to these patterns.

Schema:
  Segment:  Cue_{Component}_Seg_{SequenceNum}
            e.g., Cue_Forearm_Seg_01, Cue_Handle_Seg_02
  
  Inlay:    Inlay_{Component}_{SegmentNum}_{Material}_{SequenceNum}
            e.g., Inlay_Forearm_01_Walnut_001, Inlay_Forearm_01_Holly_002
"""

import re
from typing import Tuple


class NamingConvention:
    """Static class enforcing naming schema for FreeCAD objects."""
    
    # Regex patterns for validation
    COMPONENT_PATTERN = r"^Cue_([A-Za-z]+)$"
    SEGMENT_PATTERN = r"^Cue_([A-Za-z]+)_Seg_(\d+)$"
    INLAY_PATTERN = r"^Inlay_([A-Za-z]+)_(\d+)_([A-Za-z]+)_(\d+)$"
    
    # Allowed component names
    VALID_COMPONENTS = {
        "Forearm",
        "Handle", 
        "ButtSleeve",
        "Rings",
    }
    
    # Allowed material names
    VALID_MATERIALS = {
        "Walnut",
        "Holly",
        "Maple",
        "Ash",
        "Ebony",
        "Pattern",
        "Custom",
    }
    
    # Required properties on inlay objects
    REQUIRED_INLAY_PROPS = {
        "InlayDepth",
        "FilletRadius",
        "ReliefType",
    }
    
    # Optional properties on inlay objects
    OPTIONAL_INLAY_PROPS = {
        "ReliefStepHeight",
        "GrainAngle",
    }
    
    @staticmethod
    def parse_segment_name(name: str) -> Tuple[str, int]:
        """
        Parse segment name into component and sequence.
        
        Args:
            name: Object name, e.g., 'Cue_Forearm_Seg_01'
        
        Returns:
            (component, sequence): e.g., ('Forearm', 1)
        
        Raises:
            ValueError: If name doesn't match pattern
        """
        match = re.match(NamingConvention.SEGMENT_PATTERN, name)
        if not match:
            raise ValueError(f"Invalid segment name format: '{name}'. Expected 'Cue_{{Component}}_Seg_{{NN}}'")
        component, seq_str = match.groups()
        return component, int(seq_str)
    
    @staticmethod
    def parse_inlay_name(name: str) -> Tuple[str, int, str, int]:
        """
        Parse inlay name into component, segment, material, and sequence.
        
        Args:
            name: Object name, e.g., 'Inlay_Forearm_01_Walnut_001'
        
        Returns:
            (component, segment_num, material, sequence): 
            e.g., ('Forearm', 1, 'Walnut', 1)
        
        Raises:
            ValueError: If name doesn't match pattern
        """
        match = re.match(NamingConvention.INLAY_PATTERN, name)
        if not match:
            raise ValueError(
                f"Invalid inlay name format: '{name}'. "
                "Expected 'Inlay_{{Component}}_{{NN}}_{{Material}}_{{NNN}}'"
            )
        component, seg_num_str, material, seq_str = match.groups()
        return component, int(seg_num_str), material, int(seq_str)
    
    @staticmethod
    def validate_component_name(component: str) -> bool:
        """Check if component name is in the allowed list."""
        return component in NamingConvention.VALID_COMPONENTS
    
    @staticmethod
    def validate_material_name(material: str) -> bool:
        """Check if material name is in the allowed list."""
        return material in NamingConvention.VALID_MATERIALS
    
    @staticmethod
    def generate_segment_name(component: str, sequence: int) -> str:
        """
        Generate a segment name from parts.
        
        Args:
            component: e.g., 'Forearm'
            sequence: e.g., 1
        
        Returns:
            Name like 'Cue_Forearm_Seg_01'
        """
        return f"Cue_{component}_Seg_{sequence:02d}"
    
    @staticmethod
    def generate_inlay_name(
        component: str,
        segment: int,
        material: str,
        sequence: int
    ) -> str:
        """
        Generate an inlay name from parts.
        
        Args:
            component: e.g., 'Forearm'
            segment: e.g., 1
            material: e.g., 'Walnut'
            sequence: e.g., 1
        
        Returns:
            Name like 'Inlay_Forearm_01_Walnut_001'
        """
        return f"Inlay_{component}_{segment:02d}_{material}_{sequence:03d}"
    
    @staticmethod
    def list_valid_components() -> list:
        """Return sorted list of valid component names."""
        return sorted(NamingConvention.VALID_COMPONENTS)
    
    @staticmethod
    def list_valid_materials() -> list:
        """Return sorted list of valid material names."""
        return sorted(NamingConvention.VALID_MATERIALS)
