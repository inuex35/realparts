"""Talk to the cadcore server from inside Blender.

The kernel runs in a separate interpreter (see ``cadcore/service/server.py``):
Blender ships its own Python, and an OpenCASCADE crash then costs a restart of
the child rather than the session. Protocol: one JSON request per line, one
reply per line, errors returned as data.
"""
from __future__ import annotations

import json
import os
import queue
import select
import subprocess
import threading


class ServerError(RuntimeError):
    """A refusal from the kernel, carrying the kind the UI reacts to."""

    def __init__(self, kind: str, message: str, detail: dict | None = None):
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.detail = detail or {}


class Client:
    def __init__(self, python: str, repo: str, site: str | None = None):
        self.python = python
        self.repo = repo
        #: the site-packages Install the CAD kernel fills when `python` is
        #: Blender's own; None for a virtualenv, which carries its packages
        self.site = site
        self.proc: subprocess.Popen | None = None
        self._id = 0
        self.opened: str | None = None       # reopened after a restart

    # -- process ------------------------------------------------------------
    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        if self.alive:
            return
        if not os.path.exists(self.python):
            raise ServerError("no_interpreter", f"no python at {self.python}",
                              {"hint": "set it in the add-on preferences"})
        if self.site is not None and not os.path.isdir(self.site):
            # Blender's own interpreter without the kernel installed: the
            # message names the button to press rather than the path
            raise ServerError("no_kernel", "the CAD kernel is not installed",
                              {"hint": "press Install the CAD kernel in the "
                                       "add-on preferences (a one-time "
                                       "download; no separate Python is needed)",
                               "site": self.site})
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        if self.site is not None:
            # the packages, then the repository for `cadcore` itself, then the
            # user's PYTHONPATH. No user site: a stray OCP in ~/.local would
            # otherwise load ahead of the installed one
            env["PYTHONPATH"] = os.pathsep.join(
                [self.site, self.repo] +
                ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
            env["PYTHONNOUSERSITE"] = "1"
        # stderr stays on Blender's console so kernel tracebacks are readable.
        # --unfenced: every path here comes from a file dialog, and the kernel's
        # workspace fence would refuse paths outside the add-on's folder
        self.proc = subprocess.Popen(
            [self.python, "-m", "cadcore.service.server", "--unfenced"], cwd=self.repo, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            encoding="utf-8", bufsize=1)
        if self.opened:
            # a fresh kernel has no document; reopen so a retry after a crash
            # does not answer `no_document`
            self._reopen(self.opened)

    def stop(self) -> None:
        if self.alive:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=5)
            except Exception:                                       # noqa: BLE001
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)   # reap it, or it stays a zombie
                except Exception:                                   # noqa: BLE001
                    pass
        self.proc = None

    def _reopen(self, path: str) -> None:
        """Reopen the previous document in the new kernel, or forget the path.

        `recover` loads the sidecar the dead kernel wrote beside the document,
        which holds its unsaved edits.
        """
        try:
            self._send("open", 600.0, path=path, recover=True)
            self._send("build", 600.0)
        except Exception:                                           # noqa: BLE001
            self.opened = None       # it will not open: say so on the next call

    # -- protocol -----------------------------------------------------------
    def call(self, op: str, timeout: float = 600.0, **args):
        """One request, one reply, with a deadline and an id check.

        A hung kernel would otherwise freeze the UI thread, and a stale reply
        would be handed back as this request's answer.
        """
        self.start()
        result = self._send(op, timeout, **args)
        if op == "open":
            self.opened = args.get("path") or self.opened
        return result

    def _send(self, op: str, timeout: float = 600.0, **args):
        """One request on the connection that is already up."""
        self._id += 1
        request = dict(args, op=op, id=self._id)
        try:
            self.proc.stdin.write(json.dumps(request) + "\n")
            self.proc.stdin.flush()
            line = self._readline(timeout, op)
        except (BrokenPipeError, ValueError, OSError) as exc:
            # OSError too: writing to a dead pipe raises BrokenPipeError on
            # POSIX but EINVAL (a plain OSError) on Windows
            self.proc = None
            raise ServerError("server_died", str(exc)) from exc
        if not line:
            self.proc = None
            raise ServerError("server_died", f"no reply to {op!r}",
                              {"hint": "see the console for the kernel traceback"})
        try:
            reply = json.loads(line)
        except json.JSONDecodeError as exc:
            # a partial line means the kernel died mid-write: restart rather
            # than surface a JSONDecodeError
            self.stop()
            raise ServerError("server_died", f"garbled reply to {op!r}: {exc}",
                              {"hint": "the kernel was restarted; try again"}) from exc
        if reply.get("id") not in (None, self._id):
            self.stop()
            raise ServerError("out_of_step",
                              f"the kernel answered request {reply.get('id')}, "
                              f"not {self._id}",
                              {"hint": "the connection was restarted"})
        if not reply.get("ok"):
            raise ServerError(reply.get("kind", "error"), reply.get("message", ""),
                              reply.get("detail"))
        return reply["result"]

    def _readline(self, timeout: float, op: str) -> str:
        if not timeout:
            return self.proc.stdout.readline()
        if os.name != "nt":
            ready, _, _ = select.select([self.proc.stdout], [], [], timeout)
            if not ready:
                self.stop()
                raise ServerError("kernel_timeout",
                                  f"{op!r} did not answer within {timeout:g}s",
                                  {"hint": "the kernel was stopped; try again"})
            return self.proc.stdout.readline()
        # Windows: select() takes sockets only, so the deadline needs a thread.
        # One request is in flight at a time; an abandoned reader unblocks at
        # EOF when stop() kills the kernel.
        box: queue.Queue = queue.Queue(maxsize=1)
        stdout = self.proc.stdout
        threading.Thread(target=lambda: box.put(stdout.readline()),
                         daemon=True).start()
        try:
            return box.get(timeout=timeout)
        except queue.Empty:
            self.stop()
            raise ServerError("kernel_timeout",
                              f"{op!r} did not answer within {timeout:g}s",
                              {"hint": "the kernel was stopped; try again"}) from None


