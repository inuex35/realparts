"""A headless parametric CAD core: design document -> OpenCASCADE -> STEP.

`build`, `build_file`, `describe` and `export_step` are loaded on first use
through `__getattr__`, so `import cadcore.model.document` does not load
OpenCASCADE. tests/test_architecture.py checks this by running the import in a
subprocess.
"""
from .model.document import Document, Feature  # noqa: F401
from .errors import CadError  # noqa: F401

__version__ = "0.2.4"

#: name -> the module it lives in. Loaded on first use, not on import.
_GEOMETRY = {"build": "evaluation.api", "build_file": "evaluation.api",
             "describe": "evaluation.api", "export_step": "evaluation.api"}

__all__ = ["Document", "Feature", "CadError", *sorted(_GEOMETRY)]


def __getattr__(name: str):
    if name in _GEOMETRY:
        from importlib import import_module

        return getattr(import_module("." + _GEOMETRY[name], __name__), name)
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


def __dir__() -> list:
    return sorted(set(globals()) | set(_GEOMETRY))
