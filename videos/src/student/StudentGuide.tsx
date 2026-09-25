// Imported here directly: package.json marks only *.css as side-effectful, so
// webpack drops src/fonts.ts (side-effect-only .ts) and its font CSS with it.
import "@fontsource/nunito/800.css";
import "@fontsource/nunito/900.css";
import "@fontsource/nunito-sans/500.css";
import "@fontsource/nunito-sans/700.css";
import "@fontsource/nunito-sans/800.css";
import React, { useEffect, useState } from "react";
import { AbsoluteFill, continueRender, delayRender, Series } from "remotion";
import { theme } from "../theme";
import { CLOSING_DUR, ClosingScene, TIMELINE_DUR, TimelineScene, TITLE_DUR, TitleScene } from "./Scenes";
import {
  EMAIL_DUR, EmailScene, FAQ_DUR, FaqScene, FORM_A_DUR, FORM_B_DUR, FormAScene, FormBScene, HELP2_DUR, Help2Scene,
  LINK_DUR, LinkScene, MATCH_DUR, MatchScene, PHRASE_DUR, PhraseScene, PRIVACY_DUR, PrivacyScene, RECAP2_DUR,
  Recap2Scene, ROTATION_DUR, RotationScene, WINDOW_DUR, WindowScene,
} from "./Guide";
import {
  BADGE_DUR, BadgesScene, CHECKIN_DUR, CheckinScene, CMDS_DUR, CommandsScene, DASH_DUR, DashboardScene, POLL_DUR,
  PollScene, QA_DUR, QAScene, REMIND_DUR, RemindScene, SPRIV_DUR, SlackPrivacyScene, WELCOME_DUR, WelcomeScene,
} from "./SlackScenes";

const SCENES: [React.FC, number][] = [
  [TitleScene, TITLE_DUR],
  [TimelineScene, TIMELINE_DUR],
  [WelcomeScene, WELCOME_DUR],
  [RemindScene, REMIND_DUR],
  [LinkScene, LINK_DUR],
  [PhraseScene, PHRASE_DUR],
  [FormAScene, FORM_A_DUR],
  [MatchScene, MATCH_DUR],
  [WindowScene, WINDOW_DUR],
  [EmailScene, EMAIL_DUR],
  [FormBScene, FORM_B_DUR],
  [RotationScene, ROTATION_DUR],
  [Help2Scene, HELP2_DUR],
  [CommandsScene, CMDS_DUR],
  [DashboardScene, DASH_DUR],
  [BadgesScene, BADGE_DUR],
  [CheckinScene, CHECKIN_DUR],
  [QAScene, QA_DUR],
  [PollScene, POLL_DUR],
  [PrivacyScene, PRIVACY_DUR],
  [SlackPrivacyScene, SPRIV_DUR],
  [FaqScene, FAQ_DUR],
  [Recap2Scene, RECAP2_DUR],
  [ClosingScene, CLOSING_DUR],
];

export const STUDENT_DURATION = SCENES.reduce((sum, [, d]) => sum + d, 0);

const FONT_FACES = [
  "800 40px Nunito",
  "900 40px Nunito",
  "500 40px 'Nunito Sans'",
  "700 40px 'Nunito Sans'",
  "800 40px 'Nunito Sans'",
];

export const StudentGuide: React.FC = () => {
  // Fontsource declares the faces; make sure they are loaded before a frame is captured.
  const [handle] = useState(() => delayRender("student fonts"));
  useEffect(() => {
    Promise.all(FONT_FACES.map((f) => document.fonts.load(f)))
      .catch(() => undefined)
      .then(() => continueRender(handle));
  }, [handle]);
  return <Guide />;
};

const Guide: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: theme.bg }}>
    <Series>
      {SCENES.map(([Comp, dur], i) => (
        <Series.Sequence key={i} durationInFrames={dur}>
          <Comp />
        </Series.Sequence>
      ))}
    </Series>
  </AbsoluteFill>
);

/** Start frame of each scene, handy for stills. */
export const SCENE_STARTS = SCENES.reduce<number[]>((acc, [, d], i) => [...acc, (acc[i - 1] ?? 0) + (i ? SCENES[i - 1][1] : 0)], []);
