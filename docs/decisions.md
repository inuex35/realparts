# Why it is built this way

[← docs](README.md)

The reasons behind the decisions [architecture.md](architecture.md)
describes: what each one is for, the bug that shaped it where there was
one, and what was tried and dropped. The layer READMEs say what each layer
keeps; this page says why. What the fuzz, soak and golden checks have
caught is a separate list, [caught.md](caught.md).

## The document is data, not a program

A part is JSON: parameters, a dependency graph of features, requirements,
studies. Expressions such as `"width/2"` are parsed into a syntax tree and
walked -- arithmetic, comparisons, a fixed list of maths functions -- never
`eval`'d, because documents are things people send each other and emptying
`__builtins__` is not a sandbox. `model/` imports nothing that imports
OpenCASCADE, so a document can be read on a machine without the kernel.

The one dependency that runs the other way -- `model/` asks `features/`
what a feature takes, inside a function, when it converts units -- is
there because which arguments are lengths is written on the declarations
and nowhere else. A copy in `model/` would be the second list the rest of
the design refuses to keep.

## Names survive edits

Every face is named by the feature that made it and the role it plays
(`plate/+z`), every edge by its two faces, and the names are carried
through booleans, fillets, patterns and assemblies by walking OpenCASCADE's
Modified/Generated history. A document refers to geometry only by name, so
a hole drilled in `plate/+z` is still in the top of the plate after the
plate is made thicker.

Where OCCT gives no history the name comes from geometry: a face split by a
boolean becomes `@0` and `@1` *ordered by position*, not by OCCT's output
order, so the same piece has the same name after a rebuild; a fillet's
faces are numbered by the edge each one rounds, because an edge name does
not move when a dimension does. A lost reference once resolved to a
different hole of the same diameter, because cylinders were matched on
radius alone; that is the kind of "almost right" this rules out.

The name grammar (`/ : | # @ ~`) is spelled in one file, `cadcore/names.py`,
and a test reads the syntax tree of everything else for a name spelled by
hand. A feature id may not contain a separator for the same reason:
`a/b` as an id made a face `a/b/+z` that read as belonging to `a`.

## The kernel is a separate process

An OpenCASCADE boolean that crashes takes its process with it, and that
process must not be the one holding the person's unsaved work. That is the
whole reason; it is not a licence question and not an "OCCT cannot load into
Blender" question, both of which were given once and were wrong. Blender
and assistants talk to the kernel over one JSON object per line or as MCP
tools, and every successful edit is written beside the document as it is
made, so a crash costs the last operation.

## Every edit is all-or-nothing

`ops/session.py` opens one guard around every edit: it snapshots the
document, the built body and the evaluator, applies the edit, rebuilds, and
on any refusal puts all three back and re-raises. A guard opened inside
another is part of the outer one, so a batch is one undo step and a refusal
at step eight leaves nothing behind.

Four things the guard learned to cover after being caught out: the body
was once left as the refused edit had made it while the document was put
back, so an export answered for a part the document no longer described;
restoring once rebuilt the document from its file form, which re-runs
every load-time check, so the one path whose job is to leave nothing
behind could itself raise -- a restore is now an assignment that cannot
fail; anything a reply is computed from has to be computed inside the
guard, or the reply is a refusal with the edit already kept; and the
rollback view and the last reply's names are restored too, because a
rebuild rewrites them.

A drag is the exception to "recorded as it happens": it previews by editing
the real document once per mouse move, and thirty previews recorded as
steps pushed the real ones off a stack capped at 64. A drag is a run of
trials between `begin_drag` and `end_drag`; the end records one step, or
none.

## Blender's undo follows the kernel by revision

Blender's undo restores the scene; the kernel's document has to follow.
Following by undo depth was wrong once the kernel's stack was full: the
depth is 64 before and after an edit, so an undo found the kernel "already
there" and left the model where it was. Every recorded state now carries a
number the session hands out once and never again, a snapshot carries the
number of the state it was taken from, and the scene and the mesh remember
the number on screen. After an undo the kernel is asked for that state.
A slider's own Blender step is pushed before its throttled kernel edit, so
after an undo the kernel's parameters are also made to match the scene's:
the scene Blender restored is the truth.

## A refusal has a kind

Every refusal is a `CadError` with a machine-readable kind from the list in
`errors.py`, and usually a hint. A program reads a refusal, so it has to be
a code and not a sentence. A test walks the source for every raise and
every refusal reply and checks both directions: a kind that is not listed
is a typo, a kind that is never raised is a leftover. The kinds that look
like duplicates and are not: `no_solid` (a feature that makes none) against
`not_a_solid` (a body that is not closed); `unknown_feature` (an id this
document lacks) against `unknown_feature_type` (a type this build lacks).

## A feature is one function with one declaration

