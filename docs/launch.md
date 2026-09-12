# Getting it in front of people

The plan is small on purpose: $5 on Superhive, the source public on GitHub,
and a Discord where the first buyers say what broke. A web site, a docs site,
a logo and a terms page are all later. What matters is whether a Blender user
who has never heard of this reads the store page, buys it, installs it, and
turns up in the Discord.

## What has to be true before it goes up

Done

* the zip: `python tools/package.py build` writes `build/cadcore_bridge.zip`
* the Blender it needs is declared: `blender_version_min` in the manifest
* BSD-3-Clause, and the licence the kernel's libraries are under is named in
  the README
* the manifest says why it wants the network and the disk, which a store shows
  before the first click
* `tools/verify/verify_retail.py` installs the zip into a Blender that has
  never seen it and runs the whole loop -- the buyer's copy, not the checkout

Not done

* **a video**. The store page is the video; everything else on it is a
  caption. `tools/render/render_steps.py` and `tools/render/compose_demo.py`
  are what make one.
* **Join the Discord, inside the add-on.** Without it the road from buying to
  saying what broke has a gap in the middle.
* the zip on GitHub Releases, and Issues switched on

## The store page

Lead with what it is, then the video, then the price. Long text loses.

### The line at the top

> **Real CAD in Blender -- ask for it, or build it by hand.**

Not "control Blender without digging through menus": that promises a tool for
working Blender faster, and what arrives is a mechanical CAD kernel. At $5 a
buyer who wanted the first one asks for a refund, and is right to.

### Under it

> A parametric CAD kernel inside Blender. Boxes, sketches, holes, fillets,
> shells, threads, sheet metal, assemblies -- built as a feature history you
> can go back into and change. Every face and edge keeps its name, so a hole
> you put on a face is still on that face after you make the part longer.
>
> If you have Claude Code or Codex signed in, you can ask for a part in words
> and it builds it through the same operations your mouse uses, on the same
> undo history. Or never ask anything and model it by hand: the buttons come
> first and do not need it.
>
> Export STEP, STL or 3MF. Make a drawing. Check it will print first.

### Straight after the video, in its own box

> **Before you buy**
>
> * **Blender 5.1 or newer.** The kernel runs on Blender's own Python, which
>   became 3.13 in 5.1. It will not install on 4.x.
> * **The kernel is in the download.** One zip per platform, about 85 MB,
>   and nothing to press afterwards. There is a small zip too, for anyone who
>   would rather fetch the kernel themselves.
> * **Windows and Linux are tested.** Every push installs the zip into a fresh
>   Blender on both and runs the whole round trip. **The macOS zip is built
>   but not tested**: the sketch solver publishes no Mac build, so we compile
>   that one piece ourselves and put it in the zip -- there is still nothing
>   for you to install. It has not been run on a Mac by us. Buy it there only
>   if that is a trade you want to make.
> * **Early Access.** It does what the video shows and what the tests cover,
>   and it is still moving.
> * **Open source, BSD-3-Clause.** The whole thing. You are paying for the
>   packaged build and for it to keep going.

Saying the version and the download up front costs a few sales and saves
every refund. Both are the first thing a buyer meets.

### The list

* Pick a face, then act on it -- hole, pocket, shell, draft, thread, mirror, pattern
* Drag a fillet and watch the number, or type it
* Change any dimension in the history and the part rebuilds
* Sketches solve: drag a point, add a constraint, name a dimension
* Sheet metal with a flat pattern; assemblies with mates
* Stress and interference answered **on the CAD faces**, by name
* STEP for the next CAD, STL or 3MF for the slicer, drawings for the shop
* Printability answered before you slice: thin walls and overhangs, by face name

## The road

    Superhive $5 -> install -> Join the Discord, from the add-on -> feedback
    GitHub -> Releases -> install -> the same Discord

Four channels is enough: `#welcome` `#feedback` `#bugs` `#ideas`.

## Before the price goes up

None of these stop a $5 Early Access release. All of them would stop a
higher one.

* **macOS is untested.** planegcs ships wheels for Linux and Windows only,
  so `.github/workflows/wheels.yml` builds that one wheel on a Mac runner and
  `tools/package.py --macos-arm64 --extra-wheels=<folder>` puts it in the zip.
  A buyer installs nothing extra. What is still missing is a Mac that runs
  `verify_addon.py`: the Intel wheel is cross-compiled and never executed,
  and the arm64 one is only imported. Until one of those runs the round trip,
  the store page says untested, which is right for Early Access and not for a
  higher price.
* **The Ask box runs somebody else's CLI on somebody else's plan.** Read the
  Anthropic and OpenAI terms again on the day of the sale -- they moved three
  times in 2026 -- and keep the page's wording conditional. It is a thing the
  add-on can use, not a thing it promises.
* **About a dozen operators are walked by neither soak nor verify**:
  draw_sketch, thicken, measure, reattach among them. They are
  the ones a first buyer is least likely to reach and the ones with no net
  under them.

## What to write down about the first ten

Where they came from, bought or downloaded, whether it installed, what they
made first, where they got stuck, whether they came back. Two more that this
add-on needs:

* **which Blender they are on** -- how many bought it on 4.x and could not
  install it says whether 4.x is worth the work
* **whether the kernel install finished** -- the likeliest place to lose
  somebody after they have already paid
