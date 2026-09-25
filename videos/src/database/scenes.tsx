// The database walkthrough's teaching scenes. Diagrams are drawn from the real
// migrations (supabase/migrations/*.sql); every terminal shows real psql output
// from the demo database (./sqlOutput.ts); screens are real console captures.
import React from "react";
import { Sequence, useCurrentFrame } from "remotion";
import { tokens } from "../theme";
import { Caption, K, Pill, fadeIn, usePop } from "../admin/ui";
import { Arrow, Ev, Frame, SceneSounds, Screen, Show, SqlBlock, SqlTerminal, Sticker, Tag, cam, lineAt, mono, outAt, outDone } from "./kit";
import * as Q from "./sqlOutput";

const ORANGE = "#ff9600";
const PURPLE = "#ce82ff";
const RED = "#ff4b4b";

/** Heading pill pinned top-left of the viewport. */
const Heading: React.FC<{ at?: number; children: React.ReactNode }> = ({ at = 4, children }) => (
  <div style={{ position: "absolute", left: 80, top: 62 }}>
    <Pill at={at} size={22}>
      {children}
    </Pill>
  </div>
);

const Corner: React.FC<{ at?: number; children: React.ReactNode }> = ({ at = 6, children }) => (
  <div style={{ position: "absolute", right: 60, top: 58 }}>
    <Pill at={at} size={17} color={tokens.pencil}>
      {children}
    </Pill>
  </div>
);

/* =====================================================================
   1. The big picture
   ===================================================================== */

const Node: React.FC<{ x: number; y: number; w: number; h: number; at: number; color: string; title: string; sub: string }> = ({ x, y, w, h, at, color, title, sub }) => (
  <Sticker x={x} y={y} w={w} h={h} at={at} edge={color}>
    <div style={{ fontFamily: tokens.display, fontWeight: 900, fontSize: 38, color, lineHeight: 1.1 }}>{title}</div>
    <div style={{ fontSize: 24, fontWeight: 600, color: tokens.pencil, marginTop: 8, lineHeight: 1.25 }}>{sub}</div>
  </Sticker>
);

const Cylinder: React.FC<{ at: number }> = ({ at }) => {
  const p = usePop(at);
  const f = useCurrentFrame();
  const x = 740;
  const y = 170;
  const w = 440;
  const h = 470;
  const ry = 46;
  return (
    <div style={{ position: "absolute", left: x, top: y, width: w, height: h + ry, opacity: Math.min(1, p * 1.5), transform: `scale(${0.85 + 0.15 * p})` }}>
      <svg width={w} height={h + ry} style={{ position: "absolute", left: 0, top: 0, overflow: "visible" }}>
        <path d={`M2 ${ry} L2 ${h} A${w / 2 - 2} ${ry} 0 0 0 ${w - 2} ${h} L${w - 2} ${ry}`} fill={tokens.paper} stroke={tokens.eagerGreen} strokeWidth={4} />
        <path d={`M2 ${h} A${w / 2 - 2} ${ry} 0 0 0 ${w - 2} ${h}`} fill="none" stroke={K.greenEdge} strokeWidth={8} />
        <ellipse cx={w / 2} cy={ry} rx={w / 2 - 2} ry={ry - 2} fill={tokens.storybookGreen} stroke={tokens.eagerGreen} strokeWidth={4} />
      </svg>
      <div style={{ position: "absolute", left: 0, top: ry * 2 + 30, width: w, textAlign: "center" }}>
        <div style={{ fontFamily: tokens.display, fontWeight: 900, fontSize: 64, color: tokens.eagerGreen, lineHeight: 1 }}>Postgres</div>
        <div style={{ fontSize: 30, fontWeight: 700, color: tokens.charcoal, marginTop: 10 }}>hosted on Supabase</div>
        <div style={{ ...mono, fontSize: 24, color: tokens.pencil, marginTop: 34, opacity: fadeIn(f, at + 20, 10) }}>43 tables · 15 views</div>
        <div style={{ ...mono, fontSize: 24, color: tokens.pencil, marginTop: 8, opacity: fadeIn(f, at + 30, 10) }}>19 migrations</div>
      </div>
    </div>
  );
};

