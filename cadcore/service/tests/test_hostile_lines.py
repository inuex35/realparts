"""The line server and the bridge survive a line that is not a request.

Deep nesting, bytes outside utf-8, and a line the size of a file are each
answered as `bad_json` and the next line is read.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _serve(lines: list[bytes], timeout: float = 120.0) -> list:
    proc = subprocess.run([sys.executable, "-m", "cadcore.service.server", "--unfenced"],
                          input=b"\n".join(lines) + b"\n", capture_output=True,
                          cwd=REPO, env=dict(os.environ, PYTHONPATH=REPO), timeout=timeout)
    assert proc.returncode == 0, proc.stderr[-800:]
    return [json.loads(l) for l in proc.stdout.decode("utf-8").splitlines() if l.strip()]


def test_a_deeply_nested_line_is_refused_and_the_next_one_answered():
    deep = b"[" * 200000 + b"]" * 200000
    replies = _serve([deep, json.dumps({"op": "feature_types", "id": 2}).encode()])
    assert replies[0]["ok"] is False and replies[0]["kind"] == "bad_json"
    assert replies[1]["ok"] is True and replies[1]["id"] == 2


def test_bytes_outside_utf8_do_not_end_the_loop():
    replies = _serve([b'{"op": "feature_types", "id": 1, "note": "\xff\xfe"}',
                      json.dumps({"op": "feature_types", "id": 2}).encode()])
    # the bad bytes were replaced, so the first line parses and is answered
    # by id (refused, since feature_types takes no `note`); the loop goes on
    assert [r["id"] for r in replies] == [1, 2]
    assert replies[1]["ok"] is True


def test_a_line_the_size_of_a_file_is_a_refusal_not_a_crash():
    from cadcore.service import server

    huge = b'{"op": "x", "pad": "' + b"a" * (server.MAX_LINE + 10) + b'"}'
    replies = _serve([huge, json.dumps({"op": "feature_types", "id": 2}).encode()], timeout=300)
    assert replies[0]["kind"] == "bad_json" and "limit" in replies[0]
    assert replies[1]["ok"] is True


def test_an_attached_reply_is_matched_to_its_request():
    """A late answer to a timed-out request must not be handed to the next call."""
    import io

    from cadcore.service.mcp import Attached

    class Pretend(Attached):
        def __init__(self):
            self.written = []
            self.stream = self

        def write(self, text):
            self.written.append(json.loads(text))

        def flush(self):
            pass

        def readline(self):
            # first: a stale reply to request 7, then the real one
            if not getattr(self, "_late", False):
                self._late = True
                return json.dumps({"ok": True, "result": "stale", "id": 7}) + "\n"
            return json.dumps({"ok": True, "result": "fresh", "id": self.written[-1]["id"]}) + "\n"

    a = Pretend()
    assert a.send({"op": "build"})["result"] == "fresh"
    assert a.written[-1]["id"] == 1


def test_an_integer_too_long_to_convert_is_a_refusal_not_a_crash():
    """Python refuses to convert more than 4300 digits, with a ValueError that is
    not a JSONDecodeError."""
    replies = _serve([b'{"op": "build", "id": ' + b"9" * 5000 + b"}",
                      json.dumps({"op": "feature_types", "id": 2}).encode()])
    assert replies[0]["ok"] is False and replies[0]["kind"] == "bad_json"
    assert replies[1]["ok"] is True and replies[1]["id"] == 2
