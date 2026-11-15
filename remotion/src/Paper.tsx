// Motion rung (owner, 2026-11-12): drawn animations - the LAST resort for a stretch, after every picture rung failed, just
// before the card. No credits, no lockouts. Two looks (setting motion_look, passed in as d.look):
//   paper:      soft paper, pencil-dark ink, grain - sits quietly between the owner's own footage
//   whiteboard: a white board, blue/red marker, every line DRAWS ITSELF on in sequence and text is written out
// Templates:
//   place:  a zoom from the region down to one spot, pin and label (Natural Earth outlines, bundled offline)
//   figure: stick figures making a point (point / think / shrug / talk / walk / sign) with a simple prop and a caption
import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig, Easing } from "remotion";
import { geoMercator, geoPath } from "d3-geo";
import { feature } from "topojson-client";
import world from "world-atlas/countries-50m.json";

const countries = (feature as any)(world as any, (world as any).objects.countries);
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const HAND = "'Segoe Print', 'Comic Sans MS', 'Bradley Hand', cursive";

type Theme = { wb: boolean; bg: string; paper: string; ink: string; soft: string; accent: string; sea: string; land: string; bubble: string };
const theme = (look?: string): Theme => look === "whiteboard"
  ? { wb: true, bg: "linear-gradient(170deg, #ffffff 0%, #f6f7f8 55%, #eceef0 100%)", paper: "#ffffff", ink: "#1f3a5f", soft: "#9aa7b4",
      accent: "#d1342f", sea: "#f2f6f9", land: "#ffffff", bubble: "#ffffff" }
  : { wb: false, bg: "#f1ece2", paper: "#f1ece2", ink: "#2b2a28", soft: "#8a8174", accent: "#c8553d", sea: "#d6e3e4", land: "#e8dfcc", bubble: "#fffaf0" };

