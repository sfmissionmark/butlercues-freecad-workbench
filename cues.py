import FreeCAD
import FreeCADGui
from dimensions import *
import materials
import components
import inlays

class FC_CueCommand:
    def __init__(self, part_name, menu_text, tooltip, shortcut=None):
        self.part_name = part_name
        self.menu_text = menu_text
        self.tooltip = tooltip
        self.shortcut = shortcut

    def GetResources(self):
        resources = {
            "Pixmap": "",
            "MenuText": self.menu_text,
            "ToolTip": self.tooltip
        }
        if self.shortcut:
            resources["Accel"] = self.shortcut
        return resources

    def Activated(self):
        components.CueComponent(self.part_name)

    def IsActive(self):
        return True

# Define command configurations
CUE_COMMANDS = [
    ("Make_Joint_Cap", "joint_cap", "Joint Cap", "Create a new Joint Cap piece", "Shift+J"),
    ("Make_Joint_Ring_Pads", "joint_ring_pad", "Joint Ring Pads", "Create a new pad for joint ring", None),
    ("Make_Joint_Ring", "joint_ring", "Joint ring", "Create a new Joint ring", None),
    ("Make_Forearm", "forearm", "Forearm", "Create a new Forearm piece", "Shift+F"),
    ("Make_Handle", "handle", "Handle", "Create a new Handle piece", "Shift+H"),
    ("Make_Butt_sleeve", "butt_sleeve", "Butt sleeve", "Create a new Butt sleeve piece", "Shift+S"),
    ("Make_Butt_Ring", "butt_cap_ring", "Butt Ring", "Create a new Butt ring", None),
    ("Make_Butt_Ring_Pad", "butt_cap_pad", "Butt Ring Pad", "Create a new pad for joint ring", None),
    ("Make_Butt_Capp", "butt_cap", "Butt Capp", "Create a new Butt Capp", None),
]

# Register all commands
for command_name, part_name, menu_text, tooltip, shortcut in CUE_COMMANDS:
    FreeCADGui.addCommand(command_name, FC_CueCommand(part_name, menu_text, tooltip, shortcut))



class MaterialCommand:
    def __init__(self, color, menu_text):
        self.color = color
        self.menu_text = menu_text
    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": self.menu_text,
            "ToolTip": f"Set material to {self.menu_text}",
            # "Accel": f"Ctrl+Alt+{self.menu_text[0]}",
            "Accel": f"{self.menu_text[0]}",
        }
    def Activated(self):
        materials.setMaterial(self.color)
    def IsActive(self):
        return True

# Register material commands
material_list = materials.materials()
for material in material_list:
    color = material["rgb"]
    menu_text = material["name"]
    FreeCADGui.addCommand(menu_text, MaterialCommand(color, menu_text))


class WoodCommand:
    def __init__(self, color, menu_text):
        self.color = color
        self.menu_text = menu_text
    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": self.menu_text,
            "ToolTip": f"Set material to {self.color}"
        }
    def Activated(self):
        materials.set_wood(self.color)
    def IsActive(self):
        return True

# Register wood commands
wood_list = materials.get_wood_images()
for wood in wood_list:
    menu_text = wood["name"]
    FreeCADGui.addCommand(menu_text, WoodCommand(menu_text, menu_text))


class RestoreWoodCommand:
    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": "Restore Wood",
            "ToolTip": f"Restore wood from stored texture urls",
            "Accel": "Ctrl+Alt+R"
        }
    
    def Activated(self):
        materials.restore_wood()
    def IsActive(self):
        return True

FreeCADGui.addCommand("Restore Wood", RestoreWoodCommand())


# Full Cue command
class FC_FullCue:
    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": "Full Cue",
            "ToolTip": "Create a new Full Cue"
        }

    def Activated(self):
        full_cue_parts = [
            "joint_cap", "joint_ring_pad", "joint_ring", "joint_ring_pad",
            "forearm", "joint_ring_pad", "handle", "joint_ring_pad",
            "butt_sleeve", "butt_cap_pad", "butt_cap_ring", "butt_cap_pad",
            "butt_cap"
        ]
        for part_name in full_cue_parts:
            components.CueComponent(part_name)
            # part.cue_part(part_name)

        FreeCADGui.SendMsgToActiveView("ViewFit")
        
    def IsActive(self):
        return True

FreeCADGui.addCommand("Make_Full_Cue", FC_FullCue())


# Inlay commands
class FC_InlayCommand:
    def __init__(self, inlay_type):
        self.inlay_type = inlay_type

    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": f"Create {self.inlay_type} inlay",
            "ToolTip": f"Create a new {self.inlay_type} inlay"
        }
    

    def Activated(self):
        inlays.create_inlay_document(self.inlay_type)

    def IsActive(self):
        return True
    
# Register inlay commands
for inlay_type in ["handle", "forearm", "butt_sleeve"]:
    FreeCADGui.addCommand(f"{inlay_type}_inlay", FC_InlayCommand(inlay_type))



class FC_Inlayfix:
    def __init__(self, inlay_type):
        pass

    def GetResources(self):
            return {
                "Pixmap": "",
                "MenuText": f"Fillet for cnc",
                "ToolTip": f"Fillet selected object for cnc"
            }

    def Activated(self):
        inlays.fillet_for_cnc(self)

    def IsActive(self):
        return True

FreeCADGui.addCommand("Fillet for cnc", FC_Inlayfix("fillet_for_cnc"))
# FreeCADGui.addCommand("Job for inlay", FC_Inlayfix("job_for_inlay"))


class FC_CNCCommand:
    def __init__(self, inlay_type):
        pass

    def GetResources(self):
            return {
                "Pixmap": "",
                "MenuText": f"Job for inlay",
                "ToolTip": f"Create a new Job for inlay"
            }

    def Activated(self):
        inlays.create_cam_job()

    def IsActive(self):
        return True
    
FreeCADGui.addCommand("Job for inlay", FC_CNCCommand("job_for_inlay"))