The `@feature(...)` declaration lists the arguments and their kinds, and it
is the one source for validation, the dependency graph, the catalogue an
assistant reads, and the fields the sidebar draws. There is no second
list, because the second list fell behind: the graph's edges were once a
central list of "argument keys that look like references", and `until:
{"plane": ...}` and a bolt circle about a datum axis were both documented
and neither worked, since the plane and the axis never became ancestors.
The sidebar draws a checkbox because the declaration says the argument is
a flag, not by guessing from the value -- a flag left at its default is
not in the document at all. The properties this code keeps were being kept
by discipline, and discipline is what slips when the next person, or the
next agent, adds a feature in a hurry. The test that every type declares
its arguments could not fail for a year, because the registry adds a
common argument to every declaration; it measures past those now.

Sketch constraints had the same shape of problem: a forty-branch dispatch
that also owned the "supported constraints" list, drifting the moment
anyone added a type. It is a registry, and the refusal lists the registry.
Ten viewport operators were the same twelve lines around a different
middle; the twelve lines are `EditFromSelection`.

## What an operation promises

    target = box(40, 30, 20)
    tool = cylinder(3, 50, at=(500, 0, 0))     # half a metre away
    cut(target, tool)                          # -> target, unchanged, "done"

A hole whose parameters had moved it off the part reported that it had
drilled it; nothing about the answer was wrong, it was an answer to a
different question. So each operation on the geometry façade declares what
its result must be -- a cut makes the target smaller, a fuse never does, an
intersection is never bigger than either side -- and one place holds it to
that. Validity, whether OpenCASCADE accepts the shape at all, is checked
once per build and blamed on the first feature that broke it; it is what
found the bottle example shipping a self-intersecting wire on its thread,
with the right volume and one solid.

## Risky operations are tried in a second process first

OCCT sometimes segfaults inside a fillet's `Build()` rather than refusing,
and a segfault takes the session and the unsaved document with it. The
case that motivated this: a block whose edges were rounded thirty-three at
a time built, and the same block with a thirty-fourth killed the process.
OCCT's own pre-check reports every contour healthy; a larger stack does not
help; rounding the edges one at a time is slower and rounds fewer of them,
because each fillet changes the topology the next is selected against.

So `geometry/solids/sentry.py` keeps a helper process and asks it first.
If the helper comes back, the caller does the work itself, with its own
naming; if the helper dies, the caller refuses instead of following it.
The helper is only allowed to answer yes or no: shipping a shape and its
names across a pipe and trusting the topology to come back in order is
the one thing this kernel must not do. The offsetter behind `shell` turned
out to have the same failure, so it goes through the sentry too. Set
`CAD_SENTRY=0` to turn it off.

## What a body knows about itself

A `Body` carries provenance beside its geometry -- the stock a sheet metal
part is cut from, the bends made in it, how closely a patch met its
neighbours, what an assembly's mates left free -- because none of it can
be recovered from the shape. It did not survive every operation the same
way: booleans concatenated it, transforms let the last part win, hand-built
bodies dropped it. Mirroring a sheet metal bracket onto itself doubled its
bends, and the blank was cut with twice the material a bend eats. Now a
note declares how two of it combine, and a record that arrived from more
than one input is marked as no longer the whole of the part, so a flat
pattern refuses instead of laying out half of one.

An earlier attempt asked whether a note is still true after the body has
moved; measuring said a translated and a patterned bracket lay out the
identical blank, so that question was dropped.

## Mates are constraints, solved together

A sketch always reported its freedom; an assembly did not. `mate` computed
one transform and never asked whether the answer was unique, so a bush held
by one concentric mate came out at some angle with nothing saying it could
have been any other, and a second mate on the same part simply replaced the
first. Mates are constraints now: each ungrounded part carries six
unknowns, each mate contributes residuals, Gauss-Newton drives them to zero
from the ordered placement as a seed, and the Jacobian's rank says what is
actually constrained. Leftover freedom in a sketch is an omission; in an
assembly it is usually the mechanism, so it is reported and never refused.
What is refused is a set of mates that cannot all be true at once.

The kinds grew from three to thirteen and the seed had to grow with them.
Four hinges in a loop seeded in order left the last one 100 mm open, and the
polish over every part's six unknowns walked into a twisted compromise with
every mate a little off and called it a conflict. Over the spins the ordered
seed leaves free, the chain's own mates stay exact, so closing the loop is a
Gauss-Newton on three angles, restarted from a few angles apart because a
straight chain is a saddle for it. A washer seeded into its bore first came
out upside down when the fastened mate came second, so mates that fix which
way up a part is seed before those that only say where its axis lies.

## A drive is a mate

Holding a crank at an angle is one more equation -- the part has turned this
far about this axis since the rest pose -- so it is solved with the mates
rather than applied after them, and the rank says the mechanism is then
fully determined. The angle is measured against the rest pose and unwrapped
from one step to the next, so a drive can go round twice; without that a
turn past a half turn read as a turn back and the solver fought it.

## A bend is a feature, not a shape

