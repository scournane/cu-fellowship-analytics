# TESTLOG — cu-fellowship-analytics end-to-end verification

Run started 2026-09-14. Environment: Anthropic cloud container (Linux 6.18, x86_64),
Python 3.11.15, Docker 29.4.3, Supabase CLI 2.117.0, Node 22.22.2, npm 10.9.7.
Repo: https://github.com/scournane/cu-fellowship-analytics @ `main` (d125e12).

Tokens are redacted everywhere in this file to the form `xoxb-…3f2a`.

---

## Phase 0 — environment and setup

| # | Check | Expected | Actual | Result | Evidence |
|---|---|---|---|---|---|
| 0.1 | `git clone` then look for the Slack bot | Slack bot present | **No `*slack*` file anywhere, no `docs/setup/slack-bot.md`, no `demo-slack` target.** The repo's remote HEAD is `claude/civics-unplugged-analytics-tool-6nfb9b`, not `main`, so a plain clone checks out a branch with no bot at all. `git checkout main` fixed it. | **FAIL (repo config)** | `git branch -r`; HEAD symref; see finding F-01 |
| 0.2 | `git --version` | present | 2.43.0 | PASS | |
| 0.3 | `python3 --version` (need 3.11+) | ≥3.11 | 3.11.15 | PASS | |
| 0.4 | `docker info` (installed **and running**) | running | Installed, daemon not started. Started it manually (`setsid dockerd`); note the daemon does not survive between shell invocations unless detached with `setsid`. | PASS (after fix) | `docker info` → Server 29.4.3, overlayfs |
| 0.5 | `supabase --version` | present | Not installed. GitHub releases API is gated in this environment (403), so the documented download path failed; installed via `npm install -g supabase` instead → 2.117.0 | PASS (after fix) | see finding F-02 |
| 0.6 | `node -v` (optional frontend) | present | v22.22.2 | PASS | |
| 0.7 | Ports 64321/64322/64323/3000/3001/8000 free | all free | all free | PASS | `ss -ltn` |
| 0.8 | `python tasks.py doctor` before setup | names what is missing | `MISS Dependencies`, `MISS Console bundle`, everything else ok; printed the exact fix commands | PASS | |
| 0.9 | `python tasks.py setup` | .venv + `pip install -e ".[dev]"` + checks | Completed; also built the console bundle (vite 8.2.2, 2190 modules, 6.59s) | PASS | `cufa 0.1.0 installed in .venv` |
| 0.10 | `cp .env.example .env` | .env exists, gitignored | Created, 96 non-blank lines; `git check-ignore` confirms `.gitignore:2:.env` | PASS | |
| 0.11 | `python -m cufa.crypto keygen` → `CUFA_ENCRYPTION_KEY` | 44-char Fernet key written | 44 chars, written into `.env` | PASS | key redacted |
| 0.12 | `python tasks.py doctor` after setup | all green | All six rows `ok` | **PASS — Phase 0 exit criteria met** | |


---

## Phase 1 — the system works at all (no Slack, no Google, no API keys)

