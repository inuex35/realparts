"""Ask Claude from the sidebar; it works the kernel through the same operations
the buttons use, and every step lands in the viewport as it happens.

The model runs in a thread (the network call blocks). Every tool call it makes
is handed to the main thread, which is the only place the kernel client and
the scene may be touched, and the answer goes back to the thread.
"""
from __future__ import annotations

import json
import os
import time
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

import bpy

from . import state, sync
from .client import ServerError

API = "https://api.anthropic.com/v1/messages"
MAX_STEPS = 40                  # tool calls one question may make
MAX_TOKENS = 4096

_run = None                     # the one conversation in flight
_history: list = []             # the messages so far, so "now the other three" works
_cli_session: dict = {}         # backend -> session id, to continue a CLI conversation


class Run:
    """One question, from the first request to the model's last word."""

    def __init__(self, question: str, api_key: str, model: str, tools: list,
                 instructions: str, send=None, history: list | None = None):
        self.question, self.api_key, self.model = question, api_key, model
        self.tools = [{"name": t["name"], "description": t["description"],
                       "input_schema": t["inputSchema"]} for t in tools]
        self.system = instructions + (
            "\n\nYou are working inside Blender. The person sees the model change after "
            "every step. Keep answers short and say what you did, in plain words. "
            "If a step is refused, read the kind and the hint, and try a different way "
            "before giving up.")
        self.send = send or self._http          # a test hands in a fake
        self.messages = list(history or []) + [{"role": "user", "content": question}]
        self.stopped = False
        self.to_main: queue.Queue = queue.Queue()
        self.from_main: queue.Queue = queue.Queue()
        self.lines: list = []                   # (kind, text) for the panel
        self.done = False
        self.error = None
        self.steps = 0

    # -- the thread ----------------------------------------------------------
    def start(self):
        threading.Thread(target=self._talk, daemon=True).start()

    def _talk(self):
        try:
            while True:
                reply = self.send({"model": self.model, "max_tokens": MAX_TOKENS,
                                   "system": self.system, "tools": self.tools,
                                   "messages": self.messages})
                content = reply.get("content", [])
                self.messages.append({"role": "assistant", "content": content})
                for block in content:
                    if block.get("type") == "text" and block.get("text", "").strip():
                        self.lines.append(("said", block["text"].strip()))
                calls = [b for b in content if b.get("type") == "tool_use"]
                if reply.get("stop_reason") != "tool_use" or not calls or self.stopped:
                    return
                results = []
                for call in calls:
                    self.steps += 1
                    if self.steps > MAX_STEPS:
                        self.lines.append(("note", "stopped after %d steps" % MAX_STEPS))
                        return
                    if self.stopped:
                        answer = "refused: stopped: the person pressed Stop"
                    else:
                        self.to_main.put((call["name"], call.get("input") or {}))
                        answer = self.from_main.get()
                    results.append({"type": "tool_result", "tool_use_id": call["id"],
                                    "content": answer[:20000],
                                    "is_error": answer.startswith("refused:")})
                self.messages.append({"role": "user", "content": results})
        except Exception as exc:                                    # noqa: BLE001
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self.lines.append(("error", self.error))
        finally:
            if self.stopped:
                self.lines.append(("note", "stopped"))
            self.done = True

    def _http(self, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(API, data=data, method="POST", headers={
            "content-type": "application/json", "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:400]
            raise RuntimeError("the API said %d: %s" % (exc.code, body)) from None

    # -- the main thread -----------------------------------------------------
    def pump(self, context) -> bool:
        """Run one waiting tool call on the main thread. True while alive."""
        try:
            name, args = self.to_main.get_nowait()
        except queue.Empty:
            return not self.done
        self.lines.append(("did", "%s(%s)" % (name, ", ".join(
            "%s=%s" % (k, json.dumps(v)) for k, v in args.items())[:120])))
        from ..ui import changes

        before = changes.picture(context)
        try:
            info = state.get_client(context).call(name, **args)
            if isinstance(info, dict) and "faces" in info and "volume_mm3" in info:
                state._apply_build(context, info)
                changes.settle(context, before)   # what that did, drawn on the part
                state.push_undo("assistant: %s" % name)   # the bridge does; this did not
            elif isinstance(info, dict) and info.get("under_construction"):
                state._apply_build(context, info)
            answer = json.dumps(info, default=str)
        except ServerError as exc:
            answer = "refused: %s: %s %s" % (exc.kind, exc.message,
                                             json.dumps(getattr(exc, "detail", {}), default=str))
            self.lines[-1] = ("refused", "%s -> %s" % (self.lines[-1][1], exc.kind))
        except Exception as exc:                                    # noqa: BLE001
            answer = "refused: internal_error: %s" % exc
        self.from_main.put(answer)
        return True


class CliRun:
    """One question put to Claude Code or Codex, run as a child process.

    The CLI does the talking and the tool calls itself, through the MCP
    server attached to this Blender; the bridge already lands every step in
    the viewport. This only reads the CLI's JSON lines into the transcript
    and keeps the session id so the next question continues the last.
    """

    def __init__(self, question: str, command: list, kind: str, asked: str = ""):
        self.question, self.command, self.kind = question, command, kind
        self.asked = asked or question       # sent to the CLI; `question` is what the panel shows
        self.lines: list = []
        self.done = False
        self.error = None
        self.stopped = False
        self.session = None
        self.process = None

    def start(self):
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
            self.process = subprocess.Popen(
                self.command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=_workdir(),
                stdin=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **flags)
            try:
                self.process.stdin.write(self.asked)
                self.process.stdin.close()
            except OSError:
                pass                     # a CLI that exited at once; its code says why
            complaints: list = []
            reader = threading.Thread(target=lambda: complaints.append(self.process.stderr.read()),
                                      daemon=True)
            reader.start()               # read beside stdout: a full pipe would stall the CLI
            for raw in self.process.stdout:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                self._take(event)
            self.process.wait()
            reader.join(timeout=5)
            if self.process.returncode not in (0, None) and not self.stopped:
                _cli_session.pop(self.kind, None)          # do not resume a session that failed
                tail = ("".join(complaints) or "")[-400:].strip()
                if self.error is None:            # the stream said nothing about why
                    self.error = "%s ended with code %d%s" % (
                        self.command[0], self.process.returncode, (": " + tail) if tail else "")
                    self.lines.append(("error", self.error))
        except Exception as exc:                                    # noqa: BLE001
            self.error = "%s: %s" % (type(exc).__name__, exc)
            self.lines.append(("error", self.error))
        finally:
            if self.stopped:
                self.lines.append(("note", "stopped"))
            self.done = True

    def _take(self, event: dict) -> None:
        if self.kind == "claude":
            self._take_claude(event)
        else:
            self._take_codex(event)

    def _take_claude(self, event: dict) -> None:
        """Claude Code, `--output-format stream-json`."""
        kind = event.get("type")
        if kind == "system" and event.get("session_id"):
            self.session = event["session_id"]
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text", "").strip():
                    self.lines.append(("said", block["text"].strip()))
                elif block.get("type") == "tool_use":
                    name = block.get("name", "").replace("mcp__cadcore__", "")
                    self.lines.append(("did", "%s(%s)" % (name, _short_args(block.get("input") or {}))))
        elif kind == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result" and block.get("is_error") and self.lines:
                    label, text = self.lines[-1]
                    if label == "did":
                        self.lines[-1] = ("refused", text + " -> " + _first_line(block.get("content")))
        elif kind == "result":
            if event.get("session_id"):
                self.session = event["session_id"]
            if event.get("is_error"):
                self.error = _first_line(event.get("result"))
                self.lines.append(("error", self.error))
                if event.get("api_error_status") == 401 or "authenticat" in self.error.lower():
                    self.lines.append(("note", "sign in again: run `claude login` in a terminal, "
                                               "then Ask once more"))

    def _take_codex(self, event: dict) -> None:
        """Codex, `codex exec --json`."""
        kind = event.get("type", "")
        if kind == "thread.started" and event.get("thread_id"):
            self.session = event["thread_id"]
        item = event.get("item") or {}
        if kind == "item.completed" and item.get("type") == "agent_message" and item.get("text"):
            self.lines.append(("said", item["text"].strip()))
        elif "tool_call" in str(item.get("type", "")):
            name = item.get("tool") or item.get("name") or "tool"
            text = "%s(%s)" % (name, _short_args(item.get("arguments") or {}))
            if kind == "item.started":
                self.lines.append(("did", text))
            elif kind == "item.completed" and item.get("status") == "failed":
                if self.lines and self.lines[-1] == ("did", text):
                    self.lines[-1] = ("refused", text + " -> " + _first_line(item.get("error") or item.get("result")))
                else:
                    self.lines.append(("refused", text))
        elif kind == "error":
            self.lines.append(("error", _first_line(event.get("message"))))

    def pump(self, context) -> bool:
        return not self.done

    def stop(self):
        self.stopped = True
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass


def _short_args(args) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return args[:120]
    if not isinstance(args, dict):
        return str(args)[:120]
    return ", ".join("%s=%s" % (k, json.dumps(v, default=str)) for k, v in args.items())[:120]


def _first_line(content) -> str:
    if isinstance(content, list):
        content = " ".join(str(c.get("text", c)) if isinstance(c, dict) else str(c) for c in content)
    return str(content or "").strip().splitlines()[0][:160] if content else ""


def resolve_backend(prefs) -> str:
    """Which way Ask goes: the setting, or, on `auto`, whatever is installed."""
    chosen = getattr(prefs, "assistant_backend", "auto")
    if chosen != "auto":
        return chosen
    for kind in ("claude", "codex"):
        if shutil.which(getattr(prefs, kind + "_command", kind) or kind):
            return kind
    return "api"


def _cli_command(context, kind: str) -> tuple:
    """The argv for one question to a CLI, and why it cannot run if it cannot."""
    from . import bridge
    from .client import assistant_command, kernel_for
    from .state import kernel_home, prefs, repo_root

    p = prefs(context)
    exe = shutil.which(getattr(p, kind + "_command", kind) or kind)
    if not exe:
        return None, ("%s was not found on this computer: install %s and sign in, or set its path "
                      "in the add-on preferences" % (kind, "Claude Code" if kind == "claude" else "Codex"))
    if not bridge.listening():
        try:
            bridge.start(for_ask=True)     # and `release` at the end of the run
        except OSError as exc:
            return None, "could not open the bridge: %s" % exc
    repo = p.repo_path or repo_root()
    python, site = kernel_for(repo, p.python_path, kernel_home())
    spec = assistant_command(python, site, repo, "%s:%d" % (bridge.HOST, bridge.PORT),
                             token_file=bridge.token_path())
    session = _cli_session.get(kind)
    if kind == "claude":
        handle, path = tempfile.mkstemp(suffix=".json", prefix="cadcore-mcp-")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump({"mcpServers": {"cadcore": spec}}, file)
        # the question goes in on stdin, not argv: through a .cmd shim on
        # Windows, cmd.exe cuts an argument at its first newline
        # only the CAD tools: a file or a shell would ask for a permission
        # nobody can grant from here, and the run would end on that
        argv = [exe, "-p", "--output-format", "stream-json", "--verbose",
                "--mcp-config", path, "--allowedTools", "mcp__cadcore__*",
                "--disallowedTools", "Bash,PowerShell,Write,Edit,NotebookEdit,Task,WebFetch,WebSearch",
                "--permission-mode", "dontAsk"]
        if session:
            argv += ["--resume", session]
        return argv, None
    overrides = ["-c", 'mcp_servers.cadcore.command=%s' % json.dumps(spec["command"]),
                 "-c", 'mcp_servers.cadcore.args=%s' % json.dumps(spec["args"])]
    for key, value in (spec.get("env") or {}).items():
        overrides += ["-c", "mcp_servers.cadcore.env.%s=%s" % (key, json.dumps(value))]
    if session:
        return [exe, "exec", "resume", session, "--json", *overrides, "-"], None
    return [exe, "exec", "--json", *overrides, "-"], None          # "-": the prompt on stdin


def _workdir() -> str:
    """A local directory for the CLI to start in: the document's folder, else the user's.

    Not Blender's own cwd: started from a network share (a `\\wsl.localhost` path)
    it is a place cmd.exe refuses to start in, and `claude.cmd` dies with it.
    """
    props = getattr(getattr(bpy.context, "scene", None), "cadcore", None)
    folder = os.path.dirname(props.doc_path) if props is not None and props.doc_path else ""
    for candidate in (folder, os.path.expanduser("~"), tempfile.gettempdir()):
        if candidate and os.path.isdir(candidate) and not candidate.startswith(("\\\\", "//")):
            return candidate
    return tempfile.gettempdir()


def situation(context) -> str:
    """What Blender knows right now, said once, so the person need not.

    The picked faces and edges ("here"), the document and its features, the
    last thing the panel said. Sent ahead of every question.
    """
    props = getattr(context.scene, "cadcore", None)
    if props is None:
        return ""
    lines = ["[From Blender]"]
    body = sync.body()
    faces, pairs = sync.picked()
    if faces:
        lines.append("Selected faces: " + ", ".join(faces[:12]) + (" ..." if len(faces) > 12 else ""))
    if pairs and not faces:
        lines.append("Selected edges (between faces): " + ", ".join("%s|%s" % p for p in pairs[:12]))
    corner = sync.picked_corner(body) if body is not None else None
    if corner is not None and not faces and not pairs:
        lines.append("Selected corner: at (%.3f, %.3f, %.3f) mm, where faces %s meet"
                     % (*corner[0], ", ".join(corner[1])))
    elif not faces and not pairs:
        lines.append("Nothing is selected.")
    if props.has_document:
        names = [f.name for f in props.features]
        lines.append("Document: %s; features in order: %s" % (
            props.doc_path or "(new, unsaved)", ", ".join(names) if names else "none yet"))
        if props.has_body:
            lines.append("Body: %d faces, %.0f mm3." % (props.faces, props.volume))
        else:
            lines.append("No solid yet.")
        if 0 <= props.feature_index < len(props.features):
            lines.append("Feature shown in the panel: " + props.features[props.feature_index].name)
    else:
        lines.append("No document is open; start with new_document.")
    lines.append('"here" or "this" means the selection above. Say what you did in one or two lines. '
                 'Use only the cadcore tools: no files, no shell, no scripts.')
    # a person is watching the viewport: the part should grow in stages they
    # can follow and stop, not land finished after minutes of silence
    from .state import prefs
    if getattr(prefs(context), "assistant_stages", True):
        lines.append("Someone is watching this Blender. Work in stages: first the main body in "
                     "one call, then each group of features (holes, rounds, cuts, a second part) "
                     "as its own call, a few features per call, and one line on what that stage "
                     "made before the next. Do not write the whole part in one load_json unless "
                     "it is small.")
    return "\n".join(lines)


def ask(context, question: str, send=None) -> str | None:
    """Start a run. Returns a reason if it cannot start."""
    global _run
    if _run is not None and not _run.done:
        return "the assistant is still working"
    prefs = state.prefs(context)
    backend = resolve_backend(prefs) if send is None else "api"
    asked = situation(context) + "\n\n" + question       # the screen goes with the words
    if backend in ("claude", "codex"):
        argv, why = _cli_command(context, backend)
        if why:
            return why
        _run = CliRun(question, argv, backend, asked)
        _run.start()
    else:
        key = getattr(prefs, "anthropic_api_key", "")
        if not key and send is None:
            return "set your Anthropic API key in the add-on preferences first"
        try:
            catalogue = state.get_client(context).call("assistant_tools")
        except ServerError as exc:
            return "%s: %s" % (exc.kind, exc.message)
        _run = Run(asked, key, getattr(prefs, "assistant_model", "claude-sonnet-5"),
                   catalogue["tools"], catalogue["instructions"], send=send, history=_history)
        _run.question = question                          # shown as typed
        _run.start()
    _run.started = time.time()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.05)
    return None


def _tick() -> float | None:
    """Main-thread heartbeat: run tool calls, copy the transcript to the panel."""
    run = _run
    if run is None:
        return None
    alive = run.pump(bpy.context)
    if not alive and run.error is None:
        _remember(run)
    show(bpy.context)
    for area in getattr(bpy.context.screen, "areas", []) if bpy.context.screen else []:
        area.tag_redraw()
    if not alive:
        from . import bridge

        bridge.release()          # if this Ask switched it on, it goes off with it
    return 0.1 if alive else None


MARKS = {"said": "", "did": "  \u2714 ", "refused": "  \u2716 ", "note": "  \u2013 ", "error": "  \u2716 "}


def show(context) -> None:
    props = getattr(context.scene, "cadcore", None)
    if props is None:
        return
    if _run is not None:
        props.transcript.clear()
        for kind, text in _run.lines[-8:]:
            line = props.transcript.add()
            line.kind, line.text = kind, text
        props.assistant_busy = not _run.done
        # the answer also goes on the status line at the top of the sidebar,
        # so it is seen even with the Assistant panel folded
        said = [text for kind, text in _run.lines if kind in ("said", "error")]
        if _run.done and said:
            props.status = "assistant: " + said[-1].strip().splitlines()[0][:200]
            props.status_is_error = _run.lines[-1][0] == "error"
        elif not _run.done:
            # how long, and the last thing it did: a big request takes minutes
            # of thinking, and a silent "working" looks like a hang
            seconds = int(time.time() - getattr(_run, "started", time.time()))
            last = _run.lines[-1][1].strip().splitlines()[0][:80] if _run.lines else "thinking"
            props.status = "assistant: working, %d:%02d -- %s" % (seconds // 60, seconds % 60, last)
    # the same, in full, in the "Assistant" text if the editor strip is open
    replies = bpy.data.texts.get("Assistant")
    if replies is None:
        return
    body = []
    if _run is not None:
        body.append("> " + _run.question.strip())
        body.append("")
        for kind, text in _run.lines:
            body.append(MARKS.get(kind, "") + text)
        if not _run.done:
            body.append("  ... working")
    replies.clear()
    replies.write("\n".join(body) + "\n")
    try:
        replies.cursor_set(max(0, len(body) - 1))
    except (AttributeError, TypeError):
        pass
    screen = getattr(context, "screen", None)
    for area in getattr(screen, "areas", []) if screen else []:
        space = area.spaces.active if area.type == 'TEXT_EDITOR' else None
        if space is not None and getattr(space, "text", None) is replies:
            lines_shown = max(1, int(area.height / 20))
            space.top = max(0, len(body) - lines_shown + 1)
            area.tag_redraw()


def busy() -> bool:
    return _run is not None and not _run.done


def stop() -> None:
    """Let the current tool call finish, then end the run."""
    if _run is not None:
        if hasattr(_run, "stop"):
            _run.stop()
        else:
            _run.stopped = True


def forget() -> None:
    """Drop the conversation: a new or opened document is a new subject."""
    global _history
    _history = []
    _cli_session.clear()
    props = getattr(getattr(bpy.context, "scene", None), "cadcore", None)
    if props is not None:
        props.transcript.clear()


def _remember(run) -> None:
    """Keep a finished run's messages, so the next question continues it."""
    global _history
    if isinstance(run, CliRun):
        if run.session:
            _cli_session[run.kind] = run.session
        return
    _history = list(run.messages)
    if len(_history) > 40:                    # enough to say "the other three"
        _history = _history[-40:]


def pump_until_done(context, limit: float = 30.0) -> None:
    """For tests without a window: run the loop here instead of from a timer."""
    import time

    end = time.monotonic() + limit
    while _run is not None and not _run.done and time.monotonic() < end:
        _run.pump(context)
        time.sleep(0.01)
    while _run is not None and hasattr(_run, "to_main") and not _run.to_main.empty():
        _run.pump(context)
    if _run is not None and _run.done and _run.error is None:
        _remember(_run)
    show(context)
