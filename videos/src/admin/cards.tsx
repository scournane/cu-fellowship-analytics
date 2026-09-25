import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { theme } from "../theme";
import { clamp, fadeIn, usePop } from "./ui";

const Bg: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <AbsoluteFill style={{ background: theme.bg, fontFamily: theme.font }}>{children}</AbsoluteFill>
);

/* ---------------- Title ---------------- */
export const TitleScene: React.FC = () => {
  const f = useCurrentFrame();
  const p = usePop(5);
  const o2 = fadeIn(f, 25, 15);
  const bar = interpolate(f, [10, 40], [0, 520], clamp);
  return (
    <Bg>
      <AbsoluteFill style={{ justifyContent: "center", paddingLeft: 160, color: theme.text }}>
        <div style={{ fontSize: 34, color: theme.accent2, fontWeight: 700, letterSpacing: 4, opacity: o2 }}>
          CIVICS UNPLUGGED · CIVIC INNOVATORS
        </div>
        <div
          style={{
            fontSize: 120,
            fontWeight: 800,
            letterSpacing: -3,
            lineHeight: 1.05,
            marginTop: 20,
            transform: `translateY(${(1 - p) * 40}px)`,
            opacity: p,
          }}
        >
          Running check-ins:
          <br />
          <span style={{ color: theme.accent }}>staff guide</span>
        </div>
        <div style={{ width: bar, height: 10, background: theme.accent, borderRadius: 5, marginTop: 36 }} />
        <div style={{ fontSize: 44, color: theme.muted, marginTop: 30, opacity: o2 }}>
          Every screen staff use, captured from the real console.
        </div>
      </AbsoluteFill>
    </Bg>
  );
};

export const ClosingScene: React.FC = () => {
  const f = useCurrentFrame();
  const p = usePop(5);
  return (
    <Bg>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", color: theme.text, textAlign: "center" }}>
        <div style={{ fontSize: 96, fontWeight: 800, transform: `scale(${0.9 + 0.1 * p})`, opacity: p }}>
          You’re ready to run check-ins.
        </div>
        <div style={{ fontSize: 40, color: theme.muted, marginTop: 30, opacity: fadeIn(f, 25, 15) }}>
          Full reference: <span style={{ color: theme.accent }}>docs/setup/console.md</span>
        </div>
        <div style={{ fontSize: 34, color: theme.accent2, marginTop: 50, fontWeight: 700, letterSpacing: 4, opacity: fadeIn(f, 40, 15) }}>
          CIVICS UNPLUGGED · CIVIC INNOVATORS
        </div>
      </AbsoluteFill>
    </Bg>
  );
};

const RULES = [
  "Human decisions always win — append-only and audited",
  "No link or QR code until the API confirms the form is published",
  "The passphrase is one signal among several, never proof",
  "Help requests never lower anyone’s participation",
  "AI sees answers as anonymous text and never judges a fellow",
];

export const RulesScene: React.FC = () => {
  const f = useCurrentFrame();
  return (
    <Bg>
      <AbsoluteFill style={{ padding: "110px 160px", color: theme.text }}>
        <div style={{ fontSize: 34, color: theme.accent2, fontWeight: 700, letterSpacing: 4, opacity: fadeIn(f, 0, 12) }}>
          DESIGN INVARIANTS
        </div>
        <div style={{ fontSize: 80, fontWeight: 800, marginTop: 10, opacity: fadeIn(f, 0, 12) }}>
          What the console will never do
        </div>
        <div style={{ marginTop: 60 }}>
          {RULES.map((t, i) => {
            const at = 20 + i * 28;
            const o = fadeIn(f, at, 10);
            return (
              <div
                key={t}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 30,
                  fontSize: 46,
                  marginBottom: 34,
                  opacity: o,
                  transform: `translateX(${interpolate(o, [0, 1], [-30, 0], clamp)}px)`,
                }}
              >
                <div style={{ width: 18, height: 18, borderRadius: 999, background: theme.accent }} />
                <span>{t}</span>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
    </Bg>
  );
};
