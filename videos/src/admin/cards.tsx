import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { tokens } from "../theme";
import { Ding, K, Pill, clamp, fadeIn, usePop } from "./ui";

const Paper: React.FC<{ children: React.ReactNode; bg?: string }> = ({ children, bg = tokens.paper }) => (
  <AbsoluteFill style={{ background: bg, fontFamily: tokens.body }}>{children}</AbsoluteFill>
);

const display = (size: number, color: string = tokens.eagerGreen): React.CSSProperties => ({
  fontFamily: tokens.display,
  fontWeight: 900,
  fontSize: size,
  letterSpacing: "-0.02em",
  lineHeight: 1.02,
  color,
});

/* ---------------- Title ---------------- */
export const TitleScene: React.FC = () => {
  const f = useCurrentFrame();
  const p = usePop(6);
  const bar = interpolate(f, [18, 50], [0, 560], { ...clamp });
  return (
    <Paper>
      <AbsoluteFill style={{ justifyContent: "center", paddingLeft: 150 }}>
        <Pill at={2} size={26}>
          Civics Unplugged · Civic Innovators
        </Pill>
        <div style={{ ...display(116), marginTop: 30, transform: `translateY(${(1 - p) * 50}px) scale(${0.92 + 0.08 * p})`, transformOrigin: "0 50%", opacity: p }}>
          Running check-ins
        </div>
        <div style={{ fontFamily: tokens.body, fontWeight: 800, fontSize: 68, color: tokens.charcoal, marginTop: 10, opacity: fadeIn(f, 14, 12) }}>
          The staff guide
        </div>
        <div style={{ width: bar, height: 16, background: tokens.eagerGreen, borderRadius: 999, marginTop: 34, border: `2px solid ${K.greenEdge}` }} />
        <div style={{ fontSize: 40, fontWeight: 500, color: tokens.pencil, marginTop: 30, opacity: fadeIn(f, 28, 14), maxWidth: 1000 }}>
          Every screen and Slack command staff use — captured from the real console and bot.
        </div>
      </AbsoluteFill>
      <div style={{ position: "absolute", right: 90, top: 300 }}>
        <Ding size={460} start={10} />
      </div>
    </Paper>
  );
};

/* ---------------- Invariants ---------------- */
const RULES = [
  "Human decisions always win — append-only and audited",
  "No link or QR code until the API confirms the form is published",
  "The passphrase is one signal among several, never proof",
  "Help requests never lower anyone’s participation",
  "AI sees answers as anonymous text and never judges a fellow",
];

const Check: React.FC<{ at: number }> = ({ at }) => {
  const p = usePop(at);
  return (
    <div
      style={{
        width: 58,
        height: 58,
        borderRadius: 12,
        background: tokens.eagerGreen,
        borderBottom: `4px solid ${K.greenEdge}`,
        color: tokens.paper,
        fontSize: 36,
        fontWeight: 900,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
        transform: `scale(${p}) rotate(${(1 - p) * -20}deg)`,
      }}
    >
      ✓
    </div>
  );
};

export const RulesScene: React.FC = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <Paper>
      <AbsoluteFill style={{ padding: "100px 150px" }}>
        <Pill at={0} size={26}>
          Design invariants
        </Pill>
        <div style={{ ...display(96), marginTop: 22, opacity: fadeIn(f, 2, 10) }}>What the console will never do</div>
        <div style={{ marginTop: 56 }}>
          {RULES.map((t, i) => {
            const at = 20 + i * 28;
            const sp = spring({ frame: f - at, fps, config: { damping: 12, mass: 0.6 } });
            return (
              <div
                key={t}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 28,
                  marginBottom: 26,
                  padding: "14px 24px",
                  width: 1180,
                  border: `2px solid ${tokens.hairline}`,
                  borderRadius: 12,
                  opacity: Math.min(1, sp * 1.4),
                  transform: `translateX(${(1 - sp) * -40}px)`,
                }}
              >
                <Check at={at + 4} />
                <span style={{ fontSize: 40, fontWeight: 700, color: tokens.charcoal }}>{t}</span>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
      <div style={{ position: "absolute", right: 110, bottom: 110 }}>
        <Ding size={380} start={8} />
      </div>
    </Paper>
  );
};

/* ---------------- Closing: full-bleed green band ---------------- */
export const ClosingScene: React.FC = () => {
  const f = useCurrentFrame();
  const p = usePop(5);
  return (
    <Paper bg={tokens.eagerGreen}>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", textAlign: "center" }}>
        <div style={{ background: tokens.paper, borderRadius: 999, padding: 30, border: `2px solid ${K.greenEdge}`, borderBottomWidth: 8 }}>
          <Ding size={260} start={0} />
        </div>
        <div style={{ ...display(110, tokens.paper), marginTop: 40, transform: `scale(${0.85 + 0.15 * p})`, opacity: p }}>
          You’re ready to run check-ins.
        </div>
        <div style={{ fontSize: 40, fontWeight: 700, color: tokens.storybookGreen, marginTop: 26, opacity: fadeIn(f, 22, 12) }}>
          Full reference: docs/setup/console.md · docs/setup/slack-bot.md
        </div>
        <div
          style={{
            marginTop: 40,
            fontSize: 26,
            color: tokens.paper,
            border: `2px solid ${tokens.freshLeaf}`,
            borderRadius: 999,
            padding: "8px 22px",
            opacity: fadeIn(f, 36, 12),
            ...K.label,
          }}
        >
          Civics Unplugged · Civic Innovators
        </div>
      </AbsoluteFill>
    </Paper>
  );
};
