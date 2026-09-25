// Building blocks for the database walkthrough. Variants of the admin video's
// pieces (src/admin/real.tsx), copied here rather than edited there:
// a psql terminal with multi-line statements and coloured errors, a screenshot
// frame that reads from public/database/, a scene-level sound scheduler, and
// the flat sticker card the diagrams are built from.
import React from "react";
import { AbsoluteFill, Easing, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { theme, tokens } from "../theme";
import { K, Pill, clamp, fadeIn, usePop } from "../admin/ui";
import { Sfx, SfxName, Typing } from "../sound";

// Viewport the content sits in (same as the admin video).
export const VX = 40;
export const VY = 40;
export const VW = 1840;
export const VH = 790;

const ease = Easing.inOut(Easing.cubic);

/* ---------------- sound scheduling ---------------- */

export type Ev = { at: number; name: SfxName; volume?: number };
const PRIORITY: Record<SfxName, number> = { error: 6, success: 6, notify: 5, ding: 5, click: 4, whoosh: 3, pop: 2, key: 1 };
const MIN_GAP = 8;

/**
 * Every non-typing sound in a scene, in scene frames. A whoosh at 0 is always
 * added; `taken` are frames already used by something that plays its own sound
 * (the Caption's STEP badge pops at 9). Higher priority wins and nothing lands
 * within 8 frames of another.
 */
export const schedule = (evs: Ev[], dur: number, taken: number[] = [9]): Ev[] => {
  const all: Ev[] = [{ at: 0, name: "whoosh" }, ...evs];
  const out: Ev[] = [];
  const busy = taken.map((at) => ({ at }));
  [...all]
    .filter((e) => e.at >= 0 && e.at < dur - 4)
    .sort((x, y) => PRIORITY[y.name] - PRIORITY[x.name] || x.at - y.at)
    .forEach((e) => {
      if ([...busy, ...out].every((t) => Math.abs(t.at - e.at) >= MIN_GAP)) out.push(e);
    });
  return out.sort((x, y) => x.at - y.at);
};

export const SceneSounds: React.FC<{ evs: Ev[]; dur: number; taken?: number[] }> = ({ evs, dur, taken }) => (
  <>
    {schedule(evs, dur, taken).map((e, i) => (
      <Sfx key={i} name={e.name} at={e.at} volume={e.volume} />
    ))}
  </>
);

/* ---------------- psql terminal ---------------- */

export type SqlBlock = {
  at: number;
  cmd: string;
  out: string;
  /** Frames per typed character. */
  speed?: number;
  /** "psql" (default) shows the database prompt; "sh" a shell prompt. */
  prompt?: "psql" | "sh";
};

export type Mark = { match: string; from: number; color?: string; exact?: boolean };

const TYPE_SPEED = 0.45;
export const typeEnd = (b: SqlBlock) => b.at + b.cmd.length * (b.speed ?? TYPE_SPEED);
/** Frame the first output line appears. */
export const outAt = (b: SqlBlock) => Math.ceil(typeEnd(b) + 6);
/** Frame the last output line appears. */
export const outDone = (b: SqlBlock) => outAt(b) + 20;
/** Frame the first output line containing `needle` appears. */
export const lineAt = (b: SqlBlock, needle: string) => {
  const lines = b.out.split("\n");
  const i = Math.max(0, lines.findIndex((l) => l.includes(needle)));
  return Math.ceil(outAt(b) + ((i + 1) / lines.length) * 20);
};

const lineColor = (l: string) => {
  if (/^(ERROR|psql: error)/.test(l)) return "#FF7A7A";
  if (/^(DETAIL|CONTEXT)/.test(l)) return "#F2B8B8";
  if (/^(BEGIN|ROLLBACK|UPDATE \d|INSERT \d|\(\d+ rows?\))$/.test(l)) return "#8C93C9";
  return "#DDE6F2";
};

export const SqlTerminal: React.FC<{
  blocks: SqlBlock[];
  x?: number;
  y?: number;
  w?: number;
  h?: number;
  fontSize?: number;
  lineH?: number;
  title?: string;
  marks?: Mark[];
  scrollAt?: [number, number, number];
}> = ({ blocks, x = VX, y = VY, w = VW, h = VH, fontSize = 18, lineH = 22, title = "psql — cufa_demo_pa (demo data)", marks = [], scrollAt }) => {
  const f = useCurrentFrame();
  const scroll = scrollAt ? interpolate(f, [scrollAt[0], scrollAt[1]], [0, scrollAt[2]], { ...clamp, easing: ease }) : 0;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        borderRadius: 12,
        background: tokens.nightInk,
        border: `2px solid ${tokens.nightInk}`,
        boxSizing: "border-box",
        overflow: "hidden",
      }}
    >
      <div style={{ height: 44, background: "#141a52", display: "flex", alignItems: "center", gap: 10, paddingLeft: 18 }}>
        {["#ff4b4b", "#ffc800", tokens.eagerGreen].map((c) => (
          <div key={c} style={{ width: 14, height: 14, borderRadius: 99, background: c }} />
        ))}
        <div style={{ marginLeft: 20, color: "#b8bce0", fontFamily: tokens.body, fontSize: 20, fontWeight: 700 }}>{title}</div>
      </div>
      <div
        style={{
          padding: "16px 26px",
          fontFamily: K.mono,
          fontSize,
          lineHeight: `${lineH}px`,
          color: "#DDE6F2",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          transform: `translateY(${-scroll}px)`,
        }}
      >
        {blocks.map((b, i) => (
          <Typing key={`t${i}`} from={b.at} count={Math.ceil((b.cmd.length * (b.speed ?? TYPE_SPEED)) / 3)} every={3} />
        ))}
        {blocks.map((b, i) => {
          if (f < b.at) return null;
          const sp = b.speed ?? TYPE_SPEED;
          const n = Math.max(0, Math.floor((f - b.at) / sp));
          const typed = b.cmd.slice(0, n);
          const done = n >= b.cmd.length;
          const lines = b.out.split("\n");
          const shown = done ? Math.floor(interpolate(f, [outAt(b), outAt(b) + 20], [0, lines.length], clamp)) : 0;
          const sh = b.prompt === "sh";
          const cmdLines = typed.split("\n");
          return (
            <div key={i} style={{ marginBottom: lineH * 0.6 }}>
              {cmdLines.map((cl, j) => (
                <div key={j}>
                  <span style={{ color: tokens.freshLeaf }}>{sh ? "$ " : j === 0 || /;\s*$/.test(cmdLines[j - 1]) ? "cufa_demo_pa=# " : "cufa_demo_pa-# "}</span>
                  <span>{cl}</span>
                  {!done && j === cmdLines.length - 1 ? <span style={{ background: "#DDE6F2" }}> </span> : null}
                </div>
              ))}
              {lines.slice(0, shown).map((l, j) => {
                const m = marks.find((mk) => (mk.exact ? l === mk.match : l.includes(mk.match)) && f >= mk.from);
                const mo = m ? fadeIn(f, m.from, 8) : 0;
                const col = m?.color ?? tokens.eagerGreen;
                return (
                  <div
                    key={`o${j}`}
                    style={{
                      color: lineColor(l),
                      background: m ? `rgba(${col === tokens.eagerGreen ? "88,204,2" : col === "#ff4b4b" ? "255,75,75" : "28,176,246"},${0.22 * mo})` : undefined,
                      boxShadow: m ? `inset 6px 0 0 rgba(${col === tokens.eagerGreen ? "88,204,2" : col === "#ff4b4b" ? "255,75,75" : "28,176,246"},${mo})` : undefined,
                      marginLeft: -26,
                      paddingLeft: 26,
                      minHeight: lineH,
                    }}
                  >
                    {l}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ---------------- real console screenshot (public/database/) ---------------- */

type Box = [number, number, number, number];
export type Hl = { b: Box; from: number; to?: number; label?: string; color?: string };
type Cam = { f: number; x: number; y: number; z: number };
export const cam = (f: number, x: number, y: number, z = 1): Cam => ({ f, x, y, z });

function track(keys: Cam[], f: number, pick: (k: Cam) => number): number {
  if (f <= keys[0].f) return pick(keys[0]);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (f <= b.f) return interpolate(f, [a.f, b.f], [pick(a), pick(b)], { ...clamp, easing: ease });
  }
  return pick(keys[keys.length - 1]);
}

export const Screen: React.FC<{ img: string; h?: number; cams: Cam[]; hl?: Hl[]; dur: number }> = ({ img, h = 1080, cams, hl = [], dur }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const BASE = VW / 1920;
  const z = track(cams, f, (k) => k.z);
  const s = BASE * z;
  const vw = VW / s;
  const vh = VH / s;
  let cx = track(cams, f, (k) => k.x);
  let cy = track(cams, f, (k) => k.y);
  cx = Math.min(Math.max(cx, vw / 2), 1920 - vw / 2);
  cy = h <= vh ? vh / 2 : Math.min(Math.max(cy, vh / 2), h - vh / 2);
  return (
    <div
      style={{
        position: "absolute",
        left: VX,
        top: VY,
        width: VW,
        height: VH,
        overflow: "hidden",
        borderRadius: 16,
        background: tokens.paper,
        border: `2px solid ${tokens.faded}`,
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          position: "absolute",
          width: 1920,
          height: h,
          transformOrigin: "0 0",
          transform: `translate(${VW / 2 - cx * s}px, ${VH / 2 - cy * s}px) scale(${s})`,
        }}
      >
        <Img src={staticFile(`database/${img}.png`)} style={{ width: 1920, height: h, display: "block" }} />
        {hl.map((x, i) => {
          const end = x.to ?? dur;
          const o = Math.min(fadeIn(f, x.from, 8), interpolate(f, [end - 8, end], [1, 0], clamp));
          if (o <= 0) return null;
          const pad = 10;
          const pop = spring({ frame: f - x.from, fps, config: { damping: 9, mass: 0.6 } });
          const col = x.color ?? tokens.sparkBlue;
          return (
            <div key={i}>
              <div
                style={{
                  position: "absolute",
                  left: x.b[0] - pad,
                  top: x.b[1] - pad,
                  width: x.b[2] + pad * 2,
                  height: x.b[3] + pad * 2,
                  border: `${4 / s}px solid ${col}`,
                  borderRadius: 12 / s,
                  background: `${col}14`,
                  opacity: o,
                  transform: `scale(${0.94 + 0.06 * pop + 0.012 * Math.sin((f - x.from) / 6)})`,
                }}
              />
              {x.label ? (
                <div
                  style={{
                    position: "absolute",
                    left: x.b[0] - pad,
                    top: x.b[1] - pad - 52 / s,
                    transformOrigin: "0 100%",
                    transform: `scale(${(0.6 + 0.4 * pop) / s})`,
                    background: col,
                    color: tokens.paper,
                    fontFamily: tokens.body,
                    fontSize: 22,
                    padding: "6px 16px",
                    border: `2px solid ${col}`,
                    borderRadius: 999,
                    whiteSpace: "nowrap",
                    opacity: o,
                    ...K.label,
                  }}
                >
                  {x.label}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ---------------- stickers ---------------- */

/** Flat sticker card: white, 2px border, 12px corners, a solid bottom edge. */
export const Sticker: React.FC<{
  x: number;
  y: number;
  w: number;
  h?: number;
  at: number;
  edge?: string;
  bg?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}> = ({ x, y, w, h, at, edge = tokens.faded, bg = tokens.paper, style, children }) => {
  const p = usePop(at);
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        boxSizing: "border-box",
        background: bg,
        border: `2px solid ${edge}`,
        borderBottomWidth: 6,
        borderRadius: 12,
        padding: "16px 22px",
        fontFamily: tokens.body,
        color: tokens.charcoal,
        opacity: Math.min(1, p * 1.5),
        transform: `translateY(${(1 - p) * 24}px) scale(${0.9 + 0.1 * p})`,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

/** Small solid label pill in a group colour. */
export const Tag: React.FC<{ color: string; children: React.ReactNode; size?: number; style?: React.CSSProperties }> = ({ color, children, size = 18, style }) => (
  <span
    style={{
      display: "inline-block",
      background: color,
      color: tokens.paper,
      border: `2px solid ${color}`,
      borderRadius: 999,
      padding: "3px 14px",
      fontFamily: tokens.body,
      fontSize: size,
      whiteSpace: "nowrap",
      ...K.label,
      ...style,
    }}
  >
    {children}
  </span>
);

/** Arrow drawn from a to b; the line grows from `at` over `len` frames. */
export const Arrow: React.FC<{
  a: [number, number];
  b: [number, number];
  at: number;
  len?: number;
  color?: string;
  dash?: boolean;
  dot?: boolean;
  width?: number;
}> = ({ a, b, at, len = 18, color = tokens.faded, dash, dot = true, width = 4 }) => {
  const f = useCurrentFrame();
  const t = interpolate(f, [at, at + len], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
  if (t <= 0) return null;
  const ex = a[0] + (b[0] - a[0]) * t;
  const ey = a[1] + (b[1] - a[1]) * t;
  const ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
  const hx = (d: number, s: number) => ex - Math.cos(ang + s) * d;
  const hy = (d: number, s: number) => ey - Math.sin(ang + s) * d;
  // a travelling dot once the arrow is drawn: data moving along it
  const cyc = ((f - at - len) % 45) / 45;
  const dx = a[0] + (b[0] - a[0]) * cyc;
  const dy = a[1] + (b[1] - a[1]) * cyc;
  return (
    <svg style={{ position: "absolute", left: 0, top: 0, width: 1920, height: 1080, overflow: "visible", pointerEvents: "none" }}>
      <line x1={a[0]} y1={a[1]} x2={ex} y2={ey} stroke={color} strokeWidth={width} strokeLinecap="round" strokeDasharray={dash ? "10 10" : undefined} />
      <polygon points={`${ex},${ey} ${hx(18, 0.45)},${hy(18, 0.45)} ${hx(18, -0.45)},${hy(18, -0.45)}`} fill={color} />
      {dot && t >= 1 ? <circle cx={dx} cy={dy} r={7} fill={color} opacity={0.9} /> : null}
    </svg>
  );
};

/** Scene frame: white paper and the same corner pill as the admin video. */
export const Frame: React.FC<{ pill?: string; children: React.ReactNode }> = ({ pill, children }) => (
  <AbsoluteFill style={{ background: theme.bg, fontFamily: tokens.body }}>
    {children}
    {pill ? (
      <div style={{ position: "absolute", right: 60, top: 58 }}>
        <Pill at={6} size={17} color={tokens.pencil}>
          {pill}
        </Pill>
      </div>
    ) : null}
  </AbsoluteFill>
);

/** Fades a block in over [from, from+8] and out over [to-8, to]. */
export const Show: React.FC<{ from: number; to?: number; children: React.ReactNode }> = ({ from, to = 1e9, children }) => {
  const f = useCurrentFrame();
  const o = Math.min(fadeIn(f, from, 8), interpolate(f, [to - 8, to], [1, 0], clamp));
  if (o <= 0) return null;
  return <AbsoluteFill style={{ opacity: o }}>{children}</AbsoluteFill>;
};

export const mono: React.CSSProperties = { fontFamily: K.mono };
