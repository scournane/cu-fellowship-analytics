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
| `/template/questions?cohort=…` | Default exit ticket questions — Part A's cohort default: edit, load the approved week-1 set, import from a form, version history | `cufa questions show` / `set` / `seed-default` / `import-form` / `export` `--cohort` |
| `/sessions` | Sessions — list, create, edit | `cufa session list` / `create` / `edit` |
| `/sessions/{id}` | Session detail — the view during the lesson, and both forms | `cufa provision`, `cufa session announce`, `cufa pull` |
| `/sessions/{id}/questions` | Part A — exit ticket for one session: customise, or revert to the default; read-only once published | `cufa questions show` / `set` / `revert` / `export` `--session` |
| `/sessions/{id}/responses` | Part B responses — confidence, takeaways, themes | `cufa themes` |
| `/rotation` | Which question each upcoming week will ask | `cufa rotation` |
| `/shoutouts` | Names waiting for a human to link | `cufa shoutouts review` / `link` |
| `/review` | Needs-review queue, outside the window, unresolved addresses, straight-lining (and passphrase-era AI decisions, where any exist) | `cufa review`, `cufa decide` |
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
| `.../auth/forms.body` | create the template, write titles and questions, **publish** each form |
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
screen exists. Each part has its own template and does this once.

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
3. While the template is open, set **how the form looks and behaves**. None of
   this can be set through the Forms API, and every session's form is a copy of
   this template, so each copy inherits whatever is set here — and only what is
   set here:
   - **Theme** (the palette icon): header image, colour, background colour and
     font.
   - **Settings → Responses → Send responders a copy of their response**, if CU
     wants fellows to have one. It needs email collection on, which step 2 did.
   - **Settings → Presentation → Show progress bar**, which helps on a form of
     eight questions.
   - **Settings → Presentation → Confirmation message** — what a fellow sees after
     submitting. Something like "Thanks — you're marked as here for today's
     lesson" tells them the submission counted.

   On the Part A template, leave the questions alone: it holds only a title and
   a notice, and each session's questions are written onto its copy when it is
   provisioned, so anything added here is deleted from every copy. (Part B's
   template does carry its fields; see [`part-b-form.md`](part-b-form.md).)
4. Press **Verify template**. The app reads `form.settings` back from the API
   and turns green **only** when Google itself says `VERIFIED`.

The app does not take your word for it, and it is not being pedantic: a template
that is not Verified produces session forms that collect a typed address, which
looks identical to working until you try to trust the data. Provisioning is
blocked entirely until this passes.

Every session form is a Drive copy of this template, and copying preserves email
collection. The template is **re-verified before every provisioning run**, so if
someone edits it later and breaks it, provisioning fails loudly instead of
quietly producing forms that collect nothing. Each Part A copy is then read back
on its own, and provisioning stops unless the copy itself reports `VERIFIED` —
attendance rests on that address, so it is checked on the form fellows answer.

The look is not checked, because nothing in the API can read it back. A change
to the theme or the confirmation message reaches forms provisioned after it,
never forms already made; edit those by hand if they matter.

---

## Part A's questions — the exit ticket

Part A's questions are data, edited here rather than in the Forms editor. There
is **one default per cohort**, and any single session can have **its own
questions** instead. Every save is a new version and the previous one is kept,
so what any week's form asked can always be looked up.

### The cohort default

**Templates → Default exit ticket questions** (`/template/questions`), with the
cohort chosen at the top.

- **Use the approved exit ticket** loads the week-1 form from
  `config/part_a_default_questions.json`: first name, last name, a 1–5 rating,
  the biggest takeaway, more of, less of, open questions, other feedback.
  Pressing it again when nothing has changed saves nothing.
  (`cufa questions seed-default --cohort <id>`)
- **Copy the questions from a Google Form** reads the questions off an existing
  form — paste its edit link or id. The form is only read, never copied or
  changed. Question types the app cannot rebuild exactly (grids, dates, times,
  file uploads, ratings, images, videos) are listed and left out, and any
  "go to section" branching is dropped; the screen says which.
  (`cufa questions import-form --cohort <id> --form <link> [--dry-run]`)
- **Edit** the list directly and **Save a new version**.
  (`cufa questions export --cohort <id> --out q.json`, edit, then
  `cufa questions set --cohort <id> --file q.json`)

Editing the default changes the forms of sessions **not yet provisioned**.
Forms already made keep the questions they were made with.

### The question editor

Each row is one question: a type, the question, an optional description, and
**Required**. The types are the ones the Forms API can build exactly —
short answer, paragraph, multiple choice, checkboxes, dropdown, linear scale —
plus a **section break** and a **text block** for layout. Choice questions take
a list of options, and multiple choice and checkboxes can add **Other**;
dropdowns cannot, because Google does not allow it. A linear scale runs from 0
or 1 up to anything from 2 to 10, with optional labels at each end. Rows move
up and down, duplicate and delete.

