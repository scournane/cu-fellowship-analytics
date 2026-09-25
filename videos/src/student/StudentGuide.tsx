import React from "react";
import { AbsoluteFill, Series } from "remotion";
import { theme } from "../theme";
import { CLOSING_DUR, ClosingScene, TIMELINE_DUR, TimelineScene, TITLE_DUR, TitleScene } from "./Scenes";
import {
  EMAIL_DUR,
  EmailScene,
  FAQ_DUR,
  FaqScene,
  FORM_A_DUR,
  FORM_B_DUR,
  FormAScene,
  FormBScene,
  HELP2_DUR,
  Help2Scene,
  LINK_DUR,
  LinkScene,
  MATCH_DUR,
  MatchScene,
  PHRASE_DUR,
  PhraseScene,
  PRIVACY_DUR,
  PrivacyScene,
  RECAP2_DUR,
  Recap2Scene,
  ROTATION_DUR,
  RotationScene,
  WINDOW_DUR,
  WindowScene,
} from "./Guide";

const SCENES: [React.FC, number][] = [
  [TitleScene, TITLE_DUR],
  [TimelineScene, TIMELINE_DUR],
  [LinkScene, LINK_DUR],
  [PhraseScene, PHRASE_DUR],
  [FormAScene, FORM_A_DUR],
  [MatchScene, MATCH_DUR],
  [WindowScene, WINDOW_DUR],
  [EmailScene, EMAIL_DUR],
  [FormBScene, FORM_B_DUR],
  [RotationScene, ROTATION_DUR],
  [Help2Scene, HELP2_DUR],
  [PrivacyScene, PRIVACY_DUR],
  [FaqScene, FAQ_DUR],
  [Recap2Scene, RECAP2_DUR],
  [ClosingScene, CLOSING_DUR],
];

export const STUDENT_DURATION = SCENES.reduce((sum, [, d]) => sum + d, 0);

export const StudentGuide: React.FC = () => (
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
