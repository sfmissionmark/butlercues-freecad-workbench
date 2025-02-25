# FreeCAD.addImportType("My own format (*.own)", "importOwn")
# FreeCAD.addExportType("My own format (*.own)", "exportOwn")

import FreeCAD as App
import FreeCADGui as Gui

class DocumentObserver:
    def slotActiveDocument(self, doc):
        """
        Called when the active document changes.

        Parameters:
            doc (str): The name of the newly activated document.
        """
        if doc:
            print(f"Active document changed to: {doc}")
            # Run your script here
            your_script(doc)

def your_script(active_document_name):
    """
    Custom script to run when a document is selected.

    Parameters:
        active_document_name (str): The name of the active document.
    """
    print(f"Running script for active document: {active_document_name}")
    # Add your desired functionality here
    doc = App.getDocument(active_document_name)
    if doc:
        print(f"Document '{doc.Name}' contains {len(doc.Objects)} objects.")

# Attach the observer
observer = DocumentObserver()
App.addDocumentObserver(observer)

print("Document observer has been added.")
