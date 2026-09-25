import React from "react";
import { AbsoluteFill, Sequence, Series, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { theme } from "../theme";
import { ClosingScene, RulesScene, TitleScene } from "./cards";
import { CONFIDENCE, REPORT } from "./cliOutput";
import { RealScene, SceneDef, Terminal, sceneLength } from "./real";
import { Caption } from "./ui";

type Box = [number, number, number, number];
const mid = (b: Box, f: number) => ({ f, x: b[0] + b[2] / 2, y: b[1] + b[3] / 2 });
const cam = (f: number, x: number, y: number, z = 1) => ({ f, x, y, z });

/* Boxes are page coordinates recorded by scripts/capture-admin.mjs (public/admin/boxes.json). */
const B = {
  email: [749, 788, 422, 20] as Box,
  dev: [740, 826, 440, 32] as Box,
  connect0: [500, 544, 125, 32] as Box,
  status0: [480, 268, 960, 148] as Box,
  connectErr: [480, 268, 960, 104] as Box,
  partA: [500, 308, 409, 28] as Box,
  manual: [517, 757, 886, 20] as Box,
  verifyA: [500, 934, 181, 32] as Box,
  partB: [500, 1022, 319, 28] as Box,
  verifyB: [500, 1648, 183, 32] as Box,
  week11: [480, 1211, 960, 72] as Box,
  rotTable: [480, 672, 960, 520] as Box,
  newBtn: [480, 264, 103, 32] as Box,
  sessTable: [480, 312, 960, 400] as Box,
};

const SIGNIN: SceneDef = {
  step: "1",
  title: "Sign in",
  beats: [
    {
      img: "01-signin", h: 1080, dur: 100,
      cam: [cam(0, 960, 540, 1), cam(90, 960, 760, 1.5)],
      hl: [{ b: B.email, from: 30, label: "allowlisted address" }],
      cur: [mid(B.email, 20), mid(B.email, 50)], clicks: [55],
    },
    {
      img: "02-signin-filled", h: 1080, dur: 110,
      cam: [cam(0, 960, 760, 1.5)],
      hl: [{ b: B.dev, from: 10, label: "Sign in without Google" }],
      cur: [mid(B.email, 0), mid(B.dev, 40)], clicks: [60],
    },
  ],
  lines: [
    { from: 0, text: "Staff sign in with Google. Only addresses on CUFA_CONSOLE_ALLOWLIST get in." },
    { from: 110, text: "This developer sign-in is a labelled bypass, only in fake-Google demo mode. It is how this video got in." },
  ],
};

const CONNECT: SceneDef = {
  step: "2",
  title: "Connect Google — connect, status, disconnect",
  beats: [
    {
      img: "03-connect-before", h: 1080, dur: 140,
      cam: [cam(0, 960, 540, 1), cam(120, 960, 420, 1.35)],
      hl: [
        { b: B.status0, from: 10, to: 75, label: "status + scopes" },
        { b: B.connect0, from: 75, label: "Connect Google" },
      ],
      cur: [{ f: 60, x: 1100, y: 700 }, mid(B.connect0, 105)], clicks: [115],
    },
    {
      img: "04-connect-after", h: 1080, dur: 130,
      cam: [cam(0, 960, 420, 1.35), cam(110, 960, 330, 1.35)],
      hl: [{ b: B.connectErr, from: 15, label: "real response from this demo server", color: theme.danger }],
    },
  ],
  lines: [
    { from: 0, text: "One staff account grants access once — just forms.body and drive.file. Status shows the account and scopes." },
    { from: 75, text: "Connect runs the Google consent. Once connected, a Disconnect button appears and revokes the stored token." },
    { from: 150, text: "This demo server has no encryption key, so it refuses to store even a simulated token — and says exactly why." },
  ],
};

const TEMPLATE: SceneDef = {
  step: "3",
  title: "Template setup — Part A and Part B",
  beats: [
    {
      img: "06-template", h: 1806, dur: 250,
      cam: [cam(0, 960, 540, 1), cam(60, 960, 560, 1.15), cam(120, 960, 830, 1.3), cam(190, 960, 1300, 1.1), cam(240, 960, 1500, 1.1)],
      hl: [
        { b: B.partA, from: 10, to: 80, label: "Create the Part A template" },
        { b: B.manual, from: 80, to: 150, label: "manual step: Verified emails" },
        { b: B.verifyA, from: 110, to: 170 },
        { b: B.partB, from: 170, to: 250, label: "same again for Part B" },
        { b: B.verifyB, from: 200, to: 250 },
      ],
      cur: [{ f: 100, x: 800, y: 760 }, mid(B.verifyA, 140)], clicks: [150],
    },
    {
      img: "07-template-verified", h: 1886, dur: 110,
      cam: [cam(0, 960, 600, 1.15)],
      hl: [{ b: [480, 268, 960, 64], from: 5, label: "read back from the API" }, { b: [500, 1014, 181, 32], from: 40 }],
    },
  ],
  lines: [
    { from: 0, text: "Create the Part A (mid-lesson) and Part B (end-of-session) templates once. Every session copies them." },
    { from: 80, text: "The one manual step: in Google Forms set Collect email addresses → Verified. The API can’t do it reliably." },
    { from: 150, text: "Then Verify: the console reads form settings back from the API and trusts only that. Replace retires a template." },
    { from: 250, text: "Verified. If a template drifts later, Replace creates a fresh form — past sessions keep their own copies." },
  ],
};

const ROTATION: SceneDef = {
  step: "4",
  title: "Rotation schedule",
  beats: [
    {
      img: "08-rotation", h: 1532, dur: 220,
      cam: [cam(0, 960, 540, 1), cam(80, 960, 900, 1.1), cam(170, 960, 1180, 1.35)],
      hl: [
        { b: B.rotTable, from: 20, to: 110, label: "week → rotating question" },
        { b: B.week11, from: 120, label: "week 11 needs a teacher question", color: theme.danger },
      ],
    },
  ],
  lines: [
    { from: 0, text: "One Part B question rotates weekly: teacher question, muddiest point, application." },
    { from: 120, text: "Teacher-question weeks need your own question. With none set, provisioning is blocked — no generic stand-in." },
  ],
};

const SESSIONS: SceneDef = {
  step: "5",
  title: "Sessions — list and create",
  beats: [
    {
      img: "09-sessions", h: 1146, dur: 110,
      cam: [cam(0, 960, 540, 1), cam(90, 960, 500, 1.15)],
      hl: [{ b: B.sessTable, from: 10, to: 60, label: "every session, form state, counts" }, { b: B.newBtn, from: 60 }],
      cur: [{ f: 40, x: 900, y: 600 }, mid(B.newBtn, 80)], clicks: [90],
    },
    {
      img: "11-new-filled", h: 1080, dur: 150,
      cam: [cam(0, 960, 300, 1.2), cam(70, 960, 400, 1.2), cam(140, 960, 800, 1.2)],
      hl: [
        { b: [509, 96, 902, 20], from: 5, to: 45, label: "title" },
        { b: [533, 192, 181, 20], from: 40, to: 80, label: "date + time, with timezone" },
        { b: [509, 813, 286, 20], from: 85, to: 150, label: "week 11" },
        { b: [509, 905, 902, 20], from: 105, to: 150, label: "teacher question — left blank" },
      ],
    },
    {
      img: "12-new-passphrase", h: 1080, dur: 130,
      cam: [cam(0, 960, 620, 1.3)],
      hl: [{ b: [500, 606, 163, 28], from: 5, to: 50 }, { b: [509, 572, 902, 20], from: 50, label: "suggested passphrase" }, { b: [500, 947, 107, 32], from: 90 }],
      cur: [mid([500, 606, 163, 28], 0), mid([500, 606, 163, 28], 25), mid([500, 947, 107, 32], 100)], clicks: [30, 110],
    },
  ],
  lines: [
    { from: 0, text: "The Sessions list shows every session with its form state and counts. New session opens the form." },
    { from: 110, text: "Title, local time with its timezone, duration, grace window, cohort, and the week of the fellowship." },
    { from: 260, text: "Suggest a passphrase avoids words already used in this cohort. Save — we deliberately skip the question." },
  ],
};

const BLOCK: SceneDef = {
  step: "6",
  title: "No teacher question → provisioning blocked, then edit",
  beats: [
    {
      img: "14-detail-before", h: 1080, dur: 150,
      cam: [cam(0, 960, 540, 1), cam(60, 960, 900, 1.35)],
      hl: [{ b: [500, 962, 920, 118], from: 40, label: "real block on Part B", color: theme.danger }],
    },
    {
      img: "17-edit-question", h: 1080, dur: 130,
      cam: [cam(0, 960, 900, 1.35)],
      hl: [{ b: [509, 966, 902, 20], from: 5, to: 80, label: "Your question for this week" }, { b: [500, 1008, 107, 32], from: 80 }],
      cur: [{ f: 60, x: 1100, y: 900 }, mid([500, 1008, 107, 32], 95)], clicks: [105],
    },
  ],
  lines: [
    { from: 0, text: "The new session opens with Part B blocked: week 11 asks the teacher’s own question and none is set." },
    { from: 150, text: "Edit this session, type the question, save. Editing never touches a form that is already provisioned." },
  ],
};

const PROVISION: SceneDef = {
  step: "7",
  title: "Provision both forms",
  beats: [
    {
      img: "15-provisioned-a", h: 1080, dur: 90,
      cam: [cam(0, 960, 540, 1), cam(80, 960, 500, 1.2)],
      hl: [{ b: [480, 440, 960, 64], from: 10, label: "Part A provisioned" }],
    },
    {
      img: "40-detail-full", h: 2904, dur: 220,
      cam: [cam(0, 960, 700, 1.2), cam(90, 960, 900, 1.2), cam(150, 960, 1300, 1.2), cam(210, 960, 1300, 1.2)],
      hl: [
        { b: [508, 654, 117, 20], from: 10, to: 90, label: "publish state read back" },
        { b: [500, 898, 240, 240], from: 50, to: 130, label: "link + QR only once verified" },
        { b: [500, 1250, 460, 40], from: 140, label: "Part B provisioned too" },
      ],
    },
  ],
  lines: [
    { from: 0, text: "Provision Part A copies the template into a new form and publishes it. Safe to press twice — never duplicated." },
    { from: 100, text: "“Published and verified” is read back from the API. No link or QR code is shown until it is." },
    { from: 230, text: "Part B provisions the same way, with the week’s rotating question snapshotted onto the form." },
  ],
};

const LESSON: SceneDef = {
  step: "8",
  title: "Mid-lesson: passphrase and Announce now",
  beats: [
    {
      img: "19-mid-lesson", h: 1080, dur: 120,
      cam: [cam(0, 960, 540, 1), cam(90, 960, 470, 1.4)],
      hl: [{ b: [480, 440, 960, 138], from: 20, label: "put this on screen" }],
    },
    {
      img: "40-detail-full", h: 2904, dur: 110,
      cam: [cam(0, 960, 2380, 1.3)],
      hl: [{ b: [500, 2346, 197, 32], from: 10, label: "Announce" }],
      cur: [{ f: 0, x: 1000, y: 2250 }, mid([500, 2346, 197, 32], 30)], clicks: [45],
    },
    {
      img: "20-announced", h: 1080, dur: 90,
      cam: [cam(0, 960, 470, 1.4)],
      hl: [{ b: [480, 440, 960, 64], from: 5, label: "T0 recorded" }],
    },
  ],
  lines: [
    { from: 0, text: "During the lesson, open the session. Say the passphrase aloud AND show it — audio alone excludes fellows." },
    { from: 120, text: "Press Announce now when you tell the room the form is open. That moment is T0 for every check-in." },
    { from: 230, text: "Announced. Latency is measured from T0 and recorded, never judged. Announce again resets it." },
  ],
};

const PULL: SceneDef = {
  step: "9",
  title: "Live counter and pulling responses",
  beats: [
    {
      img: "21-pull-before", h: 2904, dur: 140,
      cam: [cam(0, 960, 2480, 1.35)],
      hl: [{ b: [480, 2414, 960, 150], from: 10, to: 80, label: "live counter, every 5 s" }, { b: [500, 2586, 116, 32], from: 80 }],
      cur: [{ f: 50, x: 1000, y: 2700 }, mid([500, 2586, 116, 32], 95)], clicks: [110],
    },
    {
      img: "22-pull-after", h: 2984, dur: 120,
      cam: [cam(0, 960, 470, 1.35), cam(70, 960, 470, 1.35), cam(110, 960, 1780, 1.2)],
      hl: [{ b: [480, 440, 960, 64], from: 5, to: 70, label: "pulled via the Forms API" }, { b: [500, 1802, 159, 32], from: 85, label: "Pull Part B" }],
    },
  ],
  lines: [
    { from: 0, text: "The Responses counter refreshes every five seconds from what is stored. Pull responses fetches new ones." },
    { from: 140, text: "Pulls are idempotent: already-recorded answers are skipped. Part B has its own pull button." },
    { from: 200, text: "If the API is unavailable, export the CSV and run `cufa ingest part-a` — the fallback path." },
  ],
};

const RESPONSES: SceneDef = {
  step: "10",
  title: "Part B responses and themes",
  beats: [
    {
      img: "23-responses", h: 2591, dur: 300,
      cam: [cam(0, 960, 490, 1.2), cam(90, 960, 490, 1.2), cam(130, 960, 860, 1.2), cam(210, 960, 860, 1.2), cam(260, 960, 1400, 1.1)],
      hl: [
        { b: [480, 305, 960, 365], from: 10, to: 100, label: "confidence: median + IQR, never a mean" },
        { b: [480, 686, 960, 344], from: 110, to: 220, label: "muddiest-point themes (AI)" },
        { b: [500, 924, 147, 32], from: 150, to: 220 },
        { b: [480, 1048, 960, 500], from: 240, label: "takeaways — counted, never graded" },
      ],
      cur: [{ f: 140, x: 900, y: 1000 }, mid([500, 924, 147, 32], 175)], clicks: [185],
    },
  ],
  lines: [
    { from: 0, text: "Each session’s end-of-session page: confidence as a distribution — read the trend, not a single score." },
    { from: 110, text: "Regenerate themes groups “what’s still unclear” answers. The AI sees anonymous text only — aggregate, no names." },
    { from: 220, text: "No key here, so no themes — the answers stay readable. Takeaways are counted, never graded." },
  ],
};

const REVIEW: SceneDef = {
  step: "11",
  title: "Review queue — deciding",
  beats: [
    {
      img: "25-review-needs", h: 5000, dur: 150,
      cam: [cam(0, 960, 400, 1), cam(70, 1000, 480, 1.35)],
      hl: [
        { b: [480, 244, 540, 32], from: 5, to: 60, label: "four tabs" },
        { b: [1060, 440, 180, 100], from: 60, label: "typed vs expected" },
      ],
    },
    {
      img: "26-review-note", h: 5000, dur: 110,
      cam: [cam(0, 1100, 480, 1.6)],
      hl: [{ b: [1250, 455, 176, 100], from: 5, label: "note + decision" }],
      cur: [{ f: 20, x: 1200, y: 560 }, mid([1250, 491, 80, 28], 60)], clicks: [80],
    },
    {
      img: "27-review-decided", h: 5000, dur: 100,
      cam: [cam(0, 960, 400, 1.2)],
      hl: [{ b: [480, 243, 960, 65], from: 5, label: "recorded, audited" }],
    },
  ],
  lines: [
    { from: 0, text: "Tier 1 rules decide clear cases; tier 2 AI takes some ambiguous ones; the rest wait here for tier 3 — you." },
    { from: 100, text: "Oldest first. Compare what the fellow typed with the expected passphrase, add an optional note, decide." },
    { from: 260, text: "Decisions are append-only: an override adds a row, never edits one. A human decision always wins." },
  ],
};

const TABS: SceneDef = {
  step: "12",
  title: "Review — AI decisions, straight-lining, addresses",
  beats: [
    {
      img: "28-review-ai", h: 1080, dur: 100,
      cam: [cam(0, 960, 540, 1), cam(80, 960, 420, 1.3)],
      hl: [{ b: [596, 244, 106, 32], from: 10, label: "AI decisions" }],
    },
    {
      img: "29-review-straight", h: 1080, dur: 100,
      cam: [cam(0, 960, 420, 1.3)],
      hl: [{ b: [704, 244, 119, 32], from: 5, to: 50 }, { b: [480, 421, 960, 122], from: 40, label: "data-quality flag only" }],
    },
    {
      img: "30-review-identities", h: 1080, dur: 100,
      cam: [cam(0, 960, 420, 1.3)],
      hl: [{ b: [825, 244, 173, 32], from: 5, to: 50 }, { b: [480, 421, 960, 80], from: 40, label: "email not on roster" }],
    },
  ],
  lines: [
    { from: 0, text: "AI decisions lists every model call with its reasoning, so staff sample it — overriding supersedes it for good." },
    { from: 100, text: "Straight-lining: identical confidence four sessions running. A data-quality flag — never a count or score." },
    { from: 200, text: "Unresolved addresses: check-ins from emails not on the roster. Fix the roster, and they resolve." },
  ],
};

const SHOUTOUTS: SceneDef = {
  step: "13",
  title: "Shoutouts — linking names",
  beats: [
    {
      img: "31-shoutouts", h: 1080, dur: 140,
      cam: [cam(0, 960, 540, 1), cam(80, 1000, 520, 1.3)],
      hl: [{ b: [488, 455, 224, 70], from: 10, to: 70, label: "as typed" }, { b: [1208, 463, 224, 60], from: 60, label: "roster candidates" }],
      cur: [{ f: 70, x: 1000, y: 700 }, mid([1208, 463, 224, 28], 105)], clicks: [120],
    },
    {
      img: "32-shoutout-linked", h: 1080, dur: 120,
      cam: [cam(0, 960, 480, 1.3)],
      hl: [{ b: [480, 268, 960, 64], from: 5, to: 60, label: "linked, against your address" }, { b: [1200, 450, 240, 85], from: 60, label: "outside the roster is fine" }],
    },
  ],
  lines: [
    { from: 0, text: "Names fellows typed for “who helped you” that didn’t match exactly one person. Pick the right fellow to link." },
    { from: 140, text: "Guest speakers and teachers get thanked too — they stay unlinked. Third-party names are protected data." },
  ],
};

const HELP: SceneDef = {
  step: "14",
  title: "Help requests — access-gated",
  beats: [
    {
      img: "33-help", h: 1080, dur: 150,
      cam: [cam(0, 960, 540, 1), cam(60, 960, 440, 1.2), cam(130, 960, 700, 1.2)],
      hl: [
        { b: [480, 268, 960, 84], from: 5, to: 60, label: "not routine data" },
        { b: [480, 368, 960, 220], from: 60, to: 110, label: "who is emailed, who may open this" },
        { b: [480, 657, 960, 252], from: 110 },
      ],
    },
    {
      img: "34-help-note", h: 1080, dur: 110,
      cam: [cam(0, 960, 780, 1.4)],
      hl: [{ b: [500, 821, 920, 28], from: 5, to: 50, label: "staff note" }, { b: [500, 857, 135, 32], from: 50 }],
      cur: [{ f: 30, x: 1100, y: 830 }, mid([500, 857, 135, 32], 65)], clicks: [80],
    },
    {
      img: "36-help-acknowledged", h: 1080, dur: 100,
      cam: [cam(0, 960, 760, 1.3)],
      hl: [{ b: [500, 829, 920, 116], from: 5, to: 55, label: "picked up" }, { b: [500, 913, 60, 32], from: 55, label: "Close when done" }],
    },
  ],
  lines: [
    { from: 0, text: "Fellows who ticked “I’d like someone to check in with me”. Only the people named for it can open this screen." },
    { from: 150, text: "Add a note on what you did, then “I’m picking this up”. Nothing the fellow typed is copied here." },
    { from: 260, text: "Close it once handled. Asking for help never lowers participation and appears in no report or count." },
  ],
};

const COHORT: SceneDef = {
  step: "15",
  title: "Cohort filter",
  beats: [
    {
      img: "32-shoutout-linked", h: 1080, dur: 80,
      cam: [cam(0, 700, 380, 1.6)],
      hl: [{ b: [480, 372, 115, 28], from: 5, label: "Cohort" }],
      cur: [{ f: 10, x: 800, y: 500 }, mid([480, 372, 115, 28], 40)], clicks: [50],
    },
    {
      img: "25-review-needs", h: 5000, dur: 70,
      cam: [cam(0, 700, 320, 1.6)],
      hl: [{ b: [480, 290, 125, 50], from: 5, label: "same filter on Review" }],
    },
  ],
  lines: [
    { from: 0, text: "Sessions, Review, Shoutouts and Help requests all share one cohort filter. Pick a cohort, or All cohorts." },
  ],
};

const SCREENS = [SIGNIN, CONNECT, TEMPLATE, ROTATION, SESSIONS, BLOCK, PROVISION, LESSON, PULL, RESPONSES, REVIEW, TABS, SHOUTOUTS, HELP, COHORT];

const CliScene: React.FC = () => (
  <AbsoluteFill style={{ background: theme.bg }}>
    <Sequence durationInFrames={190}>
      <Terminal blocks={[{ at: 10, cmd: "cufa report --cohort demo", out: REPORT }]} />
    </Sequence>
    <Sequence from={190}>
      <Terminal blocks={[{ at: 5, cmd: "cufa report --cohort demo --confidence", out: CONFIDENCE }]} />
    </Sequence>
    <Caption
      step="16"
      title="The command line — cufa"
      lines={[
        { from: 0, text: "Everything in the console is also a command. `cufa report` gives the attendance picture for a cohort." },
        { from: 190, text: "`--confidence` shows the weekly trend (median, IQR). Also: pull, ingest, review, decide, themes, --json." },
      ]}
    />
  </AbsoluteFill>
);

const CLI_DUR = 360;

const SCENES: [React.FC, number][] = [
  [TitleScene, 120],
  ...SCREENS.map((d): [React.FC, number] => {
    const S: React.FC = () => <RealScene def={d} />;
    return [S, sceneLength(d)];
  }),
  [CliScene, CLI_DUR],
  [RulesScene, 210],
  [ClosingScene, 120],
];

export const ADMIN_DURATION = SCENES.reduce((a, [, d]) => a + d, 0);

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

export const AdminGuide: React.FC = () => {
  return (
    <AbsoluteFill style={{ background: theme.bg }}>
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

