# Driving it from an assistant

[← README](../README.md)

The kernel speaks MCP, so Claude Code, Codex or anything else that speaks it
can do CAD here rather than edit JSON -- and what it builds can be checked by
the same kernel: `simulate`, `drawing`, `flat_pattern`, `printability`,
`interference`. An assistant that can only model is a toy; one that can say
whether the part holds is a colleague.

What it builds is held to what was asked: `requirements` in the document --
under 150 g, inside a 120 mm box, printable, one solid -- are measured on
every rebuild and shown red or green in the sidebar, and a study's safety
factor is checked by the same kernel. In a head-to-head on a strength-driven
spec, an agent working through this kernel ran the FEM and chose 12 mm
(safety factor 2.30); an agent writing CAD code by hand chose 9 mm and shipped
a 1.35. The difference is not the model. It is whether the tool can check.
The benchmark that produced those numbers is in [`bench/`](../bench/README.md).

## Two ways to connect

    on its own                          onto the Blender you are looking at

    Claude Code / Codex                 Claude Code / Codex        Blender + add-on
          │ MCP on stdio                      │ MCP on stdio             │
          ▼                                   ▼                          │
    cadcore-mcp                         cadcore-mcp --attach ──► loopback socket, opened
    owns the session; files live        forwards every call      by *Assistant* in the sidebar
    under the folder it started in                                       │
                                                                         ▼
                                                                  the kernel Blender started:
                                                                  one session, one undo stack,
                                                                  for you and the assistant

The first needs nothing but the Python package. The second is what the
add-on's *Config for Claude Code* button sets up.

## From the installed add-on

The Ask box needs nothing set up: it opens the door itself. For an assistant
run from a terminal, press *Claude Code* or *Codex* under "From a terminal
instead" in the sidebar's *Assistant* panel. The MCP
server entry is on the clipboard and in a file under your Blender config;
paste it into `.mcp.json` or `~/.codex/config.toml`. It names Blender's own
Python and the kernel you already installed, so the assistant starts with
nothing to download, and it attaches to the document on screen.
`tools/verify/verify_retail.py` runs exactly that entry against an installed
zip and builds the document through it.

## From a checkout

```bash
python -m cadcore.service.mcp               # every op_ on the session, as a tool
uvx --from git+https://github.com/inuex35/realparts cadcore-mcp   # or without a clone
```

It also serves resources: `cad://schema/document` is the document format,
generated from the same declarations that refuse a bad argument, and
`cad://doc/features` and `cad://example/*` are the manual and the worked
documents. They ship in the wheel, because an install is exactly the case where
the reader cannot open the repository instead.

Or onto the session Blender is showing, which is the one worth having: turn on
*Assistant* in the sidebar and the add-on listens on the loopback address, then

```bash
python -m cadcore.service.mcp --attach       # 127.0.0.1:8765 by default
```

and the assistant works on the document on screen -- the viewport changes as it
is asked, and its edits are on the same undo stack as yours. Nothing is
started from Blender and no key is held there: the assistant is already
running in a terminal, and the sidebar is where the door is opened.

`.mcp.json` in this repo already declares the plain form, so `claude` finds it
in this directory. It says `python3 -m cadcore.service.mcp` rather than naming an
interpreter inside `.venv`: a relative path there is resolved against whatever
directory the client happens to launch from, and when that is not this one the
server does not start and the failure is a bare ENOENT. Run the client from
the repository root, with the environment that has the kernel in it. For Codex,
add it to `~/.codex/config.toml`:

```toml
[mcp_servers.cadcore]
command = "python3"
args = ["-m", "cadcore.service.mcp"]
```

A served session reads and writes only under the directory it was started in.
`CAD_WORKSPACE` moves that root; see `cadcore/ops/workspace.py` for what the fence
does and does not promise.

## What the tools are

The tools are the operations -- `open`, `set_parameter`, `add_fillet`,
`interference`, `printability`, `render`, `undo` -- with their arguments read off their
own signatures, so the two cannot drift apart. That is the reason for doing it
this way rather than letting an assistant edit the document as text: the
kernel checks the edit against the document's own asserts and envelope, the
edit lands on the same undo stack as a person's, and a question like "do these
two parts collide" has an answer rather than a paragraph.

Three of the tools are the reason for the whole arrangement.

* **`optimize`** moves the parameters the document declared a range for,
  rebuilds each time, and keeps the lightest that still holds its asserts --
  and by `mass`, still passes its studies. On the bracket that is 186.6 g down
  to 129.5 g at a safety factor of 3.08, found by the kernel rather than typed
  by anyone.
* **`check`** is the other way round: build, envelope, references,
  interference, printability, in one answer, before anything is cut.
* **`render`** draws the model to a PNG and hands it back as an image, so the
  assistant can *look* at what it built. Every mistake worth catching here was
  caught by looking -- a foot that left the ground, a nut sitting on a plain
  shank, a display showing the complement of every digit. None of them
  measured wrong.
