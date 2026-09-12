// The picture: the kernel's triangles as one mesh per part, its edges as thin lines,
// a face picked by the triangle the ray hit. World is the kernel's millimetres, z up.
import React, { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { Canvas, useThree } from "@react-three/fiber";
import { OrbitControls, GizmoHelper, GizmoViewcube, Grid, ContactShadows, Line } from "@react-three/drei";
import { useStore, partOf } from "./store.js";
import Marks from "./Marks.jsx";

const ACCENT = new THREE.Color("#7aa2f7");
const BODY = new THREE.Color("#cfd4dc");
const SHADES = ["#cfd4dc", "#b9c3d3", "#d8cdbf", "#bfd3c6", "#cdc4d8", "#d6d0b8", "#b8cfd4", "#d4c0c6"];

function groups(mesh) {
  // vertices are not shared across CAD faces, so a vertex belongs to one face and one part
  const table = mesh.face_table || [];
  const parts = [...new Set(table.map(partOf))].sort();
  const byPart = parts.map(() => ({ tris: [], faces: [] }));
  mesh.triangles.forEach((t, k) => {
    const face = mesh.triangle_face[k];
    const p = parts.indexOf(partOf(table[face]));
    byPart[p].tris.push(t); byPart[p].faces.push(face);
  });
  return parts.map((name, p) => {
    const { tris, faces } = byPart[p];
    const n = tris.length * 3;
    const position = new Float32Array(n * 3), normal = new Float32Array(n * 3), faceId = new Int32Array(tris.length);
    tris.forEach((t, k) => {
      t.forEach((v, c) => {
        position.set(mesh.vertices[v], (k * 3 + c) * 3);
        normal.set(mesh.normals ? mesh.normals[v] : [0, 0, 1], (k * 3 + c) * 3);
      });
      faceId[k] = faces[k];
    });
    const edges = [];
    for (const [edge, line] of Object.entries(mesh.edges || {})) {
      if (partOf(edge.split("|")[0]) === name) edges.push({ name: edge, points: line.map((q) => new THREE.Vector3(...q)) });
    }
    return { name, position, normal, faceId, edges, shade: SHADES[p % SHADES.length] };
  });
}

function Part({ part, table, picked, pose }) {
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(part.position, 3));
    g.setAttribute("normal", new THREE.BufferAttribute(part.normal, 3));
    g.setAttribute("color", new THREE.BufferAttribute(new Float32Array(part.position.length), 3));
    return g;
  }, [part]);
  // the selected faces take the accent; everything else the part's own calm shade
  useEffect(() => {
    const colour = geometry.getAttribute("color"), base = new THREE.Color(part.shade);
    const chosen = new Set(picked.map((n) => table.indexOf(n)));
    for (let k = 0; k < part.faceId.length; k++) {
      const c = chosen.has(part.faceId[k]) ? ACCENT : base;
      for (let i = 0; i < 3; i++) colour.setXYZ(k * 3 + i, c.r, c.g, c.b);
    }
    colour.needsUpdate = true;
  }, [geometry, part, picked, table]);
  const group = useRef();
  useEffect(() => {
    const g = group.current;
    if (!g) return;
    g.matrixAutoUpdate = false;
    g.matrix.identity();
    if (pose && pose.offset) g.matrix.makeTranslation(...pose.offset);
    else if (pose && pose.pose) {
      const m = (p) => new THREE.Matrix4().set(
        p[0][0][0], p[0][0][1], p[0][0][2], p[1][0], p[0][1][0], p[0][1][1], p[0][1][2], p[1][1],
        p[0][2][0], p[0][2][1], p[0][2][2], p[1][2], 0, 0, 0, 1);
      g.matrix.copy(m(pose.pose).multiply(m(pose.rest).invert()));
    }
  }, [pose]);
  const pick = useStore((s) => s.pick), pickEdge = useStore((s) => s.pickEdge), onMenu = useStore((s) => s.onMenu);
  return (
    <group ref={group}>
      <mesh geometry={geometry}
        onClick={(e) => { if (e.delta > 3) return; e.stopPropagation(); pick(table[part.faceId[e.faceIndex]], e.shiftKey); }}
        onContextMenu={(e) => {
          if (e.delta > 3) return;
          e.stopPropagation();
          const name = table[part.faceId[e.faceIndex]];
          if (!useStore.getState().picked.includes(name)) pick(name, false);
          if (onMenu) onMenu(e.clientX, e.clientY);
        }}>
        <meshStandardMaterial vertexColors roughness={0.72} metalness={0.05} side={THREE.DoubleSide} />
      </mesh>
      {part.edges.map((edge) => (
        <Line key={edge.name} points={edge.points} color="#0b0d12" lineWidth={1} transparent opacity={0.75}
          onClick={(e) => { if (e.delta > 3) return; e.stopPropagation(); pickEdge(edge.name, e.shiftKey); }} />
      ))}
    </group>
  );
}

