import React from "react";
import { AbsoluteFill } from "remotion";
import { theme, tokens } from "../theme";
import { Check, Display, Pill, Rise, Scene } from "./ui";
import { Shot } from "./Shot";
import { Msg, SlackWindow, Typed } from "./Slack";

/* All bot text below is verbatim output from the running bot on origin/main
   (fake Slack state, `cufa slack cmd`, and reminders.py's own text builders). */

const WELCOME =
  "Hi Ardith 👋 I'm the fellowship bot.\n• I'll DM you a reminder 24 hours, 1 hour and 10 minutes before each session, with the Zoom link, and before each assignment is due. `/reminders` changes or stops them.\n• I keep track of badges and streaks for you, privately. `/badges` shows them; `/badges off` stops the messages.\n• `/me` shows your own attendance and activity; `/dashboard` gives you a private link with an export button.\n• Unsure about something, or want a staff member to reach out? Press the button below or type `/checkin`.\nPlease use your real name on Zoom so your attendance is recorded.";
const ZOOM = "<https://zoom.example.invalid/j/123456|Join Zoom>";
const R24 = `Hi Ardith, reminder: *Session 1 — What a civic problem is* starts in 24 hours (Sun, Sep 27 at 7:00 PM EDT). ${ZOOM}`;
const R1 = `Hi Ardith, reminder: *Session 1 — What a civic problem is* starts in 1 hour (Sun, Sep 27 at 7:00 PM EDT). ${ZOOM}`;
const R10 = `Hi Ardith, reminder: *Session 1 — What a civic problem is* starts in 10 minutes (Sun, Sep 27 at 7:00 PM EDT). ${ZOOM}`;
const RA = "Hi Ardith, *Solvathon pitch* is due in 24 hours (Tue, Oct 20 at 6:00 PM EDT).";
const NUDGE =
  "Hi Ardith, I couldn't find a Part B check-in for *Session 1 — What a civic problem is*. If you still want to add yours, here's the form: <https://forms|Complete Part B>. If you already sent it, you're all set; responses can take a moment to sync.";
const HELP =
  "*What I can do*\n`/reminders` — see or change when you get session and assignment reminders (`/reminders session 10m off`, `/reminders all off`)\n`/badges` — your badges and streak (`/badges off` to stop badge messages)\n`/checkin [note]` — ask a staff member to check in with you\n`/mystats` — your attendance, exit tickets and Slack activity\n`/dashboard` — a private link to the same, with an export button";
const ME =
  "*Ardith Aldergrove*\nSessions attended: 2/11 (+2 under review)\nExit tickets: 4/11\nSlack messages: 0 (last 7 days: 0)\n_`/dashboard` for the full view and an export._";
const DASH =
  "Your dashboard (private link, valid for 7 days): <http://127.0.0.1:8000/me/eyJmZWxsb3dfaWQiOiJDVS0yNjAwIn0…|http://127.0.0.1:8000/me/eyJmZWxsb3dfaWQiOiJDVS0yNjAwIn0…>";
const BADGES =
  "*Your badges, Ardith Aldergrove*\n🙌 *Recogniser* ★★☆ — Gave 1, 3 and 6 shoutouts\n✅ *First check-in* ★ — Checked in to a live session\n📝 *Reflective* ★☆☆ — Filled in 3, 6 and 9 exit tickets\n\nStreak: 0 sessions in a row · Check-ins: 2/11 · Exit tickets: 4 · Messages: 0 · Shoutouts given: 5\n_`/badges off` stops these messages._";
const BADGE_DM =
  "🎉 New badge, Sabra Stonebrook!\n✅ *First check-in* ★ — Checked in to a live session\n_`/badges` shows them all · `/badges off` stops these messages._";
const BADGES_OFF = "Badge messages are now *off*. You can turn them back on with `/badges on`.";
const REMINDERS =
  "*Your reminders*\nSession reminders: 24 hours, 1 hour, 10 minutes\nAssignment reminders: 24 hours, 1 hour, 10 minutes\nBadges and streaks: on\n\n`/reminders session 10m off` · `/reminders assignment on` · `/reminders all off`";
const CHECKIN_REPLY = "Done — a staff member has been pinged and will reach out.";
const REPEAT =
  "👋 This looks like a question that came up before, during *Sep 25 · Demo session (today)*. The reply that was marked ✅ is here: <https://x|see the answer>.\n_If it's a different question, carry on — a person will answer here._";

