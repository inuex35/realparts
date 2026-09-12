"""Which icon stands for a feature type or a constraint, and whether this
Blender knows it. Its own module so the menus can ask without importing the
panels, which import the menus."""
from __future__ import annotations

import bpy


# One icon per family. The kernel reports each feature type's family
# (`op_feature_types`), so a new type gets an icon without a row here.
FAMILY_ICONS = {
    "solid": 'MESH_CUBE', "boolean": 'MOD_BOOLEAN', "modify": 'MOD_BEVEL',
    "surface": 'OUTLINER_OB_SURFACE', "thread": 'MOD_SCREW',
    "pattern": 'MOD_ARRAY', "datum": 'EMPTY_AXIS', "input": 'IMPORT',
    "assembly": 'OUTLINER_COLLECTION',
}

# Per-type icons for the common feature types.
ICONS = {
    "sketch": 'GREASEPENCIL', "extrude": 'MESH_CUBE', "revolve": 'MESH_CYLINDER',
    "cylinder": 'MESH_CYLINDER', "hole": 'MESH_CIRCLE', "fillet": 'MOD_BEVEL',
    "shell": 'MOD_SOLIDIFY', "thicken": 'MOD_SOLIDIFY', "mirror": 'MOD_MIRROR',
    "plane": 'OUTLINER_OB_LATTICE', "delete_face": 'TRASH',
    "translate": 'ORIENTATION_GLOBAL', "sweep": 'CURVE_PATH',
}


def icon_for(kind: str, context) -> str:
    """The icon for a feature type: its own, or its family's, or a dot."""
    if kind in ICONS:
        return ICONS[kind]
    family = context.scene.cadcore.families.get(kind)
    return FAMILY_ICONS.get(family.category if family else "", 'DOT')


# One icon per constraint type.
CONSTRAINT_ICONS = {
    "coincident": 'SNAP_VERTEX', "fix": 'PINNED', "midpoint": 'SNAP_MIDPOINT',
    "horizontal": 'ARROW_LEFTRIGHT', "vertical": 'EMPTY_SINGLE_ARROW',
    "parallel": 'SNAP_EDGE', "perpendicular": 'SNAP_PERPENDICULAR',
    "tangent": 'SPHERECURVE', "angle": 'DRIVER_ROTATIONAL_DIFFERENCE',
    "distance": 'DRIVER_DISTANCE', "distance_to_line": 'DRIVER_DISTANCE',
    "radius": 'CURVE_BEZCIRCLE', "diameter": 'CURVE_BEZCIRCLE',
    "equal_length": 'SNAP_EDGE', "equal_radius": 'CURVE_BEZCIRCLE',
    "symmetric": 'MOD_MIRROR', "on_perpendicular_bisector": 'MOD_MIRROR',
    "point_on_line": 'SNAP_EDGE', "point_on_curve": 'SNAP_EDGE',
}

CONSTRAINT_FALLBACK = 'CON_TRACKTO'

_ICON_NAMES = None


def icon_names() -> frozenset:
    """The icon identifiers this Blender build knows, read once from its enum.

    Blender raises on an unknown icon name and a panel that raises does not
    draw, so icon names are checked against this set and fall back.
    """
    global _ICON_NAMES
    if _ICON_NAMES is None:
        parameters = bpy.types.UILayout.bl_rna.functions["label"].parameters
        _ICON_NAMES = frozenset(parameters["icon"].enum_items.keys())
    return _ICON_NAMES


def constraint_icon(kind: str) -> str:
    """The icon for a constraint type, or the one that always exists."""
    name = CONSTRAINT_ICONS.get(kind, CONSTRAINT_FALLBACK)
    return name if name in icon_names() else CONSTRAINT_FALLBACK
