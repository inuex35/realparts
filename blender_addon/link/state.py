"""Shared state for the add-on: the kernel client, the refresh guard and the
Blender handlers (frame change, undo, file load).

The refresh flag lives here so operators, panels and property callbacks share
it; a property callback that fired during a refresh would send the value it
was just given back to the kernel.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager

import bpy

#: the add-on's own package -- one level up from this sub-package -- which is
#: what Blender keys its preferences by, whichever door it was installed through
ADDON = __package__.rpartition(".")[0]

from ..ui import animate
from . import sync, throttle
from .client import (KERNEL_PYTHONS, Client, ServerError, blender_python,
                     kernel_for)

_client: Client | None = None
_refreshing = False


@contextmanager
def refresh_guard():
    """Suppress property update callbacks while the panel is being refreshed."""
    global _refreshing
    was, _refreshing = _refreshing, True
    try:
        yield
    finally:
        _refreshing = was


def is_refreshing() -> bool:
    return _refreshing


def repo_root() -> str:
    """The directory holding the kernel code: the bundled `kernel/` folder
    made by `tools/package.py`, or else the checkout one directory up."""
    here = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))   # the add-on's root
    bundled = os.path.join(here, "kernel")
    if os.path.isdir(os.path.join(bundled, "cadcore")):
        return bundled
    return os.path.dirname(here)


def is_bundled() -> bool:
    """Running from an installed add-on rather than a checkout."""
    return os.path.basename(repo_root()) == "kernel" and \
        os.path.dirname(repo_root()) == os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def kernel_home() -> str | None:
    """Where the installed kernel packages live; None means beside the repo.

    A bundled add-on uses Blender's per-user, per-version config directory,
    which is writable and survives an add-on upgrade.
    """
    if not is_bundled():
        return None
    return bpy.utils.user_resource('CONFIG', path="cadcore", create=True)


def kernel_status(prefs) -> tuple[bool, str]:
    """Is the kernel ready to run, and what to tell the person if not."""
    import sys

    repo = getattr(prefs, "repo_path", "") or repo_root()
    python, site = kernel_for(repo, getattr(prefs, "python_path", ""), kernel_home())
    if blender_python() is None and not os.path.exists(python):
        return False, ("this Blender's Python is %d.%d; the kernel needs %s (Blender 5.1 or newer)"
                       % (*sys.version_info[:2], " or ".join(KERNEL_PYTHONS)))
    if not os.path.exists(python):
        return False, "no kernel interpreter at %s" % python
    if site is None:
        return True, "kernel: %s" % python
    from .client import bundled_kernel

    if site == bundled_kernel():
        return True, "kernel: shipped with the add-on, nothing to download"
    if os.path.isdir(site):
        return True, "kernel installed for Blender's Python: %s" % site
    return False, "the CAD kernel is not installed yet"


def prefs(context):
    return context.preferences.addons[ADDON].preferences


def get_client(context) -> Client:
    """The kernel client, after flushing any edit held by `throttle`.

    Flushing here covers every caller: a held edit is a change the document
    has not received yet. A held edit's own call re-enters here and is let
    through (see `throttle.flush`).
    """
    global _client
    throttle.flush()
    p = prefs(context)
    repo = p.repo_path or repo_root()
    from .client import kernel_for

    python, site = kernel_for(repo, p.python_path, kernel_home())
    if (_client is None or _client.python != python or _client.repo != repo
            or _client.site != site):
        if _client is not None:
            _client.stop()
        _client = Client(python, repo, site)
    return _client


def stop_client() -> None:
    global _client
    if _client is not None:
        _client.stop()
        _client = None


def _hold(context, tool: str, dimensions: bool = False) -> None:
    """Put `tool` in hand in every 3D view."""
    screen = getattr(context, "screen", None)
    if screen is None:
        return
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        try:
            with context.temp_override(area=area, region=region):
                bpy.ops.wm.tool_set_by_id(name=tool)
                if dimensions and not context.scene.cadcore.show_dimensions:
                    bpy.ops.cadcore.show_dimensions()
        except Exception:                                           # noqa: BLE001
            pass


def twin(tool: str, mode: str) -> str:
    """The same CAD tool as that mode holds it: `cadcore.tool_box` <-> `..._edit`."""
    if not tool.startswith("cadcore."):
        tool = "cadcore.tool_pick"
    plain = tool[:-len("_edit")] if tool.endswith("_edit") else tool
    return plain + "_edit" if mode == 'EDIT_MESH' else plain


def carry_the_tool(context, into: str) -> None:
    """Keep the tool in hand across a mode change.

    Blender holds one tool per mode, so entering edit mode drops what was in
    hand for whatever edit mode had last -- Tweak -- and the next drag is
    Blender's select rather than the tool the person reached for.
    """
    came_from = 'OBJECT' if into == 'EDIT_MESH' else 'EDIT_MESH'
    try:
        held = context.workspace.tools.from_space_view3d_mode(came_from, create=False)
    except Exception:                                               # noqa: BLE001
        held = None
    _hold(context, twin(getattr(held, "idname", "") or "", into))


def hand_the_pick_tool(context) -> None:
    """Make CAD Pick the active tool in every 3D view, so a click picks a face.

    There is one tool per mode, so this runs again whenever the mode changes;
    the object-mode tool is not bound to a click in edit mode.
    """
    _hold(context, twin("cadcore.tool_pick", context.mode), dimensions=True)


def _frame_body() -> None:
    """Fit every 3D view to the part. The mesh is in millimetres, so a part is
    hundreds of units across and the default view shows a corner of it."""
    context = bpy.context
    if sync.body() is None:
        return None
    # from a timer the context has no screen; the windows are walked instead
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            space = area.spaces.active
            # far enough for a part a few metres across, in millimetres
            if space.clip_end < 100000:
                space.clip_end = 100000
            try:
                with context.temp_override(window=window, area=area, region=region):
                    bpy.ops.view3d.view_all(center=False)
                space.region_3d.view_distance *= 1.3          # a margin around the part
            except RuntimeError:
                pass
    return None


def _apply_build(context, info: dict) -> None:
    """Show a build reply. Five things change, in this order:

    1. the readout on the scene (faces, volume, envelope, requirements, status)
    2. the scene's unit, to the document's
    3. the work planes the overlay draws (one kernel call: `planes`)
    4. on a document's first body: the pick tool is put in hand, the view framed
    5. the mesh, redrawn from a `tessellate` call and stamped with the revision
    then the sidebar's lists are refilled from `describe_document`.
    """
    props = context.scene.cadcore
    previous_features = {f.name for f in props.features}
    from ..viewport import marks
    marks.forget_faces()
    _show_readout(props, info)
    _set_scene_unit(context, info.get("unit") or "mm")
    _remember_planes(context)
    if marks.PLANES:
        from ..ui import overlay
        props.show_dimensions = True
        overlay.enable()
        if not props.has_body:
            _hold(context, "cadcore.tool_pick")
    _first_body(context, props)
    _redraw_mesh(context, props, info)
    with refresh_guard():
        _refresh_features(context)
        added = [f.name for f in props.features if f.name not in previous_features]
        focus = info.get("feature") or (added[-1] if added else None)
        if focus and props.features.find(focus) >= 0:
            props.feature_index = props.features.find(focus)
            _refresh_feature_args(context)


def _show_readout(props, info: dict) -> None:
    props.faces = info.get("faces", 0)
    props.edges = info.get("edges", 0)
    props.has_body = not info.get("under_construction") and props.faces > 0
    props.volume = info.get("volume_mm3", 0.0)
    props.in_envelope = bool(info.get("in_envelope", True))
    props.rolled_back_to = info.get("rolled_back_to") or ""
    _apply_requirements(props, info)
    stats = info.get("stats") or {}
    props.status_is_error = False
    done = stats.get("evaluated", 0) + stats.get("reused", 0)
    props.status = "rebuilt%s%s" % (
        " · %d of %d features reused" % (stats.get("reused", 0), done) if done else "",
        "" if props.in_envelope else " -- outside envelope: %s" % info.get("envelope_note"))
    if info.get("under_construction"):
        props.status = info.get("hint") or "nothing makes a solid yet"


def _remember_planes(context) -> None:
    from ..viewport import marks

    marks.PLANES.clear()
    try:
        marks.PLANES.update(get_client(context).call("planes")["planes"])
    except ServerError:
        pass


def _first_body(context, props) -> None:
    """The document's first body: the pick tool in hand, the view framed once."""
    if props.has_body and not props.was_picking:
        hand_the_pick_tool(context)
        props.was_picking = True
        bpy.app.timers.register(_frame_body, first_interval=0.0)


