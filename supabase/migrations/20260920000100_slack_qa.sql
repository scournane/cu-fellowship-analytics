-- Q&A channels: the questions fellows ask, the answers they get, and what the
-- bot does with them — a per-session summary for the teacher, and a pointer
-- when a question has been asked (and answered) before.
--
-- This is the ONE place Slack message text is stored by default, and only for
-- channels the data owner has named in CUFA_SLACK_QA_CHANNELS. The reasoning
-- (ADR-032) is that a Q&A channel is different in kind from #general: a
-- question is posted so that it can be found and answered, and the whole value
-- of an answer is that the next person with the same question can be pointed
-- at it. Neither is possible without the words. Everywhere else ADR-031 holds
-- and `slack_event.text` stays NULL.
--
-- These are WORKING tables, not observations. `slack_event` is the immutable
-- record that a message happened; these rows follow the message — an edit
-- updates the text, a deletion stamps deleted_at — because a pointer to a
-- deleted answer, or a summary of a question that was retracted, is wrong.

-- ---------------------------------------------------------------------------
-- slack_qa_question — one row per top-level message in a Q&A channel.
-- ---------------------------------------------------------------------------

create table if not exists slack_qa_question (
    question_id     uuid        primary key default gen_random_uuid(),
    team_id         text        not null references slack_workspace (team_id) on delete cascade,
    channel_id      text        not null,
    message_ts      text        not null,
    -- The workspace-scoped id, as on slack_event. Never an email: nothing here
    -- joins to the roster, and nothing here is about who asked.
    slack_user_id   text        not null,
    text            text        not null,
    -- Slack markup stripped, casefolded, punctuation removed. What the lexical
    -- matcher compares.
    normalized_text text        not null,
    asked_at_utc    timestamptz not null,
    edited_at_utc   timestamptz,
    deleted_at_utc  timestamptz,
    -- A ✅ reaction on the question itself.
    resolved        boolean     not null default false,
    permalink       text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (team_id, channel_id, message_ts)
);

create index if not exists slack_qa_question_team_time_idx
    on slack_qa_question (team_id, asked_at_utc);

comment on table slack_qa_question is
    'A top-level message in a designated Q&A channel. Text IS stored here — '
    'see ADR-032 — because a question exists to be found again. Stores the '
    'Slack user id, never an email; nothing about a question is attributed to '
    'a fellow anywhere in the system.';

-- ---------------------------------------------------------------------------
-- slack_qa_answer — one row per thread reply on a question.
-- ---------------------------------------------------------------------------

create table if not exists slack_qa_answer (
    answer_id       uuid        primary key default gen_random_uuid(),
    question_id     uuid        not null references slack_qa_question (question_id) on delete cascade,
    team_id         text        not null,
    channel_id      text        not null,
    message_ts      text        not null,
    slack_user_id   text        not null,
    text            text        not null,
    answered_at_utc timestamptz not null,
    edited_at_utc   timestamptz,
    deleted_at_utc  timestamptz,
    -- A ✅ reaction on the reply: somebody marked this as THE answer.
    accepted        boolean     not null default false,
    permalink       text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (team_id, channel_id, message_ts)
);

create index if not exists slack_qa_answer_question_idx
    on slack_qa_answer (question_id, answered_at_utc);

comment on column slack_qa_answer.accepted is
    'True while a white_check_mark (or heavy_check_mark) reaction is on the '
    'reply. The pointer links an accepted answer directly; otherwise it links '
    'the thread. Nothing ranks or counts who accepted what.';

-- ---------------------------------------------------------------------------
-- slack_qa_pointer — what the bot said when a question had been asked before.
--
-- One per repeat question, so a retried delivery or a restart cannot post the
-- same pointer twice. posted_ts is NULL when chat.postMessage failed; the
-- error is kept so `cufa slack qa list` can show the ones that never landed.
-- ---------------------------------------------------------------------------

create table if not exists slack_qa_pointer (
    pointer_id          uuid        primary key default gen_random_uuid(),
    question_id         uuid        not null references slack_qa_question (question_id) on delete cascade,
    earlier_question_id uuid        not null references slack_qa_question (question_id) on delete cascade,
    method              text        not null check (method in ('lexical', 'gemini')),
    similarity          numeric(4, 3),
    reasoning           text,
    posted_ts           text,
    post_error          text,
    created_at          timestamptz not null default now(),
    unique (question_id)
);

comment on table slack_qa_pointer is
    'The bot noticed a question resembled an earlier, answered one and replied '
    'in the new thread with a link. method says which tier decided: lexical '
    '(token overlap, deterministic) or gemini (the model chose among lexical '
    'candidates). Anonymous strings only were sent either way.';

-- ---------------------------------------------------------------------------
-- slack_qa_summary — the per-session digest for the teacher.
--
-- Same shape as muddiest_theme: regenerating supersedes rather than
-- overwrites, so what the teacher read last time is still there.
-- ---------------------------------------------------------------------------

create table if not exists slack_qa_summary (
    summary_id           uuid        primary key default gen_random_uuid(),
    session_id           uuid        not null references "session" (session_id) on delete cascade,
    team_id              text        not null references slack_workspace (team_id) on delete cascade,
    questions_considered integer     not null,
    answered_count       integer     not null,
    -- Slack mrkdwn. The model's paragraph (or the plain digest when there is
    -- no model) followed by the deterministic list of questions with links.
    summary_text         text        not null,
    -- 'digest' when no model was involved.
    model                text        not null,
    prompt_version       text        not null,
    generated_at         timestamptz not null default now(),
    superseded_at        timestamptz,
    posted_channel_id    text,
    posted_ts            text
);

create index if not exists slack_qa_summary_session_idx
    on slack_qa_summary (session_id, generated_at desc);

create index if not exists slack_qa_summary_current_idx
    on slack_qa_summary (session_id) where superseded_at is null;

comment on table slack_qa_summary is
    'A summary of one session''s Q&A for the teacher. About the questions, '
    'never the askers: the model saw numbered anonymous strings, and the '
    'rendered text names nobody. Append-only; regenerating stamps '
    'superseded_at on the previous row.';

-- ---------------------------------------------------------------------------
-- Row Level Security. Same stub as every other table holding fellow-authored
-- content.
-- ---------------------------------------------------------------------------

alter table slack_qa_question enable row level security;
alter table slack_qa_answer   enable row level security;
alter table slack_qa_pointer  enable row level security;
alter table slack_qa_summary  enable row level security;

do $$
declare
    t text;
begin
    foreach t in array array['slack_qa_question', 'slack_qa_answer', 'slack_qa_pointer', 'slack_qa_summary'] loop
        if not exists (
            select 1 from pg_policies
            where schemaname = 'public' and tablename = t
              and policyname = t || '_read_todo'
        ) then
            execute format(
                'create policy %I on %I for select to authenticated using (false)',
                t || '_read_todo', t
            );  -- TODO(access): replace `false` with CU's rule.
        end if;
    end loop;
end
$$;
