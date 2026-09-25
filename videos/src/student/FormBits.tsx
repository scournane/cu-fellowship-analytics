import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { theme } from "../theme";

const clamp = {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
} as const;

export const FORM_PURPLE = "#673AB7";

/** Google-Form-ish white card. */
export const FormCard: React.FC<{
  title: string;
  subtitle: string;
  children: React.ReactNode;
  style?: React.CSSProperties;
}> = ({ title, subtitle, children, style }) => (
  <div
    style={{
      backgroundColor: theme.card,
      color: theme.ink,
      borderRadius: 24,
      overflow: "hidden",
      boxShadow: "0 30px 80px rgba(0,0,0,0.45)",
      ...style,
    }}
  >
    <div style={{ height: 14, backgroundColor: FORM_PURPLE }} />
    <div style={{ padding: "22px 40px 14px" }}>
      <div style={{ fontSize: 40, fontWeight: 800 }}>{title}</div>
      <div style={{ fontSize: 26, color: "#5F6B7A", marginTop: 4 }}>{subtitle}</div>
    </div>
    <div style={{ height: 2, backgroundColor: "#E3E7EE" }} />
    <div style={{ padding: "18px 40px 28px" }}>{children}</div>
  </div>
);

export const FieldLabel: React.FC<{ children: React.ReactNode; optional?: boolean }> = ({
  children,
  optional,
}) => (
  <div style={{ fontSize: 28, fontWeight: 700, marginBottom: 8 }}>
    {children}
    {optional ? (
      <span style={{ color: "#7A8594", fontWeight: 500 }}> (optional)</span>
    ) : (
      <span style={{ color: theme.danger }}> *</span>
    )}
  </div>
);

export const TextLine: React.FC<{
  children: React.ReactNode;
  focusAt: number;
  blurAt?: number;
}> = ({ children, focusAt, blurAt = Infinity }) => {
  const frame = useCurrentFrame();
  const focused = frame >= focusAt && frame < blurAt;
  return (
    <div
      style={{
        fontSize: 30,
        minHeight: 44,
        paddingBottom: 6,
        borderBottom: `${focused ? 4 : 2}px solid ${focused ? FORM_PURPLE : "#C5CCD6"}`,
        color: theme.ink,
      }}
    >
      {children}
    </div>
  );
};

/** Pointer + ripple that "clicks" at frame `at`, positioned inside a relative parent. */
export const ClickMark: React.FC<{ at: number; x: number; y: number }> = ({ at, x, y }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [at - 18, at - 8, at + 22, at + 34], [0, 1, 1, 0], clamp);
  const ripple = interpolate(frame, [at, at + 20], [0, 1], clamp);
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        opacity,
        pointerEvents: "none",
        translate: `${interpolate(frame, [at - 18, at], [40, 0], clamp)}px ${interpolate(frame, [at - 18, at], [40, 0], clamp)}px`,
      }}
    >
      <div
        style={{
          position: "absolute",
          left: -40,
          top: -40,
          width: 80,
          height: 80,
          borderRadius: 80,
          border: `4px solid ${theme.accent}`,
          scale: `${0.3 + ripple}`,
          opacity: frame >= at ? 1 - ripple : 0,
        }}
      />
      <svg width="44" height="56" viewBox="0 0 22 28" style={{ position: "absolute", left: -4, top: -2 }}>
        <path
          d="M2 2 L2 22 L7.5 17 L11 25.5 L14.5 24 L11 15.5 L18.5 15.5 Z"
          fill="#111"
          stroke="#fff"
          strokeWidth={1.6}
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
};
