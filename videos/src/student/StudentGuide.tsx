import React from "react";
import { AbsoluteFill, Series } from "remotion";
import { theme } from "../theme";
import {
  CLOSING_DUR,
  ClosingScene,
  HELP_DUR,
  HelpScene,
  PART_A_DUR,
  PART_B_DUR,
  PartAScene,
  PartBScene,
  PROMISES_DUR,
  PromisesScene,
  RECAP_DUR,
  RecapScene,
  TIMELINE_DUR,
  TimelineScene,
  TITLE_DUR,
  TitleScene,
} from "./Scenes";

const SCENES: [React.FC, number][] = [
  [TitleScene, TITLE_DUR],
  [TimelineScene, TIMELINE_DUR],
  [PartAScene, PART_A_DUR],
  [PartBScene, PART_B_DUR],
  [HelpScene, HELP_DUR],
  [PromisesScene, PROMISES_DUR],
  [RecapScene, RECAP_DUR],
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
