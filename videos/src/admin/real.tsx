// Real screenshots of the running console (captured by scripts/capture-admin.mjs),
// driven by a camera (pan/zoom), highlight rectangles and an animated cursor.
import React from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  Series,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { theme } from "../theme";
import { Caption, CaptionLine, clamp, fadeIn } from "./ui";

// Viewport the screenshot is shown in (absolute frame coords).
const VX = 40;
const VY = 40;
const VW = 1840;
const VH = 790;
const BASE = VW / 1920;

type Box = [number, number, number, number];
type Cam = { f: number; x: number; y: number; z: number };
type Hl = { b: Box; from: number; to?: number; label?: string; color?: string };
type Pt = { f: number; x: number; y: number };

export type Beat = {
  img: string;
  h: number;
  dur: number;
  cam: Cam[];
  hl?: Hl[];
  cur?: Pt[];
  clicks?: number[];
};

const ease = Easing.inOut(Easing.cubic);

function track<T extends { f: number }>(keys: T[], f: number, pick: (k: T) => number): number {
  if (f <= keys[0].f) return pick(keys[0]);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (f <= b.f) {
      return interpolate(f, [a.f, b.f], [pick(a), pick(b)], { ...clamp, easing: ease });
    }
  }
  return pick(keys[keys.length - 1]);
}

const Shot: React.FC<{ beat: Beat }> = ({ beat }) => {
  const f = useCurrentFrame();
  const z = track(beat.cam, f, (k) => k.z);
  const s = BASE * z;
  const vw = VW / s;
  const vh = VH / s;
  let cx = track(beat.cam, f, (k) => k.x);
  let cy = track(beat.cam, f, (k) => k.y);
  cx = Math.min(Math.max(cx, vw / 2), 1920 - vw / 2);
  cy = beat.h <= vh ? vh / 2 : Math.min(Math.max(cy, vh / 2), beat.h - vh / 2);
  const tx = VW / 2 - cx * s;
  const ty = VH / 2 - cy * s;

  let px = 0;
  let py = 0;
  if (beat.cur && beat.cur.length) {
    px = track(beat.cur, f, (k) => k.x);
    py = track(beat.cur, f, (k) => k.y);
  }

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          left: VX,
          top: VY,
          width: VW,
          height: VH,
          overflow: "hidden",
          borderRadius: 18,
          background: "#fff",
          boxShadow: "0 30px 80px rgba(0,0,0,0.45)",
        }}
      >
        <div
          style={{
            position: "absolute",
            left: 0,
            top: 0,
            width: 1920,
            height: beat.h,
            transformOrigin: "0 0",
            transform: `translate(${tx}px, ${ty}px) scale(${s})`,
          }}
        >
          <Img src={staticFile(`admin/${beat.img}.png`)} style={{ width: 1920, height: beat.h, display: "block" }} />
          {(beat.hl || []).map((h, i) => {
            const end = h.to ?? beat.dur;
            const o = Math.min(fadeIn(f, h.from, 8), interpolate(f, [end - 8, end], [1, 0], clamp));
            if (o <= 0) return null;
            const pad = 10;
            const pulse = 1 + 0.03 * Math.sin((f - h.from) / 5);
            const col = h.color ?? theme.accent;
            return (
              <div key={i}>
                <div
                  style={{
                    position: "absolute",
                    left: h.b[0] - pad,
                    top: h.b[1] - pad,
                    width: h.b[2] + pad * 2,
                    height: h.b[3] + pad * 2,
                    border: `${4 / s}px solid ${col}`,
                    borderRadius: 10,
                    boxShadow: `0 0 0 ${2000}px rgba(14,26,43,${0.28 * o})`,
                    opacity: o,
                    transform: `scale(${pulse})`,
                  }}
                />
                {h.label ? (
                  <div
                    style={{
                      position: "absolute",
                      left: h.b[0] - pad,
                      top: h.b[1] - pad - 44 / s,
                      transformOrigin: "0 0",
                      transform: `scale(${1 / s})`,
                      background: col,
                      color: theme.ink,
                      fontFamily: theme.font,
                      fontWeight: 800,
                      fontSize: 24,
                      padding: "4px 12px",
                      borderRadius: 8,
                      whiteSpace: "nowrap",
                      opacity: o,
                    }}
                  >
                    {h.label}
                  </div>
                ) : null}
              </div>
            );
          })}
          {beat.cur && beat.cur.length ? (
            <div
              style={{
                position: "absolute",
                left: px,
                top: py,
                transformOrigin: "0 0",
                transform: `scale(${1 / s})`,
                opacity: fadeIn(f, beat.cur[0].f, 8),
              }}
            >
              {(beat.clicks || []).map((c) => {
                const p = interpolate(f, [c, c + 16], [0, 1], clamp);
                if (f < c || p >= 1) return null;
                return (
                  <div
                    key={c}
                    style={{
                      position: "absolute",
                      left: -40,
                      top: -40,
                      width: 80,
                      height: 80,
                      borderRadius: 999,
                      border: `5px solid ${theme.accent}`,
                      transform: `scale(${0.3 + p})`,
                      opacity: 1 - p,
                    }}
                  />
                );
              })}
              <svg
                width={48}
                height={48}
                viewBox="0 0 24 24"
                style={{ position: "absolute", left: -6, top: -4, filter: "drop-shadow(0 4px 6px rgba(0,0,0,0.4))" }}
              >
                <path d="M4 2 L4 20 L9 15 L12.5 22 L15.5 20.5 L12 13.8 L19 13.8 Z" fill="#FFFFFF" stroke="#0E1A2B" strokeWidth={1.4} />
              </svg>
            </div>
          ) : null}
        </div>
      </div>
    </AbsoluteFill>
  );
};

