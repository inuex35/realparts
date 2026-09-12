"""Looking at the built body: triangles, frames, and what a selection means."""
from __future__ import annotations

from ... import features, names
from ...geometry import kernel
from ...errors import CadError
from ...geometry.core.measure import KINDS, dimension, face_frame
from ...geometry.core.query import select_edges
from ...geometry.io.tessellate import compact, tessellate


#: what `describe_faces` and `find_faces` understand; any other key is refused
#: by name, since a key nobody reads would filter nothing and call it a success
_FACE_QUERY = frozenset({"of_face", "shape", "parallel", "larger_than",
                         "smaller_than", "limit"})


def _matching(faces: list, query: dict) -> list:
    from ...errors import CadError
    from ...geometry.core.query import AXES

    unknown = sorted(set(query) - _FACE_QUERY)
    if unknown:
        raise CadError("bad_query",
                       "a face query does not understand: " + ", ".join(unknown),
                       {"unknown": unknown, "understood": sorted(_FACE_QUERY)})
    found = list(faces)
    if "of_face" in query:
        from ... import names as naming
        want = naming.base(str(query["of_face"]))
        found = [f for f in found if naming.base(f["name"]) == want]
    if "shape" in query:
        found = [f for f in found if f["shape"] == query["shape"]]
    if "parallel" in query:
        axis = AXES.get(str(query["parallel"]).lower())
        if axis is None:
            raise CadError("bad_query", "parallel takes an axis: x, y or z",
                           {"given": query["parallel"]})
        found = [f for f in found if "normal" in f
                 and abs(abs(sum(f["normal"][i] * axis[i] for i in range(3))) - 1.0) < 1e-6]
    if "larger_than" in query:
        found = [f for f in found if f["area_mm2"] > float(query["larger_than"])]
    if "smaller_than" in query:
        found = [f for f in found if f["area_mm2"] < float(query["smaller_than"])]
    if "limit" in query:
        found = found[:int(query["limit"])]
    return found


