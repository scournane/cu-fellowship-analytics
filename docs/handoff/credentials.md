# Credentials handoff

Every account, key and secret this system needs, in one table, so the person
taking it over can sign in to everything without archaeology.

> **Nothing in this file is filled in, and nothing in it should ever be.**
> This file is committed to git. Git history is permanent: a key pasted here and
> deleted in the next commit is still readable forever by anyone who can clone
> the repo. Fill in the **Username** and **Password / secret value** columns in a
> password manager (1Password, Bitwarden) or a Google Doc restricted to CU staff,
> and hand *that* over. Keep this file as the checklist of what belongs in it.
>
> If a value was ever pasted into Slack, an email, a ticket or a commit, treat it
> as compromised and rotate it during the handoff rather than after.

---

## 1. Accounts — someone has to own these

A login, not a key. Each one is a place a human signs in. The **Owner after
handoff** column is the part that actually matters: an account owned by a
departing contractor takes its data with it.

| # | Platform | What it is used for | Username / account | Password | Owner after handoff | Notes |
|---|---|---|---|---|---|---|
| 1 | **Google account (CU staff)** | The account that completes `cufa google connect`. **It owns every Google Form this system creates.** | | | | Must be a CU-controlled account, never a contractor's. Transferring form ownership later is manual, per form. See `docs/setup/google-cloud.md`. |
| 2 | **Google Cloud Console** — console.cloud.google.com | Holds the GCP project, the two enabled APIs, and the OAuth client. | | | | Project name/ID: ______. Usually the same login as #1. |
| 3 | **Google AI Studio** — aistudio.google.com | Where `GEMINI_API_KEY` is issued. | | | | Optional feature; see key #10. |
| 4 | **Slack workspace (admin)** | Installing the bot, inviting it to channels, reading the app config. | | | | Workspace: ______. Needs a workspace admin, not just a member. |
| 5 | **Slack API app** — api.slack.com/apps | The app "CIF participation bot" — tokens, scopes, event subscriptions. | | | | App name: `CIF participation bot`. Manifest is in `docs/setup/slack-bot.md`. |
| 6 | **GitHub** — github.com/scournane/cu-fellowship-analytics | The code. | | | | Currently under a personal account (`scournane`). **Transfer to a CU org** at handoff, or CU loses the repo when that account does. |
| 7 | **Supabase** (only if hosted, not local) | Postgres. Local dev needs no account — `supabase start` runs it in Docker. | | | | Leave blank if CU stays on local/self-hosted Postgres. Project ref: ______. |
| 8 | **Host running the bot** (VM, laptop, wherever) | The Slack bot must be a process that stays running, or events are lost permanently. | | | | Where it runs: ______. How it is restarted: ______. This is the single biggest operational risk — see `docs/setup/slack-bot.md`. |
| 9 | **SMTP / mail sender** | Help-request emails. **Not chosen yet** — CU has not picked a mail path, so nothing is configured and requests are recorded in the console only. | | | | Fill in only once CU chooses one. `SmtpNotifier` in `src/cufa/help_routing.py` takes host + port. |

---

## 2. Keys and secrets — the `.env` file

These are the variables in `.env` (copy `.env.example` → `.env`). `.env` is
gitignored and must stay that way.

| # | Variable | Platform | Where the value comes from | Value | Rotate by |
|---|---|---|---|---|---|
| 10 | `CUFA_DATABASE_URL` | Postgres / Supabase | Printed by `supabase start` for local. Hosted: Supabase → Project Settings → Database → Connection string. | | Changing the DB password in Supabase. |
| 11 | `CUFA_ENCRYPTION_KEY` | This app | `python -m cufa.crypto keygen`. Encrypts the stored Google refresh token at rest. | | Generating a new key **invalidates the stored token** — re-run `cufa google connect` after. |
| 12 | `CUFA_CONSOLE_SECRET` | This app | Any long random string; signs the console's session cookie. | | New random string. Logs everyone out; nothing else breaks. |
| 13 | `GOOGLE_CLIENT_ID` | Google Cloud | APIs & Services → Credentials → the Web application OAuth client. | | New OAuth client. |
| 14 | `GOOGLE_CLIENT_SECRET` | Google Cloud | Same OAuth client as #13. | | Credentials → the client → reset secret. |
| 15 | `GOOGLE_OAUTH_REDIRECT_URI` | Google Cloud | Not a secret, but must match the registered URI **byte for byte**. Default `http://127.0.0.1:8000/google/callback`. | | n/a — but change it in both `.env` and the Cloud Console together. |
| 16 | `GEMINI_API_KEY` | Google AI Studio | AI Studio → Get API key. Optional: without it the pipeline still finishes, and mismatches land in `needs_review` with `rule_name='ai_unavailable'`. | | Delete and re-issue in AI Studio. |
| 17 | `SLACK_BOT_TOKEN` (`xoxb-…`) | Slack app | App → OAuth & Permissions → Bot User OAuth Token. Needed in **every** mode. | | OAuth & Permissions → Revoke, then reinstall the app. |
| 18 | `SLACK_SIGNING_SECRET` | Slack app | App → Basic Information → Signing Secret. Verifies every inbound delivery; HTTP mode only (`cufa slack serve`). | | Basic Information → Regenerate. |
| 19 | `SLACK_APP_TOKEN` (`xapp-…`) | Slack app | App → Basic Information → App-Level Tokens, scope `connections:write`. Socket Mode only (`cufa slack socket`) — the mode needing no public URL. | | Revoke the app-level token and mint a new one. |

