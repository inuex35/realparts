"""Operators for the Ask box and the bridge to an outside assistant."""
from __future__ import annotations

import os

import bpy

from ..link import state
from ..link.state import ADDON


class CADCORE_OT_ask(bpy.types.Operator):
    """Send the Ask box to Claude. It uses the same operations as the buttons,
    one undoable step each, and the viewport follows along."""

    bl_idname = "cadcore.ask"
    bl_label = "Ask"
    bl_description = "Ask Claude to make or change the part; it works through the kernel and you watch"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        props = getattr(context.scene, "cadcore", None)
        if props is None or props.assistant_busy:
            return False
        long = bpy.data.texts.get(ASK_TEXT)
        return bool(props.ask.strip()) or bool(long is not None and long.as_string().strip())

    # the sidebar's Ask needs a document; from the editor, so does this


    def execute(self, context):
        from ..link import assistant

        props = context.scene.cadcore
        question = props.ask.strip()
        long = bpy.data.texts.get(ASK_TEXT)
        in_editor = getattr(getattr(context, "space_data", None), "type", "") == 'TEXT_EDITOR'
        if long is not None and (not question or in_editor):
            question = long.as_string().strip() or question   # sent from the editor
        why = assistant.ask(context, question)
        if why:
            self.report({'ERROR'}, why)
            return {'CANCELLED'}
        props.assistant_busy = True
        return {'FINISHED'}


