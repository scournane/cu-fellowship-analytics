import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { theme } from "../theme";
import { Pill, Rise, Scene, useAppear, useProgress } from "./ui";

const clamp = {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
} as const;

/* ---------------- 1. Title ---------------- */
export const TITLE_DUR = 150;
export const TitleScene: React.FC = () => {
  const bar = useProgress(20, 60);
  return (
    <Scene dur={TITLE_DUR}>
      <AbsoluteFill style={{ justifyContent: "center", padding: "0 160px" }}>
        <Rise delay={5}>
          <Pill>CIVIC INNOVATORS FELLOWSHIP</Pill>
        </Rise>
        <Rise delay={14}>
          <div style={{ fontSize: 132, fontWeight: 900, lineHeight: 1.02, marginTop: 40 }}>
            Checking in:
            <br />
            <span style={{ color: theme.accent }}>a fellow&apos;s guide</span>
          </div>
        </Rise>
        <div
          style={{
            height: 10,
            width: 520 * bar,
            backgroundColor: theme.accent2,
            borderRadius: 10,
            marginTop: 44,
          }}
        />
        <Rise delay={36}>
          <div style={{ fontSize: 48, color: theme.muted, marginTop: 36 }}>
            Everything you do in a live lesson, step by step.
          </div>
        </Rise>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------------- 2. Timeline ---------------- */
export const TIMELINE_DUR = 330;
export const TimelineScene: React.FC = () => {
  const frame = useCurrentFrame();
  const barW = 1600;
  const grow = useProgress(20, 60);
  const play = interpolate(frame, [70, 250], [0, 1], clamp);
  const aStart = 0.25;
  const aEnd = 0.42;
  const aIn = useAppear(110, 14);
  const bIn = useAppear(200, 14);
  return (
    <Scene dur={TIMELINE_DUR}>
      <AbsoluteFill style={{ padding: "90px 160px" }}>
        <Rise delay={0}>
          <div style={{ fontSize: 88, fontWeight: 900 }}>Two quick forms, every live lesson</div>
        </Rise>
        <Rise delay={10}>
          <div style={{ fontSize: 44, color: theme.muted, marginTop: 14 }}>
            They are separate. Answer both.
          </div>
        </Rise>

        {/* lesson bar */}
        <div style={{ position: "relative", marginTop: 150, width: barW, height: 180 }}>
          <div
            style={{
              position: "absolute",
              top: 70,
              left: 0,
              height: 40,
              width: barW * grow,
              backgroundColor: theme.surface,
              borderRadius: 40,
            }}
          />
          <div
            style={{
              position: "absolute",
              top: 70,
              left: 0,
              height: 40,
              width: barW * play,
              backgroundColor: "#2A4466",
              borderRadius: 40,
            }}
          />
          {/* Part A window */}
          <div
            style={{
              position: "absolute",
              top: 58,
              left: barW * aStart,
              width: barW * (aEnd - aStart),
              height: 64,
              borderRadius: 18,
              border: `5px dashed ${theme.accent}`,
              opacity: aIn,
              scale: `${0.8 + 0.2 * aIn}`,
            }}
          />
          <div
            style={{
              position: "absolute",
              top: -30,
              left: barW * aStart,
              width: barW * (aEnd - aStart),
              textAlign: "center",
              fontSize: 40,
              fontWeight: 800,
              color: theme.accent,
              opacity: aIn,
            }}
          >
            Part A
          </div>
          <div
            style={{
              position: "absolute",
              top: 140,
              left: barW * aStart - 80,
              width: barW * (aEnd - aStart) + 160,
              textAlign: "center",
              fontSize: 34,
              color: theme.text,
              opacity: aIn,
            }}
          >
            sometime 15–25 min in
          </div>
          {/* Part B marker */}
          <div
            style={{
              position: "absolute",
              top: 50,
              left: barW - 40,
              width: 80,
              height: 80,
              borderRadius: 80,
              backgroundColor: theme.accent2,
              scale: `${bIn}`,
            }}
          />
          <div
            style={{
              position: "absolute",
              top: -30,
              left: barW - 150,
              width: 300,
              textAlign: "center",
              fontSize: 40,
              fontWeight: 800,
              color: theme.accent2,
              opacity: bIn,
            }}
          >
            Part B
          </div>
          <div
            style={{
              position: "absolute",
              top: 140,
              left: barW - 150,
              width: 300,
              textAlign: "center",
              fontSize: 34,
              opacity: bIn,
            }}
          >
            at the end
          </div>
          {/* playhead */}
          <div
            style={{
              position: "absolute",
              top: 40,
              left: barW * play - 4,
              width: 8,
              height: 100,
              borderRadius: 8,
              backgroundColor: theme.text,
              opacity: grow,
            }}
          />
          <div style={{ position: "absolute", top: 140, left: 0, fontSize: 32, color: theme.muted, opacity: grow }}>
            Lesson starts
          </div>
        </div>

        <div style={{ display: "flex", gap: 60, marginTop: 70 }}>
          <Rise delay={140} style={{ flex: 1 }}>
            <div style={{ backgroundColor: theme.surface, borderRadius: 28, padding: "30px 40px", borderLeft: `12px solid ${theme.accent}` }}>
              <div style={{ fontSize: 48, fontWeight: 900 }}>Part A: &quot;I&apos;m here&quot;</div>
              <div style={{ fontSize: 38, color: theme.muted, marginTop: 8 }}>
                Mid-lesson. Type the passphrase.
              </div>
            </div>
          </Rise>
          <Rise delay={225} style={{ flex: 1 }}>
            <div style={{ backgroundColor: theme.surface, borderRadius: 28, padding: "30px 40px", borderLeft: `12px solid ${theme.accent2}` }}>
              <div style={{ fontSize: 48, fontWeight: 900 }}>Part B: &quot;What landed&quot;</div>
              <div style={{ fontSize: 38, color: theme.muted, marginTop: 8 }}>
                End of lesson. A quick reflection.
              </div>
            </div>
          </Rise>
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------------- 8. Closing ---------------- */
export const CLOSING_DUR = 150;
export const ClosingScene: React.FC = () => {
  const glow = useProgress(10, 60);
  return (
    <Scene dur={CLOSING_DUR}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", textAlign: "center" }}>
        <Rise delay={5}>
          <div style={{ fontSize: 120, fontWeight: 900 }}>
            Thanks for <span style={{ color: theme.accent }}>showing up.</span>
          </div>
        </Rise>
        <div style={{ height: 10, width: 700 * glow, backgroundColor: theme.accent2, borderRadius: 10, margin: "40px 0" }} />
        <Rise delay={30}>
          <div style={{ fontSize: 52, color: theme.muted }}>See you at the next live lesson.</div>
        </Rise>
        <Rise delay={50}>
          <div style={{ fontSize: 40, marginTop: 50, color: theme.text }}>Civics Unplugged · Civic Innovators Fellowship</div>
        </Rise>
      </AbsoluteFill>
    </Scene>
  );
};
