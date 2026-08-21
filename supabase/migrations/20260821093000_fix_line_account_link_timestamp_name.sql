-- Avoid the PostgreSQL CURRENT_TIME special value in LINE account-link RPCs.
-- The original local variable name was parsed as time with time zone inside
-- SQL expressions, which caused timestamptz comparisons to fail at runtime.

create or replace function public.create_line_link_session(
  p_user_id uuid,
  p_state_hash text,
  p_nonce_hash text,
  p_expires_at timestamptz
)
returns text
language plpgsql
security invoker
volatile
set search_path = ''
as $$
declare
  request_time timestamptz := pg_catalog.clock_timestamp();
  decoded_state_hash bytea;
  decoded_nonce_hash bytea;
begin
  if p_user_id is null
    or p_state_hash is null
    or p_state_hash !~ '^[0-9a-f]{64}$'
    or p_nonce_hash is null
    or p_nonce_hash !~ '^[0-9a-f]{64}$'
    or p_expires_at is null
    or p_expires_at <= request_time
    or p_expires_at > request_time + interval '10 minutes'
  then
    return 'invalid_request';
  end if;

  decoded_state_hash := pg_catalog.decode(p_state_hash, 'hex');
  decoded_nonce_hash := pg_catalog.decode(p_nonce_hash, 'hex');

  if not exists (
    select 1
    from public.profiles as profile
    where profile.id = p_user_id
      and profile.membership_status =
        'active'::public.membership_status
  ) then
    return 'inactive_member';
  end if;

  delete from public.line_link_sessions as session
  where session.user_id = p_user_id;

  begin
    insert into public.line_link_sessions (
      user_id,
      state_hash,
      nonce_hash,
      expires_at
    )
    values (
      p_user_id,
      decoded_state_hash,
      decoded_nonce_hash,
      p_expires_at
    );
  exception
    when unique_violation then
      return 'retry';
  end;

  return 'created';
end;
$$;

create or replace function public.complete_line_account_link(
  p_state_hash text,
  p_nonce_hash text,
  p_line_user_id text,
  p_is_friend boolean
)
returns text
language plpgsql
security invoker
volatile
set search_path = ''
as $$
declare
  request_time timestamptz := pg_catalog.clock_timestamp();
  session_user_id uuid;
  existing_line_user_id text;
  existing_status public.line_account_link_status;
  decoded_state_hash bytea;
  decoded_nonce_hash bytea;
  target_status public.line_account_link_status :=
    case
      when p_is_friend then 'active'::public.line_account_link_status
      else 'blocked'::public.line_account_link_status
    end;
begin
  if p_state_hash is null
    or p_state_hash !~ '^[0-9a-f]{64}$'
    or p_nonce_hash is null
    or p_nonce_hash !~ '^[0-9a-f]{64}$'
    or p_line_user_id is null
    or pg_catalog.btrim(p_line_user_id) = ''
    or pg_catalog.char_length(p_line_user_id) > 255
    or p_is_friend is null
  then
    return 'invalid_request';
  end if;

  decoded_state_hash := pg_catalog.decode(p_state_hash, 'hex');
  decoded_nonce_hash := pg_catalog.decode(p_nonce_hash, 'hex');

  select session.user_id
  into session_user_id
  from public.line_link_sessions as session
  where session.state_hash = decoded_state_hash
    and session.nonce_hash = decoded_nonce_hash
    and session.consumed_at is null
    and session.expires_at >= request_time
  for update;

  if not found then
    return 'invalid_session';
  end if;

  update public.line_link_sessions as session
  set consumed_at = request_time
  where session.state_hash = decoded_state_hash;

  if not exists (
    select 1
    from public.profiles as profile
    where profile.id = session_user_id
      and profile.membership_status =
        'active'::public.membership_status
  ) then
    return 'inactive_member';
  end if;

  select link.line_user_id, link.status
  into existing_line_user_id, existing_status
  from public.line_account_links as link
  where link.user_id = session_user_id
  for update;

  if found
    and existing_status <> 'unlinked'::public.line_account_link_status
    and existing_line_user_id <> p_line_user_id
  then
    return 'member_conflict';
  end if;

  begin
    insert into public.line_account_links (
      user_id,
      line_user_id,
      status,
      linked_at,
      unlinked_at,
      last_webhook_at
    )
    values (
      session_user_id,
      p_line_user_id,
      target_status,
      request_time,
      null,
      null
    )
    on conflict (user_id) do update
    set line_user_id = excluded.line_user_id,
        status = excluded.status,
        linked_at = excluded.linked_at,
        unlinked_at = null,
        last_webhook_at = null;
  exception
    when unique_violation then
      return 'line_conflict';
  end;

  delete from public.line_link_sessions as session
  where session.user_id = session_user_id;

  if p_is_friend then
    return 'linked';
  end if;

  return 'friend_required';
end;
$$;

revoke all on function public.create_line_link_session(
  uuid,
  text,
  text,
  timestamptz
) from public, anon, authenticated, service_role;

revoke all on function public.complete_line_account_link(
  text,
  text,
  text,
  boolean
) from public, anon, authenticated, service_role;

grant execute on function public.create_line_link_session(
  uuid,
  text,
  text,
  timestamptz
) to service_role;

grant execute on function public.complete_line_account_link(
  text,
  text,
  text,
  boolean
) to service_role;
