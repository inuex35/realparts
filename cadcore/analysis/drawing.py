"""2D drawings, projected from the model and dimensioned by name.

Views, dimensions and tolerances are written against CAD face names, so every
dimension is re-measured on the body at draw time and survives the model
changing. Hidden line removal is OCCT's (HLRBRep); the sheet is plain SVG.
"""
from __future__ import annotations

from .. import names as names_module

import math
from dataclasses import dataclass, field
from html import escape

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from ..errors import CadError
from ..geometry.core.measure import bounding_span
from ..geometry.core.naming import Body, face_info, faces_of
from ..geometry.core.occ import bounds

# Each entry is (direction, up). The direction is where the eye stands, not the
# line of sight: HLR's projector looks back along it, so "top" is (0, 0, 1).
VIEWS = {
    "front": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "back": ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    "top": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "bottom": ((0.0, 0.0, -1.0), (0.0, -1.0, 0.0)),   # seen from below, +x stays right
    "right": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "left": ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
    "iso": ((0.577, -0.577, 0.577), (0.0, 0.0, 1.0)),
}


@dataclass
class View:
    name: str
    visible: list = field(default_factory=list)      # polylines in view coordinates
    hidden: list = field(default_factory=list)
    bounds: tuple = (0.0, 0.0, 0.0, 0.0)             # umin, vmin, umax, vmax
    hatch: list = field(default_factory=list)        # the cut face, on a section

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]


def _axes(direction, up) -> tuple:
    n = _unit(direction)
    x = _unit(_cross(up, n))
    y = _cross(n, x)
    return n, x, y


def _unit(v) -> tuple:
    length = math.sqrt(sum(c * c for c in v)) or 1.0
    return tuple(c / length for c in v)


