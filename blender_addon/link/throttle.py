"""Coalesce parameter edits so a slider drag does not queue a rebuild per tick.

Edits are keyed; the newest value under a key replaces the held one. The first
edit in a while runs immediately, later ones run at most every `EVERY` seconds
during the drag and once more after `QUIET` seconds of silence, so the last
value always runs. `bpy.app.timers` runs on the main thread, so nothing here
touches `bpy` from another thread.
"""
from __future__ import annotations

import time

import bpy

#: seconds of quiet before a held edit runs.
QUIET = 0.10
#: minimum seconds between runs during a drag.
EVERY = 0.25

_held: dict = {}          # key -> the work, most recent wins
_ran: dict = {}           # key -> when it last actually ran
_armed = False
#: set while a held edit runs, so `flush` (called from `state.get_client`)
#: does not re-enter.
_running = False


def soon(key: str, work) -> None:
    """Run ``work`` now or later, replacing any work already held under ``key``."""
    now = time.monotonic()
    if now - _ran.get(key, 0.0) >= EVERY:
        _ran[key] = now
        _held.pop(key, None)
        _run(work)
        return
    _held[key] = work
    _arm()


def _run(work) -> None:
    global _running
    _running = True
    try:
        work()
    finally:
        _running = False


def _arm() -> None:
    global _armed
    if _armed or not hasattr(bpy.app, "timers"):
        return
    _armed = True
    bpy.app.timers.register(_beat, first_interval=QUIET)


def _beat() -> float | None:
    """Run what is held, or ask to be called again if a drag is still going."""
    global _armed
    if not _held:
        _armed = False
        return None
    now = time.monotonic()
    for key in list(_held):
        if now - _ran.get(key, 0.0) < QUIET:
            return QUIET               # still moving: wait for it to settle
        work = _held.pop(key)
        _ran[key] = now
        try:
            _run(work)
        except Exception as exc:                                    # noqa: BLE001
            print("cadcore: held edit failed --", exc)
    _armed = bool(_held)
    return QUIET if _armed else None


def flush() -> None:
    """Run everything held, now.

    Called from `state.get_client` before every kernel call: a held edit is a
    change the document has not received, so a save, undo or export would
    otherwise act on stale state.
    """
    if _running:
        return                # the held edit is what is running; let it finish
    while _held:
        key, work = _held.popitem()
        _ran[key] = time.monotonic()
        _run(work)


def forget() -> None:
    """Drop everything held without running it (the document is going away)."""
    _held.clear()
    _ran.clear()


def pending() -> int:
    return len(_held)
