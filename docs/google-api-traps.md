# Four Google API traps this system is built around

Read this before you change anything under `src/cufa/google/`, `src/cufa/template.py`,
`src/cufa/provisioning.py`, or `src/cufa/ingest/`.

Four behaviours of the Google Forms and Drive APIs will break attendance collection
**silently**. Not with an exception, not with a 500 — the code returns 200, the form
link opens in a browser, the teacher shares it, and either nothing arrives or what
arrives cannot be attributed to a person. You find out weeks later, when the data you
needed does not exist and cannot be recreated.

Everything below is current as of **August 2026**. Each section says what the trap is,
what it silently breaks, exactly where this codebase handles it, how to check the
handling still works, and what to do if Google changes the behaviour.

If you are about to "simplify" one of these — publish without reading the state back,
set email collection through the API, link a response spreadsheet, use a service
account — this document is the argument against it.

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

### What it is

Verified email collection is the entire premise of this design: Google confirms the
respondent's address instead of the respondent typing one. Setting it through
`forms.batchUpdate` → `updateSettings` → `emailCollectionType` has been reported
returning:

```
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
where `origin_key_for_session()` resolves a CSV back to the form's own `form_id` when
one exists. So a CSV re-import of API-ingested data collides instead of duplicating.
It is deliberately **not** the row number (re-export with rows reordered would
duplicate everything) and **not** the Forms `responseId` alone (which exists only on
one of the two paths).

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
system touches is inside `drive.file` by construction.

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

## How each trap is tested — `FakeGoogleClient`

`src/cufa/google/fake.py` implements the same six-method `FormsClient` protocol as the
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

### The knobs

| Constructor argument | What it simulates | Trap |
|---|---|---|
| `publish_readback_fails=True` | `setPublishSettings` returns 200 and the state does not change — `read_settings` still reports unpublished. This is what "fails silently" means. | 1 |
| `reject_email_collection=True` *(default)* | `batchUpdate` → `updateSettings` → `emailCollectionType` raises `400 INVALID_ARGUMENT` naming `requests[N].update_settings.settings.email_collection_type`. Set `False` to simulate Google fixing it. | 2 |
| `default_email_collection=...` | What a freshly created form reports: `DO_NOT_COLLECT` (default), `RESPONDER_INPUT`, or `VERIFIED`. | 2 |
| `page_size=N` *(default 2)* | Pagination granularity for `list_responses`, so a multi-page loop is exercised with a handful of rows. | 3 |
| `rate_limit_calls=N` | Raise `429 RESOURCE_EXHAUSTED` on the next N `list_responses` calls, then succeed — exercises `_list_with_backoff`. | 3 |
| `fail_on_response_page=N` | Raise `503` on the Nth `list_responses` call (1-based, counted across the client). Not retried, so it propagates — exercises watermark safety. | 3 |

| Method | What it simulates |
|---|---|
| `simulate_human_sets_verified(form_id)` | The one manual step. It is **only** available explicitly, never as a side effect of an API call — because that is exactly the property being modelled. |
| `simulate_human_breaks_verified(form_id)` | Someone edits the template and turns collection back to `RESPONDER_INPUT`. |
| `seed_responses(form_id, rows)` | Load `(email, rfc3339, passphrase)` triples (or dicts with extra answers), kept oldest-first the way the API returns them. |
| `calls(action)` | Every recorded call of one kind — for assertions like "publish was called after every create". |
| `demo_client()` | A fake already walked through create-template plus the human's Verified step, so the demo starts where a real CU install starts on day two. |

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

- `docs/decisions.md` — ADR-002 through ADR-005 record why each of these was decided
  the way it was, and what was rejected.
- `docs/setup/google-cloud.md` — enabling the APIs, the OAuth client, the two scopes.
- `src/cufa/google/base.py` — the six-method contract, chosen so that each trap is
  *observable* through the interface rather than hidden inside one implementation.
