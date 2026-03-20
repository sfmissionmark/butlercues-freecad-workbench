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

## CNC scripting quick start

Use the Python console in FreeCAD while this workbench is loaded.

```python
import inlays

# Default behavior (same as menu command):
# opens picker dialog for section + optional template
inlays.create_section_cnc_job()
```

### Scripted (non-interactive) job setup

```python
import inlays

# Create CAM job for a specific object by Name (no dialog)
inlays.create_section_cnc_job(
   target="forearm_pad",
   template_path="/absolute/path/to/job_wood_router.json",
   show_dialog=False,
)
```

### Batch example

```python
import FreeCAD as App
import inlays

doc = App.ActiveDocument
for obj_name in ["forearm_pad", "handle_pad", "butt_sleeve_pad"]:
   if doc.getObject(obj_name):
      inlays.create_section_cnc_job(target=obj_name, show_dialog=False)
```

## Compatibility

- FreeCAD 0.21+

## Releasing updates (for Addon Manager)

When you ship changes and want users to see updates in Addon Manager:

1. Bump the version in both files:
   - `package.xml` (`<version>...</version>`)
   - `metadata.txt` (`version=...`)
2. Commit and push to `main`.
3. Create a git tag and GitHub release (example: `v0.1.1`).
4. Wait for Addon Manager/index cache refresh, then users click **Update** in Addon Manager.

Example commands:

```bash
git add package.xml metadata.txt
git commit -m "Release v0.1.1"
git push
git tag v0.1.1
git push origin v0.1.1
```

## Repository

https://github.com/sfmissionmark/butlercues-freecad-workbench
