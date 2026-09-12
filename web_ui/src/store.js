// What the page knows: the document, what was built, the mesh, the pick, the parts moved.
import { create } from "zustand";
import { op, OpError } from "./api.js";

// operations that only answer a question: the picture and the panels stay as they are
const QUERIES = new Set(["interference", "bill_of_materials", "export_step", "drawing", "check", "explode",
  "drive", "describe_document", "describe_faces", "tessellate", "select_edges", "section", "measure",
  "draft_check", "mass_properties", "curvature", "project", "face_frame"]);

export const useStore = create((set, get) => ({
  doc: null, built: null, mesh: null, shapes: {}, picked: [], pickedEdges: [], poses: {}, examples: [],
  status: { text: "", error: false, at: 0 }, replies: [], asking: null, generation: 0,
  frameKey: 0,                              // bumps when the view should fit the part again
  feature: null,                            // the feature whose numbers float beside the part
  drag: null,                               // the badge of a drag in flight: label, text, refused, x, y
  hud: "",                                  // the one line under the cursor's hand: what a drag or a tool does
  tool: null,                               // {kind: "hole", face, index} while the hole tool is out
  pendingDrag: null,                        // {kind, x, y, text}: a drag the menu started, followed from the mouse
  section: null,                            // the curves where a plane passes through, drawn on the part
  driven: null, askOpen: false, askFocus: 0,
  setHud: (hud) => set({ hud }),

  say: (text, error = false) => set({ status: { text, error, at: Date.now() } }),

  // --- talking to the kernel ---------------------------------------------------
  tryOp: async (name, args = {}) => {
    try {
      const out = await op(name, args);
      if (QUERIES.has(name)) return out;
      if (out && (out.face_names !== undefined || out.faces !== undefined)) await get().shown(out);
      else await get().refresh();
      return out;
    } catch (err) {
      get().say(err instanceof OpError ? `${err.kind}: ${err.message}` : String(err), true);
      return null;
    }
  },
  refresh: async () => {
    let doc = null, info = null;
    try { doc = await op("describe_document"); } catch { /* nothing open */ }
    if (doc) { try { info = await op("build"); } catch (err) { get().say(`${err.kind}: ${err.message}`, true); } }
    set({ doc });
    await get().shown(info);
  },
  shown: async (info) => {
    let doc = null;
    try { doc = await op("describe_document"); } catch { /* nothing open */ }
    let mesh = null, shapes = {};
    if (info && info.faces) {
      try {
        mesh = await op("tessellate", { deflection: 0.12 });
        for (const f of (await op("describe_faces")).faces) shapes[f.name] = f.shape;
      } catch (err) { get().say(`${err.kind}: ${err.message}`, true); }
      const free = info.freedom;
      get().say(`${info.faces} faces, ${Math.round(info.volume_mm3)} mm³${free ? `, ${free.dof} free` : ""}`);
    } else if (doc) {
      try { mesh = await op("tessellate", { deflection: 0.12 }); }
      catch (err) { get().say(`${err.kind}: ${err.message}`, true); }
      get().say((info && info.hint) || "nothing built yet");
    }
    set((s) => {
      const picked = s.picked.filter((n) => n in shapes);
      const pickedEdges = s.pickedEdges.filter((n) => mesh && mesh.edges && n in mesh.edges);
      const feature = picked.length ? featureOf(picked[0]) : pickedEdges.length ? featureOf(pickedEdges[0])
        : s.feature && doc && doc.features.some((f) => f.id === s.feature) ? s.feature
        : doc && doc.features.length ? doc.features[doc.features.length - 1].id : null;
      return { doc, built: info, mesh, shapes, poses: {}, picked, pickedEdges, feature };
    });
  },
  // an example is a starting point, not a file to write over: opened as a copy
  open: async (path, asCopy = false) => { set({ picked: [], poses: {} }); await get().tryOp("open", { path, as_copy: asCopy }); set((s) => ({ frameKey: s.frameKey + 1 })); },
  fresh: async () => { set({ picked: [], poses: {} }); await get().tryOp("new_document"); set((s) => ({ frameKey: s.frameKey + 1 })); },

  // --- the pick ----------------------------------------------------------------
  pick: (name, shift) => set((s) => {
    if (name === null) return shift ? {} : { picked: [], pickedEdges: [], tool: null, section: null };
    const picked = !shift ? (s.picked.length === 1 && s.picked[0] === name ? [] : [name])
      : s.picked.includes(name) ? s.picked.filter((n) => n !== name) : [...s.picked, name];
    return { picked, pickedEdges: shift ? s.pickedEdges : [], tool: null, section: null, feature: picked.length ? featureOf(picked[0]) : s.feature };
  }),
  // an edge is picked on the wire: alone, or beside other edges with Shift
  pickEdge: (name, shift) => set((s) => {
    const pickedEdges = !shift ? (s.pickedEdges.length === 1 && s.pickedEdges[0] === name ? [] : [name])
      : s.pickedEdges.includes(name) ? s.pickedEdges.filter((n) => n !== name) : [...s.pickedEdges, name];
    return { pickedEdges, picked: shift ? s.picked : [], tool: null, feature: featureOf(name) };
  }),
  selectFeature: (feature) => set({ feature }),

  // --- the parts moved: exploded, or driven ------------------------------------
  explode: async (factor) => {
    if (!factor) { set({ poses: {} }); return; }
    const out = await get().tryOp("explode", { factor });
    if (!out) return;
    const poses = {};
    for (const [fid, offset] of Object.entries(out.offsets)) poses[out.scopes[fid] || fid] = { offset, rotation: null };
    set({ poses });
  },
  drive: async (part, turn) => {
    if (!turn) { set({ poses: {} }); return null; }
    const out = await get().tryOp("drive", { part, turn, frames: 1 });
    if (!out) return null;
    const frame = out.frames[out.frames.length - 1], poses = {};
    for (const [fid, pose] of Object.entries(frame)) poses[out.scopes[fid] || fid] = { pose, rest: out.rest[fid] };
    set({ poses });
    get().say(`${part} turned ${turn}°; Keep writes it into the document`);
    return { part, turn };
  },
}));

export const partOf = (name) => (name.includes(":") ? name.slice(0, name.lastIndexOf(":")) : "");
export const bodyOf = (name) => (name.includes(":") ? name.split(":")[0] : name.split("/")[0]);
// the feature that made a face or an edge: the name without its scope, role and suffixes
export const featureOf = (name) => name.split("|")[0].split("/")[0].split(":").pop().split(/[#@~]/)[0];
