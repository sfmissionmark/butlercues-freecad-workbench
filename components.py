"""
Cue Component Generation Module

This module handles the creation and organization of different elements
that collectively form a cue object. It serves as a structural foundation
for cue-based operations and management.

Functions:
    inchToMM(num): Converts inches to millimeters.

Classes:
    CueComponentManager: Manages cue components.
    CueComponent: Represents a cue component.

Note:
    This is a fundamental component of the cue system and should be
    handled with care to maintain proper cue functionality.
"""

# Import required modules
import FreeCAD as App
import FreeCADGui as Gui
from BOPTools import BOPFeatures

import dimensions
import materials


def inchToMM(num):
    try:
        if isinstance(num, str):
            num = float(num.split()[0])
        else:
            num = float(num)
        return round(num * 25.4, 2)
    except (ValueError, TypeError) as e:
        raise ValueError(f"Error converting '{num}' to millimeters: {e}")


class CueComponentManager:
    doc = App.activeDocument()
    _objects = []
    def __init__(self):
        self.doc = App.activeDocument() or App.newDocument("Cue")

        if not self.doc.getObject("CueDimensions"):
            var_set_name = "CueDimensions"
            dimensions_dict = dimensions.cue_dimensions()
            var_set = self.doc.getObject(var_set_name)
            if not var_set:
                var_set = self.doc.addObject("App::VarSet", var_set_name)
                for section, values in dimensions_dict.items():
                    for key, value in values.items():
                        if key not in ["wood_type", "material"]:
                            property_name = section + "_" + key
                            var_set.addProperty("App::PropertyLength", property_name, group=section, doc=f"{property_name} Length")
                            setattr(var_set, property_name, inchToMM(value))

        if not self.doc.getObject("CueComponents"):
            self.doc.addObject("App::DocumentObjectGroup", "CueComponents")


    def get_previous_freecad_object(self, obj):
        fc_components = self.get_components()
        previous_component = None
        for i in fc_components:
            if i.Name == obj.name:
                break
            previous_component = i

        return previous_component

    def get_components(self): #returns an ordered list of cue components
        return self.doc.getObject("CueComponents").Group

    def document(self):
        return self.doc


# Define Cue Component Class
class CueComponent:
    def __init__(self, name):
        self.part_name = name
        self.name = name
        self.manager = CueComponentManager()  # Get the singleton instance of the manager
        self.fc_doc = self.manager.document()
        self.container_group = self.fc_doc.getObject("CueComponents")
        self.draw_cone()

    def __repr__(self):
        dims = self.manager.doc.getObject("CueDimensions")
        length = getattr(dims, f"{self.name}_length", 0)
        return f"{self.name} (length: {length}mm)"

    def get_name(self):
        return self.name

    def reset_view(self):
        self.fc_doc.recompute()
        Gui.activeDocument().activeView().viewRight()
        Gui.SendMsgToActiveView("ViewFit")

    def draw_cone(self, headstock_radius=10, tailstock_radius=20, length=30):
        i = 1
        temp_name = self.name
        while self.fc_doc.getObject(temp_name):
            temp_name = f"{self.name}_{i}"
            i += 1
        self.name = temp_name
        part = self.fc_doc.addObject("Part::Cone", self.name)
        part.Label = ' '.join(self.name.split('_')).title()
        self.container_group.addObject(part)

        expression = f'CueDimensions.{self.part_name}_'
        part.setExpression('Radius1', f'{expression}od / 2')
        part.setExpression('Radius2', f'{expression}endod /2')
        part.setExpression('Height', f'{expression}length')

        part.Placement = App.Placement(App.Vector(0,0,0),App.Rotation(App.Vector(1,0,0),-90))
        previous_component = self.manager.get_previous_freecad_object(self)
        if previous_component:
            part.MapReversed = False
            part.AttachmentSupport = [(previous_component, 'Edge1')]
            part.MapPathParameter = 0
            part.MapMode = 'Concentric'
            part.recompute()

        start_expression ="(CueDimensions.finish_size_startod + ((CueDimensions.finish_size_endod - CueDimensions.finish_size_startod) / CueDimensions.finish_size_length) * Placement.Base.y)/2"
        end_expression ="(CueDimensions.finish_size_startod + ((CueDimensions.finish_size_endod - CueDimensions.finish_size_startod) / CueDimensions.finish_size_length) * (Placement.Base.y + Height))/2"
        part.setExpression('Radius1', start_expression)
        part.setExpression('Radius2', end_expression)

        self.reset_view()
        if 'wood_type' in dimensions.cue_dimensions()[self.part_name]:
            fc_part = [self.fc_doc.getObject(self.name)]
            wood = dimensions.cue_dimensions()[self.part_name]['wood_type']
            materials.set_wood(image_name=wood, optional_parts=fc_part)
        if 'material' in dimensions.cue_dimensions()[self.part_name]:
            fc_part = [self.fc_doc.getObject(self.name)]
            material = dimensions.cue_dimensions()[self.part_name]['material']
            if material == "black":
                materials.setMaterial(color = (0.0, 0.0, 0.0), optional_parts=fc_part)
            else:
                materials.setMaterial(color = (1.0, 1.0, 1.0), optional_parts=fc_part)