| # | Check | Expected | Actual | Result | Evidence |
|---|---|---|---|---|---|
| 1.1 | `python tasks.py db-up` | stack up, migrations applied | 18 migrations applied, seed loaded; DB 64322, Studio 64323, API 64321 | PASS | db-up output |
| 1.2 | `python tasks.py demo` | full pipeline + acceptance checks green | 36 acceptance checks, all `ok`, `all acceptance checks passed` | PASS | `/tmp/demo1.log` |
| 1.3 | Expected output #1: `blocked, as designed` | present in step 2 | Present **twice** (Part A template `fake-form-0001`, Part B template `fake-form-0002`): "emailCollectionType is not VERIFIED yet" | PASS | demo1.log:86, :117 |
| 1.4 | Expected output #2: teacher-question week refused, not substituted | refusal | "0 session(s) provisioned, 1 failed" then `blocked, as designed: no generic question was substituted`; weeks 10 and 11 pre-flagged "NOT SET — provisioning will refuse this week" | PASS | demo1.log:140, :155–158 |
| 1.5 | `python tasks.py demo-again` — identical numbers | byte-identical | 36 acceptance lines vs 36, `diff` empty | **PASS (idempotent)** | `diff /tmp/acc_demo.txt /tmp/acc_again.txt` |
| 1.6 | `python tasks.py test` | ~597 tests, no network | **597 passed** in 47.09s, 1 unrelated Starlette deprecation warning | PASS | `/tmp/tests.log` |
| 1.7 | `out/report.html` renders standalone | self-contained | 67 128 bytes, **zero** external `src`/`href` to http(s), 0 console errors in headless Chromium, title renders, 9 078 chars of body text | PASS | screenshot `/tmp/report_full.png` |
| 1.8 | Report contains all documented sections | 7 sections | attendance by session, every-fellow grid, attention index, assignments, funnel, confidence trend, Slack activity, awaiting-a-human, provenance — all present | PASS | same screenshot |
| 1.9 | Report contains **no email addresses** | 0 | `grep -cE '<addr regex>'` → **0** | PASS | |
| 1.10 | Report contains nothing from the help table | 0 | 0 matches for `help_request` / "check in with me" in the HTML; `open_requests` is read from `intervention` where `kind='check_in_request'`, never from `help_request` | PASS | report_html.py:520–526 |
| 1.11 | No view anywhere reads `help_request` | none | `information_schema.views` where definition ilike `%help_request%` → **0 rows**. Only `cufa help-requests`, `help_requests.py` and the console's access-restricted screen touch it | PASS | |
| 1.12 | Console: every screen clickable | 13 screens | sessions, session detail, responses, roster, review, template, assignments, assignments/new, rotation, shoutouts, dashboard, dashboard/fellow → all HTTP 200, no JS errors. `/help-requests` → **403 by design** (separate `CUFA_HELP_ALLOWLIST`; falls back to `adiah@civicsunplugged.org` from `config/help_routing.json`, and I signed in as `staff@civicsunplugged.org`) | PASS | `/tmp/console_*.png` |
| 1.13 | `/dashboard` staff screen | all documented panels | overall attendance tile, most-active-this-week, fellows by attention index with components and per-row *mark reached out*, badges and ranks, assignments + score form, funnel, per-source freshness line, Export CSV | PASS (but see 1.15) | `/tmp/console_dashboard_demo.png` |
| 1.14 | `Export CSV` | downloads, no addresses | HTTP 200, 1 742 bytes, header `fellow_id,full_name,status,messages,…`, **0 email addresses** | PASS | |
| 1.15 | Dashboard attendance headline | agrees with the report | **"0% overall attendance · 47 of 20 × 0 sessions"** while `out/report.html` says **24%** on the same database | **FAIL** | finding F-03 |
| 1.16 | Studio SQL: current decision per fellow | one row per fellow | 20 fellows, counts of attended / needs_review / not_attended | PASS | doc snippet [2] |
| 1.17 | Studio SQL: needs-review queue | populated | 47 rows, every one `decided_how = ai_unavailable` (no `GEMINI_API_KEY` set — documented behaviour, not a fault) | PASS | doc snippet [4] |
| 1.18 | Studio SQL: `identity_unresolved` | unknown address queued | 1 row, `someone.else@example.invalid`, occurrence_count 2, `resolved_at` null | PASS | doc snippet [6] |
| 1.19 | Studio SQL: provisioning log | every attempt recorded | copy_form 21 success, provision 21 success + **1 skipped** (the teacher-question week), publish 21 success, question_map 11 success | PASS | doc snippet [22] |
| 1.20 | `google_credential.refresh_token_enc` renders as bytes | Fernet ciphertext, not a readable token | Column type is `bytea`. The demo never connects Google so the table is empty; proved the round trip directly instead: `encrypt_secret` → 140 opaque bytes beginning `b'gAAAAAB…'` (Fernet), plaintext **not** present in the blob, `decrypt_secret` returns the original | PASS (by type + round trip) | |
| 1.21 | `python tasks.py frontend` | builds | Built as part of `setup`: vite 8.2.2, 2 190 modules, console.js 714 kB | PASS | |
| 1.22 | `python tasks.py demo-ai` | tier-2 live, then cached | **BLOCKED** — waiting on `GEMINI_API_KEY` | BLOCKED | |

---

