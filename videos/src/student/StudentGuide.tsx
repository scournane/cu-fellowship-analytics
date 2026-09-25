// Imported here directly: package.json marks only *.css as side-effectful, so
// webpack drops src/fonts.ts (side-effect-only .ts) and its font CSS with it.
import "@fontsource/nunito/800.css";
import "@fontsource/nunito/900.css";
import "@fontsource/nunito-sans/500.css";
import "@fontsource/nunito-sans/700.css";
import "@fontsource/nunito-sans/800.css";
import React, { useEffect, useState } from "react";
import { AbsoluteFill, continueRender, delayRender, Series } from "remotion";
import { Music } from "../sound";
import * as S from "./SceneSounds";
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

const SCENES: [React.FC, number, React.FC][] = [
  [TitleScene, TITLE_DUR, S.TitleSfx],
  [TimelineScene, TIMELINE_DUR, S.TimelineSfx],
  [WelcomeScene, WELCOME_DUR, S.WelcomeSfx],
  [RemindScene, REMIND_DUR, S.RemindSfx],
  [LinkScene, LINK_DUR, S.LinkSfx],
  [PhraseScene, PHRASE_DUR, S.PhraseSfx],
  [FormAScene, FORM_A_DUR, S.FormASfx],
  [MatchScene, MATCH_DUR, S.MatchSfx],
  [WindowScene, WINDOW_DUR, S.WindowSfx],
  [EmailScene, EMAIL_DUR, S.EmailSfx],
  [FormBScene, FORM_B_DUR, S.FormBSfx],
  [RotationScene, ROTATION_DUR, S.RotationSfx],
  [Help2Scene, HELP2_DUR, S.HelpSfx],
  [CommandsScene, CMDS_DUR, S.CommandsSfx],
  [DashboardScene, DASH_DUR, S.DashboardSfx],
  [BadgesScene, BADGE_DUR, S.BadgesSfx],
  [CheckinScene, CHECKIN_DUR, S.CheckinSfx],
  [QAScene, QA_DUR, S.QASfx],
  [PollScene, POLL_DUR, S.PollSfx],
  [PrivacyScene, PRIVACY_DUR, S.PrivacySfx],
  [SlackPrivacyScene, SPRIV_DUR, S.SlackPrivacySfx],
  [FaqScene, FAQ_DUR, S.FaqSfx],
  [Recap2Scene, RECAP2_DUR, S.RecapSfx],
  [ClosingScene, CLOSING_DUR, S.ClosingSfx],
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
    <Music />
    <Series>
      {SCENES.map(([Comp, dur, Sounds], i) => (
        <Series.Sequence key={i} durationInFrames={dur}>
          <Comp />
          <Sounds />
        </Series.Sequence>
      ))}
    </Series>
  </AbsoluteFill>
);

/** Start frame of each scene, handy for stills. */
export const SCENE_STARTS = SCENES.reduce<number[]>((acc, [, d], i) => [...acc, (acc[i - 1] ?? 0) + (i ? SCENES[i - 1][1] : 0)], []);
