# Six Google API traps this system is built around

Read this before you change anything under `src/cufa/google/`, `src/cufa/template.py`,
`src/cufa/provisioning.py`, `src/cufa/question_map.py`, or `src/cufa/ingest/`.

Six behaviours of the Google Forms and Drive APIs will break collection **silently**.
Not with an exception, not with a 500 — the code returns 200, the form link opens in a
browser, the teacher shares it, and either nothing arrives, or what arrives cannot be
attributed to a person, or it is attributed to the wrong field. You find out weeks
later, when the data you needed does not exist and cannot be recreated.

Traps 1 to 4 came from Part A. **Trap 5 came from Part B** and only bites a form with
more than one question, which is why Part A never met it — and it is the only one where
Google's actual behaviour could not be established at all. **Trap 6 came from the first
real provisioning run against a live account**, which is also where trap 2 turned out to
have been fixed by Google; see the note on it below.

Everything below is current as of **August 2026**. Each section says what the trap is,
what it silently breaks, exactly where this codebase handles it, how to check the
handling still works, and what to do if Google changes the behaviour.

If you are about to "simplify" one of these — publish without reading the state back,
set email collection through the API, link a response spreadsheet, use a service
account, hardcode a question id — this document is the argument against it.

---

## Trap 1 — API-created forms are unpublished by default

### What it is

Forms created through `forms.create` used to accept responses immediately. Google
changed that: forms created by the API were auto-published **through 30 June 2026**,
and after that date they are created in an **unpublished** state. An unpublished form
accepts no responses.

### What it silently breaks

The form exists. `forms.create` returns a `formId` and a `responderUri`. The URL
resolves and renders the form. A fellow fills it in, presses submit — and the response
is refused. Nothing in the provisioning code sees an error, because provisioning
finished successfully hours earlier. The failure surfaces as an empty
`forms.responses.list` result, which is indistinguishable from "nobody showed up".

This is the worst of the four, because the evidence of a live lesson cannot be
regenerated after the lesson.

### How this codebase handles it

`src/cufa/provisioning.py` → **`_publish_and_verify()`**.

1. `client.set_publish_settings(form_id, is_published=True, is_accepting_responses=True)`
2. stamp `session_form.published_at` — this records *the call was made*, nothing more
3. `state = client.read_settings(form_id)` — read the publish state back from Google
4. `if not state.accepts_responses:` write a `failure` row to `provisioning_log` and
   raise `PublishVerificationFailed`
5. only then stamp `session_form.publish_verified_at`

`FormState.accepts_responses` (`src/cufa/google/base.py`) is
`is_published and is_accepting_responses` — both, because either one being false
means zero responses.

**"Ready" is defined as `publish_verified_at IS NOT NULL`**, not as "provisioning
returned without raising". `provisioning.is_ready()` and the console's readiness badge
both read that column. A form whose `published_at` is set and whose
`publish_verified_at` is NULL is precisely the dangerous state, and it is visible in
one query.

Related handling:

- `RealGoogleClient.set_publish_settings` (`src/cufa/google/real.py`) posts to
  `forms/{formId}:setPublishSettings`. If the generated discovery client does not
  expose the method — it is newer than some builds of the discovery document — it
  falls through to `_raw_post()` against the REST endpoint with the same credentials.
  It never silently skips the call.
- `pull_session` (`src/cufa/ingest/forms_api.py`) adds a warning to the ingest result
  when it polls a form whose `publish_verified_at` is NULL, so an unverified form is
  called out again at read time rather than only at provisioning time.
- Required scope: `https://www.googleapis.com/auth/forms.body`.

### How to verify the handling still works

```bash
# 1. The fake reproduces the trap; provisioning must refuse.
#    (FakeGoogleClient(publish_readback_fails=True) — see the testing section.)
make test

# 2. Against a real session, provisioning is only "ready" when the column is set.
cufa provision --session <session-id>
psql "$CUFA_DATABASE_URL" -c \
  "select form_id, published_at, publish_verified_at from session_form where session_id = '<session-id>';"
```

`publish_verified_at` NULL after a successful-looking run means the read-back failed
and provisioning correctly refused to report the form as ready. Re-run `cufa provision`
for that session — it resumes the existing form rather than copying a second one.

The end-to-end check nothing else replaces: open the responder URL in a private
browser window and submit a test response.

### If Google changes this

- **If auto-publish comes back**, nothing needs to change. The read-back passes, and
  the extra `setPublishSettings` call is harmless.
- **If the endpoint or payload shape changes**, the only place to edit is
  `RealGoogleClient.set_publish_settings` (and `FORMS_API_ROOT` for the raw-POST
  fallback). Everything above it works against the `FormsClient` protocol.
