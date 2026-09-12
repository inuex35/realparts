"""Progress reporting for long operations (`simulate`, `optimize`), when a sink is installed.

A real loop reports as it finishes a real stage; the total is declared only
where it is known first. The sink is a `ContextVar`, so one caller's session
cannot report into another's stream, and with none installed `step` is a return.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager

_SINK: contextvars.ContextVar = contextvars.ContextVar("cadcore.progress",
                                                       default=None)


class _Run:
    """One operation's worth of reporting: a count, and where it goes."""

    def __init__(self, send):
        self.send = send
        self.done = 0
        self.expected: int | None = None

    def total(self, expected: int) -> None:
        # first declaration wins: the outermost loop owns the total, and an
        # inner loop redeclaring it would move the bar backwards
        if self.expected is None and expected > 0:
            self.expected = int(expected)

    def step(self, message: str) -> None:
        self.done += 1
        self.send(self.done, self.expected, str(message))


@contextmanager
def reported_to(send):
    """Report this context's progress to `send(done, total, message)`."""
    token = _SINK.set(_Run(send))
    try:
        yield
    finally:
        _SINK.reset(token)


def total(expected: int) -> None:
    """How many steps this run expects, where that is known before it starts."""
    run = _SINK.get()
    if run is not None:
        run.total(expected)


def step(message: str) -> None:
    """One real stage finished. Called from the loop that finished it."""
    run = _SINK.get()
    if run is not None:
        run.step(message)


def watched() -> bool:
    """Whether a sink is installed, for messages that are expensive to build."""
    return _SINK.get() is not None