## Phase 2 — the bot against a fake Slack

Samson asked for real Slack rather than the fake one, so the interactive
click-through (`python tasks.py demo-slack`) was skipped. The headless CI suite
was **not** skipped, for one reason: two rows of the matrix cannot be produced
from a real workspace at all. A human cannot make Slack send a forged signature,
and Socket Mode does not sign deliveries in the first place, so the signature
boundary is only observable against the fake server. Same for a replayed
delivery.

`python tasks.py demo-slack-batch` — **48 acceptance checks, all `ok`**, exit 0.
Note that this target calls `db-reset`, so it destroyed the Part A/B demo data
from Phase 1 and re-seeded; Phase 1's evidence was captured before that.

| # | Check | Expected | Actual | Result |
|---|---|---|---|---|
| 2.1 | every accepted delivery recorded | ≥63 | 63 rows | PASS |
| 2.2 | **replayed delivery** | 200 + nothing written | Hand-crafted probe: same event posted twice with a valid signature, second carrying `X-Slack-Retry-Num: 1`. Both answered **200**; counters moved `written=1, duplicate=1, retries_seen=1`; `slack_event` went 63 → **64**, one row | **PASS (by eye)** |
| 2.3 | **forged signature** | refused | Same body signed with the wrong secret → **401** `{"error": "invalid request"}`, nothing written | **PASS (by eye)** |
| 2.4 | stale timestamp | refused | Valid secret, timestamp −10 min → **401**. (Not in the brief; the replay window is closed too) | PASS |
| 2.5 | no duplicate `source_event_id` | none | `ok`. The key is a hash of the *act* (`7e88b655…`), not Slack's `event_id` — which is why a replay collides | PASS |
| 2.6 | bot messages skipped | nothing recorded | `ok` | PASS |
| 2.7 | **no message text stored** | none | All 64 rows: `text` is NULL. `text_length` / `word_count` / `has_link` kept (shape, not content). `raw` is stripped to `{type, event_ts, channel_type}` — no text there either. 0 rows have a `text` key in `raw` | **PASS** |
| 2.8 | roster attribution by email | 18 | `ok` | PASS |
| 2.9 | non-roster addresses queued, not dropped | 2 | `ok` | PASS |
| 2.10 | profiles with **no email** still recorded | 2 | `ok`, and 2 rows carry `user_email` NULL. Documented behaviour | PASS |
| 2.11 | backfill idempotent | 0 new rows on re-read | re-read 18 messages, wrote **0** | PASS |
| 2.12 | every event type present | 10 types | canvas_comment, canvas_created, canvas_edited, huddle_joined, huddle_left, member_joined_channel, message, message_changed, poll_vote, reaction_added | PASS |
| 2.13 | `slack_event` immutability trigger | installed | `ok` | PASS |
| 2.14 | huddle rows carry **no channel** | none | 2 joins, 2 leaves, no channel | PASS |
| 2.15 | canvas edit attributed to nobody | no user | `ok` (Slack names no editor) | PASS |
| 2.16 | non-canvas file looked up and skipped | skipped | `ok` (via `files.info`) | PASS |
| 2.17 | mentions extracted **before** text dropped | 4 messages | `ok` | PASS |
| 2.18 | poll: latest vote per person only | Thursday 2, Tuesday 1 | 4 votes from 3 people, a changed vote is a new row, tally counts the latest | PASS |
| 2.19 | emoji mood is cohort-level | no user named | `ok` | PASS |
| 2.20 | reply graph never reports a received count | `ok` | PASS |
| 2.21 | Q&A capture | 3 questions, 1 reply | `ok` | PASS |
| 2.22 | ✅ marks an answer accepted | `ok` | PASS |
| 2.23 | **repeated question → pointer to earlier answer** | posted | `ok`, `method=lexical similarity=0.833` | PASS |
| 2.24 | `@bot summary` → session digest | posted | `ok`, 3 questions, 1 answered | PASS |
| 2.25 | what the bot posted names **no address** | none | `ok` | PASS |
| 2.26 | Q&A text lives in Q&A tables, not `slack_event` | `ok` | PASS |
| 2.27 | welcome DM exactly once per fellow | 20 of 20 | `ok`, each recorded so a re-run sends none | PASS |
| 2.28 | welcome carries the check-in button | 20 of 20 | `ok` | PASS |
| 2.29 | 1-hour session reminder + Zoom link | 20 | `ok`, link set from Slack by a staff member | PASS |
| 2.30 | 24-hour assignment reminder + submission link | 20 | `ok` | PASS |
| 2.31 | one `bot_delivery` row per reminder | 40/40 | `ok` | PASS |
| 2.32 | no DM to the staff account | none | `ok` | PASS |
| 2.33 | no DM names an address | none | `ok` | PASS |
| 2.34 | staff channel private + flagged | `cohort-private` | `ok`; every staff-facing post went to it and none names an address | PASS |
| 2.35 | session summary + weekly digest + roster alert posted | 3 kinds | `ok` | PASS |
| 2.36 | bot status page `/` | no addresses | HTTP 200, **0 addresses**; page states "No addresses are shown here or logged" | PASS |
| 2.37 | `/stats` JSON | no addresses | HTTP 200, **0 addresses** — counts and channel names only | PASS |
| 2.38 | bot log at `CUFA_LOG_LEVEL=INFO` | no addresses | **0 addresses** across the whole run | PASS |
| 2.39 | only `/`, `/stats`, `/health` served | others 404 | `/admin` `/events` `/debug` `/metrics` → **404**; `/slack/events` → **405** on GET (POST-only, correct) | PASS |

