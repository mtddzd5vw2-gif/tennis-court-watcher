-- Phase 3.5b per-user email unsubscribe tokens and safe re-enable rotation.
-- Raw tokens are private and are exposed only to trusted service-role RPCs.

begin;

create table public.notification_email_unsubscribe_tokens (
  user_id uuid primary key references auth.users(id) on delete cascade,
  token uuid not null unique default gen_random_uuid(),
  created_at timestamptz not null default now(),
  rotated_at timestamptz not null default now()
);

alter table public.notification_email_unsubscribe_tokens
  enable row level security;

revoke all privileges on table
  public.notification_email_unsubscribe_tokens
from public, anon, authenticated, service_role;

create function public.create_email_unsubscribe_token_for_preference()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.notification_email_unsubscribe_tokens (user_id)
  values (new.user_id)
  on conflict (user_id) do nothing;

  return new;
end;
$$;

create trigger create_email_unsubscribe_token_after_preference_insert
after insert on public.notification_email_preferences
for each row
execute function public.create_email_unsubscribe_token_for_preference();

insert into public.notification_email_unsubscribe_tokens (user_id)
select preference.user_id
from public.notification_email_preferences as preference
on conflict (user_id) do nothing;

create function public.get_email_unsubscribe_token_for_message(
  p_message_id uuid
)
returns uuid
language sql
stable
security definer
set search_path = ''
as $$
  select unsubscribe_token.token
  from public.notification_messages as message
  inner join public.notification_email_unsubscribe_tokens as unsubscribe_token
    on unsubscribe_token.user_id = message.user_id
  where message.id = p_message_id
    and message.channel = 'email'::public.notification_channel;
$$;

create function public.email_unsubscribe_token_is_valid(
  p_token uuid
)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from public.notification_email_unsubscribe_tokens as unsubscribe_token
    where unsubscribe_token.token = p_token
  );
$$;

create function public.unsubscribe_email_notifications_by_token(
  p_token uuid
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.notification_email_preferences as preference
  set
    is_enabled = false,
    disabled_at = pg_catalog.now()
  from public.notification_email_unsubscribe_tokens as unsubscribe_token
  where unsubscribe_token.token = p_token
    and preference.user_id = unsubscribe_token.user_id
    and preference.is_enabled = true
    and preference.disabled_reason is null;

  -- Valid, already disabled, and unknown tokens intentionally have the same
  -- externally observable result.
  return pg_catalog.jsonb_build_object('outcome', 'processed');
end;
$$;

create function public.rotate_email_unsubscribe_token_on_reenable()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  if old.is_enabled = false
    and new.is_enabled = true
    and new.disabled_reason is null
  then
    if exists (
      select 1
      from public.notification_messages as message
      where message.user_id = new.user_id
        and message.channel = 'email'::public.notification_channel
        and message.status = any (
          array[
            'processing'::public.notification_message_status,
            'retry_wait'::public.notification_message_status
          ]
        )
    ) then
      raise exception using
        errcode = '55000',
        message = 'Email notification re-enable is temporarily unavailable.';
    end if;

    update public.notification_email_unsubscribe_tokens as unsubscribe_token
    set
      token = pg_catalog.gen_random_uuid(),
      rotated_at = pg_catalog.now()
    where unsubscribe_token.user_id = new.user_id;
  end if;

  return new;
end;
$$;

create trigger rotate_email_unsubscribe_token_after_reenable
after update of is_enabled on public.notification_email_preferences
for each row
execute function public.rotate_email_unsubscribe_token_on_reenable();

revoke all on function
  public.create_email_unsubscribe_token_for_preference()
from public, anon, authenticated, service_role;

revoke all on function
  public.rotate_email_unsubscribe_token_on_reenable()
from public, anon, authenticated, service_role;

revoke all on function
  public.get_email_unsubscribe_token_for_message(uuid)
from public, anon, authenticated, service_role;

grant execute on function
  public.get_email_unsubscribe_token_for_message(uuid)
to service_role;

revoke all on function
  public.email_unsubscribe_token_is_valid(uuid)
from public, anon, authenticated, service_role;

grant execute on function
  public.email_unsubscribe_token_is_valid(uuid)
to service_role;

revoke all on function
  public.unsubscribe_email_notifications_by_token(uuid)
from public, anon, authenticated, service_role;

grant execute on function
  public.unsubscribe_email_notifications_by_token(uuid)
to service_role;

comment on table public.notification_email_unsubscribe_tokens is
  'Private per-user email unsubscribe capability tokens; no direct role access.';

comment on function public.get_email_unsubscribe_token_for_message(uuid) is
  'Returns the private unsubscribe token for one claimed email message.';

comment on function public.email_unsubscribe_token_is_valid(uuid) is
  'Validates an unsubscribe capability for the public confirmation page.';

comment on function public.unsubscribe_email_notifications_by_token(uuid) is
  'Idempotently disables email notifications with a generic non-enumerating result.';

commit;