class InspectOps:
    """Mixed into :class:`cadcore.ops.session.Session`."""

    def op_part_of(self, face: str) -> dict:
        """Which assembled body a face belongs to, and what puts it where it is.

        ``positioner`` is the `translate` that places the body, if one does;
        ``mate`` the mate that positions it instead, which a drag has to respect.
        """
        doc = self._require_doc()
        self._ensure_body()
        canonical = self.body.canonical(face) if face in self.body.names else face
        head = canonical.split("/")[0]
        member = head.split(":")[0] if ":" in head else None
        feature_head = head.split(":")[-1].split("@")[0].split("~")[0].split("#")[0]
        assemble = next((f for f in doc.features if f.type == "assemble"), None)
        bodies = list(assemble.args.get("bodies", [])) if assemble else []
        ev = self.evaluator
        body = None
        for candidate in bodies:
            chain = ev.ancestors(candidate) | {candidate}
            if (member or feature_head) in chain or feature_head in chain:
                body = candidate
                break
        if body is None:
            if not bodies:
                raise CadError("no_assembly", "this document is one body; nothing to move it relative to",
                               {"face": face, "hint": "assemble two bodies, or open an assembly"})
            raise CadError("unknown_face", f"{face!r} belongs to no assembled body",
                           {"face": face, "bodies": bodies})
        chain = ev.ancestors(body) | {body}
        positioner = next((f.id for f in reversed(doc.features)
                           if f.type == "translate" and f.id in chain), None)
        mate = next((f.id for f in doc.features if f.type == "mate"
                     and f.args.get("move") in chain), None)
        if mate is None and assemble is not None:
            for m in assemble.args.get("mates") or []:
                faces = m.get("faces") or []
                if any(str(n).split(":")[0] == (member or body) for n in faces[:1]):
                    mate = "assemble.mates"
                    break
        offset = doc.feature(positioner).args.get("offset") if positioner else None
        return {"face": canonical, "body": body, "positioner": positioner, "mate": mate,
                "offset": offset,
                # evaluated as well: an offset may be `["dx", 0, 0]`, and a drag
                # needs the number it is starting from
                "offset_mm": ([float(doc.evaluate(v)) for v in offset] if offset else None)}

    def op_feature_types(self) -> dict:
        """Every feature type the kernel knows, with its family, a summary and its arguments; the menus are built from it."""
        return {"types": features.catalogue()}

    def op_render(self, path: str, view: str = "iso", width: int = 800,
                  height: int = 600, highlight: list | None = None) -> dict:
        """Draw the model to a PNG: `view` is iso, front, back, left, right, top or bottom; `highlight` colours faces by name."""
        from ...analysis.preview import render

        self._ensure_body()
        return render(self.body, self._path(path, writing=True), view=view,
                      width=width, height=height, highlight=highlight or [])

    def op_assistant_tools(self) -> dict:
        """The operations as tools for a language model: names, argument
        schemas, read-or-write hints, and the guide an assistant reads first."""
        from .. import catalogue

        return {"tools": catalogue.tools(type(self)), "instructions": catalogue.INSTRUCTIONS}

    def op_tessellate(self, deflection: float = 0.2, angular: float = 0.4) -> dict:   # deflection mm, angular radians
        """Triangles for the viewport, with the CAD name on every one; cached against the built body."""
        if self.body is None:
            self._rebuild()
        if self.body is None:
            return self._wire_of_the_unbuilt()
        key = (self._body_key(), round(float(deflection), 6), round(float(angular), 6))
        hit = self.meshes.get(key)
        if hit is not None:
            return hit
        mesh = compact(tessellate(self.body, deflection, angular))
        if len(self.meshes) > 6:
            self.meshes.clear()
        self.meshes[key] = mesh
        return mesh

    def _wire_of_the_unbuilt(self) -> dict:
        """What there is to draw before anything makes a solid: sketches as named polylines, work planes as squares."""
        edges = {}
        ev = self.evaluator
        if ev is not None:
            reach = 25.0
            for sketch, solved in ev.sketches.items():
                for name, points in solved.polylines().items():
                    edges[names.face(sketch, name)] = [list(p) for p in points]
                    reach = max([reach] + [abs(c) for p in points for c in p])
            for plane, frame in ev.planes.items():
                o, n, x = (frame[k] for k in ("origin", "normal", "x_axis"))
                y = (n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0])
                corners = [(-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)]
                edges[plane] = [[o[i] + reach * (u * x[i] + v * y[i]) for i in range(3)]
                                for u, v in corners]
        return {"vertices": [], "triangles": [], "triangle_face": [], "face_table": [],
                "normals": [], "edges": edges, "under_construction": True}

    def _body_key(self) -> str:
        """The content hash of whatever is currently built."""
        if self.evaluator is None:
            return "none"
        target = self.view_upto or self.doc.result or self.doc.features[-1].id
        return self.evaluator.keys.get(target, "none")

    def op_select_edges(self, query: dict) -> dict:
        """Edge names matching a query: which faces they lie on, how long they are, whether they are straight."""
        self._ensure_body()
        return {"edges": select_edges(self.body, query)}

    def op_describe_faces(self, query: dict | None = None) -> dict:
        """Every face at once: what it is, where it points, how big it is.

        ``query``  the same selector `select_edges` takes, applied to faces:
                   ``of_face`` for one, ``parallel`` for an axis, ``larger_than``
                   and ``smaller_than`` in square millimetres, ``limit``.
        """
        self._ensure_body()
        from ...geometry.core.naming import face_info

        out = []
        for name in sorted(self.body.face_names()):
            face = self.body.face(name)
            if face is None:
                continue
            info = face_info(face)
            entry = {"name": name, "shape": info.get("shape", "free"),
                     "area_mm2": round(info["area"], 4),
                     "centre": [round(c, 4) for c in info["centre"]]}
            if "normal" in info:
                entry["normal"] = [round(c, 6) for c in info["normal"]]
            if "axis" in info:
                entry["axis"] = [round(c, 6) for c in info["axis"]]
            if "radius" in info:
                entry["radius_mm"] = round(info["radius"], 4)
            out.append(entry)
        return {"faces": _matching(out, query or {})}

    def op_find_faces(self, query: dict) -> dict:
        """The names of the faces a selector matches -- the short answer."""
        return {"faces": [f["name"] for f in
                          self.op_describe_faces(query)["faces"]]}

    def op_face_frame(self, face: str) -> dict:
        """Where a face is and how it is oriented -- what drawing on it needs."""
        self._ensure_body()
        return face_frame(self.body, face)

    def op_section(self, face: str | None = None, offset: float = 0.0,
                   origin: list | None = None, normal: list | None = None,
                   deflection: float = 0.05) -> dict:
        """The curves and the area where a plane passes through the body; nothing is changed.

        The plane is ``offset`` mm off the named flat face along its normal,
        or given as ``origin`` and ``normal``.
        """
        self._ensure_body()
        if face:
            frame = face_frame(self.body, face)
            normal = frame["normal"]
            origin = [frame["origin"][i] + normal[i] * float(offset) for i in range(3)]
        elif normal is None:
            raise CadError("bad_arguments", "a section needs a face or a normal")
        else:
            origin = [float(c) for c in (origin or [0, 0, 0])]
        return kernel.section(self.body, origin, normal, deflection)

    def op_project(self, sketch: str, direction: list | None = None,
                   deflection: float = 0.05) -> dict:
        """Where a sketch's curves land on the body, projected along its normal.

        Nothing is changed: the curves come back as polylines, for a viewport
        to show where an emboss or a marking would go.
        """
        self._ensure_body()
        if not any(f.id == sketch and f.type == "sketch" for f in self.doc.features):
            raise CadError("unknown_sketch", f"no sketch called {sketch!r}",
                           {"known": [f.id for f in self.doc.features if f.type == "sketch"]})
        self.evaluator.prepare(sketch)          # a sketch nothing consumes is not built otherwise
        return kernel.projected(self.body, self.evaluator.sketch_of(sketch), direction, deflection)

    def op_curvature(self, face: str, at: list | None = None) -> dict:
        """How a face curves at a point: principal curvatures and radii, mean, Gaussian, the normal.

        ``at`` is a world point projected onto the face; the face's middle
        when left out. A flat face answers zero everywhere.
        """
        self._ensure_body()
        return kernel.curvature(self.body, face, at)

    def op_measure(self, kind: str, faces: list) -> dict:
        """Measure the model: two faces apart, the angle between them, a bore; the same function a drawing's dimension uses."""
        self._ensure_body()
        value = dimension(self.body, kind, list(faces))
        return {"kind": kind, "faces": list(faces), "value": round(value, 6),
                "unit": "deg" if kind == "angle" else
                        "mm^2" if kind == "area" else "mm"}

    def op_reply_style(self, names: str = "all") -> dict:
        """How much of the naming a build reply carries.

        ``names``  ``all`` for every face and edge name, ``changed`` for the
                   ones that appeared and went since the last build (an
                   assistant pays for every name it is told).
        """
        if names not in ("all", "changed"):
            from ...errors import CadError
            raise CadError("bad_parameter", "names is 'all' or 'changed'",
                           {"given": names})
        self.names_in_reply = names
        return {"names": names}

    def op_face_query_keys(self) -> dict:
        """What a face selector understands, so a caller never guesses."""
        return {"keys": sorted(_FACE_QUERY)}

    def op_measure_kinds(self) -> dict:
        """What can be measured -- so a menu is never a stale copy of the list."""
        return {"kinds": list(KINDS)}

    def _require_faces(self, names: list) -> None:
        """Every name has to resolve on the body as it stands now."""
        self._ensure_body()
        for name in names:
            if self.body.face(name) is None:
                raise CadError("unresolved_reference", f"no face named {name!r}",
                               {"available": self.body.face_names()})
