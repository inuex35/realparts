"""Simulation on top of the CAD core: mesh from named faces, then solve."""
from .materials import LIBRARY, Material, get  # noqa: F401
from .mesh import MeshedBody, build_mesh  # noqa: F401
from .studies.structural import Result, StaticStructural  # noqa: F401

__all__ = ["StaticStructural", "Result", "build_mesh", "MeshedBody", "Material", "get", "LIBRARY"]
