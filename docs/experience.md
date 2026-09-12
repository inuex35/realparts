# How it should feel to use

The goal is not a look. The goal is less between the person and the part.

A person does not want to "use the hole feature". They want a hole in the
face they are looking at. So the order of every action is

    target -> action

The same action must also be discoverable before a target is picked. A person
who chooses Hole first is guided to a planar face; a person who already picked
one goes straight to its settings. Never ask for the same target twice.

## The rules

1. **Touch the thing itself.** Click a face and push it. Pick an edge and
   round it. Click a hole and change its size. Drag a part. Click a number on
   the model and type over it. Nobody should have to think "which menu is
   this in".

2. **Count every step between wanting and having.** Not only clicks: menu
   levels, tab changes, mode changes, hunting for the tool, remembering a
   Blender idea, remembering an order of steps, working out what a setting
   means, telling the assistant again what is already on screen. All of it
   is cost. Cut it.

3. **Hide depth; do not remove it.** Do not put everything on one screen --
   that moves the mess onto the person. Rare and advanced things may sit
   deeper. What matters is that what is needed now shows up where it is
   needed now. Simple for a beginner, and still reachable for an expert.
   Three cues bring a deeper thing forward, and nothing else does:
   the *selection* (two faces picked: Mate; a cylinder picked: Thread),
   the *result* (a requirement added: Studies appear; a sheet feature:
   Flat Pattern appears), and the *assistant* (ask for a lighter part and
   `optimize` runs, with one line saying it did). Everything else waits
   in the stable Create, Modify, Assemble, Inspect and Export menus. These
   entries stay visible; the Selection Actions panel changes with the pick.
   Utilities holds maintenance, not modelling features.

4. **The selection is the context.** Never make someone say again what the
   screen already knows. A picked face means: push/pull, hole, pocket, a
   plane off it, a sketch on it, ask about it. A picked edge means: fillet,
   chamfer, a dimension. Read the state; offer what fits it.

5. **The assistant is not a chat window.** It is direct manipulation with
   words. Pick a face, type "M4 here", and "here" is that face. The person
   never writes "the object CameraMount, face 182, a 4.5 mm hole". What
   Blender knows -- the selection, the object, faces, edges, positions, the
   tool in hand, the view, the CAD history -- goes to the assistant on its
   own. `select -> do` and `select -> ask` are one way of working.

6. **Hands and assistant use the same commands.** Everything the panel does
   and everything the assistant does goes through the same operations
   (`add_hole`, `move_face`, `add_fillet`, `set_parameter`, ...). No side
   door for the assistant. Then undo, history, editing a number later, and
   watching what the assistant did are all one thing.

7. **Show the result at once, on the part.** Do -> see -> adjust -> keep.
   Not "type in a dialog, apply, look, go back to the dialog".

8. **Nobody manages modes.** If a mode is needed inside, that is our
   problem, not theirs. Move to the right state from what is picked and what
   is being done.

9. **One way of working, everywhere.** Pick -> handle it -> type a number if
   you like -> Enter keeps, Esc drops. The same for a part, an assembly, a
   board.

10. **Design the experience, then the UI.** Start from what people want --
    a hole, thicker, longer, rounder, put this here, move that, fix this bit
    for me -- and measure the road from wanting to having. Then shorten it.

## Where the road is long today, and where it goes

Measured on the add-on as it is. "Steps" counts clicks, menus, mode changes
and things to remember; a step in brackets is one the person has to know
about Blender.

| want | today | steps | next |
|---|---|---|---|
| a hole in this face | select body, [Tab], [face mode], click face, CAD menu, Hole, F9 for size | 7 | right-click the face: Hole; or the Hole tool: click where it goes, wheel for size |
| push this face out | same first four, then Press/Pull | 5 | click the face, drag it (the Press/Pull tool already does this in object mode) |
| round this edge | select body, [Tab], [edge mode], click edge, CAD menu, Fillet, F9 for radius | 7 | right-click the edge: Fillet; or the Fillet tool: drag the radius |
| make it longer | find the parameter in the sidebar, type | 2 | click the number on the model, type (done for sketch dimensions; every feature next) |
| thicker | same as above | 2 | same |
| put a part here | assembly written by hand | many | drag it with the Move Part tool (done); mates by clicking two faces (next) |
| "fix this bit" | select, then describe the selection to the assistant in words | 3 + words | select, type "M4 here": the selection goes with the question (done) |
| start a part | New, Start, box | 3 | keep |

What changed first, because it removes the most: the assistant now gets the
selection and the document with every question; the right-click menu on the
body offers only what fits the picked faces or edges; a click on a
dimension edits it on the model.

## The questions to ask before adding anything

1. Is the road from wanting to having shorter?
2. Is there less to remember?
3. Could this be done on the part itself instead?
4. Is the person being asked for something the selection already says?
5. Did this add a mode or a level that was not needed?
6. Would a beginner guess the next step? Is it still fast for an expert?
7. Do hands and the assistant go through the same command?

When in doubt, remove a step from something that exists rather than add a
feature. The aim is a tool that feels less like running software and more
like holding the part.
