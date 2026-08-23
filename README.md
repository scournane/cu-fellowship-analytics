# Civic Innovators check-in — Part A

Attendance for the Civics Unplugged Civic Innovators Fellowship, from a Google
Form released **mid-lesson** rather than from Zoom.

Three fields, end to end: a **Google-verified email**, a **timestamp**, and a
**session passphrase** the teacher says aloud and puts on screen.

---

## Why not Zoom

Since a March 2023 API change, Zoom hides `id` and `participant_user_id` for
guest participants as PII, and returns an email address only for participants
signed into Zoom. For an unauthenticated joiner the entire record is a
self-typed display name and a duration. Renaming yourself, or joining and
walking away, produces a record identical to real attendance.

A form released 15–25 minutes in proves something Zoom cannot: the fellow was
present at a moment they could not have predicted. The passphrase is what makes
that provable — a timestamp on its own is satisfied by an idle open tab.

The full reasoning, and every alternative rejected, is in
[`docs/decisions.md`](docs/decisions.md).

---

## Clone to a working demo

```bash
git clone <this repo> && cd cu-fellowship-analytics
make setup        # deps, supabase init, checks Docker is running
make demo         # the whole pipeline on synthetic data
```

`make demo` needs **no Google account and no `GEMINI_API_KEY`**. It runs against
`FakeGoogleClient`, which reproduces each documented Google failure mode, so the
demo exercises the trap handling rather than routing around it. It prints an
attendance report and then asserts the acceptance criteria.

Re-run `make demo` and the numbers are identical — ingest is idempotent.

Requirements: Python 3.11+, Docker (the local Supabase stack runs in it), and
the [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started).
`make setup` checks all three and tells you exactly what is missing.

### What the demo actually does

1. Resets the database and generates deterministic fixtures — 20 invented
   fellows on `@example.invalid`, 7 sessions, 60 responses covering 19 edge
   cases.
2. Creates the template form, then **proves `template verify` blocks** before a
   human sets email collection to Verified, performs that step, and verifies.
3. Provisions one form per session — copy, set content, publish, **read the
   publish state back and assert it**.
4. Pulls responses through the Forms API, then imports a manually created
   form's CSV export through the fallback path.
5. Adjudicates with tier 1 only, prints the report, and runs the acceptance
   checks.

### Other entry points

```bash
make demo-console   # demo data plus the web console, zero Google calls
make demo-ai        # tier 2 live; skips with a message if GEMINI_API_KEY is unset
make test           # 85 tests, no network
make clean          # stop Supabase, remove generated fixtures
```

Inspect the data visually in **Supabase Studio** at http://localhost:54323, or
use the copy-pasteable SQL in
[`docs/setup/local-dev.md`](docs/setup/local-dev.md).

---

## How it works

```
  staff fill in a session  ─────►  console  ─────►  Google Form (provisioned,
  (title, time, passphrase)                          published, verified)
                                                            │
  teacher says the passphrase aloud AND shows it             │ fellows submit
  on screen, then presses "Announce now"                     ▼
                                                    forms.responses.list
                                                            │
                                                            ▼
                                          checkin  ── immutable observation
                                                            │
                                       ┌────────────────────┼────────────────────┐
                                       ▼                    ▼                    ▼
                                 tier 1: rules      tier 2: Gemini        tier 3: a human
                                 (exact / fuzzy /   (only mismatch-       (always wins)
                                  not_set /          in-window cases)
                                  no_session)
                                       └────────────────────┼────────────────────┘
                                                            ▼
                                              attendance_decision
                                          append-only, versioned, provenanced
```

The load-bearing separation is between the two tables. **`checkin` is what was
observed** and is immutable — a database trigger refuses every update to an
observed column and every delete. **`attendance_decision` is the judgment** and
is append-only: superseding sets `superseded_at` and inserts a new row, and a
partial unique index guarantees exactly one current decision per check-in.

That is what makes a human override auditable months later, and what lets a
changed definition of "attended" be re-run over history without destroying the
evidence it was applied to.

### Design invariants

1. **Never drop a submission.** A wrong passphrase, an unknown address, a
   timestamp outside every window — all recorded, with the reason. A dropped row
   is an unrecoverable observation, and it hides exactly the cases worth
   looking at.
2. **The observation is separate from the decision.** See above.
3. **Every decision carries its provenance** — which rule, which model, which
   human, and when.
4. **A human override always wins**, and is never silently overwritten.
   `--force` overrides that and names exactly what it is about to destroy.
5. **Ingest is idempotent**, including across both ingestion paths.
6. **Identity never blocks ingest.** An unrecognized address still produces a
   record; the address goes to a review queue.
7. **Verify, don't assume.** Anything living in Google's systems is read back
   and asserted, never inferred from a 200.
8. **Everything is cohort-keyed**, for year-over-year comparison.
9. **Timestamps are UTC** past the parser boundary.

---

## The four Google traps

Each one fails **silently** — the code appears to work and no responses arrive,
or arrive unattributable. Read
[`docs/google-api-traps.md`](docs/google-api-traps.md) before changing anything
in `src/cufa/google/`, `template.py`, or `provisioning.py`.