def _redraw_mesh(context, props, info: dict) -> None:
    """The mesh from a `tessellate` call, stamped with the state's revision;
    under construction it is the sketches and planes as wire."""
    client = get_client(context)
    data = client.call("tessellate", deflection=props.deflection)
    revision = info.get("revision")
    if revision is None:
        revision = client.call("describe_document").get("revision", 0)
    sync.apply(data, revision=int(revision))
    props.revision = int(revision)


#: Scene unit settings per document unit: (scale_length, length_unit, system).
#: One document unit is one Blender unit, so the scene scale must be set or
#: Blender reads millimetres as metres.
_UNITS = {"mm": (0.001, 'MILLIMETERS', 'METRIC'),
          "cm": (0.01, 'CENTIMETERS', 'METRIC'),
          "m": (1.0, 'METERS', 'METRIC'),
          "in": (0.0254, 'INCHES', 'IMPERIAL')}


def _apply_requirements(props, info: dict) -> None:
    """Refill the requirement rows from a build reply; a missing or empty key
    clears them. Study rows come from the last `simulate`, not from here."""
    props.requirements.clear()
    for row in info.get("requirements") or []:
        item = props.requirements.add()
        item.name = row.get("id", "")
        unit = row.get("unit") or ""
        item.text = "%s %s %s%s" % (row.get("quantity"), row.get("compare"),
                                    _short(row.get("value")), (" " + unit) if unit else "")
        item.got = "" if row.get("got") is None else _short(row["got"])
        item.ok = bool(row.get("ok"))
        item.error = row.get("error") or ""


