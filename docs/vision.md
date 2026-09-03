# What a full-fledged version would do

Everything this system could do if it were finished, drawn from what the best
tools in each adjacent category actually ship: community analytics (Common
Room, Orbit, Threado), check-in bots (Geekbot, Polly, Standuply), student
early-warning systems, recognition bots (HeyTaco, Bonusly, Karma), connection
bots (Donut), cohort platforms (Disco, Circle, Maven), mentoring software
(Chronus, MentorcliQ), and Slack's own app surfaces.

Legend: **✅ built** · **🔜 next, cheap** · **⚠️ conflicts with a decision
this project has made, and why** · unmarked = possible, not started.

This is the "room for improvement" half of the lessons-learned report.

---

## 1. What it observes

- ✅ Messages sent, edited, deleted; reactions given and removed; channel joins and leaves
- ✅ Thread replies, message length, links, file attachments (counted, never stored)
- Reply latency between peers: how fast a question in #help-desk gets an answer
- Who-replies-to-whom graph, for spotting fellows nobody talks to
- Mentions given (received are recorded, never ranked)
- Huddle joins and leaves (Slack emits these events)
- Canvas edits and comments
- Which emoji are used, as a cohort-level mood signal — never per person
- Channel-level liveness: which channels are alive, which have gone quiet
- Time-of-day and day-of-week activity, so reminders land when fellows are around
- Poll and vote responses to bot-run polls
- 🔜 Zoom attendance ingest, cross-checked against the form check-in
- 🔜 Assignment submissions from the Google Sheet
- Calendar RSVPs for sessions
- ⚠️ Presence (online/away) — available via the API, deliberately not collected; it is surveillance of when a teenager is at their keyboard
- ⚠️ Message text — off by default; the definition counts acts, it does not read them (ADR-031)
- ⚠️ Read receipts — Slack does not expose them; nothing to decide

## 2. Identity and roster

- ✅ Slack user id → email cache; roster join at read time so a corrected roster re-attributes history
- ✅ Unmatched addresses queued for a human, never dropped, never guessed
- Automatic roster sync from the Google Sheet on a schedule
- `/roster add|remove|link` for staff, with an audit trail
- Alert staff when someone joins the workspace who is not on the roster
- Second-email aliases: a fellow's school and personal addresses on one record
- Workspace membership as the cohort boundary, per the Director's definition: leaving the workspace flips status to withdrawn automatically
- Full status lifecycle: applied → accepted → active → deferred → withdrawn → completed → alumni
- Same person across cohorts, so an alumni mentor is linked to their own fellowship year

## 3. Check-ins inside Slack (instead of Google Forms)

- Part A passphrase check-in as a Slack modal, opened from a button the bot posts mid-session — identity is the workspace-scoped Slack id, no Google account needed
- Part B exit ticket as a modal: 7-point scale as radio buttons, short text, the rotating slot, the shoutout, the help checkbox
- The rotating-question schedule enforced by the bot
- `/passphrase <word>` for the teacher, which also stamps `announced_at`
- Bot posts the check-in button at a scheduled offset, so nothing depends on a teacher remembering
- DM the check-in to anyone who was in the channel but did not submit
- A confirmation DM after submitting
- Timezone-aware scheduling per session
- ⚠️ Would replace the Verified-email Google Form. Worth doing: Slack identity is stronger and needs no Google account. Keep the Forms path as the fallback for a workspace outage.

## 4. Reminders, nudges, scheduling

- Session reminders at 24h, 1h and 10 min, with the Zoom link
- Assignment-due reminders
- Nudge non-submitters after Part B closes — capped at two, personalised, by DM (research: a third reminder reads as pressure)
- A weekly digest to fellows: this week's session, what is due, what changed
- Quiet hours: no DMs late at night, in the fellow's timezone
- Reminder preferences a fellow controls: fewer, later, none
- Bot-posted agenda at session start

## 5. Early warning and intervention

- Rules in a config file the Director owns, not in code: e.g. no check-in for 2 consecutive sessions AND no Slack activity for 14 days
- ✅ Flagging is gated on a named responder existing; a flag with nobody to act on it is never raised
- Change detection, not just level: "was active, went quiet" is the signal, not "has always been quiet"
- The flag goes to the responder by DM; never a public label, never visible to other fellows
- An intervention log, as mentoring software has it: who reached out, when, by what channel, what happened
- Follow-up reminders on open interventions that have gone stale
- Escalation tiers: responder → Director → whoever the program decides
- Re-engagement measurement: did the fellow's activity recover after contact?
- ✅ A fellow can self-flag with the help checkbox; that path is separate, routed immediately, and never enters any metric
- ⚠️ Any automated "at-risk score" shown as a number — the tool computes signals; a person decides what they mean (ADR-026 spirit)

## 6. Fellow-facing: the App Home tab for fellows

- My attendance, my check-ins, my Slack activity — the Director asked to "leave room for a model where info is shared with fellows"
- What is collected about me, in plain language, in the app
- Export my data
- Request a correction — the correction process the contract's questions asked about and never got
- Reminder preferences
- Upcoming sessions and what is due
- A "check in with me" button — the help channel, always one tap away
- Shoutouts I have given
- ⚠️ A comparison to peers ("you are in the bottom third") — never; it is the leaderboard again, pointed inward

## 7. Staff-facing: the App Home tab for staff, plus commands

