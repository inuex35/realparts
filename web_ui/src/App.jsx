// The sidebar is the document, as in the add-on: what the pick offers, the history with Roll Back
// Here, the picked feature's numbers, the parameters. The right-click menu offers only what fits.
import React, { useEffect, useState } from "react";
import Scene from "./Scene.jsx";
import { useStore, bodyOf, featureOf } from "./store.js";
import { get, post, op } from "./api.js";

const $ = (s) => useStore(s);
const MATES = ["fastened", "planar", "concentric", "parallel", "perpendicular", "angle", "distance", "tangent", "hinge", "slider", "ball", "gear", "screw", "belt", "slot", "cam"];

const Icon = ({ d }) => <svg viewBox="0 0 24 24"><path d={d} /></svg>;
const ICONS = {
  mark: "M12 2 3 7v10l9 5 9-5V7l-9-5zm0 2.3L18.6 8 12 11.7 5.4 8 12 4.3zM5 9.7l6 3.4v6.6l-6-3.3V9.7zm8 10V13l6-3.4v6.7l-6 3.4z",
  fresh: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm4 18H6V4h7v5h5v11z",
  save: "M17 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-4-4zm-5 16a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm3-10H5V5h10v4z",
  undo: "M12.5 8c-2.65 0-5.05 1-6.9 2.6L2 7v9h9l-3.62-3.62A7.98 7.98 0 0 1 12.5 10c3.54 0 6.55 2.31 7.6 5.5l2.37-.78C21.08 10.86 17.15 8 12.5 8z",
  redo: "M18.4 10.6A7.96 7.96 0 0 0 11.5 8c-4.65 0-8.58 2.86-9.96 6.72l2.37.78A7.98 7.98 0 0 1 11.5 10c1.96 0 3.73.72 5.12 1.88L13 15h9V6l-3.6 4.6z",
  fit: "M3 5v4h2V5h4V3H5a2 2 0 0 0-2 2zm2 10H3v4a2 2 0 0 0 2 2h4v-2H5v-4zm14 4h-4v2h4a2 2 0 0 0 2-2v-4h-2v4zM19 3h-4v2h4v4h2V5a2 2 0 0 0-2-2z",
};
const numberOr = (v) => { const n = Number(v); return v !== "" && Number.isFinite(n) ? n : v; };
// bytes to base64 a piece at a time: one call with a whole file as arguments overflows the stack
function base64Of(bytes) {
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

// --- what the pick is, in one line, and what fits it in the menu -------------------------
function pickWords(picked, pickedEdges, shapes) {
  if (pickedEdges.length) return { head: pickedEdges.length === 1 ? "This edge" : `These ${pickedEdges.length} edges`,
    line: `${pickedEdges.join(", ")} -- drag the green disc to round, the orange corner to chamfer` };
  if (!picked.length) return { head: "", line: "Click a face or an edge to pick it; Shift adds. Right-click for what fits it." };
  if (picked.length === 1) {
    const flat = shapes[picked[0]] === "plane";
    return { head: "This face", line: `${picked[0]} -- ${flat ? "drag the arrow to push or pull; right-click for more" : "a round face: right-click for more"}` };
  }
  return { head: `These ${picked.length} faces`, line: picked.join(", ") };
}

function useMenuItems() {
  const doc = $((s) => s.doc), picked = $((s) => s.picked), pickedEdges = $((s) => s.pickedEdges), shapes = $((s) => s.shapes);
  const tryOp = $((s) => s.tryOp), drive = $((s) => s.drive);
  if (!doc) return [];
  const out = [];
  const startDrag = (kind, x, y) => useStore.setState({ pendingDrag: { kind, x, y } });
  if (pickedEdges.length) {
    out.push({ label: pickedEdges.length === 1 ? "Round It" : "Round Them", run: (m) => startDrag("fillet", m.x, m.y) });
    out.push({ label: pickedEdges.length === 1 ? "Chamfer It" : "Chamfer Them", run: (m) => startDrag("chamfer", m.x, m.y) });
  } else if (picked.length === 1) {
    const face = picked[0];
    const words = (m, kind) => { const text = prompt("The words", "TEXT"); if (text) useStore.setState({ pendingDrag: { kind, x: m.x, y: m.y, text } }); };
    if (shapes[face] === "plane") {
      out.push({ label: "Push / Pull", hint: "drag along the normal", run: (m) => startDrag("push", m.x, m.y) });
      out.push({ label: "Hole", hint: "click where; the wheel sizes it", run: () => useStore.setState({ tool: { kind: "hole", face, index: 3 } }) });
      out.push({ label: "Plane Off It", hint: "a work plane, dragged off the face", run: (m) => startDrag("plane", m.x, m.y) });
      out.push({ label: "Split Here", hint: "a cut parallel to the face, dragged in; what is under it stays", run: (m) => startDrag("split", m.x, m.y) });
      out.push({ label: "Section Here", hint: "a plane slides into the part; the cut is drawn", run: () => useStore.setState({ tool: { kind: "section", face } }) });
      out.push({ label: "Emboss Text…", hint: "words raised off the face, or cut in", run: (m) => words(m, "emboss") });
      out.push({ label: "Draft From Here", hint: "the walls lean away from this face's plane", run: (m) => startDrag("draft", m.x, m.y) });
    } else if (shapes[face] === "cylinder") {
      out.push({ label: "Wrap Text Round It", hint: "words wrapped on, raised or cut", run: (m) => words(m, "emboss") });
      out.push({ label: "Coil Round It", hint: "a wire wound round it; drag sets the pitch", run: (m) => startDrag("coil", m.x, m.y) });
    }
    out.push({ label: "Round Its Edges", run: (m) => startDrag("fillet", m.x, m.y) });
    out.push({ label: "Chamfer Its Edges", run: (m) => startDrag("chamfer", m.x, m.y) });
  } else if (picked.length === 2) {
    const round = (n) => ["cylinder", "cone"].includes(shapes[n]);
    if (new Set(picked.map(bodyOf)).size === 2) {
      const kind = round(picked[0]) && round(picked[1]) ? "concentric" : picked.every((n) => shapes[n] === "plane") ? "fastened" : "tangent";
      out.push({ label: "Mate: Put the First Against the Second", hint: kind,
        run: () => tryOp("add_mate", { faces: picked, kind, offset: kind === "concentric" ? null : 0, flip: true }) });
      out.push({ label: "Mate As…", hint: MATES.join(", "),
        run: () => { const k = prompt("Mate kind: " + MATES.join(", "), kind); if (k && MATES.includes(k)) tryOp("add_mate", { faces: picked, kind: k, offset: ["concentric", "belt"].includes(k) ? null : 0, flip: true }); } });
    }
    out.push({ label: "Distance Between", hint: "the shortest, face to face",
      run: async () => { const r = await tryOp("measure", { kind: "distance", faces: picked }); if (r) useStore.getState().say(`${picked[0]} to ${picked[1]}: ${r.value.toFixed(3)} mm`); } });
  }
  const asm = doc.features.find((f) => f.type === "assemble" && (f.args.mates || []).length);
  const first = picked[0] || pickedEdges[0];
  if (first && asm && bodyOf(first) !== (asm.args.ground || asm.args.bodies[0])) {
    out.push({ label: "Drive This Part", hint: "turn it 30°; Keep in Parts writes it", run: async () => { const d = await drive(bodyOf(first), 30); useStore.setState({ driven: d }); } });
  }
  if (picked.length || pickedEdges.length) out.push({ label: "Ask About This…", run: () => useStore.setState({ askOpen: true, askFocus: Date.now() }) });
  return out;
}

// --- the sidebar's sections -----------------------------------------------------------------
function Section({ title, open: initially = true, children, id }) {
  const [open, setOpen] = useState(initially);
  return (
    <section className={`sec${open ? " open" : ""}`} id={id}>
      <h4 onClick={() => setOpen(!open)}><span className="chev">{open ? "▾" : "▸"}</span>{title}</h4>
      {open && <div className="body">{children}</div>}
    </section>
  );
}

function CreateSketch() {
  const doc = $((s) => s.doc), feature = $((s) => s.feature), tryOp = $((s) => s.tryOp);
  const [plane, setPlane] = useState("xy"), [shape, setShape] = useState("rect");
  const [sizes, setSizes] = useState({ width: 40, height: 30, radius: 15, depth: 10 });
  const [busy, setBusy] = useState(false);
  const chosen = doc?.features.find((f) => f.id === feature && f.type === "sketch");
  const valid = (keys) => keys.every((key) => Number.isFinite(Number(sizes[key])) && Number(sizes[key]) > 0);
  const create = async () => {
    setBusy(true);
    try {
      if (!useStore.getState().doc) await useStore.getState().fresh();
      const current = useStore.getState().doc;
      if (!current) return;
      const taken = new Set(current.features.map((f) => f.id));
      let n = 1;
      while (taken.has(`plane${n}`) || taken.has(`sketch${n}`)) n++;
      const result = await tryOp("apply", { ops: [
        { op: "add_plane", on: plane, feature_id: `plane${n}` },
        { op: "add_profile", plane: `plane${n}`, feature_id: `sketch${n}`, shape,
          width: Number(sizes.width), height: Number(sizes.height), radius: Number(sizes.radius) },
      ] });
      if (result) useStore.setState((s) => ({ feature: `sketch${n}`, picked: [], pickedEdges: [], frameKey: s.frameKey + 1 }));
    } finally { setBusy(false); }
  };
  const extrude = async () => {
    setBusy(true);
    try {
      const result = await tryOp("add_feature", { type: "extrude", args: { sketch: chosen.id, distance: Number(sizes.depth) } });
      if (result) useStore.setState((s) => ({ feature: s.doc.features.at(-1).id, frameKey: s.frameKey + 1 }));
    } finally { setBusy(false); }
  };
  return <Section title="Create Sketch" id="sec-create-sketch">
    <p className="hint">Choose a plane → create a profile → extrude.</p>
    <label>Plane <select value={plane} onChange={(e) => setPlane(e.target.value)}>
      <option value="xy">XY (top)</option><option value="xz">XZ (front)</option><option value="yz">YZ (side)</option>
    </select></label>
    <label>Profile <select value={shape} onChange={(e) => setShape(e.target.value)}>
      <option value="rect">Rectangle</option><option value="circle">Circle</option>
    </select></label>
    {(shape === "rect" ? ["width", "height"] : ["radius"]).map((key) => <label key={key}>{key} (mm)
      <input type="number" min="0.01" step="any" value={sizes[key]} onChange={(e) => setSizes({ ...sizes, [key]: e.target.value })} />
    </label>)}
    <button disabled={busy || !valid(shape === "rect" ? ["width", "height"] : ["radius"])} onClick={create}>Create Sketch</button>
    <label>Extrusion (mm)<input type="number" min="0.01" step="any" value={sizes.depth}
      onChange={(e) => setSizes({ ...sizes, depth: e.target.value })} /></label>
    <button disabled={busy || !chosen || !valid(["depth"])} onClick={extrude}>Extrude Sketch</button>
    {chosen && <p className="hint">Selected sketch: {chosen.id}</p>}
  </Section>;
}

function History() {
  const doc = $((s) => s.doc), tryOp = $((s) => s.tryOp), feature = $((s) => s.feature), selectFeature = $((s) => s.selectFeature);
  const [edits, setEdits] = useState({});
  if (!doc) return <p className="hint">Nothing open.</p>;
  const rolled = doc.rolled_back_to;
  const after = rolled ? doc.features.findIndex((f) => f.id === rolled) : -1;
  const chosen = doc.features.find((f) => f.id === feature);
  const numbers = chosen ? Object.entries(chosen.args).filter(([, v]) => typeof v === "number" || (typeof v === "string" && doc.parameters[v] !== undefined)) : [];
  const commit = (key) => {
    if (!(key in edits)) return;
    const value = numberOr(edits[key].trim()); setEdits((e) => { const c = { ...e }; delete c[key]; return c; });
    if (!Number.isFinite(value)) return;
    const was = chosen.args[key];
    if (typeof was === "string") tryOp("set_parameter", { name: was, value }); else tryOp("edit_feature", { feature_id: chosen.id, args: { [key]: value } });
  };
  return (
    <>
      <ul id="features">{doc.features.map((f, k) => (
        <li key={f.id} className={(f.suppressed ? "off" : "") + (f.id === feature ? " on" : "") + (after >= 0 && k > after ? " later" : "")}
          title={JSON.stringify(f.args)} onClick={() => selectFeature(f.id)}>
          <input type="checkbox" checked={!f.suppressed} title="on / off" onClick={(e) => e.stopPropagation()}
            onChange={(e) => tryOp("suppress_feature", { feature_id: f.id, suppressed: !e.target.checked })} />
          <span className="grow"><b>{f.id}</b> <span className="type">{f.type}{f.summary ? ` · ${f.summary}` : ""}</span></span>
          <button className="x" title="remove" onClick={(e) => { e.stopPropagation(); tryOp("remove_feature", { feature_id: f.id }); }}>×</button>
        </li>))}</ul>
      <div className="row">
        <button id="rollback" disabled={!chosen} onClick={() => tryOp("rollback", rolled === feature ? {} : { feature_id: feature })}>
          {rolled === feature && rolled ? "Build It All Again" : "Roll Back Here"}</button>
        {rolled && rolled !== feature && <button title="build the whole history again" onClick={() => tryOp("rollback", {})}>⏭</button>}
      </div>
      {chosen && numbers.length > 0 && (
        <div className="numbers" id="feature-numbers">
          <div className="hint">{chosen.id}: its numbers, also floating beside the part</div>
          {numbers.map(([key, v]) => {
            const label = typeof v === "string" ? v : key, value = typeof v === "string" ? doc.parameters[v] : v;
            return <div key={key} className="num"><span>{label}</span>
              <input value={key in edits ? edits[key] : String(value)} onChange={(e) => setEdits({ ...edits, [key]: e.target.value })}
                onBlur={() => commit(key)} onKeyDown={(e) => e.key === "Enter" && commit(key)} /></div>;
          })}
        </div>)}
    </>
  );
}

function Parameters() {
  const doc = $((s) => s.doc), tryOp = $((s) => s.tryOp);
  const [edits, setEdits] = useState({});
  if (!doc) return null;
  const commit = (name) => { if (name in edits) { tryOp("set_parameter", { name, value: numberOr(edits[name].trim()) }); setEdits((e) => { const c = { ...e }; delete c[name]; return c; }); } };
  const names = Object.keys(doc.parameters);
  const meta = doc.meta || {};
  const setMeta = (key) => (e) => { const value = e.target.value.trim(); if (value !== (meta[key] || "")) tryOp("set_meta", { [key]: value }); };
  return (
    <>
      {names.length ? <table id="params"><tbody>
        {names.map((name) => (
          <tr key={name}><td>{name}</td><td><input value={name in edits ? edits[name] : String(doc.parameters[name])} title={(doc.parameters_bounds[name] || []).join(" … ")}
            onChange={(e) => setEdits({ ...edits, [name]: e.target.value })} onBlur={() => commit(name)} onKeyDown={(e) => e.key === "Enter" && commit(name)} /></td></tr>))}
      </tbody></table> : <p className="hint">No parameters yet: a drag makes one for what it moved.</p>}
      <table id="meta"><tbody>
        <tr><td>material</td><td><input id="material" key={"m" + (meta.material || "")} defaultValue={meta.material || ""} placeholder="A6061" title="what it is made of: the bill of materials, its mass and STEP use it"
          onBlur={setMeta("material")} onKeyDown={(e) => e.key === "Enter" && e.target.blur()} /></td></tr>
        <tr><td>colour</td><td><input id="colour" key={"c" + (meta.colour || "")} defaultValue={meta.colour || ""} placeholder="#rrggbb" title="written to STEP"
          onBlur={setMeta("colour")} onKeyDown={(e) => e.key === "Enter" && e.target.blur()} /></td></tr>
      </tbody></table>
    </>
  );
}

function Parts() {
  const doc = $((s) => s.doc), mesh = $((s) => s.mesh), tryOp = $((s) => s.tryOp), explode = $((s) => s.explode), drive = $((s) => s.drive), driven = $((s) => s.driven);
  const [factor, setFactor] = useState(0), [turn, setTurn] = useState(0), [out, setOut] = useState(null), [part, setPart] = useState("");
  const asm = doc && doc.features.find((f) => f.type === "assemble" && (f.args.mates || []).length);
  const bodies = asm ? asm.args.bodies.filter((b) => b !== (asm.args.ground || asm.args.bodies[0])) : [];
  useEffect(() => { if (bodies.length && !bodies.includes(part)) setPart(bodies[0]); }, [asm]);   // eslint-disable-line
  const keep = async () => {
    if (!driven) return;
    const drives = (asm.args.drive || []).filter((d) => d.part !== driven.part).concat([{ part: driven.part, turn: driven.turn }]);
    useStore.setState({ driven: null }); setTurn(0);
    await tryOp("edit_feature", { feature_id: asm.id, args: { drive: drives } });
  };
  return (
    <>
      <div className="row"><span className="lbl">Take apart</span><input type="range" id="explode" min="0" max="3" step="0.1" value={factor}
        onChange={(e) => { const f = Number(e.target.value); setFactor(f); explode(f); }} /><span id="explode-v">{factor}</span></div>
      {asm ? <div className="row"><span className="lbl">Drive</span>
        <select id="drive-part" value={part} onChange={(e) => setPart(e.target.value)}>{bodies.map((b) => <option key={b}>{b}</option>)}</select>
        <input type="range" id="drive-turn" min="-180" max="180" step="5" value={turn} onChange={(e) => setTurn(Number(e.target.value))}
          onMouseUp={async () => useStore.setState({ driven: await drive(part, turn) })} onKeyUp={async () => useStore.setState({ driven: await drive(part, turn) })} />
        <span id="drive-v">{turn}°</span>
        <button id="keep-drive" disabled={!driven} onClick={keep}>Keep</button>
      </div> : <p className="hint">Mates solved together (an assemble with mates) are what leave a part free to drive.</p>}
      <div className="row">
        <button id="interference" onClick={async () => { const r = await tryOp("interference"); if (r) setOut(r.clear ? <div className="ok">no interference between {r.parts.join(", ")}</div> : r.interferences.map((c) => <div key={c.parts.join()} className="bad">{c.parts.join(" and ")} share {c.volume_mm3} mm³</div>)); }}>Collisions</button>
        <button id="bom" onClick={async () => { const r = await tryOp("bill_of_materials"); if (!r) return;
          const rows = (lines, depth) => lines.flatMap((l) => [<tr key={depth + l.part}><td style={{ paddingLeft: 4 + depth * 12 }}>{l.standard || l.part}</td><td>{l.quantity}</td><td>{l.material || ""}</td><td>{l.mass_g == null ? "" : l.mass_g.toFixed(1)}</td></tr>, ...rows(l.parts || [], depth + 1)]);
          setOut(<><table className="bom"><thead><tr><td>part</td><td>qty</td><td>material</td><td>g</td></tr></thead><tbody>{rows(r.parts, 0)}</tbody></table>{r.total_mass_g ? <div>total {r.total_mass_g} g</div> : null}</>); }}>Bill of Materials</button>
      </div>
      <div id="assembly-out">{out}</div>
    </>
  );
}

function Output() {
  const doc = $((s) => s.doc), tryOp = $((s) => s.tryOp), say = $((s) => s.say), picked = $((s) => s.picked), shapes = $((s) => s.shapes);
  const [report, setReport] = useState(null);
  if (!doc) return null;
  const draftCheck = async () => {
    // pulled out along the picked flat face's normal, else straight up
    let direction = [0, 0, 1];
    if (picked.length === 1 && shapes[picked[0]] === "plane") { const f = await tryOp("face_frame", { face: picked[0] }); if (f) direction = f.normal; }
    const r = await tryOp("draft_check", { direction, min_angle: 1 });
    if (!r) return;
    setReport([{ check: "draft", ok: !r.needs_draft.length, said: r.needs_draft.length ? `${r.needs_draft.length} face(s) need draft: ${r.needs_draft.slice(0, 6).join(", ")}` : "every wall has draft" },
      { check: "undercuts", ok: !r.undercuts.length, said: r.undercuts.length ? r.undercuts.map((u) => `${u.face} (${u.undercut_mm2} mm²)`).join(", ") : `none along ${direction.map((c) => c.toFixed(1)).join(", ")}` }]);
  };
  const mass = async () => { const r = await tryOp("mass_properties"); if (r) say(`${r.mass_g.toFixed(1)} g of ${r.material}, centre of mass at ${r.centre_of_mass.map((c) => c.toFixed(1)).join(", ")}`); };
  return (
    <>
      <div className="row">
        <button id="check" onClick={async () => { const r = await tryOp("check", { printing: true }); if (r) setReport(r.checks); }}>Check the Design</button>
        <button id="draft-check" title="will it come out of a mould pulled along the picked face's normal, or up" onClick={draftCheck}>Draft Check</button>
        <button id="mass" title="grams, by the material in Parameters" onClick={mass}>Mass</button>
        <button onClick={async () => { const path = prompt("Write STEP to", (doc.path || "part.json").replace(/\.json$/, ".step")); if (!path) return;
          const r = await tryOp("export_step", { path }); if (r) say(`wrote ${r.path} (${r.bytes} bytes${r.parts ? `, ${r.parts.length} parts` : ""})`); }}>STEP</button>
        <button onClick={async () => { const r = await tryOp("drawing", { spec: { views: ["front", "top", "right"], scale: 1 } }); if (!r) return;
          const w = window.open("", "_blank"); w.document.write(r.svg); w.document.close(); }}>Drawing</button>
      </div>
      {report && <div id="report">{report.map((c) => <div key={c.check} className={c.ok ? "ok" : c.severity === "advice" ? "advice" : "bad"}>{c.ok ? "✓" : "✗"} {c.check}: {c.said}</div>)}</div>}
    </>
  );
}

function Ask() {
  const doc = $((s) => s.doc), built = $((s) => s.built), picked = $((s) => s.picked), pickedEdges = $((s) => s.pickedEdges), say = $((s) => s.say), refresh = $((s) => s.refresh);
  const focus = $((s) => s.askFocus);
  const [question, setQuestion] = useState(""), [replies, setReplies] = useState([]), [asking, setAsking] = useState(null);
  useEffect(() => { if (focus) document.getElementById("question")?.focus(); }, [focus]);
  const situation = () => [
    "[From the RealParts page]",
    picked.length ? "Selected faces: " + picked.join(", ") : pickedEdges.length ? "Selected edges (between faces): " + pickedEdges.join(", ") : "Nothing is selected.",
    doc ? `Document: ${doc.path || "(new, unsaved)"}; features in order: ${doc.features.map((f) => f.id).join(", ") || "none yet"}` : "No document is open; start with new_document.",
    doc && built && built.faces ? `Body: ${built.faces} faces, ${Math.round(built.volume_mm3)} mm3.` : doc ? "No solid yet." : "",
    '"here" or "this" means the selection above. Say what you did in one or two lines. Use only the cadcore tools: no files, no shell, no scripts.',
  ].filter(Boolean).join("\n");
  const ask = async () => {
    const q = question.trim(); if (!q) return;
    const reply = await post("/ask", { question: q, situation: situation() });
    if (!reply.ok) { say(`${reply.kind}: ${reply.message}`, true); return; }
    setReplies((r) => [...r, { kind: "q", text: q }]); setQuestion("");
    const state = await get("/state");
    setAsking({ shown: 0, generation: state.generation }); say("the assistant is working…");
  };
  useEffect(() => {
    if (!asking) return;
    const t = setTimeout(async () => {
      const state = await get("/state"), a = state.ask || { done: true, events: [] };
      setReplies((r) => [...r, ...a.events.slice(asking.shown)]);
      let generation = asking.generation;
      if (state.generation !== generation) { generation = state.generation; await refresh(); }
      if (a.done) { setAsking(null); say(a.error || "the assistant is done", !!a.error); } else setAsking({ shown: a.events.length, generation });
    }, 800);
    return () => clearTimeout(t);
  }, [asking]);   // eslint-disable-line
  return (
    <>
      <textarea id="question" rows={2} placeholder='e.g. "M4 tapped hole here, 8 deep" -- the pick goes with it' value={question} onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); } }} />
      <div className="row"><button id="ask" className="primary" onClick={ask}>Ask</button><button onClick={() => post("/ask/stop", {})}>Stop</button></div>
      <div id="replies">{replies.map((e, k) => <div key={k} className={e.kind === "q" ? "q" : e.kind === "text" ? "text" : "tool"}>{e.kind === "tool" ? `▸ ${e.text} ${JSON.stringify(e.args || {}).slice(0, 120)}` : e.text}</div>)}</div>
    </>
  );
}