def _cross(a, b) -> tuple:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _polylines(compound, deflection: float) -> list:
    out = []
    if compound is None:
        return out
    explorer = TopExp_Explorer(compound, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge_s(explorer.Current())
        explorer.Next()
        curve = BRepAdaptor_Curve(edge)
        try:
            sampler = GCPnts_QuasiUniformDeflection(curve, deflection)
        except Exception:                                        # noqa: BLE001
            continue
        if not sampler.IsDone() or sampler.NbPoints() < 2:
            continue
        # HLR output is already in the projector's frame (X, Y sheet, Z depth);
        # do not project it again.
        points = [(sampler.Value(i).X(), sampler.Value(i).Y())
                  for i in range(1, sampler.NbPoints() + 1)]
        out.append(points)
    return out


def section(body: Body, view: str, at, deflection: float = 0.05) -> View:
    """A cut view: the half of the body behind the plane, with the cut face hatched.

    The plane is ``at`` millimetres along the view's own normal, so a section
    stays "through the middle" after the part changes.
    """
    if view not in VIEWS:
        raise CadError("unknown_view", f"no view called {view!r}",
                       {"available": sorted(VIEWS)})
    direction, up = VIEWS[view]
    n, x_axis, y_axis = _axes(direction, up)

    keep = _half_space(body, n, float(at))
    cut = BRepAlgoAPI_Common(body.shape, keep)
    if not cut.IsDone() or not faces_of(cut.Shape()):
        raise CadError("empty_section", f"the section of the {view} view is empty",
                       {"hint": "the plane may miss the part"})
    piece = Body(cut.Shape(), [])

    out = project(piece, view, deflection, hidden=False)
    out.hatch = _hatched(cut.Shape(), n, float(at), x_axis, y_axis)
    out.name = f"{view} section"
    return out


def _half_space(body: Body, normal, at: float):
    """A box covering the half of the part behind the cutting plane.

    A section removes the material between the viewer and the cut, so the kept
    half is on the -normal side (the viewer stands at +normal, see VIEWS).
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Ax2 as _Ax2

    span = bounding_span(body) * 1.5
    # centre the box on the part's bounding box, not the world origin, so a
    # part modelled away from the origin still falls inside it
    bnd = Bnd_Box()
    BRepBndLib.Add_s(body.shape, bnd)
    xmin, ymin, zmin, xmax, ymax, zmax = bounds(bnd)
    centre = ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)
    onto = sum(centre[i] * normal[i] for i in range(3)) - at
    base = [centre[i] - onto * normal[i] for i in range(3)]   # centre, on the plane

    frame = _Ax2(gp_Pnt(*base), gp_Dir(*normal))
    x = frame.XDirection()
    y = frame.YDirection()
    # the box spans [at - span, at] along the normal: the viewer is on the
    # +normal side, so the kept half runs backwards from the plane
    corner = gp_Pnt(*[base[i] - span * (x.Coord(i + 1) + y.Coord(i + 1))
                      - span * normal[i] for i in range(3)])
    box = BRepPrimAPI_MakeBox(_Ax2(corner, gp_Dir(*normal), x),
                              span * 2, span * 2, span)
    return box.Shape()


def _hatched(shape, normal, at: float, x_axis, y_axis) -> list:
    """Hatch lines across the faces the cut exposed: 45 degrees, 2 mm apart.

    Lines are clipped to the cut faces themselves, so a bore through a boss
    comes out as two bands rather than one crossing the hole.
    """
    cut_faces = []
    for face in faces_of(shape):
        info = face_info(face)
        if "normal" not in info:
            continue
        if abs(abs(sum(info["normal"][i] * normal[i] for i in range(3))) - 1.0) > 1e-6:
            continue
        if abs(sum(info["centre"][i] * normal[i] for i in range(3)) - at) > 1e-6:
            continue
        cut_faces.append(face)

    spacing, lines = 2.0, []
    for face in cut_faces:
        corners = _face_bounds(face, x_axis, y_axis)
        if corners is None:
            continue
        umin, vmin, umax, vmax = corners
        offset = math.floor((umin - (vmax - vmin)) / spacing) * spacing
        while offset < umax:
            lines.extend(_hatch_run(face, offset, normal, at, x_axis, y_axis,
                                    vmin, vmax))
            offset += spacing
    return lines


def _face_bounds(face, x_axis, y_axis) -> tuple | None:
    """The face's extent in the view plane, from its bounding box.

    The bounding box rather than the vertices: a cut face bounded by a full
    circle has no usable vertices. The box overshoots a little; `_hatch_run`
    clips the lines to the material.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(face, box)
    if box.IsVoid():
        return None
    xmin, ymin, zmin, xmax, ymax, zmax = bounds(box)
    corners = [(x, y, z) for x in (xmin, xmax) for y in (ymin, ymax)
               for z in (zmin, zmax)]
    us = [sum(c[i] * x_axis[i] for i in range(3)) for c in corners]
    vs = [sum(c[i] * y_axis[i] for i in range(3)) for c in corners]
    return (min(us), min(vs), max(us), max(vs))


def _hatch_run(face, offset: float, normal, at: float, x_axis, y_axis,
               vmin: float, vmax: float) -> list:
    """One 45-degree line, broken where it leaves the material."""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.TopAbs import TopAbs_State
    from OCP.gp import gp_Pnt2d

    surface = BRep_Tool.Surface_s(face)
    steps = max(12, int((vmax - vmin) / 0.4))
    segments, run = [], []
    for k in range(steps + 1):
        v = vmin + (vmax - vmin) * k / steps
        u = offset + (v - vmin)
        point = gp_Pnt(*[x_axis[i] * u + y_axis[i] * v + normal[i] * at
                         for i in range(3)])
        projector = GeomAPI_ProjectPointOnSurf(point, surface)
        if projector.NbPoints() < 1:
            continue
        classifier = BRepClass_FaceClassifier(
            face, gp_Pnt2d(*projector.LowerDistanceParameters()), 1e-7)
        if classifier.State() in (TopAbs_State.TopAbs_IN, TopAbs_State.TopAbs_ON):
            run.append((u, v))
        else:
            if len(run) > 1:
                segments.append([run[0], run[-1]])
            run = []
    if len(run) > 1:
        segments.append([run[0], run[-1]])
    return segments


def project(body: Body, view: str = "front", deflection: float = 0.05,
            hidden: bool = True) -> View:
    """One orthographic view of the body, with hidden lines separated."""
    if view not in VIEWS:
        raise CadError("unknown_view", f"no view called {view!r}",
                       {"available": sorted(VIEWS)})
    direction, up = VIEWS[view]
    n, x_axis, y_axis = _axes(direction, up)

    algo = HLRBRep_Algo()
    algo.Add(body.shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*n), gp_Dir(*x_axis))))
    algo.Update()
    algo.Hide()
    shapes = HLRBRep_HLRToShape(algo)

    visible = _polylines(shapes.VCompound(), deflection)
    visible += _polylines(shapes.OutLineVCompound(), deflection)
    hidden_lines = []
    if hidden:
        hidden_lines = _polylines(shapes.HCompound(), deflection)
        hidden_lines += _polylines(shapes.OutLineHCompound(), deflection)

    points = [p for line in visible + hidden_lines for p in line]
    if not points:
        raise CadError("empty_view", f"the {view} view came out empty")
    bounds = (min(p[0] for p in points), min(p[1] for p in points),
              max(p[0] for p in points), max(p[1] for p in points))
    return View(view, visible, hidden_lines, bounds)


