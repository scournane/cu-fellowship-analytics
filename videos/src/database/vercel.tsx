// Why Vercel Pro. Plan facts are ONLY the ones below, as of September 2026, per
// vercel.com/docs/plans; the scheduler facts come from deploy/vercel/README.md.
import React from "react";
import { useCurrentFrame } from "remotion";
import { tokens } from "../theme";
import { Caption, K, Pill, fadeIn, usePop } from "../admin/ui";
import { Ev, Frame, SceneSounds, Show, Sticker, Tag, mono } from "./kit";

const ORANGE = "#ff9600";
const RED = "#ff4b4b";

const V_TICK = 330;
const V_SEAT = 700;
export const VERCEL_DUR = 1060;

const Row: React.FC<{ at: number; ok: boolean; children: React.ReactNode }> = ({ at, ok, children }) => {
  const p = usePop(at);
  return (
    <div style={{ display: "flex", gap: 18, alignItems: "flex-start", marginTop: 24, opacity: Math.min(1, p * 1.5), transform: `translateX(${(1 - p) * -20}px)` }}>
      <div
        style={{
          width: 44,
          height: 44,
          borderRadius: 12,
          flexShrink: 0,
          background: ok ? tokens.eagerGreen : tokens.paper,
          border: `2px solid ${ok ? K.greenEdge : RED}`,
          color: ok ? tokens.paper : RED,
          fontSize: 26,
          fontWeight: 900,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginTop: 2,
        }}
      >
        {ok ? "✓" : "✕"}
      </div>
      <div style={{ fontSize: 31, fontWeight: 700, color: tokens.charcoal, lineHeight: 1.3 }}>{children}</div>
    </div>
  );
};

const Heading: React.FC<{ at: number; children: React.ReactNode }> = ({ at, children }) => (
  <div style={{ position: "absolute", left: 80, top: 62 }}>
    <Pill at={at} size={22}>
      {children}
    </Pill>
  </div>
);

/* ---------- beat B: the reminder timeline ---------- */
const AX0 = 470;
const AX1 = 1790;
const MARKS = [
  { x: 640, label: "24 h before" },
  { x: 1300, label: "1 h before" },
  { x: 1600, label: "10 min before" },
];
const START_X = 1740;

const Lane: React.FC<{ y: number; at: number; label: string; sub: string; ticks: number[]; ok: boolean[]; color: string }> = ({ y, at, label, sub, ticks, ok, color }) => {
  const f = useCurrentFrame();
  const o = fadeIn(f, at, 10);
  return (
    <div style={{ position: "absolute", left: 0, top: 0, opacity: o }}>
      <div style={{ position: "absolute", left: 110, top: y - 34, width: 340 }}>
        <div style={{ fontSize: 28, fontWeight: 800, color }}>{label}</div>
        <div style={{ fontSize: 20, fontWeight: 600, color: tokens.pencil }}>{sub}</div>
      </div>
      <div style={{ position: "absolute", left: AX0, top: y - 1, width: AX1 - AX0, height: 3, background: tokens.hairline }} />
      {ticks.map((x, i) => {
        const t = fadeIn(f, at + 6 + (i / ticks.length) * 30, 4);
        return <div key={i} style={{ position: "absolute", left: x - 2, top: y - 14, width: 4, height: 28, borderRadius: 2, background: color, opacity: t }} />;
      })}
      {MARKS.map((m, i) => {
        const p = fadeIn(f, at + 40 + i * 6, 8);
        return (
          <div
            key={m.label}
            style={{
              position: "absolute",
              left: m.x - 24,
              top: y - 24,
              width: 48,
              height: 48,
              borderRadius: 12,
              background: ok[i] ? tokens.eagerGreen : tokens.paper,
              border: `2px solid ${ok[i] ? K.greenEdge : RED}`,
              color: ok[i] ? tokens.paper : RED,
              fontSize: 28,
              fontWeight: 900,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              opacity: p,
              transform: `scale(${0.6 + 0.4 * p})`,
            }}
          >
            {ok[i] ? "✓" : "✕"}
          </div>
        );
      })}
    </div>
  );
};