const useT = () => {
  const f = useCurrentFrame(); const { fps, width, height } = useVideoConfig();
  return { f, t: f / fps, W: width, H: height, tall: height > width };
};
const appear = (t: number, at: number, len = 0.45) => interpolate(t, [at, at + len], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
// hand-drawn "boil": a tiny jitter that changes ~8 times a second (paper only; marker lines on a board stay put)
const jit = (f: number, i: number, a = 1.2) => { const s = Math.sin((Math.floor(f / 4) + 1) * 12.9898 + i * 78.233) * 43758.5453; return ((s - Math.floor(s)) - 0.5) * 2 * a; };
// whiteboard draw-on: stroke dash revealed from 0 to the full line (inherited by everything inside a <g>)
const drawOn = (th: Theme, k: number, len = 3000) => th.wb ? { strokeDasharray: `${len} ${len}`, strokeDashoffset: len * (1 - k) } : {};
// whiteboard text is written out; paper text fades in
const write = (th: Theme, s: string, k: number) => th.wb ? s.slice(0, Math.round(s.length * k)) : s;

const Surface: React.FC<{ th: Theme }> = ({ th }) => th.wb ? (
  <AbsoluteFill style={{ background: "linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.65) 42%, transparent 52%)", opacity: 0.6 }} />
) : (<>
  <AbsoluteFill style={{ opacity: 0.35, mixBlendMode: "multiply",
    backgroundImage: "radial-gradient(rgba(0,0,0,0.08) 1px, transparent 1px), radial-gradient(rgba(0,0,0,0.05) 1px, transparent 1px)",
    backgroundSize: "7px 7px, 11px 11px", backgroundPosition: "0 0, 3px 5px" }} />
  <AbsoluteFill style={{ background: "radial-gradient(ellipse at 50% 48%, transparent 55%, rgba(60,45,25,0.22) 100%)" }} />
</>);

const Caption: React.FC<{ th: Theme; text?: string; k: number; y: number }> = ({ th, text, k, y }) => text ? (
  <div style={{ position: "absolute", left: 0, right: 0, top: y, textAlign: "center", opacity: th.wb ? 1 : k }}>
    <span style={{ fontFamily: HAND, fontSize: 52, color: th.ink, background: th.wb ? "transparent" : "rgba(255,252,244,0.75)", padding: "6px 26px", borderRadius: 14 }}>
      {write(th, String(text).slice(0, 60), k)}</span>
  </div>) : null;

// ------------------------------------------------------------------ place: zoom to one spot
export const Place: React.FC<{ d: any; dur: number }> = ({ d, dur }) => {
  const { t, W, H, tall } = useT(); const th = theme(d.look);
  const lat = +d.lat, lon = +d.lon;
  const z = interpolate(t, [0.2, Math.max(1.6, dur * 0.65)], [0, 1], { ...clamp, easing: Easing.inOut(Easing.cubic) });
  const half = Math.exp(interpolate(z, [0, 1], [Math.log(38), Math.log(Math.max(0.12, +d.span || 1.2))]));
  const top = tall ? 300 : 60, bottom = tall ? 1330 : H - 60;
  const box = { type: "MultiPoint", coordinates: [[lon - half, Math.max(-80, lat - half * 0.62)], [lon + half, Math.min(80, lat + half * 0.62)]] } as any;
  const proj = geoMercator().fitExtent([[40, top], [W - 40, bottom]], box);
  const path = geoPath(proj);
  const [x, y] = proj([lon, lat]) as [number, number];
  const pin = appear(t, Math.max(1.4, dur * 0.62), 0.5);
  const pulse = (t * 0.9) % 1;
  const coast = appear(t, 0, 1.4);   // whiteboard: the coastlines are drawn on first
  return (
    <AbsoluteFill style={{ background: th.sea }}>
      <svg width={W} height={H}>
        <g fill={th.land} stroke={th.wb ? th.ink : th.soft} strokeWidth={th.wb ? 2.2 : 1.6} style={drawOn(th, coast, 4000)}>
          {(countries.features as any[]).map((c, i) => <path key={i} d={path(c) || ""} />)}</g>
        {pin > 0 ? <g opacity={th.wb ? 1 : pin}>
          <circle cx={x} cy={y} r={20 + 46 * pulse} fill="none" stroke={th.accent} strokeWidth={4} opacity={1 - pulse} />
          {th.wb
            ? <g fill="none" stroke={th.accent} strokeWidth={8} strokeLinecap="round" style={drawOn(th, pin, 400)}>
                <circle cx={x} cy={y} r={34} /><path d={`M${x - 16},${y - 16} L${x + 16},${y + 16} M${x + 16},${y - 16} L${x - 16},${y + 16}`} /></g>
            : <g transform={`translate(0 ${(1 - pin) * -60})`}>
                <path d={`M${x},${y} c-18,-26 -30,-40 -30,-58 a30,30 0 1 1 60,0 c0,18 -12,32 -30,58z`} fill={th.accent} stroke={th.ink} strokeWidth={3} />
                <circle cx={x} cy={y - 58} r={10} fill={th.paper} /></g>}
        </g> : null}
      </svg>
      <Surface th={th} />
      {d.name ? <div style={{ position: "absolute", left: 0, right: 0, top: tall ? 1380 : H - 190, textAlign: "center", opacity: th.wb ? 1 : pin }}>
        <div style={{ fontFamily: HAND, fontSize: tall ? 76 : 70, color: th.ink }}>{write(th, String(d.name).slice(0, 32), pin)}</div>
        {d.sub ? <div style={{ fontFamily: HAND, fontSize: 40, color: th.soft, marginTop: 4 }}>{write(th, String(d.sub).slice(0, 40), appear(t, Math.max(1.9, dur * 0.7), 0.5))}</div> : null}
      </div> : null}
    </AbsoluteFill>
  );
};

// ------------------------------------------------------------------ figure: stick figures making a point
type Pose = "point" | "think" | "shrug" | "talk" | "walk" | "sign" | "stand";
const Stick: React.FC<{ th: Theme; x: number; y: number; s: number; pose: Pose; t: number; f: number; face: 1 | -1; seed: number; sign?: string; k: number }> =
  ({ th, x, y, s, pose, t, f, face, seed, sign, k }) => {
  const J = (i: number) => th.wb ? 0 : jit(f, seed * 31 + i);
  const P = (px: number, py: number, i: number) => `${x + (px * face) * s + J(i)},${y + py * s + J(i + 7)}`;
  const walk = pose === "walk" ? Math.sin(t * 7) : 0;
  const legs = `M${P(0, -55, 1)} L${P(-22 - 14 * walk, 0, 2)} M${P(0, -55, 3)} L${P(22 + 14 * walk, 0, 4)}`;
  const talk = Math.sin(t * 5);
  const arms: Record<Pose, string> = {
    point: `M${P(0, -112, 5)} L${P(48, -128, 6)} L${P(92, -150, 7)} M${P(0, -112, 8)} L${P(-18, -82, 9)} L${P(-22, -58, 10)}`,
    think: `M${P(0, -112, 5)} L${P(30, -92, 6)} L${P(12, -140, 7)} M${P(0, -112, 8)} L${P(-20, -84, 9)} L${P(-8, -64, 10)}`,
    shrug: `M${P(0, -112, 5)} L${P(36, -100, 6)} L${P(58, -136, 7)} M${P(0, -112, 8)} L${P(-36, -100, 9)} L${P(-58, -136, 10)}`,
    talk: `M${P(0, -112, 5)} L${P(38, -104 + 6 * talk, 6)} L${P(70, -126 - 14 * talk, 7)} M${P(0, -112, 8)} L${P(-20, -84, 9)} L${P(-24, -60, 10)}`,
    walk: `M${P(0, -112, 5)} L${P(-24 * walk, -62, 6)} M${P(0, -112, 8)} L${P(24 * walk, -62, 9)}`,
    sign: `M${P(0, -112, 5)} L${P(28, -150, 6)} L${P(30, -196, 7)} M${P(0, -112, 8)} L${P(-28, -150, 9)} L${P(-30, -196, 10)}`,
    stand: `M${P(0, -112, 5)} L${P(18, -84, 6)} L${P(22, -60, 7)} M${P(0, -112, 8)} L${P(-18, -84, 9)} L${P(-22, -60, 10)}`,
  };
  const tilt = pose === "shrug" ? 8 : pose === "think" ? -6 : 0;
  const signW = Math.max(180, String(sign || "").slice(0, 14).length * 24 + 40) * s;   // the board fits its words
  // whiteboard: head, then body, then legs, then arms, each drawn on in turn
  const part = (i: number) => clamp01(k * 4 - i);
  return (
    <g fill="none" stroke={th.ink} strokeWidth={7 * s} strokeLinecap="round" strokeLinejoin="round">
      <circle cx={x + J(13)} cy={y - 152 * s + J(14)} r={24 * s} transform={`rotate(${tilt * face} ${x} ${y - 128 * s})`} fill={th.paper} style={drawOn(th, part(0), 400)} />
      <path d={`M${P(0, -128, 11)} L${P(0, -55, 12)}`} style={drawOn(th, part(1), 400)} />
      <path d={legs} style={drawOn(th, part(2), 600)} /><path d={arms[pose]} style={drawOn(th, part(3), 600)} />
      {pose === "sign" ? <g opacity={th.wb ? 1 : k}>
        <rect x={x - signW / 2} y={y - 290 * s} width={signW} height={96 * s} rx={8} fill={th.bubble} fillOpacity={th.wb ? part(3) : 1} style={drawOn(th, part(3), 1600)} />
        <text x={x} y={y - 230 * s} textAnchor="middle" fontFamily={HAND} fontSize={34 * s} fill={th.accent} stroke="none">{write(th, String(sign || "").slice(0, 14), part(3))}</text>
      </g> : null}
    </g>
  );
};
const clamp01 = (v: number) => Math.max(0, Math.min(1, v));

const PROPS: Record<string, (x: number, y: number, s: number, th: Theme) => React.ReactNode> = {
  house: (x, y, s) => <path d={`M${x - 90 * s},${y} v-110 h${180 * s} v110 z M${x - 110 * s},${y - 110 * s} L${x},${y - 200 * s} L${x + 110 * s},${y - 110 * s} M${x - 20 * s},${y} v-60 h${40 * s} v60`} />,
  shop: (x, y, s) => <path d={`M${x - 100 * s},${y} v-120 h${200 * s} v120 z M${x - 115 * s},${y - 120 * s} l${20 * s},-40 h${190 * s} l${20 * s},40 M${x - 60 * s},${y - 120 * s} v40 M${x},${y - 120 * s} v40 M${x + 60 * s},${y - 120 * s} v40 M${x - 70 * s},${y - 70 * s} h${60 * s} v40 h-${60 * s} z`} />,
  mosque: (x, y, s) => <path d={`M${x - 90 * s},${y} v-90 h${180 * s} v90 z M${x - 70 * s},${y - 90 * s} a${70 * s},${70 * s} 0 0 1 ${140 * s},0 M${x},${y - 160 * s} v-25 M${x + 130 * s},${y} v-210 M${x + 115 * s},${y} v-200 M${x + 122 * s},${y - 210 * s} l0,-20`} />,
  plane: (x, y, s) => <path d={`M${x - 120 * s},${y - 120 * s} h${210 * s} q${30 * s},0 ${30 * s},10 q0,10 -${30 * s},10 h-${210 * s} z M${x - 10 * s},${y - 120 * s} l-40,-60 h${30 * s} l60,60 M${x - 10 * s},${y - 100 * s} l-40,50 h${30 * s} l60,-50 M${x - 110 * s},${y - 120 * s} l-15,-35 h${20 * s} l30,35`} />,
  boat: (x, y, s) => <path d={`M${x - 110 * s},${y - 40 * s} h${220 * s} l-30,40 h-${160 * s} z M${x},${y - 40 * s} v-170 l${90 * s},150 z M${x - 140 * s},${y + 10 * s} q20,-12 40,0 t40,0 t40,0 t40,0 t40,0 t40,0 t40,0`} />,
  car: (x, y, s) => <path d={`M${x - 120 * s},${y - 30 * s} v-40 l${40 * s},-50 h${130 * s} l${40 * s},50 h${30 * s} v40 z M${x - 70 * s},${y - 20 * s} a22,22 0 1 0 1,0 M${x + 70 * s},${y - 20 * s} a22,22 0 1 0 1,0`} />,
  money: (x, y, s, th) => <g><circle cx={x} cy={y - 90 * s} r={70 * s} /><text x={x} y={y - 64 * s} textAnchor="middle" fontFamily={HAND} fontSize={90 * s} fill={th.accent} stroke="none">$</text></g>,
  question: (x, y, s, th) => <text x={x} y={y - 40 * s} textAnchor="middle" fontFamily={HAND} fontSize={220 * s} fill={th.accent} stroke="none">?</text>,
};

export const Figure: React.FC<{ d: any; dur: number }> = ({ d, dur }) => {
  const { t, f, W, H, tall } = useT(); const th = theme(d.look);
  const poses: Pose[] = ["point", "think", "shrug", "talk", "walk", "sign", "stand"];
  const pose: Pose = poses.includes(d.pose) ? d.pose : "stand";
  const n = Math.max(1, Math.min(3, Number(d.people) || 1));
  const s = tall ? 1.7 : 1.75;
  const ground = tall ? 1180 : H * 0.74;
  const prop = PROPS[String(d.prop || "")];
  const enter = pose === "walk" ? interpolate(t, [0, Math.min(2.2, dur * 0.5)], [-260, 0], { ...clamp, easing: Easing.out(Easing.quad) }) : 0;
  // paper fades things in; the whiteboard draws them in order: ground, people, prop, bubbles, caption
  const kGround = th.wb ? appear(t, 0, 0.5) : appear(t, 0);
  const kPeople = th.wb ? interpolate(t, [0.4, 1.8], [0, 1], clamp) : appear(t, 0.1, 0.5);
  const kProp = th.wb ? interpolate(t, [1.6, 2.6], [0, 1], clamp) : appear(t, 0.6, 0.6);
  const xs = n === 1 ? [W * (prop ? 0.36 : 0.5)] : n === 2 ? [W * 0.27, W * 0.55] : [W * 0.22, W * 0.38, W * 0.54];
  const say: string[] = (Array.isArray(d.say) ? d.say : []).map((x: any) => String(x).slice(0, 26)).slice(0, 2);
  const groundPath = `M${W * 0.08},${ground + (th.wb ? 0 : jit(f, 90))} Q${W * 0.5},${ground + 6 + (th.wb ? 0 : jit(f, 91))} ${W * 0.92},${ground + (th.wb ? 0 : jit(f, 92))}`;
  const t0 = th.wb ? 2.4 : 0.9;   // bubbles and thought after the drawing
  return (
    <AbsoluteFill style={{ background: th.bg }}>
      <svg width={W} height={H}>
        <path d={groundPath} fill="none" stroke={th.soft} strokeWidth={4} strokeLinecap="round" opacity={th.wb ? 1 : kGround} style={drawOn(th, kGround, 2400)} />
        <g opacity={th.wb ? 1 : kPeople} transform={`translate(${enter} 0)`}>
          {xs.map((x, i) => <Stick key={i} th={th} x={x} y={ground} s={s} t={t + i * 0.4} f={f} seed={i + 1}
            k={th.wb ? clamp01(kPeople * xs.length - i) : 1}
            pose={n === 2 && i === 1 && pose !== "walk" ? "talk" : pose} face={n === 2 && i === 1 ? -1 : 1} sign={d.sign} />)}
        </g>
        {prop ? <g fill="none" stroke={th.ink} strokeWidth={6} strokeLinecap="round" strokeLinejoin="round" opacity={th.wb ? 1 : kProp}
          transform={th.wb ? undefined : `translate(0 ${(1 - kProp) * 30})`} style={drawOn(th, kProp, 2600)}>{prop(W * (n > 1 ? 0.76 : 0.68), ground, s, th)}</g> : null}
        {pose === "think" ? <g opacity={appear(t, t0)} fill={th.paper} stroke={th.ink} strokeWidth={4}>
          <circle cx={xs[0] + 60 * s} cy={ground - 230 * s} r={9} /><circle cx={xs[0] + 85 * s} cy={ground - 262 * s} r={14} />
          <ellipse cx={xs[0] + 150 * s} cy={ground - 320 * s} rx={78 * s} ry={50 * s} />
          <text x={xs[0] + 150 * s} y={ground - 300 * s} textAnchor="middle" fontFamily={HAND} fontSize={64 * s} fill={th.accent} stroke="none">?</text>
        </g> : null}
        {say.map((txt, i) => {
          // each bubble sized to its words, above its own speaker (the first to the left, a reply to the right)
          const bw = Math.max(160, txt.length * 21) * s * 0.75 + 70;
          const sx = xs[Math.min(i, xs.length - 1)];
          const bx = Math.min(W - bw / 2 - 30, Math.max(bw / 2 + 30, sx + (i ? 1 : -1) * bw * 0.25)), by = ground - 300 * s;
          const k = th.wb ? interpolate(t, [t0 + i * 1.2, t0 + i * 1.2 + 0.9], [0, 1], clamp) : appear(t, t0 + i * 1.1, 0.4);
          return <g key={i} opacity={th.wb ? (k > 0 ? 1 : 0) : k}>
            <g style={drawOn(th, k, 1600)}>
              <rect x={bx - bw / 2} y={by - 50 * s} width={bw} height={86 * s} rx={30} fill={th.bubble} stroke={th.ink} strokeWidth={4} />
              <path d={`M${sx + (i ? -10 : 10) * s},${by + 36 * s} L${sx},${ground - 190 * s} L${sx + (i ? 18 : -18) * s},${by + 36 * s}`} fill={th.bubble} stroke={th.ink} strokeWidth={4} />
            </g>
            <text x={bx} y={by + 6 * s} textAnchor="middle" fontFamily={HAND} fontSize={32 * s} fill={i ? th.accent : th.ink}>{write(th, txt, k)}</text>
          </g>;
        })}
      </svg>
      <Surface th={th} />
      <Caption th={th} text={d.caption} k={th.wb ? interpolate(t, [3.2, 4.4], [0, 1], clamp) : appear(t, 1.2, 0.6)} y={tall ? 1290 : H * 0.82} />
    </AbsoluteFill>
  );
};
