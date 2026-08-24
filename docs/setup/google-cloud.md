# Google Cloud setup

One-time work: a Google Cloud project with two APIs enabled and an OAuth client, so a CU
staff member can connect their Google account to this system. Fifteen minutes, done once
per install.

**Do this with a CU staff account, not a contractor's.** The account that completes the
OAuth flow **owns every form this system creates** — a service account cannot own a
Google Form (see `docs/google-api-traps.md`, trap 4). When that person leaves, the forms
leave with them unless ownership is transferred first. Use an account CU controls and
expects to keep.

---

## 1. Create or choose a Google Cloud project

https://console.cloud.google.com → project picker → **New project**, or reuse an existing
CU project. The project is only a container for the API enablement and the OAuth client;
it does not own the forms.

Note which Google account you are signed in as. If CU has a Google Workspace domain,
being signed in as a Workspace user is what makes the "Internal" consent-screen option
available in step 3, which is much less friction.

## 2. Enable the two APIs

**APIs & Services → Library**, then enable both:

- **Google Forms API** — creating, updating and publishing forms, and reading responses.
- **Google Drive API** — copying the template form. A Google Form is a Drive file, so the
  copy goes through Drive.

Both are required. Skipping Drive gives a `403` on `files.copy` at the first
`cufa provision`, with a message pointing at this page.

## 3. Configure the OAuth consent screen

**APIs & Services → OAuth consent screen** (in newer consoles: **Google Auth platform →
Branding / Audience**).

**User type:**

- **Internal** — available only if the account is in a Google Workspace organisation.
  Choose this if CU has one. Only users in the organisation can authorise the app, there
  is no test-user list, and no Google verification review.
- **External** — for a plain `@gmail.com` account or a personal project. The app stays in
  **Testing** status, which is fine for an internal tool, but you must add every account
  that will connect under **Audience → Test users**. An account not on that list gets
  `access_denied` no matter what else is correct. Refresh tokens issued to a Testing-status
  external app **expire after seven days**, which means reconnecting weekly — a strong
  reason to use Internal if CU's Workspace allows it.

**App information:** an app name CU staff will recognise on the consent screen (e.g.
"CU Fellowship Check-in"), a support email, and a developer contact email. Nothing else
is needed for a local-only internal tool.

**Scopes:** add the four this app requests (step 5 explains each). You can also leave the
scope list empty here and let them be requested at authorisation time; adding them makes
the consent screen show what it will actually ask for.

## 4. Create the OAuth 2.0 Client ID

**APIs & Services → Credentials → Create credentials → OAuth client ID**.

- **Application type: Web application.** Not "Desktop app" — the console runs an HTTP
  redirect handler, and the two client types are not interchangeable.
- **Name:** anything, e.g. "CU check-in console (local)".
- **Authorised redirect URIs → Add URI:**

```text
http://127.0.0.1:8000/google/callback
```

Copy that string exactly. Google matches redirect URIs by exact string:
`localhost` and `127.0.0.1` are different values, a trailing slash is a different value,
and `https` is a different value. It must equal `GOOGLE_OAUTH_REDIRECT_URI` in `.env`,
which defaults to exactly the URI above (`src/cufa/config.py`).

If you change `CUFA_CONSOLE_PORT`, change the port in **both** places — the registered
redirect URI here and `GOOGLE_OAUTH_REDIRECT_URI` in `.env` — or the flow fails with
`redirect_uri_mismatch`.

No **Authorised JavaScript origins** entry is needed: the console is server-rendered and
the OAuth exchange happens server-side.

Copy the **Client ID** and **Client secret** from the dialog. The secret is retrievable
later from the same page.

## 5. The scopes, and why each one

Requested in `src/cufa/google/base.py` (`SCOPES`) and `src/cufa/google/oauth.py`
(`build_flow`):

| Scope | Why it is needed |
|---|---|
| `https://www.googleapis.com/auth/forms.body` | Create the template form, update titles/descriptions/the passphrase question, and **publish** each session form. Since 1 July 2026 an API-created form is unpublished and accepts no responses until `setPublishSettings` is called, so this scope is what makes a form able to collect anything at all. |
| `https://www.googleapis.com/auth/drive.file` | Two jobs. (a) Copy the template (`files.copy`) — every session form is a Drive copy, because copying preserves the Verified email-collection setting that the API cannot reliably set. (b) **Read responses**: `forms.responses.list` accepts `drive`, `drive.file`, or `forms.responses.readonly`, and `drive.file` covers it for forms this app created. |
| `openid` | Standard OpenID Connect scope, requested so the app can identify the connected account. |
| `https://www.googleapis.com/auth/userinfo.email` | Read **only** the connected account's own address, so the console can display "connected as …" and the CLI can store it in `google_credential.account_email`. It reads no other person's data. |

So the consent screen shows four items, not two. The two that grant access to CU's data
are the first two.

