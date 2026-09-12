"""A picture of the model, made without Blender.

A small orthographic renderer over the kernel's tessellation, so an assistant
driving the kernel over MCP can look at a part. The lines drawn are the part's
own named edges rather than the triangle mesh's; named faces can be
highlighted in a different colour.
"""
from __future__ import annotations


import numpy as np

from ..errors import CadError
from ..geometry.io.tessellate import tessellate

#: Camera direction for each named view, as the direction the camera looks along.
VIEWS = {
    "iso": (-1.0, -1.0, -0.8),
    "front": (0.0, 1.0, 0.0),
    "back": (0.0, -1.0, 0.0),
    "left": (1.0, 0.0, 0.0),
    "right": (-1.0, 0.0, 0.0),
    "top": (0.0, 0.0, -1.0),
    "bottom": (0.0, 0.0, 1.0),
}

BACKGROUND = (24, 26, 30)
BODY = (176, 180, 188)
PICKED = (232, 132, 48)
EDGE = (18, 20, 24)


def _basis(direction) -> tuple:
    """Right, up and forward for a camera looking along ``direction``."""
    forward = np.array(direction, dtype=float)
    forward /= np.linalg.norm(forward)
    world_up = np.array([0.0, 0.0, 1.0])
    if abs(float(forward @ world_up)) > 0.95:
        # looking down, +y is up the page; looking up (the bottom view) -y is,
        # so +x stays on the right as third-angle projection puts it
        world_up = np.array([0.0, 1.0 if forward[2] < 0 else -1.0, 0.0])
    right = np.cross(forward, world_up)
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    return right, up, forward


class Camera:
    """One orthographic fit shared by everything drawn, so vertices and edges
    go through the same mapping and land on the same pixels."""

    def __init__(self, points, direction, width, height, margin=0.08):
        self.right, self.up, self.forward = _basis(direction)
        self.width, self.height = width, height
        flat = np.stack([points @ self.right, points @ self.up], axis=1)
        low, high = flat.min(axis=0), flat.max(axis=0)
        span = np.maximum(high - low, 1e-9)
        room = 1.0 - 2 * margin
        self.scale = min(width * room / span[0], height * room / span[1])
        self.middle = (low + high) / 2

    def to_screen(self, points):
        flat = np.stack([points @ self.right, points @ self.up], axis=1)
        screen = (flat - self.middle) * self.scale
        screen[:, 0] += self.width / 2
        screen[:, 1] = self.height / 2 - screen[:, 1]   # y down, as an image is
        return screen

    def depth(self, points):
        return points @ self.forward


def _shade(normals, forward) -> np.ndarray:
    """Lambert against a light over the camera's shoulder, plus a little fill."""
    light = -forward + np.array([0.35, 0.2, 0.5])
    light /= np.linalg.norm(light)
    facing = np.abs(normals @ light)
    return 0.28 + 0.72 * facing


def render(body, path: str, view: str = "iso", width: int = 800, height: int = 600,
           highlight=(), deflection: float | None = None) -> dict:
    """Draw the body to a PNG and say what was drawn."""
    from PIL import Image

    if view not in VIEWS:
        # a CadError so the refusal carries a kind across the process boundary
        raise CadError("unknown_view", "no view called %r" % view,
                       {"views": sorted(VIEWS)})
    mesh = tessellate(body, deflection=deflection or 0.25, angular=0.35)
    points = np.asarray(mesh["vertices"], dtype=float)
    if not len(points):
        raise CadError("empty_mesh", "there is nothing to draw")
    faces = np.asarray(mesh["triangles"], dtype=int)
    normals = np.asarray(mesh["normals"], dtype=float)
    camera = Camera(points, VIEWS[view], width, height)
    screen, depth = camera.to_screen(points), camera.depth(points)

    picked = set(highlight or ())
    of_face = mesh.get("triangle_face") or []
    colour = np.tile(np.array(BODY, dtype=float), (len(faces), 1))
    if picked:
        wanted = np.array([name in picked for name in of_face])
        colour[wanted] = PICKED

    # per-triangle normals from the vertices: the shared vertex normals average
    # across CAD edges and round off the corners
    a, b, c = (points[faces[:, i]] for i in range(3))
    face_normal = np.cross(b - a, c - a)
    length = np.linalg.norm(face_normal, axis=1, keepdims=True)
    face_normal = np.divide(face_normal, np.maximum(length, 1e-12))
    if len(normals) == len(points):                 # keep the sign the kernel meant
        agree = np.sign(np.einsum("ij,ij->i", face_normal, normals[faces[:, 0]]))
        face_normal *= np.where(agree == 0, 1.0, agree)[:, None]
    tone = _shade(face_normal, camera.forward)

    image = np.tile(np.array(BACKGROUND, dtype=float), (height, width, 1))
    zbuffer = np.full((height, width), np.inf)
    for index in range(len(faces)):
        _triangle(image, zbuffer, screen[faces[index]], depth[faces[index]],
                  colour[index] * tone[index])
    _edges(image, zbuffer, mesh.get("edges") or {}, camera)

    Image.fromarray(np.clip(image, 0, 255).astype(np.uint8)).save(path)
    return {"path": path, "view": view, "width": width, "height": height,
            "triangles": int(len(faces)),
            "highlighted": sorted(picked & set(of_face)) if picked else []}