def face_point(body: Body, name: str, view: str) -> tuple:
    """Where a named face's centre lands in a view -- for leaders and marks."""
    if view not in VIEWS:
        raise CadError("unknown_view", f"no view called {view!r}",
                       {"available": sorted(VIEWS)})
    face = body.face(name)
    if face is None:
        raise CadError("unresolved_reference", f"no face named {name!r} on this body",
                       {"available": body.face_names()})
    centre = face_info(face)["centre"]
    _, x_axis, y_axis = _axes(*VIEWS[view])
    return (sum(centre[i] * x_axis[i] for i in range(3)),
            sum(centre[i] * y_axis[i] for i in range(3)))


def measure(body: Body, kind: str, references: list) -> float:
    """The value of a dimension, measured on the model by
    :mod:`cadcore.geometry.core.measure` so the drawing, the panel and an agent get
    the same answer."""
    from ..geometry.core.measure import dimension
    return dimension(body, kind, references)


# ------------------------------------------------------------------- sheet --
SHEET = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0)}
SYMBOLS = {"position": "⌖", "flatness": "⏥", "perpendicularity": "⟂",
           "parallelism": "∥", "concentricity": "◎", "cylindricity": "⌭",
           "profile": "⌓", "runout": "↗", "straightness": "—",
           "circularity": "○"}


def _layout(views: dict, names: list, size, margin: float, gap: float,
            scale: float) -> dict:
    """Third-angle layout: top view above the front, right view to its right,
    all aligned with the front view."""
    anchor = names[0]
    offsets = {anchor: (0.0, 0.0)}
    front = views[anchor]
    right_edge = front.width * scale
    for name in names[1:]:
        view = views[name]
        if name == "top":
            offsets[name] = (0.0, (front.height + gap) * scale)
        elif name == "bottom":
            offsets[name] = (0.0, -(view.height + gap) * scale)
        elif name in ("right", "iso", "back"):
            # third angle puts the rear view beyond the right view; it must
            # not share the top view's place
            offsets[name] = (right_edge + gap * scale, 0.0)
            right_edge += (view.width + gap) * scale
        elif name == "left":
            offsets[name] = (-(view.width + gap) * scale, 0.0)
        else:
            offsets[name] = (right_edge + gap * scale, 0.0)
            right_edge += (view.width + gap) * scale

    # centre the group on the sheet, leaving room for the title block
    lows_u = [offsets[n][0] for n in names]
    highs_u = [offsets[n][0] + views[n].width * scale for n in names]
    lows_v = [offsets[n][1] for n in names]
    highs_v = [offsets[n][1] + views[n].height * scale for n in names]
    span_u, span_v = max(highs_u) - min(lows_u), max(highs_v) - min(lows_v)
    origin_u = margin + max(0.0, (size[0] - 2 * margin - 100 - span_u) / 2)
    origin_v = margin + max(0.0, (size[1] - 2 * margin - span_v) / 2)

    placed = {}
    for name in names:
        view = views[name]
        u = origin_u + offsets[name][0] - min(lows_u)
        v = origin_v + offsets[name][1] - min(lows_v)
        # store the sheet position of the view's own (umin, vmin) corner
        placed[name] = (u - view.bounds[0] * scale,
                        size[1] - (v - view.bounds[1] * scale))
    return placed


def _view_of(body: Body, name: str, spec: dict, deflection: float) -> View:
    """A view by name, or a section when the sheet says where to cut it:

        {"views": ["front", "top"], "sections": {"top": 0.0}}

    The section plane is a distance along the view's own normal.
    """
    sections = spec.get("sections") or {}
    if name in sections:
        return section(body, name, float(sections[name]), deflection)
    return project(body, name, deflection, bool(spec.get("hidden", True)))


