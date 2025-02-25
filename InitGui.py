#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import FreeCAD
import FreeCADGui
from FreeCADGui import Workbench



class MyWorkbench (Workbench):

    MenuText = "Cues"
    ToolTip = "A description of my workbench"
    Icon = os.path.expanduser("~/Library/Application Support/FreeCAD/Mod/ButlerCues/resources/icons/bcwb.png")

    def Initialize(self):
        """This function is executed when the workbench is first activated.
        It is executed once in a FreeCAD session followed by the Activated function.
        """
        #import MyModuleA, MyModuleB # import here all the needed files that create your FreeCAD commands
        import materials
        import cues
        
        self.list = ["Make_Joint_Cap",
                     "Make_Joint_Ring_Pads",
                     "Make_Joint_Ring",
                     "Make_Forearm", 
                     "Make_Handle", 
                     "Make_Butt_sleeve",
                     "Make_Butt_Ring",
                     "Make_Butt_Ring_Pad",
                     "Make_Butt_Capp",
                     "Make_Full_Cue",
                     ]
        self.appendToolbar("Cue Commands", self.list) # creates a new toolbar with your commands
        self.appendMenu("Cues", self.list) # creates a new menu

        self.appendMenu("Materials", []) # creates a new menu
        for material in materials.materials():
            self.appendMenu("Materials", material['name'])

        self.appendMenu("Woods", []) # creates a new menu
        for wood in materials.get_wood_images():
            self.appendMenu("Woods", wood['name'])
        self.appendMenu("Woods", "Restore Wood")

        self.appendMenu("Inlays", []) # creates a new menu
        for i in ["forearm", "handle", "butt_sleeve"]:
            self.appendMenu("Inlays", f"{i}_inlay")

        return

    def Activated(self):
        """This function is executed whenever the workbench is activated"""
        import materials
        materials.restore_wood()
        return

    def Deactivated(self):
        """This function is executed whenever the workbench is deactivated"""
        return

    def ContextMenu(self, recipient):
        """This function is executed whenever the user right-clicks on screen"""
        # "recipient" will be either "view" or "tree"
        self.appendContextMenu("Materials", self.list) # add commands to the context menu

    def GetClassName(self): 
        # This function is mandatory if this is a full Python workbench
        # This is not a template, the returned string should be exactly "Gui::PythonWorkbench"
        return "Gui::PythonWorkbench"
       
FreeCADGui.addWorkbench(MyWorkbench())