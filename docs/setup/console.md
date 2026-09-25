# Running the session console

The console is a small internal web app for two or three CU staff. It exists so
that nobody has to build a Google Form by hand — a staff member fills in the
session details, and the system provisions **both** forms for that session,
publishes them, verifies that they published, and pulls the responses back
later.

Everything here is also a `cufa` command. The console is a convenience layer, not
the only entry point; if it ever breaks, the pipeline still runs from a terminal.

---

## Starting it

```bash
make db-up          # the local Supabase stack (needs Docker)
cufa serve          # http://127.0.0.1:8000
```

Or `make demo-console`, which loads the synthetic demo data first and runs the
console against `FakeGoogleClient` — every screen is clickable, including
provisioning and review, with **zero Google calls**.

The console is local only. It is not built to be hosted publicly, and nothing in
this repo deploys it.

### The screens

| Path | Screen | Equivalent command |
|---|---|---|
| `/` | Connect Google | `cufa google connect` / `status` / `disconnect` |
| `/template` | Template setup — **both parts**, each with its own manual step | `cufa template create` / `verify` / `status` `--part a|b` |
| `/sessions` | Sessions — list, create, edit | `cufa session list` / `create` / `edit` |
| `/sessions/{id}` | Session detail — the mid-lesson view, and both forms | `cufa provision`, `cufa session announce`, `cufa pull` |
| `/sessions/{id}/responses` | Part B responses — confidence, takeaways, themes | `cufa themes` |
| `/rotation` | Which question each upcoming week will ask | `cufa rotation` |
| `/shoutouts` | Names waiting for a human to link | `cufa shoutouts review` / `link` |
| `/review` | Needs-review queue, AI decisions, unresolved addresses, straight-lining | `cufa review`, `cufa decide` |
| `/dashboard` | Staff dashboard — attendance, the attention list and its outreach toggle, badges and ranks, assignments and scores, the funnel | `cufa report`, `/leaderboard` and `/score` in Slack |
| `/dashboard/fellow/{id}` | One fellow as staff see them: the fellow's own page plus aliases, interventions and airtime | `/fellow <name\|id\|email>` in Slack |
| `/help-requests` | **Access-gated.** Fellows who asked to be checked in with | `cufa help-requests list` / `ack` / `close` |

`/healthz` reports whether the database is reachable, and
`/sessions/{id}/responses.json` is what the live response counter polls.

`/me/{token}` is the one page here that is **not** a staff screen: it shows one
fellow their own record, is reached only through the signed, 7-day link the bot
hands out with `/dashboard` in Slack, and carries none of the console's nav or
configuration. There is no fellow login.

**`/help-requests` has its own access list**, separate from the sign-in
allowlist below — being able to use the console does not open it. See
[`../safeguarding.md`](../safeguarding.md).

---

## Signing in

Google sign-in, restricted to an allowlist of CU addresses in `.env`:

```
CUFA_CONSOLE_ALLOWLIST=alice@civicsunplugged.org,bob@civicsunplugged.org
CUFA_CONSOLE_SECRET=<a long random string>

# Who may open the Help requests screen. A SUBSET of the list above, and a
# separate one on purpose. Leave blank to fall back to whoever is named in
# config/help_routing.json — the people already receiving those emails.
CUFA_HELP_ALLOWLIST=alice@civicsunplugged.org
```

Google is the door to prefer, because it is the only one that records *who*
opened a fellow's page. Passwords for an internal tool used by three people are
an account-recovery problem and a credential-storage problem, and the staff
already have Google accounts.

### The shared password, when Google is not an option

A hosted deployment may have no Google OAuth client — the redirect URI has to be
registered, and until it is, nobody can sign in at all. For that case there is
one shared password:

```
CUFA_CONSOLE_PASSWORD=<a passphrase>
```

Blank, and the door does not exist: the sign-in screen does not draw the field
and `POST /signin/password` answers 403 to everything, including an empty guess.
Set, and anyone holding it gets a session — as `shared-password@console.local`,
an address that is not a mailbox and is on no allowlist. That last part is
load-bearing: `/help-requests` is gated on the **email** allowlist, so the shared
password never opens it. A secret everyone knows cannot say who read a
safeguarding record, so it does not get to read one.

