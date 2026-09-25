import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { theme } from "../theme";
import { Check, Pill, Rise, Scene, SceneHeader, Steps, Typed, useAppear, useProgress } from "./ui";
import { ClickMark, FieldLabel, FormCard, FORM_PURPLE, TextLine } from "./FormBits";

const clamp = {
  extrapolateLeft: "clamp",
  extrapolateRight: "clamp",
} as const;

/* ---------------- 1. Title ---------------- */
export const TITLE_DUR = 150;
export const TitleScene: React.FC = () => {
  const bar = useProgress(20, 60);
  return (
    <Scene dur={TITLE_DUR}>
      <AbsoluteFill style={{ justifyContent: "center", padding: "0 160px" }}>
        <Rise delay={5}>
          <Pill>CIVIC INNOVATORS FELLOWSHIP</Pill>
        </Rise>
        <Rise delay={14}>
          <div style={{ fontSize: 132, fontWeight: 900, lineHeight: 1.02, marginTop: 40 }}>
            Checking in:
            <br />
            <span style={{ color: theme.accent }}>a fellow&apos;s guide</span>
          </div>
        </Rise>
        <div
          style={{
            height: 10,
            width: 520 * bar,
            backgroundColor: theme.accent2,
            borderRadius: 10,
            marginTop: 44,
          }}
        />
        <Rise delay={36}>
          <div style={{ fontSize: 48, color: theme.muted, marginTop: 36 }}>
            Two quick forms. About two minutes. Here&apos;s how.
          </div>
        </Rise>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------------- 2. Timeline ---------------- */
export const TIMELINE_DUR = 330;
export const TimelineScene: React.FC = () => {
  const frame = useCurrentFrame();
  const barW = 1600;
  const grow = useProgress(20, 60);
  const play = interpolate(frame, [70, 250], [0, 1], clamp);
  const aStart = 0.25;
  const aEnd = 0.42;
  const aIn = useAppear(110, 14);
  const bIn = useAppear(200, 14);
  return (
    <Scene dur={TIMELINE_DUR}>
      <AbsoluteFill style={{ padding: "90px 160px" }}>
        <Rise delay={0}>
          <div style={{ fontSize: 88, fontWeight: 900 }}>Two quick forms, every live lesson</div>
        </Rise>
        <Rise delay={10}>
          <div style={{ fontSize: 44, color: theme.muted, marginTop: 14 }}>
            They are separate. Answer both.
          </div>
        </Rise>

        {/* lesson bar */}
        <div style={{ position: "relative", marginTop: 150, width: barW, height: 180 }}>
          <div
            style={{
              position: "absolute",
              top: 70,
              left: 0,
              height: 40,
              width: barW * grow,
              backgroundColor: theme.surface,
              borderRadius: 40,
            }}
          />
          <div
            style={{
              position: "absolute",
              top: 70,
              left: 0,
              height: 40,
              width: barW * play,
              backgroundColor: "#2A4466",
              borderRadius: 40,
            }}
          />
          {/* Part A window */}
          <div
            style={{
              position: "absolute",
              top: 58,
              left: barW * aStart,
              width: barW * (aEnd - aStart),
              height: 64,
              borderRadius: 18,
              border: `5px dashed ${theme.accent}`,
              opacity: aIn,
              scale: `${0.8 + 0.2 * aIn}`,
            }}
          />
          <div
            style={{
              position: "absolute",
              top: -30,
              left: barW * aStart,
              width: barW * (aEnd - aStart),
              textAlign: "center",
              fontSize: 40,
              fontWeight: 800,
              color: theme.accent,
              opacity: aIn,
            }}
          >
            Part A
          </div>
          <div
            style={{
              position: "absolute",
              top: 140,
              left: barW * aStart - 80,
              width: barW * (aEnd - aStart) + 160,
              textAlign: "center",
              fontSize: 34,
              color: theme.text,
              opacity: aIn,
            }}
          >
            sometime 15–25 min in
          </div>
          {/* Part B marker */}
          <div
            style={{
              position: "absolute",
              top: 50,
              left: barW - 40,
              width: 80,
              height: 80,
              borderRadius: 80,
              backgroundColor: theme.accent2,
              scale: `${bIn}`,
            }}
          />
          <div
            style={{
              position: "absolute",
              top: -30,
              left: barW - 150,
              width: 300,
              textAlign: "center",
              fontSize: 40,
              fontWeight: 800,
              color: theme.accent2,
              opacity: bIn,
            }}
          >
            Part B
          </div>
          <div
            style={{
              position: "absolute",
              top: 140,
              left: barW - 150,
              width: 300,
              textAlign: "center",
              fontSize: 34,
              opacity: bIn,
            }}
          >
            at the end
          </div>
          {/* playhead */}
          <div
            style={{
              position: "absolute",
              top: 40,
              left: barW * play - 4,
              width: 8,
              height: 100,
              borderRadius: 8,
              backgroundColor: theme.text,
              opacity: grow,
            }}
          />
          <div style={{ position: "absolute", top: 140, left: 0, fontSize: 32, color: theme.muted, opacity: grow }}>
            Lesson starts
          </div>
        </div>

        <div style={{ display: "flex", gap: 60, marginTop: 70 }}>
          <Rise delay={140} style={{ flex: 1 }}>
            <div style={{ backgroundColor: theme.surface, borderRadius: 28, padding: "30px 40px", borderLeft: `12px solid ${theme.accent}` }}>
              <div style={{ fontSize: 48, fontWeight: 900 }}>Part A: &quot;I&apos;m here&quot;</div>
              <div style={{ fontSize: 38, color: theme.muted, marginTop: 8 }}>
                Mid-lesson. Type the passphrase.
              </div>
            </div>
          </Rise>
          <Rise delay={225} style={{ flex: 1 }}>
            <div style={{ backgroundColor: theme.surface, borderRadius: 28, padding: "30px 40px", borderLeft: `12px solid ${theme.accent2}` }}>
              <div style={{ fontSize: 48, fontWeight: 900 }}>Part B: &quot;What landed&quot;</div>
              <div style={{ fontSize: 38, color: theme.muted, marginTop: 8 }}>
                End of lesson. A quick reflection.
              </div>
            </div>
          </Rise>
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------------- 3. Part A ---------------- */
export const PART_A_DUR = 660;
const A = { chat: 20, signin: 110, phrase: 200, type: 300, submit: 440, done: 462 };

export const PartAScene: React.FC = () => {
  const frame = useCurrentFrame();
  const chatIn = useAppear(A.chat, 14);
  const chatOut = interpolate(frame, [A.phrase - 20, A.phrase - 5], [1, 0], clamp);
  const phraseIn = useAppear(A.phrase, 12);
  const cardIn = useAppear(A.signin);
  const doneP = useAppear(A.done);
  const press = interpolate(frame, [A.submit - 2, A.submit + 3, A.submit + 10], [1, 0.9, 1], clamp);
  const signGlow = interpolate(frame, [A.signin + 20, A.signin + 35, A.phrase], [0, 1, 0], clamp);
  return (
    <Scene dur={PART_A_DUR}>
      <AbsoluteFill style={{ padding: "60px 60px" }}>
        <SceneHeader pill="PART A · MID-LESSON" title="Prove you're here" />
        <div style={{ display: "flex", gap: 40, marginTop: 40, flex: 1 }}>
          <div style={{ width: 800 }}>
            <Steps
              steps={[
                { at: A.chat, title: "Open the link when it drops", tip: "It arrives 15–25 minutes in, with no warning." },
                { at: A.signin, title: "Be signed into Google", tip: "Your verified email is collected for you." },
                { at: A.phrase, title: "Catch the passphrase", tip: "Your teacher says it aloud and shows it on screen." },
                { at: A.type, title: "Type it in", tip: "Made a typo? It's still recorded and a person reviews it." },
                { at: A.submit, title: "Submit right away", tip: "The timestamp matters, so don't leave the tab sitting open." },
              ]}
            />
          </div>

          <div style={{ position: "relative", width: 1000, height: 860 }}>
            {/* chat message */}
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                opacity: chatIn * chatOut,
                translate: `0px ${(1 - chatIn) * 30}px`,
                backgroundColor: theme.surface,
                borderRadius: 24,
                padding: "22px 32px",
              }}
            >
              <div style={{ fontSize: 28, color: theme.muted }}>Lesson chat · your teacher</div>
              <div style={{ fontSize: 38, marginTop: 6 }}>
                Part A is open!{" "}
                <span style={{ color: "#7FB8FF", textDecoration: "underline" }}>forms.gle/part-a</span>
              </div>
            </div>
            {/* teacher's slide with passphrase */}
            <div
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                right: 0,
                opacity: phraseIn,
                scale: `${0.85 + 0.15 * phraseIn}`,
                backgroundColor: theme.accent,
                color: theme.ink,
                borderRadius: 24,
                padding: "16px 32px",
                textAlign: "center",
              }}
            >
              <div style={{ fontSize: 28, fontWeight: 700 }}>On your teacher&apos;s screen</div>
              <div style={{ fontSize: 72, fontWeight: 900, letterSpacing: 6 }}>MAPLE RIVER</div>
            </div>

            {/* the form */}
            <div
              style={{
                position: "absolute",
                top: 200,
                left: 0,
                right: 0,
                opacity: cardIn,
                translate: `0px ${(1 - cardIn) * 60}px`,
              }}
            >
              <FormCard title="Part A check-in" subtitle="Civic Innovators · Live lesson">
                <div style={{ position: "relative" }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 18,
                      padding: "14px 18px",
                      borderRadius: 16,
                      backgroundColor: `rgba(63,182,168,${0.08 + 0.25 * signGlow})`,
                    }}
                  >
                    <div
                      style={{
                        width: 60,
                        height: 60,
                        borderRadius: 60,
                        backgroundColor: theme.accent2,
                        color: "#fff",
                        fontSize: 32,
                        fontWeight: 800,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                      }}
                    >
                      A
                    </div>
                    <div>
                      <div style={{ fontSize: 30, fontWeight: 700 }}>alex.rivera@gmail.com</div>
                      <div style={{ fontSize: 24, color: "#5F6B7A" }}>Signed in · email recorded automatically</div>
                    </div>
                  </div>

                  <div style={{ opacity: 1 - doneP }}>
                    <div style={{ marginTop: 28 }}>
                      <FieldLabel>Session passphrase</FieldLabel>
                      <TextLine focusAt={A.type - 10} blurAt={A.submit - 20}>
                        <Typed
                          text="MAPLE RIVER"
                          start={A.type}
                          cps={9}
                          caretUntil={A.submit - 20}
                          style={{ fontWeight: 700, letterSpacing: 3 }}
                        />
                      </TextLine>
                    </div>
                    <div
                      style={{
                        marginTop: 34,
                        display: "inline-block",
                        backgroundColor: FORM_PURPLE,
                        color: "#fff",
                        fontSize: 30,
                        fontWeight: 700,
                        padding: "14px 40px",
                        borderRadius: 12,
                        scale: `${press}`,
                        position: "relative",
                      }}
                    >
                      Submit
                      <ClickMark at={A.submit} x={120} y={40} />
                    </div>
                  </div>

                  {/* success overlay */}
                  <div
                    style={{
                      position: "absolute",
                      left: 0,
                      right: 0,
                      top: 110,
                      bottom: 0,
                      backgroundColor: theme.card,
                      display: "flex",
                      alignItems: "center",
                      gap: 30,
                      opacity: doneP,
                    }}
                  >
                    <Check start={A.done} size={120} />
                    <div>
                      <div style={{ fontSize: 44, fontWeight: 900 }}>Response recorded</div>
                      <div style={{ fontSize: 28, color: "#5F6B7A" }}>That&apos;s it. Back to the lesson!</div>
                    </div>
                  </div>
                </div>
              </FormCard>
            </div>
          </div>
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------------- 4. Part B ---------------- */
export const PART_B_DUR = 690;
const B = { rate: 20, pick: 85, take: 140, week: 270, shout: 400, box: 510, tick: 545, submit: 600, done: 620 };

const Scale: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 16, position: "relative" }}>
      <span style={{ fontSize: 22, color: "#5F6B7A", whiteSpace: "nowrap", marginTop: 28 }}>Not at all</span>
      {[1, 2, 3, 4, 5, 6, 7].map((n) => {
        const sel = n === 5 && frame >= B.pick;
        return (
          <div key={n} style={{ textAlign: "center", position: "relative" }}>
            <div style={{ fontSize: 24, color: "#5F6B7A" }}>{n}</div>
            <div
              style={{
                width: 46,
                height: 46,
                borderRadius: 46,
                border: `3px solid ${sel ? FORM_PURPLE : "#9AA3AF"}`,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <div
                style={{
                  width: 26,
                  height: 26,
                  borderRadius: 26,
                  backgroundColor: FORM_PURPLE,
                  scale: `${interpolate(frame, [B.pick, B.pick + 8], [0, 1], clamp) * (n === 5 ? 1 : 0)}`,
                }}
              />
            </div>
            {n === 5 ? <ClickMark at={B.pick} x={24} y={50} /> : null}
          </div>
        );
      })}
      <span style={{ fontSize: 22, color: "#5F6B7A", whiteSpace: "nowrap", marginTop: 28 }}>Very</span>
    </div>
  );
};