export const BIG_DUR = 450;
export const BigPictureScene: React.FC = () => {
  const src = [
    { t: "Google Forms", s: "Part A exit ticket · Part B check-in", c: tokens.sparkBlue },
    { t: "Slack", s: "who posted, where, when — not what", c: PURPLE },
    { t: "Roster", s: "CU’s list: fellows, emails, cohort", c: tokens.eagerGreen },
  ];
  const dst = [
    { t: "Staff console", s: "Dashboard · Review · Sessions", c: tokens.sparkBlue },
    { t: "Slack bot", s: "reminders · digests · summaries", c: PURPLE },
    { t: "Reports", s: "cufa report · CSV exports", c: ORANGE },
  ];
  const ys = [140, 355, 570];
  const srcAt = [22, 36, 50];
  const dstAt = [170, 184, 198];
  const evs: Ev[] = [
    ...srcAt.map((at) => ({ at, name: "pop" as const })),
    { at: 100, name: "pop", volume: 1 },
    ...dstAt.map((at) => ({ at, name: "pop" as const })),
    { at: 290, name: "pop" },
  ];
  const f = useCurrentFrame();
  return (
    <Frame>
      <Heading>One database for the whole programme</Heading>
      {src.map((n, i) => (
        <Node key={n.t} x={100} y={ys[i]} w={450} h={160} at={srcAt[i]} color={n.c} title={n.t} sub={n.s} />
      ))}
      {src.map((n, i) => (
        <Arrow key={`a${i}`} a={[560, ys[i] + 80]} b={[735, 400 + (i - 1) * 60]} at={64 + i * 6} color={n.c} />
      ))}
      <Cylinder at={100} />
      {dst.map((n, i) => (
        <Arrow key={`b${i}`} a={[1190, 400 + (i - 1) * 60]} b={[1365, ys[i] + 80]} at={140 + i * 6} color={n.c} />
      ))}
      {dst.map((n, i) => (
        <Node key={n.t} x={1370} y={ys[i]} w={450} h={160} at={dstAt[i]} color={n.c} title={n.t} sub={n.s} />
      ))}
      <div style={{ position: "absolute", left: 960, top: 745, transform: "translateX(-50%)", opacity: fadeIn(f, 290, 8) }}>
        <Pill at={290} size={24} color={tokens.eagerGreen} style={{ borderColor: tokens.eagerGreen }}>
          cohort_id on every row
        </Pill>
      </div>
      <Caption
        step="1"
        title="The big picture"
        lines={[
          { from: 0, text: "Everything the fellowship records ends up in one place: a single Postgres database, hosted on Supabase." },
          { from: 100, text: "Three things feed it: Google Forms responses, activity from the Slack workspace, and CU’s roster." },
          { from: 170, text: "Three things read it: the staff console, the Slack bot, and reports with their CSV exports." },
          { from: 290, text: "Every row belongs to a cohort, so next year can be compared with this year instead of mixed into it." },
        ]}
      />
      <SceneSounds evs={evs} dur={BIG_DUR} />
    </Frame>
  );
};

/* =====================================================================
   2. The tables, grouped
   ===================================================================== */

type Tbl = { name: string; cols: string };
type Group = { key: string; label: string; note: string; color: string; x: number; y: number; h: number; at: number; tables: Tbl[] };

const CW = 533;
const COLX = [60, 693, 1326];
const TOP_Y = 60;
const TOP_H = 350;
const BOT_Y = 460;
const BOT_H = 366;
const ROW_H = 66;
const HEAD_H = 52;

const GROUPS: Group[] = [
  {
    key: "ref", label: "Reference", note: "who and when", color: tokens.eagerGreen, x: COLX[0], y: TOP_Y, h: TOP_H, at: 120,
    tables: [
      { name: "cohort", cols: "cohort_id · label · starts_on" },
      { name: "fellow", cols: "fellow_id · cohort_id · primary_email · status" },
      { name: "fellow_alias", cols: "fellow_id · email · kind" },
      { name: "session", cols: "session_id · cohort_id · scheduled_at_utc" },
    ],
  },
  {
    key: "forms", label: "Forms", note: "what was asked", color: tokens.sparkBlue, x: COLX[1], y: TOP_Y, h: TOP_H, at: 235,
    tables: [
      { name: "form_template", cols: "form_id · part · is_active" },
      { name: "session_form", cols: "session_id · part · form_id · question_set_id" },
      { name: "part_a_question_set", cols: "version · content · superseded_at" },
      { name: "part_a_form_question", cols: "form_id · question_id → question_key" },
    ],
  },
  {
    key: "obs", label: "Observations", note: "immutable", color: tokens.nightInk, x: COLX[1], y: BOT_Y, h: BOT_H, at: 350,
    tables: [
      { name: "checkin", cols: "Part A exit ticket: submitted_email · submitted_at_utc · form_id · answers" },
      { name: "checkin_b", cols: "Part B: confidence_raw · takeaway_text · rotating_text" },
      { name: "slack_event", cols: "event_type · channel_id · text_length · word_count" },
    ],
  },
  {
    key: "dec", label: "Decisions", note: "append-only", color: ORANGE, x: COLX[0], y: BOT_Y, h: BOT_H, at: 465,
    tables: [{ name: "attendance_decision", cols: "checkin_id · status · decided_by · rule_name · human_email · superseded_at" }],
  },
  {
    key: "care", label: "People care", note: "own tables, own rules", color: PURPLE, x: COLX[2], y: BOT_Y, h: BOT_H, at: 580,
    tables: [
      { name: "help_request", cols: "fellow_id · session_id · status · acknowledged_by" },
      { name: "peer_shoutout", cols: "checkin_b_id · named_fellow_id · match_method" },
    ],
  },
  {
    key: "bot", label: "Bot & assignments", note: "operations", color: tokens.charcoal, x: COLX[2], y: TOP_Y, h: TOP_H, at: 695,
    tables: [
      { name: "assignment", cols: "title · kind · due_at_utc · link" },
      { name: "assignment_submission", cols: "fellow_id · score · graded_by" },
      { name: "intervention", cols: "fellow_id · kind · by_email" },
      { name: "bot_delivery", cols: "dedupe_key · kind · status" },
    ],
  },
];

const groupNotes: Record<string, string> = {
  dec: "Exactly one current decision per check-in, enforced by a unique index. Every row names a rule or a person.",
  care: "Neither feeds any attendance number. Help requests are read by no view at all.",
};