| # | Trap | How this repo handles it |
|---|---|---|
| 1 | API-created forms are **unpublished** since 2026-07-01 and accept nothing, while the link still resolves | `setPublishSettings` is called, then the state is **read back and asserted**. "Ready" means `publish_verified_at IS NOT NULL`, nothing else. |
| 2 | `emailCollectionType: VERIFIED` is rejected by `batchUpdate` with a 400 | One template form; a human sets Verified by hand once; the API confirms it before anything proceeds; each session form is a Drive `files.copy` of that template. Re-verified on **every** provisioning run. |
| 3 | No REST equivalent of `Form.setDestination()`, so there is no linked Sheet to export | Read `forms.responses.list` directly — `respondentEmail` plus RFC3339 UTC, which removes the Sheets timezone trap entirely. Incremental via a watermark that advances only after a complete pass. |
| 4 | A service account cannot own a Google Form | User OAuth, exactly two scopes, refresh token encrypted at rest. The forms end up in a CU staff member's Drive, which is where CU wants them. |

---

## The CLI

Everything the console does is also a command. That keeps the system
scriptable, keeps it testable without a browser, and keeps it usable on the day
the web app breaks.

```
cufa db up | down | reset
cufa serve                              # the console
cufa google connect | status | disconnect
cufa template create | verify | status
cufa load-roster    --csv <path> --cohort <id>
cufa load-sessions  --csv <path>
cufa session        list | create | announce | suggest-passphrase
cufa provision      --session <id> | --cohort <id> [--dry-run]
cufa pull           --session <id> | --cohort <id>
cufa ingest part-a  --csv <path> --cohort <id> --sheet-timezone <IANA>
cufa adjudicate     --cohort <id> [--no-ai] [--force]
cufa decide         --checkin <id> --status <s> --by <email> --note "<text>"
cufa review         [--status needs_review | ai | unresolved-identity]
cufa report         --cohort <id> [--json]
```

`--sheet-timezone` is **mandatory and has no default** — not UTC, not the
machine's zone. A Sheets export writes wall-clock times with no offset marker,
so guessing shifts every check-in by hours without failing.

---

## Adjudication

Tier 1 is deterministic and decides everything it can:

| Observation | Decision | Rule | Confidence |
|---|---|---|---|
| `exact` in window | attended | `exact_match` | 1.0 |
| `fuzzy` (Levenshtein ≤ 1) in window | attended | `fuzzy_match` | 0.9 |
| `not_set` in window | attended | `no_passphrase_required` | 0.7 |
| no session matched | not attended | `outside_all_windows` | 0.6 |
| `mismatch` in window | → tier 2 | — | — |

Fuzzy is on by default because the passphrase is *heard aloud* and typed on a
phone. Rejecting `justise` for `justice` penalises someone who was in the room
and heard it, which is backwards from the intent.

Tier 2 exists only for what edit distance genuinely cannot read —
`"the word was justice"`, `"justice i think?"`, `"jushtis"`, `"sorry I missed
it"`. It is sent **two strings and nothing else**: the expected passphrase and
the submitted answer. No names, no addresses, no history. Narrower context is
better privacy and better accuracy at once.

Without a key, without a network, or out of quota, tier 2 degrades to
`needs_review` with `rule_name='ai_unavailable'` and the pipeline finishes.
**`needs_review` is never turned into `not_attended`** — absent evidence is not
evidence of absence.

---

## Accessibility

The passphrase must be **said aloud AND displayed on screen**. Audio-only
excludes deaf and hard-of-hearing fellows, and anyone whose audio drops. This
widens who could copy the word down, which is exactly why the passphrase is one
signal among several and never proof on its own. The console says this on the
session screen, not only here.

---

## Two things deliberately left undecided

Both are marked in the code with a `TODO`, and neither has a placeholder value,
because a plausible-looking guess in either place quietly becomes the policy.

- **`TODO(retention)`** in `src/cufa/form_content.py` — CU has not set a
  retention period, and the form tells fellows what happens to their data.
- **`TODO(access)`** in `supabase/migrations/20260801000500_rls.sql` — CU has
  said the data should be visible to every full-time team member but has not
  defined granular permissions, and a derived attendance judgment should not
  automatically be as open as a raw timestamp.

---

## Documentation

| Document | What it is for |
|---|---|
| [`docs/setup/local-dev.md`](docs/setup/local-dev.md) | Docker, Supabase, Studio, make targets, SQL snippets |
| [`docs/setup/console.md`](docs/setup/console.md) | Running the console, connecting Google, the one manual step |
| [`docs/setup/google-cloud.md`](docs/setup/google-cloud.md) | Enabling the APIs, the OAuth client, the exact scopes |
| [`docs/google-api-traps.md`](docs/google-api-traps.md) | The four traps — **read this before touching the Google code** |
| [`docs/decisions.md`](docs/decisions.md) | ADRs: what was decided, what was rejected, and why |

---

## Scope

This is **Part A** only: verified email, timestamp, passphrase.

Out of scope by design: Part B's reflection fields, Slack and Zoom integration,
auto-posting links, scheduled triggers, reminder nudges, gamification,
participation scoring, at-risk flags, dashboards beyond the review screen and
the terminal report, and cloud deployment. Local only.

Never commit real fellow data. Every fixture name is invented and every fixture
address is `@example.invalid`, a reserved TLD that cannot be registered.