- A dashboard: attendance rate, active fellows this week, review-queue sizes, last data received
- `/attendance <session>` for a quick look
- `/fellow <name>` for a profile card: attendance, activity, open interventions — permission-gated
- The review queue in Slack: resolve an identity, decide a needs-review check-in, link a shoutout name, with buttons
- A post-session summary auto-posted to the staff channel: who attended, Part B response count, muddiest-point themes
- A Monday digest for staff: who has gone quiet, what is due, what is waiting for a human
- `/report` to regenerate and share the HTML report
- Roles: staff, Director, help-responder — each sees what their role needs
- ⚠️ Staff seeing help requests in the general dashboard — no; that screen is gated separately (ADR-025)

## 8. Recognition and gamification

The research is clear that shallow points-and-badges on an intrinsically
motivated group reduces intrinsic motivation, and that leaderboards on
received recognition build popularity contests. So:

- ✅ Peer shoutouts collected and resolved to fellows
- Rank by recognition **given**, never received: "most generous" is safe, "most popular" is not
- Personal streaks, shown only to the fellow: "5 sessions in a row"
- Milestone badges by private DM: first check-in, first shoutout given, tenth message
- A weekly "shoutout of the week" drawn from the giving side
- Everything opt-in; a fellow can turn it all off
- ⚠️ Public leaderboards on participation — no
- ⚠️ Points for showing up — no; attendance is the weakest predictor of outcomes and the easiest to game
- ⚠️ Rewards with cash value — no; the overjustification effect is strongest with tangible rewards

## 9. Community connection

- Coffee-chat pairing across project teams, opt-in, Donut-style
- Intro prompts when a new member joins: three questions, posted for them
- Alumni buddy pairing for first-year fellows
- Project-team channels created from the roster
- Scheduled icebreakers in #general
- Celebrations — only with explicit consent about what is shared

## 10. Content and curriculum feedback

- ✅ Muddiest-point answers clustered into themes for the teacher — about content, never about a person
- "One question I still have" collected after each session and digested for the teacher
- ✅ Q&A in a designated channel captured, with replies, and summarised per session for the teacher
- Resource tracker: links shared, which got reactions, which got reshared
- A per-session feedback loop closed back to fellows: "here is what you said was unclear, and what we are changing" — the single highest-leverage trust move in the research

## 11. Analytics and reporting

- ✅ The self-contained HTML report: every fellow × every session, attendance, confidence, Slack, review queues, provenance
- Community-health metrics from the community field: daily and monthly active, stickiness (DAU/MAU), activation (first message within N days of joining), retention curve by week, engagement tiers (inactive / passive / active / power)
- The funnel: accepted → joined Slack → first message → first check-in → completed
- ✅ Cohort-keyed everywhere, so a second year compares against the first
- Channel health over time
- Peer-support response time in the help channel
- Interaction network: who talks to whom, to find the isolated
- Completion criteria and a completion report
- Scheduled regeneration and delivery of the report — the "evergreen" mechanism
- Export to Google Sheets for anyone who wants to slice it themselves
- ⚠️ A single combined participation score — only after the Director sets the weights; the report shows the three signals side by side until then

## 12. Governance, privacy, consent

- A retention policy engine: delete or anonymise after N months — the `TODO(retention)` that is still open
- Data subject access, export and deletion, on request, by a fellow or a parent
- Consent tracking, including parental consent for anyone under the age the program sets
- The privacy notice inside the app, not only on a form
- An access audit log: who looked at whose record, when
- ✅ Row-level security on every table holding fellow data, with the policy left for CU to write
- Anonymised, cohort-level statistics for anything shared outside CU
- ✅ No direct messages read; no text stored; no biometrics; ⚠️ never presence, never attention inference

## 13. Operations and reliability — the evergreen problem

- A dead-man switch: alert the staff channel when the bot has received nothing for N hours
- Automatic backfill on restart, so a gap heals itself
- One-command hosted deployment, so "who runs it" has an answer that is not a laptop
- ✅ Health endpoint and status page
- Scheduled report regeneration (a GitHub Action or cron)
- Errors posted to a staff channel, not only to a log
- Configuration changed from Slack, not from code
- Multiple workspaces at once: Spring and Fall side by side
- ✅ Idempotent everything, so any of the above can be re-run without fear

## 14. Integrations

- Zoom participant reports, cross-checked against the check-in
- Google Sheets for assignments and the roster
- Google Calendar as the source of truth for the session schedule
- HubSpot contact sync, if CU ends up using it
- Airtable or Notion export for staff who live there
- Email digests for people who are not in Slack

## 15. AI, kept in its lane

- ✅ Passphrase adjudication for answers edit distance cannot read
- ✅ Muddiest-point clustering, text only, no names in the payload
- ✅ Summarise a session's Q&A thread for the teacher — from anonymous strings; a plain digest without a key
- Draft an outreach message for a staff member to edit — never send one
- ✅ Detect a question that has been asked before and point at the earlier answer — word overlap first, the model only among candidates, answered questions only
- ⚠️ Sentiment analysis of individuals — no
- ⚠️ Attention or engagement scoring from video — no (see the Zoom research: scientifically contested, banned in education under the EU AI Act, and Zoom itself removed its version)

---

## If only five could be built next

1. **Part A and Part B as Slack modals** (§3). Removes the Google dependency, uses a stronger identity, and puts the check-in where the fellows already are.
2. **The posting bot** (§4). Form links and reminders on schedule. The failure mode is visible — the link stops appearing — which is the right kind of failure.
3. **The dead-man switch** (§13). The collector's failure mode is silence; this is the one thing that makes it loud.
4. **The staff App Home with the review queue** (§7). The review queues exist; putting them where staff already are is what gets them worked.
5. **The fellow App Home** (§6). Transparency is the trust move the research keeps pointing at, and the Director explicitly asked for room to grow into it.
