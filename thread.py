import FreeCAD
import Part

class PoolCueThread:
    def __init__(self):
        # Standard radial pin dimensions in inches
        self.major_diameter = 0.36856    # MJ00
        self.minor_diameter = 0.30638    # MN00
        self.pitch_diameter = 0.31918    # PD00
        self.pitch = 0.13158            # PI00
        self.lead_angle = 55.86         # LA00 in degrees
        self.root_angle = 55.37         # RA00 in degrees
        self.thread_roundness = 0.00154  # TR00
        self.oversize_tolerance = 0.00084 # OV00
        self.is_worn = False

    # Getters for all dimensions
    def get_major_diameter(self):
        return self.major_diameter
    
    def get_minor_diameter(self):
        return self.minor_diameter
    
    def get_pitch_diameter(self):
        return self.pitch_diameter
    
    def get_pitch(self):
        return self.pitch
    
    def get_lead_angle(self):
        return self.lead_angle
    
    def get_root_angle(self):
        return self.root_angle
    
    def get_thread_roundness(self):
        return self.thread_roundness
    
    def get_oversize_tolerance(self):
        return self.oversize_tolerance
    
    def get_worn_status(self):
        return self.is_worn

    def set_worn(self, worn):
        self.is_worn = worn

    def is_within_tolerance(self, measured_diameter):
        """Check if a measured diameter is within tolerance"""
        return abs(measured_diameter - self.major_diameter) <= self.oversize_tolerance

class HelicalDrawer:
    def __init__(self, pool_cue_thread):
        self.pool_cue_thread = pool_cue_thread

    def create_thread_profile(self):
        # Create a new sketch
        doc = FreeCAD.ActiveDocument
        sketch = doc.addObject('Sketcher::SketchObject', 'ThreadProfile')
        
        # Get thread dimensions
        major_radius = pool_cue_thread.get_major_diameter() / 2
        minor_radius = pool_cue_thread.get_minor_diameter() / 2
        pitch = pool_cue_thread.get_pitch()
        root_angle = pool_cue_thread.get_root_angle()
        
        # Create thread profile points
        # Starting from the top of the thread
        sketch.addGeometry(Part.LineSegment(
            FreeCAD.Vector(0, major_radius, 0),
            FreeCAD.Vector(pitch/2, minor_radius, 0)))
        
        sketch.addGeometry(Part.LineSegment(
            FreeCAD.Vector(pitch/2, minor_radius, 0),
            FreeCAD.Vector(pitch, major_radius, 0)))
        
        return sketch

    def draw_helical(self):
        # Create a new document if none exists
        doc = FreeCAD.newDocument("HelicalThread")
        
        # Create the thread profile
        profile = self.create_thread_profile()
        
        # Create a helix path
        major_diameter = self.pool_cue_thread.get_major_diameter()
        pitch = self.pool_cue_thread.get_pitch()
        helix = Part.makeHelix(pitch, 10, major_diameter / 2)
        
        # Add the helix to the document
        helix_feature = doc.addObject("Part::Feature", "Helix")
        helix_feature.Shape = helix
        
        # Recompute the document
        doc.recompute()

# Example usage
# pool_cue_thread = PoolCueThread()
# helical_drawer = HelicalDrawer(pool_cue_thread)
# helical_drawer.draw_helical()




# import FreeCAD
# import Part

# class PoolCueThread:
#     def __init__(self):
#         # Standard radial pin dimensions in inches
#         self.major_diameter = 0.36856    # MJ00
#         self.minor_diameter = 0.30638    # MN00
#         self.pitch_diameter = 0.31918    # PD00
#         self.pitch = 0.13158            # PI00
#         self.lead_angle = 55.86         # LA00 in degrees
#         self.root_angle = 55.37         # RA00 in degrees
#         self.thread_roundness = 0.00154  # TR00
#         self.oversize_tolerance = 0.00084 # OV00
#         self.is_worn = False

#     # Getters for all dimensions
#     def get_major_diameter(self):
#         return self.major_diameter
    
#     def get_minor_diameter(self):
#         return self.minor_diameter
    
#     def get_pitch_diameter(self):
#         return self.pitch_diameter
    
#     def get_pitch(self):
#         return self.pitch
    
#     def get_lead_angle(self):
#         return self.lead_angle
    
#     def get_root_angle(self):
#         return self.root_angle
    
#     def get_thread_roundness(self):
#         return self.thread_roundness
    
#     def get_oversize_tolerance(self):
#         return self.oversize_tolerance
    
#     def get_worn_status(self):
#         return self.is_worn

#     def set_worn(self, worn):
#         self.is_worn = worn

#     def is_within_tolerance(self, measured_diameter):
#         """Check if a measured diameter is within tolerance"""
#         return abs(measured_diameter - self.major_diameter) <= self.oversize_tolerance

# class HelicalDrawer:
#     def __init__(self, pool_cue_thread):
#         self.pool_cue_thread = pool_cue_thread

#     def draw_helical(self):
#         major_diameter = self.pool_cue_thread.get_major_diameter()
#         pitch = self.pool_cue_thread.get_pitch()
#         lead_angle = self.pool_cue_thread.get_lead_angle()
        
#         # Create a new document
#         doc = FreeCAD.newDocument("HelicalThread")
        
#         # Create a helix
#         helix = Part.makeHelix(pitch, 10, major_diameter / 2)
        
#         # Add the helix to the document
#         helix_feature = doc.addObject("Part::Feature", "Helix")
#         helix_feature.Shape = helix
        
#         # Recompute the document to apply changes
#         doc.recompute()

# # Example usage
# pool_cue_thread = PoolCueThread()
# helical_drawer = HelicalDrawer(pool_cue_thread)
# helical_drawer.draw_helical()