def kernel_python(repo: str) -> str:
    """Where a developer's virtualenv puts the kernel's interpreter."""
    if os.name == "nt":
        return os.path.join(repo, ".venv", "Scripts", "python.exe")
    return os.path.join(repo, ".venv", "bin", "python")


#: the Python versions every wheel the kernel pins is published for
KERNEL_PYTHONS = ("3.13", "3.12")


def blender_python() -> str | None:
    """The interpreter this add-on runs in, if the kernel can use it.

    Since Blender 2.91 `sys.executable` is Blender's bundled Python. Returns
    None when the executable is not a Python (an embedded build or a launcher)
    or its version is not in KERNEL_PYTHONS.
    """
    import sys

    exe = sys.executable
    if not exe or not os.path.exists(exe):
        return None
    if not os.path.basename(exe).lower().startswith("python"):
        return None
    if "%d.%d" % sys.version_info[:2] not in KERNEL_PYTHONS:
        return None
    return exe


def kernel_prefix(repo: str, home: str | None = None) -> str:
    """Where Install the CAD kernel puts the packages for Blender's Python.

    Under `home` when given (Blender replaces the add-on folder on upgrade),
    else beside the repository; versioned by interpreter because the wheels
    are. Filled with `pip install --prefix`, not `--target`: `--target` drops
    the OpenCASCADE libraries the NGSolve wheels put in `<prefix>/lib`.
    """
    import sys

    return os.path.join(home or repo, ".kernel", "cp%d%d" % sys.version_info[:2])


def kernel_site(repo: str, home: str | None = None) -> str:
    """The site-packages inside the kernel prefix.

    Asked of `sysconfig` because the layout differs per platform
    (`lib/python3.13/site-packages` on POSIX, `Lib\\site-packages` on Windows).
    """
    import sysconfig

    prefix = kernel_prefix(repo, home)
    return sysconfig.get_path("purelib", vars={"base": prefix, "platbase": prefix})


