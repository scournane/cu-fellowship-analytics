import React from "react";
import { Sfx, SfxName, Typing } from "../sound";

/* Sound for each scene, on the same frames the scene's own animations use
   (see the timing constants in Scenes.tsx, Guide.tsx and SlackScenes.tsx). */

type Cue = [SfxName, number];
const Cues: React.FC<{ cues: Cue[]; children?: React.ReactNode }> = ({ cues, children }) => (
  <>
    {cues.map(([name, at], i) => (
      <Sfx key={i} name={name} at={at} />
    ))}
    {children}
  </>
);

/** Keys for a `Typed` line in ui.tsx: char n appears at start + ceil(n * 30 / cps). */
const TypedKeys: React.FC<{ start: number; chars: number; cps: number }> = ({ start, chars, cps }) => {
  const every = Math.max(2, Math.round(30 / cps));
  const count = Math.ceil((chars * 30) / cps / every);
  return <Typing from={start + 1} count={count} every={every} />;
};
/** Keys for the Slack composer in Slack.tsx: one char every 2 frames from `at`. */
const SlackKeys: React.FC<{ at: number; chars: number }> = ({ at, chars }) => (
  <Typing from={at + 2} count={chars} every={2} />
);

export const TitleSfx = () => <Cues cues={[["whoosh", 0], ["pop", 10], ["ding", 22]]} />;
export const TimelineSfx = () => <Cues cues={[["whoosh", 0], ["pop", 110], ["pop", 140], ["pop", 200], ["pop", 225]]} />;
export const WelcomeSfx = () => <Cues cues={[["whoosh", 0], ["notify", 15], ["pop", 30], ["pop", 110], ["pop", 200], ["click", 240]]} />;
export const RemindSfx = () => (
  <Cues cues={[["whoosh", 0], ["notify", 30], ["notify", 70], ["notify", 110], ["notify", 160], ["pop", 220], ["pop", 290], ["notify", 370]]} />
);
export const LinkSfx = () => <Cues cues={[["whoosh", 0], ["pop", 20], ["pop", 70], ["pop", 120]]} />;
export const PhraseSfx = () => <Cues cues={[["whoosh", 0], ["pop", 20], ["pop", 80], ["pop", 140]]} />;
// FormAScene: type 150 ("lantern", 7 chars at 7 cps), submit 300, done 322
export const FormASfx = () => (
  <Cues cues={[["whoosh", 0], ["pop", 20], ["pop", 110], ["click", 300], ["success", 322]]}>
    <TypedKeys start={150} chars={7} cps={7} />
  </Cues>
);
export const MatchSfx = () => <Cues cues={[["whoosh", 0], ["pop", 20], ["pop", 50], ["pop", 80], ["error", 110], ["pop", 200]]} />;
export const WindowSfx = () => <Cues cues={[["whoosh", 0], ["pop", 130], ["error", 180], ["pop", 230]]} />;
export const EmailSfx = () => <Cues cues={[["whoosh", 0], ["pop", 20], ["pop", 90], ["pop", 160]]} />;
// FormBScene: pick 60, take 110, week 230, shout 350, tick 460, submit 520, done 540
export const FormBSfx = () => (
  <Cues cues={[["whoosh", 0], ["pop", 20], ["click", 60], ["click", 460], ["click", 520], ["success", 540]]}>
    <TypedKeys start={110} chars={51} cps={22} />
    <TypedKeys start={230} chars={43} cps={22} />
    <TypedKeys start={350} chars={17} cps={14} />
  </Cues>
);
export const RotationSfx = () => <Cues cues={[["whoosh", 0], ["pop", 20], ["pop", 70], ["pop", 120]]} />;
export const HelpSfx = () => <Cues cues={[["whoosh", 0], ["pop", 30], ["pop", 100], ["pop", 170]]} />;
export const CommandsSfx = () => (
  <Cues cues={[["whoosh", 0], ["notify", 40], ["notify", 140], ["notify", 280]]}>
    <SlackKeys at={10} chars={"/help".length} />
  </Cues>
);
export const DashboardSfx = () => (
  <Cues cues={[["whoosh", 0], ["notify", 40], ["pop", 60], ["pop", 240]]}>
    <SlackKeys at={10} chars={"/dashboard".length} />
  </Cues>
);
export const BadgesSfx = () => (
  <Cues cues={[["whoosh", 0], ["notify", 20], ["notify", 145], ["notify", 300]]}>
    <SlackKeys at={120} chars={"/badges".length} />
    <SlackKeys at={270} chars={"/badges off".length} />
  </Cues>
);
export const CheckinSfx = () => (
  <Cues cues={[["whoosh", 0], ["notify", 120]]}>
    <SlackKeys at={10} chars={"/checkin I'm finding the budget reading hard this week".length} />
  </Cues>
);
export const QASfx = () => <Cues cues={[["whoosh", 0], ["pop", 20], ["pop", 90], ["success", 180], ["pop", 260], ["notify", 300]]} />;
export const PollSfx = () => <Cues cues={[["whoosh", 0], ["notify", 10], ["click", 60], ["click", 140], ["pop", 200]]} />;
export const PrivacySfx = () => <Cues cues={[["whoosh", 0], ["pop", 30], ["pop", 85], ["pop", 140], ["pop", 195]]} />;
export const SlackPrivacySfx = () => <Cues cues={[["whoosh", 0], ["pop", 30], ["pop", 85], ["pop", 140], ["pop", 195]]} />;
export const FaqSfx = () => <Cues cues={[["whoosh", 0], ...Array.from({ length: 8 }, (_, i): Cue => ["pop", 20 + i * 50])]} />;
export const RecapSfx = () => (
  <Cues cues={[["whoosh", 0], ["pop", 12], ["success", 35], ["pop", 71], ["pop", 107], ["pop", 143], ["pop", 179], ["success", 215]]} />
);
export const ClosingSfx = () => <Cues cues={[["ding", 2], ["pop", 30]]} />;