const Header: React.FC<{ pill: string; title: string }> = ({ pill, title }) => (
  <Rise delay={0} style={{ display: "flex", alignItems: "center", gap: 28 }}>
    <Pill color={tokens.sparkBlue}>{pill}</Pill>
    <Display>{title}</Display>
  </Rise>
);

const Pts: React.FC<{ items: { at: number; t: React.ReactNode }[] }> = ({ items }) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
    {items.map((it, i) => (
      <Rise key={i} delay={it.at} style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
        <div style={{ marginTop: 4 }}>
          <Check start={it.at} size={48} />
        </div>
        <div style={{ fontSize: 40, lineHeight: 1.25, color: tokens.pencil, fontWeight: 500 }}>{it.t}</div>
      </Rise>
    ))}
  </div>
);
const Q: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span style={{ color: theme.accent, fontWeight: 800 }}>{children}</span>
);

const Layout: React.FC<{ pill: string; title: string; left: React.ReactNode; right: React.ReactNode; leftW?: number }> = ({
  pill,
  title,
  left,
  right,
  leftW = 700,
}) => (
  <AbsoluteFill style={{ padding: "56px 60px" }}>
    <Header pill={pill} title={title} />
    <div style={{ display: "flex", gap: 50, marginTop: 36 }}>
      <div style={{ width: leftW, flexShrink: 0 }}>{left}</div>
      <div>{right}</div>
    </div>
  </AbsoluteFill>
);

/* ---------- Welcome DM ---------- */
export const WELCOME_DUR = 360;
export const WelcomeScene: React.FC = () => (
  <Scene dur={WELCOME_DUR}>
    <Layout
      pill="SLACK"
      title="Meet the fellowship bot"
      left={
        <Pts
          items={[
            { at: 30, t: <>When you join Slack, the bot sends you <Q>one welcome DM</Q>.</> },
            { at: 110, t: <>It explains every command you can use.</> },
            { at: 200, t: <>Want someone to reach out? Press <Q>Check in with me</Q>, any time.</> },
          ]}
        />
      }
      right={
        <SlackWindow title="CUFA participation bot" subtitle="Direct message">
          <Msg at={15} who="CUFA participation bot" bot time="9:02 AM" text={WELCOME} button={{ label: "Check in with me", pressAt: 240 }} />
        </SlackWindow>
      }
    />
  </Scene>
);

/* ---------- Reminders ---------- */
export const REMIND_DUR = 480;
export const RemindScene: React.FC = () => (
  <Scene dur={REMIND_DUR}>
    <Layout
      pill="SLACK"
      title="Reminders, by private DM"
      left={
        <Pts
          items={[
            { at: 20, t: <>Sessions: <Q>24 hours, 1 hour and 10 minutes</Q> before, with the Zoom link.</> },
            { at: 150, t: <>Assignments: before they&apos;re due.</> },
            { at: 220, t: <>Times are shown in <Q>your own timezone</Q>.</> },
            { at: 290, t: <>Quiet hours (21:00–08:00 by default): an overnight reminder is <Q>skipped</Q>, not sent late.</> },
            { at: 360, t: <>Missed Part B? You may get up to two gentle nudges.</> },
          ]}
        />
      }
      right={
        <SlackWindow title="CUFA participation bot" subtitle="Direct message">
          <Msg at={30} who="CUFA participation bot" bot time="Sat 7:00 PM" text={R24} size={23} />
          <Msg at={70} who="CUFA participation bot" bot time="Sun 6:00 PM" text={R1} size={23} />
          <Msg at={110} who="CUFA participation bot" bot time="Sun 6:50 PM" text={R10} size={23} />
          <Msg at={160} who="CUFA participation bot" bot time="Mon 6:00 PM" text={RA} size={23} />
          <Msg at={370} who="CUFA participation bot" bot time="Sun 9:05 PM" text={NUDGE} size={23} />
        </SlackWindow>
      }
    />
  </Scene>
);

