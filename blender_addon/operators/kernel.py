"""Installing the kernel's packages into Blender's Python."""
from __future__ import annotations

import os

import bpy

from ..link import state
from ..link.client import KERNEL_PYTHONS, blender_python, kernel_prefix
from ..link.state import ADDON


class CADCORE_OT_install_kernel(bpy.types.Operator):
    bl_idname = "cadcore.install_kernel"
    bl_label = "Install the CAD kernel"
    bl_description = ("Download the solid modelling, sketch solving and study "
                      "libraries for Blender's own Python (about 160 MB, once). "
                      "No separate Python is needed")

    _process = None
    _timer = None
    _lines: list = []

    #: which requirements file to install: the kernel alone, or with the FEM
    #: studies, which are most of the download
    which: bpy.props.EnumProperty(
        name="Install", default='studies',
        items=(('studies', "Kernel + studies (about 160 MB)",
                "modelling, drawings, sheet metal, export, and simulate / "
                "optimize"),
               ('kernel', "Kernel only (about 85 MB)",
                "everything except simulate and optimize; can be added later")))

    def execute(self, context):
        return self._start(context)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def _start(self, context):
        import subprocess
        import threading

        prefs = context.preferences.addons[ADDON].preferences
        repo = prefs.repo_path or state.repo_root()
        requirements = os.path.join(
            repo, "requirements-sim.txt" if self.which == 'studies'
            else "requirements.txt")
        if not os.path.exists(requirements):
            self.report({'ERROR'},
                        "no %s in %s -- set the repository path"
                        % (os.path.basename(requirements), repo))
            return {'CANCELLED'}
        python = blender_python()
        if python is None:
            # only a source build with an unusual interpreter lands here
            self.report({'ERROR'},
                        "this Blender's Python is not one the kernel's wheels "
                        "exist for (%s); build a .venv beside the repository "
                        "by hand instead" % " or ".join(KERNEL_PYTHONS))
            return {'CANCELLED'}

        prefix = kernel_prefix(repo, state.kernel_home())
        # A prefix of our own, since Blender's site-packages may be read-only
        # (`--prefix`, not `--target`: see `kernel_prefix`). `--ignore-installed`
        # keeps Blender's bundled numpy from standing in for the pinned one.
        script = (
            "import subprocess, sys\n"
            "try:\n"
            "    import pip\n"
            "except ImportError:\n"
            "    subprocess.check_call([sys.executable, '-m', 'ensurepip', "
            "'--user'])\n"
            "subprocess.check_call([sys.executable, '-m', 'pip', 'install', "
            "'--upgrade', '--ignore-installed', '--prefix', %r, '-r', %r])\n"
            % (prefix, requirements))
        self._process = subprocess.Popen(
            [python, "-c", script], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace", bufsize=1)
        # pip's output is read on a thread so the modal never blocks on the
        # pipe; the status line shows pip's last line
        self._lines = []
        stdout = self._process.stdout

        def drain():
            for line in stdout:
                line = line.strip()
                if line:
                    self._lines.append(line)
                    print("[kernel install]", line)
        threading.Thread(target=drain, daemon=True).start()
        self._site = prefix
        context.scene.cadcore.status = "installing the kernel into %s" % prefix
        self._timer = context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        code = self._process.poll()
        if code is None:
            if self._lines:
                context.scene.cadcore.status = "installing: " + self._lines[-1][:90]
            return {'RUNNING_MODAL'}
        context.window_manager.event_timer_remove(self._timer)
        prefs = context.preferences.addons[ADDON].preferences
        if code == 0:
            # blank means Blender's own Python; a written path would pin the
            # preference to this Blender's binary
            prefs.python_path = ""
            state.stop_client()
            context.scene.cadcore.status = "kernel installed: %s" % self._site
            self.report({'INFO'}, context.scene.cadcore.status)
            if not context.scene.cadcore.has_document:
                bpy.ops.cadcore.new_document()      # straight on to Start, no second step
            return {'FINISHED'}
        last = next((l for l in reversed(self._lines) if "error" in l.lower()),
                    self._lines[-1] if self._lines else "see the console")
        context.scene.cadcore.status = "kernel install failed: " + last[:90]
        self.report({'ERROR'}, context.scene.cadcore.status)
        return {'CANCELLED'}
