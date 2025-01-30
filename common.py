import sys
import os
import dimensions

# Add FreeCAD libraries to path
FREECAD_LIB = '/Applications/FreeCAD.app/Contents/Resources/lib'  # Mac OS path
if os.path.exists(FREECAD_LIB):
    sys.path.append(FREECAD_LIB)


import FreeCAD
import FreeCADGui
# import PySide

import Part
import Sketcher


def setMaterial(anObject):
    anObject.ViewObject.ShapeMaterial.AmbientColor = (0.08799999952316284, 0.08799999952316284, 0.08799999952316284, 0.0)
    anObject.ViewObject.ShapeMaterial.DiffuseColor = (0.0, 0.0, 0.0, 0.0)
    anObject.ViewObject.ShapeMaterial.EmissiveColor = (0.0, 0.0, 0.0, 0.0)
    anObject.ViewObject.ShapeMaterial.Shininess = 1.0
    anObject.ViewObject.ShapeMaterial.SpecularColor = (1.0, 1.0, 1.0, 0.0)
    anObject.ViewObject.ShapeMaterial.Transparency = 0.0


def setViewStandard():
    FreeCADGui.activeDocument().activeView().viewRight()
    FreeCADGui.SendMsgToActiveView("ViewFit")


def getDocuments():
	openDocuments = FreeCAD.listDocuments()
	return list(openDocuments.keys())


def baselineCue():
	documentName = "Baseline_cue"
	if documentName in getDocuments():
		doc = FreeCAD.getDocument(documentName)
		return doc
	else:
		return FreeCAD.newDocument(documentName)


def inchToMM(num):
    print(num)
    if type(num) == str:
        num = float(num.split()[0])
    else:
        num = float(num)
    return round(num*25.4, 2)					


def drawLine(sketch, vecTuple1, vecTuple2):
    vector1 = FreeCAD.Vector(inchToMM(vecTuple1[0]), inchToMM(vecTuple1[1]), inchToMM(vecTuple1[2]))
    vector2 = FreeCAD.Vector(inchToMM(vecTuple2[0]), inchToMM(vecTuple2[1]), inchToMM(vecTuple2[2]))
    return sketch.addGeometry(Part.LineSegment(vector1 ,vector2), False)


def drawConstructionLine(sketch, vecTuple1, vecTuple2):
	linenumber = drawLine(sketch, vecTuple1, vecTuple2)
	sketch.toggleConstruction(linenumber)
	return linenumber


def drawSection(sketch=None, id=0, od=0, length=0, start_distance=0):
    id_radius = id/2
    od_radius = od/2
    length = (length + start_distance)
    drawLine(sketch, (id_radius,start_distance,0), (od_radius,start_distance,0))
    drawLine(sketch, (id_radius,length,0), (od_radius,length,0))
    drawLine(sketch, (id_radius,start_distance,0), (id_radius,length,0))
    drawLine(sketch, (od_radius,start_distance,0), (od_radius,length,0))