**Phase 2 exit criteria met.** From evidence, not assumption: no message text and
no addresses were stored or posted.

---

## Phase 4 — behaviours provable without the workspace

Run while waiting on the real Slack app. Everything here is offline evidence
for rows that appear in the Phase 4 matrix; the rows that need a human in a real
workspace are listed as BLOCKED in the Phase 3/4 section below.

| # | Check | Expected | Actual | Result |
|---|---|---|---|---|
| 4.1 | `/help` | fellow command list | 5 commands, no staff ones shown | PASS |
| 4.2 | `/me` | own data only | own attendance, exit tickets, message counts. No other fellow named | PASS |
| 4.3 | `/dashboard` | signed link, 7 days | `http://127.0.0.1:8000/me/<signed token>`, stated as "private link, valid for 7 days" | PASS |
| 4.4 | `/badges` | own badges + streak | own badges, streak, counts, `/badges off` hint | PASS |
| 4.5 | `/checkin <note>` | staff ping + request opened | `intervention recorded fellow=CU-2600 kind=check_in_request`; reply said no staff channel is configured yet, so nobody was pinged — correct, `CUFA_SLACK_STAFF_CHANNEL` is blank | PASS |
| 4.6 | `/reminders` | current cadence | session 24h/1h/10m, assignment 24h/1h/10m, badges on | PASS |
| 4.7 | `/cufa-reminders status` **via `cufa slack cmd`** | cadence | **"I don't know `/cufa-reminders`. Try `/help`."** | **FAIL** — finding F-04 |
| 4.8 | staff command as a **non-admin** | refused | `⛔ /fellow is a staff command. Ask a workspace admin, or have your address added to CUFA_SLACK_ADMINS.` | **PASS (negative case)** |
| 4.9 | `/report` as admin | cohort summary | sessions, fellows, attendance, check-ins, exit tickets, most active, attention index, per-source freshness | PASS (number wrong — F-03) |
| 4.10 | `/fellow <name>` | profile card | aliases, attendance, activity vs cohort mean, attention index + why, outreach flag, assignments, interventions, funnel | PASS (fraction wrong — F-03) |
| 4.11 | `/attendance next` | session card | next session, checked in 0/20, exit tickets 0/20, names of non-attenders | PASS |
| 4.12 | `/leaderboard` + `checkins` | staff ranking | labelled "(staff view — not shown to fellows)" | PASS (counts wrong — F-03) |
| 4.13 | `/assignment create` | created | `Created *Solvathon deck v2* (Solvathon), due 2026-10-01 18:00 America/New_York. Reminders go out 24h, 1h and 10m before.` | PASS |
| 4.14 | `/assignment list` | lists | title, kind, due in cohort timezone, submitted/scored counts, link, id | PASS |
| 4.15 | `/score <a> <f> 87 "note"` | recorded | `score recorded assignment=… fellow=CU-2601`; `Recorded 87 for *Bexley Brambleton*` | PASS |
| 4.16 | `/zoom next <url>` | link set | `Zoom link set on *Lesson 2 — deliberation*. It will be in every reminder.` | PASS |
| 4.17 | `/outreach <f> "note"` then `/outreach clear <f>` | set then cleared | both, each an `intervention` row with provenance | PASS |
| 4.18 | `/alias <f> <addr> personal` | second address added | `*Delphine Dunmore* now also resolves from other@example.invalid … past ones included` | PASS |
| 4.19 | **alias an address that is another fellow's primary** | refused | `⚠️ alias faro.fallowmere@example.invalid is the primary address of fellow CU-2605` | **PASS (invariant)** |
| 4.20 | `/link @someone <fellow>` | linked | alias added + slack user linked; **log line redacts the address to `g***@example.invalid` at INFO** | PASS |
| 4.21 | `/alerts` | non-roster accounts | 2 listed, including one with no email on profile | PASS |
| 4.22 | `/alerts resolve @u staff` | resolved | `Resolved: <@U0DEMO0029> marked as staff.` | PASS |
| 4.23 | `/digest` with no staff channel | refuses clearly | `⚠️ No staff channel the bot can find. Set CUFA_SLACK_STAFF_CHANNEL … and invite the bot to it.` | PASS |
| 4.24 | `/sync` | pulls | `users=0 new_users=0 alerts=0 channels=0 messages_read=0 messages_written=0` | PASS |
| 4.25 | **two-nudge cap enforced by the database** | third refused | Inserted nudge 1 and nudge 2 directly. Nudge **3** → `violates check constraint "bot_delivery_nudge_number_valid"`. Re-sending nudge **2** under a fresh dedupe key → `violates unique constraint "bot_delivery_two_nudges_only"`. Exactly 2 rows landed | **PASS — holds even if the application code regresses** |
| 4.26 | `bot_delivery` written **before** the send | pending→sent | `status` is one of pending/sent/failed with `attempt_count` 1–3 and a unique `dedupe_key`; the fake-Slack run produced 40 rows for 40 messages | PASS |
| 4.27 | **quiet hours skip, not delay** | skipped | `is_quiet_time` → `_should_send` returns `False` and increments `suppressed_quiet_hours`, surfaced as `skipped_quiet`. There is no defer path: the strings `defer`, `postpone`, `reschedul`, `retry_after`, `queue_for_later` appear nowhere in `reminders.py`. Overnight windows wrap midnight correctly (22:30 ✓, 03:00 ✓, 07:59 ✓, 08:00 ✗); a window set over "now" suppresses | **PASS** |
| 4.28 | badges DM'd privately, never posted publicly | none public | fake-Slack check: "every badge award reachable on Slack was announced — 0 unannounced"; no public badge post exists, and `/leaderboard` is explicitly labelled staff-only | PASS |
| 4.29 | bot never edits a tally into a poll message | never | `bot.py:576` comment and code path: "results are never edited into it, so a channel cannot watch a tally move" | PASS |
| 4.30 | `insights --section polls` names no voter | no names | fake-Slack check "emoji mood is cohort-level (no user in the output)" and "reply graph … never a received count" | PASS |

