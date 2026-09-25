import React from "react";
import { Img, staticFile } from "remotion";
import { theme } from "../theme";

/** A real console screenshot (1920x1080 capture), cropped and slowly zoomed. */
export const Shot: React.FC<{
  file: string;
  crop: { x: number; y: number; w: number; h: number };
  width: number;
  dur?: number;
  label?: string;
  style?: React.CSSProperties;
}> = ({ file, crop, width, dur, label, style }) => {
  const s = width / crop.w;
  return (
    <div style={{ ...style }}>
      {label ? (
        <div style={{ fontSize: 28, color: theme.muted, marginBottom: 12, fontWeight: 700, letterSpacing: 1 }}>
          {label}
        </div>
      ) : null}
      <div
        style={{
          width,
          height: crop.h * s,
          overflow: "hidden",
          borderRadius: 20,
          backgroundColor: "#fff",
          boxShadow: "0 30px 80px rgba(0,0,0,0.5)",
          border: `4px solid ${theme.surface}`,
        }}
      >
        <div>
          <Img
            src={staticFile(`student/${file}`)}
            style={{
              width: 1920 * s,
              height: 1080 * s,
              marginLeft: -crop.x * s,
              marginTop: -crop.y * s,
              display: "block",
              maxWidth: "none",
            }}
          />
        </div>
      </div>
    </div>
  );
};