export const VercelScene: React.FC = () => {
  const f = useCurrentFrame();
  const perMinute = Array.from({ length: 66 }, (_, i) => AX0 + 10 + i * 20);
  const evs: Ev[] = [
    { at: 26, name: "pop" },
    { at: 46, name: "pop" },
    { at: 66, name: "pop" },
    { at: 86, name: "pop" },
    { at: 120, name: "pop" },
    { at: 140, name: "pop" },
    { at: 160, name: "pop" },
    { at: 180, name: "pop" },
    { at: 200, name: "pop" },
    { at: V_TICK, name: "whoosh", volume: 0.6 },
    { at: V_TICK + 30, name: "pop" },
    { at: V_TICK + 130, name: "pop" },
    { at: V_TICK + 250, name: "pop" },
    { at: V_TICK + 270, name: "pop" },
    { at: V_SEAT, name: "whoosh", volume: 0.6 },
    { at: V_SEAT + 24, name: "pop" },
    { at: V_SEAT + 44, name: "pop" },
    { at: V_SEAT + 70, name: "pop" },
    { at: V_SEAT + 160, name: "pop", volume: 1 },
  ];
  return (
    <Frame>
      {/* ---------- A: Hobby vs Pro ---------- */}
      <Show from={0} to={V_TICK + 6}>
        <Heading at={4}>Hobby vs Pro, on what this app needs</Heading>
        <Sticker x={100} y={130} w={800} h={520} at={26} edge={tokens.faded}>
          <Tag color={tokens.charcoal} size={24}>Hobby</Tag>
          <Row at={46} ok={false}>Personal, non-commercial use only</Row>
          <Row at={66} ok={false}>1 team member</Row>
          <Row at={86} ok={false}>Cron jobs: at most once per day</Row>
        </Sticker>
        <Sticker x={1020} y={130} w={800} h={520} at={120} edge={tokens.eagerGreen}>
          <Tag color={tokens.eagerGreen} size={24}>Pro</Tag>
          <Row at={140} ok>$20 per paid seat per month, and each paid seat includes $20 of usage credit</Row>
          <Row at={160} ok>More paid seats (Owner or Member, who deploy and manage): $20 a month each</Row>
          <Row at={180} ok>Viewer seats: free and unlimited, but viewers can’t deploy</Row>
          <Row at={200} ok>Cron jobs: per-minute schedules</Row>
        </Sticker>
      </Show>

      {/* ---------- B: reminders need a minute hand ---------- */}
      <Show from={V_TICK} to={V_SEAT + 6}>
        <Heading at={V_TICK}>Reminders need a minute hand</Heading>
        <div style={{ position: "absolute", left: 0, top: 0, opacity: fadeIn(f, V_TICK + 30, 10) }}>
          <div style={{ position: "absolute", left: AX0, top: 190, width: AX1 - AX0, height: 4, background: tokens.charcoal, borderRadius: 2 }} />
          {MARKS.map((m) => (
            <div key={m.label} style={{ position: "absolute", left: m.x, top: 132, transform: "translateX(-50%)", textAlign: "center" }}>
              <div style={{ fontSize: 24, fontWeight: 800, color: tokens.charcoal, whiteSpace: "nowrap" }}>{m.label}</div>
              <div style={{ width: 4, height: 30, background: tokens.charcoal, margin: "6px auto 0" }} />
            </div>
          ))}
          <div style={{ position: "absolute", left: START_X, top: 170, transform: "translateX(-50%)" }}>
            <div style={{ width: 22, height: 44, background: tokens.eagerGreen, borderRadius: 6 }} />
          </div>
          <div style={{ position: "absolute", left: START_X, top: 226, transform: "translateX(-50%)", fontSize: 20, fontWeight: 800, color: tokens.eagerGreen, whiteSpace: "nowrap", ...K.label }}>session</div>
          <div style={{ position: "absolute", left: 110, top: 176, fontSize: 22, fontWeight: 700, color: tokens.pencil, whiteSpace: "nowrap" }}>time → (not to scale)</div>
        </div>
        <Lane y={320} at={V_TICK + 130} label="Once a day" sub="Hobby cron" ticks={[900]} ok={[false, false, false]} color={RED} />
        <Lane y={430} at={V_TICK + 180} label="Every minute" sub="Pro cron, or today’s workaround" ticks={perMinute} ok={[true, true, true]} color={tokens.eagerGreen} />
        <Sticker x={100} y={520} w={1080} h={260} at={V_TICK + 250} edge={ORANGE}>
          <Tag color={ORANGE} size={20}>Today: a workaround</Tag>
          <div style={{ fontSize: 28, fontWeight: 700, color: tokens.charcoal, marginTop: 14, lineHeight: 1.3 }}>
            Supabase’s pg_cron calls the app’s <span style={mono}>/bot/cron/tick</span> every minute.
          </div>
          <div style={{ ...mono, fontSize: 21, color: tokens.sparkBlue, background: K.blueWash, borderRadius: 10, padding: "6px 14px", marginTop: 14, display: "inline-block" }}>
            select cron.schedule(&apos;cufa-tick&apos;, &apos;* * * * *&apos;, $job$ … $job$);
          </div>
          <div style={{ fontSize: 20, fontWeight: 600, color: tokens.pencil, marginTop: 10 }}>deploy/vercel/README.md · “The scheduler, on Supabase”</div>
        </Sticker>
        <Sticker x={1220} y={520} w={600} h={260} at={V_TICK + 270} edge={tokens.eagerGreen}>
          <Tag color={tokens.eagerGreen} size={20}>On Pro</Tag>
          <div style={{ fontSize: 30, fontWeight: 700, color: tokens.charcoal, marginTop: 14, lineHeight: 1.3 }}>
            The same minute tick runs natively on Vercel, next to the app it calls.
          </div>
        </Sticker>
      </Show>

      {/* ---------- C: seats ---------- */}
      <Show from={V_SEAT}>
        <Heading at={V_SEAT}>One login each</Heading>
        {[
          { n: "Samson", r: "Owner · paid seat", c: tokens.eagerGreen, x: 100, at: V_SEAT + 24 },
          { n: "Adiah", r: "Member · paid seat", c: tokens.sparkBlue, x: 540, at: V_SEAT + 44 },
        ].map((s) => (
          <Sticker key={s.n} x={s.x} y={130} w={400} h={330} at={s.at} edge={s.c} style={{ textAlign: "center" }}>
            <div style={{ width: 130, height: 130, borderRadius: 999, background: s.c, color: tokens.paper, fontFamily: tokens.display, fontWeight: 900, fontSize: 72, display: "flex", alignItems: "center", justifyContent: "center", margin: "6px auto 0" }}>{s.n[0]}</div>
            <div style={{ fontFamily: tokens.display, fontWeight: 900, fontSize: 44, color: tokens.charcoal, marginTop: 14 }}>{s.n}</div>
            <div style={{ marginTop: 10 }}>
              <Tag color={s.c} size={19}>{s.r}</Tag>
            </div>
          </Sticker>
        ))}
        <Sticker x={980} y={130} w={840} h={330} at={V_SEAT + 70} edge={tokens.hairline}>
          <div style={{ display: "flex", gap: 22, marginTop: 6 }}>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} style={{ width: 96, height: 96, borderRadius: 999, background: tokens.paper, border: `3px dashed ${tokens.faded}`, opacity: fadeIn(f, V_SEAT + 76 + i * 5, 8) }} />
            ))}
            <div style={{ fontSize: 48, fontWeight: 900, color: tokens.faded, alignSelf: "center" }}>…</div>
          </div>
          <div style={{ fontFamily: tokens.display, fontWeight: 900, fontSize: 44, color: tokens.charcoal, marginTop: 22 }}>Viewers</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: tokens.pencil, marginTop: 8 }}>Free and unlimited. They can look, but can’t deploy.</div>
        </Sticker>
        <Sticker x={100} y={500} w={1720} h={230} at={V_SEAT + 160} edge={K.greenEdge} bg={tokens.storybookGreen}>
          <div style={{ fontFamily: tokens.display, fontWeight: 900, fontSize: 80, color: K.greenEdge, lineHeight: 1.1 }}>2 paid seats × $20 = $40 a month</div>
          <div style={{ fontSize: 30, fontWeight: 700, color: tokens.charcoal, marginTop: 14 }}>Each paid seat includes $20 of usage credit. Viewer seats cost nothing.</div>
        </Sticker>
      </Show>

      <div style={{ position: "absolute", right: 60, top: 794, fontSize: 21, fontWeight: 700, color: tokens.pencil, opacity: fadeIn(f, 20, 10) }}>
        Source: vercel.com/docs/plans (Sept 2026)
      </div>
      <Caption
        step="7"
        title="Why Vercel Pro"
        lines={[
          { from: 0, text: "The console and the Slack bot run as one Vercel deployment. Here is the case for putting it on Vercel Pro." },
          { from: 110, text: "Vercel’s Hobby plan is for personal, non-commercial use, with one team member. A CU programme isn’t what it’s for." },
          { from: 220, text: "Pro is $20 per paid seat per month, and each paid seat includes $20 of usage credit. Viewer seats are free." },
          { from: V_TICK, text: "The bot sends reminders 24 hours, 1 hour and 10 minutes before each session. That needs a tick every minute." },
          { from: V_TICK + 125, text: "On Hobby, cron runs at most once a day. So today the minute tick is a workaround, borrowed from Supabase’s pg_cron." },
          { from: V_TICK + 250, text: "On Pro, a per-minute schedule runs natively on Vercel, next to the app it calls." },
          { from: V_SEAT, text: "With paid seats, Samson and Adiah each get their own login to deploy and manage, instead of sharing one account." },
          { from: V_SEAT + 160, text: "Two paid seats come to $40 a month. Anyone who only needs to look can have a free Viewer seat." },
        ]}
      />
      <SceneSounds evs={evs} dur={VERCEL_DUR} />
    </Frame>
  );
};