def _short(value) -> str:
    if isinstance(value, float):
        return ("%.4g" % value)
    return str(value)


def _set_scene_unit(context, unit: str) -> None:
    """Set the scene unit settings to the document's unit."""
    known = _UNITS.get(unit)
    if known is None:
        return
    scale, length, system = known
    settings = context.scene.unit_settings
    if settings.system != system:
        settings.system = system
    if abs(settings.scale_length - scale) > 1e-12:
        settings.scale_length = scale
    if settings.length_unit != length:
        settings.length_unit = length


def clear_scene_for_empty_document(context) -> None:
    """Reset the scene and the panel to an empty document.

    Drops the last document's body and wires from the scene, refreshes the
    feature list, and marks that no solid is built. The New Document operator
    calls this, and so does the assistant bridge when an op empties the
    document -- otherwise a stale mesh is left drawn and selectable over a
    document that has no solid, and picking its faces answers `no_solid`.
    """
    props = context.scene.cadcore
    _refresh_features(context)
    _refresh_families(context)
    for coll in (props.requirements, props.studies, props.report):
        coll.clear()
    props.faces = props.edges = 0
    props.volume = 0.0
    props.has_body = False
    props.was_picking = False
    sync.clear()


def _refresh_features(context) -> None:
    props = context.scene.cadcore
    doc = get_client(context).call("describe_document")
    # `saved` and `undo_depth` are the kernel's: it owns the document and
    # writes the sidecar.
    props.saved = bool(doc.get("saved", True))
    props.undo_depth = int(doc.get("undo_depth", 0))
    props.revision = int(doc.get("revision", props.revision))
    keep = props.features[props.feature_index].name if \
        0 <= props.feature_index < len(props.features) else None
    props.features.clear()
    for f in doc["features"]:
        item = props.features.add()
        item.name = f["id"]
        item.kind = f["type"]
        item.summary = f.get("summary", "")
        item.suppressed = bool(f.get("suppressed", False))
    if keep is not None:
        for i, f in enumerate(props.features):
            if f.name == keep:
                props.feature_index = i
                break
    props.parameters.clear()
    bounds = doc.get("parameters_bounds") or {}
    for name, value in doc["parameters"].items():
        # Only numeric parameters get a field; an expression such as
        # "2*width" is skipped.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        item = props.parameters.add()
        item.name = name
        item.value = float(value)
        span = bounds.get(name)
        if span and len(span) == 2:
            item.bounded = True
            item.low, item.high = float(span[0]), float(span[1])
    meta = doc.get("meta") or {}
    if props.material != (meta.get("material") or ""):
        props.material = meta.get("material") or ""
    if props.colour != (meta.get("colour") or ""):
        props.colour = meta.get("colour") or ""
    # Publish the parameters as ID properties on the body for keyframes and
    # drivers.
    for body in sync.bodies():        # every part carries them, so a driver
        animate.publish(body, doc.get("parameters"), doc.get("parameters_bounds"))
    animate.remember(doc.get("parameters"))
    _refresh_feature_args(context, doc)


