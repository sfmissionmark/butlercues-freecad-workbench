#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
material.py
FreeCAD workbench module for ButlerCues
"""

__title__ = "part"
__author__ = "Mark Butler"
__version__ = "0.1.0"
__date__ = "2025"

import FreeCAD as App
import Part
import Sketcher
import common
import FreeCADGui as Gui
import dimensions
from pivy import coin
import os


def parts_to_color():
    selection = Gui.Selection.getSelection()
    parts_to_color = []
    for anObject in selection:
        if anObject.TypeId == "PartDesign::Body":
            parts_to_color.append(anObject)
        elif anObject.TypeId == "Part::Feature":
            if anObject.Shape:
                parts_to_color.append(anObject.Shape)
        elif anObject.TypeId == "App::Part":
            bodies = [obj for obj in anObject.Group if obj.TypeId == "PartDesign::Body"]
            for body in bodies:
                parts_to_color.append(body)
        elif "Revolution" in anObject.Name:
            name = anObject.Name.split("Revolution_")[-1]
            parts_to_color.append(App.ActiveDocument.getObject(name))    
        else:
            #meh try anyway
            parts_to_color.append(anObject)

    return parts_to_color




def setMaterial(color = (0.0, 0.0, 0.0), optional_parts=None):
    if optional_parts is None:
        optional_parts = parts_to_color()
    for obj in optional_parts:
        remove_texture(obj)
        view = obj.ViewObject
        factor = 0.8
        line_width = 2
        view.ShapeColor = color
        view.LineColor = tuple(max(0.0, min(1.0, c * factor)) for c in color)
        view.LineWidth = line_width

    Gui.Selection.clearSelection()



def materials():
    return [
        {"name": "Black", "color": "black", "rgb": (0.0, 0.0, 0.0)},
        {"name": "White", "color": "white", "rgb": (1.0, 1.0, 1.0)}, 
        {"name": "Gold", "color": "gold", "rgb": (0.83, 0.68, 0.21)},
        {"name": "Silver", "color": "silver", "rgb": (0.5, 0.5, 0.5)},
        {"name": "Brass", "color": "brass", "rgb": (0.71, 0.55, 0.35)},
        {"name": "Aluminum", "color": "aluminum", "rgb": (0.75, 0.75, 0.75)},
    ]
    



def getChildPosition(iRootnode):    
    pos = 0
    for c in iRootnode.getChildren():
        if str(c).find("SoSwitch") != -1:
            return pos
            break
        pos = pos + 1
    return -1



def get_wood_images():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    wood_images_dir = os.path.join(script_dir, 'wood_images')
    images = []
    if os.path.exists(wood_images_dir):
        for file in os.listdir(wood_images_dir):
            if file.lower().endswith(('.png', '.jpg', '.jpeg')):
                images.append({
                    "name": os.path.splitext(file)[0],
                    "path": os.path.join(wood_images_dir, file)
                })
    return images


def restore_wood():
    doc = App.ActiveDocument
    if not doc: return
    matching_objects = []
    property_name = "Texture_URL"
    property_type = "App::PropertyString"
    for obj in doc.Objects:
        # Check if the property exists in the object
        if property_name in obj.PropertiesList:
            # Check if the property's type matches
            if obj.getTypeIdOfProperty(property_name) == property_type:
                matching_objects.append(obj)

    print(f"Found {len(matching_objects)} objects with {property_name} property.")
    print(f"Restoring wood textures.")

    for a_file in matching_objects:
        wood_name = os.path.basename(a_file.Texture_URL).split(".")[0]
        set_wood(wood_name, [a_file])


def set_wood(image_name="Amboyna", optional_parts=None):
    if optional_parts is None:
        optional_parts = parts_to_color()
    for iObj in optional_parts:
        remove_texture(iObj)
        rootnode = iObj.ViewObject.RootNode
        
        material = coin.SoMaterial()
        material.shininess = 0  # Adjust value between 0.0 (dull) and 1.0 (shiny)
        rootnode.insertChild(material, 2)

        texture = coin.SoTexture2()
        texture.model = coin.SoTexture2.REPLACE
        texture.enableCompressedTexture = True
        texture.wrapS = coin.SoTexture2.CLAMP
        texture.wrapT = coin.SoTexture2.CLAMP
        rootnode.insertChild(texture, 2)

        transform = coin.SoTexture2Transform()
        transform.scaleFactor.setValue(1, 1)  # Adjust scale factors as needed
        rootnode.insertChild(transform, 2)


        SoPickStyle = coin.SoPickStyle()

        for image in get_wood_images():
            if image["name"] == image_name:
                texture.filename = image["path"]
                if not hasattr(iObj, "Texture_URL"):
                    info = "Texture URL or HDD local path."
                    iObj.addProperty("App::PropertyString", "Texture_URL", "Texture", info)
                iObj.Texture_URL = image["path"]
                break

        coordinate = coin.SoTextureCoordinateEnvironment()
        # coordinate = coin.SoTextureCoordinateObject()
        coordinate.directionS = (0.1, 0.1, 0.1)
        coordinate.directionT = (0.1, 0.1, 0.1)
        coordinate.directionR = (0.1, 0.1, 0.1)
        coordinate.point = (0.1, 0.1, 0.1)
        # coordinate.model = coin.SoTextureCoordinateObject.CYLINDRICAL
        # coordinate.mapping = coin.SoTextureCoordinateObject.SPHERE
        # coordinate.function = coin.SoTextureCoordinateObject.REFLECTION
        # coordinate.filename = iObj.Texture_URL
        coordinate.wrapS = coin.SoTexture2.CLAMP
        coordinate.wrapT = coin.SoTexture2.CLAMP
        coordinate.wrapR = coin.SoTexture2.CLAMP
        pos = 0
        for c in rootnode.getChildren():
            if str(c).find("SoSwitch") != -1:
                break
            pos = pos + 1
        rootnode.insertChild(coordinate, pos)
    Gui.Selection.clearSelection()


def remove_texture(obj):
    rootnode = obj.ViewObject.RootNode
    # Remove texture-related nodes
    children_to_remove = []
    for i, child in enumerate(rootnode.getChildren()):
        if any(node_type in str(child) for node_type in ["SoTexture2", "SoTextureCoordinate", "SoTexture2Transform"]):
            children_to_remove.append(i)
    
    # Remove nodes in reverse order to maintain correct indices
    for index in sorted(children_to_remove, reverse=True):
        rootnode.removeChild(index)
        
    # Remove Texture_URL property if it exists
    if hasattr(obj, "Texture_URL"):
        obj.removeProperty("Texture_URL")




print("hello")