### Why not a broader Drive scope

`drive.file` grants access **only to files the app itself created**, plus files a user
explicitly opens with it. That is sufficient here by construction: this app creates the
template through its own credentials, so the template is app-created and stays in scope,
and every session form is a copy the app makes, so each copy is app-created too. The
entire set of files this system touches is inside `drive.file`.

`https://www.googleapis.com/auth/drive` would hand this tool read/write access to the
staff member's **entire** Drive — every document, every folder — to do a job that never
needs to see anything it did not create. It is also a restricted scope, which pulls in
Google's app-verification process for no benefit. If you ever find yourself widening this
because something returned 403, read `docs/google-api-traps.md` trap 4 first: widening
is the last resort, not the first diagnosis.

`https://www.googleapis.com/auth/forms.responses.readonly` is not requested either. It
would be a third scope granting something `drive.file` already covers for app-created
forms, and every extra item on a consent screen is one more thing a staff member has to
decide about.

## 6. Put the credentials in `.env`

```bash
cp .env.example .env      # if you have not already
```

```dotenv
GOOGLE_CLIENT_ID=xxxxxxxxxxxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxxxxxxxxxxxxxxxxxxx
GOOGLE_OAUTH_REDIRECT_URI=http://127.0.0.1:8000/google/callback
```

`.env` is gitignored. `.env.example` is committed and contains no real values. Do not
download the client-secret JSON into the repo — `client_secret*.json` and `token.json`
are gitignored precisely because that is the common accident, but the reliable rule is:
the secret lives in `.env` and nowhere else in the tree.

## 7. Generate the encryption key

The Google **refresh token is encrypted at rest** in Postgres. Without a key, the app
refuses to store one rather than falling back to plaintext.

```bash
.venv/bin/python -m cufa.crypto keygen
```

That prints a URL-safe base64 Fernet key. Put it in `.env`:

```dotenv
CUFA_ENCRYPTION_KEY=<the printed key>
```

Keep it. Rotating the key makes existing stored credentials undecryptable, and the fix is
to reconnect Google — the error message says so. The key is never written to the database
or the repo, and `logging_setup` redacts key-shaped strings at every log level.

## 8. Connect the account

Either through the console:

```bash
cufa serve          # then open http://127.0.0.1:8000 → "Connect Google"
```

or from the CLI on a headless machine, which prints the URL and takes the pasted `code`:

```bash
cufa google connect
cufa google status
```

`cufa google status` prints the connected address, when it was connected, when the token
was last refreshed, and the granted scopes — and warns if a required scope is missing.

Then do the one-time template step, which is the single thing the Forms API cannot do
reliably: `cufa template create`, set **Settings → Responses → Collect email addresses →
Verified** by hand on the template form, then `cufa template verify`. Provisioning stays
blocked until the API itself confirms `VERIFIED`. See `docs/setup/console.md` and
`docs/google-api-traps.md` trap 2.

---

## Troubleshooting

**`redirect_uri_mismatch`.** The URI the app sent is not registered on the client,
character for character. Compare `GOOGLE_OAUTH_REDIRECT_URI` in `.env` against the entry
under Credentials → your client → Authorised redirect URIs. Usual causes: `localhost` vs
`127.0.0.1`, a changed console port, a trailing slash.

**`access_denied` for an account that should work.** External + Testing app: the account
is not in the **Test users** list. Add it, or switch the consent screen to Internal if CU
has a Workspace.

**"Google returned no refresh token."** Google issues a refresh token only on first
consent. If the account has already granted this app access, remove it at
https://myaccount.google.com/permissions and connect again. (`authorization_url()`
already requests `access_type=offline` with `prompt=consent`, which is what makes the
refresh token come back at all; without both, the connection silently stops working an
hour later.)

**`403` with "API has not been used in project …".** One of the two APIs is not enabled.
Enable it and wait a minute for propagation.

**`403` on `files.copy` specifically.** Drive API not enabled, or the connected account
lost access to the template form.

**The connection stops working after a week.** An app whose consent screen is External
and whose publishing status is Testing has its refresh tokens revoked after seven days.
Move the consent screen to Internal if CU has a Workspace; otherwise move the app to
Production (which may trigger Google's verification review for `forms.body`) or accept
weekly reconnection. Symptom: `invalid_grant — Token has been expired or revoked`.

**A staff member is leaving.** Transfer ownership of the forms in Drive first, then
`cufa google disconnect` and reconnect as the new account. `disconnect` keeps the row —
so "who connected this, and when" survives — while clearing the stored ciphertext.

---

## See also

- `docs/google-api-traps.md` — trap 1 (publish), trap 2 (Verified email), trap 4 (OAuth
  vs service accounts), and the reference links to Google's own documentation.
- `docs/setup/console.md` — the connect screen and the one-time template step.
- `docs/setup/local-dev.md` — Docker, the Supabase CLI, and running everything with no
  Google account at all.