def drawSection2(sketch=None, id=0, od=0, length=0, start_distance=0, part_name=""):

    doc = FreeCAD.activeDocument()
    obj = doc.getObject("CueDimensions")
    length = getattr(obj, part_name + "_length")
    id_radius = getattr(obj, part_name + "_id")
    od_radius = getattr(obj, part_name + "_od")
    start_distance = inchToMM(start_distance)

    # Define vertices of the parallelogram
    p1 = FreeCAD.Vector( (id_radius), 0, 0)  # left-bottom
    p2 = FreeCAD.Vector( (od_radius), 0, 0)  # right-bottom
    p3 = FreeCAD.Vector( (od_radius), length, 0)  # right-top
    p4 = FreeCAD.Vector( (id_radius), length, 0)  # left-top

    # Add lines between the vertices
    l1 = sketch.addGeometry(Part.LineSegment(p1, p2))  # Bottom edge
    l2 = sketch.addGeometry(Part.LineSegment(p2, p3))  # Right edge
    l3 = sketch.addGeometry(Part.LineSegment(p3, p4))  # Top edge
    l4 = sketch.addGeometry(Part.LineSegment(p4, p1))  # Left edge

    # # Add constraints to ensure it's a parallelogram
    sketch.addConstraint(Sketcher.Constraint('Coincident', l1, 2, l2, 1))  # P2 of l1 == P1 of l2
    sketch.addConstraint(Sketcher.Constraint('Coincident', l2, 2, l3, 1))  # P2 of l2 == P1 of l3
    sketch.addConstraint(Sketcher.Constraint('Coincident', l3, 2, l4, 1))  # P2 of l3 == P1 of l4
    sketch.addConstraint(Sketcher.Constraint('Coincident', l4, 2, l1, 1))  # P2 of l4 == P1 of l1

    # # Add parallel constraints
    sketch.addConstraint(Sketcher.Constraint('Parallel', l1, l3))  # Bottom and Top edges parallel
    sketch.addConstraint(Sketcher.Constraint('Parallel', l2, l4))  # Left and Right edges parallel

    # # Fully define the geometry with dimensions (optional)
    # sketch.addConstraint(Sketcher.Constraint('DistanceX', l1, 50))  # Width of bottom edge
    # sketch.addConstraint(Sketcher.Constraint('DistanceY', l4, 30))  # Height of left edge

    # Recompute the document to apply changes
    FreeCAD.activeDocument().recompute()



def makeRevolution(body, section_name, sketch):
	doc = FreeCAD.activeDocument()
	revolution_name = 'Revolution_' + section_name
	body.newObject('PartDesign::Revolution',revolution_name)
	revolution = doc.getObject(revolution_name)	
	revolution.Profile = sketch
	revolution.Angle = 360.0
	revolution.Reversed = 1
	revolution.ReferenceAxis = (sketch, ['V_Axis'])
	revolution.Midplane = 0
	sketch.Visibility = False
	FreeCAD.activeDocument().recompute()
	return revolution


def make_cue_part(name, od, id, length, position):
    var_set()
    body_name = name + "_body"
    part_name = name + "_part"
    sketch_name = name + "_sketch"
    doc = FreeCAD.activeDocument()
    body = doc.getObject(body_name)
    part = doc.getObject(part_name)
    sketch = doc.getObject(sketch_name)
    if not part:
        part = doc.addObject('App::Part',part_name)
    part.Placement = FreeCAD.Placement(FreeCAD.Vector(0, position ,0),FreeCAD.Rotation(FreeCAD.Vector(0,0,1),0))
    if body:
        body_name = body_name + "1"
    body = part.newObject('PartDesign::Body', body_name)

    if sketch:
        sketch_name = sketch_name + "_cue"
    sketch = body.newObject('Sketcher::SketchObject', sketch_name)

    drawSection2(
         sketch=sketch, 
         id=id, 
         od=od,
         length=length, 
         part_name=name)
    makeRevolution(body, part_name, sketch)
    setViewStandard()
    return body


def var_set():
    doc = FreeCAD.activeDocument()
    var_set_name = "CueDimensions"

    dimensions_dict = {name: value for name, value in vars(dimensions).items() 
                      if not name.startswith('__')}

    var_set = doc.getObject(var_set_name)
    if not var_set:
        var_set = doc.addObject("App::VarSet", var_set_name)
        for section, values in dimensions_dict.items():
            for key, value in values.items():
                property_name = section + "_" + key
                var_set.addProperty("App::PropertyLength", property_name, group=section, doc="Some tooltip information")
                setattr(var_set, property_name, inchToMM(value))

    return var_set


def get_name_of_bodies():
    doc = FreeCAD.activeDocument()
    bodies = []
    for i in doc.findObjects(Type="PartDesign::Body"):
        bodies.append(i.Label)
    return bodies

def get_body_by_name(name):
    doc = FreeCAD.activeDocument()
    for i in doc.findObjects(Type="PartDesign::Body"):
        i.Label == name
        return i
    return None

def place_body_y(body):
     body.Placement.Base = FreeCAD.Vector(0, 100, 0)