/* ---------- /help and /me, /reminders ---------- */
export const CMDS_DUR = 480;
export const CommandsScene: React.FC = () => (
  <Scene dur={CMDS_DUR}>
    <Layout
      pill="SLACK"
      title="Your commands"
      leftW={620}
      left={
        <Pts
          items={[
            { at: 20, t: <><Q>/help</Q> lists what you can do.</> },
            { at: 130, t: <><Q>/me</Q> shows your own attendance, exit tickets and messages.</> },
            { at: 270, t: <><Q>/reminders</Q> shows and changes your reminders, e.g. <Q>/reminders all off</Q>.</> },
            { at: 360, t: <>Replies are <Q>only visible to you</Q>.</> },
          ]}
        />
      }
      right={
        <SlackWindow title="# general" width={1140}>
          <Typed at={10} who="Ardith" cmd="/help" />
          <Msg at={40} who="CUFA participation bot" bot ephemeral text={HELP} size={19} />
          <Msg at={140} who="CUFA participation bot" bot ephemeral text={ME} size={19} />
          <Msg at={280} who="CUFA participation bot" bot ephemeral text={REMINDERS} size={19} />
        </SlackWindow>
      }
    />
  </Scene>
);

/* ---------- /dashboard and the real /me page ---------- */
export const DASH_DUR = 480;
export const DashboardScene: React.FC = () => (
  <Scene dur={DASH_DUR}>
    <AbsoluteFill style={{ padding: "56px 60px" }}>
      <Header pill="SLACK" title="/dashboard: your private page" />
      <div style={{ display: "flex", gap: 40, marginTop: 30 }}>
        <div style={{ width: 760 }}>
          <SlackWindow title="# general" width={760}>
            <Typed at={10} who="Ardith" cmd="/dashboard" />
            <Msg at={40} who="CUFA participation bot" bot ephemeral text={DASH} size={20} />
          </SlackWindow>
          <div style={{ marginTop: 30 }}>
            <Pts
              items={[
                { at: 90, t: <>Shows <Q>your own data and nobody else&apos;s</Q>.</> },
                { at: 170, t: <>Link lasts 7 days. Ask <Q>/dashboard</Q> for a new one.</> },
                { at: 250, t: <>Flip reminders on/off, and <Q>Export my data (CSV)</Q>.</> },
              ]}
            />
          </div>
        </div>
        <div style={{ position: "relative", width: 1020, height: 880 }}>
          <Rise delay={60} style={{ position: "absolute", top: 0, left: 0 }}>
            <Shot file="me.png" crop={{ x: 480, y: 60, w: 960, h: 680 }} width={1000} label="REAL SCREEN · your /me page" />
          </Rise>
          <Rise delay={240} style={{ position: "absolute", top: 380, left: 20 }}>
            <Shot file="me3.png" crop={{ x: 480, y: 670, w: 960, h: 390 }} width={1000} />
          </Rise>
        </div>
      </div>
    </AbsoluteFill>
  </Scene>
);

/* ---------- Badges ---------- */
export const BADGE_DUR = 420;
export const BadgesScene: React.FC = () => (
  <Scene dur={BADGE_DUR}>
    <Layout
      pill="SLACK"
      title="Badges stay private"
      leftW={640}
      left={
        <Pts
          items={[
            { at: 20, t: <>New badges arrive <Q>by private DM</Q>. They&apos;re never posted publicly.</> },
            { at: 130, t: <><Q>/badges</Q> shows yours, your streak and counts.</> },
            { at: 280, t: <>Rather not get them? <Q>/badges off</Q>. Turn back on with <Q>/badges on</Q>.</> },
          ]}
        />
      }
      right={
        <SlackWindow title="CUFA participation bot" subtitle="Direct message" width={1120}>
          <Msg at={20} who="CUFA participation bot" bot time="8:14 PM" text={BADGE_DM} size={21} />
          <Typed at={120} who="Ardith" cmd="/badges" />
          <Msg at={145} who="CUFA participation bot" bot ephemeral text={BADGES} size={21} />
          <Typed at={270} who="Ardith" cmd="/badges off" />
          <Msg at={300} who="CUFA participation bot" bot ephemeral text={BADGES_OFF} size={21} />
        </SlackWindow>
      }
    />
  </Scene>
);

/* ---------- /checkin ---------- */
export const CHECKIN_DUR = 330;
export const CheckinScene: React.FC = () => (
  <Scene dur={CHECKIN_DUR}>
    <Layout
      pill="SLACK"
      title="/checkin: ask for a staff check-in"
      left={
        <Pts
          items={[
            { at: 20, t: <>Type <Q>/checkin</Q>, with a note if you like.</> },
            { at: 110, t: <>A staff member is pinged <Q>privately</Q> and reaches out to you.</> },
            { at: 190, t: <>Same as the button in your welcome DM. It never counts against you.</> },
          ]}
        />
      }
      right={
        <SlackWindow title="# general">
          <Typed at={10} who="Ardith" cmd="/checkin I'm finding the budget reading hard this week" />
          <Msg at={120} who="CUFA participation bot" bot ephemeral text={CHECKIN_REPLY} />
        </SlackWindow>
      }
    />
  </Scene>
);

