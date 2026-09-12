// What the pick draws on the part, and the drags made on it: the arrow (push/pull), the two
// edge grips (round, chamfer), the plane mark (a work plane off the face), the hole ring.
import React, { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { useThree } from "@react-three/fiber";
import { Line, Html } from "@react-three/drei";
import { useStore, featureOf } from "./store.js";
import { op } from "./api.js";
import { LiveDrag, Snapper, toUV, to3D, snap } from "./drags.js";

const YELLOW = "#f2c14e", GREEN = "#6fd39a", ORANGE = "#f08a4b", BLUE = "#7aa2f7";
export const HOLE_STEPS = [[3.4, "M3"], [4.5, "M4"], [5.5, "M5"], [6.6, "M6"], [9.0, "M8"], [11.0, "M10"], [13.5, "M12"]];

const v3 = (a) => new THREE.Vector3(...a);

function useScreen() {
  // world -> region pixels, and millimetres per pixel at a point
  const { camera, size } = useThree();
  return useMemo(() => ({
    project: (p) => { const q = v3(p).project(camera); return { x: (q.x + 1) / 2 * size.width, y: (1 - q.y) / 2 * size.height }; },
    mmPerPixel: (p) => {
      const right = new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld, 0);
      const a = v3(p).project(camera), b = v3(p).add(right).project(camera);
      return 2 / (Math.hypot((b.x - a.x) * size.width, (b.y - a.y) * size.height) || 1e-9);
    },
  }), [camera, size]);
}

function useFrame(face) {
  const [frame, setFrame] = useState(null);
  const revision = useStore((s) => s.doc && s.doc.revision);
  useEffect(() => {
    let live = true;
    if (!face) { setFrame(null); return undefined; }
    op("face_frame", { face }).then((f) => live && setFrame(f)).catch(() => live && setFrame(null));
    return () => { live = false; };
  }, [face, revision]);
  return frame;
}

function useGrab(controls) {
  // pointer capture on a mark, the orbit held still while the drag runs. A drag the menu
  // started has no button held: the mouse is followed until a click.
  return (e, onMove, onUp) => {
    if (controls) controls.enabled = false;
    if (!e.nativeEvent) {
      const move = (ev) => onMove(ev);
      const down = (ev) => {
        ev.stopPropagation(); ev.preventDefault();
        window.removeEventListener("pointermove", move); window.removeEventListener("pointerdown", down, true);
        window.removeEventListener("click", swallow, true);
        if (controls) controls.enabled = true;
        onUp(ev);
      };
      const swallow = (ev) => { ev.stopPropagation(); ev.preventDefault(); window.removeEventListener("click", swallow, true); };
      window.addEventListener("pointermove", move); window.addEventListener("pointerdown", down, true);
      window.addEventListener("click", swallow, true);
      return;
    }
    e.stopPropagation();
    const target = e.nativeEvent.target;          // the canvas: the three.js object has no DOM events
    target.setPointerCapture(e.pointerId);
    const move = (ev) => onMove(ev);
    const up = (ev) => {
      target.releasePointerCapture(ev.pointerId);
      target.removeEventListener("pointermove", move); target.removeEventListener("pointerup", up);
      if (controls) controls.enabled = true;
      onUp(ev);
    };
    target.addEventListener("pointermove", move); target.addEventListener("pointerup", up);
  };
}

function Tip({ at, radius, colour, title, onDown, children }) {
  const [hover, setHover] = useState(false);
  const setHud = useStore((s) => s.setHud);
  return (
    <mesh position={at} onPointerDown={onDown}
      onPointerOver={(e) => { e.stopPropagation(); setHover(true); setHud(title); document.body.style.cursor = "grab"; }}
      onPointerOut={() => { setHover(false); setHud(""); document.body.style.cursor = ""; }}>
      {children || <sphereGeometry args={[radius, 20, 14]} />}
      <meshStandardMaterial color={colour} emissive={colour} emissiveIntensity={hover ? 0.6 : 0.25} roughness={0.5} />
    </mesh>
  );
}