def _refresh_feature_args(context, doc=None) -> None:
    """Refill the selected feature's argument rows.

    The argument set and each kind come from the feature type's declaration,
    not from the values: a flag left at its default is absent from the document.
    """
    props = context.scene.cadcore
    _refresh_families(context)
    with refresh_guard():
        props.feature_args.clear()
        if not (0 <= props.feature_index < len(props.features)):
            return
        doc = doc or get_client(context).call("describe_document")
        fid = props.features[props.feature_index].name
        feature = next((f for f in doc["features"] if f["id"] == fid), None)
        if feature is None:
            return
        args = feature.get("args", {})
        parameters = doc.get("parameters", {})
        declared = _declared_args(props, feature.get("type", ""))

        def add(name, kind, value, parameter="", choices=()):
            item = props.feature_args.add()
            item.name = name
            item.kind = kind
            item.parameter = parameter
            item.choices = ", ".join(choices)
            if kind == "flag":
                item.flag = bool(value)
            elif kind == "number":
                item.value = float(value)
            else:
                item.text = "" if value is None else str(value)

        for key, spec in declared.items():
            kind = spec.get("kind")
            value = args.get(key, spec.get("default"))
            if kind == "one_of":
                # A `one_of` argument is drawn as the kind of the value it holds.
                kind = ("flag" if isinstance(value, bool) else
                        "number" if isinstance(value, (int, float))
                        or (isinstance(value, str) and value in parameters)
                        else "text" if isinstance(value, str) else "")
            if kind == "flag":
                add(key, "flag", value if value is not None else False)
            elif kind == "number":
                if isinstance(value, str) and value in parameters:
                    add(key, "number", parameters[value], parameter=value)
                elif isinstance(value, (int, float)) and not isinstance(value, bool):
                    add(key, "number", value)
            elif kind in ("text", "name") and isinstance(value, str):
                # `ref` arguments are not listed: they change by reordering
                # features, not by typing.
                add(key, "text", value, choices=spec.get("choices", ()))
            elif kind == "spec" and isinstance(value, dict):
                for sub, inner in value.items():
                    if isinstance(inner, (int, float)) and not isinstance(inner, bool):
                        add(f"{key}.{sub}", "number", inner)
                    elif isinstance(inner, str) and inner in parameters:
                        add(f"{key}.{sub}", "number", parameters[inner], parameter=inner)

        # Undeclared numeric arguments (a hand-edited document) are still shown.
        for key, value in args.items():
            if key in declared:
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                add(key, "number", value)


def _declared_args(props, kind: str) -> dict:
    for family in props.families:
        if family.name == kind:
            try:
                return json.loads(family.args or "{}")
            except ValueError:
                return {}
    return {}


CONSTRAINT_REFS = ("line", "lines", "point", "points", "circle", "arc", "of", "at")


def _refresh_families(context) -> None:
    """Fetch the feature type catalogue from the kernel, once per document."""
    props = context.scene.cadcore
    if len(props.families):
        return
    try:
        out = get_client(context).call("feature_types")
    except ServerError:
        return
    with refresh_guard():
        for kind in out["types"]:
            item = props.families.add()
            item.name = kind["name"]
            item.category = kind["category"]
            item.summary = kind["summary"]
            item.args = json.dumps(kind.get("args", {}))


def _refresh_constraints(context) -> None:
    """Refill the constraint rows for the selected sketch; a dimension gets an
    editable field."""
    props = context.scene.cadcore
    with refresh_guard():
        props.constraints.clear()
        props.sketch_name = ""
        props.sketch_dof = -1
        if not (0 <= props.feature_index < len(props.features)):
            return
        item = props.features[props.feature_index]
        if item.kind != "sketch":
            return
        try:
            out = get_client(context).call("sketch_constraints", sketch=item.name)
        except ServerError:
            return                              # not built this far: nothing to show
        props.sketch_name = item.name
        props.sketch_dof = -1 if out["dof"] is None else out["dof"]
        for constraint in out["constraints"]:
            row = props.constraints.add()
            row.name = constraint["type"]
            row.index = constraint["index"]
            row.detail = ", ".join(
                str(constraint[key]) if not isinstance(constraint[key], list)
                else "-".join(str(v) for v in constraint[key])
                for key in CONSTRAINT_REFS if key in constraint)
            row.has_value = "value" in constraint
            row.value_text = "" if not row.has_value else str(constraint["value"])


