"""Draw the sidebar as text, so its structure can be looked at without a window.

    blender -b -noaudio --factory-startup -P tools/render/show_panel.py -- [doc]

The layout calls are recorded against a stand-in and printed as a tree, in
two states side by side: nothing picked, and two faces picked.
"""
import os
import sys

import bpy

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DOC = argv[0] if argv else os.path.join(REPO, "examples", "bracket.json")
WIDE = 46


class Cell:
    """One drawn thing: a label, a button, a field."""

    def __init__(self, text: str, icon: str = "", enabled: bool = True,
                 alert: bool = False, kind: str = "label"):
        self.text, self.icon, self.enabled = text, icon, enabled
        self.alert, self.kind = alert, kind

    def render(self) -> str:
        mark = {"operator": "[%s]", "prop": "%s", "label": "%s"}[self.kind]
        out = mark % (self.text or self.icon or "")
        if self.alert:
            out = "!" + out
        if not self.enabled:
            out = "·" + out + "·"
        return out


class Layout:
    """Stands in for `UILayout`, and remembers the shape of what was drawn."""

    def __init__(self, kind: str = "column", depth: int = 0):
        self.kind, self.depth = kind, depth
        self.items: list = []
        self.enabled = True
        self.alert = False
        self.active = True
        self.use_property_split = False
        self.use_property_decorate = True
        self.alignment = 'EXPAND'
        self.scale_x = self.scale_y = 1.0
        self.operator_context = 'INVOKE_DEFAULT'

    # -- containers ---------------------------------------------------------
    def _child(self, kind: str) -> "Layout":
        sub = Layout(kind, self.depth + 1)
        sub.enabled = self.enabled
        sub.alert = self.alert
        self.items.append(sub)
        return sub

    def row(self, *a, **k):
        return self._child("row")

    def column(self, *a, **k):
        return self._child("column")

    def box(self, *a, **k):
        return self._child("box")

    def split(self, *a, **k):
        return self._child("row")

    def grid_flow(self, *a, **k):
        return self._child("column")

    def column_flow(self, *a, **k):
        return self._child("column")

    def separator(self, *a, **k):
        self.items.append(Cell("", kind="label"))

    def separator_spacer(self, *a, **k):
        pass

    # -- things --------------------------------------------------------------
    def label(self, text: str = "", icon: str = 'NONE', **k):
        self.items.append(Cell(text, icon, self.enabled, self.alert))

    def operator(self, idname: str, text: str = None, icon: str = 'NONE', **k):
        name = text if text is not None else idname.split(".")[-1].replace("_", " ")
        self.items.append(Cell(name or _icon(icon), _icon(icon),
                               self.enabled, self.alert, "operator"))
        return _Props()

    def prop(self, data, name: str, text: str = None, **k):
        shown = text if text is not None else name.replace("_", " ")
        value = getattr(data, name, "")
        if isinstance(value, float):
            value = "%g" % value
        elif isinstance(value, bool):
            value = "on" if value else "off"
        self.items.append(Cell("%s %s" % (shown, value) if shown else str(value),
                               "", self.enabled, self.alert, "prop"))
        return None

    def template_list(self, *a, **k):
        self.items.append(Cell("(list)", kind="label"))

    def menu(self, *a, **k):
        self.items.append(Cell("(menu)", kind="label"))

    def popover(self, *a, **k):
        self.items.append(Cell("(popover)", kind="label"))

    def __getattr__(self, name):
        def anything(*a, **k):
            return _Props()
        return anything

    # -- rendering -----------------------------------------------------------
    def render(self, out: list, indent: str = "") -> None:
        if self.kind == "box":
            inner: list = []
            for item in self.items:
                _render(item, inner, "")
            width = max([len(line) for line in inner] + [len(indent) + 4])
            out.append(indent + "┌" + "─" * (width + 2) + "┐")
            for line in inner:
                out.append(indent + "│ " + line.ljust(width) + " │")
            out.append(indent + "└" + "─" * (width + 2) + "┘")
            return
        if self.kind == "row":
            parts: list = []
            for item in self.items:
                if isinstance(item, Cell):
                    parts.append(item.render())
                else:
                    inner = []
                    item.render(inner, "")
                    parts.extend(inner)
            text = "  ".join(p for p in parts if p.strip())
            if text:
                out.append(indent + text)
            return
        for item in self.items:
            _render(item, out, indent)


def _render(item, out: list, indent: str) -> None:
    if isinstance(item, Cell):
        text = item.render()
        out.append(indent + text if text.strip() else "")
    else:
        item.render(out, indent)


class _Props:
    def __setattr__(self, name, value):
        pass

    def __getattr__(self, name):
        return _Props()


def _icon(icon: str) -> str:
    return "" if icon in ('NONE', None) else "<%s>" % icon.lower()


class Stand:
    def __init__(self, cls, layout):
        self.layout = layout
        self.bl_label = getattr(cls, "bl_label", "")
        self.bl_idname = getattr(cls, "bl_idname", "")


def sheet(panels, label: str) -> list:
    lines = ["", "=" * WIDE, "  %s" % label, "=" * WIDE]
    for cls in panels:
        layout = Layout()
        try:
            cls.draw(Stand(cls, layout), bpy.context)
        except Exception as exc:                                    # noqa: BLE001
            lines.append("  %s: %s" % (cls.bl_label, exc))
            continue
        closed = 'DEFAULT_CLOSED' in getattr(cls, "bl_options", set())
        lines.append("")
        lines.append(("▸ " if closed else "▾ ") + cls.bl_label)
        drawn: list = []
        layout.render(drawn, "  ")
        lines.extend(line.rstrip() for line in drawn)
    return lines


def main() -> None:
    bpy.ops.preferences.addon_enable(module="cadcore_bridge")
    import cadcore_bridge as addon

    bpy.ops.cadcore.open(filepath=DOC)
    # in the order they are registered, which is the order Blender draws them.
    # Sorting by name here put Analysis above History and made this a picture
    # of a sidebar nobody has
    panels = [c for c in addon.CLASSES
              if isinstance(c, type) and issubclass(c, bpy.types.Panel)]

    ob, table, attr = _cad()
    # whatever this document's first faces are called, not two names typed
    # here: `bracket.json` has `plate/+z` where `sketch_plate.json` has
    # `plate/top`, and naming one made the second sheet a copy of the first
    for label, how_many in (("nothing picked", 0), ("two faces picked", 2)):
        wanted = set(table[:how_many])
        for poly in ob.data.polygons:
            poly.select = table[attr.data[poly.index].value] in wanted
        print("\n".join(sheet(panels, "%s%s" % (
            label, "" if not wanted else ": " + ", ".join(sorted(wanted))))))


def _cad():
    import json

    import cadcore_bridge as addon         # `main` imports it into its own frame

    ob = addon.sync.body()
    return ob, json.loads(ob["cad_face_table"]), ob.data.attributes["cad_face"]


if __name__ == "__main__":
    main()