Everything else in `.env.example` (`CUFA_SLACK_*` timings, allowlists, channel
names, feature flags) is **configuration, not secrets** — it can live in the repo
or a plain doc. `.env.example` documents each one inline.

---

## 3. Permissions the accounts must carry

Handing over a login is not enough if its grants are wrong. Verify these after
the handover with `cufa doctor` and `cufa slack doctor`, which name what is
missing.

**Google OAuth scopes** (`src/cufa/google/base.py`) — the consent screen must
grant all four, plus `openid` and `userinfo.email`:

- `https://www.googleapis.com/auth/forms.body`
- `https://www.googleapis.com/auth/drive.file`
- `openid`
- `https://www.googleapis.com/auth/userinfo.email`

Both the **Google Forms API** and the **Google Drive API** must be enabled on the
project. Drive is not optional: a Form is a Drive file, so the template copy goes
through Drive, and skipping it gives a `403` on the first `cufa provision`.

**Consent screen type matters for the handoff.** If the app is **External** and in
Testing status, refresh tokens **expire after seven days** — someone must reconnect
weekly, forever. If CU has a Google Workspace domain, switch it to **Internal**
before handing over. This is the difference between a system that runs itself and
one that needs a standing calendar reminder.

**Slack bot scopes** (`src/cufa/slack/bot.py`):

- Required: `channels:history`, `channels:read`, `users:read`, `users:read.email`, `reactions:read`
- Reminders / badges / slash commands: `chat:write`, `im:write`, `commands`
- Private channels and mentions: `groups:history`, `groups:read`, `app_mentions:read`

The bot must also be **invited to** every channel it should record, including the
private staff channel named in `CUFA_SLACK_STAFF_CHANNEL`.

---

## 4. Non-credential things that still have to be handed over

Access without these leaves the new owner stuck.

| Thing | Where it lives | Value |
|---|---|---|
| Part B template form | A Google Form owned by account #1 | Form URL / ID: ______ |
| Staff console allowlist | `CUFA_CONSOLE_ALLOWLIST` in `.env` | Who can sign in: ______ |
| Help-request recipients | `config/help_routing.json` | Currently the Director of Programs (`adiah@civicsunplugged.org`). **Marked `DRAFT — NOT APPROVED`** — CU has not named a Fellow-support responder. If `recipients` is emptied, the check-in checkbox is omitted from every form. |
| Slack staff channel | `CUFA_SLACK_STAFF_CHANNEL` | Channel: ______ |
| Q&A channels | `CUFA_SLACK_QA_CHANNELS` | Channels whose **message text is stored**: ______ |
| Cohort identifier | `CUFA_SLACK_COHORT` | Default `cu-2026` |

---

## 5. Handoff checklist

- [ ] Fill in the Username / Password columns in a password manager, not this file.
- [ ] Rotate every secret that was ever shared over Slack, email or a ticket.
- [ ] Transfer the GitHub repo from `scournane` to a CU-owned org.
- [ ] Confirm the Google account owning the Forms is CU-controlled; transfer any form owned by a departing account.
- [ ] Switch the OAuth consent screen to **Internal** if CU has Workspace (kills the 7-day token expiry).
- [ ] Add the new owner as an admin on the Slack workspace and the Slack app.
- [ ] Write down where the Slack bot process runs and how it gets restarted.
- [ ] Run `cufa doctor` and `cufa slack doctor` as the new owner. Both must exit 0.
- [ ] Remove the departing person's access from every account above.
