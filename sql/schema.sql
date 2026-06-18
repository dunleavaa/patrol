-- Sign-in/out system schema (Postgres / Supabase)
-- Run this once in the Supabase SQL Editor (Dashboard -> SQL Editor -> New query).
-- The W2H sync (w2h_load.py) fills people / positions / categories / shifts.
-- The app itself writes the attendance table.

create table if not exists people (
    helper_number text primary key,          -- stable W2H id (join key)
    name          text not null,
    first_name    text,
    last_name     text,
    email         text,                       -- used for the remote magic-link sign-in
    phone         text,
    cell          text,
    city          text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

create table if not exists positions (
    position_id text primary key,             -- stable W2H id
    name        text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

-- The category is the kiosk-vs-remote switch. The sync inserts new categories
-- with a suggested mode; an admin can then change signin_mode and the sync will
-- NOT overwrite that choice (see w2h_load.py). Batawa categories arrive as
-- 'review' until you set them to 'kiosk'.
create table if not exists categories (
    cat         text primary key,             -- W2H short code, e.g. 'SE'
    name        text,                          -- e.g. 'Special Events'
    signin_mode text not null default 'review'
                check (signin_mode in ('kiosk', 'remote', 'ignore', 'review')),
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create table if not exists shifts (
    shift_id       text primary key,          -- stable W2H id (upsert key)
    schedule_id    text,
    helper_number  text references people(helper_number),
    position_id    text references positions(position_id),
    cat            text references categories(cat),
    description    text,                       -- event / location name
    shift_date     date,
    start_ts       timestamp,                  -- local (Eastern) wall-clock time
    end_ts         timestamp,
    duration_hours numeric,
    last_seen_at   timestamptz not null default now(),  -- bumped each sync
    cancelled      boolean not null default false,      -- set if it vanishes from W2H
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

-- The app's own data: who actually signed in/out. "Currently signed in" is just
-- the rows where signed_out_at is null.
create table if not exists attendance (
    id             bigint generated always as identity primary key,
    shift_id       text references shifts(shift_id),
    helper_number  text references people(helper_number),
    signed_in_at   timestamptz,
    signed_out_at  timestamptz,
    method         text check (method in ('kiosk', 'remote')),
    position_id    text references positions(position_id),  -- role signed in as (may differ from schedule)
    shift_label    text,                            -- shift block signed in for (may differ from schedule)
    eligible_hours numeric,                          -- creditable hours for this record
    hours_recorded boolean not null default false,   -- have these hours been logged/submitted?
    auto_closed    boolean not null default false,   -- true if the sign-out was set by the end-of-day job, not the person
    created_at     timestamptz not null default now()
);

-- A record of each sync run, so a failed pull is visible rather than silent.
create table if not exists sync_runs (
    id           bigint generated always as identity primary key,
    started_at   timestamptz not null default now(),
    people_count int,
    shift_count  int,
    ok           boolean,
    message      text
);

create index if not exists idx_shifts_date    on shifts(shift_date);
create index if not exists idx_shifts_helper  on shifts(helper_number);
create index if not exists idx_shifts_cat     on shifts(cat);
create index if not exists idx_attend_open    on attendance(shift_id) where signed_out_at is null;

-- NOTE: before the app's browser client touches these tables, enable Row Level
-- Security on them in Supabase and add policies. The loader connects as the
-- database owner, which bypasses RLS, so the sync keeps working regardless.