def _svg_polyline(points, klass: str) -> str:
    data = " ".join(f"{u:.3f},{v:.3f}" for u, v in points)
    return f'<polyline class="{klass}" points="{data}"/>'


def _arrow(u, v, angle, size=1.6) -> str:
    left = (u - size * math.cos(angle - 0.35), v - size * math.sin(angle - 0.35))
    right = (u - size * math.cos(angle + 0.35), v - size * math.sin(angle + 0.35))
    return (f'<polygon class="arrow" points="{u:.2f},{v:.2f} {left[0]:.2f},{left[1]:.2f} '
            f'{right[0]:.2f},{right[1]:.2f}"/>')


def sheet(body: Body, spec: dict, meta: dict | None = None,
          parts: list | None = None) -> str:
    """A drawing sheet as SVG: views, dimensions, tolerances and title block,
    all measured on the body at draw time.

    ``parts`` is an assembly's bill of materials (the flat lines): each part
    gets a numbered balloon on the first view and a row in a parts list.
    """
    meta = meta or {}
    size = SHEET.get(spec.get("sheet", "A3"), SHEET["A3"])
    scale = float(spec.get("scale", 1.0))
    margin, gap = 12.0, 22.0
    names = spec.get("views") or ["front", "top", "right"]
    deflection = float(spec.get("deflection", 0.05))
    views = {name: _view_of(body, name, spec, deflection) for name in names}

    placed = _layout(views, names, size, margin, gap, scale)

    parts_list = parts
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size[0]}mm" '
             f'height="{size[1]}mm" viewBox="0 0 {size[0]} {size[1]}">',
             '<style>'
             '.visible{fill:none;stroke:#111;stroke-width:0.35}'
             '.hidden{fill:none;stroke:#777;stroke-width:0.18;stroke-dasharray:1.6 1.2}'
             '.hatch{fill:none;stroke:#666;stroke-width:0.12}'
             '.hatch{fill:none;stroke:#555;stroke-width:0.12}'
             '.dim{fill:none;stroke:#c1121f;stroke-width:0.18}'
             '.arrow{fill:#c1121f;stroke:none}'
             '.frame{fill:none;stroke:#111;stroke-width:0.3}'
             'text{font-family:DejaVu Sans,Helvetica,sans-serif;font-size:3.2px;fill:#111}'
             'text.dim{font-size:3px;fill:#c1121f;stroke:none}'
             'text.small{font-size:2.6px;fill:#333}'
             '</style>',
             f'<rect class="frame" x="{margin / 2}" y="{margin / 2}" '
             f'width="{size[0] - margin}" height="{size[1] - margin}"/>']

    def to_sheet(name: str, point) -> tuple:
        if name not in placed:
            raise CadError("view_not_on_the_sheet",
                           f"the sheet has no {name!r} view to draw on",
                           {"views": list(names)})
        ox, oy = placed[name]
        return (ox + point[0] * scale, oy - point[1] * scale)   # SVG y runs down

    for name in names:
        view = views[name]
        for line in view.hidden:
            out.append(_svg_polyline([to_sheet(name, p) for p in line], "hidden"))
        for line in view.hatch:
            out.append(_svg_polyline([to_sheet(name, p) for p in line], "hatch"))
        for line in view.visible:
            out.append(_svg_polyline([to_sheet(name, p) for p in line], "visible"))
        label = to_sheet(name, (view.bounds[0], view.bounds[1]))
        out.append(f'<text x="{label[0]:.1f}" y="{label[1] + 6:.1f}">'
                     f'{view.name.upper()}</text>')

    corners = {name: [to_sheet(name, (u, v))
                      for u in (views[name].bounds[0], views[name].bounds[2])
                      for v in (views[name].bounds[1], views[name].bounds[3])]
               for name in names}
    for dimension in spec.get("dimensions", []):
        out.extend(_draw_dimension(body, dimension, to_sheet, scale, corners))
    for note in spec.get("notes", []):
        out.extend(_draw_note(body, note, to_sheet))
    for tolerance in spec.get("tolerances", []):
        out.extend(_draw_tolerance(body, tolerance, spec.get("datums", {}), to_sheet))
    for letter, face in (spec.get("datums") or {}).items():
        view = spec.get("datum_view", names[0])
        at = to_sheet(view, face_point(body, face, view))
        out.append(f'<rect class="frame" x="{at[0] - 2.4:.1f}" y="{at[1] - 2.4:.1f}" '
                     f'width="4.8" height="4.8"/>')
        out.append(f'<text x="{at[0] - 1.1:.1f}" y="{at[1] + 1.2:.1f}">{letter}</text>')

    if parts_list:
        out.extend(_balloons(body, parts_list, names[0], views[names[0]], to_sheet,
                             scale, deflection))
        out.extend(_parts_list(size, margin, parts_list))
    out.extend(_title_block(size, margin, spec, meta, scale))
    out.append("</svg>")
    return "\n".join(out)


