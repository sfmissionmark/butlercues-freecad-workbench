
import FreeCAD
import FreeCADGui
import materials
import components
import inlays
import traceback
import os
import sys
import Path
from Path.Post.Processor import PostProcessorFactory
# Ensure tiling command is registered
try:
    import tiling
except ImportError:
    tiling = None

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


class UpdateInlaysCommand:
    def GetResources(self):
        return {
            "Pixmap": "",
            "MenuText": "Update Inlays",
            "ToolTip": "Refresh linked inlays from source inlay documents"
        }

    def Activated(self):
        inlays.update_all_previews()

    def IsActive(self):
        return True


FreeCADGui.addCommand("Update Inlays", UpdateInlaysCommand())


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
                "MenuText": f"Fillet for Inlay",
                "ToolTip": f"Fillet selected object for inlay"
            }

    def Activated(self):
        inlays.fillet_for_cnc(show_dialog=True)

    def IsActive(self):
        return True

fillet_command = FC_Inlayfix("fillet_for_cnc")
FreeCADGui.addCommand("Fillet for Inlay", fillet_command)
FreeCADGui.addCommand("Fillet for cnc", fillet_command)
# FreeCADGui.addCommand("Job for inlay", FC_Inlayfix("job_for_inlay"))


class FC_CNCCommand:
    def __init__(self, inlay_type):
        pass

    def GetResources(self):
            return {
                "Pixmap": "",
                "MenuText": f"Pattern",
                "ToolTip": f"Create an inlay pattern"
            }

    def Activated(self):
        try:
            FreeCAD.Console.PrintMessage("[Cues] Pattern command activated\n")
            inlays.create_cam_job()
        except Exception:
            FreeCAD.Console.PrintError("[Cues] Pattern command failed\n")
            FreeCAD.Console.PrintError(traceback.format_exc())

    def IsActive(self):
        return True
    
pattern_command = FC_CNCCommand("job_for_inlay")
FreeCADGui.addCommand("Cues_Pattern", pattern_command)


class FC_SectionCAMJobCommand:
    def GetResources(self):
            return {
                "Pixmap": "",
                "MenuText": f"Inlay Job",
                "ToolTip": f"Create an Inlay Job using Profile operations and optional template"
            }

    def Activated(self):
        try:
            inlays.create_section_cnc_job()
        except Exception:
            FreeCAD.Console.PrintError("[Cues] Inlay Job failed\n")
            FreeCAD.Console.PrintError(traceback.format_exc())

    def IsActive(self):
        return True


FreeCADGui.addCommand("Cues_Section_CAM_Job", FC_SectionCAMJobCommand())


class FC_XUpNestingCommand:
    def GetResources(self):
            return {
                "Pixmap": "",
                "MenuText": f"X-up Nesting",
                "ToolTip": f"Create an editable X-up nesting layout only (no CAM job)"
            }

    def Activated(self):
        try:
            inlays.create_xup_nesting_layout()
        except Exception:
            FreeCAD.Console.PrintError("[Cues] X-up Nesting failed\n")
            FreeCAD.Console.PrintError(traceback.format_exc())

    def IsActive(self):
        return True


FreeCADGui.addCommand("Cues_XUp_Nesting", FC_XUpNestingCommand())


class FC_PocketCAMJobCommand:
    def GetResources(self):
            return {
                "Pixmap": "",
                "MenuText": f"Pocket Job",
                "ToolTip": f"Create a CAM Pocket Job for a selected solid using an optional template"
            }

    def Activated(self):
        try:
            inlays.create_pocket_cnc_job()
        except Exception:
            FreeCAD.Console.PrintError("[Cues] Pocket Job failed\n")
            FreeCAD.Console.PrintError(traceback.format_exc())

    def IsActive(self):
        return True


FreeCADGui.addCommand("Cues_Pocket_CAM_Job", FC_PocketCAMJobCommand())