// --- the flat face: arrow, plane mark -----------------------------------------------
function FaceMarks({ face, frame, size, controls }) {
  const screen = useScreen();
  const grab = useGrab(controls);
  const o = frame.origin, n = frame.normal, x = frame.x_axis;
  const reach = size * 0.22, r = size * 0.014;
  const tip = [0, 1, 2].map((i) => o[i] + n[i] * reach);
  const planeAt = [0, 1, 2].map((i) => o[i] + x[i] * reach * 0.55 + n[i] * size * 0.01);

  const pushPull = async (e) => {
    const a = screen.project(o), b = screen.project([0, 1, 2].map((i) => o[i] + n[i] * 10));
    const axis = { x: b.x - a.x, y: b.y - a.y }, length = Math.hypot(axis.x, axis.y);
    if (length < 1e-3) { useStore.getState().say("the face is edge-on; orbit a little and try again", true); return; }
    const perPixel = 10 / length; axis.x /= length; axis.y /= length;
    const start = { x: e.clientX, y: e.clientY };
    const faces = (await op("describe_faces")).faces;
    const snapper = new Snapper(faces, frame, face);
    const drag = await new LiveDrag({ label: `Push/Pull ${face}`,
      make: (v) => ["move_face", { face, distance: Math.round(v * 1000) / 1000 }],
      edit: (v) => ({ distance: Math.round(v * 1000) / 1000 }),
      format: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)} mm` }).begin();
    useStore.getState().setHud(`Push/Pull ${face}: drag along the normal | Ctrl fine | type a number | Enter keeps | Esc`);
    grab(e, (ev) => {
      if (drag.typed) return;
      let d = ((ev.clientX - start.x) * axis.x + (ev.clientY - start.y) * axis.y) * perPixel;
      d = snap(d, ev.ctrlKey ? 0.1 : 0.5);
      let flush = null;
      if (!ev.ctrlKey) [d, flush] = snapper.distance(d, perPixel);
      if (Math.abs(d) < 1e-6) return;
      drag.preview(d, { x: ev.clientX, y: ev.clientY });
      if (flush) drag.say(`${d >= 0 ? "+" : ""}${d.toFixed(2)} mm, flush with ${flush}`);
    }, () => { drag.finish(); useStore.getState().setHud(""); });
  };

  // a drag measured along the face's normal on the screen: the plane mark, a split, an emboss
  const alongNormal = async (e, { label, make, edit, format, step = 1, hud }) => {
    const a = screen.project(o), b = screen.project([0, 1, 2].map((i) => o[i] + n[i] * 10));
    const axis = { x: b.x - a.x, y: b.y - a.y }, length = Math.hypot(axis.x, axis.y);
    if (length < 1e-3) { useStore.getState().say("the face is edge-on; orbit a little and try again", true); return; }
    const perPixel = 10 / length; axis.x /= length; axis.y /= length;
    const start = { x: e.clientX, y: e.clientY };
    const drag = await new LiveDrag({ label, make, edit, format }).begin();
    useStore.getState().setHud(hud);
    grab(e, (ev) => {
      if (drag.typed) return;
      const d = snap(((ev.clientX - start.x) * axis.x + (ev.clientY - start.y) * axis.y) * perPixel, ev.ctrlKey ? step / 10 : step);
      if (Math.abs(d) < 1e-6) return;
      drag.preview(d, { x: ev.clientX, y: ev.clientY });
    }, () => { drag.finish(); useStore.getState().setHud(""); });
  };
  const planeOff = (e) => alongNormal(e, { label: `Plane off ${face}`,
    make: (v) => ["add_plane", { face, offset: Math.round(v * 1000) / 1000 }],
    edit: (v) => ({ offset: Math.round(v * 1000) / 1000 }),
    hud: `Work plane off ${face}: drag it away from the face | Enter keeps | Esc` });
  const splitHere = (e) => alongNormal(e, { label: `Split off ${face}`,
    make: (v) => ["add_split", { face, offset: Math.round(v * 1000) / 1000, keep: "below" }],
    edit: (v, made) => ["set_parameter", { name: `${made}_offset`, value: Math.round(v * 1000) / 1000 }],
    format: (v) => `${v >= 0 ? "+" : ""}${v.toFixed(2)} mm`,
    hud: `Split off ${face}: drag the cut into the part; what is under it stays | type a number | Enter keeps | Esc` });
  const embossText = (e, text) => alongNormal(e, { label: `${text} on ${face}`, step: 0.5,
    make: (v) => ["add_emboss", { face, text, depth: Math.round(v * 1000) / 1000 }],
    edit: (v) => ({ depth: Math.round(Math.abs(v) * 1000) / 1000, cut: v < 0 }),
    format: (v) => `${v >= 0 ? "raised" : "cut"} ${Math.abs(v).toFixed(2)} mm`,
    hud: `${text} on ${face}: drag out to raise, in to cut | type a number | Enter keeps | Esc` });
  // a drag measured sideways: degrees of draft away from this face's plane
  const draftFromHere = async (e) => {
    const x0 = e.clientX;
    const drag = await new LiveDrag({ label: `Draft from ${face}`,
      make: (v) => ["add_draft", { parting_face: face, angle: Math.round(v * 100) / 100 }],
      edit: (v) => ({ angle: Math.round(v * 100) / 100 }), format: (v) => `${v.toFixed(1)}°` }).begin();
    useStore.getState().setHud(`Draft from ${face}: the walls lean away from its plane; drag right for more | type degrees | Enter keeps | Esc`);
    grab(e, (ev) => {
      if (drag.typed) return;
      const v = snap(Math.max(0, (ev.clientX - x0) / 12), ev.ctrlKey ? 0.1 : 0.5);
      if (v <= 0) return;
      drag.preview(v, { x: ev.clientX, y: ev.clientY });
    }, () => { drag.finish(); useStore.getState().setHud(""); });
  };

  const pending = useStore((s) => s.pendingDrag);
  useEffect(() => {
    if (!pending || !["push", "plane", "split", "emboss", "draft"].includes(pending.kind)) return;
    useStore.setState({ pendingDrag: null });
    const at = { clientX: pending.x, clientY: pending.y };
    if (pending.kind === "push") pushPull(at);
    else if (pending.kind === "plane") planeOff(at);
    else if (pending.kind === "split") splitHere(at);
    else if (pending.kind === "emboss") embossText(at, pending.text);
    else draftFromHere(at);
  }, [pending]);   // eslint-disable-line

  const square = useMemo(() => {
    const s = size * 0.05, y = [n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0]];
    const c = (a, b) => [0, 1, 2].map((i) => planeAt[i] + x[i] * a * s + y[i] * b * s);
    return [c(-1, -1), c(1, -1), c(1, 1), c(-1, 1), c(-1, -1)].map((p) => v3(p));
  }, [frame, size]);   // eslint-disable-line

  return (
    <group>
      <Line points={[v3(o), v3(tip)]} color={YELLOW} lineWidth={2} />
      <Tip at={tip} radius={r} colour={YELLOW} title={`${face}: drag the arrow to push or pull`} onDown={pushPull} />
      <Line points={square} color={BLUE} lineWidth={1.5} />
      <Tip at={planeAt} radius={r * 0.7} colour={BLUE} title={`${face}: drag the plane mark for a work plane off this face`} onDown={planeOff}>
        <boxGeometry args={[r * 1.6, r * 1.6, r * 0.3]} />
      </Tip>
    </group>
  );
}

// --- the edge grips: green rounds, orange takes the corner off ------------------------
function EdgeGrips({ edges, at, lean, size, controls }) {
  const screen = useScreen();
  const grab = useGrab(controls);
  const r = size * 0.014;
  const spot = (k) => [0, 1, 2].map((i) => at[i] + lean[i] * size * (0.09 + 0.07 * k));

  const start = (kind) => async (e) => {
    const perPixel = screen.mmPerPixel(at);
    const x0 = e.clientX;
    const drag = await new LiveDrag({ label: `${kind} ${edges.length} edge(s)`,
      make: (v) => ["add_fillet", { edges, radius: Math.round(v * 1000) / 1000, kind }],
      edit: (v) => ({ [kind === "fillet" ? "radius" : "distance"]: Math.round(v * 1000) / 1000 }),
      format: (v) => `${v.toFixed(1)} mm` }).begin();
    useStore.getState().setHud(`${kind === "fillet" ? "Round" : "Chamfer"} ${edges.length} edge(s): drag right for more | type a number | Enter keeps | Esc`);
    grab(e, (ev) => {
      if (drag.typed) return;
      const value = snap(Math.max(0, (ev.clientX - x0) * perPixel), ev.ctrlKey ? 0.1 : 0.5);
      if (value <= 0) return;
      drag.preview(value, { x: ev.clientX, y: ev.clientY });
    }, () => { drag.finish(); useStore.getState().setHud(""); });
  };
  const pending = useStore((s) => s.pendingDrag);
  useEffect(() => {
    if (!pending || !["fillet", "chamfer"].includes(pending.kind)) return;
    useStore.setState({ pendingDrag: null });
    start(pending.kind)({ clientX: pending.x, clientY: pending.y });
  }, [pending]);   // eslint-disable-line
  return (
    <group>
      <Tip at={spot(0)} radius={r} colour={GREEN} title="drag the green disc to round these edges" onDown={start("fillet")}>
        <cylinderGeometry args={[r * 1.1, r * 1.1, r * 0.35, 24]} />
      </Tip>
      <Tip at={spot(1)} radius={r} colour={ORANGE} title="drag the orange corner to chamfer these edges" onDown={start("chamfer")}>
        <coneGeometry args={[r * 1.1, r * 1.4, 4]} />
      </Tip>
    </group>
  );
}

// --- the hole tool: a ring follows the cursor on the face; click drills; the wheel sizes it
function HoleTool({ tool, frame, controls }) {
  const { camera, gl } = useThree();
  const [cursor, setCursor] = useState(null);
  const [snapped, setSnapped] = useState(null);
  const snapper = useRef(null);
  const screen = useScreen();
  const say = useStore((s) => s.say), setHud = useStore((s) => s.setHud), tryOp = useStore((s) => s.tryOp);
  useEffect(() => {
    op("describe_faces").then((r) => { snapper.current = new Snapper(r.faces, frame, tool.face); });
  }, [tool.face, frame]);
  const step = tool.index;
  useEffect(() => {
    const [d, name] = HOLE_STEPS[step];
    setHud(`Hole on ${tool.face}: click to drill ${name} clearance (${d} mm) | wheel: size | Esc to finish`);
    const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(v3(frame.normal), v3(frame.origin));
    const ray = new THREE.Raycaster();
    const canvas = gl.domElement;
    const track = (ev) => {
      const rect = canvas.getBoundingClientRect();
      ray.setFromCamera(new THREE.Vector2(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1), camera);
      const hit = ray.ray.intersectPlane(plane, new THREE.Vector3());
      if (!hit) { setCursor(null); return null; }
      let uv = toUV(hit.toArray(), frame), label = null;
      if (!ev.ctrlKey && snapper.current) [uv, label] = snapper.current.uv(uv, screen.mmPerPixel(hit.toArray()));
      setCursor(uv); setSnapped(label);
      return uv;
    };
    const click = async (ev) => {
      if (ev.button !== 0) return;
      const uv = track(ev); if (!uv) return;
      ev.stopPropagation();
      const [, name] = HOLE_STEPS[useStore.getState().tool.index];
      const out = await tryOp("add_hole", { face: tool.face, standard: name, fit: "normal", at: [Math.round(uv[0] * 100) / 100, Math.round(uv[1] * 100) / 100] });
      if (out) say(`${name} hole on ${tool.face} at (${uv[0].toFixed(1)}, ${uv[1].toFixed(1)})`);
    };
    const wheel = (ev) => {
      ev.preventDefault(); ev.stopPropagation();
      useStore.setState((s) => ({ tool: { ...s.tool, index: (s.tool.index + (ev.deltaY < 0 ? 1 : -1) + HOLE_STEPS.length) % HOLE_STEPS.length } }));
    };
    const key = (ev) => { if (ev.key === "Escape" || ev.key === "Enter") { useStore.setState({ tool: null }); setHud(""); } };
    if (controls) controls.enabled = false;
    canvas.addEventListener("pointermove", track); canvas.addEventListener("click", click, true);
    canvas.addEventListener("wheel", wheel, { passive: false, capture: true }); window.addEventListener("keydown", key);
    return () => {
      canvas.removeEventListener("pointermove", track); canvas.removeEventListener("click", click, true);
      canvas.removeEventListener("wheel", wheel, { capture: true }); window.removeEventListener("keydown", key);
      if (controls) controls.enabled = true;
    };
  }, [tool.face, step, frame, controls]);   // eslint-disable-line
  if (!cursor) return null;
  const [d] = HOLE_STEPS[step];
  const ring = [];
  for (let k = 0; k <= 48; k++) { const a = (k / 48) * Math.PI * 2; ring.push(v3(to3D([cursor[0] + Math.cos(a) * d / 2, cursor[1] + Math.sin(a) * d / 2], frame))); }
  return (
    <group>
      <Line points={ring} color={YELLOW} lineWidth={2} />
      {snapped && <mesh position={to3D(cursor, frame)}><sphereGeometry args={[d * 0.08, 12, 8]} /><meshBasicMaterial color={GREEN} /></mesh>}
    </group>
  );
}

// --- the selected feature's numbers, beside the faces it made ----------------------------
function Dimensions({ mesh, size }) {
  const doc = useStore((s) => s.doc), feature = useStore((s) => s.feature), tryOp = useStore((s) => s.tryOp);
  const [editing, setEditing] = useState(null);
  const f = doc && doc.features.find((x) => x.id === feature);
  const anchor = useMemo(() => {
    if (!mesh || !f) return null;
    const table = mesh.face_table || [];
    // the faces the feature made; a feature that made none (a moved face, a plane) is
    // anchored at the faces it names
    let wanted = new Set(table.filter((n) => featureOf(n) === f.id));
    if (!wanted.size) {
      const named = JSON.stringify(f.args).match(/"[^"]*\/[^"]*"/g) || [];
      for (const q of named) for (const n of q.slice(1, -1).split("|")) if (table.includes(n)) wanted.add(n);
    }
    const total = [0, 0, 0]; let count = 0;
    mesh.triangles.forEach((t, k) => {
      if (!wanted.has(table[mesh.triangle_face[k]])) return;
      for (const v of t) { const p = mesh.vertices[v]; total[0] += p[0]; total[1] += p[1]; total[2] += p[2]; count++; }
    });
    return count ? total.map((c) => c / count) : null;
  }, [mesh, f]);
  if (!f || !anchor) return null;
  const numbers = Object.entries(f.args).filter(([, v]) => typeof v === "number" || (typeof v === "string" && doc.parameters[v] !== undefined));
  if (!numbers.length) return null;
  const commit = (key, raw) => {
    setEditing(null);
    const value = Number(raw);
    if (!Number.isFinite(value)) return;
    const was = f.args[key];
    if (typeof was === "string") tryOp("set_parameter", { name: was, value });      // the number is a parameter
    else tryOp("edit_feature", { feature_id: f.id, args: { [key]: value } });
  };
  return (
    <group>
      <mesh position={anchor}><sphereGeometry args={[size * 0.006, 10, 8]} /><meshBasicMaterial color={YELLOW} /></mesh>
      <Html position={anchor} style={{ pointerEvents: "auto" }} zIndexRange={[3, 0]}>
        <div className="dims">
          {numbers.map(([key, v]) => {
            const value = typeof v === "string" ? doc.parameters[v] : v;
            const label = typeof v === "string" ? v : key;
            return editing === key
              ? <input key={key} autoFocus defaultValue={value} onBlur={(e) => commit(key, e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") commit(key, e.target.value); if (e.key === "Escape") setEditing(null); }} />
              : <div key={key} className="dim" title="click to type over it" onClick={() => setEditing(key)}>
                  <span className="k">{label}</span><span className="v">{tidy(value)}</span></div>;
          })}
        </div>
      </Html>
    </group>
  );
}

// --- a round face has no arrow: its drags come from the menu and run sideways ---------
function RoundFaceDrags({ face, mesh, controls }) {
  const screen = useScreen();
  const grab = useGrab(controls);
  const pending = useStore((s) => s.pendingDrag);
  useEffect(() => {
    if (!pending || !["emboss", "coil"].includes(pending.kind)) return;
    useStore.setState({ pendingDrag: null });
    const centre = mesh && mesh.vertices.length ? bbox(mesh).centre : [0, 0, 0];
    const perPixel = screen.mmPerPixel(centre), x0 = pending.x;
    (async () => {
      const drag = pending.kind === "emboss"
        ? await new LiveDrag({ label: `${pending.text} round ${face}`,
            make: (v) => ["add_emboss", { face, text: pending.text, depth: Math.round(v * 1000) / 1000 }],
            edit: (v) => ({ depth: Math.round(Math.abs(v) * 1000) / 1000, cut: v < 0 }),
            format: (v) => `${v >= 0 ? "raised" : "cut"} ${Math.abs(v).toFixed(2)} mm` }).begin()
        : await new LiveDrag({ label: `Coil round ${face}`,
            make: (v) => ["add_coil", { face, pitch: Math.round(v * 100) / 100 }],
            edit: (v, made) => ["set_parameter", { name: `${made}_pitch`, value: Math.round(v * 100) / 100 }],
            format: (v) => `pitch ${v.toFixed(1)} mm` }).begin();
      useStore.getState().setHud(pending.kind === "emboss"
        ? `${pending.text} round ${face}: drag right to raise, left to cut | type a number | Enter keeps | Esc`
        : `Coil round ${face}: drag right for a longer pitch | type a number | Enter keeps | Esc`);
      grab({ clientX: pending.x, clientY: pending.y }, (ev) => {
        if (drag.typed) return;
        let v = snap((ev.clientX - x0) * perPixel, ev.ctrlKey ? 0.1 : 0.5);
        if (pending.kind === "coil") v = Math.max(0.5, v);
        else if (Math.abs(v) < 1e-6) return;
        drag.preview(v, { x: ev.clientX, y: ev.clientY });
      }, () => { drag.finish(); useStore.getState().setHud(""); });
    })();
  }, [pending]);   // eslint-disable-line
  return null;
}

// --- the section tool: a plane parallel to the face follows the mouse; the cut is drawn ----
function SectionTool({ tool, frame, controls }) {
  const { gl } = useThree();
  const screen = useScreen();
  const setHud = useStore((s) => s.setHud), say = useStore((s) => s.say);
  useEffect(() => {
    const o = frame.origin, n = frame.normal;
    const a = screen.project(o), b = screen.project([0, 1, 2].map((i) => o[i] + n[i] * 10));
    const axis = { x: b.x - a.x, y: b.y - a.y }, length = Math.hypot(axis.x, axis.y) || 1e-9;
    const perPixel = 10 / length; axis.x /= length; axis.y /= length;
    setHud(`Section off ${tool.face}: move the mouse to slide the plane into the part | click to leave it | Esc`);
    const canvas = gl.domElement;
    let busy = false, next = null;
    const show = async (offset) => {
      if (busy) { next = offset; return; }
      busy = true;
      try { useStore.setState({ section: await op("section", { face: tool.face, offset }) }); }
      catch { useStore.setState({ section: null }); }
      busy = false;
      if (next !== null) { const again = next; next = null; show(again); }
    };
    const track = (ev) => {
      const rect = canvas.getBoundingClientRect();
      const d = ((ev.clientX - rect.left - a.x) * axis.x + (ev.clientY - rect.top - a.y) * axis.y) * perPixel;
      show(Math.round(Math.min(0, d) * 10) / 10);
    };
    const click = (ev) => {
      if (ev.button !== 0) return;
      ev.stopPropagation();
      const cut = useStore.getState().section;
      if (cut) say(`section ${cut.area_mm2.toFixed(1)} mm², outline ${cut.length_mm.toFixed(1)} mm`);
      useStore.setState({ tool: null }); setHud("");
    };
    const key = (ev) => { if (ev.key === "Escape" || ev.key === "Enter") { useStore.setState({ tool: null }); setHud(""); } };
    if (controls) controls.enabled = false;
    canvas.addEventListener("pointermove", track); canvas.addEventListener("click", click, true); window.addEventListener("keydown", key);
    return () => {
      canvas.removeEventListener("pointermove", track); canvas.removeEventListener("click", click, true); window.removeEventListener("keydown", key);
      if (controls) controls.enabled = true;
    };
  }, [tool.face, frame, controls]);   // eslint-disable-line
  return null;
}

function SectionLines() {
  const section = useStore((s) => s.section);
  if (!section) return null;
  return <group>{section.curves.map((c, k) => <Line key={k} points={c.points.map((p) => v3(p))} color={YELLOW} lineWidth={2.5} />)}</group>;
}

export default function Marks({ mesh, size }) {
  const { controls } = useThree();
  const picked = useStore((s) => s.picked), pickedEdges = useStore((s) => s.pickedEdges), shapes = useStore((s) => s.shapes);
  const tool = useStore((s) => s.tool), drag = useStore((s) => s.drag);
  const one = picked.length === 1 && shapes[picked[0]] === "plane" ? picked[0] : null;
  const frame = useFrame(one);
  const edgeLines = useMemo(() => {
    if (!mesh) return [];
    return pickedEdges.map((name) => (mesh.edges || {})[name]).filter(Boolean);
  }, [mesh, pickedEdges]);
  // the grips sit on the first picked edge's middle, or beside a picked face's centre
  const grip = useMemo(() => {
    if (edgeLines.length) {
      const line = edgeLines[0], mid = line[Math.floor(line.length / 2)];
      const centre = mesh.vertices.length ? bbox(mesh).centre : mid;
      const lean = normalise([mid[0] - centre[0], mid[1] - centre[1], mid[2] - centre[2] + size * 0.3]);
      return { at: mid, lean };
    }
    if (one && frame) return { at: frame.origin, lean: normalise([-frame.x_axis[0], -frame.x_axis[1], -frame.x_axis[2]]) };
    return null;
  }, [edgeLines, one, frame, mesh, size]);
  const [edgesOfFace, setEdgesOfFace] = useState([]);
  useEffect(() => {
    if (!one || pickedEdges.length) { setEdgesOfFace([]); return; }
    op("select_edges", { query: { of_face: one } }).then((r) => setEdgesOfFace(r.edges)).catch(() => setEdgesOfFace([]));
  }, [one, pickedEdges.length, frame]);
  const edges = pickedEdges.length ? pickedEdges : edgesOfFace;
  const round = picked.length === 1 && ["cylinder", "cone"].includes(shapes[picked[0]]) ? picked[0] : null;
  return (
    <group>
      {edgeLines.map((line, k) => <Line key={k} points={line.map((p) => v3(p))} color={BLUE} lineWidth={3} />)}
      {!tool && one && frame && (!drag || !drag.label.includes("edge")) && <FaceMarks face={one} frame={frame} size={size} controls={controls} />}
      {!tool && round && <RoundFaceDrags face={round} mesh={mesh} controls={controls} />}
      {!tool && grip && edges.length > 0 && (!drag || drag.label.includes("edge")) && <EdgeGrips edges={edges} at={grip.at} lean={grip.lean} size={size} controls={controls} />}
      {tool && tool.kind === "hole" && frame && <HoleTool tool={tool} frame={frame} controls={controls} />}
      {tool && tool.kind === "section" && frame && <SectionTool tool={tool} frame={frame} controls={controls} />}
      <SectionLines />
      {!drag && <Dimensions mesh={mesh} size={size} />}
    </group>
  );
}

function bbox(mesh) {
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const v of mesh.vertices) for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], v[i]); hi[i] = Math.max(hi[i], v[i]); }
  return { centre: [0, 1, 2].map((i) => (lo[i] + hi[i]) / 2), size: Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) };
}
// a number as a person reads it: three figures, no trailing zeros after the point
function tidy(value) { const text = Number(value).toPrecision(3); return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text; }
function normalise(v) { const l = Math.hypot(...v) || 1; return v.map((c) => c / l); }