- **Never delete step 3.** The read-back is not a belt-and-braces nicety; it is the
  only thing standing between a 200 response and a lesson with no attendance data.

---

## Trap 2 — `emailCollectionType: VERIFIED` is unreliable through the API

> **Update, August 2026 — observed working.** On a live run against a real
> Workspace account, `batchUpdate` → `updateSettings` → `emailCollectionType:
> VERIFIED` was **accepted**, and reading the settings back confirmed `VERIFIED`.
> Both templates verified with no human step at all.
>
> **Nothing below has been removed, and nothing should be.** The code already
> handles this outcome: `try_set_verified_email` attempts the call precisely so
> that "if Google fixes it the human step disappears for free", and
> `verify_template` reads the state back regardless — so a 200 was never what
> unblocked provisioning, and is not what unblocked it here. The manual step is
> still documented, still offered, and still the fallback the moment the API
> goes back to rejecting it or a template is edited by hand. One successful run
> against one account is not a guarantee about Google's behaviour, and this is
> exactly the shape of thing that regresses quietly.


### What it is

Verified email collection is the entire premise of this design: Google confirms the
respondent's address instead of the respondent typing one. Setting it through
`forms.batchUpdate` → `updateSettings` → `emailCollectionType` has been reported
returning:

```text
400 INVALID_ARGUMENT
Invalid value at 'requests[0].update_settings.settings.email_collection_type'
(type.googleapis.com/google.apps.forms.v1.FormSettings.EmailCollectionType)
```

