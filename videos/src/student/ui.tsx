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

const clamp = {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
} as const;

/** 0→1 spring starting at `delay` frames. */
export const useAppear = (delay: number, damping = 200) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - delay, fps, config: { damping } });
};

/** Linear-ish 0→1 progress between two frames. */
export const useProgress = (from: number, to: number) => {
  const frame = useCurrentFrame();
  return interpolate(frame, [from, to], [0, 1], {
    ...clamp,
    easing: Easing.bezier(0.33, 1, 0.68, 1),
  });
};

/** Scene wrapper: background + fade in/out at the edges. */
export const Scene: React.FC<{
  dur: number;
  children: React.ReactNode;
  bg?: string;
}> = ({ dur, children, bg = theme.bg }) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill
      style={{
        backgroundColor: bg,
        fontFamily: theme.font,
        color: theme.text,
        opacity: interpolate(frame, [0, 12, dur - 12, dur], [0, 1, 1, 0], clamp),
      }}
    >
      {children}
    </AbsoluteFill>
  );
};

/** Element that slides up + fades in at `delay`. */
export const Rise: React.FC<{
  delay: number;
  children: React.ReactNode;
  style?: React.CSSProperties;
  distance?: number;
}> = ({ delay, children, style, distance = 40 }) => {
  const p = useAppear(delay);
  return (
    <div
      style={{
        opacity: p,
        translate: `0px ${(1 - p) * distance}px`,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

/** Typing text with a blinking caret. */
export const Typed: React.FC<{
  text: string;
  start: number;
  cps?: number; // chars per second
  showCaret?: boolean;
  caretUntil?: number;
  style?: React.CSSProperties;
}> = ({ text, start, cps = 18, showCaret = true, caretUntil = Infinity, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const n = Math.max(
    0,
    Math.min(text.length, Math.floor(((frame - start) / fps) * cps)),
  );
  const typing = frame >= start - 10 && n < text.length;
  const caretOn =
    showCaret &&
    frame >= start - 10 &&
    frame < caretUntil &&
    (typing || Math.floor(frame / 15) % 2 === 0);
  return (
    <span style={style}>
      {text.slice(0, n)}
      <span
        style={{
          display: "inline-block",
          width: 3,
          height: "1em",
          marginLeft: 2,
          verticalAlign: "-0.12em",
          backgroundColor: "currentColor",
          opacity: caretOn ? 1 : 0,
        }}
      />
    </span>
  );
};

/** Animated check mark drawn with SVG stroke. */
export const Check: React.FC<{
  start: number;
  size?: number;
  color?: string;
  circle?: boolean;
}> = ({ start, size = 120, color = theme.accent2, circle = true }) => {
  const draw = useProgress(start + 6, start + 22);
  const pop = useAppear(start, 12);
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: size,
        backgroundColor: circle ? color : "transparent",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        scale: `${pop}`,
        flexShrink: 0,
      }}
    >
      <svg width={size * 0.6} height={size * 0.6} viewBox="0 0 24 24">
        <path
          d="M4 12.5 L10 18 L20 6"
          fill="none"
          stroke={circle ? "#fff" : color}
          strokeWidth={3.2}
          strokeLinecap="round"
          strokeLinejoin="round"
          pathLength={1}
          strokeDasharray={1}
          strokeDashoffset={1 - draw}
        />
      </svg>
    </div>
  );
};

/** Small pill label. */
export const Pill: React.FC<{
  children: React.ReactNode;
  color?: string;
  textColor?: string;
  style?: React.CSSProperties;
}> = ({ children, color = theme.accent, textColor = theme.ink, style }) => (
  <span
    style={{
      display: "inline-block",
      backgroundColor: color,
      color: textColor,
      borderRadius: 999,
      padding: "8px 26px",
      fontWeight: 800,
      fontSize: 34,
      letterSpacing: 1,
      ...style,
    }}
  >
    {children}
  </span>
);

/** Step list on the left of the form scenes; highlights active step. */
export const Steps: React.FC<{
  steps: { at: number; title: string; tip?: string }[];
}> = ({ steps }) => {
  const frame = useCurrentFrame();
  const activeIdx = steps.reduce((acc, s, i) => (frame >= s.at ? i : acc), -1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      {steps.map((s, i) => {
        const active = i === activeIdx;
        const past = i < activeIdx;
        return (
          <Rise key={s.title} delay={s.at - 6} distance={24}>
            <div
              style={{
                display: "flex",
                gap: 22,
                alignItems: "flex-start",
                padding: "16px 22px",
                borderRadius: 22,
                backgroundColor: active ? theme.surface : "transparent",
                border: `3px solid ${active ? theme.accent : "transparent"}`,
                opacity: past ? 0.55 : 1,
              }}
            >
              <div
                style={{
                  width: 58,
                  height: 58,
                  borderRadius: 58,
                  flexShrink: 0,
                  backgroundColor: past ? theme.accent2 : theme.accent,
                  color: theme.ink,
                  fontWeight: 900,
                  fontSize: 32,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                {i + 1}
              </div>
              <div>
                <div style={{ fontSize: 42, fontWeight: 800, lineHeight: 1.15 }}>
                  {s.title}
                </div>
                {s.tip && active ? (
                  <div
                    style={{
                      fontSize: 32,
                      color: theme.muted,
                      marginTop: 6,
                      lineHeight: 1.25,
                    }}
                  >
                    {s.tip}
                  </div>
                ) : null}
              </div>
            </div>
          </Rise>
        );
      })}
    </div>
  );
};

/** Header used on the two form scenes. */
export const SceneHeader: React.FC<{ pill: string; title: string; pillColor?: string }> = ({
  pill,
  title,
  pillColor,
}) => (
  <Rise delay={0} style={{ display: "flex", alignItems: "center", gap: 30 }}>
    <Pill color={pillColor}>{pill}</Pill>
    <div style={{ fontSize: 64, fontWeight: 900 }}>{title}</div>
  </Rise>
);