def said(detail: dict) -> str:
    """A refusal's detail as one short line: hint and error first, then the
    scalar values. Lists and dicts are left out."""
    if not detail:
        return ""
    parts = [str(detail[key]) for key in ("hint", "error") if detail.get(key)]
    for key, value in detail.items():
        if key in ("hint", "error") or isinstance(value, (list, dict, tuple)):
            continue
        parts.append("%s %s" % (key.replace("_", " "), value))
    return ", ".join(parts)[:220]


def push_undo(label: str, operator=None) -> None:
    """Push a Blender undo step for a kernel edit.

    Blender pushes a step for an ``UNDO`` operator only when it runs from an
    event; one called from Python (panel button, menu entry, test) pushes
    nothing. The step captures the scene, including `undo_depth`, which is
    what `on_undo` rolls the kernel back to; the geometry itself lives in
    the kernel process and is never in the memfile.
    """
    # Not twice: an operator Blender ran from a click (`options.is_invoke`)
    # pushes its own step when it finishes.
    if operator is not None and getattr(getattr(operator, "options", None), "is_invoke", False):
        return

    try:
        bpy.ops.ed.undo_push(message="CAD: %s" % label)
    except RuntimeError:
        pass                      # no screen to push against, as in `-b`


def set_error(context, exc: ServerError) -> str:
    """Put a refusal on the panel's status line and return its detail text.

    Used by paths with no operator to report through (a preview worker, a
    hover) as well as by `report_error`, so refusals read the same everywhere.
    """
    detail = said(exc.detail)
    props = context.scene.cadcore
    props.status = f"{exc.kind}: {exc.message}" + (" -- " + detail if detail else "")
    props.status_is_error = True
    return detail


def report_error(op, exc: ServerError) -> set:
    detail = set_error(bpy.context, exc)
    op.report({'ERROR'}, f"{exc.kind}: {exc.message} {detail}".strip())
    return {'CANCELLED'}


# -- driving the model from the timeline ----------------------------------------
@bpy.app.handlers.persistent
def on_frame(scene, depsgraph=None) -> None:
    """Frame-change handler: rebuild when an animated parameter moved.

    Runs only when `drive_on_frame` is on, no refresh is in progress (the
    panel writing values back would read as an edit), and a value changed.
    """
    props = getattr(scene, "cadcore", None)
    if props is None or not props.drive_on_frame or is_refreshing():
        return
    body = sync.body()
    changed = animate.differs(body) if body is not None else {}
    if not changed:
        return
    client = get_client(bpy.context)
    info = None
    for name, value in changed.items():
        try:
            info = client.call("set_parameter", name=name, value=value)
        except ServerError as exc:
            print("cadcore: %s = %g refused -- %s" % (name, value, exc.message))
            break
        # Noted per value after the kernel accepts it; noting a refused value
        # would stop `differs` ever offering it again.
        animate.note({name: value})
    if info is not None:
        with refresh_guard():
            _apply_build(bpy.context, info)


def wanted_revision(props) -> int:
    """The kernel revision Blender's undo just restored: in edit mode the
    body's mesh remembers it (an edit-mode step restores the mesh alone), else the scene."""
    body = sync.editing()
    remembered = sync.remembered_revision(body) if body is not None else None
    return int(props.revision) if remembered is None else remembered


def _walk_kernel(props) -> tuple[int, dict | None]:
    """Take the kernel to the wanted revision; the build to draw, or None
    when it was there already."""
    want = wanted_revision(props)
    info = get_client(bpy.context).call("goto_revision", revision=want)
    return want, (info if info.get("moved") else None)


_undone = [None]         # the build an undo restored, waiting for the timer that draws it


def _match_the_scene(props):
    """The kernel's parameters set to the restored scene's where they differ;
    the build reply, or None when they agree. The scene Blender restored is
    the truth: a slider's step is pushed before its throttled kernel edit."""
    from . import throttle

    client = get_client(bpy.context)
    theirs = client.call("describe_document").get("parameters", {})
    ours = {p.name: p.value for p in props.parameters if p.name in theirs}
    differ = {name: value for name, value in ours.items()
              if isinstance(theirs.get(name), (int, float))
              and abs(float(theirs[name]) - float(value)) > 1e-9}
    if not differ:
        return None
    throttle.forget()                        # an edit still in flight would undo this
    return client.call("set_parameters", values=differ)


