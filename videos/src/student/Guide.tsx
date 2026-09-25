import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { theme } from "../theme";
import { Check, Pill, Rise, Scene, Typed, useAppear } from "./ui";
import { ClickMark, FORM_PURPLE, TextLine } from "./FormBits";
import { Shot } from "./Shot";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;
const G_GREY = "#5F6368";
const G_BG = "#F0EBF8";

/* ---------- shared bits ---------- */
const Header: React.FC<{ pill: string; title: string; color?: string }> = ({ pill, title, color }) => (
  <Rise delay={0} style={{ display: "flex", alignItems: "center", gap: 28 }}>
    <Pill color={color}>{pill}</Pill>
    <div style={{ fontSize: 64, fontWeight: 900 }}>{title}</div>
  </Rise>
);

const Bullets: React.FC<{ items: { at: number; text: React.ReactNode }[]; size?: number; color?: string }> = ({
  items,
  size = 42,
  color = theme.accent2,
}) => (
  <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
    {items.map((it, i) => (
      <Rise key={i} delay={it.at} style={{ display: "flex", gap: 22, alignItems: "flex-start" }}>
        <div style={{ marginTop: 4 }}>
          <Check start={it.at} size={52} color={color} />
        </div>
        <div style={{ fontSize: size, lineHeight: 1.25 }}>{it.text}</div>
      </Rise>
    ))}
  </div>
);

const Quote: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span style={{ color: theme.accent, fontWeight: 800 }}>{children}</span>
);

/** Google-Forms styled page: lilac background, header card, question cards. */
const GForm: React.FC<{ children: React.ReactNode; scale?: number }> = ({ children, scale = 1 }) => (
  <div
    style={{
      backgroundColor: G_BG,
      borderRadius: 24,
      padding: 22,
      color: "#202124",
      fontFamily: "Roboto, Arial, sans-serif",
      boxShadow: "0 30px 80px rgba(0,0,0,0.5)",
      zoom: scale,
    }}
  >
    {children}
  </div>
);

const GCard: React.FC<{ children: React.ReactNode; top?: boolean; style?: React.CSSProperties }> = ({
  children,
  top,
  style,
}) => (
  <div
    style={{
      backgroundColor: "#fff",
      borderRadius: 12,
      borderTop: top ? `12px solid ${FORM_PURPLE}` : undefined,
      border: top ? undefined : "1px solid #DADCE0",
      padding: "18px 28px",
      marginBottom: 12,
      ...style,
    }}
  >
    {children}
  </div>
);

const QTitle: React.FC<{ children: React.ReactNode; required?: boolean }> = ({ children, required }) => (
  <div style={{ fontSize: 27, fontWeight: 500 }}>
    {children}
    {required ? <span style={{ color: "#D93025" }}> *</span> : null}
  </div>
);
const QDesc: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div style={{ fontSize: 20, color: G_GREY, marginTop: 4, lineHeight: 1.3 }}>{children}</div>
);

const Account: React.FC<{ email: string }> = ({ email }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 14, fontSize: 21, color: G_GREY, marginTop: 10 }}>
    <span style={{ fontWeight: 700, color: "#202124" }}>{email}</span>
    <span style={{ color: "#1A73E8" }}>Switch account</span>
  </div>
);

/* ---------- 3. How the link arrives (real screenshot) ---------- */
export const LINK_DUR = 270;
export const LinkScene: React.FC = () => (
  <Scene dur={LINK_DUR}>
    <AbsoluteFill style={{ padding: 60 }}>
      <Header pill="PART A · STEP 1" title="The link arrives mid-lesson" />
      <div style={{ display: "flex", gap: 60, marginTop: 40 }}>
        <div style={{ width: 720 }}>
          <Bullets
            items={[
              { at: 20, text: <>Sometime <Quote>15–25 minutes in</Quote>. The timing isn&apos;t announced.</> },
              { at: 70, text: <>Your teacher shares the form link, or a <Quote>QR code</Quote> to scan with your phone.</> },
              { at: 120, text: <>Same address either way. Open it right away.</> },
            ]}
          />
        </div>
        <Rise delay={10}>
          <Shot
            file="sess1.png"
            crop={{ x: 617, y: 630, w: 948, h: 450 }}
            width={1020}
            dur={LINK_DUR}
            label="REAL SCREEN · what your teacher shares"
          />
        </Rise>
      </div>
    </AbsoluteFill>
  </Scene>
);