Two placeholders are filled in per session when its form is made:
`{lesson}` becomes the session's week number and `{session_title}` its title.
The week-1 form's title is `Exit Ticket: Lesson {lesson} "{session_title}"`.
A session with no week number, such as a makeup session, gets an empty
`{lesson}` — "Lesson" with nothing after it — so give it a week, or customise
its questions, if that reads badly.

Above six answerable questions the editor warns, and still saves. Every extra
question costs completion, and the exit ticket is filled in at the end of a
lesson by people who are already leaving.

The answers are counted and shown, never graded, and never used to decide
attendance — a blank or one-word reflection from a fellow inside the window is
still a fellow inside the window.

### One session's questions

On the session screen, Part A's **Questions** card says which set the session
will use — *default v3*, or *custom v1* — and lists the questions as they will
appear, placeholders filled in.

- **Customise for this session** starts from the current default and saves the
  result as this session's own questions. That is a full copy, not a list of changes: editing
  the default afterwards does not reach it.
- **Revert to the cohort default** drops the session's own questions. The old
  versions stay in the history.
- (`cufa questions set --session <id> --file q.json`,
  `cufa questions revert --session <id>`)

**Once the session's Part A form is published, its questions are locked.** The
card turns read-only and `cufa questions set` refuses. A published form's
questions are what fellows answered; changing them afterwards would split one
session's answers across two sets, with nothing to say which fellow saw which.

---

## Per-session use

### Creating a session

Fields: title, scheduled date and time (local), timezone (defaults to the
browser's), duration, grace minutes (default 15), week, cohort.

**The window is attendance.** A Part A response counts when it comes from a
verified address and is submitted between *scheduled start − grace* and
*scheduled end + grace*, inclusive. Grace widens both sides: a fellow who
submits a few minutes before a lesson that started early is present, and so is
one who finishes the exit ticket just after it ends. If the exit ticket stays
open longer than the grace after the lesson — the week-1 form said ten minutes,
which 15 covers — widen the grace, or the late ones will read as outside the
window.

Get the time right. A session entered an hour off turns every response into
*outside the window*. Fixing the time and re-running adjudication corrects
them, and each decision's note records the window it was judged against.

**Week** fills `{lesson}` in the exit ticket's title and questions, and chooses
Part B's rotating question. It is typed, not derived from the date.

### During the lesson

The session detail screen is what a teacher uses during the lesson.

1. **Provision Part A** — copies the template, writes the session's questions
   onto the copy, publishes it, and reads back both the publish state and
   Verified email collection. Safe to press twice: a session that already has a
   verified form is shown, not given a second one. It is greyed out, with a
   banner saying why, while the cohort has no default questions and the session
   has none of its own. Once it succeeds, the session's questions are locked.
2. **Share the link both ways at once: the QR code on screen AND the link in the
   Zoom chat.** The console says this next to the link. A QR code alone misses
   anyone whose phone is their Zoom screen and anyone using a screen reader; a
   chat link alone misses anyone who joined after it was posted.
3. **Announce now** — stamps `announced_at_utc`, which is what latency is
   measured from. Press it when you share the link, not when you provisioned the
   form.
4. The response count updates on its own. **Pull responses** runs ingest for this
   session on demand.

When to share it is the teacher's call, as long as responses land inside the
window. The week-1 exit ticket went out at the end of the lesson and stayed open
ten minutes. Keep the link to the lesson itself: a form link posted in a channel
ahead of time can be submitted by anyone with it, at any time inside the window,
from wherever they are — which is the known weakness of attendance by address
and timing (ADR-037).

### Afterwards

The **Review** screen has these lists:

- **Needs review**, oldest first — one-click *attended* / *not attended*, which
  writes a human decision under your address. A human decision always wins and
  is never overwritten by a later automated pass. What lands here: a CSV row
  inside one session's window (a hand-made form cannot show that Google
  verified the address), and a timestamp inside two overlapping windows.
- **Outside the window** — responses judged *not attended* because they were
  submitted outside their session's window, or outside every window. Each shows
  how far outside it was and how many questions were answered. The commonest
  cause is a session time entered wrongly: fix the session and re-run
  adjudication rather than overriding each row.
- **Unresolved addresses** — submissions from addresses not on the roster. The
  check-in was still recorded; fix the roster entry and it re-attributes with no
  backfill, because identity resolves at read time.
- **AI decisions** appears only when there are passphrase-era decisions made by
  the retired model tier, so they stay auditable (ADR-040).

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
| Provision Part A is greyed out | The cohort has no default exit ticket questions and the session has none of its own. Load the approved set on **Templates → Default exit ticket questions**, or customise the session. |
| Customise for this session and Revert are disabled | Its Part A form is published, so its questions are locked. That is deliberate — see above. |
| A fellow who was there shows as not attended | Look on **Outside the window**. If the whole session is there, its scheduled time is wrong: fix it and re-run adjudication. If it is one fellow, record a human decision. |
| The session's forms do not have the header image or confirmation message | They were copied before the template was given them. Set them on the template for future forms, and by hand on the forms already made. |
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
