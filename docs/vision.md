# What a full-fledged version would do

Everything this system could do if it were finished, drawn from what the best
tools in each adjacent category actually ship: community analytics (Common
Room, Orbit, Threado), check-in bots (Geekbot, Polly, Standuply), student
early-warning systems, recognition bots (HeyTaco, Bonusly, Karma), connection
bots (Donut), cohort platforms (Disco, Circle, Maven), mentoring software
(Chronus, MentorcliQ), and Slack's own app surfaces.

> **Last reconciled against the code on 2026-09-25, at commit `c377642`.** Every
> mark below was re-derived by reading `src/` and `tests/` at that commit. A ✅
> means an implementing file and a covering test both exist; it does not by
> itself mean the path has been exercised against real Slack, and where the
> repo's own verification record (`FEATURES.md`, `FINDINGS.md`) says it has not
> been, the bullet is ◑ and says so.

Legend: **✅ built** · **◑ partially built** · **🔜 next, cheap** · **⚠️ conflicts
with a decision this project has made, and why** · unmarked = possible, not
started.

This is the "room for improvement" half of the lessons-learned report.

---

## 1. What it observes

- ✅ Messages sent, edited, deleted; reactions given and removed; channel joins and leaves
- ✅ Thread replies, message length, links, file attachments (counted, never stored)
- Reply latency between peers: how fast a question in #help-desk gets an answer
- ✅ Who-replies-to-whom graph, for spotting fellows nobody talks to — `slack/insights.py:46`, test `tests/test_slack_signals.py:194`
- ✅ Mentions given (received are recorded, never ranked) — extracted at `slack/events.py:81` before the text is dropped, aggregated at `slack/insights.py:129`, test `tests/test_slack_signals.py:209`
- ✅ Huddle joins and leaves (Slack emits these events) — `slack/events.py:401`, `slack/insights.py:166`, test `tests/test_slack_signals.py:228`
- ◑ Canvas edits and comments (edits, creates and shares work — `slack/events.py:437`, `slack/insights.py:205`, test `tests/test_slack_signals.py:228`; comments never arrive, because `file_comment_added` is not a valid bot event type and Slack refuses the whole manifest with it in, see `docs/setup/slack-bot.md:170`)
- ✅ Which emoji are used, as a cohort-level mood signal — never per person — `slack/insights.py:231`, test `tests/test_slack_signals.py:216`, which asserts the query reads no user column
- ✅ Channel-level liveness: which channels are alive, which have gone quiet — `slack/insights.py:280`, test `tests/test_slack_signals.py:246`
- ✅ Time-of-day and day-of-week activity, so reminders land when fellows are around — `slack/insights.py:321`, consumed by the reminder engine at `slack/reminders.py:363`, tests `tests/test_slack_signals.py:281` and `tests/test_reminders.py:127`
- ✅ Poll and vote responses to bot-run polls — `slack/polls.py:20`, `slack/events.py:505`, tests `tests/test_slack_signals.py:340` and `:412`. The duplicate `action_id` that made every multi-option poll unpostable (F-12) is fixed at `slack/polls.py:31`
- ◑ Zoom attendance ingest, cross-checked against the form check-in (a transcript is ingested at `zoom.py:98` and `zoom.py:192` lists fellows who checked in to Part A but never spoke, test `tests/test_engagement.py`; nothing reads a Zoom *participant report*, so a fellow who attended silently is indistinguishable from one who was absent)
- ◑ Assignment submissions from the Google Sheet (submissions and scores are recorded and reported — `assignments.py:252`, `assignments.py:269`, `/assignment` and `/score` — but a human types them; nothing reads a Sheet)
- Calendar RSVPs for sessions
- ⚠️ Presence (online/away) — available via the API, deliberately not collected; it is surveillance of when a teenager is at their keyboard
- ⚠️ Message text — off by default; the definition counts acts, it does not read them (ADR-031)
- ⚠️ Read receipts — Slack does not expose them; nothing to decide

## 2. Identity and roster

