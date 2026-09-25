// Title, recap and closing cards: variants of src/admin/cards.tsx with this
// video's words. Same paper, same green headline, same Ding.
import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { tokens } from "../theme";
import { Ding, K, Pill, clamp, fadeIn, usePop } from "../admin/ui";
import { Sfx } from "../sound";

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
        <div style={{ ...display(100), marginTop: 30, maxWidth: 1180, transform: `translateY(${(1 - p) * 50}px) scale(${0.92 + 0.08 * p})`, transformOrigin: "0 50%", opacity: p }}>
          Where the fellowship’s data lives
        </div>
        <div style={{ fontFamily: tokens.body, fontWeight: 800, fontSize: 60, color: tokens.charcoal, marginTop: 14, opacity: fadeIn(f, 14, 12) }}>
          The database walkthrough
        </div>
        <div style={{ width: bar, height: 16, background: tokens.eagerGreen, borderRadius: 999, marginTop: 34, border: `2px solid ${K.greenEdge}` }} />
        <div style={{ fontSize: 38, fontWeight: 500, color: tokens.pencil, marginTop: 30, opacity: fadeIn(f, 28, 14), maxWidth: 1100 }}>
          What it holds, what it promises, and why it should run on Vercel Pro — shown with real queries on the demo data.
        </div>
      </AbsoluteFill>
      <Sfx name="pop" at={2} volume={0.8} />
      <Sfx name="ding" at={10} />
      <div style={{ position: "absolute", right: 90, top: 290 }}>
        <Ding size={420} start={10} />
      </div>
    </Paper>
  );
};

/* ---------------- Recap checklist ---------------- */
const RECAP = [
  "One Postgres database, and every row belongs to a cohort",
  "Check-ins are observations: written once, never edited",
  "Decisions are append-only, and a named human override wins",
  "Exit-ticket questions are versioned; answers resolve at read time",
  "Help requests feed no view, count, score or export",
  "Slack text is not stored; free text is counted, never graded",
];

const Check: React.FC<{ at: number }> = ({ at }) => {
  const p = usePop(at);
  return (
    <div
      style={{
        width: 54,
        height: 54,
        borderRadius: 12,
        background: tokens.eagerGreen,
        borderBottom: `4px solid ${K.greenEdge}`,
        color: tokens.paper,
        fontSize: 34,
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

export const RECAP_DUR = 270;
export const RecapScene: React.FC = () => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <Paper>
      <AbsoluteFill style={{ padding: "90px 150px" }}>
        <Pill at={0} size={26}>
          Recap
        </Pill>
        <div style={{ ...display(88), marginTop: 22, opacity: fadeIn(f, 2, 10) }}>What the database guarantees</div>
        <div style={{ marginTop: 40 }}>
          {RECAP.map((t, i) => {
            const at = 24 + i * 26;
            const sp = spring({ frame: f - at, fps, config: { damping: 12, mass: 0.6 } });
            return (
              <div
                key={t}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 26,
                  marginBottom: 16,
                  padding: "10px 22px",
                  width: 1300,
                  border: `2px solid ${tokens.hairline}`,
                  borderRadius: 12,
                  opacity: Math.min(1, sp * 1.4),
                  transform: `translateX(${(1 - sp) * -40}px)`,
                }}
              >
                <Check at={at + 4} />
                <Sfx name="pop" at={at + 4} volume={0.8} />
                <span style={{ fontSize: 34, fontWeight: 700, color: tokens.charcoal }}>{t}</span>
              </div>
            );
          })}
        </div>
      </AbsoluteFill>
      <Sfx name="whoosh" at={0} />
      <Sfx name="ding" at={12} volume={0.7} />
      <div style={{ position: "absolute", right: 70, bottom: 80 }}>
        <Ding size={280} start={8} />
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
      <Sfx name="ding" at={4} />
      <Sfx name="success" at={20} volume={0.6} />
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", textAlign: "center" }}>
        <div style={{ background: tokens.paper, borderRadius: 999, padding: 30, border: `2px solid ${K.greenEdge}`, borderBottomWidth: 8 }}>
          <Ding size={260} start={0} />
        </div>
        <div style={{ ...display(100, tokens.paper), marginTop: 40, transform: `scale(${0.85 + 0.15 * p})`, opacity: p }}>
          Now you know where the data lives.
        </div>
        <div style={{ fontSize: 36, fontWeight: 700, color: tokens.storybookGreen, marginTop: 26, opacity: fadeIn(f, 22, 12) }}>
          Full reference: docs/decisions.md · docs/safeguarding.md · docs/setup/local-dev.md
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
