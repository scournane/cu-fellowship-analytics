# Running the session console

The console is a small internal web app for two or three CU staff. It exists so
that nobody has to build a Google Form by hand — a staff member fills in the
session details, and the system provisions the form, publishes it, verifies that
it published, and pulls the responses back later.

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
| `/template` | Template setup | `cufa template create` / `verify` / `status` |
| `/sessions` | Sessions — list, create, edit | `cufa session list` / `create` / `edit` |
| `/sessions/{id}` | Session detail — the mid-lesson view | `cufa provision`, `cufa session announce`, `cufa pull` |
| `/review` | Needs-review queue, AI decisions, unresolved addresses | `cufa review`, `cufa decide` |

`/healthz` reports whether the database is reachable, and
`/sessions/{id}/responses.json` is what the live response counter polls.

---

## Signing in

Google sign-in, restricted to an allowlist of CU addresses in `.env`:

```
CUFA_CONSOLE_ALLOWLIST=alice@civicsunplugged.org,bob@civicsunplugged.org
CUFA_CONSOLE_SECRET=<a long random string>
```

There is deliberately no password system. Passwords for an internal tool used by
three people are an account-recovery problem and a credential-storage problem in
exchange for nothing — the staff already have Google accounts, and the app
already needs Google.

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

## When something looks wrong

| What you see | What it means |
|---|---|
| Verify template stays red | The API is not reporting `VERIFIED`. Re-do the manual step on the template form itself, not on a session copy. |
| Provisioning fails with a publish error | The form was created but did not read back as published (trap 1). Press provision again — it resumes the form that already exists rather than making another. |
| A session shows a form but not "ready" | Its publish state was never verified. It may be accepting nothing. Re-provision. |
| No responses arriving | Check the form is ready, then check the session's scheduled time, duration and grace — a mismatch shows up as ingest warnings. |
| The database banner says unreachable | Docker or the Supabase stack is not running. `make db-up`. |

---

## See also

- [`local-dev.md`](local-dev.md) — Docker, Supabase, Studio, SQL snippets
- [`google-cloud.md`](google-cloud.md) — enabling the APIs and creating the OAuth client
- [`../google-api-traps.md`](../google-api-traps.md) — why the setup is shaped this way