function Fit({ mesh, frameKey }) {
  const { camera, controls, size } = useThree();
  useEffect(() => {
    // for the page's tests: where a world point lands on the screen
    window.__realparts = { ...(window.__realparts || {}),
      project: (p) => { const q = new THREE.Vector3(...p).project(camera); return { x: (q.x + 1) / 2 * size.width, y: (1 - q.y) / 2 * size.height }; },
      pick: (name) => useStore.getState().pick(name, false),
      pickEdge: (name) => useStore.getState().pickEdge(name, false),
      state: () => { const s = useStore.getState(); return { picked: s.picked, pickedEdges: s.pickedEdges, feature: s.feature, drag: s.drag, hud: s.hud, tool: s.tool, section: s.section }; },
    };
  }, [camera, size]);
  useEffect(() => {
    if (!mesh || !controls) return;
    const points = mesh.vertices.length ? mesh.vertices : Object.values(mesh.edges || {}).flat();
    if (!points.length) return;
    const box = new THREE.Box3();
    for (const v of points) box.expandByPoint(new THREE.Vector3(...v));
    const centre = box.getCenter(new THREE.Vector3()), size = box.getSize(new THREE.Vector3()).length();
    controls.target.copy(centre);
    camera.position.copy(centre).add(new THREE.Vector3(0.75, -0.9, 0.55).multiplyScalar(size * 1.05));
    camera.near = size * 0.01; camera.far = size * 30; camera.updateProjectionMatrix();
    controls.update();
  }, [frameKey, mesh ? mesh.faces && mesh.faces.length : 0, controls]);      // eslint-disable-line
  return null;
}

export default function Scene() {
  const mesh = useStore((s) => s.mesh), picked = useStore((s) => s.picked), poses = useStore((s) => s.poses);
  const frameKey = useStore((s) => s.frameKey), pick = useStore((s) => s.pick);
  const parts = useMemo(() => (mesh ? groups(mesh) : []), [mesh]);
  const table = mesh ? mesh.face_table || [] : [];
  const extent = useMemo(() => {
    if (!mesh || !mesh.vertices.length) return 100;
    const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const v of mesh.vertices) for (let i = 0; i < 3; i++) { lo[i] = Math.min(lo[i], v[i]); hi[i] = Math.max(hi[i], v[i]); }
    return Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]);
  }, [mesh]);
  const floor = useMemo(() => {
    if (!mesh || !mesh.vertices.length) return 0;
    return Math.min(...mesh.vertices.map((v) => v[2])) - 0.5;
  }, [mesh]);
  const poseOf = (name) => poses[name] || Object.entries(poses).find(([scope]) => name.startsWith(scope + ":"))?.[1];
  return (
    <Canvas camera={{ position: [120, -150, 100], up: [0, 0, 1], fov: 34, near: 1, far: 5000 }}
      gl={{ antialias: true, alpha: true }} onPointerMissed={(e) => { if (e.button === 0 && e.type === "click") pick(null, e.shiftKey); }}
      onContextMenu={(e) => e.preventDefault()} style={{ position: "fixed", inset: 0 }}>
      <hemisphereLight args={["#ffffff", "#3a4256", 0.75]} />
      {/* no shadow map: at millimetre scale it striped every grazing face; the contact shadow grounds the part */}
      <directionalLight position={[180, -140, 260]} intensity={1.3} />
      <directionalLight position={[-160, 120, 80]} intensity={0.35} />
      {parts.map((part) => <Part key={part.name} part={part} table={table} picked={picked} pose={poseOf(part.name)} />)}
      {mesh?.under_construction && Object.entries(mesh.edges || {}).map(([name, points]) =>
        points.length > 1 && <Line key={name} points={points} color="#7aa2f7" lineWidth={2} />)}
      <Marks mesh={mesh} size={extent} />
      <group position={[0, 0, floor]} rotation={[Math.PI / 2, 0, 0]}>
        <Grid infiniteGrid fadeDistance={900} fadeStrength={2} cellSize={10} sectionSize={50}
          cellColor="#2a3040" sectionColor="#354055" cellThickness={0.6} sectionThickness={1} />
      </group>
      <ContactShadows position={[0, 0, floor]} rotation={[Math.PI / 2, 0, 0]} opacity={0.5} scale={600} blur={2.2} far={80} />
      <OrbitControls makeDefault enableDamping dampingFactor={0.12} mouseButtons={{ LEFT: THREE.MOUSE.ROTATE, MIDDLE: THREE.MOUSE.DOLLY, RIGHT: THREE.MOUSE.PAN }} />
      <GizmoHelper alignment="bottom-right" margin={[70, 70]}>
        <GizmoViewcube faces={["Right", "Left", "Back", "Front", "Top", "Bottom"]}
          color="#1f2430" hoverColor="#7aa2f7" textColor="#e9ecf1" strokeColor="#3a4256" opacity={0.9} />
      </GizmoHelper>
      <Fit mesh={mesh} frameKey={frameKey} />
    </Canvas>
  );
}