def _reconcile() -> float | None:
    """Draw the document Blender's undo restored.

    Runs from a zero-second timer, not inside `undo_post`: rebuilding mesh
    datablocks while Blender is still finishing the undo crashes it.
    """
    scene = bpy.context.scene
    props = getattr(scene, "cadcore", None)
    if props is None or not (props.has_document or props.doc_path):
        return None
    try:
        want, info = _walk_kernel(props)
        waiting, _undone[0] = _undone[0], None
        if info is None and waiting is not None and waiting[0] == want:
            info = waiting[1]
        if info is not None:
            with refresh_guard():
                _apply_build(bpy.context, info)
                props.revision = want
        matched = _match_the_scene(props)
        if matched is not None:
            with refresh_guard():
                _apply_build(bpy.context, matched)
    except ServerError as exc:
        # nothing_to_undo past the kernel's 64 steps: the model stops there.
        print("cadcore: undo could not follow -- %s" % exc.message)
    return None


@bpy.app.handlers.persistent
def on_undo(scene) -> None:
    """Undo/redo handler: the kernel follows Blender's undo at once, the mesh a moment later.
    Walked here so that an operator run straight after (Adjust Last Operation, F9)
    edits the restored document and replaces the feature instead of stacking one."""
    props = getattr(scene, "cadcore", None)
    if props is None or not (props.has_document or props.doc_path):
        return
    throttle.forget()        # a slider edit still held is for the document undo just replaced
    try:
        want, info = _walk_kernel(props)
    except ServerError as exc:
        print("cadcore: undo could not follow -- %s" % exc.message)
        return
    if info is not None:
        _undone[0] = (want, info)
    if not bpy.app.timers.is_registered(_reconcile):
        bpy.app.timers.register(_reconcile, first_interval=0.0)


@bpy.app.handlers.persistent
def on_load(_dummy=None) -> None:
    """Load handler: the scene remembers a document the kernel does not hold.

    Clear everything derived and ask the user to open the document again, so
    the panel and the kernel cannot silently show different parts.
    """
    scene = getattr(bpy.context, "scene", None)
    props = getattr(scene, "cadcore", None) if scene else None
    if props is None:
        return
    remembered = props.doc_path
    props.has_document = False
    props.features.clear()
    props.requirements.clear()
    props.studies.clear()
    props.undo_depth = 0
    props.revision = 0
    if remembered:
        props.status = "this file remembers %s -- open it again to edit" % remembered
        props.doc_path = ""
    throttle.forget()


def guard_mesh() -> None:
    """If a Blender tool changed the body's mesh, draw the kernel's mesh again.

    The mesh is the kernel's. Knife, Loop Cut or a moved vertex change the
    picture but not the part, and the next rebuild would drop the change
    anyway. Better to drop it at once and say so, and point at Bake for
    anyone who wants to keep editing in Blender.
    """
    scene = getattr(bpy.context, "scene", None)
    props = getattr(scene, "cadcore", None) if scene else None
    if sync.body() is None or props is None or not props.has_body             or not sync.changed_by_hand():
        return None
    try:
        data = get_client(bpy.context).call("tessellate", deflection=props.deflection)
    except ServerError as exc:
        print("cadcore: could not redraw the body -- %s" % exc.message)
        return None
    sync.apply(data)
    props.status = ("the mesh is drawn by the CAD, so the change was undone. "
                    "Use the CAD tools, or Bake to edit it as a Blender mesh")
    props.status_is_error = True
    return None


@bpy.app.handlers.persistent
def on_depsgraph(scene, depsgraph) -> None:
    """Runs after every change in the scene; looks only at the body's mesh."""
    meshes = {ob.data for ob in sync.bodies() if ob.mode != 'EDIT'}
    if not meshes:
        return
    for update in depsgraph.updates:
        if update.is_updated_geometry and getattr(update.id, "original", None) in meshes:
            # not here: writing a mesh inside this handler is not safe
            if not bpy.app.timers.is_registered(guard_mesh):
                bpy.app.timers.register(guard_mesh, first_interval=0.0)
            return


def register_handler() -> None:
    if on_frame not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(on_frame)
    if on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(on_depsgraph)
    for hook in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if on_undo not in hook:
            hook.append(on_undo)
    if on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(on_load)


def unregister_handler() -> None:
    if on_frame in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(on_frame)
    if on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(on_depsgraph)
    for hook in (bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if on_undo in hook:
            hook.remove(on_undo)
    if on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(on_load)
    animate.forget()
