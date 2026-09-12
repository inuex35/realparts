"""The kernel's public surface: everything a feature can ask OpenCASCADE for.

Nothing is implemented here. Operations live in :mod:`primitives`,
:mod:`booleans`, :mod:`sweeps`, :mod:`modify`, :mod:`transform`,
:mod:`surfaces` and :mod:`threads`, measuring in :mod:`measure` and file
formats in :mod:`exchange`. Inside the kernel, import those modules directly;
going through this facade would create import cycles.
"""
from __future__ import annotations

from .solids.booleans import common, cut, fuse
from .solids.split import plane_face, section, split
from .solids.emboss import emboss, projected
from ..errors import CadError
from .io.exchange import (export_brep, export_iges, export_step, import_brep, import_iges,
                          import_step)
from .io.mesh import import_mesh, write_gltf
from .core.measure import (area, axis_span, bounding_span, bounds_of, curvature, edge_geometry, edge_points,
                           face_frame, first_hit, helix_start, is_solid, kind_of, mass_properties,
                           nearest, require_solid, volume)
from .solids.modify import chamfer, draft, draft_parted, fillet, hole_tool, rib, shell
from .core.naming import Body
from .solids.primitives import box, cylinder
from .solids.fasteners import fastener
from .solids.sheet import flange, flat_pattern, sheet
from .solids.surfaces import (boundary_loops, cap, delete_face, extend, fill, from_boundary,
                              from_points, move_face, offset_surface, planar, sew, skin, swept,
                              thicken, trim, unified)
from .solids.surfaces import extruded as extruded_surface
from .solids.surfaces import revolved as revolved_surface
from .solids.sweeps import extrude, helix_wire, loft, revolve, sweep
from .core.sketch2d import offset_loops, text_loops
from .solids.threads import spec_of as thread_spec
from .solids.threads import thread
from .solids.transform import circular_pattern, compound, mirror, path_pattern, pattern, translate

__all__ = [
    "Body", "CadError",
    # solids
    "box", "cylinder", "extrude", "revolve", "loft", "sweep", "rib", "helix_wire",
    "text_loops", "offset_loops",
    # booleans and edits
    "cut", "fuse", "common", "fillet", "chamfer", "shell", "draft", "hole_tool",
    "split", "section", "plane_face", "emboss", "projected", "draft_parted",
    # copies
    "translate", "mirror", "pattern", "circular_pattern", "path_pattern", "compound",
    # surfaces
    "planar", "skin", "extruded_surface", "revolved_surface", "swept", "fill",
    "from_points", "from_boundary",
    "sew", "thicken", "cap", "offset_surface", "trim", "extend", "delete_face",
    "boundary_loops", "kind_of", "is_solid", "require_solid", "move_face",
    "unified",
    # threads
    "thread", "thread_spec",
    # sheet metal
    "sheet", "flange", "flat_pattern",
    # measuring
    "face_frame", "first_hit", "edge_geometry", "edge_points", "bounding_span", "volume", "area",
    "curvature", "mass_properties", "nearest", "axis_span", "helix_start",
    # files
    "import_step", "import_iges", "import_brep", "import_mesh",
    "export_step", "export_iges", "export_brep", "write_gltf",
]


# --------------------------------------------------------------- promises --
#
# What each operation promises about its result, enforced by `keep_promises`
# (see `cadcore.geometry.core.promises`); e.g. a cut whose tool misses the target
# is a failure, not a success.
from .core.promises import Promise, keep_promises

#: Every exported operation that returns a body, and what it promises about the
#: result. A new operation must appear here (checked by
#: cadcore/geometry/tests/test_promises.py).
PROMISES = {
    # booleans: the only place a "success" can be a no-op
    "cut": Promise("target", "less", why="the tool did not reach the target"),
    "fuse": Promise("target", "at_least", why="a fuse cannot make a part smaller"),
    "common": Promise("target", "at_most",
                      why="an intersection cannot be bigger than either side"),
    "split": Promise(valid=False, why="one side is smaller; both sides are two solids"),
    "emboss": Promise(why="raised adds material, cut takes it away"),
    # material off
    "shell": Promise("body", "less", why="hollowing takes material away"),
    "hole_tool": Promise(valid=True, why="a tool, not applied to anything yet"),
    # no volume direction the kernel can state: a fillet removes material on a
    # convex edge and adds it on a concave one, a draft does either
    "fillet": Promise(why="rounds convex edges and fills concave ones"),
    "chamfer": Promise(why="as a fillet"),
    "draft": Promise(why="leans a face either way"),
    "draft_parted": Promise(why="leans each half away from the parting line"),
    "rib": Promise(why="a rib may land inside material that is already there"),
    "thread": Promise(why="cuts a relief and adds a ridge; measured in threads.py"),
    # made from nothing, so there is nothing to compare against
    "box": Promise(), "cylinder": Promise(),
    "extrude": Promise(), "revolve": Promise(), "loft": Promise(),
    "sweep": Promise(), "sheet": Promise(),
    "translate": Promise(), "mirror": Promise(), "pattern": Promise(),
    "circular_pattern": Promise(), "path_pattern": Promise(),
    "compound": Promise(valid=False, why="a compound of parts is not one solid"),
    "flange": Promise(), "flat_pattern": Promise(valid=False,
                                                 why="a flat blank is a face"),
    # surfaces: open shells and faces, where validity still means something but
    # volume does not
    "planar": Promise(), "skin": Promise(), "extruded_surface": Promise(),
    "from_points": Promise(), "from_boundary": Promise(),
    "revolved_surface": Promise(), "swept": Promise(), "fill": Promise(),
    "sew": Promise(), "thicken": Promise(), "cap": Promise(),
    "offset_surface": Promise(), "trim": Promise(), "extend": Promise(),
    "delete_face": Promise(), "move_face": Promise(), "unified": Promise(),
    "import_step": Promise(valid=False, why="somebody else's file, as it is"),
    "import_iges": Promise(valid=False, why="somebody else's file, as it is"),
    "import_brep": Promise(valid=False, why="somebody else's file, as it is"),
    "import_mesh": Promise(valid=False, why="a mesh: one face per facet, closed if it was"),
}



keep_promises(globals(), PROMISES)
