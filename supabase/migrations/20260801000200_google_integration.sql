-- State that mirrors things living in Google's systems.
--
-- Every row here exists so that external state can be *read back and asserted*
-- rather than assumed from a 200 response. See docs/google-api-traps.md.

-- The connected CU staff account. Forms are owned by this person's Drive, which
-- is the point: the work product stays with CU.
create table if not exists google_credential (
    credential_id     uuid        primary key default gen_random_uuid(),
    account_email     text        not null unique,
    -- Fernet ciphertext, never the token itself. A raw SELECT on this table
    -- must not expose a usable credential.
    refresh_token_enc bytea       not null,
    scopes            text[]      not null default '{}',
    connected_at      timestamptz not null default now(),
    last_refreshed_at timestamptz,
    revoked_at        timestamptz
);

comment on column google_credential.refresh_token_enc is
    'Fernet-encrypted refresh token. Key comes from CUFA_ENCRYPTION_KEY and is '
    'never stored in the database or the repo.';

-- Trap 2: emailCollectionType cannot be set reliably through the API, so the
-- app creates ONE template form, a human flips email collection to Verified by
-- hand, and every per-session form is a Drive copy of that template.
create table if not exists form_template (
    template_id                 uuid        primary key default gen_random_uuid(),
    form_id                     text        not null unique,
    form_url                    text,
    edit_url                    text,
    created_at                  timestamptz not null default now(),
    -- NULL until the API itself confirms VERIFIED. Provisioning is blocked
    -- while it is NULL; we do not accept the human's word for it.
    verified_email_confirmed_at timestamptz,
    settings_snapshot           jsonb       not null default '{}'::jsonb,
    last_verified_at            timestamptz,
    is_active                   boolean     not null default true
);

comment on table form_template is
    'The single template form every session form is copied from. Copying a '
    'Google Form preserves its settings, including email collection.';

comment on column form_template.settings_snapshot is
    'form.settings exactly as last read back from the API, so a later drift is '
    'diffable rather than mysterious.';

-- One provisioned form per session. Provisioning is idempotent against this
-- table: if a row exists, we show it instead of creating a second form.
create table if not exists session_form (
    session_form_id     uuid        primary key default gen_random_uuid(),
    session_id          uuid        not null unique
                        references "session" (session_id) on delete cascade,
    template_id         uuid        references form_template (template_id),
    form_id             text        not null unique,
    form_url            text        not null,
    edit_url            text,
    provisioned_at      timestamptz not null default now(),
    published_at        timestamptz,
    -- Trap 1: set only after setPublishSettings is called AND the state is read
    -- back and asserted true. A form that is not verified-published accepts no
    -- responses, silently.
    publish_verified_at timestamptz,
    response_watermark  text,
    last_polled_at      timestamptz
);

comment on column session_form.response_watermark is
    'RFC3339 UTC timestamp of the newest response consumed. Advanced only after '
    'a complete successful pull, so a mid-pull failure re-reads rather than skips.';

create index if not exists session_form_session_idx on session_form (session_id);