- ✅ Slack user id → email cache; roster join at read time so a corrected roster re-attributes history
- ✅ Unmatched addresses queued for a human, never dropped, never guessed
- Automatic roster sync from the Google Sheet on a schedule
- ◑ `/roster add|remove|link` for staff, with an audit trail (`/link` at `slack/commands.py:480` and `/alias` at `:470` both write `by_email` provenance — `slack/identity.py:147`, `:209`, test `tests/test_slack_bot.py:153`; there is no `add` or `remove`, so a roster still arrives only through `cufa load-roster`)
- ◑ Alert staff when someone joins the workspace who is not on the roster (handler `slack/app.py:75`, post `slack/digest.py:265`, test `tests/test_slack_bot.py:344`; `team_join` is now in the manifest — `docs/setup/slack-bot.md:162` — but it has never fired, because the workspace has one human, so the live path is unproven end to end)
- ✅ Second-email aliases: a fellow's school and personal addresses on one record — `slack/identity.py:147`, test `tests/test_slack_bot.py:153`, and `:168`, which proves one address cannot belong to two fellows
- Workspace membership as the cohort boundary, per the Director's definition: leaving the workspace flips status to withdrawn automatically
- ◑ Full status lifecycle: applied → accepted → active → deferred → withdrawn → completed → alumni (the check constraint at `supabase/migrations/20260801000100_core_reference.sql:30` allows four of these — active, withdrawn, deferred, alumni; `accepted` and `completed` exist only as timestamps on `fellow`, `applied` not at all, and nothing transitions a status automatically)
- Same person across cohorts, so an alumni mentor is linked to their own fellowship year

## 3. Check-ins inside Slack (instead of Google Forms)

