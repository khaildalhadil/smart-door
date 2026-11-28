-- Chat with the door from the dashboard, like on WhatsApp (admins only).
--
-- Supabase -> SQL Editor -> paste this file -> Run.  Safe to run again.
-- (The same lines are section 10 of schema.sql, for a fresh project.)
--
-- The dashboard inserts a message (sender 'admin', status 'pending'); the Pi
-- (app/webchat.py, secret key) claims it, answers it like a WhatsApp message
-- -- keywords first, OpenAI for free text -- and inserts the reply (sender
-- 'door').  Each admin sees only their own conversation; members and
-- everyone else see nothing and can write nothing.

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
