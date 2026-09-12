// A drag is a run of trial edits between begin_drag and end_drag, which the kernel records
// as one step or none. Ported from the add-on's viewport/live.py.
import { op, OpError } from "./api.js";
import { useStore } from "./store.js";

export class LiveDrag {
  // make(value) -> [op, args] creates the feature; edit(value) -> args edits it after
  constructor({ label, make, edit, format = (v) => `${v.toFixed(2)} mm` }) {
    Object.assign(this, { label, make, edit, format });
    this.feature = null; this.value = null; this.good = null; this.refused = null;
    this.typed = ""; this.busy = false; this.pending = null; this.done = false;
  }

  async begin() {
    await op("begin_drag");
    useStore.setState({ drag: { label: this.label, text: "", refused: false, x: 0, y: 0 } });
    this._onKey = (e) => this.key(e);
    window.addEventListener("keydown", this._onKey, true);
    return this;
  }

  // the badge beside the cursor: what the drag is at, and whether it built
  say(text, refused = false, at = null) {
    useStore.setState((s) => ({ drag: { ...(s.drag || {}), label: this.label, text, refused, ...(at ? { x: at.x, y: at.y } : {}) } }));
  }

  preview(value, at = null) {
    if (this.done) return;
    this.value = value;
    this.say(this.format(value), false, at);
    this.pending = value;
    if (!this.busy) this._run();
  }

  async _run() {
    this.busy = true;
    while (this.pending !== null && !this.done) {
      const value = this.pending; this.pending = null;
      try {
        let out;
        if (this.feature === null) out = await op(...this.make(value));
        else {
          // edit answers the arguments to change, or [op, args] for an operation of its own
          const change = this.edit(value, this.feature);
          out = Array.isArray(change) ? await op(...change) : await op("edit_feature", { feature_id: this.feature, args: change });
        }
        if (this.feature === null) this.feature = out.feature;
        this.good = value; this.refused = null;
        await useStore.getState().shown(out);
        if (!this.done) this.say(this.format(value), false);
      } catch (err) {
        // the part on screen is still the last one that built: say so in the badge
        this.refused = value;
        this.say(err instanceof OpError ? err.message : String(err), true);
      }
    }
    this.busy = false;
  }

  key(e) {
    if (e.key === "Escape") { e.preventDefault(); this.cancel(); return; }
    if (e.key === "Enter") { e.preventDefault(); this.finish(); return; }
    if (/^[0-9.\-]$/.test(e.key)) { this.typed += e.key; e.preventDefault(); }
    else if (e.key === "Backspace") { this.typed = this.typed.slice(0, -1); e.preventDefault(); }
    else return;
    const typed = Number(this.typed);
    if (this.typed && Number.isFinite(typed)) { this.preview(typed); this.say(`${this.typed} mm (typed)`); }
  }

  _theValueThatBuilt() { return this.refused !== null && this.good !== null ? this.good : this.value; }

  async finish() {
    if (this.done) return;
    this.done = true; this.pending = null;
    window.removeEventListener("keydown", this._onKey, true);
    const value = this._theValueThatBuilt();
    const store = useStore.getState();
    if (value === null) { await this._drop(); return; }
    try {
      await op("reset_drag");
      await op(...this.make(value));
      await store.shown(await op("end_drag", { keep: true }));
      store.say(`${this.label}: ${this.format(value)}${this.refused !== null ? " (the shape would not take any more)" : ""}`);
    } catch (err) {
      try { await store.shown(await op("end_drag", { keep: false })); } catch { /* already gone */ }
      store.say(err instanceof OpError ? `${err.kind}: ${err.message}` : String(err), true);
    }
    useStore.setState({ drag: null });
  }

  async cancel() {
    if (this.done) return;
    this.done = true; this.pending = null;
    window.removeEventListener("keydown", this._onKey, true);
    await this._drop();
    useStore.getState().say("cancelled");
  }

  async _drop() {
    try { await useStore.getState().shown(await op("end_drag", { keep: false })); } catch { /* nothing to drop */ }
    useStore.setState({ drag: null });
  }
}

// Snap targets, built once per drag from describe_faces: parallel planes, and points on the face.
export class Snapper {
  static PIXELS = 9;
  constructor(faces, frame, own) {
    const n = frame.normal, o = frame.origin;
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    this.planes = [];
    for (const f of faces) {
      if (f.shape !== "plane" || f.name === own || !f.normal) continue;
      if (Math.abs(Math.abs(dot(n, f.normal)) - 1) > 1e-3) continue;
      const d = dot([f.centre[0] - o[0], f.centre[1] - o[1], f.centre[2] - o[2]], n);
      if (Math.abs(d) > 1e-6) this.planes.push([d, f.name]);
    }
    this.points = [[[0, 0], "centre"]];
    for (const f of faces) {
      if (f.shape !== "cylinder" || !f.axis) continue;
      if (Math.abs(Math.abs(dot(n, f.axis)) - 1) > 1e-3) continue;
      this.points.push([toUV(f.centre, frame), f.name]);
    }
  }
  distance(raw, mmPerPixel) {
    let best = raw, label = null;
    for (const [d, name] of this.planes) {
      if (Math.abs(d - raw) <= Snapper.PIXELS * mmPerPixel && (label === null || Math.abs(d - raw) < Math.abs(best - raw))) { best = d; label = name; }
    }
    return [best, label];
  }
  uv(uv, mmPerPixel) {
    const reach = Snapper.PIXELS * mmPerPixel;
    for (const [[pu, pv], name] of this.points) if (Math.hypot(uv[0] - pu, uv[1] - pv) <= reach) return [[pu, pv], name];
    for (const [[pu, pv], name] of this.points) {
      if (Math.abs(uv[0] - pu) <= reach) return [[pu, uv[1]], `${name} (u)`];
      if (Math.abs(uv[1] - pv) <= reach) return [[uv[0], pv], `${name} (v)`];
    }
    return [uv, null];
  }
}

// a face frame: origin, outward normal, x_axis; (u, v) are millimetres in the face's own plane
export function toUV(point, frame) {
  const d = [point[0] - frame.origin[0], point[1] - frame.origin[1], point[2] - frame.origin[2]];
  const x = frame.x_axis, n = frame.normal;
  const y = [n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0]];
  return [d[0] * x[0] + d[1] * x[1] + d[2] * x[2], d[0] * y[0] + d[1] * y[1] + d[2] * y[2]];
}
export function to3D(uv, frame) {
  const x = frame.x_axis, n = frame.normal;
  const y = [n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0]];
  return [0, 1, 2].map((i) => frame.origin[i] + x[i] * uv[0] + y[i] * uv[1]);
}
export const snap = (value, step) => Math.round(value / step) * step;