### One correction to the brief

The brief asks me to confirm that **"nothing the bot posts contains an address
or a raw `<@U…>`"**. As written that is not what the system implements, and I
think the system is right.

`/alerts` deliberately prints both: `• <@U0DEMO0028> Guest Speaker —
guest.speaker@example.invalid`. It has to. The command exists so a staff member
can match an unrostered account to a roster record, and the address is the thing
being matched. It is an ephemeral reply to an admin, not a channel post. A
`<@U…>` is also not a leak — Slack renders it as a mention.

The invariant the code actually holds, which I verified, is narrower and better:

- **nothing fellow-facing** names an address (`/me`, `/badges`, `/dashboard`, `/help`, `/reminders`, `/checkin` — checked, none do)
- **nothing posted to a channel** names an address (fake-Slack checks: "what the bot posted names no address", "no staff-channel post names an email address")
- **no DM** names an address ("no direct message names an email address")
- the **status page, `/stats` and INFO logs** name no address

---

## Phase 5 — the rest of the system, with the Slack data in place

Run against a database carrying both datasets at once: `demo-slack-batch` first
for the Slack rows, then the Part A/B pipeline with the reset step stubbed out
(`/tmp/demo_no_reset.py`). Final state: 99 Part A, 82 Part B, 64 Slack events,
20 fellows, 13 sessions, 2 assignments.

