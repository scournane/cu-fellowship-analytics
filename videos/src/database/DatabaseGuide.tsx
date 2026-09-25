// The database walkthrough for CU staff: what the database holds, what it
// guarantees (with real queries on the demo data), and why Vercel Pro.
// Wired like src/admin/AdminGuide.tsx: one music bed, scenes in a Series with a
// short fade at each cut, fonts held until loaded.
import React from "react";
import "@fontsource/nunito/800.css";
import "@fontsource/nunito/900.css";
import "@fontsource/nunito-sans/500.css";
import "@fontsource/nunito-sans/700.css";
import "@fontsource/nunito-sans/800.css";
import { AbsoluteFill, Series, continueRender, delayRender, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { theme } from "../theme";
import { Music } from "../sound";
import { ClosingScene, RECAP_DUR, RecapScene, TitleScene } from "./cards";
import {
  BIG_DUR,
  BigPictureScene,
  EXIT_DUR,
  ExitTicketScene,
  OBS_DUR,
  ObservedScene,
  PRIV_DUR,
  PrivacyScene,
  STAFF_DUR,
  StaffScene,
  TABLES_DUR,
  TablesScene,
} from "./scenes";
import { VERCEL_DUR, VercelScene } from "./vercel";

const SCENES: [React.FC, number][] = [
  [TitleScene, 140],
  [BigPictureScene, BIG_DUR],
  [TablesScene, TABLES_DUR],
  [ObservedScene, OBS_DUR],
  [ExitTicketScene, EXIT_DUR],
  [PrivacyScene, PRIV_DUR],
  [StaffScene, STAFF_DUR],
  [VercelScene, VERCEL_DUR],
  [RecapScene, RECAP_DUR],
  [ClosingScene, 140],
];

export const DATABASE_DURATION = SCENES.reduce((a, [, d]) => a + d, 0);

/** Short fade in/out around each scene so cuts are not abrupt. */
const Fade: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const f = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const o = interpolate(f, [0, 10, durationInFrames - 10, durationInFrames], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return <AbsoluteFill style={{ opacity: o }}>{children}</AbsoluteFill>;
};

/**
 * Register the Nunito faces from public/admin/fonts with the FontFace API and hold
 * rendering until they load, so no frame falls back to a system font.
 */
const FACES: [string, string, string][] = [
  ["Nunito", "800", "nunito-latin-800-normal.woff2"],
  ["Nunito", "900", "nunito-latin-900-normal.woff2"],
  ["Nunito Sans", "500", "nunito-sans-latin-500-normal.woff2"],
  ["Nunito Sans", "700", "nunito-sans-latin-700-normal.woff2"],
  ["Nunito Sans", "800", "nunito-sans-latin-800-normal.woff2"],
];
const useFonts = () => {
  const [handle] = React.useState(() => delayRender("Loading Nunito fonts"));
  React.useEffect(() => {
    Promise.all(
      FACES.map(([family, weight, file]) => {
        const face = new FontFace(family, `url(${staticFile(`admin/fonts/${file}`)}) format("woff2")`, { weight });
        document.fonts.add(face);
        return face.load();
      }),
    )
      .then(() => continueRender(handle))
      .catch((e) => {
        console.error(e);
        continueRender(handle);
      });
  }, [handle]);
};

export const DatabaseGuide: React.FC = () => {
  useFonts();
  return (
    <AbsoluteFill style={{ background: theme.bg }}>
      <Music />
      <Series>
        {SCENES.map(([Scene, d], i) => (
          <Series.Sequence key={i} durationInFrames={d}>
            <Fade>
              <Scene />
            </Fade>
          </Series.Sequence>
        ))}
      </Series>
    </AbsoluteFill>
  );
};