- Part A passphrase check-in as a Slack modal, opened from a button the bot posts mid-session — identity is the workspace-scoped Slack id, no Google account needed
- Part B exit ticket as a modal: 7-point scale as radio buttons, short text, the rotating slot, the shoutout, the help checkbox
- ◑ The rotating-question schedule enforced by the bot (the schedule is owned and enforced — `config/rotation.json`, `rotation.py`, applied at `provisioning.py:361` — but on the Google Forms path; there is no Slack modal for it to govern)
- ◑ `/passphrase <word>` for the teacher, which also stamps `announced_at` (announce-and-stamp exists, in the console at `console/app.py:1662`, test `tests/test_console.py:648`; there is no such slash command — `slack/commands.py:530` lists every one that exists)
- ◑ Bot posts the check-in button at a scheduled offset, so nothing depends on a teacher remembering (the button and its handler are built — `slack/welcome.py:37`, `slack/app.py:89`, test `tests/test_slack_bot.py:471` — but it ships once in the welcome DM; nothing posts it per session at an offset)
- ◑ DM the check-in to anyone who was in the channel but did not submit (non-submitters are DM'd — `slack/reminders.py:631`, test `tests/test_reminders.py:274` — but the set is the roster, not who was observed in the channel)
- A confirmation DM after submitting
- ✅ Timezone-aware scheduling per session — the session carries its own zone (`sessions.py:31`) and every reminder is rendered and quiet-hour-checked in it (`slack/reminders.py:101`, `:339`), test `tests/test_reminders.py:171`
- ⚠️ Would replace the Verified-email Google Form. Worth doing: Slack identity is stronger and needs no Google account. Keep the Forms path as the fallback for a workspace outage.

## 4. Reminders, nudges, scheduling

- ✅ Session reminders at 24h, 1h and 10 min, with the Zoom link — `slack/reminders.py:529`, link at `:182`, offsets at `:43`, test `tests/test_reminders.py:186`
- ✅ Assignment-due reminders — `slack/reminders.py:581`, test `tests/test_reminders.py:220`
- ✅ Nudge non-submitters after Part B closes — capped at two, personalised, by DM (research: a third reminder reads as pressure) — `slack/reminders.py:631`, offsets at `:56`, test `tests/test_reminders.py:274`; the cap is also a database constraint, so a third nudge is impossible whatever the code does
- ✅ A weekly digest to fellows: this week's session, what is due, what changed — `slack/reminders.py:818`, text at `:886`, test `tests/test_reminders.py:358`
- ✅ Quiet hours: no DMs late at night, in the fellow's timezone — `slack/reminders.py:74`, window resolved per fellow at `:380`, tests `tests/test_reminders.py:171` and `:206`
- ✅ Reminder preferences a fellow controls: fewer, later, none — `slack/preferences.py:103`, `/reminders` at `slack/commands.py:206`, test `tests/test_reminders.py:396`
- ◑ Bot-posted agenda at session start (`slack/reminders.py:749`, test `tests/test_reminders.py:260`; it has never fired live — no announcement channel is configured, so the tick at session start produced nothing, see `FEATURES.md`)

## 5. Early warning and intervention

- Rules in a config file the Director owns, not in code: e.g. no check-in for 2 consecutive sessions AND no Slack activity for 14 days
- ✅ Flagging is gated on a named responder existing; a flag with nobody to act on it is never raised
- Change detection, not just level: "was active, went quiet" is the signal, not "has always been quiet"
- ◑ The flag goes to the responder by DM; never a public label, never visible to other fellows (it does reach the named responder privately and is never a public label — `help_routing.py:224`, `help_requests.py:64`, test `tests/test_safeguarding.py`; delivery is email, `help_routing.py:293`, not a Slack DM)
- ✅ An intervention log, as mentoring software has it: who reached out, when, by what channel, what happened — `interventions.py:33`, which records kind, `by_email`, `by_slack_user`, note and source, test `tests/test_engagement.py:152`
- Follow-up reminders on open interventions that have gone stale
- Escalation tiers: responder → Director → whoever the program decides
- Re-engagement measurement: did the fellow's activity recover after contact?
- ✅ A fellow can self-flag with the help checkbox; that path is separate, routed immediately, and never enters any metric
- ⚠️ Any automated "at-risk score" shown as a number — the tool computes signals; a person decides what they mean (ADR-026 spirit)

## 6. Fellow-facing: the App Home tab for fellows

- ◑ My attendance, my check-ins, my Slack activity — the Director asked to "leave room for a model where info is shared with fellows" (all three are there, at `console/dashboard.py:423` behind a signed `/me/<token>` link, plus the `/me` command; test `tests/test_dashboards.py:136`. It is a web page and a slash command, not an App Home tab — nothing in the tree calls `views.publish`)
- What is collected about me, in plain language, in the app
- ✅ Export my data — `console/dashboard.py:462`, CSV built at `:179`, button at `frontend/src/Fellow.jsx:677`, test `tests/test_dashboards.py:149`
- Request a correction — the correction process the contract's questions asked about and never got
- ✅ Reminder preferences — `console/dashboard.py:373` on the fellow's own page and `/reminders` in Slack, test `tests/test_dashboards.py:190`
- Upcoming sessions and what is due
- ✅ A "check in with me" button — the help channel, always one tap away — `slack/welcome.py:37`, handled at `slack/app.py:89`, pinged to staff at `slack/commands.py:246`, test `tests/test_slack_bot.py:471`
- Shoutouts I have given
- ⚠️ A comparison to peers ("you are in the bottom third") — never; it is the leaderboard again, pointed inward

## 7. Staff-facing: the App Home tab for staff, plus commands

- ◑ A dashboard: attendance rate, active fellows this week, review-queue sizes, last data received (all four are on the console's staff dashboard — `console/dashboard.py:259`, `:74`, rendered at `frontend/src/Dashboard.jsx:647`, `:663`, `:709`, test `tests/test_dashboards.py:90`; it is a web page that `/admin-dashboard` hands staff a link to, not an App Home tab)
- ✅ `/attendance <session>` for a quick look — `slack/commands.py:304`, test `tests/test_slack_bot.py:440`
- ✅ `/fellow <name>` for a profile card: attendance, activity, open interventions — permission-gated — `slack/commands.py:309`, gate at `slack/permissions.py:21` and `:76`, test `tests/test_slack_bot.py:384`
- ◑ The review queue in Slack: resolve an identity, decide a needs-review check-in, link a shoutout name, with buttons (identity resolution is there as commands — `/alerts` at `slack/commands.py:490`, `/alias`, `/link` — but a needs-review check-in and a shoutout name can only be decided in the console, `console/app.py:1876`, and none of it has buttons: the only Block Kit actions the bot registers are the check-in button and poll votes)
- ✅ A post-session summary auto-posted to the staff channel: who attended, Part B response count, muddiest-point themes — `slack/digest.py:176`, text at `:118`, test `tests/test_slack_bot.py:316`
- ✅ A Monday digest for staff: who has gone quiet, what is due, what is waiting for a human — `slack/digest.py:248`, text at `:206`, test `tests/test_slack_bot.py:330`
- ✅ `/report` to regenerate and share the HTML report — `slack/commands.py:345`, test `tests/test_slack_bot.py:442`
- ◑ Roles: staff, Director, help-responder — each sees what their role needs (two roles are enforced in Slack, staff and fellow — `slack/permissions.py:21`, test `tests/test_slack_bot.py:384` — and the help-responder is a third, separate allowlist on the console's help screen, `config/help_routing.json`, test `tests/test_console.py:320`; the Director is not a distinct role anywhere)
- ⚠️ Staff seeing help requests in the general dashboard — no; that screen is gated separately (ADR-025)

## 8. Recognition and gamification

The research is clear that shallow points-and-badges on an intrinsically
motivated group reduces intrinsic motivation, and that leaderboards on
received recognition build popularity contests. So:

- ✅ Peer shoutouts collected and resolved to fellows
- ✅ Rank by recognition **given**, never received: "most generous" is safe, "most popular" is not — `slack/badges.py:347`, where `shoutouts_given` is a rank key and no received-side key exists, test `tests/test_slack_bot.py:306`
- ✅ Personal streaks, shown only to the fellow: "5 sessions in a row" — computed at `slack/badges.py:90`, rendered to the fellow at `:272`, tests `tests/test_slack_bot.py:286` for the arithmetic and `:391` for a fellow's `/badges` naming nobody else
- ✅ Milestone badges by private DM: first check-in, first shoutout given, tenth message — rules at `slack/badges.py:74`, DM at `:290`, test `tests/test_slack_bot.py:293`
- A weekly "shoutout of the week" drawn from the giving side
- ◑ Everything opt-in; a fellow can turn it all off (the off switch works and is honoured — `slack/preferences.py:134`, test `tests/test_slack_bot.py:297`, "Bo opted out and hears nothing"; the default is `gamification: True` at `slack/preferences.py:28`, so it is opt-**out**, not opt-in)
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
- ◑ "One question I still have" collected after each session and digested for the teacher (the muddiest-point slot asks it — "What's still unclear?", `config/rotation.json` — and is clustered at `themes.py:314`; but it is the same rotating field, scheduled on sessions 2, 5 and 8 only, so it is not collected after *each* session)
- ✅ Q&A in a designated channel captured, with replies, and summarised per session for the teacher
- Resource tracker: links shared, which got reactions, which got reshared
- A per-session feedback loop closed back to fellows: "here is what you said was unclear, and what we are changing" — the single highest-leverage trust move in the research

## 11. Analytics and reporting

- ✅ The self-contained HTML report: every fellow × every session, attendance, confidence, Slack, review queues, provenance
- Community-health metrics from the community field: daily and monthly active, stickiness (DAU/MAU), activation (first message within N days of joining), retention curve by week, engagement tiers (inactive / passive / active / power)
- ✅ The funnel: accepted → joined Slack → first message → first check-in → completed — `funnel.py:23`, per-cohort roll-up at `:98`, test `tests/test_engagement.py:185`, on both dashboards (`console/dashboard.py:92`, `:137`)
- ✅ Cohort-keyed everywhere, so a second year compares against the first
- ◑ Channel health over time (`slack/insights.py:280` classifies every channel alive / quiet / silent by recency, test `tests/test_slack_signals.py:246`; it is a snapshot — nothing stores or plots the series, so "this channel is dying" is not a question it can answer)
- Peer-support response time in the help channel
- ✅ Interaction network: who talks to whom, to find the isolated — `slack/insights.py:46`, which also returns the fellows nobody has replied to, test `tests/test_slack_signals.py:194`
- ◑ Completion criteria and a completion report (completion is recordable and is the funnel's last stage — `funnel.py:113`, `:23`, test `tests/test_engagement.py:185`; the criteria are nowhere, so a human sets the flag by hand, and there is no completion report)
- Scheduled regeneration and delivery of the report — the "evergreen" mechanism
- Export to Google Sheets for anyone who wants to slice it themselves
- ⚠️ A single combined participation score — only after the Director sets the weights; the report shows the three signals side by side until then

## 12. Governance, privacy, consent

- A retention policy engine: delete or anonymise after N months — the `TODO(retention)` that is still open
- ◑ Data subject access, export and deletion, on request, by a fellow or a parent (access and export are built for the fellow themselves — `console/dashboard.py:423`, `:462`, test `tests/test_dashboards.py:149`; there is no deletion path, and by design there cannot be a simple one — `supabase/migrations/20260801000700_checkin_immutability.sql:20` refuses to delete a check-in — and no parent route at all)
- Consent tracking, including parental consent for anyone under the age the program sets
- The privacy notice inside the app, not only on a form
- An access audit log: who looked at whose record, when
- ✅ Row-level security on every table holding fellow data, with the policy left for CU to write
- Anonymised, cohort-level statistics for anything shared outside CU
- ✅ No direct messages read; no text stored; no biometrics; ⚠️ never presence, never attention inference

## 13. Operations and reliability — the evergreen problem

- A dead-man switch: alert the staff channel when the bot has received nothing for N hours
- ◑ Automatic backfill on restart, so a gap heals itself (the backfill works and is idempotent — `slack/backfill.py:113`, `:222`, test `tests/test_slack.py:114`, and the recovery was proven live in `FEATURES.md`; it is **not** automatic. Nothing calls it: its only caller in the tree is the `cufa slack backfill` CLI, and neither `slack/bot.py` startup nor `slack/digest.py:323`'s tick invokes it, so a gap heals only when a human notices and runs it)
- ◑ One-command hosted deployment, so "who runs it" has an answer that is not a laptop (`deploy/vercel/` deploys both halves from one project and a real tick ran on it against Supabase — `deploy/vercel/api/index.py:49`, route tested at `tests/test_slack.py:389`; it is more than one command: there is no `deploy` target in `tasks.py`, the environment has to be populated by hand, and `deploy/vercel/README.md` explains that Vercel's own cron cannot hit `/bot/cron/tick` every minute, so an external `pg_cron` job plus a Vault secret has to be set up separately)
- ✅ Health endpoint and status page
- Scheduled report regeneration (a GitHub Action or cron)
- Errors posted to a staff channel, not only to a log
- ◑ Configuration changed from Slack, not from code (per-session and per-fellow settings are: `/zoom` sets the link every reminder carries, `/assignment` creates due dates, `/reminders` sets a fellow's cadence — `slack/commands.py:530` for the full list, test `tests/test_slack_bot.py:440`; deployment configuration — staff channel, cohort, quiet hours, Q&A channels, admins — is environment-only, `config.py`)
- ◑ Multiple workspaces at once: Spring and Fall side by side (the schema is keyed for it — every observation carries `team_id`, and `slack_workspace` maps a team to a cohort at `slack/store.py:79` — but one deployment carries one bot token and one `CUFA_SLACK_COHORT`, so two workspaces means two deployments)
- ✅ Idempotent everything, so any of the above can be re-run without fear

## 14. Integrations

- ◑ Zoom participant reports, cross-checked against the check-in (a cloud-recording **transcript** is parsed and cross-checked — `zoom.py:70`, `:98`, `:192` — and unmatched speakers are listed rather than guessed at; the participant report, which is what actually carries join and leave times, is not read)
- ◑ Google Sheets for assignments and the roster (the roster loads from a CSV export of a Sheet — `roster.py:131` — and the Sheets timezone trap it brings with it is handled at `ingest/csv_path.py:49`; nothing talks to the Sheets API, and assignments are typed in through `/assignment` and `/score`)
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

Written before the reconciliation above. As of `c377642`, **2 is built** —
reminders, nudges, digests, agendas and the Zoom link all ship from one engine,
`slack/reminders.py`. 1, 3 and 5 are untouched; 4 exists as a web console
rather than an App Home tab.

1. **Part A and Part B as Slack modals** (§3). Removes the Google dependency, uses a stronger identity, and puts the check-in where the fellows already are.
2. **The posting bot** (§4). Form links and reminders on schedule. The failure mode is visible — the link stops appearing — which is the right kind of failure.
3. **The dead-man switch** (§13). The collector's failure mode is silence; this is the one thing that makes it loud.
4. **The staff App Home with the review queue** (§7). The review queues exist; putting them where staff already are is what gets them worked.
5. **The fellow App Home** (§6). Transparency is the trust move the research keeps pointing at, and the Director explicitly asked for room to grow into it.
