# FreeCAD.addImportType("My own format (*.own)", "importOwn")
# FreeCAD.addExportType("My own format (*.own)", "exportOwn")

import FreeCAD as App
import FreeCADGui as Gui
import os

class DocumentObserver:
    def slotActiveDocument(self, doc):
        """
        Called when the active document changes.

        Parameters:
            doc (str): The name of the newly activated document.
        """
        if doc:
            your_script(doc)

def your_script(active_document_name):
    """
    Custom script to run when a document is selected.

    Parameters:
        active_document_name (str): The name of the active document.
    """
    doc = App.getDocument(active_document_name)
    if not doc:
        return


observer = None


def install_document_observer():
    global observer
    if observer is None:
        observer = DocumentObserver()
        App.addDocumentObserver(observer)

# Keep observer installation opt-in. Set BUTLERCUES_ENABLE_OBSERVER=1 to enable.
if os.environ.get("BUTLERCUES_ENABLE_OBSERVER", "").lower() in {"1", "true", "yes", "on"}:
    install_document_observer()
