# ButlerCues FreeCAD Workbench

ButlerCues is a custom FreeCAD workbench for modeling pool cue components, applying materials/wood textures, and creating inlay geometry.

## Install (manual)

1. Close FreeCAD.
2. Clone this repository into your FreeCAD Mod directory:
   - macOS: `~/Library/Application Support/FreeCAD/Mod/`
   - Linux: `~/.local/share/FreeCAD/Mod/`
   - Windows: `%APPDATA%/FreeCAD/Mod/`
3. Ensure the folder name is `ButlerCues` (or keep repository folder name if cloning directly).
4. Start FreeCAD and select the **Cues** workbench.

## Development notes

- Main entry points: `Init.py` and `InitGui.py`
- Commands are registered from `cues.py`
- Geometry helpers are in `components.py` and `inlays.py`
- Wood/material handling is in `materials.py`

## Compatibility

- FreeCAD 0.21+

## Repository

https://github.com/sfmissionmark/butlercues-freecad-workbench