def _balloons(body: Body, lines: list, view_name: str, view, to_sheet, scale: float,
              deflection: float) -> list:
    """A numbered circle per part, above the view, with a leader to the part.

    The number is the row in the parts list. A part is found by the scope its
    faces carry (``left:base``), a pattern's copies by the name before ``~``.
    """
    from ..geometry.io.exchange import assembly_tree

    tree = assembly_tree(body)
    if not tree:
        return []
    leaves = _flat_tree(tree)
    numbers = {line["part"]: k for k, line in enumerate(lines, start=1)}
    out, column = [], 0
    top = to_sheet(view_name, (view.bounds[0], view.bounds[3]))[1] - 9.0
    for name, solid in leaves:
        number = numbers.get(name) or numbers.get(name.split(names_module.INSTANCE)[0])
        if number is None:
            continue
        try:
            seen = project(Body(solid, []), view_name, deflection, hidden=False)
        except CadError:
            continue
        # the highest point of the part in this view: a point on its outline
        u, v = max((p for line in seen.visible for p in line), key=lambda p: p[1])
        at = to_sheet(view_name, (u, v))
        left = to_sheet(view_name, (view.bounds[0], 0.0))[0]
        circle = (left + 6.0 + 9.0 * column, top)
        column += 1
        out.append(f'<line class="dim" x1="{at[0]:.2f}" y1="{at[1]:.2f}" '
                   f'x2="{circle[0]:.2f}" y2="{circle[1] + 3.0:.2f}"/>')
        out.append(f'<circle class="dim" cx="{circle[0]:.2f}" cy="{circle[1]:.2f}" r="3"/>')
        out.append(f'<text class="dim" x="{circle[0] - 1.0:.2f}" y="{circle[1] + 1.1:.2f}">'
                   f'{number}</text>')
    return out


def _flat_tree(tree: dict, scope: str = "") -> list:
    out = []
    for name, node in tree.items():
        here = names_module.scoped(scope, name) if scope else name
        out += _flat_tree(node, here) if isinstance(node, dict) else [(here, node)]
    return out


def _parts_list(size, margin, lines: list) -> list:
    """The table above the bottom-left corner: number, part, quantity, material, mass."""
    columns = ((2.0, "NO"), (10.0, "PART"), (58.0, "QTY"), (68.0, "MATERIAL"), (92.0, "MASS g"))
    width, row_height = 112.0, 4.4
    x = margin / 2
    y = size[1] - margin / 2 - row_height * (len(lines) + 1)
    out = [f'<rect class="frame" x="{x:.1f}" y="{y:.1f}" width="{width}" '
           f'height="{row_height * (len(lines) + 1):.1f}"/>']
    for dx, label in columns:
        out.append(f'<text class="small" x="{x + dx:.1f}" y="{y + 3.2:.1f}">{label}</text>')
    for k, line in enumerate(lines, start=1):
        row_y = y + 3.2 + row_height * k
        mass = "" if line.get("mass_g") is None else f"{line['mass_g']:.1f}"
        for dx, text in ((2.0, str(k)), (10.0, line["part"]), (58.0, str(line["quantity"])),
                         (68.0, line.get("material") or ""), (92.0, mass)):
            out.append(f'<text class="small" x="{x + dx:.1f}" y="{row_y:.1f}">'
                       f'{escape(str(text), quote=False)}</text>')
    return out


