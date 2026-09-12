# service

The ways into the kernel from outside its process. Nothing here contains
modelling logic; each module is a transport over `ops/`.

## Where to start

`server.py` is the line protocol: one JSON object per line in, one line back
with the same id. `mcp.py` is the same operations as MCP tools, and
`--attach` forwards every call to the session a running Blender is showing.
`cli.py` is `python -m cadcore build|export|drawing|simulate|optimize|
fuzz|soak|...`. `resources.py` serves the manual and the examples as MCP
resources.

    line protocol (server.py)        MCP (mcp.py)              command line (cli.py)
    {"id": 7, "op": "add_fillet",    tools/call add_fillet     python -m cadcore build x.json
     "edges": [...], "radius": 2}    {"edges": [...],
              │                       "radius": 2}                       │
              └──────────────┬──────────────┘                            │
                             ▼                                           ▼
                    handle(session, request)                    a Session of its own
                             │
                    op_add_fillet on the Session?            no → unknown_op
                    arguments bind, and are the right type?  no → bad_arguments
                             │
                ┌────────────┴────────────┐
                ▼                         ▼
     {"id": 7, "ok": true,      {"id": 7, "ok": false, "kind": "fillet_failed",
      "result": {...}}           "message": "...", "detail": {"hint": "..."}}

## What it keeps

* **Every `op_*` method is an operation and a tool**, found by reflection,
  its docstring the description, its signature the schema. The plain types
  the signature declares are checked before the call.
* **Malformed input gets a `bad_json` reply** and the server reads the next
  line; a late reply is dropped by id rather than handed to the next caller.
* **Nothing but the protocol goes to stdout.** OpenCASCADE writes to fd 1;
  both servers duplicate the real stdout for replies and point fd 1 at
  stderr.
* **A served session is fenced** to its workspace root.
* `fuzz` moves parameters inside the declared envelope and checks every
  reference survives; `soak` performs random edits, undos and rollbacks and
  checks a refused edit leaves the document byte for byte unchanged.
