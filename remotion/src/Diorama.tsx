// "Diorama" 3D style (owner, 2026-11-21: Three.js beats that look better than claymation): a floating low-poly island - layered
// earth, grass, a road, low-poly trees, houses, vehicles - with numbered label pins, warm soft-shadow light and a slow dolly.
// Original code (no downloaded models or third-party style kits); deterministic: the same spec always renders the same frames.
import React, { useLayoutEffect, useMemo } from "react";
import { useThree } from "@react-three/fiber";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig, Easing } from "remotion";
import { ThreeCanvas } from "@remotion/three";
import * as THREE from "three";

export type DioProp = { kind: "house" | "factory" | "tree" | "car" | "truck" | "tower" | "person" | "box" | "windmill" | "tank"; label?: string; color?: string };
export type DioramaSpec = { style?: "diorama"; title?: string; props: DioProp[]; sky?: string; seed?: number };

const rnd = (s: number) => { let x = Math.sin(s * 9301 + 49297) * 233280; return x - Math.floor(x); };
const M: React.FC<{ c: string; r?: number; flat?: boolean }> = ({ c, r = 0.85, flat = true }) => <meshStandardMaterial color={c} roughness={r} metalness={0} flatShading={flat} />;

const Tree: React.FC<{ p: [number, number, number]; s?: number; c?: string }> = ({ p, s = 1, c = "#3f9b4a" }) => (
  <group position={p} scale={s}>
    <mesh castShadow position={[0, 0.25, 0]}><cylinderGeometry args={[0.06, 0.09, 0.5, 6]} /><M c="#7a5230" /></mesh>
    <mesh castShadow position={[0, 0.75, 0]}><icosahedronGeometry args={[0.38, 0]} /><M c={c} /></mesh>
    <mesh castShadow position={[0.12, 1.02, 0.05]}><icosahedronGeometry args={[0.24, 0]} /><M c={c} /></mesh>
  </group>
);
const House: React.FC<{ c?: string }> = ({ c = "#e9d6b5" }) => (
  <group>
    <mesh castShadow receiveShadow position={[0, 0.35, 0]}><boxGeometry args={[0.9, 0.7, 0.7]} /><M c={c} /></mesh>
    <mesh castShadow position={[0, 0.88, 0]} rotation={[0, Math.PI / 4, 0]}><coneGeometry args={[0.72, 0.42, 4]} /><M c="#c2452d" /></mesh>
    <mesh position={[0.2, 0.32, 0.351]}><boxGeometry args={[0.18, 0.18, 0.02]} /><meshStandardMaterial color="#ffd77a" emissive="#ffb94a" emissiveIntensity={0.6} /></mesh>
    <mesh position={[-0.2, 0.22, 0.351]}><boxGeometry args={[0.16, 0.36, 0.02]} /><M c="#6b4426" /></mesh>
  </group>
);
const Factory: React.FC<{ c?: string }> = ({ c = "#6f93b8" }) => (
  <group>
    <mesh castShadow receiveShadow position={[0, 0.4, 0]}><boxGeometry args={[1.3, 0.8, 0.8]} /><M c={c} /></mesh>
    {[-0.4, 0, 0.4].map((x, i) => <mesh key={i} castShadow position={[x, 0.92, 0]} rotation={[0, 0, Math.PI / 4]}><boxGeometry args={[0.32, 0.32, 0.78]} /><M c="#47678a" /></mesh>)}
    <mesh castShadow position={[0.5, 1.15, -0.2]}><cylinderGeometry args={[0.09, 0.11, 0.9, 8]} /><M c="#8a8f99" /></mesh>
  </group>
);
const Car: React.FC<{ c?: string; long?: boolean }> = ({ c = "#e0432f", long }) => (
  <group>
    <mesh castShadow position={[0, 0.16, 0]}><boxGeometry args={[long ? 1.1 : 0.7, 0.22, 0.36]} /><M c={c} flat={false} r={0.4} /></mesh>
    <mesh castShadow position={[long ? -0.25 : -0.05, 0.34, 0]}><boxGeometry args={[long ? 0.45 : 0.38, 0.2, 0.32]} /><M c={long ? "#f4f4f4" : c} flat={false} r={0.4} /></mesh>
    {[-1, 1].flatMap((sx) => [-1, 1].map((sz) => <mesh key={`${sx}${sz}`} position={[sx * (long ? 0.38 : 0.24), 0.07, sz * 0.18]} rotation={[Math.PI / 2, 0, 0]}><cylinderGeometry args={[0.08, 0.08, 0.06, 10]} /><M c="#1c1c1c" /></mesh>))}
  </group>
);
const Tower: React.FC<{ c?: string }> = ({ c = "#d9dde4" }) => (
  <group>
    <mesh castShadow position={[0, 0.9, 0]}><cylinderGeometry args={[0.16, 0.26, 1.8, 8]} /><M c={c} /></mesh>
    <mesh castShadow position={[0, 1.9, 0]}><sphereGeometry args={[0.22, 10, 8]} /><meshStandardMaterial color="#ff6b4a" emissive="#ff4a2a" emissiveIntensity={0.5} /></mesh>
  </group>
);
const Person: React.FC<{ c?: string }> = ({ c = "#3a6fd8" }) => (
  <group>
    <mesh castShadow position={[0, 0.2, 0]}><capsuleGeometry args={[0.09, 0.22, 4, 8]} /><M c={c} flat={false} /></mesh>
    <mesh castShadow position={[0, 0.45, 0]}><sphereGeometry args={[0.09, 10, 8]} /><M c="#f0c9a0" flat={false} /></mesh>
  </group>
);
const Box: React.FC<{ c?: string }> = ({ c = "#c8954f" }) => (
  <group>{[[0, 0.15, 0], [0.32, 0.15, 0.05], [0.14, 0.45, 0.02]].map((p, i) => <mesh key={i} castShadow position={p as any}><boxGeometry args={[0.3, 0.3, 0.3]} /><M c={c} /></mesh>)}</group>
);
const Windmill: React.FC<{ c?: string; f: number }> = ({ c = "#f2f2f2", f }) => (
  <group>
    <mesh castShadow position={[0, 0.8, 0]}><cylinderGeometry args={[0.05, 0.08, 1.6, 8]} /><M c={c} /></mesh>
    <group position={[0, 1.6, 0.08]} rotation={[0, 0, f * 0.05]}>{[0, 1, 2].map((i) => <mesh key={i} castShadow rotation={[0, 0, (i * 2 * Math.PI) / 3]} position={[0, 0, 0]}><boxGeometry args={[0.07, 0.9, 0.02]} /><M c={c} /></mesh>)}</group>
  </group>
);
const Tank: React.FC<{ c?: string }> = ({ c = "#9fb3c8" }) => (
  <mesh castShadow position={[0, 0.45, 0]}><cylinderGeometry args={[0.38, 0.38, 0.9, 16]} /><M c={c} flat={false} r={0.5} /></mesh>
);