def bundled_kernel() -> str | None:
    """Where an extension zip's own wheels were installed, or None.

    Blender installs the wheels a manifest lists and puts them on its own
    path, so nothing is downloaded -- but the kernel is a child process and
    inherits no path, so the directory is handed to it the way a prefix of
    ours would be. Asked with `find_spec`, which does not run the package:
    importing OCP into Blender is what the child process exists to avoid.
    """
    import importlib.util

    try:
        import bpy                       # outside Blender there is no zip to have carried them
    except ImportError:
        return None
    managed = bpy.utils.user_resource('EXTENSIONS')
    if not managed:
        return None
    try:
        found = [importlib.util.find_spec(name) for name in ("OCP", "planegcs")]
    except (ImportError, ValueError):
        return None
    if not all(found):
        return None
    where = found[0].submodule_search_locations
    if not where:
        return None
    site = os.path.dirname(list(where)[0])
    # only what Blender itself installed from a manifest counts: an OCP that
    # happens to be importable here is somebody else's, not the zip's
    return site if os.path.realpath(site).startswith(os.path.realpath(managed)) else None


def kernel_for(repo: str, preferred: str = "",
               home: str | None = None) -> tuple[str, str | None]:
    """Which interpreter runs the kernel, and where its packages are.

    In order: the interpreter named in the preferences; a `.venv` beside the
    repository; the wheels an extension zip brought with it; Blender's own
    Python with the installer's prefix (which may not exist yet). Returns
    `(python, site)`, `site` being None when the interpreter's own
    site-packages hold the kernel.
    """
    if preferred:
        return preferred, None
    venv = kernel_python(repo)
    if os.path.exists(venv):
        return venv, None
    own = blender_python()
    if own is not None:
        return own, bundled_kernel() or kernel_site(repo, home)
    # nothing usable: report the developer path, which a person can create by hand
    return venv, None


def find_host_python() -> str | None:
    """A Python the kernel's wheels exist for: Blender's own, else one on the PATH.

    Only versions in KERNEL_PYTHONS are accepted; any other would pass every
    check here and fail later inside pip's resolver.
    """
    import shutil
    import subprocess

    own = blender_python()
    if own is not None:
        return own

    found: dict[str, str] = {}
    for name in ("python3.13", "python3.12", "python3", "python"):
        path = shutil.which(name)
        if not path:
            continue
        try:
            out = subprocess.run([path, "-c",
                                  "import sys; print('%d.%d' % sys.version_info[:2])"],
                                 capture_output=True, text=True,
                                 encoding="utf-8", timeout=20)
        except Exception:                                        # noqa: BLE001
            continue
        found.setdefault(out.stdout.strip(), path)
    for version in KERNEL_PYTHONS:
        if version in found:
            return found[version]
    return None


def assistant_command(python: str, site: str | None, repo: str,
                      where: str = "127.0.0.1:8765", token_file: str | None = None) -> dict:
    """The command an assistant runs to drive the session Blender is showing.

    Uses the interpreter and packages `kernel_for` chose. Returned as
    `command`, `args`, `env`, the shape MCP clients want. `token_file` is
    where the bridge wrote its token; the attached server reads it from
    the environment and says it first.
    """
    env = {"PYTHONNOUSERSITE": "1"}
    if token_file:
        env["CADCORE_BRIDGE_TOKEN_FILE"] = token_file
    path = [p for p in (site, repo) if p]
    if path:
        env["PYTHONPATH"] = os.pathsep.join(path)
    return {"command": python,
            "args": ["-m", "cadcore.service.mcp", "--attach", where],
            "env": env}


#: the assistants a config can be written for, and the file each reads
ASSISTANTS = {
    "claude": ("Claude Code", ".mcp.json"),
    "codex": ("Codex", "config.toml"),
}


def assistant_config(client: str, spec: dict) -> str:
    """The MCP server entry for one client, ready to paste.

    Strings go through `json.dumps` in both formats: a JSON string is a valid
    TOML basic string, which keeps Windows paths intact.
    """
    if client == "claude":
        return json.dumps({"mcpServers": {"cadcore": spec}}, indent=2) + "\n"
    if client == "codex":
        lines = ["[mcp_servers.cadcore]",
                 "command = %s" % json.dumps(spec["command"]),
                 "args = %s" % json.dumps(spec["args"])]
        if spec.get("env"):
            lines += ["", "[mcp_servers.cadcore.env]"]
            lines += ["%s = %s" % (k, json.dumps(v)) for k, v in spec["env"].items()]
        return "\n".join(lines) + "\n"
    raise ValueError("no config format for %r; one of %s"
                     % (client, ", ".join(sorted(ASSISTANTS))))