A flange records the edge it was bent at, the angle, the inside radius and
the length, and the flat pattern is computed from those numbers rather than
by unfolding the geometry afterwards -- the developed length depends on how
the material stretched, which no amount of looking at the solid recovers.
`developed length = A * (R + K * T)`, with `K` the neutral-axis factor, a
property of the material and the tooling, so a flange takes it.

## Surfaces come back to solids

The shapes a designer reaches for when a solid will not do -- a patch
across an opening, a skin lofted between profiles, a face pulled off and
rebuilt -- are open, and every one has to come back to a solid: make a
surface, work on it, sew and thicken or cap it. An open shape has boundary
edges with one face rather than two, and those are named too
(`skin/side|open`), because they are what the next operation refers to.

## Threads are geometry when they have to be

A tapped hole used to be a cylinder at the tapping diameter plus a note on
the drawing, which is right for a part cut on a machine and wrong for a
part that will be printed, rendered, or checked for clearance. Threads can
be cut for real on any cylindrical face by name; the sense -- into a hole
or onto a shaft -- is read off the face, which knows which side its
material is on.

## Requirements are status, not gates

`asserts` guard the parameters and a study's `require` guards a physics
result; between them was a gap for the things a designer says first --
under 150 grams, fits a 120 mm box, prints without supports. A requirement
is a row the kernel measures on every build and reports beside the
geometry; an edit that breaks one is not refused, because a part half-way
through being made is allowed to be too heavy. Its comparison is called
`compare` because `op` is the line protocol's word for the operation.

## Sketches refuse what they cannot pin down

Segments carry names, and those names become the faces the extrusion makes,
so a fillet picked on `profile/right` survives any change to the sketch's
dimensions. An under-constrained sketch is refused unless the document says
it may move: a sketch that silently solves is how CAD models become
unpredictable. A drawn sketch is dimensioned automatically, one constraint
at a time while freedom remains, so it comes out fully determined and
editable.

## Mechanisms are sketches with continuation

A four-bar closes in two configurations, and a solver started fresh picks
the nearer one, which can flip between frames; each frame is seeded from
the previous solution. A Geneva wheel's engagement is declared, not
solved. Gear assembly rules are refused by name with the failing numbers.

## The workspace fence

    An assistant given this cannot write files, run commands or reach
    anything outside the document it opened.

Two thirds of that was true when it was first written: `save`, the exports
and `open` took any path. A served session (the line protocol, the MCP
server) now has a root, every path an operation takes is resolved inside
it after symlinks, and the files a *document* names -- a STEP to import, a
part to bring in -- go through the same fence. It is a fence against an
assistant asked to save a bracket writing to a dotfile, not a security
boundary against code sharing the interpreter. A library `Session()` is
not confined, because that is somebody's own script. Blender's own kernel
runs unfenced -- its paths come from a file dialog -- and the fence for
requests arriving over the bridge is on the bridge.

## The bridge admits by token

The add-on's loopback socket once served any connection, and loopback is
every process on the machine -- in WSL and in containers, more than that.
It writes a random token to a file only this user can read when it starts
listening, the assistant's command names the file, and a connection's
first line has to say it. Requests may not carry the client's own
parameters (`timeout` through `**args` once hung the main thread), nor a
file outside the open document's folder.

## The cache key files each parent under its argument

A feature's cache key hashes its own resolved arguments and its parents'
keys. As a bare list in order of appearance, `cut{target: a, tool: b}` and
`cut{tool: a, target: b}` were one key, and a shared cache handed the
second document the first one's solid. Each parent's key is now filed
under the argument it sits in, and the values left out of the hash are
chosen by where they sit, not by whether their text equals a feature's id.

## The file is written in its own unit, and comes back byte for byte

The kernel works in millimetres and always will: OCCT's tolerances are
absolute, and a model built in inches would be solved a thousand times
coarser than one built in metres. So a unit is a property of the document,
converted on the way in and back on the way out. Until the two were made
inverses, saving an inch document multiplied it by 25.4 -- read converted,
write said `unit: "in"` over millimetre numbers -- and again on every save
after that. Nothing in the suite had compared a document to itself
through a save; `test_format.py` does now, for every shipped example.
Unknown keys are kept for the same reason: a document written by a newer
build, or by a person with a field of their own, used to come back from
its first save missing whatever could not be read.

## Measuring the build, and what it found

The scaling table in architecture.md was measured rather than guessed, and
the first run of it was half again as slow: OCCT's two-shape boolean
constructor performs the operation, and the `Build()` underneath it -- the
line that reads like the one that starts the work -- was running every
boolean a second time. A thread that took 16 seconds took 10 after.

## Comments say the rule, not the story

Code comments here say the condition the code keeps and stop. What used
to be wrong, why the old way was tempting, and how the new way is better
belong in the commit that made the change, in [caught.md](caught.md), or on
this page -- a paragraph of it above every function doubled the files and
hid the one line that mattered.
