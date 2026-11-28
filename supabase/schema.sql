-- Smart Lock: Supabase schema for the web dashboard.
--
-- شغّله مرة واحدة: لوحة Supabase ← SQL Editor ← New query ← الصق الملف كله ← Run.
-- بعدها اجعل حسابك مديرًا: القسم 9 في آخر الملف. الخطوات كاملة في README.md.
--
-- Run it once in the Supabase dashboard (SQL Editor).  It is safe to run
-- again: existing rows stay.
--
--   door_events    one row per entry, refusal, doorbell ring, alarm ...
--   door_status    one row: the door right now (heartbeat every minute)
--   door-photos    private bucket with the camera pictures
--   door_members   who may use the dashboard, and with which role
--   door_chat      admins' messages to the door and its replies (section 10)
--   door_audit     who changed which account, and when
--
-- Two roles.  The rules live HERE, in row-level security and in the
-- functions below; the page only hides what the database would refuse anyway.
--
--                                              admin   member
--   live door status                            yes     yes
--   numbers, charts, written analysis           yes     yes
--   entries, refusals (*), doorbell, alarms     yes     yes
--   camera pictures                             yes     no
--   WhatsApp commands, panel admin, system      yes     no
--   accounts: list, add, role, password,        yes     no
--     delete, and the change log
--   own password                                yes     yes
--
--   (*) members see refused door codes, faces, fingers and cards; not wrong panel
--       PINs, nor messages from unknown WhatsApp numbers.
--
-- A signed-in account that is not in door_members sees nothing at all, so an
-- account someone made for themselves is harmless.
--
-- Who writes:
--   * the Pi writes events, status and pictures with the SECRET key (.env on
--     the Pi), which bypasses row-level security;
--   * accounts are created and deleted by the Edge Function door-accounts
--     (supabase/functions/door-accounts), which first asks door_me() whether
--     the caller is an admin, then uses its own secret key;
--   * roles change only through door_set_role() / door_remove_member(),
--     which check the caller themselves.


-- 1) Members and roles -------------------------------------------------------
create table if not exists public.door_members (
  user_id     uuid primary key references auth.users (id) on delete cascade,
  role        text not null check (role in ('admin', 'member')),
  added_by    uuid references auth.users (id) on delete set null,
  added_at    timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
alter table public.door_members enable row level security;
-- No policy on purpose: read and changed only through the functions below.

-- The signed-in user's role: 'admin', 'member', or null (no access).
create or replace function public.door_role()
returns text
language sql
stable
security definer
set search_path = ''
as $$
  select m.role
  from public.door_members m
  where m.user_id = (select auth.uid());
$$;

-- Who am I: {"user_id", "email", "role"}; role is null without access.
create or replace function public.door_me()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_build_object('user_id', u.id, 'email', u.email, 'role', m.role)
  from auth.users u
  left join public.door_members m on m.user_id = u.id
  where u.id = (select auth.uid());
$$;


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
drop policy if exists "door viewers read events" on public.door_events;   -- earlier version
drop policy if exists "door members read events" on public.door_events;
create policy "door members read events" on public.door_events
  for select to authenticated
  using (
    case (select public.door_role())
      when 'admin' then true
      when 'member' then
        category in ('entry', 'doorbell', 'alarm')
        or (category = 'denied' and method in ('keypad', 'face', 'fingerprint', 'card'))
      else false
    end
  );


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
drop policy if exists "door viewers read status" on public.door_status;   -- earlier version
drop policy if exists "door members read status" on public.door_status;
create policy "door members read status" on public.door_status
  for select to authenticated
  using ((select public.door_role()) in ('admin', 'member'));


-- 4) Accounts (admins only) ----------------------------------------------------
create table if not exists public.door_audit (
  id            bigint generated always as identity primary key,
  ts            timestamptz not null default now(),
  actor         uuid,                        -- who did it
  actor_email   text not null default '',
  action        text not null,               -- member.add, member.role, member.remove, password.reset
  target        uuid,                        -- whose account
  target_email  text not null default '',
  old_role      text,
  new_role      text
);
create index if not exists door_audit_ts on public.door_audit (ts desc);

alter table public.door_audit enable row level security;
drop policy if exists "door admins read audit" on public.door_audit;
create policy "door admins read audit" on public.door_audit
  for select to authenticated
  using ((select public.door_role()) = 'admin');