with no enum value documented that works — see
[googleapis/google-api-nodejs-client#3467](https://github.com/googleapis/google-api-nodejs-client/issues/3467).

### What it silently breaks

Two ways, both quiet:

- If the 400 is caught and swallowed, the form is created with Google's default
  collection setting and still looks fine. Responses arrive with **no email at all**,
  or with an address the respondent typed — which is exactly the self-reported
  identity this system exists to replace.
- If someone opens the template later and switches collection back to *Responder
  input*, every form copied from that point on collects typed addresses. The data
  looks identical in the table. You cannot tell afterwards which rows were verified.

### How this codebase handles it

Template-and-copy, in `src/cufa/template.py`:

1. **One template form**, created once by `create_template()`. It is idempotent — an
   existing active template is returned rather than a second one created, because two
   templates means two sets of settings and only one of them gets checked.
2. **The API call is still attempted**, in `try_set_verified_email()`. A `400` is
   treated as the *expected* outcome, logged at INFO, and returns `False`. Any other
   `GoogleApiError` is re-raised as `EmailCollectionRejected` — "broken in the
   documented way" and "broken in a new way" deserve different responses. This is why
   the manual step disappears for free the day Google fixes the enum: nothing needs to
   be rewritten, the call simply starts succeeding.
3. **A human sets it by hand, once**: Settings → Responses → Collect email addresses →
   Verified. The exact wording lives in `template.MANUAL_STEP` and is printed by
   `cufa template create` and shown on the console's template screen.
4. **The app reads `form.settings` back and refuses to proceed** until Google itself
   reports `VERIFIED`. `verify_template()` writes `verified_email_confirmed_at`,
   `last_verified_at` and a `settings_snapshot` on success; on failure it **clears**
   `verified_email_confirmed_at` and raises `TemplateNotVerified`. A template that was
   verified in September and edited in October stops being usable the moment we
   notice — it does not keep its old green tick.
5. **Every session form is a Drive `files.copy` of that template**
   (`RealGoogleClient.copy_form` → `drive.files().copy(...)`, then `forms.get` for the
   responder URL). Copying a Google Form preserves its settings, including email
   collection. `forms.batchUpdate` is then used only for what it does reliably: title,
   description, and the passphrase question — see `_content_requests()` in
   `src/cufa/provisioning.py`, which deliberately contains no settings request.

**The template is re-verified before every provisioning run.** `provision_session()`
calls `require_verified_template()`, which calls `verify_template()` — it never reads
the stored flag and trusts it. That includes `--dry-run`: a dry run that skipped the
gate would report a plan that could not actually run.

### How to verify the handling still works

```bash
cufa template status     # what is stored: verified-at, last-checked, settings snapshot
cufa template verify     # re-reads form.settings from Google right now; exit 1 if not VERIFIED
```

```sql
select form_id, verified_email_confirmed_at, last_verified_at, settings_snapshot
  from form_template
 where is_active;
```

`settings_snapshot` is the `settings` object exactly as last read back, so drift is
diffable rather than mysterious. In tests, `FakeGoogleClient.simulate_human_breaks_verified()`
flips a template to `RESPONDER_INPUT`; provisioning must then fail with
`TemplateNotVerified` and create no form.

Spot-check on real data: a response row whose `submitted_email` is blank or does not
look like a Google account is a signal the copy chain lost the setting.

### If Google changes this

- **If `updateSettings` starts accepting the enum**, `try_set_verified_email()` returns
  `True` and the very first `verify_template()` passes with no human involvement. You
  can then drop the manual instructions from the console copy. **Keep
  `verify_template()`** — it is what catches a human breaking the template later, which
  is independent of the API bug.
- **If the enum is renamed**, change `EMAIL_COLLECTION_VERIFIED` in
  `src/cufa/google/base.py`. It is referenced by name in exactly one place per module.
- **If Drive copy stops preserving settings** — verify by copying the template by hand
  and reading the copy's settings — the architecture is gone and you need a new one.
  Do not paper over it by setting collection per form; that is the call that does not
  work.

---

## Trap 3 — the REST API cannot link a response spreadsheet

### What it is

`Form.setDestination()` exists in Apps Script. There is no REST equivalent. A form
provisioned through the API has **no linked Sheet**, so there is no spreadsheet to
export and no CSV to import.

### What it silently breaks

The obvious workaround — have someone link a Sheet by hand and export CSV — walks
straight into a second, nastier trap. **Google Sheets writes form timestamps in the
spreadsheet's own locale with no offset marker.** A cell reads `2026-09-15 13:05:00`.
That string carries no timezone. Parse it as UTC and every check-in in the file shifts
by hours, in a way that looks completely plausible: the times are still sensible, the
dates are still right, and submissions quietly land inside or outside the wrong session
windows. Nothing errors.

### How this codebase handles it

Read responses directly, in `src/cufa/ingest/forms_api.py` → **`pull_session()`** and
`pull_cohort()`. This is not a workaround; it is strictly better than a linked sheet:

- `forms.responses.list` returns **`respondentEmail`** — the verified address, with no
  question mapping needed.
- It returns **RFC3339 UTC** timestamps. `RealGoogleClient.list_responses` takes
  `lastSubmittedTime` (falling back to `createTime`) — the moment the fellow actually
  answered, not when a left-open tab created a draft. `parse_rfc3339()` in
  `src/cufa/timeutil.py` refuses a timestamp with no offset rather than assuming one.
- **The timezone trap is eliminated, not mitigated.** `checkin.source_timezone` is NULL
  on the API path because nothing was converted and there is no zone to audit.

Incremental polling:

- Filter is `timestamp > <watermark>`, built in `pull_session()` from
  `session_form.response_watermark` (RFC3339, written by `iso_utc()`).
- Pages are consumed via `nextPageToken` until it is absent.
- **The watermark is advanced only after the whole page loop completes without
  raising.** The `update session_form set response_watermark = ...` statement is the
  last thing in the `try` block. On any exception the `except` branch updates only
  `last_polled_at`, marks the `load_run` failed, and re-raises — the watermark does not
  move, so the next pull re-reads that page.
- Re-reading is free because `source_event_id` makes writes idempotent
  (`ON CONFLICT DO NOTHING` on the UNIQUE index).
- `_list_with_backoff()` retries `429` with exponential backoff and jitter, up to four
  attempts. Other statuses propagate — a 503 mid-pull is exactly the case the watermark
  discipline exists for.

**The CSV fallback still exists**, in `src/cufa/ingest/csv_path.py`, for forms someone
created by hand. There, `--sheet-timezone` is **mandatory with no default** — not UTC,
not the machine's zone. Omitting it raises `MissingTimezone`, whose message names the
flag, gives a complete example command, and says where to find the value
(Sheets → File → Settings → Time zone). Defaulting it would make the most dangerous
case — an operator who never thought about timezones — the silent one. The raw string
and the zone applied are both stored (`checkin.submitted_at_raw`,
`checkin.source_timezone`) so any conversion can be re-derived later.

Both paths produce the same idempotency key. `source_event_id` is
SHA-256 of `(origin_key, normalized_email, submitted_at_utc truncated to the second)`,
where `origin_key_for_session()` resolves a CSV row back to the form's own `form_id`
when one exists, and falls back to `cohort:<id>` when it does not. So a CSV re-import of
API-ingested data collides instead of duplicating, and so does a re-download of the same
export saved as `responses (1).csv`. The key is deliberately **not** the row number
(re-export with rows reordered would duplicate everything), **not** the Forms
`responseId` alone (it exists on only one of the two paths), and **not** the file name
(a renamed re-export is the likeliest way a duplicate import actually happens).

### How to verify the handling still works

```bash
cufa pull --session <session-id>     # first run: rows_written > 0
cufa pull --session <session-id>     # second run: read=0 or written=0, never duplicates
```

```sql
select form_id, response_watermark, last_polled_at from session_form;
select source, source_timezone, submitted_at_raw, submitted_at_utc from checkin limit 5;
```

On the API path `source_timezone` must be NULL and `submitted_at_raw` must end in `Z`.
A non-NULL `source_timezone` on a `forms_api` row means something converted a timestamp
that did not need converting.

In tests, `FakeGoogleClient(fail_on_response_page=2)` fails mid-pull; assert the
watermark is unchanged afterwards. `FakeGoogleClient(page_size=2)` forces pagination.

### If Google changes this

- **If a REST `setDestination` appears, do not switch to it.** You would be trading a
  clean UTC feed for locale-formatted strings with no offset — reintroducing the exact
  bug this path avoids. A linked Sheet is worth having only as something for humans to
  look at.
- **If the `filter` syntax changes**, the expression is built in exactly one place
  (`pull_session`, `response_filter = f"timestamp > {watermark}"`) and parsed in one
  place in the fake (`_parse_timestamp_filter`).
- **If `lastSubmittedTime` is renamed or dropped**, edit
  `RealGoogleClient.list_responses`. Do not fall back to `createTime` as the primary —
  it is when the response was started.

---

## Trap 4 — service accounts cannot own Workspace assets

### What it is

A service account cannot own a Google Form. The two ways around that are domain-wide
delegation, where a service account impersonates a real user (and needs Workspace admin
console access to configure), or ordinary user OAuth, where a person signs in once.

### What it silently breaks

Attempting the service-account route tends to fail late and confusingly: form creation
appears to work in some configurations and the assets end up somewhere nobody can find,
or Drive quota errors surface with no obvious cause. Worse, the failure mode after a
misconfigured delegation is a form owned by an account CU cannot administer — which is
the same class of problem as the contractor-ownership problem below.

### How this codebase handles it

User OAuth, in `src/cufa/google/oauth.py`, with **exactly two API scopes**, declared
once in `src/cufa/google/base.py`:

```python
SCOPES = (
    "https://www.googleapis.com/auth/forms.body",   # create, update, publish
    "https://www.googleapis.com/auth/drive.file",   # copy the template
)
```

`build_flow()` requests those two plus `openid` and `userinfo.email`, which are used
only to learn which account connected (so the console can display it and the CLI can
store it). No other scope is requested.

**Why `drive.file` is enough.** It grants access only to files the app itself created
or that a user explicitly opened with it. This app creates the template through its own
credentials, so the template is app-created and stays in scope — and every session form
is a copy the app makes, so each copy is app-created too. The entire object graph this
system touches is inside `drive.file` by construction. It is also what authorizes reading
responses: `forms.responses.list` accepts `drive`, `drive.file` or
`forms.responses.readonly`, and the narrowest of those that works is the one requested.

**Why not broader Drive scope.** `drive` or `drive.readonly` would hand this tool read
access to a CU staff member's whole Drive to do a job that never needs to see anything
it did not create. It is also a sensitive/restricted scope, which drags in Google's
verification process for no benefit. The narrow scope is both less risk and less
paperwork.

**Why domain-wide delegation was rejected.** It requires Workspace super-admin access
to the admin console, which CU may not have available on demand, and it grants a
service account the ability to impersonate users across the domain — a large standing
permission for a tool that provisions one form a week. User OAuth needs one person to
click a consent screen once. The spec puts domain-wide delegation explicitly out of
scope.

**The token is encrypted at rest.** `store_credential()` runs the refresh token through
`crypto.encrypt_secret()` (Fernet, key from `CUFA_ENCRYPTION_KEY`, never stored in the
database or the repo) into `google_credential.refresh_token_enc`, a `bytea`.
`credential_status()` reads connection state **without decrypting anything**;
`load_credentials()` decrypts in memory only, refreshes, and stamps
`last_refreshed_at`. `disconnect()` sets `revoked_at` and overwrites the ciphertext
with an empty `bytea`, keeping the "who connected this, and when" record while removing
the credential. `google_credential` has RLS enabled with **no policy at all**, so
nothing but the service role can read it.

**The connecting account owns the forms.** That is the point: the work product stays in
CU's Drive rather than a contractor's.

### How to verify the handling still works

```bash
cufa google status   # connected account, granted scopes, warns if a required scope is missing
```

```sql
-- Must be ciphertext. Fernet tokens start with the bytes 'gAAAAA'.
select account_email, left(encode(refresh_token_enc, 'escape'), 12) as looks_like, scopes
  from google_credential;
```

If that column ever renders as something that looks like a Google refresh token
(`1//…`), the encryption path has been bypassed. `src/cufa/logging_setup.py` also
redacts token-shaped and key-shaped strings at **every** log level, including DEBUG,
and email addresses at INFO and above.

### If Google changes this

- **If CU acquires Workspace admin and wants delegation**, the change is confined to
  `load_credentials()` returning a delegated credential; `RealGoogleClient` and
  everything above it talk to the `FormsClient` protocol and do not care where the
  credentials came from. Re-read this section before doing it anyway — the ownership
  argument does not change.
- **If `drive.file` stops covering copies of app-created files**, that shows up as a
  `403` on `files.copy` in `RealGoogleClient.copy_form`. Confirm against Google's scope
  documentation before widening the scope; widening is the last resort, not the first.
- **If a staff member leaves**, transfer form ownership in Drive first, then reconnect
  with the new account (`cufa google disconnect`, then `cufa google connect`).

---

## Trap 5 — responses are keyed by `questionId`, and copies share them

> **Update, August 2026 — resolved by measurement.** Against a live Workspace
> account, `files.copy` **preserves** question ids: a copy of the Part B
> template carried all four of the template's ids unchanged. `FakeGoogleClient`
> defaults to that behaviour, so the fake now matches reality.
>
> **This makes the read-back more important, not less.** Every Part B form
> copied from one template shares the same ids — so the rotating slot has the
> *same* `questionId` in week 2 and in week 5, with different question text. The
> id therefore says nothing about which week's question it was, and "what was
> actually asked in week 3" is answerable only from the per-form
> `question_text` snapshot. Anything that keyed a map on `questionId` alone
> would look correct and quietly merge ten weeks of different questions into
> one.
>
> The `regenerate` setting is kept and the mapping tests still run under both.
> One measurement, on one account, on one day, is evidence about Google's
> current behaviour and not a guarantee about it — and the cost of keeping the
> code correct under either is one API call per provisioned form.
>
> One id is *not* shared: the help checkbox is created on each copy rather than
> carried by the template, so it gets a fresh id per form. A form's map is
> therefore partly shared and partly unique, which is exactly the shape that
> defeats reasoning about ids in the abstract.

Added with Part B. Trap 1 through trap 4 apply unchanged; this one only bites a form
with more than one question, which is why Part A never met it.

### What it is

`forms.responses.list` returns each response's answers keyed by **`questionId`** — not by
question title, not by position in the form:

```json
"answers": {
  "1a2b3c4d": {"questionId": "1a2b3c4d", "textAnswers": {"answers": [{"value": "6"}]}},
  "5e6f7a8b": {"questionId": "5e6f7a8b", "textAnswers": {"answers": [{"value": "…"}]}}
}
```

Part A had one question, so "the answer" was whatever came back. Part B has five, and
every one of them has to be told apart.

**When this was written, whether Drive's `files.copy` preserves question ids could not
be verified either way.** It has since been measured — it preserves them, see the note
above — but the handling was built without that knowledge, and is unchanged by it: the
ids are read back off each form rather than assumed. What follows describes both
possibilities, because code that is correct under only one of them fails silently under
the other, and because the measurement is an observation rather than a promise.

### What it silently breaks

Everything, in the worst possible way — quietly and plausibly.

If the code assumes ids are **preserved** and Google regenerates them, every answer
arrives under an id nothing recognises. If the code assumes they are **regenerated** and
Google preserves them, a cached id from another form still resolves. Either mistake puts
a confidence rating in the takeaway column and a takeaway in the confidence column.

The result is not an error. It is a table full of data that looks entirely ordinary:
integers in the integer column, sentences in the text column, a confidence trend that
plots. Nobody discovers it, because there is nothing to discover — the numbers are just
wrong.

Matching on **title** instead has its own failure, and it is a certainty rather than a
risk: the rotating slot's title changes every single week *by design*, and a teacher can
retitle any field in the Forms editor without telling anyone. Matching on **position**
breaks the first time somebody adds a question.

### How this codebase handles it

Nothing is assumed and nothing is hardcoded.

1. **The form is read back after it is built.** `provision_session(..., part="b")` calls
   `client.get_form(form_id)` — a separate call from `read_settings`, deliberately, and
   never served from a cache a `batchUpdate` could have invalidated.

   `src/cufa/provisioning.py` → `_apply_part_b_content`

2. **The mapping is recorded per form**, in `form_question_map`: `questionId` → one of
   `confidence` / `takeaway` / `rotating` / `shoutout` / `help`, plus the item index, the
   rotating kind, and **the exact text shown to fellows**.

   `src/cufa/question_map.py` → `record_map`

3. **Slots are matched by item index**, which this application controls at creation time.
   Never by title.

   `src/cufa/form_content_b.py` → `item_specs`

4. **Ingest resolves every answer through that table, and refuses a form whose map is
   missing or incomplete.** Not "skip that field" — refuse the form, write nothing, and
   say which slot is missing.

   `src/cufa/question_map.py` → `require_map`; `src/cufa/ingest/forms_b.py`

5. **The question text is snapshotted at provisioning time and never reconstructed.**
   `config/rotation.json` may have changed since; "what was actually asked in week 3" has
   to be answerable from the database alone.

Re-provisioning is the repair. On a form that is already published, provisioning re-reads
it and refreshes the map **without touching a single question**, so pressing it on a form
that is already collecting is safe.

### How to verify the handling still works

`FakeGoogleClient` takes a `question_id_scheme` argument with two settings, and the suite
runs the mapping tests under **both**:

```python
# Ids survive the copy.
FakeGoogleClient(question_id_scheme=QUESTION_IDS_PRESERVED)
# Ids are minted fresh on the copy.
FakeGoogleClient(question_id_scheme=QUESTION_IDS_REGENERATED)
```

That is not an edge case being covered. It is the unresolved question being made
harmless: whichever one Google does, the same assertions hold.

```bash
pytest tests/test_part_b.py -k "question_ids or map or retitled"
```

The tests that matter:

- `test_1_2_question_ids_resolve_under_either_copy_behaviour` — the confidence rating
  lands in `confidence_raw` and the takeaway in `takeaway_text`, under both schemes.
- `test_2b_a_retitled_question_does_not_move_its_answers` — a teacher fixes a typo; the
  answers stay where they were.
- `test_3_an_incomplete_map_refuses_to_ingest` — a deleted slot stops the run and writes
  nothing.
- `test_4_question_text_is_snapshot_and_survives_a_config_change` — rewriting
  `config/rotation.json` does not rewrite history.

`make demo` also stages the failure deliberately: it deletes one slot from one form's
map, shows `cufa pull --part b` refusing that form, and then repairs it by
re-provisioning.

### If Google changes this

- **Now that the copy behaviour is known**, this stays exactly as it is. Reading the ids
  back costs one API call per form, per provisioning run, and removes an entire class of
  invisible corruption. Do not "optimise" it away on the strength of one measurement — and
  note that "ids are preserved" is the case that makes a per-form map *necessary* rather
  than redundant, because it means every week's form answers under the same ids.
- **If `forms.get` stops returning `questionId` on items**, provisioning must fail loudly
  rather than record a partial map — `record_map` already raises when a spec's index has no
  question id.
- **If a stable, documented alias for a question becomes available** (something the caller
  sets and the API echoes back), that is worth adopting: it would let the map be asserted
  rather than merely recorded. Keep the read-back regardless.

---

## Trap 6 — `updateItem` needs the whole item body, not the field you are changing

Found on the first real provisioning run, in August 2026. Offline it looks
right; the fake used to accept it; the live API does not.

### What it is

To retitle an existing question you send `updateItem` with an `updateMask`. The
obvious request sends only the field named in the mask:

```json
{"updateItem": {
  "item": {"title": "What's still unclear?"},
  "location": {"index": 2},
  "updateMask": "title"}}
```

The live API rejects it:

```
400 Invalid requests[1]: A QuestionItem or QuestionGroupItem cannot be changed
    into a non question Item type by an Update operation.
```

An item body with no `questionItem` reads to Google as a request to convert the
question into a plain text block — **whatever the `updateMask` says**. The mask
decides what is applied; the body has to describe the item it still is.

### What it silently breaks

Part B only, and completely.

Part A has always sent its full item body, so it works and goes on working. Part
B retitles the rotating slot on **every single provision** — that is what the
rotation *is* — so a title-only body means no Part B form can ever be created,
while Part A carries on fine. The failure is loud but the cause is not: nothing
in the message mentions the mask, and the request looks like the documentation.

Worse, it fails **after** `files.copy` has already succeeded, so each attempt
leaves an orphaned copy in Drive. See below.

### How this codebase handles it

`ItemSpec` holds the item body once, and both requests are built from it:

```python
spec.request         # createItem: {"item": <body>, "location": {...}}
spec.update_request  # updateItem: {"item": <body>, "location": {...},
                     #              "updateMask": "title"}
```

`src/cufa/form_content_b.py` → `ItemSpec.update_request`

Because the body is shared, a create and an update cannot describe different
items — which is the other way this bites, quietly, when somebody keeps two
copies in sync by hand.

### How to verify the handling still works

`FakeGoogleClient.batch_update` now raises the same 400 for an `updateItem`
whose body has no `questionItem`. Every Part B provisioning test retitles the
rotating slot, so all of them exercise it:

```bash
pytest tests/test_part_b.py -q
```

There is no separate "trap 6 test" because there does not need to be one: the
fake refuses the bad shape, so any regression fails the whole Part B suite
rather than one case somebody might delete.

### The orphan it leaves behind

`files.copy` succeeds, then `batchUpdate` fails. The copy is real, in Drive, and
nothing points at it.

`provision_session` records that copy so a retry **resumes** it instead of
making another — and records it on a **separate autocommit connection**, because
every caller wraps provisioning in a transaction and rolls it back when this
raises. Writing the row on the caller's connection looks correct and is not: it
disappears with the rollback, and the orphan becomes invisible.

`src/cufa/provisioning.py` → `_record_orphan`

Covered by `tests/test_provenance.py`:
`test_a_form_copied_before_a_failure_is_recorded_despite_a_rollback` and
`test_the_next_run_resumes_that_form_rather_than_copying_another`.

To find orphans that predate this fix, list the forms the app can see in Drive
and compare against `session_form` and `form_template` — anything in Drive with
no row is an orphan, and safe to bin once you have checked it has no responses.

### If Google changes this

If a title-only body starts being accepted, nothing here needs to change —
sending the full body remains correct and is not more expensive. Do not
"simplify" it back; the saving is a few hundred bytes per provision and the cost
is Part B not working at all.

---

## How each trap is tested — `FakeGoogleClient`

`src/cufa/google/fake.py` implements the same seven-method `FormsClient` protocol as the
real client, in memory. Its purpose is not offline tests — that is a side effect. Its
purpose is that **trap handling is only trustworthy if the failures are actually
exercised.** A comment saying "we check the publish state" proves nothing; a fake that
returns an unpublished form and a test asserting provisioning refuses it proves
something.

Its defaults reproduce Google's real August-2026 behaviour, so the happy path through
the fake is reachable *only* by code that handles the traps:

| Default | Reproduces |
|---|---|
| a new form is `is_published=False`, `is_accepting_responses=False` | trap 1 |
| a new form is `DO_NOT_COLLECT` | trap 2, Google's real default |
| `batchUpdate` with `emailCollectionType` raises `400 INVALID_ARGUMENT` | trap 2 |
| `copy_form` carries the source's `email_collection_type` | why template-and-copy works |
| `copy_form` preserves question ids | trap 5 — one of two equally plausible behaviours |
| `updateItem` with no `questionItem` in the body raises `400` | trap 6 |

### The knobs

| Constructor argument | What it simulates | Trap |
|---|---|---|
| `publish_readback_fails=True` | `setPublishSettings` returns 200 and the state does not change — `read_settings` still reports unpublished. This is what "fails silently" means. | 1 |
| `reject_email_collection=True` *(default)* | `batchUpdate` → `updateSettings` → `emailCollectionType` raises `400 INVALID_ARGUMENT` naming `requests[N].update_settings.settings.email_collection_type`. Set `False` to simulate Google fixing it. | 2 |
| `default_email_collection=...` | What a freshly created form reports: `DO_NOT_COLLECT` (default), `RESPONDER_INPUT`, or `VERIFIED`. | 2 |
| `page_size=N` *(default 2)* | Pagination granularity for `list_responses`, so a multi-page loop is exercised with a handful of rows. | 3 |
| `rate_limit_calls=N` | Raise `429 RESOURCE_EXHAUSTED` on the next N `list_responses` calls, then succeed — exercises `_list_with_backoff`. | 3 |
| `fail_on_response_page=N` | Raise `503` on the Nth `list_responses` call (1-based, counted across the client). Not retried, so it propagates — exercises watermark safety. | 3 |
| `question_id_scheme=...` | Whether `copy_form` preserves question ids (`QUESTION_IDS_PRESERVED`, the default) or mints new ones (`QUESTION_IDS_REGENERATED`). Unlike every other knob here, neither setting is "the failure" — Google's real behaviour is unknown, so the Part B mapping tests run under **both**. | 5 |

| Method | What it simulates |
|---|---|
| `simulate_human_sets_verified(form_id)` | The one manual step. It is **only** available explicitly, never as a side effect of an API call — because that is exactly the property being modelled. |
| `simulate_human_breaks_verified(form_id)` | Someone edits the template and turns collection back to `RESPONDER_INPUT`. |
| `simulate_teacher_retitles(form_id, index, title)` | A teacher fixes the wording of a question in the Forms editor. The question id does not change — which is exactly why answers are resolved by id and slots assigned by index. |
| `seed_responses(form_id, rows)` | Load `(email, rfc3339, passphrase)` triples for Part A, or dicts with `answers_by_index` / `answers_by_id` for Part B, kept oldest-first the way the API returns them. Answers are stored keyed by question id, as the API returns them. |
| `get_form(form_id)` | The form's items with their question ids — what provisioning reads back to build the map. |
| `calls(action)` | Every recorded call of one kind — for assertions like "publish was called after every create". |
| `demo_client()` | A fake already walked through create-template plus the human's Verified step, so the demo starts where a real CU install starts on day two. |
| `save()` / `restore(path)` | Persist the fake's forms and responses to a JSON file (`CUFA_FAKE_GOOGLE_STATE`, default `fixtures/fake_google_state.json`), so `make demo` and `make demo-console` share one fake across separate `cufa` processes. |

### The shape of each test

```python
# Trap 1 — publish read-back fails: provisioning must raise and must not report ready.
client = FakeGoogleClient(publish_readback_fails=True)
# ... create + verify template, then:
with pytest.raises(PublishVerificationFailed):
    provision_session(conn, client, session_id)
assert is_ready(conn, session_id) is False

# Trap 1 — publish is actually called after every form creation.
assert client.calls("set_publish_settings")

# Trap 2 — an unverified template blocks provisioning entirely.
client.simulate_human_breaks_verified(template_form_id)
with pytest.raises(TemplateNotVerified):
    provision_session(conn, client, session_id)

# Trap 2 — the 400 is handled and leaves nothing half-provisioned.
assert try_set_verified_email(client, form_id) is False

# Trap 3 — a multi-page list is fully consumed.
client = FakeGoogleClient(page_size=2)   # with 5 seeded responses

# Trap 3 — a mid-pull failure leaves the watermark unmoved.
client = FakeGoogleClient(fail_on_response_page=2)
```

Route both the fake and the Gemini adjudicator in through injection —
`google.factory.set_fake_client()` and the `adjudicator=` argument to
`adjudicate_cohort()`. `CUFA_FAKE_GOOGLE=1` selects the fake through the same code path
the real client uses, so there is no separate branch to rot. **Tests must not hit the
network.**

---

## References

Every `developers.google.com` link below was confirmed in August 2026 to be the
canonical path for that method or guide. The pages themselves could not be fetched from
the environment these docs were written in (outbound access to `developers.google.com`
is blocked there), so the URLs were verified through search-index lookups returning the
exact path and page title, not by reading the rendered page. If one 404s, search for the
method name — Google has already moved these once, from `/forms/api/…` to
`/workspace/forms/api/…`. The GitHub issue was fetched and read directly.

**Forms API — methods**

- `forms.create` — https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/create
- `forms.batchUpdate` — https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/batchUpdate
- `forms.setPublishSettings` — https://developers.google.com/workspace/forms/api/reference/rest/v1/forms/setPublishSettings
- `forms.responses.list` — https://developers.google.com/workspace/forms/api/reference/rest/v1/forms.responses/list
- REST resource index — https://developers.google.com/workspace/forms/api/reference/rest

**Forms API — guides**

- API changes to Google Forms (the publish-state change) — https://developers.google.com/workspace/forms/api/guides/api-changes-to-google-forms
- Publish and manage responders — https://developers.google.com/workspace/forms/api/guides/publish-form
- Create a form or quiz — https://developers.google.com/workspace/forms/api/guides/create-form-quiz
- Update a form or quiz — https://developers.google.com/workspace/forms/api/guides/update-form-quiz
- Retrieve forms and responses — https://developers.google.com/workspace/forms/api/guides/retrieve-forms-responses
- Usage limits — https://developers.google.com/workspace/forms/api/limits
- Release notes — https://developers.google.com/workspace/forms/release-notes

**Drive API**

- `files.copy` — https://developers.google.com/workspace/drive/api/reference/rest/v3/files/copy
- Choose Google Drive API scopes — https://developers.google.com/workspace/drive/api/guides/api-specific-auth

**OAuth**

- OAuth 2.0 scopes for Google APIs — https://developers.google.com/identity/protocols/oauth2/scopes
- Using OAuth 2.0 for web server applications — https://developers.google.com/identity/protocols/oauth2/web-server
- Configure the OAuth consent screen — https://developers.google.com/workspace/guides/configure-oauth-consent
- Create access credentials — https://developers.google.com/workspace/guides/create-credentials

**Trap 2 evidence**

- `email_collection_type` rejected with 400 —
  https://github.com/googleapis/google-api-nodejs-client/issues/3467 (fetched and read;
  reports `Invalid value at 'requests[0].update_settings.settings.email_collection_type'`)

**Trap 3 context**

- Apps Script `Form.setDestination()`, which has no REST equivalent —
  https://developers.google.com/apps-script/reference/forms/form

---

## See also

- `docs/decisions.md` — ADR-002 through ADR-005 record why traps 1-4 were decided the
  way they were; **ADR-024** does the same for trap 5.
- `docs/setup/google-cloud.md` — enabling the APIs, the OAuth client, the two scopes.
- `docs/setup/part-b-form.md` — the Part B form, its own Verified step, and the rotation.
- `src/cufa/google/base.py` — the seven-method contract, chosen so that each trap is
  *observable* through the interface rather than hidden inside one implementation.
