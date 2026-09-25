import React from "react";
import { AbsoluteFill, Sequence, Series, continueRender, delayRender, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { theme } from "../theme";
import { ClosingScene, RulesScene, TitleScene } from "./cards";
import { DOCTOR, ENGAGEMENT, EXPORT, REPORT, TEMPLATE_CSV } from "./cliOutput";
import { CornerPill, RealScene, SceneDef, SlackExchange, SlackPane, Terminal, sceneLength } from "./real";
import { SLACK } from "./slackOutput";
import { Caption, CaptionLine } from "./ui";

type Box = [number, number, number, number];
const mid = (b: Box, f: number) => ({ f, x: b[0] + b[2] / 2, y: b[1] + b[3] / 2 });
const cam = (f: number, x: number, y: number, z = 1) => ({ f, x, y, z });
const click = (b: Box, from: number, at: number) => ({ cur: [{ f: from, x: b[0] + b[2] + 120, y: b[1] + 160 }, mid(b, at - 10)], clicks: [at] });

// Page coordinates recorded by scripts/capture-admin.mjs (public/admin/boxes.json).

const SIGNIN: SceneDef = {
  step: "1",
  title: "Sign in",
  beats: [
    {
      img: "01-signin", h: 1280, dur: 200,
      cam: [cam(0, 960, 640, 1), cam(80, 960, 760, 1.5)],
      hl: [
        { b: [740, 495, 440, 75], from: 20, to: 90, label: "Google sign-in + allowlist" },
        { b: [749, 906, 422, 24], from: 90, to: 140, label: "developer bypass (demo only)" },
        { b: [740, 950, 440, 40], from: 140 },
      ],
      ...click([740, 950, 440, 40], 110, 170),
    },
  ],
  lines: [
    { from: 0, text: "Staff sign in with Google; only addresses on CUFA_CONSOLE_ALLOWLIST get in. A site password is the other door." },
    { from: 90, text: "The developer sign-in is a labelled bypass that exists only in fake-Google demo mode. It is how this video got in." },
  ],
};

const CONNECT: SceneDef = {
  step: "2",
  title: "Connect Google — connect, status, disconnect",
  beats: [
    {
      img: "03-connect-before", h: 1280, dur: 110,
      cam: [cam(0, 960, 500, 1.2)],
      hl: [{ b: [647, 582, 174, 40], from: 20, label: "Connect Google" }],
      ...click([647, 582, 174, 40], 30, 80),
    },
    {
      img: "04-connect-after", h: 1280, dur: 200,
      cam: [cam(0, 960, 500, 1.2), cam(150, 960, 560, 1.35)],
      hl: [
        { b: [627, 267, 928, 60], from: 5, to: 60, label: "no Google call was made" },
        { b: [627, 360, 928, 210], from: 60, to: 140, label: "status: account, scopes, refresh" },
        { b: [647, 718, 258, 40], from: 140, label: "Reconnect · Disconnect" },
      ],
    },
  ],
  lines: [
    { from: 0, text: "One staff account grants access once — only forms.body and drive.file. Every form then lives in CU’s Drive." },
    { from: 110, text: "Connected. Status shows the account, when it connected and its scopes. The refresh token is encrypted at rest." },
    { from: 250, text: "Reconnect re-runs consent; Disconnect revokes and deletes the stored token." },
  ],
};

const TEMPLATE: SceneDef = {
  step: "3",
  title: "Template setup — Part A and Part B",
  beats: [
    {
      img: "06-template", h: 2156, dur: 260,
      cam: [cam(0, 960, 540, 1), cam(70, 960, 800, 1.25), cam(170, 960, 1500, 1.1), cam(250, 960, 1600, 1.1)],
      hl: [
        { b: [663, 741, 856, 24], from: 30, to: 110, label: "manual step: Collect emails → Verified" },
        { b: [647, 937, 269, 40], from: 90, to: 160, label: "Verify Part A" },
        { b: [647, 1685, 268, 40], from: 180, label: "same for Part B" },
      ],
      ...click([647, 937, 269, 40], 100, 150),
    },
    {
      img: "07-template-verified", h: 2237, dur: 100,
      cam: [cam(0, 960, 600, 1.15)],
      hl: [{ b: [647, 1019, 269, 40], from: 10, label: "read back from the API" }],
    },
  ],
  lines: [
    { from: 0, text: "Create the Part A (mid-lesson) and Part B (end-of-session) templates once; every session copies them." },
    { from: 30, text: "The one manual step: in Google Forms set Collect email addresses → Verified. The API can’t do it reliably." },
    { from: 140, text: "Verify reads the form settings back from the API and trusts only that. Replace retires a template for a fresh one." },
  ],
};

const ROTATION: SceneDef = {
  step: "4",
  title: "Rotation schedule",
  beats: [
    {
      img: "08-rotation", h: 2115, dur: 210,
      cam: [cam(0, 960, 540, 1), cam(80, 960, 1000, 1.1), cam(160, 960, 1450, 1.3)],
      hl: [
        { b: [627, 683, 928, 700], from: 20, to: 110, label: "week → rotating question" },
        { b: [627, 1493, 928, 59], from: 120, label: "week 11 needs a teacher question", color: theme.danger },
      ],
    },
  ],
  lines: [
    { from: 0, text: "One Part B question rotates weekly: the teacher’s own question, muddiest point, or application." },
    { from: 120, text: "Teacher-question weeks need your question. Without it provisioning is blocked — never a generic stand-in." },
  ],
};

const SESSIONS: SceneDef = {
  step: "5",
  title: "Sessions — list, CSV schedule, create",
  beats: [
    {
      img: "09-sessions", h: 2150, dur: 170,
      cam: [cam(0, 960, 540, 1), cam(60, 960, 420, 1.25)],
      hl: [
        { b: [611, 477, 960, 700], from: 5, to: 60, label: "every session: form state, check-ins" },
        { b: [647, 407, 888, 49], from: 60, to: 120, label: "Load a schedule from CSV" },
        { b: [1075, 432, 175, 30], from: 90, to: 130, label: "template.csv" },
        { b: [627, 240, 137, 40], from: 125, label: "New session" },
      ],
      ...click([627, 240, 137, 40], 130, 160),
    },
    {
      img: "11-new-filled", h: 1938, dur: 170,
      cam: [cam(0, 960, 420, 1.2), cam(70, 960, 460, 1.2), cam(130, 960, 1500, 1.2)],
      hl: [
        { b: [656, 276, 870, 24], from: 5, to: 45, label: "title" },
        { b: [679, 381, 193, 24], from: 40, to: 90, label: "local date + time" },
        { b: [656, 486, 828, 24], from: 55, to: 90, label: "timezone" },
        { b: [656, 1562, 341, 24], from: 110, to: 170, label: "week 11" },
        { b: [656, 1663, 870, 24], from: 130, to: 170, label: "teacher question — left blank" },
      ],
    },
    {
      img: "12-new-passphrase", h: 1938, dur: 130,
      cam: [cam(0, 960, 1380, 1.3), cam(80, 960, 1500, 1.3)],
      hl: [{ b: [647, 1337, 227, 32], from: 5, to: 50 }, { b: [656, 1297, 870, 24], from: 45, to: 95, label: "suggested passphrase" }, { b: [647, 1711, 140, 40], from: 90 }],
      cur: [mid([647, 1337, 227, 32], 0), mid([647, 1337, 227, 32], 20), mid([647, 1711, 140, 40], 100)],
      clicks: [25, 110],
    },
  ],
  lines: [
    { from: 0, text: "Sessions lists every session. Load a whole schedule from a CSV — download template.csv for the columns." },
    { from: 170, text: "New session: title, local time with its timezone, duration, grace window, cohort and the fellowship week." },
    { from: 340, text: "Suggest a passphrase proposes a fresh word. Save — here we skip the teacher question on purpose." },
  ],
};

const BLOCK: SceneDef = {
  step: "6",
  title: "Blocked without a question, then edit",
  beats: [
    {
      img: "14-detail-before", h: 2581, dur: 150,
      cam: [cam(0, 960, 540, 1), cam(70, 960, 1000, 1.3)],
      hl: [{ b: [645, 1020, 885, 145], from: 50, label: "Part B blocked", color: theme.danger }],
    },
    {
      img: "17-edit-question", h: 1949, dur: 130,
      cam: [cam(0, 960, 1560, 1.3)],
      hl: [{ b: [656, 1674, 870, 24], from: 5, to: 80, label: "Your question for this week" }, { b: [647, 1722, 140, 40], from: 80 }],
      ...click([647, 1722, 140, 40], 60, 105),
    },
  ],
  lines: [
    { from: 0, text: "The new session opens with Part B blocked: week 11 asks the teacher’s own question and none is set." },
    { from: 150, text: "Edit this session, type the week’s question and save. Now Part B can be provisioned." },
  ],
};

const PROVISION: SceneDef = {
  step: "7",
  title: "Provision both forms",
  beats: [
    {
      img: "14-detail-before", h: 2581, dur: 90,
      cam: [cam(0, 960, 760, 1.3)],
      hl: [{ b: [647, 794, 178, 40], from: 5, label: "Provision Part A" }],
      ...click([647, 794, 178, 40], 10, 60),
    },
    {
      img: "19-mid-lesson", h: 3884, dur: 230,
      cam: [cam(0, 960, 800, 1.2), cam(90, 960, 950, 1.2), cam(150, 960, 1400, 1.2), cam(220, 960, 1400, 1.2)],
      hl: [
        { b: [655, 692, 141, 20], from: 10, to: 90, label: "published and verified" },
        { b: [647, 939, 240, 240], from: 50, to: 140, label: "link + QR only once verified" },
        { b: [647, 1307, 410, 36], from: 150, label: "Part B provisioned too" },
      ],
    },
  ],
  lines: [
    { from: 0, text: "Provision Part A copies the template, publishes it and reads the state back. Safe to press twice." },
    { from: 100, text: "“Published and verified” comes from the API, not an assumption. No link or QR code appears until it does." },
    { from: 230, text: "Part B provisions the same way, with the week’s rotating question snapshotted onto the form." },
  ],
};

const LESSON: SceneDef = {
  step: "8",
  title: "Mid-lesson: passphrase, Announce, live counter, pull",
  beats: [
    {
      img: "19-mid-lesson", h: 3884, dur: 110,
      cam: [cam(0, 960, 540, 1), cam(80, 960, 480, 1.4)],
      hl: [{ b: [627, 450, 928, 140], from: 20, label: "say it AND show it" }],
    },
    {
      img: "19-mid-lesson", h: 3884, dur: 100,
      cam: [cam(0, 960, 2820, 1.3)],
      hl: [{ b: [647, 2777, 164, 40], from: 10, label: "Announce now" }],
      ...click([647, 2777, 164, 40], 10, 60),
    },
    {
      img: "20-announced", h: 3942, dur: 110,
      cam: [cam(0, 960, 2900, 1.3)],
      hl: [{ b: [647, 2834, 274, 40], from: 5, to: 55, label: "T0 recorded" }, { b: [627, 2915, 928, 150], from: 55, label: "live counter, every 5 s" }],
    },
    {
      img: "22-pull-after", h: 3942, dur: 170,
      cam: [cam(0, 960, 3000, 1.3), cam(80, 960, 3000, 1.3), cam(130, 960, 3300, 1.2)],
      hl: [
        { b: [647, 3090, 164, 40], from: 5, to: 80, label: "Pull responses (Forms API)" },
        { b: [647, 3398, 888, 49], from: 90, label: "Zoom transcript upload" },
      ],
      ...click([647, 3090, 164, 40], 0, 40),
    },
  ],
  lines: [
    { from: 0, text: "During the lesson, open the session. Say the passphrase aloud AND display it — audio alone excludes fellows." },
    { from: 110, text: "Press Announce now when you tell the room the form is open. That moment is T0 for every check-in’s latency." },
    { from: 320, text: "Pull fetches new responses; repeats are skipped. No API? Export the CSV and run `cufa ingest part-a`." },
    { from: 400, text: "Uploading the Zoom transcript adds who spoke and for how long to the session summary." },
  ],
};

const RESPONSES: SceneDef = {
  step: "9",
  title: "Part B responses and themes",
  beats: [
    {
      img: "23-responses", h: 3411, dur: 270,
      cam: [cam(0, 960, 560, 1.15), cam(90, 960, 560, 1.15), cam(130, 960, 950, 1.2), cam(200, 960, 950, 1.2), cam(250, 960, 1500, 1.1)],
      hl: [
        { b: [647, 346, 888, 36], from: 10, to: 100, label: "confidence: median + IQR, never a mean" },
        { b: [647, 766, 888, 36], from: 110, to: 200 },
        { b: [647, 1038, 200, 40], from: 130, to: 200, label: "Regenerate themes (AI, aggregate)" },
        { b: [647, 1206, 888, 36], from: 215, label: "takeaways — counted, never graded" },
      ],
    },
  ],
  lines: [
    { from: 0, text: "Each session’s end-of-session page shows confidence as a distribution — read the trend, not one score." },
    { from: 110, text: "Themes group the “what’s still unclear” answers. The model sees anonymous text only and never judges a fellow." },
    { from: 210, text: "Takeaways are counted, never graded: rating writing would penalise second-language and different writers." },
  ],
};

const REVIEW: SceneDef = {
  step: "10",
  title: "Review queue — deciding",
  beats: [
    {
      img: "26-review-note", h: 5000, dur: 200,
      cam: [cam(0, 960, 500, 1), cam(70, 1100, 480, 1.4)],
      hl: [
        { b: [627, 222, 560, 40], from: 5, to: 70, label: "four queues" },
        { b: [1339, 432, 216, 112], from: 80, to: 200, label: "note + decision" },
      ],
      ...click([1339, 472, 109, 32], 120, 175),
    },
    {
      img: "27-review-decided", h: 5000, dur: 90,
      cam: [cam(0, 960, 400, 1.15)],
    },
  ],
  lines: [
    { from: 0, text: "Tier 1 rules decide clear cases, tier 2 AI takes some ambiguous ones, the rest wait here for tier 3 — you." },
    { from: 80, text: "Compare what the fellow typed with the expected passphrase, add an optional note, decide." },
    { from: 200, text: "Decisions are append-only and audited: an override adds a row. A human decision always wins." },
  ],
};

const TABS: SceneDef = {
  step: "11",
  title: "Review — AI decisions, straight-lining, addresses",
  beats: [
    { img: "28-review-ai", h: 1280, dur: 100, cam: [cam(0, 960, 540, 1.15)], hl: [{ b: [747, 222, 107, 40], from: 10, label: "AI decisions" }] },
    {
      img: "29-review-straight", h: 1280, dur: 100, cam: [cam(0, 960, 480, 1.3)],
      hl: [{ b: [856, 222, 128, 40], from: 5, to: 45 }, { b: [611, 397, 960, 142], from: 40, label: "data-quality flag only" }],
    },
    {
      img: "30-review-identities", h: 1280, dur: 100, cam: [cam(0, 960, 480, 1.3)],
      hl: [{ b: [986, 222, 177, 40], from: 5, to: 45 }, { b: [611, 397, 960, 92], from: 40, label: "email not on roster" }],
    },
  ],
  lines: [
    { from: 0, text: "AI decisions lists every model call with its reasoning so staff can sample it; an override supersedes it for good." },
    { from: 100, text: "Straight-lining: identical confidence four sessions running. A data-quality flag, never a count or score." },
    { from: 200, text: "Unresolved addresses: check-ins from emails not on the roster are listed here, never guessed at." },
  ],
};

const SHOUTOUTS: SceneDef = {
  step: "12",
  title: "Shoutouts — linking names",
  beats: [
    {
      img: "31-shoutouts", h: 2497, dur: 150,
      cam: [cam(0, 960, 540, 1), cam(70, 1050, 480, 1.35)],
      hl: [{ b: [640, 278, 180, 30], from: 5, to: 50, label: "cohort filter" }, { b: [1291, 450, 264, 68], from: 60, label: "roster candidates" }],
      ...click([1291, 450, 264, 32], 70, 125),
    },
    { img: "32-shoutout-linked", h: 2501, dur: 90, cam: [cam(0, 960, 500, 1.2)] },
  ],
  lines: [
    { from: 0, text: "Names fellows typed for “who helped you” that matched nobody, or more than one person. Pick the right one to link." },
    { from: 150, text: "Guest speakers and teachers get thanked too and stay unlinked. Third-party names are protected data." },
  ],
};

const HELP: SceneDef = {
  step: "13",
  title: "Help requests — access-gated",
  beats: [
    {
      img: "34-help-note", h: 1433, dur: 190,
      cam: [cam(0, 960, 540, 1), cam(80, 960, 820, 1.35)],
      hl: [{ b: [647, 776, 888, 129], from: 60, to: 120, label: "a staff note, not the fellow’s words" }, { b: [647, 866, 186, 40], from: 120, label: "I’m picking this up" }],
      ...click([647, 866, 186, 40], 120, 170),
    },
    {
      img: "36-help-acknowledged", h: 1280, dur: 100,
      cam: [cam(0, 960, 800, 1.3)],
      hl: [{ b: [647, 836, 888, 129], from: 5, to: 50, label: "picked up" }, { b: [647, 926, 75, 40], from: 50, label: "Close when done" }],
    },
  ],
  lines: [
    { from: 0, text: "Fellows who ticked “I’d like someone to check in with me”. Only the people named for it can open this screen." },
    { from: 120, text: "Add a note on what you did, then pick it up. Close it when handled." },
    { from: 190, text: "A help request never lowers participation and appears in no report, export, count or score." },
  ],
};

const DASHBOARD: SceneDef = {
  step: "14",
  title: "Staff dashboard",
  beats: [
    {
      img: "50-dashboard", h: 5000, dur: 330,
      cam: [cam(0, 960, 540, 1), cam(60, 960, 520, 1.15), cam(140, 960, 1150, 1.15), cam(220, 960, 1650, 1.15), cam(320, 960, 2000, 1.15)],
      hl: [
        { b: [627, 335, 928, 345], from: 10, to: 70, label: "attendance, fellows, requests, unrostered" },
        { b: [779, 272, 126, 40], from: 70, to: 130, label: "Export CSV" },
        { b: [627, 945, 928, 440], from: 140, to: 220, label: "needs a human — incl. Slack /checkin" },
        { b: [627, 1457, 928, 300], from: 225, to: 330, label: "attention index, with its reasons" },
        { b: [815, 2071, 141, 32], from: 290, label: "Reached out" },
      ],
    },
  ],
  lines: [
    { from: 0, text: "New on main: /dashboard. Overall attendance, active fellows, open check-in requests and unrostered Slack accounts." },
    { from: 70, text: "Export CSV downloads the engagement table. “Last data in” shows when each source last produced anything." },
    { from: 140, text: "Needs a human: check-in requests (including ones pressed in Slack) and roster alerts." },
    { from: 225, text: "Fellows sorted by attention index, parts always shown. Help requests and assignment scores never enter it." },
  ],
};

const FELLOW: SceneDef = {
  step: "15",
  title: "Fellow page, assignments and roster",
  beats: [
    {
      img: "51-fellow", h: 2502, dur: 150,
      cam: [cam(0, 960, 540, 1), cam(70, 960, 600, 1.15), cam(140, 960, 1200, 1.1)],
      hl: [{ b: [835, 428, 193, 32], from: 20, to: 90, label: "Mark reached out" }],
    },
    {
      img: "52-assignments", h: 1280, dur: 90,
      cam: [cam(0, 960, 500, 1.2)],
      hl: [{ b: [611, 340, 960, 86], from: 5, to: 50, label: "assignments with reminders" }, { b: [627, 284, 173, 40], from: 45 }],
      ...click([627, 284, 173, 40], 40, 75),
    },
    {
      img: "53-assignment-new", h: 1280, dur: 100,
      cam: [cam(0, 960, 560, 1.25)],
      hl: [{ b: [656, 276, 870, 24], from: 5, to: 60, label: "title, due, link, status" }, { b: [647, 867, 176, 40], from: 60 }],
    },
    {
      img: "54-roster", h: 2522, dur: 130,
      cam: [cam(0, 960, 540, 1), cam(70, 960, 560, 1.2)],
      hl: [{ b: [627, 270, 928, 230], from: 5, to: 70, label: "load a roster CSV" }, { b: [1339, 595, 216, 32], from: 70, label: "per-fellow timezone" }],
    },
  ],
  lines: [
    { from: 0, text: "A fellow’s page: what they see, plus attention index, aliases, interventions and airtime. Mark reached out here." },
    { from: 150, text: "Assignments: create and edit Solvathons and case briefs. Fellows get reminders 24 h, 1 h and 10 min before." },
    { from: 340, text: "Roster: load fellows from CSV and set each fellow’s timezone so reminders land at the right local time." },
  ],
};

const SCREENS1 = [SIGNIN, CONNECT, TEMPLATE, ROTATION, SESSIONS, BLOCK, PROVISION, LESSON, RESPONSES, REVIEW, TABS, SHOUTOUTS, HELP, DASHBOARD, FELLOW];

/* ---------------- Slack ---------------- */

const X = (k: string, dur: number, extra: Partial<SlackExchange> = {}): SlackExchange => ({ ...SLACK[k], dur, ...extra });

const SLACK_STAFF: { step: string; title: string; ex: SlackExchange[]; lines: CaptionLine[] } = {
  step: "16",
  title: "Slack — staff slash commands",
  ex: [
    X("report", 150),
    X("fellow", 160),
    X("attendance", 150),
    X("leaderboard", 120),
    X("asg_create", 130),
    X("asg_list", 100),
    X("score", 100),
    X("zoom", 110),
    X("outreach", 100),
    X("alias", 120),
    X("alerts", 120),
    X("link", 100),
    X("alerts_resolve", 90),
    X("digest", 90),
    X("sync", 90),
    X("admindash", 110),
  ],
  lines: [],
};
{
  const text: Record<string, string> = {
    report: "`/report` — the cohort so far, and when each data source last produced anything.",
    fellow: "`/fellow` — one fellow’s card: attendance, activity vs. cohort mean, attention index and why, badges, funnel.",
    attendance: "`/attendance next` — who checked in, who filled the exit ticket, who is missing.",
    leaderboard: "`/leaderboard` — a staff-only ranking. Fellows never see it.",
    asg_create: "`/assignment create` — an assignment with automatic reminders…",
    asg_list: "…and `/assignment list` shows what is due, handed in and scored.",
    score: "`/score` records a Solvathon or case-brief score you gave by hand.",
    zoom: "`/zoom next <link>` puts the Zoom link on the next session; it rides in every reminder.",
    outreach: "`/outreach` marks that someone has reached out, with who and when.",
    alias: "`/alias` adds a second address to one roster record — history re-attributes, past included.",
    alerts: "`/alerts` lists Slack accounts that joined but are not on the roster.",
    link: "`/link` attaches a Slack account to a fellow…",
    alerts_resolve: "…or `/alerts resolve` marks it as staff or ignored.",
    digest: "`/digest` posts the weekly digest now…",
    sync: "…and `/sync` pulls members, channels and messages from Slack now.",
    admindash: "`/admin-dashboard` links the staff dashboard. The password is never sent over Slack.",
  };
  let at = 0;
  const keys = ["report", "fellow", "attendance", "leaderboard", "asg_create", "asg_list", "score", "zoom", "outreach", "alias", "alerts", "link", "alerts_resolve", "digest", "sync", "admindash"];
  SLACK_STAFF.ex.forEach((e, i) => {
    SLACK_STAFF.lines.push({ from: at, text: text[keys[i]] });
    at += e.dur;
  });
}

const SLACK_REFUSE = {
  step: "17",
  title: "Slack — not staff? Refused.",
  ex: [
    X("refused", 130, { who: "Ardith Aldergrove", channel: "general" }),
    X("checkin", 130, { who: "Ardith Aldergrove", channel: "general" }),
  ],
  lines: [
    { from: 0, text: "Staff commands check CUFA_SLACK_ADMINS. A fellow who tries `/report` is refused, and told why." },
    { from: 130, text: "Fellows do have `/checkin`: it pings staff privately. It lands in the staff channel and on the dashboard." },
  ],
};

const SlackScene: React.FC<{ def: { step: string; title: string; ex: SlackExchange[]; lines: CaptionLine[] } }> = ({ def }) => (
  <AbsoluteFill style={{ background: theme.bg }}>
    <Series>
      {def.ex.map((e, i) => (
        <Series.Sequence key={i} durationInFrames={e.dur}>
          <SlackPane ex={e} />
        </Series.Sequence>
      ))}
    </Series>
    <Caption step={def.step} title={def.title} lines={def.lines} />
    <CornerPill>Real bot reply · via cufa slack cmd</CornerPill>
  </AbsoluteFill>
);
const slackLen = (d: { ex: SlackExchange[] }) => d.ex.reduce((a, e) => a + e.dur, 0);

const WORKSPACE: SceneDef = {
  step: "18",
  title: "The private staff channel, in the fake Slack",
  beats: [
    {
      img: "61-fake-slack", h: 5000, dur: 520,
      cam: [
        cam(0, 960, 540, 1),
        cam(40, 860, 1880, 1.35),
        cam(130, 860, 1800, 1.35),
        cam(220, 860, 1540, 1.35),
        cam(320, 860, 1270, 1.35),
        cam(400, 860, 950, 1.2),
        cam(470, 860, 1390, 1.35),
        cam(510, 860, 1390, 1.35),
      ],
      hl: [
        { b: [415, 1955, 871, 42], from: 50, to: 120, label: "/checkin → #cohort-private" },
        { b: [415, 1652, 871, 128], from: 120, to: 210, label: "session summary" },
        { b: [415, 1420, 871, 232], from: 210, to: 300, label: "weekly digest" },
        { b: [415, 1195, 871, 160], from: 330, to: 400, label: "“asked before” pointer in #q-and-a" },
        { b: [415, 707, 871, 468], from: 400, to: 470, label: "@bot summary for the teacher" },
        { b: [415, 1370, 871, 44], from: 470, label: "poll posted by the bot" },
      ],
    },
  ],
  lines: [
    { from: 0, text: "This is the fake Slack workspace wired to the real bot. Everything under “What the bot posted” is real output." },
    { from: 50, text: "A fellow’s /checkin lands in the private staff channel, with who to follow up and how." },
    { from: 120, text: "`cufa slack tick` posts the session summary after each session ends: check-ins, exit tickets, who is missing." },
    { from: 210, text: "The Monday digest: this week, what is due, who has gone quiet, and who needs a human that nobody has reached." },
    { from: 330, text: "In #q-and-a a repeated question gets a pointer to the earlier answer…" },
    { from: 400, text: "…and “@bot summary” posts the session’s Q&A digest for the teacher. No names in it." },
    { from: 470, text: "`cufa slack poll` posts a poll; fellows vote with buttons and `cufa slack polls` shows the totals." },
  ],
};

const PRIVACY: SceneDef = {
  step: "19",
  title: "Slack privacy boundaries",
  beats: [
    {
      img: "60-bot-status", h: 1280, dur: 170,
      cam: [cam(0, 960, 540, 1), cam(60, 960, 300, 1.4)],
      hl: [{ b: [512, 65, 896, 20], from: 20, to: 170, label: "text stored: no" }],
    },
    {
      img: "61-fake-slack", h: 5000, dur: 150,
      cam: [cam(0, 700, 420, 1.2), cam(60, 700, 1100, 1.2)],
      hl: [{ b: [415, 119, 871, 475], from: 5, to: 60, label: "every delivery: signed, 200" }, { b: [43, 1010, 315, 240], from: 60, label: "replay + bad-signature tests" }],
    },
  ],
  lines: [
    { from: 0, text: "The bot records that a message happened — who, where, when, length — never the text. Status says so." },
    { from: 90, text: "It never subscribes to or stores direct messages. Only named Q&A channels keep text, in their own tables." },
    { from: 170, text: "Every delivery is signature-checked; replays are dropped and forged requests are rejected." },
  ],
};

/* ---------------- CLI ---------------- */

const CliScene: React.FC = () => (
  <AbsoluteFill style={{ background: theme.bg }}>
    <Sequence durationInFrames={210}>
      <Terminal blocks={[{ at: 10, cmd: "cufa slack doctor", out: DOCTOR }]} />
    </Sequence>
    <Sequence from={210} durationInFrames={180}>
      <Terminal blocks={[{ at: 5, cmd: "cufa report --cohort demo", out: REPORT }]} />
    </Sequence>
    <Sequence from={390} durationInFrames={170}>
      <Terminal blocks={[{ at: 5, cmd: "cufa slack engagement", out: ENGAGEMENT }]} />
    </Sequence>
    <Sequence from={560}>
      <Terminal
        blocks={[
          { at: 5, cmd: "curl …/dashboard/export.csv?cohort=demo | head -5", out: EXPORT },
          { at: 70, cmd: "curl …/sessions/template.csv", out: TEMPLATE_CSV },
        ]}
      />
    </Sequence>
    <Caption
      step="20"
      title="Preflight and the command line"
      lines={[
        { from: 0, text: "`cufa slack doctor` checks tokens, scopes, channels, admins and the database before a first run." },
        { from: 210, text: "Everything in the console is also a command: `cufa report` gives the cohort’s attendance picture." },
        { from: 390, text: "`cufa slack engagement` prints the attention index table; `--json` for scripts." },
        { from: 560, text: "The dashboard’s CSV export and the sessions CSV template are plain files — real output shown." },
      ]}
    />
  </AbsoluteFill>
);
const CLI_DUR = 720;

const real = (d: SceneDef): [React.FC, number] => {
  const S: React.FC = () => <RealScene def={d} />;
  return [S, sceneLength(d)];
};
const slack = (d: typeof SLACK_STAFF): [React.FC, number] => {
  const S: React.FC = () => <SlackScene def={d} />;
  return [S, slackLen(d)];
};

const SCENES: [React.FC, number][] = [
  [TitleScene, 120],
  ...SCREENS1.map(real),
  slack(SLACK_STAFF),
  slack(SLACK_REFUSE),
  real(WORKSPACE),
  real(PRIVACY),
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
      .then(() => {
        console.log("FONTS loaded", document.fonts.check("900 40px Nunito"), document.fonts.size);
        continueRender(handle);
      })
      .catch((e) => {
        console.error(e);
        continueRender(handle);
      });
  }, [handle]);
};

export const AdminGuide: React.FC = () => {
  useFonts();
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
