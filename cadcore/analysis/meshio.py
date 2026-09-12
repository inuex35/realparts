"""Mesh output: STL, 3MF and OBJ, written directly from the same tessellation the
viewport uses, so what a slicer sees is what the viewport showed.
"""
from __future__ import annotations

import struct
import zipfile
from html import escape
from pathlib import Path

from ..errors import CadError
from ..geometry.io.tessellate import tessellate

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>
"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0"
    Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def _mesh(body, deflection: float, angular: float) -> dict:   # deflection mm, angular radians
    mesh = tessellate(body, deflection, angular)
    if not mesh["triangles"]:
        raise CadError("empty_mesh", "the body tessellated to nothing",
                       {"deflection": deflection})
    return mesh


def write_stl(body, path: str, deflection: float = 0.05, angular: float = 0.3,   # deflection mm, angular radians
              binary: bool = True) -> dict:
    """Write the body as STL, binary by default (ASCII with ``binary=False``)."""
    mesh = _mesh(body, deflection, angular)
    vertices, triangles = mesh["vertices"], mesh["triangles"]

    def normal(tri):
        a, b, c = (vertices[i] for i in tri)
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        n = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2],
             u[0] * v[1] - u[1] * v[0]]
        length = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 or 1.0
        return [c / length for c in n]

    if binary:
        out = bytearray()
        out += b"RealParts".ljust(80, b"\0")
        out += struct.pack("<I", len(triangles))
        for tri in triangles:
            out += struct.pack("<3f", *normal(tri))
            for index in tri:
                out += struct.pack("<3f", *vertices[index])
            out += struct.pack("<H", 0)
        Path(path).write_bytes(bytes(out))
    else:
        lines = ["solid RealParts"]
        for tri in triangles:
            n = normal(tri)
            lines.append(f"  facet normal {n[0]:g} {n[1]:g} {n[2]:g}")
            lines.append("    outer loop")
            for index in tri:
                v = vertices[index]
                lines.append(f"      vertex {v[0]:g} {v[1]:g} {v[2]:g}")
            lines.extend(["    endloop", "  endfacet"])
        lines.append("endsolid RealParts")
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": path, "triangles": len(triangles), "vertices": len(vertices),
            "bytes": Path(path).stat().st_size}


def write_3mf(body, path: str, deflection: float = 0.05, angular: float = 0.3,   # deflection mm, angular radians
              name: str = "part") -> dict:
    """Write the body as 3MF; the model is declared in millimetres."""
    mesh = _mesh(body, deflection, angular)
    vertices, triangles = mesh["vertices"], mesh["triangles"]

    rows = [f'<vertex x="{v[0]:.6g}" y="{v[1]:.6g}" z="{v[2]:.6g}"/>' for v in vertices]
    faces = [f'<triangle v1="{t[0]}" v2="{t[1]}" v3="{t[2]}"/>' for t in triangles]
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xml:lang="en-US" '
        'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        '  <metadata name="Application">RealParts</metadata>\n'
        f'  <metadata name="Title">{escape(name, quote=False)}</metadata>\n'
        '  <resources>\n'
        '    <object id="1" type="model">\n'
        '      <mesh>\n'
        '        <vertices>\n          ' + "\n          ".join(rows) + '\n'
        '        </vertices>\n'
        '        <triangles>\n          ' + "\n          ".join(faces) + '\n'
        '        </triangles>\n'
        '      </mesh>\n'
        '    </object>\n'
        '  </resources>\n'
        '  <build>\n    <item objectid="1"/>\n  </build>\n'
        '</model>\n')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", RELS)
        archive.writestr("3D/3dmodel.model", model)
    return {"path": path, "triangles": len(triangles), "vertices": len(vertices),
            "bytes": Path(path).stat().st_size}


def write_obj(body, path: str, deflection: float = 0.05, angular: float = 0.3) -> dict:   # deflection mm, angular radians
    """Write the body as OBJ, one group per named face, so the names survive."""
    mesh = _mesh(body, deflection, angular)
    vertices, triangles, owners = mesh["vertices"], mesh["triangles"], mesh["triangle_face"]
    lines = ["# RealParts, millimetres"]
    lines += [f"v {v[0]:.6g} {v[1]:.6g} {v[2]:.6g}" for v in vertices]
    current = None
    for tri, owner in zip(triangles, owners):
        if owner != current:
            current = owner
            lines.append("g " + owner.replace(" ", "_"))
        lines.append("f %d %d %d" % (tri[0] + 1, tri[1] + 1, tri[2] + 1))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": path, "triangles": len(triangles), "vertices": len(vertices),
            "bytes": Path(path).stat().st_size}
