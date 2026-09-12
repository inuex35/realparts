"""GPU drawing shared by the modal tools.

Points use ``POINT_UNIFORM_COLOR``: the plain ``UNIFORM_COLOR`` shader never
writes a point size, so its dots come out one pixel or not at all depending
on the backend.
"""
from __future__ import annotations


def points(positions, colour, size: float) -> None:
    """Draw round dots of the given pixel size."""
    import gpu
    from gpu_extras.batch import batch_for_shader

    if not positions:
        return
    shader = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'POINTS', {"pos": list(positions)})
    shader.bind()
    shader.uniform_float("color", colour)
    try:
        shader.uniform_float("size", size)
    except ValueError:                      # some builds size points by state alone
        pass
    gpu.state.point_size_set(size)
    batch.draw(shader)


def line_strip(positions, colour) -> None:
    """Draw a polyline through the points; fewer than two draws nothing."""
    import gpu
    from gpu_extras.batch import batch_for_shader

    positions = list(positions)
    if len(positions) < 2:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": positions})
    shader.bind()
    shader.uniform_float("color", colour)
    batch.draw(shader)
