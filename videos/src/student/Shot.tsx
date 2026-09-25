import React from "react";
import { Img, staticFile } from "remotion";
import { tokens } from "../theme";
import { Pill } from "./ui";

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
        <div style={{ marginBottom: 14 }}>
          <Pill color={tokens.faded} textColor={tokens.pencil} style={{ fontSize: 24, borderBottomWidth: 2 }}>
            {label.replace(/^REAL (SCREEN|LOG) · /, (_m, k) => `Real ${String(k).toLowerCase()} · `)}
          </Pill>
        </div>
      ) : null}
      <div
        style={{
          width,
          height: crop.h * s,
          overflow: "hidden",
          borderRadius: 16,
          backgroundColor: "#fff",
          border: `2px solid ${tokens.faded}`,
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