To rotate or revoke: change the line, or delete it. The password is re-checked on
every request, so clearing it signs out everyone it let in, immediately — it does
not wait for their cookies to expire.

Nothing rate-limits the guesses beyond whatever is in front of the console. Use
Google where Google is available.

`/admin-dashboard` in Slack hands staff the console address. It never sends the
password: a Slack message is searchable, exportable and forwardable, so whoever
runs the install passes the password along some other way.

`CUFA_CONSOLE_SECRET` signs the session cookie. Change it from the default; a
known signing key means anyone can mint a session.

---

## The one-time setup, and why it is not automatic

Two screens run once, ever.

### 1. Connect Google

One staff member signs in and grants two scopes:

| Scope | Why |
|---|---|
| `.../auth/forms.body` | create the template, set titles and the question, **publish** each form |
| `.../auth/drive.file` | copy the template — **and** read responses back |

`drive.file` is load-bearing twice. `forms.responses.list` accepts `drive`,
`drive.file` or `forms.responses.readonly`, and **not** `forms.body`; dropping
`drive.file` to tighten the grant would silently break every response pull.

The forms end up owned by whichever account connects, so **connect as a CU staff
account**, not a contractor's. That is the point of using user OAuth rather than
a service account: the work product stays in CU's Drive. See
[`google-cloud.md`](google-cloud.md) for creating the OAuth client.

The refresh token is encrypted with `CUFA_ENCRYPTION_KEY` before it is stored. A
raw `select * from google_credential` returns ciphertext. Generate a key with:

```bash
python -m cufa.crypto keygen
```

### 2. Template setup — the manual step

This is the one thing the Forms API cannot do reliably, and the reason this
screen exists.

Setting email collection through `forms.batchUpdate` → `updateSettings` →
`emailCollectionType` has been observed returning `400 INVALID_ARGUMENT`, with
no working enum value. Verified email collection is the entire premise of the
design — an address the respondent *types* is exactly the self-reported identity
this system replaces — so it cannot depend on a call that may reject.

So:

1. Press **Create template**. The app creates one template form. It also
   *attempts* the API call anyway, so that if Google ever fixes it this step
   disappears for free.
2. Open the link the screen shows you and set
   **Settings → Responses → Collect email addresses → Verified**. About thirty
   seconds.
3. Press **Verify template**. The app reads `form.settings` back from the API
   and turns green **only** when Google itself says `VERIFIED`.

The app does not take your word for it, and it is not being pedantic: a template
that is not Verified produces session forms that collect a typed address, which
looks identical to working until you try to trust the data. Provisioning is
blocked entirely until this passes.

Every session form is a Drive copy of this template, and copying preserves email
collection. The template is **re-verified before every provisioning run**, so if
someone edits it later and breaks it, provisioning fails loudly instead of
quietly producing forms that collect nothing.

---

## Per-session use

### Creating a session