export const PartBScene: React.FC = () => {
  const frame = useCurrentFrame();
  const cardIn = useAppear(10);
  const doneP = useAppear(B.done);
  const ticked = frame >= B.tick;
  const press = interpolate(frame, [B.submit - 2, B.submit + 3, B.submit + 10], [1, 0.9, 1], clamp);
  const fieldGap = 18;
  return (
    <Scene dur={PART_B_DUR}>
      <AbsoluteFill style={{ padding: "60px 60px" }}>
        <SceneHeader pill="PART B · END OF LESSON" title="What landed?" pillColor={theme.accent2} />
        <div style={{ display: "flex", gap: 40, marginTop: 36, flex: 1 }}>
          <div style={{ width: 800 }}>
            <Steps
              steps={[
                { at: B.rate, title: "Rate your confidence, 1 to 7", tip: "Honest beats high. The trend is what helps." },
                { at: B.take, title: "One sentence: your takeaway", tip: "Plain words are perfect. It isn't graded." },
                { at: B.week, title: "Answer this week's question", tip: "One question that changes each week." },
                { at: B.shout, title: "Shout out a peer (optional)", tip: "Kept private. Not shown to them automatically." },
                { at: B.box, title: "Want a check-in? Tick the box (optional)", tip: "A real person on staff follows up with you." },
              ]}
            />
          </div>
          <div style={{ position: "relative", width: 1000 }}>
            <div style={{ opacity: cardIn, translate: `0px ${(1 - cardIn) * 60}px` }}>
              <FormCard title="Part B reflection" subtitle="Civic Innovators · Live lesson">
                <div style={{ position: "relative" }}>
                  <div style={{ opacity: 1 - doneP, display: "flex", flexDirection: "column", gap: fieldGap }}>
                    <div>
                      <FieldLabel>How confident do you feel about today&apos;s topic?</FieldLabel>
                      <Scale />
                    </div>
                    <div>
                      <FieldLabel>Your takeaway, in one sentence</FieldLabel>
                      <TextLine focusAt={B.take - 10} blurAt={B.week - 10}>
                        <Typed text="Small local wins build trust in civic action." start={B.take} caretUntil={B.week - 10} />
                      </TextLine>
                    </div>
                    <div>
                      <FieldLabel>This week: What&apos;s still unclear?</FieldLabel>
                      <TextLine focusAt={B.week - 10} blurAt={B.shout - 10}>
                        <Typed text="How to choose which council meeting to attend." start={B.week} caretUntil={B.shout - 10} />
                      </TextLine>
                    </div>
                    <div>
                      <FieldLabel optional>Shout out a peer</FieldLabel>
                      <TextLine focusAt={B.shout - 10} blurAt={B.box - 10}>
                        <Typed text="Jordan, for a great breakout example" start={B.shout} caretUntil={B.box - 10} />
                      </TextLine>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 18, position: "relative" }}>
                      <div
                        style={{
                          width: 42,
                          height: 42,
                          borderRadius: 8,
                          border: `3px solid ${ticked ? FORM_PURPLE : "#9AA3AF"}`,
                          backgroundColor: ticked ? FORM_PURPLE : "transparent",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          position: "relative",
                          flexShrink: 0,
                        }}
                      >
                        {ticked ? <Check start={B.tick} size={40} circle={false} color="#fff" /> : null}
                        <ClickMark at={B.tick} x={20} y={24} />
                      </div>
                      <div style={{ fontSize: 30, fontWeight: 600 }}>
                        I&apos;d like someone to check in with me
                      </div>
                    </div>
                    <div>
                      <div
                        style={{
                          display: "inline-block",
                          backgroundColor: FORM_PURPLE,
                          color: "#fff",
                          fontSize: 30,
                          fontWeight: 700,
                          padding: "12px 40px",
                          borderRadius: 12,
                          scale: `${press}`,
                          position: "relative",
                        }}
                      >
                        Submit
                        <ClickMark at={B.submit} x={120} y={36} />
                      </div>
                    </div>
                  </div>
                  <div
                    style={{
                      position: "absolute",
                      inset: 0,
                      display: "flex",
                      flexDirection: "column",
                      alignItems: "center",
                      justifyContent: "center",
                      gap: 26,
                      backgroundColor: theme.card,
                      opacity: doneP,
                    }}
                  >
                    <Check start={B.done} size={160} />
                    <div style={{ fontSize: 48, fontWeight: 900 }}>Thanks! Reflection sent.</div>
                  </div>
                </div>
              </FormCard>
            </div>
          </div>
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------------- 5. Help ---------------- */
export const HELP_DUR = 330;
export const HelpScene: React.FC = () => {
  const frame = useCurrentFrame();
  const pulse = 1 + 0.05 * Math.sin(frame / 8);
  const heartIn = useAppear(5, 12);
  return (
    <Scene dur={HELP_DUR} bg="#10302E">
      <AbsoluteFill style={{ padding: "100px 160px", flexDirection: "row", alignItems: "center", gap: 100 }}>
        <div style={{ scale: `${heartIn * pulse}`, flexShrink: 0 }}>
          <svg width="340" height="310" viewBox="0 0 24 22">
            <path
              d="M12 21 C 5 15.5 1 12 1 7 A 5.5 5.5 0 0 1 12 4.5 A 5.5 5.5 0 0 1 23 7 C 23 12 19 15.5 12 21 Z"
              fill={theme.accent2}
            />
          </svg>
        </div>
        <div>
          <Rise delay={10}>
            <div style={{ fontSize: 92, fontWeight: 900, lineHeight: 1.05 }}>
              Asking for help <span style={{ color: theme.accent2 }}>never</span> counts against you.
            </div>
          </Rise>
          <div style={{ marginTop: 50, display: "flex", flexDirection: "column", gap: 26 }}>
            {[
              "The check-in box is left out of every count and score. Always.",
              "It goes to a real, named person on staff.",
              "Tick it any week, for any reason, big or small.",
            ].map((t, i) => (
              <Rise key={t} delay={70 + i * 45} style={{ display: "flex", alignItems: "center", gap: 26 }}>
                <Check start={70 + i * 45} size={64} />
                <div style={{ fontSize: 44 }}>{t}</div>
              </Rise>
            ))}
          </div>
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------------- 6. What we don't do ---------------- */
export const PROMISES_DUR = 330;
export const PromisesScene: React.FC = () => {
  const cards = [
    { head: "Your writing isn't graded", body: "We count that you answered, not how well you wrote it." },
    { head: "No AI judges your answers", body: "Nobody's response is scored by a model. Ever." },
    { head: "Shoutouts stay private", body: "Not shown to the person you named unless staff decide to." },
  ];
  return (
    <Scene dur={PROMISES_DUR}>
      <AbsoluteFill style={{ padding: "100px 120px" }}>
        <Rise delay={0}>
          <div style={{ fontSize: 88, fontWeight: 900 }}>Relax. Here&apos;s what we don&apos;t do.</div>
        </Rise>
        <div style={{ display: "flex", gap: 44, marginTop: 90 }}>
          {cards.map((c, i) => (
            <Rise key={c.head} delay={40 + i * 50} style={{ flex: 1 }} distance={80}>
              <div
                style={{
                  backgroundColor: theme.surface,
                  borderRadius: 32,
                  padding: "48px 44px",
                  height: 520,
                  borderTop: `14px solid ${[theme.accent, theme.accent2, "#7FB8FF"][i]}`,
                }}
              >
                <div
                  style={{
                    width: 90,
                    height: 90,
                    borderRadius: 90,
                    border: `8px solid ${theme.danger}`,
                    position: "relative",
                    marginBottom: 36,
                  }}
                >
                  <div
                    style={{
                      position: "absolute",
                      left: 33,
                      top: -4,
                      width: 8,
                      height: 82,
                      backgroundColor: theme.danger,
                      rotate: "-45deg",
                    }}
                  />
                </div>
                <div style={{ fontSize: 56, fontWeight: 900, lineHeight: 1.1 }}>{c.head}</div>
                <div style={{ fontSize: 40, color: theme.muted, marginTop: 24, lineHeight: 1.3 }}>{c.body}</div>
              </div>
            </Rise>
          ))}
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------------- 7. Recap ---------------- */
export const RECAP_DUR = 360;
export const RecapScene: React.FC = () => {
  const items = [
    "Stay signed into Google during class",
    "Part A: type the passphrase, submit right away",
    "Part B: rate, reflect, answer at the end",
    "Two separate forms. Answer both.",
    "Need support? Tick the check-in box.",
  ];
  return (
    <Scene dur={RECAP_DUR}>
      <AbsoluteFill style={{ padding: "90px 160px" }}>
        <Rise delay={0}>
          <div style={{ fontSize: 96, fontWeight: 900 }}>Quick recap</div>
        </Rise>
        <div style={{ display: "flex", flexDirection: "column", gap: 34, marginTop: 60 }}>
          {items.map((t, i) => (
            <Rise key={t} delay={25 + i * 38} style={{ display: "flex", alignItems: "center", gap: 34 }}>
              <Check start={35 + i * 38} size={80} color={i === 4 ? theme.accent2 : theme.accent2} />
              <div style={{ fontSize: 56, fontWeight: 700 }}>{t}</div>
            </Rise>
          ))}
        </div>
      </AbsoluteFill>
    </Scene>
  );
};

/* ---------------- 8. Closing ---------------- */
export const CLOSING_DUR = 150;
export const ClosingScene: React.FC = () => {
  const glow = useProgress(10, 60);
  return (
    <Scene dur={CLOSING_DUR}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", textAlign: "center" }}>
        <Rise delay={5}>
          <div style={{ fontSize: 120, fontWeight: 900 }}>
            Thanks for <span style={{ color: theme.accent }}>showing up.</span>
          </div>
        </Rise>
        <div style={{ height: 10, width: 700 * glow, backgroundColor: theme.accent2, borderRadius: 10, margin: "40px 0" }} />
        <Rise delay={30}>
          <div style={{ fontSize: 52, color: theme.muted }}>See you at the next live lesson.</div>
        </Rise>
        <Rise delay={50}>
          <div style={{ fontSize: 40, marginTop: 50, color: theme.text }}>Civics Unplugged · Civic Innovators Fellowship</div>
        </Rise>
      </AbsoluteFill>
    </Scene>
  );
};