const Pin: React.FC<{ n: number; text: string; pos: [number, number, number] }> = ({ n, text, pos }) => {
  const tex = useMemo(() => {
    const cv = document.createElement("canvas"); cv.width = 640; cv.height = 150; const g = cv.getContext("2d")!;
    g.fillStyle = "#e84a33"; g.beginPath(); g.roundRect(70, 30, 560, 90, 18); g.fill();
    g.fillStyle = "#fff"; g.beginPath(); g.arc(70, 75, 58, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#e84a33"; g.font = "900 70px Montserrat, sans-serif"; g.textAlign = "center"; g.textBaseline = "middle"; g.fillText(String(n), 70, 80);
    g.fillStyle = "#fff"; g.textAlign = "left"; let z = 56; g.font = `800 ${z}px Montserrat, sans-serif`;
    const T = text.toUpperCase().slice(0, 18); while (z > 28 && g.measureText(T).width > 500) { z -= 4; g.font = `800 ${z}px Montserrat, sans-serif`; }
    g.fillText(T, 150, 78);
    const t = new THREE.CanvasTexture(cv); t.anisotropy = 8; return t;
  }, [n, text]);
  return <sprite position={pos} scale={[1.6, 0.375, 1]}><spriteMaterial map={tex} depthTest={false} /></sprite>;
};

const Camera: React.FC<{ secs: number }> = ({ secs }) => {
  const f = useCurrentFrame(); const { fps, width, height } = useVideoConfig(); const { camera } = useThree();
  const k = height > width ? 2.1 : 1, up = height > width ? 1.55 : 1;   // portrait: further back and higher (more top-down) so the island fills the tall frame   // portrait (Shorts): stand back so the whole island fits the narrow frame
  useLayoutEffect(() => {
    const p = interpolate(f / fps, [0, secs], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.inOut(Easing.cubic) });
    const a = -0.5 + p * 0.5, r = 11.5 - p * 1.6, y = 6.2 - p * 1.0;
    camera.position.set(Math.sin(a) * r * k, y * k * up, Math.cos(a) * r * k); camera.lookAt(0, height > width ? 0.2 : 0.6, 0); camera.updateProjectionMatrix();
  }, [f, fps, secs, camera, k, up]);
  return null;
};

