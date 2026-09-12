"""The OpenCASCADE names that moved between OCCT 7.9 and 8.0.

One place knows which binding is installed. Three things moved:
the NCollection typedefs went to `OCP.collections` under their template
names, `BRepTopAdaptor_FClass2d` went, and `TopoDS` dropped the `_s` that
marks a static method -- `TopoDS.Face_s` is `TopoDS.Face` there.
"""
from __future__ import annotations

from OCP.TopoDS import TopoDS

try:                                             # OCCT 8.0
    from OCP.collections import (                # noqa: F401
        Array1_gp_Pnt as Array1OfPnt,
        List_TopoDS_Shape as ListOfShape,
        IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher
        as IndexedDataMapOfShapeListOfShape)
    from OCP.IntTools import IntTools_FClass2d as FClass2d       # noqa: F401
    #: `TopoDS.Face_s` is written at 23 call sites and means the same thing on
    #: both. Spelled here rather than at every one of them.
    for _name in ("Face", "Edge", "Wire", "Shell", "Solid", "Vertex", "Shape",
                  "CompSolid", "Compound"):
        if hasattr(TopoDS, _name) and not hasattr(TopoDS, _name + "_s"):
            setattr(TopoDS, _name + "_s", getattr(TopoDS, _name))
except ImportError:                              # OCCT 7.9
    from OCP.TColgp import TColgp_Array1OfPnt as Array1OfPnt      # noqa: F401
    from OCP.TopTools import (                   # noqa: F401
        TopTools_ListOfShape as ListOfShape,
        TopTools_IndexedDataMapOfShapeListOfShape as IndexedDataMapOfShapeListOfShape)
    from OCP.BRepTopAdaptor import BRepTopAdaptor_FClass2d as FClass2d   # noqa: F401


def bounds(box) -> tuple:
    """``(xmin, ymin, zmin, xmax, ymax, zmax)`` of a ``Bnd_Box``.

    Not `Get`: 7.9 hands back the six numbers and 8.0 a `Bnd_Box::Limits` the
    binding cannot convert. The two corner points are on both.
    """
    low, high = box.CornerMin(), box.CornerMax()
    return (low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z())