Fields: title, scheduled date and time (local), timezone (defaults to the
browser's), duration, grace minutes (default 15), passphrase, cohort.

The grace window widens matching on **both** sides of the scheduled block, so a
fellow who submits a few minutes before a lesson that started early is present,
not absent.

**Choosing a passphrase.** The guidance is on the screen next to the field, not
buried here:

- One word, roughly 5–10 letters.
- Avoid homophones — `their`/`there`, `flour`/`flower`. A fellow who *heard* the
  word and typed the other spelling was still in the room.
- Avoid words that appear in this week's slides or readings; those are guessable
  from materials that were sent to everyone, including people who did not come.
- Never reuse one. The console warns you if the word matches an earlier session
  in the cohort, and makes you confirm.

**Suggest a passphrase** draws from a curated list with no common homophones or
near-homophones.

A session with **no** passphrase is legal. It adjudicates as `not_set` at lower
confidence, not as a failure.

### During the lesson

The session detail screen is what a teacher uses mid-lesson.

1. **Provision form** — copies the template, sets the title and question,
   publishes, and verifies the publish state. Safe to press twice: a session that
   already has a verified form is shown, not given a second one.
2. Share the link. There is a large copy button and a QR code.
3. **Say the passphrase aloud AND put it on screen.** The console says this on
   the screen, because audio-only excludes deaf and hard-of-hearing fellows and
   anyone whose audio drops. Showing it widens who could copy it down — which is
   exactly why the passphrase is one signal among several and never proof on its
   own.
4. **Announce now** — stamps `announced_at_utc`. This is what latency is measured
   from. Press it when you say the word, not when you provisioned the form.
5. The response count updates on its own. **Pull responses** runs ingest for this
   session on demand.

Release the form **15–25 minutes in**. Released at the start it proves only that
someone joined; released mid-session it proves presence at a moment the fellow
could not have predicted.

### Afterwards

The **Review** screen has three lists:

- **Needs review**, oldest first — one-click *attended* / *not attended*, which
  writes a human decision under your address. A human decision always wins and
  is never overwritten by a later automated pass.
- **AI decisions**, with the model's reasoning, so a person can spot-check the
  model rather than trust it.
- **Unresolved addresses** — submissions from addresses not on the roster. The
  check-in was still recorded; fix the roster entry and it re-attributes with no
  backfill, because identity resolves at read time.

---

## The look, and where it is defined

Every colour, radius, border and type size in the console comes from one theme:
`frontend/src/theme/classroomTheme.js`. Screens ask for components and never set
a colour of their own, so the whole console changes from that file and nowhere
else.

It is compiled, not read at start-up:

```bash
cd frontend
npm run theme      # rebuilds src/theme/classroom.css and classroom.js
npm run build      # rebuilds the bundle that imports them
```

The compiled pair is committed alongside the source. `python tasks.py frontend`
refuses to build when they have drifted apart, because a theme edited and not
rebuilt renders the *previous* look with no error anywhere.

Two things worth knowing before changing it:

* **The fonts are bundled, not fetched.** Nunito and Nunito Sans come from
  `node_modules` through the build, so the console renders the same on a laptop
  with no network. Naming a font in the theme that nothing loads is a silent
  fallback to whatever the browser has.
* **It is a light theme on purpose.** `main.jsx` pins `mode="light"`; a dark
  scheme derived from these colours would be a different design rather than this
  one after dark.

---

## When something looks wrong

| What you see | What it means |
|---|---|
| Verify template stays red | The API is not reporting `VERIFIED`. Re-do the manual step on the template form itself, not on a session copy. |
| Provisioning fails with a publish error | The form was created but did not read back as published (trap 1). Press provision again — it resumes the form that already exists rather than making another. |
| A session shows a form but not "ready" | Its publish state was never verified. It may be accepting nothing. Re-provision. |
| No responses arriving | Check the form is ready, then check the session's scheduled time, duration and grace — a mismatch shows up as ingest warnings. |
| The database banner says unreachable | Docker or the Supabase stack is not running. `make db-up`. |
| Provision Part B is greyed out | Either the Part B template is not verified — it has its own manual step — or this week asks the teacher's own question and none is set. The banner on the session says which. |
| Pulling Part B refuses with "question map is missing" | The form's `questionId` → field map is incomplete, so answers cannot be told apart. Press **Re-check Part B**: it re-reads the form and records the map again without touching a single question. |
| The help checkbox is not on a form | Nobody is named in `config/help_routing.json`. The session screen says so. This is deliberate — see [`../safeguarding.md`](../safeguarding.md). |
| "Help requests" is missing from the nav | Your address is not on `CUFA_HELP_ALLOWLIST` (or, if that is unset, not named in `config/help_routing.json`). |

---

## See also

- [`local-dev.md`](local-dev.md) — Docker, Supabase, Studio, SQL snippets
- [`google-cloud.md`](google-cloud.md) — enabling the APIs and creating the OAuth client
- [`part-b-form.md`](part-b-form.md) — the end-of-session form, its own Verified step, and the rotation
- [`../safeguarding.md`](../safeguarding.md) — the help-request path, for CU staff
- [`../google-api-traps.md`](../google-api-traps.md) — why the setup is shaped this way