| # | Check | Expected | Actual | Result |
|---|---|---|---|---|
| 5.1 | `cufa slack engagement --cohort demo` | index + components | 0–100, sorted, each row showing attendance, exit tickets, messages, outreach flag and named flags | PASS |
| 5.2 | attention index is **0–100, relative to the cohort** | in range | Top 100, bottom 0, everything between; components always printed beside it | PASS |
| 5.3 | **neither the help checkbox nor assignment scores feed the index** | absent | `engagement.py:23` states it, and no query in `engagement.py` references `help_request` or `assignment_submission.score`. Verified by grep across `src/` | PASS |
| 5.4 | form completeness counted, never graded | counted | Report: "free text is counted, never graded"; `checkin_b` stores the text, no score column, no length threshold in any query | PASS |
| 5.5 | **no model reads a fellow's words** to decide anything | none | Retention is keyword matching against `config/retention_rubric.json`, whose own comment says "Deterministic keyword matching only — no model reads a fellow's words". The only AI path is tier-2 passphrase adjudication, which sees a passphrase, not prose | PASS |
| 5.6 | no score is a participation signal | none | Report: "no score feeds any participation figure above"; `/fellow` lists assignments separately from the index | PASS |
| 5.7 | a session under review leaves the attendance denominator | left out | `needs_review` rows are excluded from `attended` and shown separately as "+N review" in every surface | PASS |
| 5.8 | **identity resolves at read time** — an alias re-attributes history with no re-ingest | old row changes | An event **ingested at 16:36:40** resolved to **CU-2604** after an alias created at **16:41:32**. No re-ingest was run. `v_fellow_email` is a plain view, not a materialised copy | **PASS (proved)** |
| 5.9 | **one address belongs to one fellow** | refused | See 4.19 | PASS |
| 5.10 | `cufa fellow funnel` | 5 stages | accepted 20 (100%), joined Slack 20 (100%), first message 16 (80%), first check-in 20 (100%), completed 1 (5%), with median gaps | PASS |
| 5.11 | `cufa fellow completed <id>` | stamps | `ok`, and the funnel moved to 1 completed | PASS |
| 5.12 | `cufa fellow retention` | deterministic rubric | Prints the rubric's concepts and terms, then `0/4 none` for all 20 — correct, but only because the fixtures contain none of the rubric terms | PASS (see F-08) |
| 5.13 | `cufa fellow alias` / `merge` | present | both subcommands exist; re-attribution proved at 5.8 | PASS |
| 5.14 | `cufa zoom ingest --vtt` | speaking share, unmatched names | 6 turns written. Share by time: Ardith 67.2% (2 turns, 51 words), Bexley 15.3%, Corvin 6.6%. **"Not A Real Fellow" listed as `[unmatched]`** and called out separately: "ask them to use their real name, or link a Slack profile with that name" — not guessed at | **PASS** |
| 5.15 | `cufa zoom share` | same view | identical output, read-only | PASS |
| 5.16 | `cufa assignment` create/list/score | all | exercised via the Slack path (4.13–4.15) and `cufa assignment list` | PASS |
| 5.17 | `cufa report --cohort demo --html` | builds | 76 697 bytes, **0 addresses**, **0 `help_request` references** | PASS |
| 5.18 | staff dashboard `/dashboard` | every panel | all present (see 1.13); `/dashboard/fellow/<id>` opens | PASS |
| 5.19 | `Export CSV` | no addresses | 0 addresses | PASS |
| 5.20 | **`python tasks.py test` after all of the above** | still green | **597 passed**, 45.98s, 0 failures | PASS |

**Invariant violated:** 5.2's sibling — the *overall* attendance rate — is not
bounded. `/report` and `/dashboard` both printed **235%**. Written up as F-03.