class CADCORE_OT_ask_dialog(bpy.types.Operator):
    """A wide box for a long question. A dialog field, not the Text Editor,
    because Blender's Text Editor takes no IME input (Japanese, Chinese, Korean)."""

    bl_idname = "cadcore.ask_dialog"
    bl_label = "Ask the Assistant"
    bl_description = "Write a longer question in a wide box and send it"
    bl_options = {'REGISTER'}

    question: bpy.props.StringProperty(name="Question", options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        props = getattr(context.scene, "cadcore", None)
        return props is not None and not props.assistant_busy

    def invoke(self, context, event):
        self.question = context.scene.cadcore.ask
        return context.window_manager.invoke_props_dialog(self, width=640, confirm_text="Send")

    def draw(self, context):
        from ..ui.panels import wrap

        layout = self.layout
        row = layout.row()
        row.scale_y = 1.6
        row.prop(self, "question", text="", placeholder="what to make or change, in your own words")
        if len(self.question) > 60:
            shown = layout.column(align=True)
            shown.enabled = False
            wrap(shown, self.question, context, icon='BLANK1')
        layout.prop(state.prefs(context), "assistant_stages", text="Step by step")
        note = layout.row()
        note.enabled = False
        note.label(text="Enter sends. The selection and the document go with it.", icon='INFO')

    def execute(self, context):
        props = context.scene.cadcore
        if not self.question.strip():
            return {'CANCELLED'}
        props.ask = self.question.strip()
        return bpy.ops.cadcore.ask()


ASK_TEXT = "Ask"                    # the text block a long question is written in


REPLY_TEXT = "Assistant"            # the text block the replies are shown in


def _screen_of(context):
    """The screen to lay the editor out on; a timer's context may have none."""
    screen = getattr(context, "screen", None)
    if screen is None and getattr(context, "window", None) is not None:
        screen = context.window.screen
    if screen is None:
        for window in context.window_manager.windows:
            if any(a.type == 'VIEW_3D' for a in window.screen.areas):
                return window.screen
        return context.window_manager.windows[0].screen if context.window_manager.windows else None
    return screen


class CADCORE_OT_ask_editor(bpy.types.Operator):
    """Write a long question in a Text Editor: many lines, wrapped, and your
    keyboard's own input method works there. It opens inside this window,
    under the 3D view, the way the Scripting workspace lays things out."""

    bl_idname = "cadcore.ask_editor"
    bl_label = "Write a Long Question"
    bl_description = ("Open a small Text Editor window for a long question. "
                      "Ask sends what is written there when the field is empty")

    def execute(self, context):
        text = bpy.data.texts.get(ASK_TEXT)
        if text is None:
            text = bpy.data.texts.new(ASK_TEXT)
            text.write(context.scene.cadcore.ask)
        area = self._area(context)
        if area is None:
            self.report({'ERROR'}, "no window to put the editor in")
            return {'CANCELLED'}
        area.type = 'TEXT_EDITOR'
        space = area.spaces.active
        space.text = text
        space.show_word_wrap = True
        space.show_line_numbers = False
        space.show_syntax_highlight = False
        try:
            text.cursor_set(0, character=0)     # show it from the top, not the last line
            space.top = 0
        except (AttributeError, TypeError):
            pass
        self._replies_beside(context, area)
        return {'FINISHED'}

    def _replies_beside(self, context, left):
        """The replies in an editor of their own, above the question, like a chat."""
        from ..link import assistant

        screen = _screen_of(context)
        if screen is None:
            return
        for area in screen.areas:
            if area.type == 'TEXT_EDITOR' and getattr(area.spaces.active, "text", None) is not None \
                    and area.spaces.active.text.name == REPLY_TEXT:
                return
        replies = bpy.data.texts.get(REPLY_TEXT) or bpy.data.texts.new(REPLY_TEXT)
        question = left.spaces.active.text
        before = set(a.as_pointer() for a in screen.areas)
        try:
            with context.temp_override(area=left, region=next(r for r in left.regions if r.type == 'WINDOW')):
                bpy.ops.screen.area_split(direction='HORIZONTAL', factor=0.4)
        except RuntimeError:
            return                       # no room to split: the question alone, then
        new = [a for a in screen.areas if a.as_pointer() not in before]
        if not new:
            return
        both = sorted(new + [left], key=lambda a: a.y)
        bottom, top = both[0], both[-1]
        for area, text in ((top, replies), (bottom, question)):
            area.type = 'TEXT_EDITOR'
            area.spaces.active.text = text
            area.spaces.active.show_word_wrap = True
            area.spaces.active.show_line_numbers = False
            area.spaces.active.show_syntax_highlight = False
        space = top.spaces.active
        space.show_word_wrap = True
        space.show_line_numbers = False
        space.show_syntax_highlight = False
        assistant.show(context)

    def _area(self, context):
        """An area for the editor, in this window: one already showing the
        Ask text, else the timeline (idle in CAD work), else a strip split
        off the bottom of the 3D view, else a new window."""
        screen = _screen_of(context)
        if screen is None:
            return None
        for area in screen.areas:
            if area.type == 'TEXT_EDITOR' and getattr(area.spaces.active, "text", None) is not None \
                    and area.spaces.active.text.name == ASK_TEXT:
                return area
        for area in screen.areas:
            # the timeline, if it is tall enough to write in
            if area.type == 'DOPESHEET_EDITOR' and area.spaces.active.mode == 'TIMELINE' \
                    and area.height >= 160:
                return area
        view = next((a for a in screen.areas if a.type == 'VIEW_3D'), None)
        if view is not None:
            before = set(a.as_pointer() for a in screen.areas)
            try:
                with context.temp_override(area=view, region=next(r for r in view.regions if r.type == 'WINDOW')):
                    bpy.ops.screen.area_split(direction='HORIZONTAL', factor=0.35)
            except RuntimeError:
                pass                     # a maximised view cannot be split: a window then
            new = [a for a in screen.areas if a.as_pointer() not in before]
            if new:
                # the lower of the two is the strip; the 3D view keeps the top
                return min(new + [view], key=lambda a: a.y)
        bpy.ops.wm.window_new()
        return context.window_manager.windows[-1].screen.areas[0]


class CADCORE_OT_ask_stop(bpy.types.Operator):
    bl_idname = "cadcore.ask_stop"
    bl_label = "Stop"
    bl_description = "Stop after the step that is running now"

    @classmethod
    def poll(cls, context):
        props = getattr(context.scene, "cadcore", None)
        return props is not None and props.assistant_busy

    def execute(self, context):
        from ..link import assistant

        assistant.stop()
        return {'FINISHED'}


class CADCORE_OT_ask_clear(bpy.types.Operator):
    bl_idname = "cadcore.ask_clear"
    bl_label = "Clear"
    bl_description = "Forget the conversation so far; the next question starts fresh"

    def execute(self, context):
        from ..link import assistant

        assistant.forget()
        context.scene.cadcore.ask = ""
        return {'FINISHED'}


class CADCORE_OT_assistant_login(bpy.types.Operator):
    bl_idname = "cadcore.assistant_login"
    bl_label = "Sign In"
    bl_description = "Open a terminal that signs in to Claude Code or Codex, so Ask can use your plan"
    bl_options = {'REGISTER'}

    kind: bpy.props.EnumProperty(items=(('claude', "Claude Code", ""), ('codex', "Codex", "")),
                                 default='claude')

    def execute(self, context):
        import shutil
        import subprocess
        import sys

        prefs = state.prefs(context)
        command = getattr(prefs, self.kind + "_command", self.kind) or self.kind
        if not shutil.which(command):
            self.report({'ERROR'}, "%s was not found: install %s first" % (
                command, "Claude Code" if self.kind == 'claude' else "Codex"))
            return {'CANCELLED'}
        line = "%s login" % command
        try:
            if sys.platform == "win32":
                subprocess.Popen(["cmd", "/c", "start", "Sign in", "cmd", "/k", line])
            elif sys.platform == "darwin":
                subprocess.Popen(["osascript", "-e", 'tell application "Terminal" to do script "%s"' % line])
            else:
                terminal = next((t for t in ("x-terminal-emulator", "gnome-terminal", "konsole", "xterm")
                                 if shutil.which(t)), None)
                if terminal is None:
                    self.report({'ERROR'}, "no terminal found: run `%s` yourself" % line)
                    return {'CANCELLED'}
                subprocess.Popen([terminal, "--", "sh", "-c", line + "; exec sh"] if terminal == "gnome-terminal"
                                 else [terminal, "-e", "sh -c '%s; exec sh'" % line])
        except OSError as exc:
            self.report({'ERROR'}, "could not open a terminal: %s" % exc)
            return {'CANCELLED'}
        context.scene.cadcore.status = "sign in in the terminal that opened, then Ask again"
        self.report({'INFO'}, context.scene.cadcore.status)
        return {'FINISHED'}


class CADCORE_OT_bridge(bpy.types.Operator):
    bl_idname = "cadcore.bridge"
    bl_label = "Let an Assistant Drive This"
    bl_description = ("Open a door on this machine so an assistant can work on "
                      "the document Blender is showing, and you watch it change")
    bl_options = {'REGISTER'}

    def execute(self, context):
        from ..link import bridge

        props = context.scene.cadcore
        if bridge.listening():
            bridge.stop()
            props.status = "the assistant's door is shut"
        else:
            try:
                where = bridge.start()
            except OSError as exc:
                self.report({'ERROR'}, "could not listen: %s" % exc)
                return {'CANCELLED'}
            props.status = "listening on %s -- run the kernel's MCP with --attach" % where
        props.status_is_error = False
        self.report({'INFO'}, props.status)
        return {'FINISHED'}


class CADCORE_OT_assistant_config(bpy.types.Operator):
    bl_idname = "cadcore.assistant_config"
    bl_label = "Config for an Assistant"
    bl_description = ("Copy the MCP server entry that lets Claude Code or Codex "
                      "drive this Blender -- the same interpreter and kernel the "
                      "add-on uses, so there is nothing else to install")
    bl_options = {'REGISTER'}

    client: bpy.props.EnumProperty(
        name="Assistant", default='claude',
        items=(('claude', "Claude Code", "the mcpServers block for .mcp.json "
                                          "(Claude Desktop reads the same)"),
               ('codex', "Codex", "the [mcp_servers.cadcore] table for "
                                  "~/.codex/config.toml")))

    def execute(self, context):
        from ..link import bridge
        from ..link.client import ASSISTANTS, assistant_command, assistant_config, kernel_for

        props = context.scene.cadcore
        if not bridge.listening():
            try:
                bridge.start()
            except OSError as exc:
                self.report({'ERROR'}, "could not listen: %s" % exc)
                return {'CANCELLED'}
        prefs = context.preferences.addons[ADDON].preferences
        repo = prefs.repo_path or state.repo_root()
        python, site = kernel_for(repo, prefs.python_path, state.kernel_home())
        spec = assistant_command(python, site, repo,
                                 "%s:%d" % (bridge.HOST, bridge.PORT),
                                 token_file=bridge.token_path())
        text = assistant_config(self.client, spec)
        context.window_manager.clipboard = text
        # also written to disk beside the kernel
        name, target = ASSISTANTS[self.client]
        where = os.path.join(state.kernel_home() or repo,
                             "assistant-%s%s" % (self.client, os.path.splitext(target)[1]))
        try:
            with open(where, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            where = "(clipboard only)"
        props.status = "config for %s copied -- paste into %s (also at %s)" % (
            name, target, where)
        props.status_is_error = False
        self.report({'INFO'}, props.status)
        return {'FINISHED'}