-- Every account in this project, with its role (null = no access).
create or replace function public.door_list_members()
returns table (user_id uuid, email text, role text, added_at timestamptz,
               created_at timestamptz, last_sign_in_at timestamptz, is_me boolean)
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
  if (select public.door_role()) is distinct from 'admin' then
    raise exception 'not_admin' using errcode = '42501';
  end if;
  return query
    select u.id, u.email::text, m.role, m.added_at, u.created_at, u.last_sign_in_at,
           u.id = (select auth.uid())
    from auth.users u
    left join public.door_members m on m.user_id = u.id
    where u.deleted_at is null
    order by (m.role = 'admin') desc nulls last, lower(u.email::text);
end;
$$;

-- Give an account a role, or change it.  Not your own: an admin cannot lock
-- themselves out, so there is always at least one admin.
create or replace function public.door_set_role(p_user uuid, p_role text)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_me     uuid := (select auth.uid());
  v_old    text;
  v_email  text;
begin
  -- One account change at a time, and the caller's role is read AFTER the
  -- lock: two admins demoting each other at the same moment cannot both win.
  perform pg_advisory_xact_lock(hashtext('public.door_members'));
  if (select public.door_role()) is distinct from 'admin' then
    raise exception 'not_admin' using errcode = '42501';
  end if;
  if p_role is null or p_role not in ('admin', 'member') then
    raise exception 'bad_role' using errcode = '22023';
  end if;
  if p_user is null or p_user = v_me then
    raise exception 'self_change' using errcode = '42501';
  end if;
  select u.email into v_email from auth.users u where u.id = p_user and u.deleted_at is null;
  if not found then
    raise exception 'no_user' using errcode = 'P0002';
  end if;
  select m.role into v_old from public.door_members m where m.user_id = p_user;
  if v_old is not distinct from p_role then
    return;
  end if;
  insert into public.door_members (user_id, role, added_by)
  values (p_user, p_role, v_me)
  on conflict (user_id) do update set role = excluded.role, updated_at = now();
  insert into public.door_audit (actor, actor_email, action, target, target_email, old_role, new_role)
  values (v_me, coalesce((select u.email from auth.users u where u.id = v_me), ''),
          case when v_old is null then 'member.add' else 'member.role' end,
          p_user, coalesce(v_email, ''), v_old, p_role);
end;
$$;

-- Take an account's access away (the Edge Function then deletes the login).
create or replace function public.door_remove_member(p_user uuid)
returns void
language plpgsql
volatile
security definer
set search_path = ''
as $$
declare
  v_me     uuid := (select auth.uid());
  v_old    text;
  v_email  text;
begin
  perform pg_advisory_xact_lock(hashtext('public.door_members'));
  if (select public.door_role()) is distinct from 'admin' then
    raise exception 'not_admin' using errcode = '42501';
  end if;
  if p_user is null or p_user = v_me then
    raise exception 'self_change' using errcode = '42501';
  end if;
  select u.email into v_email from auth.users u where u.id = p_user;
  delete from public.door_members m where m.user_id = p_user returning m.role into v_old;
  if v_old is null then
    return;                                  -- had no access: nothing to log
  end if;
  insert into public.door_audit (actor, actor_email, action, target, target_email, old_role, new_role)
  values (v_me, coalesce((select u.email from auth.users u where u.id = v_me), ''),
          'member.remove', p_user, coalesce(v_email, ''), v_old, null);
end;
$$;


-- 5) Privileges ----------------------------------------------------------------
-- Explicit, so the tables work whether or not the project exposes new tables
-- to the Data API automatically, and whatever default grants it has.
revoke all on public.door_events, public.door_status, public.door_members, public.door_audit
  from anon, authenticated;
revoke all on sequence public.door_audit_id_seq from anon, authenticated;
grant select on public.door_events, public.door_status, public.door_audit to authenticated;
grant select, insert, update, delete
  on public.door_events, public.door_status, public.door_members to service_role;
grant select, insert on public.door_audit to service_role;
grant usage on sequence public.door_audit_id_seq to service_role;