def _triangle(image, zbuffer, corners, depth, colour) -> None:
    """One triangle, z-buffered, over the pixels its own box covers."""
    height, width = zbuffer.shape
    lo = np.floor(corners.min(axis=0)).astype(int)
    hi = np.ceil(corners.max(axis=0)).astype(int) + 1
    x0, y0 = max(lo[0], 0), max(lo[1], 0)
    x1, y1 = min(hi[0], width), min(hi[1], height)
    if x1 <= x0 or y1 <= y0:
        return
    (ax, ay), (bx, by), (cx, cy) = corners
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if abs(area) < 1e-12:
        return
    ys, xs = np.mgrid[y0:y1, x0:x1]
    w0 = ((bx - ax) * (ys - ay) - (by - ay) * (xs - ax)) / area
    w1 = ((xs - ax) * (cy - ay) - (ys - ay) * (cx - ax)) / area
    inside = (w0 >= 0) & (w1 >= 0) & (w0 + w1 <= 1)
    if not inside.any():
        return
    here = depth[0] + w1 * (depth[1] - depth[0]) + w0 * (depth[2] - depth[0])
    nearer = inside & (here < zbuffer[y0:y1, x0:x1])
    if not nearer.any():
        return
    zbuffer[y0:y1, x0:x1][nearer] = here[nearer]
    image[y0:y1, x0:x1][nearer] = colour


def _edges(image, zbuffer, edges, camera) -> None:
    """Draw the part's own edges over the shading, depth-tested with a bias.

    An edge lies exactly on the faces that meet there, so without the bias it
    loses the depth test about half the time and comes out dashed.
    """
    finite = zbuffer[np.isfinite(zbuffer)]
    span = float(np.ptp(finite)) if finite.size else 1.0
    bias = max(span * 0.01, 1e-6)
    height, width = zbuffer.shape
    for polyline in edges.values():
        line = np.asarray(polyline, dtype=float)
        if len(line) < 2:
            continue
        pixels = camera.to_screen(line)
        depth = camera.depth(line)
        for i in range(len(pixels) - 1):
            _line(image, zbuffer, pixels[i], pixels[i + 1], depth[i], depth[i + 1],
                  bias, width, height)


#: Edge width in pixels. At one pixel an outline reads as a shading artefact.
EDGE_WIDTH = 2


def _line(image, zbuffer, start, end, z0, z1, bias, width, height) -> None:
    steps = int(max(abs(end[0] - start[0]), abs(end[1] - start[1]))) + 1
    if steps > 4000:
        return
    t = np.linspace(0.0, 1.0, steps)
    xs = np.rint(start[0] + (end[0] - start[0]) * t).astype(int)
    ys = np.rint(start[1] + (end[1] - start[1]) * t).astype(int)
    zs = z0 + (z1 - z0) * t
    spread = range(EDGE_WIDTH)
    for dx in spread:
        for dy in spread:
            px, py = xs + dx, ys + dy
            on = (px >= 0) & (px < width) & (py >= 0) & (py < height)
            if not on.any():
                continue
            ax, ay, az = px[on], py[on], zs[on]
            visible = az <= zbuffer[ay, ax] + bias
            image[ay[visible], ax[visible]] = EDGE