/* ---------- #q-and-a ---------- */
export const QA_DUR = 510;
export const QAScene: React.FC = () => (
  <Scene dur={QA_DUR}>
    <Layout
      pill="SLACK"
      title="Ask in #q-and-a"
      leftW={640}
      left={
        <Pts
          items={[
            { at: 20, t: <>Post your question in <Q>#q-and-a</Q>.</> },
            { at: 90, t: <>Anyone can answer <Q>in the thread</Q>.</> },
            { at: 170, t: <>Got your answer? React <Q>✅</Q> to mark it.</> },
            { at: 260, t: <>Asked before? The bot links the earlier answer. Different question? Carry on and a person will answer.</> },
          ]}
        />
      }
      right={
        <SlackWindow title="# q-and-a" width={1120}>
          <Msg at={20} who="Ardith Aldergrove" time="4:52 PM" text="How do I find the budget line items for a city department?" size={22} />
          <Msg at={90} who="Bexley Brambleton" time="4:52 PM" indent text="The city open-data portal has the adopted budget by department. Search the department name there." reaction={{ emoji: "✅", at: 180 }} size={22} />
          <Msg at={260} who="Corvin Cinderwick" time="4:53 PM" text="How do I find the budget line items for a city department?" size={22} />
          <Msg at={300} who="CUFA participation bot" bot time="4:53 PM" indent text={REPEAT} size={22} />
        </SlackWindow>
      }
    />
  </Scene>
);

/* ---------- Polls ---------- */
export const POLL_DUR = 390;
export const PollScene: React.FC = () => (
  <Scene dur={POLL_DUR}>
    <Layout
      pill="SLACK"
      title="Polls"
      leftW={660}
      left={
        <Pts
          items={[
            { at: 20, t: <>Vote by pressing an option.</> },
            { at: 100, t: <>Changed your mind? Press another. <Q>Only your latest vote counts.</Q></> },
            { at: 190, t: <>The tally is <Q>never shown live</Q> in the channel.</> },
            { at: 260, t: <>Staff see totals, <Q>not who voted for what</Q>.</> },
          ]}
        />
      }
      right={
        <div>
          <SlackWindow title="# general" width={1080}>
            <Msg
              at={10}
              who="CUFA participation bot"
              bot
              time="4:50 PM"
              text="Poll: Which evening works for the budget office hours?"
              buttons={[
                { label: "Tuesday", pressAt: [140] },
                { label: "Thursday", pressAt: [60] },
              ]}
            />
          </SlackWindow>
          <Rise delay={200} style={{ marginTop: 30 }}>
            <Shot file="slack_top.png" crop={{ x: 400, y: 115, w: 900, h: 142 }} width={1080} label="REAL LOG · the bot received Delphine's vote, then her change" />
          </Rise>
        </div>
      }
    />
  </Scene>
);

/* ---------- Slack privacy ---------- */
export const SPRIV_DUR = 390;
export const SlackPrivacyScene: React.FC = () => {
  const cards = [
    { h: "The bot can't read your DMs", b: "It has no permission to, and messages sent to the bot are switched off." },
    { h: "Message text isn't stored", b: "Only counts: length, word count, links. The exception: #q-and-a, where questions and answers are kept to point repeats at answers." },
    { h: "No email addresses", b: "Nothing the bot posts shows anyone's address." },
    { h: "Only you see your stats", b: "/me, /badges and /dashboard show your own data, to you alone." },
  ];
  return (
    <Scene dur={SPRIV_DUR}>
      <AbsoluteFill style={{ padding: "56px 90px" }}>
        <Header pill="SLACK" title="What the bot does and doesn't see" />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 30, marginTop: 44 }}>
          {cards.map((c, i) => (
            <Rise key={c.h} delay={30 + i * 55} distance={60}>
              <div style={{ backgroundColor: "#fff", border: "2px solid #e5e5e5", borderRadius: 14, padding: "30px 38px", borderColor: "#1cb0f6", borderBottomWidth: 6, height: 350 }}>
                <div style={{ fontSize: 46, fontWeight: 800, color: tokens.charcoal }}>{c.h}</div>
                <div style={{ fontSize: 36, color: theme.muted, marginTop: 14, lineHeight: 1.3 }}>{c.b}</div>
              </div>
            </Rise>
          ))}
        </div>
      </AbsoluteFill>
    </Scene>
  );
};
