import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { theme } from "../theme";

export const clamp = {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
} as const;

export const fadeIn = (f: number, start: number, dur = 12) =>
  interpolate(f, [start, start + dur], [0, 1], clamp);

export const usePop = (start: number) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: f - start, fps, config: { damping: 14, mass: 0.6 } });
};

export const typed = (text: string, f: number, start: number, perChar = 1.6) =>
  text.slice(0, Math.max(0, Math.floor((f - start) / perChar)));

// Geometry of the mock console shell (absolute frame coords).
export const SHELL = { x: 40, y: 40, w: 1840, h: 790 };
export const SIDEBAR_W = 360;
export const MX = SHELL.x + SIDEBAR_W; // main area origin
export const MY = SHELL.y;
export const MAIN_W = SHELL.w - SIDEBAR_W; // 1480
export const NAV = [
  "Connect Google",
  "Templates",
  "Sessions",
  "Rotation",
  "Shoutouts",
  "Review",
  "Help requests",
];
export const navPoint = (i: number): [number, number] => [220, 222 + 72 * i];
export const cohortPoint: [number, number] = [220, 760];
/** Convert main-area coords into absolute frame coords. */
export const M = (x: number, y: number): [number, number] => [MX + x, MY + y];

export const Shell: React.FC<{
  active: number;
  cohortOpen?: boolean;
  cohortLabel?: string;
  hideNav?: boolean;
  children: React.ReactNode;
}> = ({ active, cohortOpen, cohortLabel = "Fall 2026", hideNav, children }) => {
  return (
    <div
      style={{
        position: "absolute",
        left: SHELL.x,
        top: SHELL.y,
        width: SHELL.w,
        height: SHELL.h,
        borderRadius: 24,
        overflow: "hidden",
        display: "flex",
        boxShadow: "0 30px 80px rgba(0,0,0,0.45)",
        fontFamily: theme.font,
      }}
    >
      <div
        style={{
          width: SIDEBAR_W,
          background: theme.surface,
          color: theme.text,
          position: "relative",
        }}
      >
        <div style={{ padding: "34px 34px 0" }}>
          <div style={{ fontSize: 34, fontWeight: 800, color: theme.accent }}>
            Civic Innovators
          </div>
          <div style={{ fontSize: 26, color: theme.muted, marginTop: 4 }}>
            Session console
          </div>
        </div>
        {!hideNav &&
          NAV.map((label, i) => {
            const on = i === active;
            return (
              <div
                key={label}
                style={{
                  position: "absolute",
                  left: 18,
                  top: 222 - 32 + 72 * i - SHELL.y,
                  width: SIDEBAR_W - 36,
                  height: 64,
                  borderRadius: 12,
                  display: "flex",
                  alignItems: "center",
                  paddingLeft: 22,
                  fontSize: 30,
                  fontWeight: on ? 700 : 500,
                  background: on ? "rgba(242,182,50,0.16)" : "transparent",
                  color: on ? theme.accent : theme.text,
                  borderLeft: on ? `6px solid ${theme.accent}` : "6px solid transparent",
                }}
              >
                {label}
              </div>
            );
          })}
        {!hideNav && (
          <div
            style={{
              position: "absolute",
              left: 18,
              top: cohortPoint[1] - SHELL.y - 40,
              width: SIDEBAR_W - 36,
            }}
          >
            <div
              style={{
                height: 80,
                borderRadius: 12,
                border: `2px solid ${cohortOpen ? theme.accent : "rgba(255,255,255,0.25)"}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "0 20px",
                fontSize: 28,
              }}
            >
              <span>
                <span style={{ color: theme.muted }}>Cohort: </span>
                {cohortLabel}
              </span>
              <span style={{ color: theme.muted }}>▾</span>
            </div>
          </div>
        )}
      </div>
      <div
        style={{
          flex: 1,
          background: "#F5F7FB",
          color: theme.ink,
          position: "relative",
        }}
      >
        {children}
      </div>
    </div>
  );
};

export const PageTitle: React.FC<{ title: string; sub?: string; right?: React.ReactNode }> = ({
  title,
  sub,
  right,
}) => (
  <div style={{ position: "absolute", left: 60, top: 36, right: 60 }}>
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      <div style={{ fontSize: 60, fontWeight: 800, letterSpacing: -1 }}>{title}</div>
      {right}
    </div>
    {sub ? <div style={{ fontSize: 30, color: "#5A6B82", marginTop: 4 }}>{sub}</div> : null}
  </div>
);

export const Abs: React.FC<{
  x: number;
  y: number;
  w?: number;
  h?: number;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}> = ({ x, y, w, h, style, children }) => (
  <div style={{ position: "absolute", left: x, top: y, width: w, height: h, ...style }}>
    {children}
  </div>
);

export const Btn: React.FC<{
  label: string;
  x: number;
  y: number;
  w: number;
  h?: number;
  primary?: boolean;
  pressedAt?: number;
  size?: number;
}> = ({ label, x, y, w, h = 80, primary, pressedAt, size = 32 }) => {
  const f = useCurrentFrame();
  const press =
    pressedAt !== undefined
      ? interpolate(f, [pressedAt, pressedAt + 4, pressedAt + 10], [1, 0.94, 1], clamp)
      : 1;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        borderRadius: 14,
        background: primary ? theme.ink : "#FFFFFF",
        color: primary ? "#FFFFFF" : theme.ink,
        border: primary ? "none" : "2px solid #C9D2DF",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: size,
        fontWeight: 700,
        transform: `scale(${press})`,
      }}
    >
      {label}
    </div>
  );
};

export const Badge: React.FC<{ label: string; tone: "ok" | "warn" | "bad" | "info" | "muted"; size?: number }> = ({
  label,
  tone,
  size = 28,
}) => {
  const colors = {
    ok: ["#DDF3EF", "#1E7F73"],
    warn: ["#FDF1D6", "#8A6300"],
    bad: ["#FBE2E1", "#B23A38"],
    info: ["#E3ECFA", "#2C5AA0"],
    muted: ["#EBEEF3", "#5A6B82"],
  }[tone];
  return (
    <span
      style={{
        display: "inline-block",
        background: colors[0],
        color: colors[1],
        borderRadius: 999,
        padding: "6px 20px",
        fontSize: size,
        fontWeight: 700,
        whiteSpace: "nowrap",
      }}
    >
      {tone === "ok" ? "✓ " : ""}
      {label}
    </span>
  );
};

export const Card: React.FC<{
  x: number;
  y: number;
  w: number;
  h?: number;
  style?: React.CSSProperties;
  children?: React.ReactNode;
}> = ({ x, y, w, h, style, children }) => (
  <div
    style={{
      position: "absolute",
      left: x,
      top: y,
      width: w,
      height: h,
      background: "#FFFFFF",
      borderRadius: 18,
      border: "2px solid #E1E6EE",
      padding: 32,
      boxSizing: "border-box",
      ...style,
    }}
  >
    {children}
  </div>
);

export const Field: React.FC<{
  label: string;
  value: string;
  x: number;
  y: number;
  w: number;
  focus?: boolean;
}> = ({ label, value, x, y, w, focus }) => (
  <div style={{ position: "absolute", left: x, top: y, width: w }}>
    <div style={{ fontSize: 26, fontWeight: 600, color: "#5A6B82", marginBottom: 8 }}>{label}</div>
    <div
      style={{
        height: 72,
        borderRadius: 12,
        background: "#FFFFFF",
        border: `2px solid ${focus ? theme.accent2 : "#C9D2DF"}`,
        display: "flex",
        alignItems: "center",
        padding: "0 22px",
        fontSize: 32,
        fontWeight: 500,
        overflow: "hidden",
        whiteSpace: "nowrap",
      }}
    >
      {value}
    </div>
  </div>
);

export type CursorKey = { f: number; x: number; y: number };

export const Cursor: React.FC<{ keys: CursorKey[]; clicks?: number[] }> = ({ keys, clicks = [] }) => {
  const f = useCurrentFrame();
  let x = keys[0].x;
  let y = keys[0].y;
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i];
    const b = keys[i + 1];
    if (f >= a.f && f <= b.f) {
      const t = interpolate(f, [a.f, b.f], [0, 1], {
        ...clamp,
        easing: Easing.inOut(Easing.cubic),
      });
      x = a.x + (b.x - a.x) * t;
      y = a.y + (b.y - a.y) * t;
    } else if (f > b.f) {
      x = b.x;
      y = b.y;
    }
  }
  const opacity = fadeIn(f, keys[0].f, 8);
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {clicks.map((c) => {
        const p = interpolate(f, [c, c + 16], [0, 1], clamp);
        if (f < c || p >= 1) return null;
        return (
          <div
            key={c}
            style={{
              position: "absolute",
              left: x - 40,
              top: y - 40,
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
        style={{ position: "absolute", left: x - 6, top: y - 4, opacity, filter: "drop-shadow(0 4px 6px rgba(0,0,0,0.4))" }}
      >
        <path d="M4 2 L4 20 L9 15 L12.5 22 L15.5 20.5 L12 13.8 L19 13.8 Z" fill="#FFFFFF" stroke="#0E1A2B" strokeWidth={1.4} />
      </svg>
    </AbsoluteFill>
  );
};

export type CaptionLine = { from: number; text: string };

export const Caption: React.FC<{ step: string; title: string; lines: CaptionLine[] }> = ({
  step,
  title,
  lines,
}) => {
  const f = useCurrentFrame();
  const idx = lines.reduce((acc, l, i) => (f >= l.from ? i : acc), 0);
  const cur = lines[idx];
  const o = fadeIn(f, cur.from, 10);
  const shift = interpolate(o, [0, 1], [16, 0]);
  return (
    <div
      style={{
        position: "absolute",
        left: 40,
        top: 856,
        width: 1840,
        height: 190,
        fontFamily: theme.font,
        color: theme.text,
        display: "flex",
        gap: 36,
        alignItems: "flex-start",
      }}
    >
      <div
        style={{
          minWidth: 170,
          height: 170,
          borderRadius: 20,
          background: theme.accent,
          color: theme.ink,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          fontWeight: 800,
        }}
      >
        <div style={{ fontSize: 24, letterSpacing: 2 }}>STEP</div>
        <div style={{ fontSize: 76, lineHeight: 1 }}>{step}</div>
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 44, fontWeight: 800, color: theme.accent, marginBottom: 8 }}>{title}</div>
        <div
          style={{
            fontSize: 38,
            lineHeight: 1.3,
            opacity: o,
            transform: `translateY(${shift}px)`,
          }}
        >
          {cur.text}
        </div>
      </div>
    </div>
  );
};

export const Check: React.FC<{ at: number; label: string; size?: number }> = ({ at, label, size = 32 }) => {
  const f = useCurrentFrame();
  const p = usePop(at);
  const done = f >= at;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 18, fontSize: size, marginBottom: 14 }}>
      <div
        style={{
          width: 46,
          height: 46,
          borderRadius: 999,
          background: done ? theme.accent2 : "#E1E6EE",
          color: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 30,
          fontWeight: 800,
          transform: `scale(${done ? 0.6 + 0.4 * p : 1})`,
        }}
      >
        {done ? "✓" : ""}
      </div>
      <span style={{ color: done ? theme.ink : "#8A97AA", fontWeight: done ? 600 : 500 }}>{label}</span>
    </div>
  );
};