// --- the page --------------------------------------------------------------------------
export default function App() {
  const doc = $((s) => s.doc), built = $((s) => s.built), status = $((s) => s.status), refresh = $((s) => s.refresh), open = $((s) => s.open), fresh = $((s) => s.fresh), tryOp = $((s) => s.tryOp), say = $((s) => s.say);
  const hud = $((s) => s.hud), drag = $((s) => s.drag), picked = $((s) => s.picked), pickedEdges = $((s) => s.pickedEdges), shapes = $((s) => s.shapes), mesh = $((s) => s.mesh);
  const askOpen = $((s) => s.askOpen);
  const [examples, setExamples] = useState([]), [menu, setMenu] = useState(null), [shown, setShown] = useState(false), [dropping, setDropping] = useState(false);
  const items = useMenuItems();
  useEffect(() => { get("/examples").then((r) => setExamples(r.examples)); refresh(); }, []);   // eslint-disable-line
  useEffect(() => { if (!status.text) return; setShown(true); const t = setTimeout(() => setShown(false), status.error ? 8000 : 4000); return () => clearTimeout(t); }, [status]);
  useEffect(() => { useStore.setState({ onMenu: (x, y) => setMenu({ x, y }) }); }, []);
  useEffect(() => {
    // for the page's tests: the menu's words for the pick, and running one of them
    window.__realparts = { ...(window.__realparts || {}), actions: () => items.map((a) => a.label),
      action: (label, x, y) => { const a = items.find((i) => i.label === label); if (a) a.run({ x, y }); return !!a; } };
  }, [items]);
  useEffect(() => {
    // the menu goes on a click anywhere else, or Escape
    if (!menu) return undefined;
    const close = (e) => { if (!e.target.closest || !e.target.closest("#menu")) setMenu(null); };
    const key = (e) => { if (e.key === "Escape") setMenu(null); };
    window.addEventListener("pointerdown", close, true); window.addEventListener("keydown", key);
    return () => { window.removeEventListener("pointerdown", close, true); window.removeEventListener("keydown", key); };
  }, [menu]);
  useEffect(() => {
    const onKey = (e) => {
      if (["INPUT", "TEXTAREA"].includes(e.target.tagName) || useStore.getState().drag) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") { e.preventDefault(); tryOp(e.shiftKey ? "redo" : "undo"); }
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); }
      if (e.key.toLowerCase() === "f") useStore.setState((s) => ({ frameKey: s.frameKey + 1 }));
    };
    window.addEventListener("keydown", onKey); return () => window.removeEventListener("keydown", onKey);
  });
  useEffect(() => {
    // a file dropped anywhere on the page is sent up and opened
    const over = (e) => { e.preventDefault(); setDropping(true); };
    const leave = (e) => { if (!e.relatedTarget) setDropping(false); };
    const drop = async (e) => {
      e.preventDefault(); setDropping(false);
      const file = e.dataTransfer.files[0]; if (!file) return;
      const data = base64Of(new Uint8Array(await file.arrayBuffer()));
      say(`opening ${file.name}…`);
      const reply = await post("/upload", { name: file.name, data });
      if (!reply.ok) { say(`${reply.kind}: ${reply.message}`, true); return; }
      useStore.setState({ picked: [], pickedEdges: [], poses: {} });
      await useStore.getState().shown(reply.result);
      useStore.setState((s) => ({ frameKey: s.frameKey + 1 }));
      say(`opened ${file.name}`);
    };
    window.addEventListener("dragover", over); window.addEventListener("dragleave", leave); window.addEventListener("drop", drop);
    return () => { window.removeEventListener("dragover", over); window.removeEventListener("dragleave", leave); window.removeEventListener("drop", drop); };
  }, [say]);
  const save = async () => { const path = doc && doc.path ? doc.path : prompt("Save as", "part.json"); if (!path) return; const r = await tryOp("save", { path }); if (r) say(`saved ${r.path || path}`); };
  const name = doc ? (doc.path ? doc.path.split(/[\\/]/).pop() : "new document") + (doc.saved ? "" : " •") : "no document";
  const words = pickWords(picked, pickedEdges, shapes);
  const scopes = mesh ? new Set((mesh.face_table || []).map((n) => n.includes(":") ? n.split(":")[0] : "")) : new Set();
  return (
    <>
      <Scene />
      <header id="bar" className="glass">
        <div className="mark"><Icon d={ICONS.mark} /></div>
        <select id="examples" title="open an example" defaultValue="" onChange={(e) => e.target.value && open(e.target.value, true)}>
          <option value="">Open…</option>{examples.map((e) => <option key={e.path} value={e.path} title={e.note}>{e.name}</option>)}
        </select>
        <span id="docname">{name}{built && built.faces ? ` · ${built.faces} faces` : ""}</span>
        <div className="spacer" />
        <button className="icon" title="New" onClick={fresh}><Icon d={ICONS.fresh} /></button>
        <button className="icon" title="Save (Ctrl+S)" onClick={save}><Icon d={ICONS.save} /></button>
        <span className="sep" />
        <button className="icon" title="Undo (Ctrl+Z)" onClick={() => tryOp("undo")}><Icon d={ICONS.undo} /></button>
        <button className="icon" title="Redo (Ctrl+Shift+Z)" onClick={() => tryOp("redo")}><Icon d={ICONS.redo} /></button>
        <span className="sep" />
        <button className="icon" title="Fit the view (F)" onClick={() => useStore.setState((s) => ({ frameKey: s.frameKey + 1 }))}><Icon d={ICONS.fit} /></button>
      </header>
      <aside id="side" className="glass">
        <div id="pick" className={picked.length || pickedEdges.length ? "on" : ""}>{words.line}</div>
        <CreateSketch />
        <Section title="History" id="sec-history"><History /></Section>
        <Section title="Parameters" id="sec-params"><Parameters /></Section>
        {doc && scopes.size > 1 && <Section title="Parts" id="sec-parts" open={false}><Parts /></Section>}
        <Section title="Ask" id="sec-ask" open={!!askOpen} key={askOpen ? "ask-open" : "ask"}><Ask /></Section>
        <Section title="Checks and Output" id="sec-output" open={false}><Output /></Section>
      </aside>
      <div id="status" className={`glass${shown ? " shown" : ""}${status.error ? " error" : ""}`}>{status.text}</div>
      {dropping && <div id="drop" className="glass">Drop a .json document, or a STEP, IGES, BREP, STL, OBJ, 3MF or glTF file to open it</div>}
      {hud && !drag && <div id="hud" className="glass">{hud}</div>}
      {drag && <div id="badge" className={drag.refused ? "refused" : ""} style={{ left: drag.x + 18, top: drag.y + 18 }}>{drag.text}</div>}
      {drag && <div id="hud" className="glass">{hud || drag.label}</div>}
      {menu && items.length > 0 && (
        <div id="menu" className="glass" style={{ left: menu.x, top: menu.y }} onPointerLeave={() => setMenu(null)}>
          <div className="head">{words.head}</div>
          {items.map((a) => <div key={a.label} className="item" onClick={() => { setMenu(null); a.run(menu); }}>
            {a.label}{a.hint ? <small>{a.hint}</small> : null}</div>)}
        </div>)}
      {menu && items.length === 0 && setMenu(null)}
    </>
  );
}