def _draw_dimension(body: Body, spec: dict, to_sheet, scale: float,
                    corners: dict | None = None) -> list:
    view = spec.get("view", "front")
    kind = spec.get("type", "linear")
    faces = spec.get("faces") or spec.get("between") or []
    value = measure(body, kind, faces)
    offset = float(spec.get("offset", 10.0))

    if kind == "linear":
        # measure along the faces' normal, not the line between their centres:
        # two faces offset sideways still give the distance between the planes
        direction = _view_direction(body, faces, view)
        a = to_sheet(view, face_point(body, faces[0], view))
        b = to_sheet(view, face_point(body, faces[1], view))
        # a and b are sheet coordinates, whose y runs down; flip the view-space
        # direction to match
        d = (direction[0], -direction[1])
        n = (-d[1], d[0])
        # the dimension line clears the whole projected outline, not just the
        # two faces
        box = (corners or {}).get(view) or [a, b]
        reach = [p[0] * n[0] + p[1] * n[1] for p in box]
        base = (max(reach) + offset) if offset >= 0 else (min(reach) + offset)
        a2 = (a[0] + n[0] * (base - a[0] * n[0] - a[1] * n[1]),
              a[1] + n[1] * (base - a[0] * n[0] - a[1] * n[1]))
        b2 = (b[0] + n[0] * (base - b[0] * n[0] - b[1] * n[1]),
              b[1] + n[1] * (base - b[0] * n[0] - b[1] * n[1]))
        angle = math.atan2(b2[1] - a2[1], b2[0] - a2[0])
        text = f"{value:.1f}"
        mid = ((a2[0] + b2[0]) / 2, (a2[1] + b2[1]) / 2)
        return [_svg_polyline([a, a2], "dim"), _svg_polyline([b, b2], "dim"),
                _svg_polyline([a2, b2], "dim"),
                _arrow(a2[0], a2[1], angle + math.pi), _arrow(b2[0], b2[1], angle),
                f'<text class="dim" x="{mid[0]:.1f}" y="{mid[1] - 1.2:.1f}" '
                f'text-anchor="middle">{text}</text>']

    # everything else is a leader with the number on it, prefixed or suffixed
    # per kind so an angle reads as degrees and an area as mm², never as `R`
    symbols = {"diameter": ("⌀", ""), "radius": ("R", ""),
               "angle": ("", "°"), "area": ("", " mm²"), "centres": ("", "")}
    if kind not in symbols:
        raise CadError("unknown_dimension",
                       f"a drawing cannot show a {kind!r} dimension",
                       {"kinds": sorted(symbols) + ["linear"]})
    symbol, suffix = symbols[kind]
    centre = to_sheet(view, face_point(body, faces[0], view))
    tip = (centre[0] + offset, centre[1] - offset)
    shoulder = (tip[0] + 5.0, tip[1])            # a leader ends flat, then the text
    return [_svg_polyline([centre, tip, shoulder], "dim"),
            _arrow(centre[0], centre[1], math.atan2(centre[1] - tip[1], centre[0] - tip[0])),
            f'<text class="dim" x="{shoulder[0] + 1:.1f}" y="{shoulder[1] - 0.8:.1f}">'
            f'{symbol}{value:.1f}{suffix}</text>']


def _draw_note(body: Body, spec: dict, to_sheet) -> list:
    """A leader and a line of text, e.g. a threaded-hole callout. Threads are
    notes, not geometry."""
    view = spec.get("view", "front")
    at = to_sheet(view, face_point(body, spec["face"], view))
    tip = (at[0] + float(spec.get("dx", 16.0)), at[1] - float(spec.get("dy", 12.0)))
    shoulder = (tip[0] + 5.0, tip[1])
    angle = math.atan2(at[1] - tip[1], at[0] - tip[0])
    return [_svg_polyline([at, tip, shoulder], "dim"),
            _arrow(at[0], at[1], angle),
            f'<text x="{shoulder[0] + 1:.1f}" y="{shoulder[1] - 0.8:.1f}">'
            f'{escape(str(spec["text"]), quote=False)}</text>']


def _view_direction(body: Body, faces: list, view: str) -> tuple:
    """The measuring direction in the view: the faces' normal, projected."""
    _, x_axis, y_axis = _axes(*VIEWS[view])
    for name in faces:
        info = face_info(body.face(name))
        normal = info.get("normal")
        if normal is None:
            continue
        u = sum(normal[i] * x_axis[i] for i in range(3))
        v = sum(normal[i] * y_axis[i] for i in range(3))
        length = math.hypot(u, v)
        if length > 1e-6:
            return (u / length, v / length)
    raise CadError("not_in_view", "the dimension's direction is edge-on in this view",
                   {"faces": faces, "view": view})