const Island: React.FC<{ spec: DioramaSpec; f: number }> = ({ spec, f }) => {
  const seed = spec.seed ?? 7;
  // slots around the island for the spec's props (up to 8), trees fill the rest
  const slots: [number, number][] = [[-1.7, 0.2], [1.6, 0.3], [0, -1.0], [-0.4, 1.3], [2.1, -1.2], [-2.2, -1.3], [1.0, 1.4], [-0.9, -1.5]];
  const props = (spec.props || []).slice(0, 8);
  const trees = useMemo(() => Array.from({ length: 16 }, (_, i) => {
    const a = rnd(seed + i) * Math.PI * 2, d = 1.1 + rnd(seed + i * 3) * 1.6;
    return { p: [Math.cos(a) * d * 1.25, 0, Math.sin(a) * d] as [number, number, number], s: 0.6 + rnd(seed + i * 5) * 0.6, c: rnd(seed + i * 7) > 0.5 ? "#3f9b4a" : "#56b05a" };
  }).filter((t) => t.p[2] < 0.6 || Math.abs(t.p[0]) > 2.3).filter((t) => slots.slice(0, props.length).every(([x, z]) => Math.hypot(t.p[0] - x, t.p[2] - z) > 0.9)), [seed, props.length]);
  const roadPts = useMemo(() => new THREE.CatmullRomCurve3([new THREE.Vector3(-2.8, 0.02, 1.6), new THREE.Vector3(-0.6, 0.02, 0.4), new THREE.Vector3(0.8, 0.02, 0.9), new THREE.Vector3(2.8, 0.02, -0.4)]), []);
  return (
    <group>
      {/* layered earth slab: grass, soil, rock */}
      <mesh receiveShadow position={[0, -0.1, 0]}><boxGeometry args={[6.4, 0.2, 4.4]} /><M c="#7cbf5a" /></mesh>
      <mesh position={[0, -0.45, 0]}><boxGeometry args={[6.3, 0.5, 4.3]} /><M c="#9a6a3c" /></mesh>
      <mesh position={[0, -0.85, 0]}><boxGeometry args={[6.1, 0.3, 4.1]} /><M c="#6e4a2a" /></mesh>
      <mesh receiveShadow position={[0, 0.001, 0]}><tubeGeometry args={[roadPts, 64, 0.32, 2, false]} /><M c="#5b6170" /></mesh>
      <mesh position={[-1.9, 0.01, -1.5]} rotation={[-Math.PI / 2, 0, 0]}><circleGeometry args={[0.55, 20]} /><meshStandardMaterial color="#5fb7e8" roughness={0.2} /></mesh>
      {trees.map((t, i) => <Tree key={i} p={t.p} s={t.s} c={t.c} />)}
      {props.map((pr, i) => {
        const [x, z] = slots[i]; const c = pr.color;
        const el = pr.kind === "house" ? <House c={c} /> : pr.kind === "factory" ? <Factory c={c} /> : pr.kind === "tree" ? <Tree p={[0, 0, 0]} s={1.3} c={c} />
          : pr.kind === "car" ? <Car c={c} /> : pr.kind === "truck" ? <Car c={c || "#e0432f"} long /> : pr.kind === "tower" ? <Tower c={c} />
          : pr.kind === "person" ? <Person c={c} /> : pr.kind === "windmill" ? <Windmill c={c} f={f} /> : pr.kind === "tank" ? <Tank c={c} /> : <Box c={c} />;
        const pop = interpolate(f, [8 + i * 6, 22 + i * 6], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp", easing: Easing.out(Easing.back(1.6)) });
        return (
          <group key={i} position={[x, 0, z]} rotation={[0, rnd(seed + i * 11) * 0.8 - 0.4, 0]} scale={Math.max(0.001, pop)}>
            {el}
            {pr.label ? <Pin n={i + 1} text={pr.label} pos={[0, (pr.kind === "tower" || pr.kind === "windmill" ? 2.15 : 1.45) + (i % 2) * 0.3, 0]} /> : null}
          </group>
        );
      })}
    </group>
  );
};

export const Diorama: React.FC<{ spec: DioramaSpec; secs: number }> = ({ spec, secs }) => {
  const f = useCurrentFrame(); const { width, height } = useVideoConfig();
  const bob = Math.sin(f / 30) * 0.04;
  return (
    <AbsoluteFill style={{ background: `radial-gradient(ellipse at 50% 35%, ${spec.sky || "#9cc7e8"} 0%, #6f9cc4 100%)` }}>
      <ThreeCanvas width={width} height={height} shadows gl={{ antialias: true, preserveDrawingBuffer: true }} camera={{ fov: 32, near: 0.1, far: 100 }}>
        <Camera secs={secs} />
        <hemisphereLight args={["#fff6e0", "#6a8aa8", 1.0]} />
        <directionalLight castShadow position={[5, 9, 4]} intensity={1.6} color="#fff1d6" shadow-mapSize={[2048, 2048]}
          shadow-camera-left={-6} shadow-camera-right={6} shadow-camera-top={6} shadow-camera-bottom={-6} shadow-bias={-0.0005} />
        <group position={[0, bob, 0]}><Island spec={spec} f={f} /></group>
      </ThreeCanvas>
      {spec.title ? (
        <div style={{ position: "absolute", left: 70, top: 56, padding: "14px 26px", borderRadius: 14, background: "rgba(14,22,34,0.82)", color: "#fff",
          font: "800 46px Montserrat, sans-serif", letterSpacing: 1, opacity: interpolate(f, [0, 15], [0, 1], { extrapolateRight: "clamp" }) }}>{spec.title}</div>
      ) : null}
    </AbsoluteFill>
  );
};
