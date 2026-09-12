"""STEP and IGES, in and out.

An imported solid has no history to inherit names from, so its faces are named
for what they are. That is deterministic for a given file but not stable across
a re-export from another CAD system.
"""
from __future__ import annotations

import contextlib
import os

from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.IGESControl import IGESControl_Reader, IGESControl_Writer
from OCP.Interface import Interface_Static
from OCP.STEPControl import (STEPControl_Reader, STEPControl_StepModelType,
                             STEPControl_Writer)
from OCP.TopoDS import TopoDS

from ... import names
from ...errors import CadError
from ..core.naming import Body, faces_of, role_names

__all__ = ["import_step", "import_iges", "export_step", "export_iges", "assembly_tree",
           "import_brep", "export_brep", "heal"]


def import_step(feature_id: str, path: str, healed: bool = False,
                tolerance: float | None = None) -> Body:
    """Read a STEP file and name every face from its geometry.

    Planes are named by axis, cylinders as sides, the rest by position. With
    ``healed`` the shape is repaired first (gaps sewn, tolerances brought up
    to ``tolerance`` mm, open shells closed into solids where they can be).
    A part's colour and material, when the file carries them, come back in
    the body's notes by part.
    """
    if not os.path.exists(path):
        raise CadError("file_not_found", f"no STEP file at {path!r}")
    reader = STEPControl_Reader()
    if reader.ReadFile(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise CadError("step_read_failed", f"OCCT could not read {path!r}")
    reader.TransferRoots()
    if reader.NbShapes() < 1:
        raise CadError("empty_step", f"{path!r} holds no transferable shape")
    shape = reader.OneShape()
    faces = faces_of(shape)
    if not faces:
        raise CadError("empty_step", f"{path!r} holds no faces")
    parts, looks = _step_components(path)
    report = None
    if parts:
        # an assembly: each part's faces are named under the part's own name,
        # the way this kernel scopes its own assemblies
        named, shapes = [], []
        for scope, part in parts:
            if healed:
                part, _ = heal(part, tolerance)
            shapes.append(part)
            named += [(names.scoped(scope, n), f)
                      for n, f in role_names(feature_id, faces_of(part))]
        body = Body(_compound(shapes), named)
    else:
        if healed:
            shape, report = heal(shape, tolerance)
        body = Body(shape, role_names(feature_id, faces_of(shape)))
        looks = {feature_id: looks[key] for key in looks if key == ""}
    colours = {k: v["colour"] for k, v in looks.items() if v.get("colour")}
    materials = {k: v["material"] for k, v in looks.items() if v.get("material")}
    if colours:
        body.notes["colours"] = colours
    if materials:
        body.notes["materials"] = materials
    if healed:
        body.notes["import"] = {"path": os.path.basename(path), "healed": True,
                                **(report or {})}
    return body


def heal(shape, tolerance: float | None = None) -> tuple:
    """The shape repaired: ``(shape, report)``.

    ShapeFix_Shape fixes wires, faces and shells within the tolerance; a shell
    that is closed but not a solid is sewn and made one.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.ShapeFix import ShapeFix_Shape, ShapeFix_Solid
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    tolerance = float(tolerance) if tolerance else 1e-3
    fixer = ShapeFix_Shape(shape)
    fixer.SetPrecision(tolerance)
    fixer.SetMaxTolerance(max(tolerance, 1e-2))
    with _quietly():
        fixer.Perform()
    fixed = fixer.Shape()
    solids = TopExp_Explorer(fixed, TopAbs_ShapeEnum.TopAbs_SOLID)
    made_solid = False
    if not solids.More() and faces_of(fixed):
        sewing = BRepBuilderAPI_Sewing(tolerance)
        sewing.Load(fixed)
        with _quietly():
            sewing.Perform()
        sewn = sewing.SewedShape()
        shells = TopExp_Explorer(sewn, TopAbs_ShapeEnum.TopAbs_SHELL)
        if shells.More() and sewing.NbFreeEdges() == 0:
            solid = ShapeFix_Solid()
            fixed = solid.SolidFromShell(TopoDS.Shell_s(shells.Current()))
            made_solid = True
        else:
            fixed = sewn
    valid = BRepCheck_Analyzer(fixed).IsValid()
    return fixed, {"tolerance_mm": tolerance, "made_solid": made_solid, "valid": bool(valid)}


def _step_components(path: str) -> tuple:
    """``(scope, shape)`` per leaf part of a STEP assembly, placed; empty for one solid.

    The second value is the looks of every label: ``{scope: {"colour", "material"}}``,
    with ``""`` for a file that is one solid.
    """
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from OCP.TDF import TDF_ChildIterator, TDF_Label

    doc = _xcaf_document()
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetMatMode(True)
    with _quietly():
        if reader.ReadFile(path) != IFSelect_ReturnStatus.IFSelect_RetDone \
                or not reader.Transfer(doc):
            return [], {}
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    looks: dict = {}

    def walk(label, scope: list, out: list) -> None:
        it = TDF_ChildIterator(label, False)
        while it.More():
            child = it.Value()
            referred = TDF_Label()
            if not XCAFDoc_ShapeTool.GetReferredShape_s(child, referred):
                it.Next()
                continue
            here = _label_name(child) or _label_name(referred) or f"part{child.Tag()}"
            here = here.replace(names.SCOPE, "_").replace(names.ROLE, "_")
            if XCAFDoc_ShapeTool.IsAssembly_s(referred):
                # the referred assembly's components carry their own
                # locations relative to it; the shape tool composes them
                walk(referred, scope + [here], out)
            else:
                scoped = names.SCOPE.join(scope + [here])
                out.append((scoped, XCAFDoc_ShapeTool.GetShape_s(child)))
                looks[scoped] = _looks(doc, referred, XCAFDoc_ShapeTool.GetShape_s(referred))
            it.Next()

    out: list = []
    it = TDF_ChildIterator(tool.Label(), False)
    while it.More():
        root = it.Value()
        if XCAFDoc_ShapeTool.IsFree_s(root):
            if XCAFDoc_ShapeTool.IsAssembly_s(root):
                walk(root, [], out)
            else:
                looks.setdefault("", _looks(doc, root, XCAFDoc_ShapeTool.GetShape_s(root)))
        it.Next()
    # a nested assembly's parts come back placed in their own assembly's frame;
    # re-placing them with the outer location is what GetShape on the
    # component already did for the first level, so deeper levels are moved by
    # the located shapes of their assembly components
    return out, looks


def _looks(doc, label, shape) -> dict:
    """The colour (#rrggbb) and material name on a shape's label, or an empty dict."""
    from OCP.Quantity import Quantity_Color
    from OCP.TDataStd import TDataStd_TreeNode
    from OCP.XCAFDoc import (XCAFDoc, XCAFDoc_ColorTool, XCAFDoc_ColorType, XCAFDoc_DocumentTool,
                             XCAFDoc_Material)

    out: dict = {}
    colours = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    colour = Quantity_Color()
    for kind in (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen):
        if colours.GetColor(shape, kind, colour) or XCAFDoc_ColorTool.GetColor_s(label, kind, colour):
            out["colour"] = "#%02x%02x%02x" % tuple(
                max(0, min(255, round(c * 255))) for c in (colour.Red(), colour.Green(), colour.Blue()))
            break
    node = TDataStd_TreeNode()
    # FindAttribute segfaults on a label without the tree node: ask first
    if label.IsAttribute(XCAFDoc.MaterialRefGUID_s()) \
            and label.FindAttribute(XCAFDoc.MaterialRefGUID_s(), node) and node.HasFather():
        material = XCAFDoc_Material()
        if node.Father().Label().FindAttribute(XCAFDoc_Material.GetID_s(), material):
            name = material.GetName()
            if name is not None and name.ToCString():
                out["material"] = name.ToCString()
    return out


def _label_name(label) -> str | None:
    from OCP.TDataStd import TDataStd_Name

    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        return attr.Get().ToExtString()
    return None


def _xcaf_document():
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application

    kind = TCollection_ExtendedString("MDTV-XCAF")
    doc = TDocStd_Document(kind)
    XCAFApp_Application.GetApplication_s().NewDocument(kind, doc)
    return doc


def _compound(shapes: list):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    out = TopoDS_Compound()
    builder.MakeCompound(out)
    for shape in shapes:
        builder.Add(out, shape)
    return out


def assembly_tree(body: Body) -> dict | None:
    """The parts of an assembly by scope: ``{"base": solid, "wheel": {"hub": solid}}``.

    Read off the face names, which is where this kernel keeps the assembly: a
    solid whose faces are ``base:plate/+z`` is the part ``base``, and one
    named ``wide:cone/side`` sits inside the sub-assembly ``wide``. A solid
    with no scope is not part of an assembly, and a body with none is None.
    A pattern of a part gives ``bush``, ``bush~1`` and so on.
    """
    from OCP.TopAbs import TopAbs_ShapeEnum
    from OCP.TopExp import TopExp_Explorer

    solids, explorer = [], TopExp_Explorer(body.shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    while explorer.More():
        solids.append(explorer.Current())
        explorer.Next()
    if len(solids) < 2:
        return None
    tree: dict = {}
    for solid in solids:
        parsed = [names.parse(n) for n in (body.name_of(f) for f in faces_of(solid)) if n]
        scopes = {p.scope for p in parsed if p.scope}
        if not scopes:
            if not parsed:
                return None
            # a solid modelled in the assembly's own document: named for the
            # feature that made it, beside the parts brought in
            scope = parsed[0].feature
        else:
            scope = sorted(scopes, key=len)[0]
        path = scope.split(names.SCOPE)
        copies = {p.instance for p in parsed if p.scope == scope}
        if copies and None not in copies and len(copies) == 1:
            path[-1] += names.INSTANCE + str(copies.pop())
        here = tree
        for step in path[:-1]:
            here = here.setdefault(step, {})
        leaf = path[-1]
        while leaf in here:                      # two solids in one part: keep both
            leaf += "_"
        here[leaf] = solid
    return tree


@contextlib.contextmanager
def _quietly():
    """OCCT's STEP assembly writer narrates its transfer on stdout; keep only failures."""
    from OCP.Message import Message, Message_Gravity

    printers = Message.DefaultMessenger_s().Printers()
    was = [(printers.Value(i), printers.Value(i).GetTraceLevel())
           for i in range(1, printers.Size() + 1)]
    for printer, _ in was:
        printer.SetTraceLevel(Message_Gravity.Message_Fail)
    try:
        yield
    finally:
        for printer, level in was:
            printer.SetTraceLevel(level)


def _export_assembly(tree: dict, path: str, name: str, looks: dict | None = None) -> None:
    """``looks`` is ``{scope: {"colour": "#rrggbb", "material": name}}``; ``""`` is the root."""
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    doc = _xcaf_document()
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    looks = looks or {}

    def label(node, called: str, scope: str):
        if isinstance(node, dict):
            here = tool.NewShape()
            for child_name, child in node.items():
                child_scope = names.SCOPE.join(s for s in (scope, child_name) if s)
                component = tool.AddComponent(here, label(child, child_name, child_scope),
                                              TopLoc_Location())
                TDataStd_Name.Set_s(component, TCollection_ExtendedString(child_name))
        else:
            here = tool.AddShape(node, False)
        TDataStd_Name.Set_s(here, TCollection_ExtendedString(called))
        _dress(doc, here, looks.get(scope) or {})
        return here

    label(tree, name, "")
    tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.SetColorMode(True)
    writer.SetMaterialMode(True)
    with _quietly():
        if not writer.Transfer(doc):
            raise CadError("export_failed", "the STEP writer could not translate this assembly",
                           {"path": path})
        if writer.Write(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise CadError("export_failed", "STEP writer reported an error", {"path": path})


def export_iges(body: Body, path: str, unit: str = "MM") -> None:
    """Write the body as IGES BRep entities."""
    writer = IGESControl_Writer(unit, 1)          # 1 = BRep entities, not surfaces
    writer.AddShape(body.shape)
    writer.ComputeModel()
    if not writer.Write(path):
        raise CadError("export_failed", "IGES writer reported an error", {"path": path})


def import_iges(feature_id: str, path: str, healed: bool = False,
                tolerance: float | None = None) -> Body:
    """Read an IGES file; faces are named from their geometry as for STEP.

    IGES mostly carries loose surfaces: ``healed`` sews them and makes a
    solid when they close.
    """
    if not os.path.exists(path):
        raise CadError("file_not_found", f"no IGES file at {path!r}")
    reader = IGESControl_Reader()
    if reader.ReadFile(path) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise CadError("iges_read_failed", f"OCCT could not read {path!r}")
    reader.TransferRoots()
    shape = reader.OneShape()
    faces = faces_of(shape)
    if not faces:
        raise CadError("empty_iges", f"{path!r} holds no faces",
                       {"hint": "IGES often carries surfaces, not solids"})
    report = None
    if healed:
        shape, report = heal(shape, tolerance)
    body = Body(shape, role_names(feature_id, faces_of(shape)))
    if healed:
        body.notes["import"] = {"path": os.path.basename(path), "healed": True, **report}
    return body


def import_brep(feature_id: str, path: str) -> Body:
    """Read OCCT's own .brep format; faces are named from their geometry."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    if not os.path.exists(path):
        raise CadError("file_not_found", f"no BREP file at {path!r}")
    shape = TopoDS_Shape()
    with open(path, "rb") as handle:
        try:
            BRepTools.Read_s(shape, handle, BRep_Builder())
        except Exception as exc:                                    # noqa: BLE001
            raise CadError("brep_read_failed", f"OCCT could not read {path!r}",
                           {"reason": str(exc).strip() or type(exc).__name__}) from exc
    faces = faces_of(shape)
    if shape.IsNull() or not faces:
        raise CadError("brep_read_failed", f"{path!r} holds no faces")
    return Body(shape, role_names(feature_id, faces))


def export_brep(body: Body, path: str) -> None:
    """Write OCCT's own .brep format: exact, and the only format that loses nothing."""
    from OCP.BRepTools import BRepTools

    try:
        with open(path, "wb") as handle:
            BRepTools.Write_s(body.shape, handle)
    except Exception as exc:                                        # noqa: BLE001
        raise CadError("export_failed", "BREP writer reported an error",
                       {"path": path, "reason": str(exc).strip() or type(exc).__name__}) from exc


def _dress(doc, label, look: dict) -> None:
    """Put a colour and a material on a label, when the look names them."""
    from OCP.Quantity import Quantity_Color, Quantity_TypeOfColor
    from OCP.TCollection import TCollection_HAsciiString
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    colour = look.get("colour")
    if colour:
        r, g, b = rgb_of(colour)
        XCAFDoc_DocumentTool.ColorTool_s(doc.Main()).SetColor(
            label, Quantity_Color(r, g, b, Quantity_TypeOfColor.Quantity_TOC_RGB),
            XCAFDoc_ColorType.XCAFDoc_ColorSurf)
    material = look.get("material")
    if material:
        materials = XCAFDoc_DocumentTool.MaterialTool_s(doc.Main())
        made = materials.AddMaterial(TCollection_HAsciiString(str(material)),
                                     TCollection_HAsciiString(""), 0.0,
                                     TCollection_HAsciiString(""), TCollection_HAsciiString(""))
        materials.SetMaterial(label, made)


def rgb_of(colour: str) -> tuple:
    """``#rrggbb`` as three numbers in 0..1, or a refusal."""
    text = str(colour).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    try:
        if len(text) != 6:
            raise ValueError
        return tuple(int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        raise CadError("bad_parameter", f"{colour!r} is not a colour like #rrggbb") from None


def looks_of(body: Body) -> dict:
    """``{scope: {"colour", "material"}}`` from the body's notes; ``""`` is the whole body."""
    out: dict = {}
    for kind, key in (("colours", "colour"), ("materials", "material")):
        for scope, value in (body.notes.get(kind) or {}).items():
            out.setdefault(scope, {})[key] = value
    return out


def export_step(body: Body, path: str, unit: str = "MM", name: str | None = None,
                looks: dict | None = None) -> None:
    """Write STEP. An assembly goes out as one, each part a named component.

    A part's colour and material (``notes["colours"]``, ``notes["materials"]``)
    go with it; ``looks`` adds or overrides them, ``""`` being the whole body.
    """
    Interface_Static.SetCVal_s("write.step.unit", unit)
    tree = assembly_tree(body)
    looks = {**looks_of(body), **{k: v for k, v in (looks or {}).items() if v}}
    if tree:
        _export_assembly(tree, path, name or "assembly", looks)
        return
    if looks:
        _export_assembly(body.shape, path, name or "part",
                         {"": looks.get("") or next(iter(looks.values()))})
        return
    writer = STEPControl_Writer()
    # check the transfer status: a shape that cannot be translated leaves the
    # writer empty, and `Write` then reports success on a file with no geometry
    with _quietly():
        moved = writer.Transfer(body.shape, STEPControl_StepModelType.STEPControl_AsIs)
        if str(moved).rsplit(".", 1)[-1] != "IFSelect_RetDone":
            raise CadError("export_failed",
                           "the STEP writer could not translate this shape",
                           {"path": path, "status": str(moved)})
        if writer.Write(path) != 1:
            raise CadError("export_failed", "STEP writer reported an error", {"path": path})