export type SceneDef = { step: string; title: string; beats: Beat[]; lines: CaptionLine[] };

export const sceneLength = (s: SceneDef) => s.beats.reduce((a, b) => a + b.dur, 0);

export const RealScene: React.FC<{ def: SceneDef }> = ({ def }) => (
  <AbsoluteFill style={{ background: theme.bg }}>
    <Series>
      {def.beats.map((b, i) => (
        <Series.Sequence key={i} durationInFrames={b.dur}>
          <Shot beat={b} />
        </Series.Sequence>
      ))}
    </Series>
    <Caption step={def.step} title={def.title} lines={def.lines} />
    <div
      style={{
        position: "absolute",
        right: 60,
        top: 56,
        fontFamily: theme.font,
        fontSize: 18,
        fontWeight: 700,
        color: theme.text,
        background: "rgba(14,26,43,0.82)",
        padding: "6px 14px",
        borderRadius: 8,
        letterSpacing: 1,
      }}
    >
      REAL SCREEN · demo data · fake-Google mode
    </div>
  </AbsoluteFill>
);

/* ---------------- terminal (real `cufa report` output) ---------------- */

export type TermBlock = { at: number; cmd: string; out: string };

export const Terminal: React.FC<{ blocks: TermBlock[]; scrollAt?: [number, number, number] }> = ({ blocks, scrollAt }) => {
  const f = useCurrentFrame();
  const lineH = 22;
  const scroll = scrollAt ? interpolate(f, [scrollAt[0], scrollAt[1]], [0, scrollAt[2]], { ...clamp, easing: ease }) : 0;
  return (
    <div
      style={{
        position: "absolute",
        left: VX,
        top: VY,
        width: VW,
        height: VH,
        borderRadius: 18,
        background: "#0A1220",
        border: "2px solid #22324A",
        overflow: "hidden",
        boxShadow: "0 30px 80px rgba(0,0,0,0.45)",
      }}
    >
      <div style={{ height: 44, background: "#16263D", display: "flex", alignItems: "center", gap: 10, paddingLeft: 18 }}>
        {["#E4605E", "#F2B632", "#3FB6A8"].map((c) => (
          <div key={c} style={{ width: 14, height: 14, borderRadius: 99, background: c }} />
        ))}
        <div style={{ marginLeft: 20, color: theme.muted, fontFamily: theme.font, fontSize: 20 }}>cu-fellowship-analytics — bash</div>
      </div>
      <div
        style={{
          padding: "18px 28px",
          fontFamily: "'DejaVu Sans Mono', Menlo, monospace",
          fontSize: 18,
          lineHeight: `${lineH}px`,
          color: "#DDE6F2",
          whiteSpace: "pre",
          transform: `translateY(${-scroll}px)`,
        }}
      >
        {blocks.map((b, i) => {
          if (f < b.at) return null;
          const typed = b.cmd.slice(0, Math.max(0, Math.floor((f - b.at) / 1.2)));
          const doneTyping = typed.length >= b.cmd.length;
          const outStart = b.at + b.cmd.length * 1.2 + 6;
          const lines = b.out.split("\n");
          const shown = doneTyping ? Math.floor(interpolate(f, [outStart, outStart + 20], [0, lines.length], clamp)) : 0;
          return (
            <div key={i}>
              <span style={{ color: theme.accent2 }}>$ </span>
              <span>{typed}</span>
              {!doneTyping ? <span style={{ background: "#DDE6F2" }}> </span> : null}
              {"\n"}
              {lines.slice(0, shown).join("\n")}
              {shown > 0 ? "\n\n" : ""}
            </div>
          );
        })}
      </div>
    </div>
  );
};
