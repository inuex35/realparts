# Worked examples

[← README](../README.md)

## A worked example: the sheet clip

`examples/sheet_knob.json` and `examples/sheet_collar.json` are a tarp-style
clip for holding a bed sheet down, and `examples/sheet_clip.json` is the two of
them assembled in the position they take under load.

It is here because it exercises the parts of this that matter to a real print
rather than to a test. The knob is a revolve whose head flares at 52 degrees --
not the 45 the geometry wanted, because `python -m cadcore print-check` measured 29% of the
surface unsupported at 45 and 2.6% at 52. The collar's mouth, keyway and
elastic slot are three pockets rather than one sketch with three overlapping
loops, so each cut is an ordinary boolean. Every edge on both faces is
chamfered in two features -- `{"of_face": "plate/top"}` names them all -- so no
printed corner runs against the fabric.

The fit is a claim the model can check: `python -m cadcore interference examples/sheet_clip.json`
says the two parts do not touch, and the assembled volume is the sum of the two
parts to the milligram, which is what "the neck passes through the slot" means
arithmetically.

## Three assemblies

`examples/linkage.json` is a four-bar linkage: four copies of
`examples/parts/link.json` at four lengths, hinged in a loop, with one
freedom left and `crank_angle` driving it. `python -m cadcore drive
examples/linkage.json crank --turn 360 --frames 12 --collisions` walks the
crank round and says where the rocker goes and whether anything touches.

`examples/assembly_nested.json` is `assembly.json` twice on a rail, one with
a bigger bore: names nest (`left:base:plate/+z`), the bill of materials
nests, and the STEP goes out as an assembly of assemblies.

`examples/fastened.json` is an M6 socket head screw on a washer in an M6
clearance hole, all three from the same table, so `interference` says
clearance and the bill lists the screw by its standard name.