def _draw_tolerance(body: Body, spec: dict, datums: dict, to_sheet) -> list:
    view = spec.get("view", "front")
    kind = spec.get("kind", "position")
    if kind not in SYMBOLS:
        raise CadError("unknown_tolerance", f"{kind!r} is not a control I know",
                       {"available": sorted(SYMBOLS)})
    for letter in spec.get("datums", []):
        if letter not in datums:
            raise CadError("unknown_datum", f"datum {letter!r} is not defined",
                           {"defined": sorted(datums)})
    at = to_sheet(view, face_point(body, spec["face"], view))
    box = (at[0] + float(spec.get("dx", 14.0)), at[1] - float(spec.get("dy", 14.0)))
    cells = [SYMBOLS[kind], ("⌀" if spec.get("diametral") else "")
             + f"{float(spec['value']):.2f}"] + list(spec.get("datums", []))
    widths = [6.0, 12.0] + [5.0] * len(spec.get("datums", []))
    frame = [_svg_polyline([at, box], "dim")]
    x = box[0]
    for cell, width in zip(cells, widths):
        frame.append(f'<rect class="frame" x="{x:.1f}" y="{box[1] - 4:.1f}" '
                     f'width="{width}" height="5"/>')
        frame.append(f'<text x="{x + width / 2:.1f}" y="{box[1] - 0.4:.1f}" '
                     f'text-anchor="middle">{cell}</text>')
        x += width
    return frame


def _title_block(size, margin, spec, meta, scale) -> list:
    width, height = 92.0, 26.0
    x = size[0] - margin / 2 - width
    y = size[1] - margin / 2 - height
    title = spec.get("title", {})
    rows = [("PART", title.get("part") or meta.get("name", "")),
            ("MATERIAL", title.get("material", "")),
            ("FINISH", title.get("finish", "")),
            ("SCALE", f"{scale:g}:1"),
            ("UNITS", "mm")]
    out = [f'<rect class="frame" x="{x:.1f}" y="{y:.1f}" width="{width}" height="{height}"/>']
    for i, (label, value) in enumerate(rows):
        row_y = y + 4.6 + i * 4.4
        out.append(f'<text class="small" x="{x + 2:.1f}" y="{row_y:.1f}">{label}</text>')
        out.append(f'<text x="{x + 26:.1f}" y="{row_y:.1f}">{escape(str(value), quote=False)}</text>')
    return out


# -------------------------------------------------------------------- DXF --
def dxf(body: Body, spec: dict) -> str:
    """The same projected views as DXF (R2000), for laser cutters and CAM.

    Visible and hidden lines go on separate layers so a cutter cuts one and
    not the other.
    """
    names = spec.get("views") or ["front"]
    scale = float(spec.get("scale", 1.0))
    views = {name: _view_of(body, name, spec, float(spec.get("deflection", 0.05)))
             for name in names}
    size = SHEET.get(spec.get("sheet", "A3"), SHEET["A3"])
    placed = {name: (u, size[1] - v)             # DXF counts y upward
              for name, (u, v) in _layout(views, names, size, 12.0, 22.0, scale).items()}

    out = ["999", "RealParts", "0", "SECTION", "2", "HEADER",
           "9", "$ACADVER", "1", "AC1015",
           "9", "$INSUNITS", "70", "4",              # millimetres
           "0", "ENDSEC",
           "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", "3"]
    for layer, colour in (("VISIBLE", "7"), ("HIDDEN", "8"), ("HATCH", "9")):
        out += ["0", "LAYER", "2", layer, "70", "0", "62", colour, "6", "CONTINUOUS"]
    out += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]

    for name in names:
        view = views[name]
        origin_u, origin_v = placed[name]
        for layer, lines in (("HIDDEN", view.hidden), ("HATCH", view.hatch),
                             ("VISIBLE", view.visible)):
            for polyline in lines:
                out += ["0", "LWPOLYLINE", "8", layer, "100", "AcDbEntity",
                        "100", "AcDbPolyline", "90", str(len(polyline)), "70", "0"]
                for u, v in polyline:
                    out += ["10", f"{origin_u + u * scale:.4f}",
                            "20", f"{origin_v + v * scale:.4f}"]
    out += ["0", "ENDSEC", "0", "EOF"]
    return "\n".join(out) + "\n"
