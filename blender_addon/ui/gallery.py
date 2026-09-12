"""The shipped examples as pictures, so a new document can start from one."""
from __future__ import annotations

import os

import bpy
import bpy.utils.previews

_previews = None
_items: list = []

#: what the gallery offers, in the order it draws them. The shipped examples
#: are more than this: the rest are what the tests build, and a person opening
#: the add-on for the first time should not have to read past them.
ABOUT = {
    "bracket": "a bracket: holes, a round, and studies that check it",
    "cup": "turned on an axis, hollowed, with a handle swept on",
    "flange": "a bolt circle round a bore",
    "sketch_plate": "drawn as a sketch, then given a thickness",
    "cast_cover": "a cast part: taper, a lofted tower, mirrored lugs, a rib",
    "sheet_bracket": "sheet metal, two bends, and the flat to cut",
    "intake_cowl": "built from surfaces: trimmed, patched and sewn shut",
    "assembly": "two parts, held together by their faces",
}


def folder() -> str:
    from ..link.state import repo_root

    return os.path.join(repo_root(), "examples")


def load() -> None:
    """Read every example's picture once, at register."""
    global _previews, _items
    unload()
    _previews = bpy.utils.previews.new()
    _items = []
    pictures = os.path.join(folder(), "previews")
    if not os.path.isdir(pictures):
        return
    index = 0
    for stem, about in ABOUT.items():
        picture = os.path.join(pictures, stem + ".png")
        if not os.path.exists(picture) or \
                not os.path.exists(os.path.join(folder(), stem + ".json")):
            continue
        icon = _previews.load(stem, picture, 'IMAGE')
        _items.append((stem, stem.replace("_", " "), about, icon.icon_id, index))
        index += 1


def unload() -> None:
    global _previews
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None


def items(self, context):
    """Enum items for `template_icon_view`; the list is kept alive here."""
    return _items or [("none", "no examples found", "", 'QUESTION', 0)]


def path_of(stem: str) -> str:
    return os.path.join(folder(), stem + ".json")
