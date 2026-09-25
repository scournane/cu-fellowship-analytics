-- Two halves of the Slack bot were built side by side on the same base and
-- each declared `assignment` and a Zoom column on `session` in its own shape.
-- `create table if not exists` meant whichever migration ran first won the
-- table and the other's code failed at runtime. This makes the two shapes one.
--
-- Everything here is additive or a rename-by-copy, and every statement is
-- safe to run on a database that took either migration first.

-- ---------------------------------------------------------------------------
-- assignment: the union of both column sets
-- ---------------------------------------------------------------------------

alter table assignment add column if not exists kind         text not null default 'other';
alter table assignment add column if not exists link         text;
alter table assignment add column if not exists max_score    numeric(6, 2);
alter table assignment add column if not exists created_by   text;
alter table assignment add column if not exists description  text;
alter table assignment add column if not exists status       text not null default 'active';
alter table assignment add column if not exists due_at_local timestamp;

do $$
begin
    if not exists (select 1 from pg_constraint where conname = 'assignment_kind_check') then
        alter table assignment add constraint assignment_kind_check
            check (kind in ('solvathon', 'case_brief', 'other'));
    end if;
    if not exists (select 1 from pg_constraint where conname = 'assignment_status_check') then
        alter table assignment add constraint assignment_status_check
            check (status in ('active', 'cancelled'));
    end if;
end $$;

-- The bot creates assignments with a zone but no typed wall-clock time; the
-- console always has one. Keep it, but do not require it.
alter table assignment alter column due_at_local drop not null;
alter table assignment alter column timezone set default 'America/New_York';
update assignment
   set due_at_local = (due_at_utc at time zone timezone)
 where due_at_local is null;

-- `url` (console, CLI) and `link` (bot) were the same thing. `link` stays;
-- the code answers to both names.
do $$
begin
    if exists (
        select 1 from information_schema.columns
         where table_name = 'assignment' and column_name = 'url'
    ) then
        update assignment set link = coalesce(link, url);
        alter table assignment drop column url;
    end if;
end $$;

comment on column assignment.link is
    'Where to submit. Included in every reminder and digest. The console and '
    'CLI call this url; the row carries both names.';
comment on column assignment.status is
    'cancelled assignments are kept rather than deleted, and stop appearing in '
    'reminders and digests.';

-- ---------------------------------------------------------------------------
-- session: one Zoom column
-- ---------------------------------------------------------------------------

alter table "session" add column if not exists zoom_url text;

do $$
begin
    if exists (
        select 1 from information_schema.columns
         where table_name = 'session' and column_name = 'zoom_link'
    ) then
        update "session" set zoom_url = coalesce(zoom_url, zoom_link);
        alter table "session" drop column zoom_link;
    end if;
end $$;

comment on column "session".zoom_url is
    'Join URL. Set from the console, `cufa session edit`, or `/zoom` in Slack; '
    'included in every reminder, agenda and digest.';
