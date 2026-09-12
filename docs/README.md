# Using RealParts

Read in this order the first time. Every page stands on its own after that.

1. **Install** -- Blender 5.1 or newer, the add-on zip, and *Install the CAD
   Kernel* from the sidebar: [how-to.md, section 0](how-to.md#0-set-up-once).
   For an assistant instead of Blender: [assistant.md](assistant.md).
2. **A first part** -- New, then a box or a drawn shape, then click the part:
   [how-to.md, sections 1-4](how-to.md#1-start).
3. **Editing it** -- push and pull a face, round an edge, drag a sketch
   point, type over a number, ask the assistant:
   [how-to.md, sections 5-8](how-to.md#5-push-pull-hole-round).
4. **Saving and getting it out** -- the JSON document, STEP for other CAD,
   STL or 3MF for a printer, a drawing for the shop:
   [how-to.md, sections 10-12](how-to.md#10-get-it-out).

## By topic

| page | what it covers |
|---|---|
| [how-to.md](how-to.md) | the whole tour, with pictures |
| [features.md](features.md) | every feature type, units, requirements, switching features off, repairing references, the sketch vocabulary |
| [addon.md](addon.md) | the Blender add-on in detail: what the pick offers, drags, planes, the Ask box, animation, rendering |
| [web.md](web.md) | the kernel over HTTP and the page beside it: open, pick, act, ask, in a browser |
| [native.md](native.md) | the desktop app: the same thin client as a Qt window |
| [assistant.md](assistant.md) | Claude Code or Codex over MCP: setup, what the tools can check, the workspace fence |
| [output.md](output.md) | printing, flat patterns, measuring, drawings, assemblies and mates, the bill of materials |
| [simulation.md](simulation.md) | studies: static, modal, thermal, buckling; mesh convergence |
| [mechanisms.md](mechanisms.md) | linkages and gears, solved by the sketch solver |
| [examples.md](examples.md), [quadruped.md](quadruped.md) | the shipped examples, and the four-legged walker |

## For developers

[architecture.md](architecture.md) is the layout and what happens to one
edit. [decisions.md](decisions.md) is why it is built that way: the reasons,
the bugs that shaped them, and what was tried and dropped.
[caught.md](caught.md) is what the fuzz, soak and golden checks have found.
The rules for changing the code are in [../CLAUDE.md](../CLAUDE.md).

`images/` holds the pictures these pages use. The pages ship inside the
Python package and are served to assistants as MCP resources, so each stays
self-contained Markdown.
