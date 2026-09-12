"""The shared shape of every operator that turns a selection into a CAD edit.

Collect the selection, refuse it if it is the wrong size, call one kernel
operation, apply the rebuild, write a status line. A subclass supplies
``operation``, ``wants`` (how many picks) and ``arguments(props, picked)``.
"""
from __future__ import annotations

from ..link import sync
from ..link.client import ServerError
from ..link.state import push_undo, _apply_build, get_client, report_error


def _least(wants) -> int | None:
    """The minimum in a ``"2+"`` spelling, else None."""
    if isinstance(wants, str) and wants.endswith("+") and wants[:-1].isdigit():
        return int(wants[:-1])
    return None


def enough(wants, have: int) -> bool:
    """Whether `have` picked items satisfy `wants`."""
    if wants is None:
        return True
    if wants == "any":
        return have > 0
    least = _least(wants)
    return have >= least if least is not None else have == wants


def needed(wants, picks: str) -> str:
    """The text for an unsatisfied `wants`; used for both the panel label and the refusal."""
    one, many = picks[:-1], picks
    if wants == "any":
        return "select an %s" % one if one[0] in "aeiou" else "select a %s" % one
    least = _least(wants)
    if least is not None:
        return "select %d or more %s" % (least, many)
    return "select %d %s" % (wants, many if wants != 1 else one)


def edit_dialog(self, context, event):
    """Show settings before committing a face edit."""
    self.seed(context)
    if hasattr(self, "advanced"):
        self.advanced = any(getattr(self, name) != self.properties.bl_rna.properties[name].default
                            for name in self.settings if name not in self.primary_settings)
    return context.window_manager.invoke_props_dialog(self, width=360)


def extra_settings(self, layout):
    """Keep the remaining operator settings reachable in its dialog."""
    layout.prop(self, "advanced")
    if self.advanced:
        for name in self.settings:
            if name not in self.primary_settings:
                layout.prop(self, name)


class EditFromSelection:
    """Mixin for an operator that edits the document from what is picked.

    ``wants`` is how many items the operation needs: a number for exactly
    that many, ``"2+"`` for that many or more, ``"any"`` for one or more,
    ``None`` for an operation that reads the selection but does not need one.
    The panel label and the refusal are both written from it.
    """

    operation: str = ""
    wants = None
    picks: str = "faces"                    # or "edges"
    #: operator properties seeded from the same-named sidebar properties when
    #: not set; declaring them on the operator is what makes F9 show them
    settings: tuple = ()

    def seed(self, context) -> None:
        """Fill any setting not explicitly given from the sidebar."""
        props = context.scene.cadcore
        for name in self.settings:
            if not self.properties.is_property_set(name):
                setattr(self, name, getattr(props, name))

    # -- what a subclass fills in -------------------------------------------
    def arguments(self, props, picked: list) -> dict:
        """The keyword arguments for the kernel operation."""
        return {}

    def summarise(self, info: dict, picked: list, props) -> str:
        """The status line shown in the panel afterwards."""
        return "%s on %s" % (info.get("feature", self.operation),
                             ", ".join(picked) or "the body")

    # -- the part that is always the same -----------------------------------
    def execute(self, context):
        # Run in object mode and restore the mode afterwards. In edit mode the
        # geometry lives in a BMesh: the `cad_face` attribute reads empty and
        # the mesh cannot be rewritten, neither of which is a ServerError.
        with sync.out_of_edit_mode(sync.editing()):
            return self._perform(context)

    def _perform(self, context):
        props = context.scene.cadcore
        self.seed(context)
        try:
            picked = self.selection(context)
        except ServerError as exc:
            return report_error(self, exc)
        if not self.enough(picked):
            self.report({'ERROR'}, "empty_selection: %s (got %d)"
                        % (self.needed(), len(picked)))
            return {'CANCELLED'}
        try:
            info = get_client(context).call(self.operation,
                                            **self.arguments(props, picked))
            _apply_build(context, info)
        except ServerError as exc:
            return report_error(self, exc)
        props.status = self.summarise(info, picked, props)
        push_undo(self.operation, self)
        self.report({'INFO'}, props.status)
        return {'FINISHED'}

    # -- selection ----------------------------------------------------------
    def selection(self, context) -> list:
        if self.picks == "edges":
            return sync.selected_edge_names(get_client(context))
        return sync.selected_face_names()

    def enough(self, picked: list) -> bool:
        return enough(self.wants, len(picked))

    def needed(self) -> str:
        return needed(self.wants, self.picks)