revoke all on function public.door_role() from public, anon;
revoke all on function public.door_me() from public, anon;
revoke all on function public.door_list_members() from public, anon;
revoke all on function public.door_set_role(uuid, text) from public, anon;
revoke all on function public.door_remove_member(uuid) from public, anon;
grant execute on function public.door_role() to authenticated, service_role;
grant execute on function public.door_me() to authenticated, service_role;
grant execute on function public.door_list_members() to authenticated;
grant execute on function public.door_set_role(uuid, text) to authenticated;
grant execute on function public.door_remove_member(uuid) to authenticated;


-- 6) Pictures (admins only) ------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('door-photos', 'door-photos', false, 2097152, array['image/jpeg'])
on conflict (id) do update
  set public = false,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "door viewers read photos" on storage.objects;      -- earlier version
drop policy if exists "door admins read photos" on storage.objects;
create policy "door admins read photos" on storage.objects
  for select to authenticated
  using (bucket_id = 'door-photos' and (select public.door_role()) = 'admin');


-- 7) Live updates in the dashboard (they obey the same row-level security) -------
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


-- 8) Earlier version: one read-only e-mail list, replaced by door_members ---------
drop function if exists public.is_door_viewer();
drop table if exists public.door_viewers;


-- 9) YOU: the first admin --------------------------------------------------------
--    أنشئ حسابك أولًا من Authentication ← Users ← Add user (مع Auto Confirm)،
--    ثم غيّر البريد هنا إلى بريدك وشغّل هذا الجزء. بعدها تضيف الباقين من الصفحة.
--    Create your account first (Authentication -> Users -> Add user), then put
--    its e-mail here.  Everyone else is added from the dashboard.
insert into public.door_members (user_id, role)
select u.id, 'admin'
from auth.users u
where lower(u.email) = lower('you@example.com')
on conflict (user_id) do update set role = 'admin', updated_at = now();


-- 10) Chat with the door (admins only) ------------------------------------------
--     Same as supabase/chat.sql: the dashboard writes, the Pi answers.
create table if not exists public.door_chat (
  id          bigint generated always as identity primary key,
  created_at  timestamptz not null default now(),
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  sender      text not null check (sender in ('admin', 'door')),
  body        text not null default '' check (char_length(body) <= 4000),
  texts       jsonb not null default '{}'::jsonb,   -- the reply in Arabic and English
  photo       text not null default '',             -- path in the door-photos bucket
  intent      text not null default '',             -- open, status, photo, ai:... (log)
  status      text not null default 'pending'
              check (status in ('pending', 'working', 'done', 'stale', 'error')),
  reply_to    bigint references public.door_chat (id) on delete cascade
);
create index if not exists door_chat_pending on public.door_chat (id) where status = 'pending';
create index if not exists door_chat_mine on public.door_chat (user_id, id);
alter table public.door_chat enable row level security;

drop policy if exists "door admins read own chat" on public.door_chat;
create policy "door admins read own chat" on public.door_chat
  for select to authenticated
  using (user_id = (select auth.uid()) and (select public.door_role()) = 'admin');

-- Only a plain new message: the Pi alone writes replies, statuses and pictures.
drop policy if exists "door admins write to the door" on public.door_chat;
create policy "door admins write to the door" on public.door_chat
  for insert to authenticated
  with check (
    user_id = (select auth.uid())
    and (select public.door_role()) = 'admin'
    and sender = 'admin' and status = 'pending'
    and reply_to is null and photo = '' and intent = '' and texts = '{}'::jsonb
    and char_length(body) between 1 and 1000
    and created_at between now() - interval '1 minute' and now() + interval '1 minute'
  );

drop policy if exists "door admins clear own chat" on public.door_chat;
create policy "door admins clear own chat" on public.door_chat
  for delete to authenticated
  using (user_id = (select auth.uid()) and (select public.door_role()) = 'admin');

revoke all on public.door_chat from anon, authenticated;
grant select, insert, delete on public.door_chat to authenticated;
grant select, insert, update, delete on public.door_chat to service_role;
do $$
begin
  execute format('grant usage on sequence %s to authenticated, service_role',
                 pg_get_serial_sequence('public.door_chat', 'id'));
end $$;

-- Replies appear in the page as soon as the Pi writes them.
do $$
begin
  alter publication supabase_realtime add table public.door_chat;
exception when duplicate_object then null;
end $$;