class FC_ExportGroupCAMJobsCommand:
    def GetResources(self):
            return {
                "Pixmap": "",
                "MenuText": "Export CAM Jobs in Selected Group",
                "ToolTip": "Post-process all CAM jobs found recursively under the selected group"
            }

    def _is_cam_job(self, obj):
        if not obj:
            return False
        try:
            if str(getattr(obj, "TypeId", "")) == "Path::FeatureCompoundPython":
                if hasattr(obj, "Operations") and hasattr(obj, "Model") and hasattr(obj, "Stock"):
                    return True
        except Exception:
            pass
        try:
            return hasattr(obj, "Operations") and hasattr(obj, "Model") and hasattr(obj, "Stock")
        except Exception:
            return False

    def _collect_jobs_recursive(self, root_obj):
        jobs = []
        seen = set()

        def _walk(obj, active_group_label=None):
            if not obj:
                return
            name = str(getattr(obj, "Name", "") or "")
            if name and name in seen:
                return
            if name:
                seen.add(name)

            current_group_label = active_group_label
            try:
                if str(getattr(obj, "TypeId", "")) == "App::DocumentObjectGroup":
                    current_group_label = str(getattr(obj, "Label", getattr(obj, "Name", "Group")) or "Group")
            except Exception:
                pass

            if self._is_cam_job(obj):
                jobs.append((obj, current_group_label))

            child_lists = []
            try:
                child_lists.append(list(getattr(obj, "Group", []) or []))
            except Exception:
                pass
            try:
                child_lists.append(list(getattr(obj, "OutList", []) or []))
            except Exception:
                pass

            for child_list in child_lists:
                for child in child_list:
                    _walk(child, current_group_label)

        root_group_label = str(getattr(root_obj, "Label", getattr(root_obj, "Name", "Group")) or "Group")
        _walk(root_obj, root_group_label)
        return jobs

    def _safe_file_token(self, text):
        token = "".join(ch if str(ch).isalnum() or str(ch) in ("-", "_") else "_" for ch in str(text or ""))
        token = token.strip("_")
        return token or "cam_group"

    def _default_output_for_group(self, job, group_label):
        current_path = str(getattr(job, "PostProcessorOutputFile", "") or "").strip()
        base_dir = ""
        ext = ".nc"

        if current_path:
            try:
                base_dir = os.path.dirname(current_path)
            except Exception:
                base_dir = ""
            try:
                _, current_ext = os.path.splitext(current_path)
                if current_ext:
                    ext = current_ext
            except Exception:
                pass

        group_token = self._safe_file_token(group_label)
        file_name = f"{group_token}{ext}"
        if base_dir:
            return os.path.join(base_dir, file_name)
        return file_name

    def _resolve_post_name_for_job(self, job):
        try:
            machine_name = str(getattr(job, "Machine", "") or "").strip()
        except Exception:
            machine_name = ""

        if machine_name:
            try:
                from Machine.models.machine import MachineFactory

                machine = MachineFactory.get_machine(machine_name)
                post_name = str(getattr(machine, "postprocessor_file_name", "") or "").strip()
                if post_name:
                    return post_name, True
            except Exception:
                pass

        try:
            post_name = str(getattr(job, "PostProcessor", "") or "").strip()
        except Exception:
            post_name = ""
        if not post_name:
            try:
                post_name = str(Path.Preferences.defaultPostProcessor() or "").strip()
            except Exception:
                post_name = ""
        return post_name, False

    def _ensure_job_machine_for_export(self, job):
        try:
            current_machine = str(getattr(job, "Machine", "") or "").strip()
        except Exception:
            current_machine = ""
        if current_machine:
            return current_machine

        try:
            from Machine.models.machine import MachineFactory
        except Exception:
            return ""

        def _pick_machine_name(names):
            normalized = [str(name or "").strip() for name in (names or []) if str(name or "").strip()]
            normalized = [name for name in normalized if name != "<any>"]
            if not normalized:
                return ""

            preferred_tokens = ("xyzb", "xyzac", "xyzbc", "xyza", "4axis", "4-axis", "rotary", "b")
            for token in preferred_tokens:
                for name in normalized:
                    low = name.lower().replace(" ", "")
                    if token in low:
                        return name
            return normalized[0]

        machine_name = ""
        try:
            machine_name = _pick_machine_name(MachineFactory.list_configurations())
        except Exception:
            machine_name = ""

        if not machine_name:
            try:
                MachineFactory.create_standard_configs()
                machine_name = _pick_machine_name(MachineFactory.list_configurations())
            except Exception:
                machine_name = ""

        if machine_name:
            try:
                job.Machine = machine_name
                FreeCAD.Console.PrintMessage(
                    f"[Cues] Auto-set machine '{machine_name}' for export on '{getattr(job, 'Label', getattr(job, 'Name', 'Job'))}'.\n"
                )
                return machine_name
            except Exception:
                return ""
        return ""

    def _file_extension_for_job(self, job, postprocessor, use_new_flow=False):
        try:
            output_path = str(getattr(job, "PostProcessorOutputFile", "") or "").strip()
        except Exception:
            output_path = ""
        if output_path:
            try:
                _, ext = os.path.splitext(output_path)
                if ext:
                    return ext
            except Exception:
                pass

        if use_new_flow:
            try:
                ext = str(postprocessor.get_file_extension() or "").strip()
                if ext:
                    if not ext.startswith("."):
                        ext = f".{ext}"
                    return ext
            except Exception:
                pass
        return ".nc"

    def _write_text_file(self, file_path, content):
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
        except Exception:
            pass
        with open(file_path, "w", encoding="utf-8", newline=None) as handle:
            handle.write(content)

    def _post_job_to_folder(self, job, group_label, output_dir, name_counter):
        self._ensure_job_machine_for_export(job)
        post_name, use_new_flow = self._resolve_post_name_for_job(job)
        if not post_name:
            raise RuntimeError("No post processor configured for job")

        original_args = ""
        try:
            original_args = str(getattr(job, "PostProcessorArgs", "") or "")
        except Exception:
            original_args = ""

        forced_args = original_args
        if "--no-show-editor" not in forced_args:
            forced_args = (forced_args + " --no-show-editor").strip()

        try:
            try:
                if hasattr(job, "PostProcessorArgs"):
                    job.PostProcessorArgs = forced_args
            except Exception:
                pass

            postprocessor = PostProcessorFactory.get_post_processor(job, post_name)
            if not postprocessor:
                raise RuntimeError(f"Post processor '{post_name}' unavailable")

            post_data = postprocessor.export2() if use_new_flow else postprocessor.export()
            if not post_data:
                raise RuntimeError("Post processor returned no output")
        finally:
            try:
                if hasattr(job, "PostProcessorArgs"):
                    job.PostProcessorArgs = original_args
            except Exception:
                pass

        ext = self._file_extension_for_job(job, postprocessor, use_new_flow=use_new_flow)
        base_token = self._safe_file_token(group_label)
        count = int(name_counter.get(base_token, 0)) + 1
        name_counter[base_token] = count
        if count > 1:
            base_token = f"{base_token}_{count}"

        written_files = []
        for idx, item in enumerate(post_data, start=1):
            if not isinstance(item, (tuple, list)) or len(item) < 2:
                continue
            subpart, gcode = item[0], item[1]
            if gcode is None:
                continue

            suffix = ""
            subpart_text = str(subpart or "").strip()
            if subpart_text and subpart_text.lower() != "allitems":
                suffix = f"_{self._safe_file_token(subpart_text)}"
            elif len(post_data) > 1:
                suffix = f"_{idx}"

            output_name = f"{base_token}{suffix}{ext}"
            output_path = os.path.join(output_dir, output_name)
            self._write_text_file(output_path, str(gcode))
            written_files.append(output_path)

        if not written_files:
            raise RuntimeError("Post processor produced no writable gcode sections")
        return written_files

    def _job_export_bucket(self, job):
        try:
            label = str(getattr(job, "Label", getattr(job, "Name", "")) or "").strip().lower()
        except Exception:
            label = ""
        if "pocket" in label:
            return "pocket"
        return "inlay"

    def Activated(self):
        try:
            selection = FreeCADGui.Selection.getSelection() or []
        except Exception:
            selection = []

        if not selection:
            FreeCAD.Console.PrintError("[Cues] Select an Inlays/CAM group first.\n")
            return

        jobs_by_name = {}
        selected_labels = []
        for root in selection:
            root_label = str(getattr(root, "Label", getattr(root, "Name", "selection")) or "selection")
            selected_labels.append(root_label)

            root_jobs = self._collect_jobs_recursive(root)
            if (not root_jobs) and self._is_cam_job(root):
                root_jobs = [(root, root_label)]

            for job, group_label in root_jobs:
                job_name = str(getattr(job, "Name", "") or "")
                if not job_name:
                    job_name = f"__anon_{id(job)}"
                if job_name not in jobs_by_name:
                    jobs_by_name[job_name] = (job, group_label)

        jobs = list(jobs_by_name.values())

        if not jobs:
            joined = ", ".join(selected_labels) if selected_labels else "selection"
            FreeCAD.Console.PrintError(f"[Cues] No CAM jobs found under selected items: {joined}.\n")
            return

        doc = FreeCAD.ActiveDocument
        doc_path = ""
        try:
            doc_path = str(getattr(doc, "FileName", "") or "").strip()
        except Exception:
            doc_path = ""
        if not doc_path:
            FreeCAD.Console.PrintError("[Cues] Save the document first so exports can go to <doc folder>/cam_export/<document name>.\n")
            return

        doc_folder = os.path.dirname(doc_path)
        doc_name = os.path.splitext(os.path.basename(doc_path))[0].strip()
        if not doc_name:
            doc_name = str(getattr(doc, "Name", "document") or "document").strip() or "document"

        output_dir = os.path.join(doc_folder, "cam_export", doc_name)
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as exc:
            FreeCAD.Console.PrintError(f"[Cues] Could not create export folder '{output_dir}': {exc}\n")
            return

        exported = 0
        failed = 0
        written_paths = []
        name_counter = {}
        for job, group_label in jobs:
            try:
                bucket = self._job_export_bucket(job)
                bucket_dir = os.path.join(output_dir, bucket)
                try:
                    os.makedirs(bucket_dir, exist_ok=True)
                except Exception:
                    pass
                files = self._post_job_to_folder(job, group_label, bucket_dir, name_counter)
                written_paths.extend(files)
                exported += 1
            except Exception as exc:
                failed += 1
                job_label = str(getattr(job, "Label", getattr(job, "Name", "Job")) or "Job")
                FreeCAD.Console.PrintError(f"[Cues] Failed to export '{job_label}': {exc}\n")

        root_label = ", ".join(selected_labels) if selected_labels else "selection"
        FreeCAD.Console.PrintMessage(
            f"[Cues] Exported {exported} CAM job(s) from '{root_label}' to '{output_dir}'"
            + (f" ({failed} failed).\n" if failed else ".\n")
        )
        if written_paths:
            FreeCAD.Console.PrintMessage("[Cues] Wrote:\n")
            for path in written_paths:
                FreeCAD.Console.PrintMessage(f"  - {path}\n")
            # Reveal the first exported file in Finder (macOS only)
            try:
                if os.name == "posix" and sys.platform == "darwin":
                    import subprocess
                    subprocess.Popen(["open", "-R", written_paths[0]])
            except Exception as exc:
                FreeCAD.Console.PrintError(f"[Cues] Could not reveal file in Finder: {exc}\n")

    def IsActive(self):
        try:
            return bool(FreeCAD.ActiveDocument)
        except Exception:
            return True


FreeCADGui.addCommand("Cues_Export_Group_CAM_Jobs", FC_ExportGroupCAMJobsCommand())
