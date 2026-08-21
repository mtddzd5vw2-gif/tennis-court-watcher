-- Phase 4 LINE account-link foundation.
-- LINE Login callbacks and Messaging API delivery are added in later migrations.

alter type public.notification_channel
  add value if not exists 'line';

create type public.line_account_link_status as enum (
  'active',
  'blocked',
  'unlinked',
  'delivery_failed'
);

create table public.line_account_links (
  user_id uuid primary key references auth.users(id) on delete cascade,
  line_user_id text not null,
  status public.line_account_link_status not null
    default 'active'::public.line_account_link_status,
  linked_at timestamptz not null default pg_catalog.now(),
  unlinked_at timestamptz,
  last_webhook_at timestamptz,
  created_at timestamptz not null default pg_catalog.now(),
  updated_at timestamptz not null default pg_catalog.now(),
  constraint line_account_links_line_user_id_key unique (line_user_id),
  constraint line_account_links_line_user_id_not_blank
    check (pg_catalog.btrim(line_user_id) <> ''),
  constraint line_account_links_line_user_id_length
    check (pg_catalog.char_length(line_user_id) <= 255),
  constraint line_account_links_unlinked_state_check
    check (
      (
        status = 'unlinked'::public.line_account_link_status
        and unlinked_at is not null
      )
      or (
        status <> 'unlinked'::public.line_account_link_status
        and unlinked_at is null
      )
    ),
  constraint line_account_links_unlinked_at_order
    check (unlinked_at is null or unlinked_at >= linked_at)
);

create index line_account_links_status_idx
  on public.line_account_links (status);

create table public.line_link_sessions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  state_hash bytea not null,
  nonce_hash bytea not null,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default pg_catalog.now(),
  constraint line_link_sessions_state_hash_key unique (state_hash),
  constraint line_link_sessions_nonce_hash_key unique (nonce_hash),
  constraint line_link_sessions_state_hash_length
    check (pg_catalog.octet_length(state_hash) = 32),
  constraint line_link_sessions_nonce_hash_length
    check (pg_catalog.octet_length(nonce_hash) = 32),
  constraint line_link_sessions_expiry_window
    check (
      expires_at > created_at
      and expires_at <= created_at + interval '10 minutes'
    ),
  constraint line_link_sessions_consumed_at_order
    check (
      consumed_at is null
      or (
        consumed_at >= created_at
        and consumed_at <= expires_at
      )
    )
);

create index line_link_sessions_user_created_at_idx
  on public.line_link_sessions (user_id, created_at desc);

create index line_link_sessions_pending_expiry_idx
  on public.line_link_sessions (expires_at)
  where consumed_at is null;

create function public.set_line_account_link_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at := pg_catalog.now();
  return new;
end;
$$;

create trigger set_line_account_links_updated_at
before update on public.line_account_links
for each row
execute function public.set_line_account_link_updated_at();

create function public.get_my_line_link_status()
returns table (
  is_linked boolean,
  link_status text,
  linked_at timestamptz,
  last_webhook_at timestamptz
)
language plpgsql
security invoker
stable
set search_path = ''
as $$
declare
  current_user_id uuid := auth.uid();
begin
  if current_user_id is null then
    raise exception 'authentication required'
      using errcode = '42501';
  end if;

  if not exists (
    select 1
    from public.profiles as profile
    where profile.id = current_user_id
      and profile.membership_status =
        'active'::public.membership_status
  ) then
    raise exception 'active membership required'
      using errcode = '42501';
  end if;

  return query
  select
    link.status <> 'unlinked'::public.line_account_link_status,
    link.status::pg_catalog.text,
    link.linked_at,
    link.last_webhook_at
  from public.line_account_links as link;
end;
$$;

alter table public.line_account_links enable row level security;
alter table public.line_link_sessions enable row level security;

create policy line_account_links_select_own_active
on public.line_account_links
for select
to authenticated
using (
  (select auth.uid()) is not null
  and (select auth.uid()) = user_id
  and exists (
    select 1
    from public.profiles as profile
    where profile.id = (select auth.uid())
      and profile.membership_status =
        'active'::public.membership_status
  )
);

revoke all privileges on table
  public.line_account_links,
  public.line_link_sessions
from public, anon, authenticated, service_role;

grant select, insert, update, delete on table
  public.line_account_links,
  public.line_link_sessions
to service_role;

grant select (status, linked_at, last_webhook_at)
on table public.line_account_links
to authenticated;

revoke all on function public.set_line_account_link_updated_at()
from public, anon, authenticated, service_role;

revoke all on function public.get_my_line_link_status()
from public, anon, authenticated, service_role;

grant execute on function public.get_my_line_link_status()
to authenticated;

comment on table public.line_account_links is
  'Restricted mapping between one Auth member and one LINE account.';

comment on column public.line_account_links.line_user_id is
  'Sensitive opaque LINE identifier. Never expose through browser APIs or logs.';

comment on table public.line_link_sessions is
  'Short-lived SHA-256 state and nonce hashes for one-time LINE Login linking.';

comment on function public.get_my_line_link_status() is
  'Returns only the authenticated active member own safe LINE link status.';
