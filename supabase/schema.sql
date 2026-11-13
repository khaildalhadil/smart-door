-- Smart Lock: Supabase schema for the web dashboard.
--
-- شغّله مرة واحدة: لوحة Supabase ← SQL Editor ← New query ← الصق الملف كله ← Run.
-- قبل التشغيل غيّر البريد في آخر سطر إلى بريدك. الخطوات كاملة في README.md.
--
-- Run it once in the Supabase dashboard (SQL Editor).  It is safe to run
-- again: nothing is dropped, existing rows stay.
--
--   door_events   one row per entry, refusal, doorbell ring, alarm ...
--   door_status   one row: the door right now (heartbeat every minute)
--   door-photos   private bucket with the camera pictures
--   door_viewers  the e-mail addresses allowed to open the dashboard
--
-- Who can do what:
--   * the Pi writes with the SECRET key (.env on the Pi), which bypasses
--     row-level security;
--   * the dashboard reads with the PUBLISHABLE key plus a signed-in user, and
--     row-level security lets a user read only if their e-mail is listed in
--     door_viewers.  Nobody can write from the dashboard.

-- 1) Who may read ------------------------------------------------------------
create table if not exists public.door_viewers (
  email     text primary key,
  added_at  timestamptz not null default now()
);
alter table public.door_viewers enable row level security;
-- No policy on purpose: only the SQL editor and the secret key see this list.

create or replace function public.is_door_viewer()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.door_viewers v
    where lower(v.email) = lower(coalesce((select auth.jwt() ->> 'email'), ''))
  );
$$;

revoke all on function public.is_door_viewer() from public, anon;
grant execute on function public.is_door_viewer() to authenticated, service_role;

-- 2) Events ------------------------------------------------------------------
create table if not exists public.door_events (
  uid         text primary key,              -- made on the Pi; resending is harmless
  ts          timestamptz not null,          -- when it happened
  category    text not null,                 -- entry, denied, doorbell, alarm, remote, admin, system
  method      text not null default '',      -- keypad, face, fingerprint, temp_code, whatsapp, doorbell ...
  person      text not null default '',      -- user name, when known
  event       text not null default '',      -- event-log name: lock.unlock, keypad.deny ...
  detail      text not null default '',
  level       text not null default 'INFO',
  photo       text not null default '',      -- path inside the door-photos bucket, '' = none
  created_at  timestamptz not null default now()
);
create index if not exists door_events_ts on public.door_events (ts desc);
create index if not exists door_events_category_ts on public.door_events (category, ts desc);

alter table public.door_events enable row level security;
drop policy if exists "door viewers read events" on public.door_events;
create policy "door viewers read events" on public.door_events
  for select to authenticated
  using ((select public.is_door_viewer()));

-- 3) Live status ---------------------------------------------------------------
create table if not exists public.door_status (
  id          text primary key,              -- always 'door'
  updated_at  timestamptz not null default now(),
  lock        text not null default '',      -- locked / unlocked
  alarm       text not null default '',      -- '', fire, smoke, fire+smoke
  camera      boolean,
  bot         boolean,                       -- is the WhatsApp bot collecting alerts
  info        jsonb not null default '{}'::jsonb
);

alter table public.door_status enable row level security;
drop policy if exists "door viewers read status" on public.door_status;
create policy "door viewers read status" on public.door_status
  for select to authenticated
  using ((select public.is_door_viewer()));

-- 4) Privileges ----------------------------------------------------------------
-- Explicit, so the tables work whether or not the project exposes new tables
-- to the Data API automatically.
revoke all on public.door_events, public.door_status, public.door_viewers from anon;
revoke all on public.door_viewers from authenticated;
grant select on public.door_events, public.door_status to authenticated;
grant select, insert, update, delete
  on public.door_events, public.door_status, public.door_viewers to service_role;

-- 5) Pictures ------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('door-photos', 'door-photos', false, 2097152, array['image/jpeg'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "door viewers read photos" on storage.objects;
create policy "door viewers read photos" on storage.objects
  for select to authenticated
  using (bucket_id = 'door-photos' and (select public.is_door_viewer()));

-- 6) Live updates in the dashboard ---------------------------------------------
do $$
begin
  alter publication supabase_realtime add table public.door_events;
exception when duplicate_object then null;
end $$;

do $$
begin
  alter publication supabase_realtime add table public.door_status;
exception when duplicate_object then null;
end $$;

-- 7) YOUR e-mail: the account you will sign in to the dashboard with -----------
--    غيّر البريد هنا إلى بريدك (نفس الحساب الذي تنشئه في Authentication ← Users).
insert into public.door_viewers (email)
values ('you@example.com')
on conflict (email) do nothing;