/* ---------- 4. Passphrase on the teacher's screen ---------- */
export const PHRASE_DUR = 270;
export const PhraseScene: React.FC = () => (
  <Scene dur={PHRASE_DUR}>
    <AbsoluteFill style={{ padding: 60 }}>
      <Header pill="PART A · STEP 2" title="Catch today's passphrase" />
      <div style={{ display: "flex", gap: 60, marginTop: 40 }}>
        <div style={{ width: 720 }}>
          <Bullets
            items={[
              { at: 20, text: <>One word. Your teacher <Quote>says it aloud</Quote> and <Quote>puts it on screen</Quote>.</> },
              { at: 80, text: <>It&apos;s a new word each session, so listen every time.</> },
              { at: 140, text: <>It&apos;s the one part of a check-in that someone who isn&apos;t in the room can&apos;t produce.</> },
            ]}
          />
        </div>
        <Rise delay={10}>
          <Shot
            file="sess1.png"
            crop={{ x: 617, y: 160, w: 948, h: 440 }}
            width={1020}
            dur={PHRASE_DUR}
            label="REAL SCREEN · the session page your teacher projects"
          />
        </Rise>
      </div>
    </AbsoluteFill>
  </Scene>
);

/* ---------- 5. Part A form, faithful render ---------- */
export const FORM_A_DUR = 450;
const FA = { type: 150, submit: 300, done: 322 };
export const FormAScene: React.FC = () => {
  const frame = useCurrentFrame();
  const doneP = useAppear(FA.done);
  const press = interpolate(frame, [FA.submit - 2, FA.submit + 3, FA.submit + 10], [1, 0.9, 1], clamp);
  return (
    <Scene dur={FORM_A_DUR}>
      <AbsoluteFill style={{ padding: 60 }}>
        <Header pill="PART A · STEP 3" title="Fill in the check-in form" />
        <div style={{ display: "flex", gap: 50, marginTop: 36 }}>
          <div style={{ width: 700 }}>
            <Bullets
              items={[
                { at: 20, text: <>Be <Quote>signed into Google</Quote>. Your email is confirmed by Google, so you don&apos;t type it.</> },
                { at: 110, text: <>Type the word. <Quote>Spelling doesn&apos;t have to be perfect.</Quote></> },
                { at: FA.submit - 20, text: <>Submit right away. The time you submit is recorded too.</> },
              ]}
            />
          </div>
          <Rise delay={5} style={{ width: 1050, position: "relative" }}>
            <GForm>
              <GCard top>
                <div style={{ fontSize: 34, fontWeight: 500 }}>
                  Check-in — Session 1 — What a civic problem is (2026-09-27 19:00)
                </div>
                <div style={{ fontSize: 20, color: "#202124", marginTop: 10, lineHeight: 1.35 }}>
                  This is the attendance check-in for today&apos;s live lesson.
                  <br />
                  What we collect: your email address (confirmed by Google, so you don&apos;t type it), the time you
                  submit this, and today&apos;s passphrase.
                  <br />
                  Who sees it: Civics Unplugged staff.
                </div>
                <div style={{ height: 1, backgroundColor: "#DADCE0", margin: "12px -28px 0" }} />
                <Account email="ardith.aldergrove@example.invalid" />
                <div style={{ fontSize: 20, color: "#D93025", marginTop: 8 }}>* Indicates required question</div>
              </GCard>
              <div style={{ position: "relative" }}>
                <div style={{ opacity: 1 - doneP }}>
                  <GCard>
                    <QTitle required>Today&apos;s passphrase</QTitle>
                    <QDesc>
                      The word your teacher said out loud and put on screen during today&apos;s lesson. Spelling
                      doesn&apos;t have to be perfect.
                    </QDesc>
                    <div style={{ width: 520, marginTop: 18 }}>
                      <TextLine focusAt={FA.type - 10} blurAt={FA.submit - 20}>
                        <Typed text="lantern" start={FA.type} cps={7} caretUntil={FA.submit - 20} />
                      </TextLine>
                    </div>
                  </GCard>
                  <div
                    style={{
                      display: "inline-block",
                      backgroundColor: FORM_PURPLE,
                      color: "#fff",
                      fontSize: 24,
                      fontWeight: 500,
                      padding: "12px 34px",
                      borderRadius: 6,
                      scale: `${press}`,
                      position: "relative",
                    }}
                  >
                    Submit
                    <ClickMark at={FA.submit} x={90} y={30} />
                  </div>
                </div>
                <div style={{ position: "absolute", inset: 0, opacity: doneP }}>
                  <GCard style={{ display: "flex", alignItems: "center", gap: 26, minHeight: 170 }}>
                    <Check start={FA.done} size={100} />
                    <div style={{ fontSize: 30 }}>Your response has been recorded.</div>
                  </GCard>
                </div>
              </div>
            </GForm>
          </Rise>
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------- 6. Typos and review ---------- */
export const MATCH_DUR = 420;
export const MatchScene: React.FC = () => {
  const rows = [
    { typed: "lantern", verdict: "Exact match: attended", c: theme.accent2, at: 20 },
    { typed: "Lantern.", verdict: "Capitals & punctuation ignored: attended", c: theme.accent2, at: 50 },
    { typed: "lanturn", verdict: "One letter off: attended", c: theme.accent2, at: 80 },
    { typed: "the word was lantern", verdict: "Anything else: checked further, never auto-rejected", c: theme.accent, at: 110 },
  ];
  return (
    <Scene dur={MATCH_DUR}>
      <AbsoluteFill style={{ padding: 60 }}>
        <Header pill="PART A" title="What if I make a typo?" />
        <div style={{ display: "flex", gap: 50, marginTop: 40 }}>
          <div style={{ width: 800, display: "flex", flexDirection: "column", gap: 20 }}>
            {rows.map((r) => (
              <Rise key={r.typed} delay={r.at} style={{ backgroundColor: theme.surface, borderRadius: 20, padding: "18px 26px", borderLeft: `10px solid ${r.c}` }}>
                <div style={{ fontSize: 40, fontFamily: "monospace", fontWeight: 700 }}>&quot;{r.typed}&quot;</div>
                <div style={{ fontSize: 32, color: theme.muted, marginTop: 4 }}>{r.verdict}</div>
              </Rise>
            ))}
            <Rise delay={200}>
              <div style={{ fontSize: 34, lineHeight: 1.3, marginTop: 6 }}>
                If a case can&apos;t be decided, <Quote>a person decides</Quote>. &quot;Needs review&quot; never
                turns into &quot;absent&quot; on its own.
              </div>
            </Rise>
          </div>
          <Rise delay={150}>
            <Shot
              file="review.png"
              crop={{ x: 617, y: 160, w: 948, h: 700 }}
              width={960}
              dur={MATCH_DUR}
              label="REAL SCREEN · staff review queue"
            />
          </Rise>
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------- 7. Timing window ---------- */
export const WINDOW_DUR = 360;
export const WindowScene: React.FC = () => {
  const frame = useCurrentFrame();
  const W = 1600;
  const grow = interpolate(frame, [20, 70], [0, 1], clamp);
  // 18:45 .. 20:45 = 120 min across W; lesson 19:00-20:30
  const px = (min: number) => (min / 120) * W;
  const gIn = interpolate(frame, [80, 110], [0, 1], clamp);
  return (
    <Scene dur={WINDOW_DUR}>
      <AbsoluteFill style={{ padding: "60px 160px" }}>
        <Header pill="TIMING" title="When does a check-in count?" />
        <Rise delay={10}>
          <div style={{ fontSize: 40, color: theme.muted, marginTop: 24 }}>
            Example: Session 1 starts 19:00 and runs 90 minutes, with 15 minutes&apos; grace either side.
          </div>
        </Rise>
        <div style={{ position: "relative", width: W, height: 220, marginTop: 90 }}>
          <div style={{ position: "absolute", top: 60, left: 0, width: W * grow, height: 70, borderRadius: 16, backgroundColor: "#2A4466" }} />
          <div style={{ position: "absolute", top: 60, left: px(15), width: px(90) * grow, height: 70, borderRadius: 12, backgroundColor: theme.accent2 }} />
          <div style={{ position: "absolute", top: 76, left: px(15) + 30, fontSize: 36, fontWeight: 800, color: theme.ink, opacity: grow }}>
            The lesson, 19:00 to 20:30
          </div>
          {[
            { m: 0, t: "18:45" },
            { m: 15, t: "19:00" },
            { m: 105, t: "20:30" },
            { m: 120, t: "20:45" },
          ].map((k) => (
            <div key={k.t} style={{ position: "absolute", top: 150, left: px(k.m) - 60, width: 120, textAlign: "center", fontSize: 32, opacity: gIn }}>
              {k.t}
            </div>
          ))}
          <div style={{ position: "absolute", top: 0, left: 0, width: px(15), textAlign: "center", fontSize: 28, color: theme.accent, opacity: gIn }}>grace</div>
          <div style={{ position: "absolute", top: 0, left: px(105), width: px(15), textAlign: "center", fontSize: 28, color: theme.accent, opacity: gIn }}>grace</div>
        </div>
        <div style={{ marginTop: 40 }}>
          <Bullets
            items={[
              { at: 130, text: <>Inside this window: your Part A counts toward <Quote>that</Quote> lesson.</> },
              { at: 180, text: <>Before or after it: your answer is still kept, but it can&apos;t count as attending that lesson. Submit during class.</> },
              { at: 230, text: <>Part B goes out at the end, so submitting a few minutes past the window is expected and fine.</> },
            ]}
          />
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------- 8. Which email ---------- */
export const EMAIL_DUR = 330;
export const EmailScene: React.FC = () => (
  <Scene dur={EMAIL_DUR}>
    <AbsoluteFill style={{ padding: 60 }}>
      <Header pill="YOUR ACCOUNT" title="Which Google account?" />
      <div style={{ display: "flex", gap: 50, marginTop: 40 }}>
        <div style={{ width: 800 }}>
          <Bullets
            items={[
              { at: 20, text: <>Use the email <Quote>on the fellowship roster</Quote>. That&apos;s how your check-in is linked to you.</> },
              { at: 90, text: <>Signed in with a different address? <Quote>It&apos;s still recorded.</Quote> It goes to a staff list of unmatched addresses.</> },
              { at: 160, text: <>Staff fix the roster, and every past check-in from that address links back to you. Nothing is lost.</> },
            ]}
          />
        </div>
        <Rise delay={80}>
          <Shot
            file="ident.png"
            crop={{ x: 617, y: 160, w: 948, h: 480 }}
            width={960}
            dur={EMAIL_DUR}
            label="REAL SCREEN · staff: unresolved addresses"
          />
        </Rise>
      </div>
    </AbsoluteFill>
  </Scene>
);

/* ---------- 9. Part B form, faithful render ---------- */
export const FORM_B_DUR = 600;
const FB = { pick: 60, take: 110, week: 230, shout: 350, tick: 460, submit: 520, done: 540 };
export const FormBScene: React.FC = () => {
  const frame = useCurrentFrame();
  const doneP = useAppear(FB.done);
  const ticked = frame >= FB.tick;
  const press = interpolate(frame, [FB.submit - 2, FB.submit + 3, FB.submit + 10], [1, 0.9, 1], clamp);
  const steps = [
    { at: 20, t: "Rate 1 to 7 (required)" },
    { at: FB.take - 10, t: "One-sentence takeaway (required)" },
    { at: FB.week - 10, t: "This week's question (required)" },
    { at: FB.shout - 10, t: "Who helped you today? (optional)" },
    { at: FB.tick - 30, t: "Before you go: the check-in box (optional)" },
  ];
  const active = steps.reduce((a, s, i) => (frame >= s.at ? i : a), -1);
  return (
    <Scene dur={FORM_B_DUR}>
      <AbsoluteFill style={{ padding: "50px 60px" }}>
        <Header pill="PART B · END OF LESSON" title="The end-of-session form" color={theme.accent2} />
        <div style={{ display: "flex", gap: 40, marginTop: 30 }}>
          <div style={{ width: 640, display: "flex", flexDirection: "column", gap: 16 }}>
            {steps.map((s, i) => (
              <Rise key={s.t} delay={s.at}>
                <div
                  style={{
                    fontSize: 38,
                    fontWeight: 800,
                    padding: "14px 20px",
                    borderRadius: 18,
                    backgroundColor: i === active ? theme.surface : "transparent",
                    border: `3px solid ${i === active ? theme.accent2 : "transparent"}`,
                    opacity: i < active ? 0.55 : 1,
                  }}
                >
                  {i + 1}. {s.t}
                </div>
              </Rise>
            ))}
            <Rise delay={40}>
              <div style={{ fontSize: 32, color: theme.muted, marginTop: 10, lineHeight: 1.3 }}>
                Nothing here is graded or scored. Writing more doesn&apos;t score higher than writing less.
              </div>
            </Rise>
          </div>
          <Rise delay={5} style={{ width: 1160, position: "relative" }}>
            <GForm scale={0.8}>
              <GCard top style={{ padding: "12px 24px" }}>
                <div style={{ fontSize: 28, fontWeight: 500 }}>
                  End-of-session — Session 1 — What a civic problem is (2026-09-27 19:00)
                </div>
                <Account email="ardith.aldergrove@example.invalid" />
              </GCard>
              <div style={{ position: "relative" }}>
                <div style={{ opacity: 1 - doneP }}>
                  <GCard style={{ padding: "10px 24px", position: "relative" }}>
                    <QTitle required>I could explain today&apos;s topic to someone</QTitle>
                    <div style={{ display: "flex", alignItems: "flex-end", gap: 20, marginTop: 6 }}>
                      <span style={{ fontSize: 20, color: G_GREY, paddingBottom: 6 }}>Not at all</span>
                      {[1, 2, 3, 4, 5, 6, 7].map((n) => (
                        <div key={n} style={{ textAlign: "center", position: "relative" }}>
                          <div style={{ fontSize: 18, color: G_GREY }}>{n}</div>
                          <div
                            style={{
                              width: 32,
                              height: 32,
                              borderRadius: 32,
                              border: `3px solid ${n === 5 && frame >= FB.pick ? FORM_PURPLE : "#5F6368"}`,
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "center",
                            }}
                          >
                            <div
                              style={{
                                width: 16,
                                height: 16,
                                borderRadius: 16,
                                backgroundColor: FORM_PURPLE,
                                scale: `${n === 5 ? interpolate(frame, [FB.pick, FB.pick + 8], [0, 1], clamp) : 0}`,
                              }}
                            />
                          </div>
                          {n === 5 ? <ClickMark at={FB.pick} x={16} y={36} /> : null}
                        </div>
                      ))}
                      <span style={{ fontSize: 20, color: G_GREY, paddingBottom: 6 }}>Easily</span>
                    </div>
                  </GCard>
                  <GCard style={{ padding: "10px 24px" }}>
                    <QTitle required>In one sentence, what are you taking away from today?</QTitle>
                    <QDesc>
                      One sentence is genuinely enough. Nobody is marking the writing — we are reading what landed, not
                      how it is phrased.
                    </QDesc>
                    <div style={{ marginTop: 6 }}>
                      <TextLine focusAt={FB.take - 10} blurAt={FB.week - 10}>
                        <Typed text="Civic problems start with the people who live them." start={FB.take} caretUntil={FB.week - 10} cps={22} />
                      </TextLine>
                    </div>
                  </GCard>
                  <GCard style={{ padding: "10px 24px" }}>
                    <QTitle required>What surprised you about the problem we picked apart today?</QTitle>
                    <div style={{ marginTop: 6 }}>
                      <TextLine focusAt={FB.week - 10} blurAt={FB.shout - 10}>
                        <Typed text="How many groups were already working on it." start={FB.week} caretUntil={FB.shout - 10} cps={22} />
                      </TextLine>
                    </div>
                  </GCard>
                  <GCard style={{ padding: "10px 24px" }}>
                    <QTitle>Who helped you today?</QTitle>
                    <QDesc>
                      Optional. A name, or a few names. This is not a vote and there is no leaderboard — it is so the
                      people doing quiet work get noticed.
                    </QDesc>
                    <div style={{ marginTop: 6 }}>
                      <TextLine focusAt={FB.shout - 10} blurAt={FB.tick - 30}>
                        <Typed text="Bexley Brambleton" start={FB.shout} caretUntil={FB.tick - 30} cps={14} />
                      </TextLine>
                    </div>
                  </GCard>
                  <GCard style={{ padding: "10px 24px" }}>
                    <QTitle>Before you go</QTitle>
                    <QDesc>
                      Optional, and it counts for nothing either way — ticking this never affects your attendance, your
                      participation, or anything else recorded about you. It goes to one person, who will get in touch.
                    </QDesc>
                    <div style={{ display: "flex", alignItems: "center", gap: 16, marginTop: 10 }}>
                      <div
                        style={{
                          width: 30,
                          height: 30,
                          borderRadius: 4,
                          border: `3px solid ${ticked ? FORM_PURPLE : "#5F6368"}`,
                          backgroundColor: ticked ? FORM_PURPLE : "transparent",
                          position: "relative",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        {ticked ? <Check start={FB.tick} size={30} circle={false} color="#fff" /> : null}
                        <ClickMark at={FB.tick} x={14} y={18} />
                      </div>
                      <div style={{ fontSize: 24 }}>I&apos;d like someone to check in with me</div>
                    </div>
                  </GCard>
                  <div
                    style={{
                      display: "inline-block",
                      backgroundColor: FORM_PURPLE,
                      color: "#fff",
                      fontSize: 22,
                      padding: "10px 30px",
                      borderRadius: 6,
                      scale: `${press}`,
                      position: "relative",
                    }}
                  >
                    Submit
                    <ClickMark at={FB.submit} x={80} y={26} />
                  </div>
                </div>
                <div style={{ position: "absolute", inset: 0, opacity: doneP }}>
                  <GCard style={{ display: "flex", alignItems: "center", gap: 26, minHeight: 200 }}>
                    <Check start={FB.done} size={110} />
                    <div style={{ fontSize: 32 }}>Your response has been recorded.</div>
                  </GCard>
                </div>
              </div>
            </GForm>
          </Rise>
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------- 10. Rotation ---------- */
export const ROTATION_DUR = 330;
export const RotationScene: React.FC = () => (
  <Scene dur={ROTATION_DUR}>
    <AbsoluteFill style={{ padding: 60 }}>
      <Header pill="PART B" title="Question 3 changes every week" color={theme.accent2} />
      <div style={{ display: "flex", gap: 50, marginTop: 40 }}>
        <div style={{ width: 800, display: "flex", flexDirection: "column", gap: 22 }}>
          {[
            { k: "Teacher's own question", w: "Weeks 1, 4, 7, 10", q: "Your teacher writes it for that lesson.", c: "#E3A6E8", at: 20 },
            { k: "Muddiest point", w: "Weeks 2, 5, 8", q: "“What's still unclear?”", c: "#7FB8FF", at: 70 },
            { k: "Application", w: "Weeks 3, 6, 9", q: "“One way you'd use this in your project”", c: "#8FD19E", at: 120 },
          ].map((r) => (
            <Rise key={r.k} delay={r.at} style={{ backgroundColor: theme.surface, borderRadius: 20, padding: "20px 28px", borderLeft: `10px solid ${r.c}` }}>
              <div style={{ fontSize: 40, fontWeight: 900 }}>{r.k}</div>
              <div style={{ fontSize: 30, color: theme.muted }}>{r.w}</div>
              <div style={{ fontSize: 36, marginTop: 6 }}>{r.q}</div>
            </Rise>
          ))}
          <Rise delay={180}>
            <div style={{ fontSize: 32, color: theme.muted, lineHeight: 1.3 }}>
              &quot;What&apos;s still unclear&quot; answers are grouped into themes for your teacher, never shown as
              single answers with names on them.
            </div>
          </Rise>
        </div>
        <Rise delay={60}>
          <Shot
            file="rotation.png"
            crop={{ x: 617, y: 130, w: 948, h: 700 }}
            width={960}
            dur={ROTATION_DUR}
            label="REAL SCREEN · this cohort's weekly schedule"
          />
        </Rise>
      </div>
    </AbsoluteFill>
  </Scene>
);

/* ---------- 11. Help ---------- */
export const HELP2_DUR = 390;
export const Help2Scene: React.FC = () => (
  <Scene dur={HELP2_DUR} bg="#10302E">
    <AbsoluteFill style={{ padding: 60 }}>
      <Rise delay={0}>
        <div style={{ fontSize: 76, fontWeight: 900, lineHeight: 1.05 }}>
          Asking for help <span style={{ color: theme.accent2 }}>never</span> counts against you.
        </div>
      </Rise>
      <div style={{ display: "flex", gap: 50, marginTop: 40 }}>
        <div style={{ width: 800 }}>
          <Bullets
            items={[
              { at: 30, text: <>Tick <Quote>&quot;I&apos;d like someone to check in with me&quot;</Quote> and it&apos;s emailed straight away to the <Quote>Director of Programs</Quote>.</> },
              { at: 100, text: <>The email carries only your name and the session. Not your answers.</> },
              { at: 170, text: <>Left out of every count, rate, score, report and export. Tick it any week.</> },
            ]}
          />
        </div>
        <Rise delay={60}>
          <Shot
            file="help.png"
            crop={{ x: 617, y: 160, w: 948, h: 400 }}
            width={960}
            dur={HELP2_DUR}
            label="REAL SCREEN · who receives it"
          />
        </Rise>
      </div>
    </AbsoluteFill>
  </Scene>
);

/* ---------- 12. Privacy: shoutouts, what staff see ---------- */
export const PRIVACY_DUR = 420;
export const PrivacyScene: React.FC = () => {
  const cards = [
    { h: "Shoutouts are protected", b: "Not a vote, no leaderboard. Never shown to the person you named without an explicit decision by staff.", c: theme.accent },
    { h: "Writing isn't graded", b: "Free text is counted, never marked. No AI reads your answers to judge you.", c: theme.accent2 },
    { h: "Who sees what", b: "Civics Unplugged staff see responses. Your teacher sees unclear-point themes, not names.", c: "#7FB8FF" },
    { h: "The two parts are separate", b: "Answer one and not the other: both still count as data. Neither fills in for the other.", c: "#E3A6E8" },
  ];
  return (
    <Scene dur={PRIVACY_DUR}>
      <AbsoluteFill style={{ padding: "60px 90px" }}>
        <Header pill="PRIVACY" title="What happens to your answers" />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 34, marginTop: 50 }}>
          {cards.map((c, i) => (
            <Rise key={c.h} delay={30 + i * 55} distance={60}>
              <div style={{ backgroundColor: theme.surface, borderRadius: 28, padding: "34px 40px", borderTop: `12px solid ${c.c}`, height: 330 }}>
                <div style={{ fontSize: 50, fontWeight: 900 }}>{c.h}</div>
                <div style={{ fontSize: 38, color: theme.muted, marginTop: 16, lineHeight: 1.3 }}>{c.b}</div>
              </div>
            </Rise>
          ))}
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------- 13. FAQ ---------- */
export const FAQ_DUR = 600;
export const FaqScene: React.FC = () => {
  const qs = [
    { q: "Missed the passphrase or joined late?", a: "Submit anyway and say so in the answer. Nothing is thrown away; a person reviews it." },
    { q: "Signed in with the wrong account?", a: "It's still recorded. Tell staff your roster email so it links to you." },
    { q: "Made a typo?", a: "One letter off still counts. Anything else goes to a person, never an automatic \"absent\"." },
    { q: "Missed a form entirely?", a: "Part A and Part B are separate. Answer whichever you still can, and tell staff." },
    { q: "Not getting reminders?", a: "Check /reminders. Overnight quiet hours skip reminders rather than send them late." },
    { q: "Dashboard link stopped working?", a: "Links last 7 days. Type /dashboard for a fresh one." },
    { q: "Too many badge messages?", a: "/badges off. Your badges still show in /badges and on your dashboard." },
    { q: "Having a hard week?", a: "Tick the check-in box, press Check in with me, or type /checkin. It costs you nothing." },
  ];
  return (
    <Scene dur={FAQ_DUR}>
      <AbsoluteFill style={{ padding: "50px 80px" }}>
        <Header pill="FAQ" title="Something went wrong?" />
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22, marginTop: 34 }}>
          {qs.map((x, i) => (
            <Rise key={x.q} delay={20 + i * 50} style={{ backgroundColor: theme.surface, borderRadius: 22, padding: "16px 26px", height: 190 }}>
              <div style={{ fontSize: 36, fontWeight: 900, color: theme.accent }}>{x.q}</div>
              <div style={{ fontSize: 30, marginTop: 6, lineHeight: 1.3 }}>{x.a}</div>
            </Rise>
          ))}
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------- 14. Recap ---------- */
export const RECAP2_DUR = 330;
export const Recap2Scene: React.FC = () => {
  const items = [
    "Read your welcome DM; reminders come 24h, 1h and 10m before",
    "Stay signed into your roster Google account",
    "Part A (mid-lesson): type the passphrase, submit during class",
    "Part B (end): rate, one sentence, this week's question",
    "Ask in #q-and-a; mark the answer that helped with ✅",
    "/me, /dashboard, /badges, /reminders, /checkin",
  ];
  return (
    <Scene dur={RECAP2_DUR}>
      <AbsoluteFill style={{ padding: "80px 140px" }}>
        <Rise delay={0}>
          <div style={{ fontSize: 96, fontWeight: 900 }}>Quick recap</div>
        </Rise>
        <div style={{ display: "flex", flexDirection: "column", gap: 26, marginTop: 40 }}>
          {items.map((t, i) => (
            <Rise key={t} delay={25 + i * 36} style={{ display: "flex", alignItems: "center", gap: 30 }}>
              <Check start={35 + i * 36} size={66} />
              <div style={{ fontSize: 46, fontWeight: 700 }}>{t}</div>
            </Rise>
          ))}
        </div>
      </AbsoluteFill>
    </Scene>
  );
};