const GroupCard: React.FC<{ g: Group }> = ({ g }) => {
  const f = useCurrentFrame();
  const tall = g.y === BOT_Y;
  return (
    <Sticker x={g.x} y={g.y} w={CW} h={g.h} at={g.at} edge={g.color} style={{ padding: "12px 20px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, height: HEAD_H - 8 }}>
        <Tag color={g.color} size={20}>
          {g.label}
        </Tag>
        <span style={{ fontSize: 20, fontWeight: 700, color: tokens.pencil }}>{g.note}</span>
      </div>
      {g.tables.map((t, i) => {
        const o = fadeIn(f, g.at + 10 + i * 7, 8);
        return (
          <div key={t.name} style={{ height: tall ? 96 : ROW_H, opacity: o, transform: `translateX(${(1 - o) * -16}px)`, borderTop: i ? `2px solid ${tokens.hairline}` : undefined, paddingTop: 6, boxSizing: "border-box" }}>
            <div style={{ ...mono, fontSize: 23, fontWeight: 700, color: tokens.charcoal, lineHeight: 1.15 }}>{t.name}</div>
            <div style={{ ...mono, fontSize: 16, color: tokens.pencil, lineHeight: 1.3, marginTop: 3 }}>{t.cols}</div>
          </div>
        );
      })}
      {groupNotes[g.key] ? (
        <div style={{ marginTop: 12, fontSize: 21, fontWeight: 600, color: tokens.charcoal, lineHeight: 1.3, opacity: fadeIn(f, g.at + 30, 10) }}>{groupNotes[g.key]}</div>
      ) : null}
    </Sticker>
  );
};

/** A key that links two groups, drawn as a short bar with its column name. */
const Link: React.FC<{ a: [number, number]; b: [number, number]; at: number; label: string; color: string; lx: number; ly: number }> = ({ a, b, at, label, color, lx, ly }) => {
  const f = useCurrentFrame();
  const p = usePop(at + 8);
  return (
    <>
      <Arrow a={a} b={b} at={at} len={12} color={color} dot={false} width={5} />
      <div
        style={{
          position: "absolute",
          left: lx,
          top: ly,
          transform: `translate(-50%, -50%) scale(${0.6 + 0.4 * p})`,
          opacity: fadeIn(f, at + 6, 6),
          background: tokens.paper,
          border: `2px solid ${color}`,
          borderRadius: 999,
          padding: "2px 10px",
          ...mono,
          fontSize: 15,
          fontWeight: 700,
          color,
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </div>
    </>
  );
};

export const TABLES_DUR = 1150;
const COUNTS_AT = 920;
export const TablesScene: React.FC = () => {
  const LINK_AT = 810;
  const obs = GROUPS.find((g) => g.key === "obs")!;
  const counts: SqlBlock = { at: 12, cmd: "select 'cohort' as table_name, count(*) as rows from cohort union all … ;", out: Q.COUNTS, speed: 0.4 };
  const evs: Ev[] = [
    ...GROUPS.map((g) => ({ at: g.at, name: "pop" as const })),
    { at: LINK_AT + 8, name: "pop" },
    { at: LINK_AT + 26, name: "pop" },
    { at: LINK_AT + 44, name: "pop" },
    { at: COUNTS_AT, name: "whoosh", volume: 0.6 },
    { at: COUNTS_AT + outAt(counts), name: "pop", volume: 0.7 },
  ];
  // row centre of a table inside the Observations card
  const obsRow = (i: number) => obs.y + 12 + HEAD_H - 8 + i * 96 + 30;
  return (
    <Frame>
      <Show from={0} to={COUNTS_AT + 6}>
        <div style={{ position: "absolute", left: 0, top: 0, width: 1920, height: 1080 }}>
          {GROUPS.map((g) => (
            <GroupCard key={g.key} g={g} />
          ))}
          {/* checkin → attendance_decision */}
          <Link a={[COLX[1] - 2, obsRow(0)]} b={[COLX[0] + CW + 4, obsRow(0)]} at={LINK_AT} label="checkin_id" color={ORANGE} lx={(COLX[0] + CW + COLX[1]) / 2} ly={obsRow(0) - 26} />
          {/* checkin_b → peer_shoutout */}
          <Link a={[COLX[1] + CW + 2, obsRow(1)]} b={[COLX[2] - 4, obsRow(1)]} at={LINK_AT + 18} label="checkin_b_id" color={PURPLE} lx={(COLX[1] + CW + COLX[2]) / 2} ly={obsRow(1) - 26} />
          {/* session_form / part_a_form_question → checkin */}
          <Link a={[COLX[1] + CW / 2, TOP_Y + TOP_H + 4]} b={[COLX[1] + CW / 2, BOT_Y - 4]} at={LINK_AT + 36} label="form_id · session_id" color={tokens.sparkBlue} lx={COLX[1] + CW / 2 + 150} ly={(TOP_Y + TOP_H + BOT_Y) / 2 + 2} />
        </div>
      </Show>
      <Sequence from={COUNTS_AT} durationInFrames={TABLES_DUR - COUNTS_AT} layout="none">
        <SqlTerminal blocks={[counts]} />
      </Sequence>
      <Caption
        step="2"
        title="The tables, grouped"
        lines={[
          { from: 0, text: "The 19 migrations in supabase/migrations build 43 tables. Staff meet these 18, in six groups." },
          { from: 120, text: "Reference: each cohort, its fellows (plus any extra email addresses they use), and every live session." },
          { from: 235, text: "Forms: the Google Form templates, each session’s copies, and the exit-ticket questions, kept as versions." },
          { from: 350, text: "Observations: what arrived, as it arrived. Part A exit tickets, Part B check-ins, and Slack activity." },
          { from: 465, text: "Decisions: one table, `attendance_decision`, holding the judgment about each check-in and who made it." },
          { from: 580, text: "People care: help requests and peer shoutouts, each kept in a table of its own, with its own rules." },
          { from: 695, text: "Bot and assignments: due dates, staff-entered scores, who reached out to a fellow, every reminder sent." },
          { from: LINK_AT, text: "The groups link by id: a decision points at its check-in, a shoutout at the Part B check-in it came from." },
          { from: COUNTS_AT, text: "Real counts from the demo: 20 fellows, 12 sessions, 103 check-ins, 103 decisions. No Slack data is loaded yet." },
        ]}
      />
      <SceneSounds evs={evs} dur={TABLES_DUR} />
    </Frame>
  );
};

/* =====================================================================
   3. Observed vs decided
   ===================================================================== */

const Bullet: React.FC<{ at: number; children: React.ReactNode; color: string }> = ({ at, children, color }) => {
  const f = useCurrentFrame();
  const o = fadeIn(f, at, 10);
  return (
    <div style={{ display: "flex", gap: 16, alignItems: "flex-start", marginTop: 20, opacity: o, transform: `translateY(${(1 - o) * 10}px)` }}>
      <div style={{ width: 14, height: 14, borderRadius: 4, background: color, marginTop: 13, flexShrink: 0 }} />
      <div style={{ fontSize: 31, fontWeight: 600, color: tokens.charcoal, lineHeight: 1.3 }}>{children}</div>
    </div>
  );
};

const B_IMM = 250;
const B_OVR = 500;
const B_GRD = 800;
export const OBS_DUR = 1060;

const IMM: SqlBlock = { at: 12, cmd: "begin;\nupdate checkin set answers = '{}' where checkin_id = 'be2264fd-…';\nrollback;", out: Q.IMMUTABLE, speed: 0.6 };
const OVR: SqlBlock = {
  at: 10,
  speed: 0.26,
  cmd:
    "begin;\n" +
    "update attendance_decision set superseded_at = now()\n where checkin_id = 'c45e77c5-…' and superseded_at is null;\n" +
    "insert into attendance_decision (checkin_id, attended, status, confidence, decided_by, human_email)\n" +
    "values ('c45e77c5-…', true, 'attended', 1.0, 'human', 'staff.demo@example.invalid');\n" +
    "select decided_by, status, coalesce(rule_name, human_email) as by_whom, confidence,\n" +
    "       superseded_at is not null as superseded\n  from attendance_decision where checkin_id = 'c45e77c5-…' order by created_at;\n" +
    "rollback;",
  out: Q.OVERRIDE,
};
const GRD: SqlBlock = {
  at: 10,
  speed: 0.3,
  cmd:
    "begin;\n" +
    "insert into attendance_decision (checkin_id, attended, status, decided_by)\nvalues ('c45e77c5-…', true, 'attended', 'human');\n" +
    "rollback;\nbegin;\n" +
    "insert into attendance_decision (checkin_id, attended, status, decided_by, human_email)\nvalues ('c45e77c5-…', true, 'attended', 'human', 'staff.demo@example.invalid');\n" +
    "rollback;",
  out: Q.GUARDS,
};

export const ObservedScene: React.FC = () => {
  const evs: Ev[] = [
    { at: 24, name: "pop" },
    { at: 90, name: "pop" },
    { at: 150, name: "pop" },
    { at: B_IMM, name: "whoosh", volume: 0.6 },
    { at: B_IMM + lineAt(IMM, "ERROR"), name: "error" },
    { at: B_OVR, name: "whoosh", volume: 0.6 },
    { at: B_OVR + lineAt(OVR, "human      |") + 4, name: "success" },
    { at: B_GRD, name: "whoosh", volume: 0.6 },
    { at: B_GRD + lineAt(GRD, "duplicate key") + 4, name: "success" },
  ];
  const f = useCurrentFrame();
  return (
    <Frame>
      <Show from={0} to={B_IMM + 6}>
        <Heading>Two kinds of data, kept apart</Heading>
        <Sticker x={100} y={150} w={780} h={500} at={24} edge={tokens.nightInk}>
          <Tag color={tokens.nightInk} size={22}>Observed</Tag>
          <div style={{ ...mono, fontSize: 26, fontWeight: 700, color: tokens.charcoal, marginTop: 16 }}>checkin · checkin_b · slack_event</div>
          <Bullet at={40} color={tokens.nightInk}>What arrived, exactly as it arrived</Bullet>
          <Bullet at={56} color={tokens.nightInk}>Written once: a database trigger refuses every edit and every delete</Bullet>
          <Bullet at={72} color={tokens.nightInk}>Keeps the email typed or verified; the fellow is looked up when read</Bullet>
        </Sticker>
        <Arrow a={[890, 400]} b={[1030, 400]} at={80} color={tokens.faded} />
        <div style={{ position: "absolute", left: 960, top: 345, transform: "translateX(-50%)", fontSize: 22, fontWeight: 800, color: tokens.pencil, opacity: fadeIn(f, 84, 8), ...K.label }}>judged</div>
        <Sticker x={1040} y={150} w={780} h={500} at={90} edge={ORANGE}>
          <Tag color={ORANGE} size={22}>Decided</Tag>
          <div style={{ ...mono, fontSize: 26, fontWeight: 700, color: tokens.charcoal, marginTop: 16 }}>attendance_decision</div>
          <Bullet at={106} color={ORANGE}>attended · not attended · needs review</Bullet>
          <Bullet at={122} color={ORANGE}>Every row names who decided: a rule by name, or a person by address</Bullet>
          <Bullet at={138} color={ORANGE}>A new decision supersedes the old one, and the old one stays</Bullet>
          <Bullet at={154} color={ORANGE}>Exactly one current decision per check-in</Bullet>
        </Sticker>
      </Show>
      <Sequence from={B_IMM} durationInFrames={B_OVR - B_IMM} layout="none">
        <SqlTerminal blocks={[IMM]} marks={[{ match: "ERROR", from: lineAt(IMM, "ERROR"), color: RED }]} title="psql — cufa_demo_pa (demo data) · inside BEGIN … ROLLBACK" />
      </Sequence>
      <Sequence from={B_OVR} durationInFrames={B_GRD - B_OVR} layout="none">
        <SqlTerminal
          blocks={[OVR]}
          title="psql — cufa_demo_pa (demo data) · inside BEGIN … ROLLBACK"
          marks={[
            { match: "rule       | needs_review", from: lineAt(OVR, "rule       |") + 10, color: tokens.sparkBlue },
            { match: "human      | attended", from: lineAt(OVR, "human      |") + 4 },
          ]}
        />
      </Sequence>
      <Sequence from={B_GRD} durationInFrames={OBS_DUR - B_GRD} layout="none">
        <SqlTerminal
          blocks={[GRD]}
          title="psql — cufa_demo_pa (demo data) · inside BEGIN … ROLLBACK"
          marks={[
            { match: "decision_provenance_present", from: lineAt(GRD, "decision_provenance_present"), color: RED },
            { match: "attendance_decision_one_current", from: lineAt(GRD, "one_current"), color: RED },
          ]}
        />
      </Sequence>
      <Caption
        step="3"
        title="Observed vs decided"
        lines={[
          { from: 0, text: "The database keeps two kinds of thing apart: what was observed, and what was decided about it." },
          { from: 100, text: "A check-in is a fact about what arrived. A decision is a judgment, and it always says who made it." },
          { from: B_IMM, text: "Try to change a check-in’s answers and the database itself refuses, whoever asks." },
          { from: B_IMM + 110, text: "Only `latency_seconds`, a number worked out from the session, may ever be recomputed. Nothing here was saved." },
          { from: B_OVR, text: "A human override, run the way the console runs it, in a transaction we roll back. The demo has no overrides yet." },
          { from: B_OVR + 170, text: "The rule’s needs_review row stays, marked superseded. The human’s decision is now current, with their address." },
          { from: B_GRD, text: "Two more guards: a human decision with no address is rejected, and a check-in can’t have two current decisions." },
        ]}
      />
      <SceneSounds evs={evs} dur={OBS_DUR} />
    </Frame>
  );
};

/* =====================================================================
   4. The exit ticket in data
   ===================================================================== */

const FlowNode: React.FC<{ x: number; y: number; w: number; h: number; at: number; color: string; name: string; sub: string; chip?: string }> = ({ x, y, w, h, at, color, name, sub, chip }) => (
  <Sticker x={x} y={y} w={w} h={h} at={at} edge={color}>
    <div style={{ ...mono, fontSize: 25, fontWeight: 700, color }}>{name}</div>
    <div style={{ fontSize: 25, fontWeight: 600, color: tokens.charcoal, marginTop: 8, lineHeight: 1.3 }}>{sub}</div>
    {chip ? (
      <div style={{ display: "inline-block", marginTop: 12, ...mono, fontSize: 19, color: tokens.sparkBlue, background: K.blueWash, borderRadius: 10, padding: "3px 12px" }}>{chip}</div>
    ) : null}
  </Sticker>
);

const E_QS = 290;
const E_RAW = 510;
const E_ANS = 720;
export const EXIT_DUR = 980;
const QS: SqlBlock = {
  at: 10,
  speed: 0.25,
  cmd:
    "select coalesce(s.title, '(cohort default)') as scope, q.version, q.source,\n       q.created_by, q.superseded_at is not null as superseded,\n       jsonb_array_length(q.content -> 'questions') as questions\n  from part_a_question_set q left join \"session\" s on s.session_id = q.session_id\n where q.cohort_id = 'demo' order by scope, q.version;",
  out: Q.QSETS,
};
const RAW: SqlBlock = {
  at: 10,
  speed: 0.3,
  cmd: "select a.key as question_id, a.value as answer from checkin c cross join lateral jsonb_each(c.answers) a\n where c.checkin_id = 'be2264fd-…' limit 4;",
  out: Q.RAW,
};
const ANS: SqlBlock = {
  at: 10,
  speed: 0.35,
  cmd: "select item_index, question_key, kind, answer_values, has_content\n  from v_checkin_answer where checkin_id = 'be2264fd-…' order by item_index;",
  out: Q.ANSWER,
};

export const ExitTicketScene: React.FC = () => {
  const evs: Ev[] = [
    { at: 24, name: "pop" },
    { at: 50, name: "pop" },
    { at: 76, name: "pop" },
    { at: 150, name: "pop" },
    { at: 200, name: "pop" },
    { at: E_QS, name: "whoosh", volume: 0.6 },
    { at: E_QS + outAt(QS), name: "pop", volume: 0.7 },
    { at: E_RAW, name: "whoosh", volume: 0.6 },
    { at: E_RAW + outAt(RAW), name: "pop", volume: 0.7 },
    { at: E_ANS, name: "whoosh", volume: 0.6 },
    { at: E_ANS + outDone(ANS), name: "success", volume: 0.8 },
  ];
  return (
    <Frame>
      <Show from={0} to={E_QS + 6}>
        <Heading>From question to answer</Heading>
        <FlowNode x={90} y={130} w={530} h={230} at={24} color={tokens.sparkBlue} name="part_a_question_set" sub="The questions, as versions. An edit adds a version; none is overwritten." />
        <Arrow a={[630, 245]} b={[690, 245]} at={44} color={tokens.sparkBlue} dot={false} />
        <FlowNode x={695} y={130} w={530} h={230} at={50} color={tokens.sparkBlue} name="session_form" sub="Which version each session’s form was built from." />
        <Arrow a={[1235, 245]} b={[1295, 245]} at={70} color={tokens.sparkBlue} dot={false} />
        <FlowNode x={1300} y={130} w={530} h={230} at={76} color={tokens.sparkBlue} name="part_a_form_question" sub="Question id → question, read back from each form." chip="q0000e → q_session_rating" />
        <FlowNode x={90} y={470} w={620} h={250} at={150} color={tokens.nightInk} name="checkin.answers" sub="Raw answers on the immutable check-in, keyed by question id." chip={'"q0000e": {"values": ["4"]}'} />
        <Arrow a={[720, 595]} b={[1290, 595]} at={180} color={tokens.nightInk} />
        <Arrow a={[1565, 370]} b={[1565, 465]} at={190} color={tokens.sparkBlue} />
        <FlowNode x={1300} y={470} w={530} h={250} at={200} color={tokens.eagerGreen} name="v_checkin_answer" sub="Joins the two when read: one row per check-in and question." chip="q_session_rating = {4}" />
      </Show>
      <Sequence from={E_QS} durationInFrames={E_RAW - E_QS} layout="none">
        <SqlTerminal
          blocks={[QS]}
          marks={[
            { match: "(cohort default)             |       1", from: outDone(QS) + 10, color: tokens.sparkBlue },
            { match: "(cohort default)             |       2", from: outDone(QS) + 40 },
          ]}
        />
      </Sequence>
      <Sequence from={E_RAW} durationInFrames={E_ANS - E_RAW} layout="none">
        <SqlTerminal blocks={[RAW]} marks={[{ match: "q0000e", from: outDone(RAW) + 10, color: tokens.sparkBlue }]} />
      </Sequence>
      <Sequence from={E_ANS} durationInFrames={EXIT_DUR - E_ANS} layout="none">
        <SqlTerminal blocks={[ANS]} marks={[{ match: "q_session_rating", from: outDone(ANS) + 10 }]} />
      </Sequence>
      <Caption
        step="4"
        title="The exit ticket in data"
        lines={[
          { from: 0, text: "Part A is the exit ticket. Staff write its questions, and the database keeps them as data, not as settings." },
          { from: 100, text: "Every form records which question id means which question, read back from Google rather than assumed." },
          { from: 180, text: "A response is stored raw, under question ids. It is matched to its question only when someone reads it." },
          { from: E_QS, text: "Real versions: the cohort default is on v2 and v1 is kept, marked superseded. Two sessions have their own set." },
          { from: E_RAW, text: "One fellow’s raw answers, exactly as the Forms API returned them: a question id, its title, the values." },
          { from: E_ANS, text: "`v_checkin_answer` resolves each id to a named question. A question left blank would still get a row." },
        ]}
      />
      <SceneSounds evs={evs} dur={EXIT_DUR} />
    </Frame>
  );
};

/* =====================================================================
   5. Privacy guarantees
   ===================================================================== */

const P_SLACK = 280;
const P_COUNT = 620;
const P_RLS = 860;
export const PRIV_DUR = 1120;
const TW = 1320;

const HELP: SqlBlock = {
  at: 10,
  speed: 0.3,
  cmd:
    "select count(*) from information_schema.views\n where view_definition ilike '%help_request%';\n" +
    "select has_table_privilege('anon', 'help_request', 'select') as anon_reads,\n       has_table_privilege('authenticated', 'help_request', 'select') as signed_in_reads;",
  out: Q.HELP,
};
const SLACK_D: SqlBlock = { at: 10, cmd: "\\d slack_event", out: Q.SLACK_D, speed: 1 };
const SLACK_C: SqlBlock = { at: SLACK_D.at + 60, cmd: "select obj_description('slack_event'::regclass);", out: Q.SLACK_C, speed: 0.4 };
const COUNTED: SqlBlock = {
  at: 10,
  speed: 0.22,
  cmd:
    "select a.question_key, count(*) filter (where a.has_content) as answered, count(*) as responses\n  from v_checkin_answer a join \"session\" s on s.session_id = a.session_id\n where s.cohort_id = 'demo' and s.week_index = 3\n group by a.item_index, a.question_key order by a.item_index;",
  out: Q.COUNTED,
};
const COUNTED_C: SqlBlock = { at: 150, cmd: "select obj_description('v_checkin_answer'::regclass);", out: Q.COUNTED_C, speed: 0.4 };
const RLS: SqlBlock = {
  at: 10,
  speed: 0.25,
  cmd: "select relname as table_name, relrowsecurity as rls_on from pg_class\n where relname in ('fellow','checkin','checkin_b','attendance_decision',\n                   'help_request','peer_shoutout','slack_event')\n order by 1;",
  out: Q.RLS,
};
const POL: SqlBlock = {
  at: 120,
  speed: 0.25,
  cmd: "select tablename, policyname, roles, qual from pg_policies\n where tablename in ('fellow','checkin','attendance_decision') order by 1;",
  out: Q.POLICIES,
};

const PROMISES = [
  { t: "The help box is isolated", p: "0 views read help_request, and signed-in roles can’t read it", start: 0 },
  { t: "Slack text is not stored", p: "length and word count only · DMs are never subscribed to", start: P_SLACK },
  { t: "Free text is counted, never graded", p: "answered or not, and how many; nothing is scored", start: P_COUNT },
  { t: "RLS is on for fellow data", p: "signed-in reads are denied until CU writes its rules", start: P_RLS },
];
const PASS = [HELP, SLACK_C, COUNTED_C, POL].map((b, i) => [0, P_SLACK, P_COUNT, P_RLS][i] + outDone(b) + 4);

const Promise: React.FC<{ i: number; y: number }> = ({ i, y }) => {
  const f = useCurrentFrame();
  const pr = PROMISES[i];
  const active = f >= pr.start && (i === 3 || f < PROMISES[i + 1].start);
  const done = f >= PASS[i];
  const p = usePop(PASS[i]);
  const col = done ? tokens.eagerGreen : active ? tokens.sparkBlue : tokens.hairline;
  return (
    <Sticker x={1390} y={y} w={490} h={168} at={12 + i * 14} edge={col} style={{ padding: "14px 18px" }}>
      <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 12,
            flexShrink: 0,
            background: done ? tokens.eagerGreen : tokens.paper,
            border: `2px solid ${done ? K.greenEdge : col}`,
            color: done ? tokens.paper : tokens.pencil,
            fontSize: done ? 28 : 24,
            fontWeight: 900,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            transform: done ? `scale(${0.6 + 0.4 * p})` : undefined,
          }}
        >
          {done ? "✓" : i + 1}
        </div>
        <div>
          <div style={{ fontSize: 26, fontWeight: 800, color: tokens.charcoal, lineHeight: 1.15 }}>{pr.t}</div>
          <div style={{ fontSize: 20, fontWeight: 600, color: tokens.pencil, marginTop: 6, lineHeight: 1.3 }}>{pr.p}</div>
        </div>
      </div>
    </Sticker>
  );
};

export const PrivacyScene: React.FC = () => {
  const evs: Ev[] = [
    { at: 26, name: "pop" },
    { at: 40, name: "pop" },
    { at: 54, name: "pop" },
    { at: P_SLACK, name: "whoosh", volume: 0.6 },
    { at: P_COUNT, name: "whoosh", volume: 0.6 },
    { at: P_RLS, name: "whoosh", volume: 0.6 },
    ...PASS.map((at) => ({ at, name: "success" as const })),
  ];
  const tprops = { w: TW, fontSize: 17, lineH: 21 };
  return (
    <Frame>
      <Sequence durationInFrames={P_SLACK} layout="none">
        <SqlTerminal {...tprops} blocks={[HELP]} marks={[{ match: "     0", from: outDone(HELP), color: tokens.eagerGreen }, { match: " f          | f", from: outDone(HELP) + 6 }]} />
      </Sequence>
      <Sequence from={P_SLACK} durationInFrames={P_COUNT - P_SLACK} layout="none">
        <SqlTerminal
          {...tprops}
          blocks={[SLACK_D, SLACK_C]}
          marks={[
            { match: " text_length", from: outDone(SLACK_D) + 4 },
            { match: " word_count", from: outDone(SLACK_D) + 4 },
            { match: " text            |", from: outDone(SLACK_D) + 14, color: tokens.sparkBlue },
          ]}
        />
      </Sequence>
      <Sequence from={P_COUNT} durationInFrames={P_RLS - P_COUNT} layout="none">
        <SqlTerminal {...tprops} blocks={[COUNTED, COUNTED_C]} marks={[{ match: "q_budget_parts", from: outDone(COUNTED) + 6 }, { match: "q_other_feedback", from: outDone(COUNTED) + 6 }]} />
      </Sequence>
      <Sequence from={P_RLS} durationInFrames={PRIV_DUR - P_RLS} layout="none">
        <SqlTerminal {...tprops} blocks={[RLS, POL]} marks={[{ match: "| false", from: outDone(POL) + 4 }]} />
      </Sequence>
      <div style={{ position: "absolute", left: 1390, top: 44 }}>
        <Pill at={4} size={20}>
          Four promises, checked
        </Pill>
      </div>
      {PROMISES.map((_, i) => (
        <Promise key={i} i={i} y={100 + i * 184} />
      ))}
      <Caption
        step="5"
        title="Privacy guarantees, with proof"
        lines={[
          { from: 0, text: "Four privacy promises, each checked against the real database rather than taken on trust." },
          { from: 70, text: "The “check in with me” box lands in `help_request` only. No view reads that table, and signed-in roles cannot." },
          { from: P_SLACK, text: "Slack: the bot records that a message happened, with its length and word count. The `text` column stays empty." },
          { from: P_SLACK + 170, text: "It never reads direct messages: it subscribes to channel messages only, and `cufa\u00a0slack\u00a0doctor` fails on DM scopes." },
          { from: P_COUNT, text: "Free text is counted, never graded. For week 3 the database knows 8 of 11 answered one question, not how well." },
          { from: P_RLS, text: "Row Level Security is on for fellow data. Until CU writes its access rules, the policies let signed-in users read nothing." },
        ]}
      />
      <SceneSounds evs={evs} dur={PRIV_DUR} />
    </Frame>
  );
};

/* =====================================================================
   6. How staff look at it
   ===================================================================== */

const S_REV = 240;
const S_CSV = 480;
const S_SUP = 680;
export const STAFF_DUR = 930;
const CSV1: SqlBlock = { at: 10, prompt: "sh", cmd: "curl -s …/dashboard/export.csv?cohort=demo | head -4 | cut -c1-150", out: Q.EXPORT, speed: 0.6 };
const CSV2: SqlBlock = { at: 110, prompt: "sh", cmd: "curl -s …/dashboard/export.csv?cohort=demo | head -1 | tr , '\\n' | grep -c help", out: Q.EXPORT_HELP, speed: 0.6 };

export const StaffScene: React.FC = () => {
  const evs: Ev[] = [
    { at: 60, name: "whoosh", volume: 0.7 },
    { at: 70, name: "pop", volume: 0.9 },
    { at: 130, name: "pop", volume: 0.9 },
    { at: S_REV, name: "whoosh", volume: 0.6 },
    { at: S_REV + 40, name: "pop", volume: 0.9 },
    { at: S_REV + 110, name: "pop", volume: 0.9 },
    { at: S_CSV, name: "whoosh", volume: 0.6 },
    { at: S_CSV + outAt(CSV1), name: "pop", volume: 0.7 },
    { at: S_CSV + outDone(CSV2), name: "success" },
    { at: S_SUP, name: "whoosh", volume: 0.6 },
    { at: S_SUP + 20, name: "pop" },
    { at: S_SUP + 40, name: "pop" },
    { at: S_SUP + 90, name: "pop" },
  ];
  const f = useCurrentFrame();
  return (
    <Frame>
      <Sequence durationInFrames={S_REV} layout="none">
        <Screen
          img="dashboard"
          dur={S_REV}
          cams={[cam(0, 960, 540, 1), cam(60, 1090, 470, 1.3)]}
          hl={[
            { b: [627, 336, 458, 176], from: 70, to: 130, label: "overall attendance" },
            { b: [779, 272, 126, 40], from: 130, label: "Export CSV" },
          ]}
        />
      </Sequence>
      <Sequence from={S_REV} durationInFrames={S_CSV - S_REV} layout="none">
        <Screen
          img="review"
          dur={S_CSV - S_REV}
          cams={[cam(0, 960, 540, 1), cam(40, 1090, 520, 1.3)]}
          hl={[
            { b: [627, 222, 590, 44], from: 40, to: 110, label: "review queues" },
            { b: [611, 432, 960, 132], from: 110, label: "needs_review: waiting for a person" },
          ]}
        />
      </Sequence>
      <Show from={0} to={S_CSV}>
        <Corner>Real screen · demo data · fake-Google mode</Corner>
      </Show>
      <Sequence from={S_CSV} durationInFrames={S_SUP - S_CSV} layout="none">
        <SqlTerminal blocks={[CSV1, CSV2]} title="bash — console on :8200, same demo database" marks={[{ match: "0", exact: true, from: outDone(CSV2), color: tokens.eagerGreen }]} />
      </Sequence>
      <Show from={S_SUP}>
        <Heading at={S_SUP}>In Supabase Studio · described, not captured</Heading>
        <Sticker x={100} y={140} w={840} h={320} at={S_SUP + 20} edge={tokens.sparkBlue}>
          <Tag color={tokens.sparkBlue} size={22}>Table editor</Tag>
          <div style={{ fontSize: 34, fontWeight: 700, color: tokens.charcoal, marginTop: 20, lineHeight: 1.3 }}>
            Browse any table like a spreadsheet: <span style={mono}>checkin</span>, <span style={mono}>attendance_decision</span>, <span style={mono}>provisioning_log</span> and the views.
          </div>
          <div style={{ fontSize: 26, fontWeight: 600, color: tokens.pencil, marginTop: 18 }}>No query and no connection string needed.</div>
        </Sticker>
        <Sticker x={980} y={140} w={840} h={320} at={S_SUP + 40} edge={tokens.eagerGreen}>
          <Tag color={tokens.eagerGreen} size={22}>SQL editor</Tag>
          <div style={{ fontSize: 34, fontWeight: 700, color: tokens.charcoal, marginTop: 20, lineHeight: 1.3 }}>
            Paste a ready-made query from <span style={mono}>docs/setup/local-dev.md</span>: current decisions, the review queue, Part A answers.
          </div>
          <div style={{ fontSize: 26, fontWeight: 600, color: tokens.pencil, marginTop: 18 }}>The same queries this video ran.</div>
        </Sticker>
        <Sticker x={100} y={500} w={1720} h={110} at={S_SUP + 90} edge={ORANGE}>
          <div style={{ fontSize: 32, fontWeight: 700, color: tokens.charcoal, lineHeight: 1.35, opacity: fadeIn(f, S_SUP + 90, 8) }}>
            Studio connects with a privileged role, so it shows every row. It is not what the RLS policies protect against.
          </div>
        </Sticker>
      </Show>
      <Caption
        step="6"
        title="How staff look at it"
        lines={[
          { from: 0, text: "Staff don’t have to write SQL. The console’s Dashboard reads these same tables: attendance, fellows, when data last came in." },
          { from: 130, text: "Export CSV downloads the dashboard’s fellow table as a plain file." },
          { from: S_REV, text: "Review lists every check-in whose current decision is needs_review. One click records a human decision." },
          { from: S_CSV, text: "The export is real. Its header has no help-request column: `grep -c help` finds nothing." },
          { from: S_SUP, text: "In Supabase, the Table editor browses tables like a spreadsheet, and the SQL editor runs the queries in local-dev.md." },
          { from: S_SUP + 120, text: "Studio connects with a privileged role, so it shows every row: the RLS policies are aimed at other connections." },
        ]}
      />
      <SceneSounds evs={evs} dur={STAFF_DUR} />
    </Frame>
  );
};
