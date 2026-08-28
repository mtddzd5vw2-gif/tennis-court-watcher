-- Make LINE the primary member authentication method while keeping email
-- magic links as an optional backup identity.

update public.legal_document_versions as document
set is_current = false
where document.document_type = 'terms'
  and document.is_current;

insert into public.legal_document_versions (
  document_type,
  version,
  effective_at,
  is_current
)
values (
  'terms',
  '2026-08-28',
  timestamptz '2026-08-28 00:00:00+09',
  true
);

update public.profiles as profile
set membership_status = 'pending_terms'::public.membership_status
where profile.membership_status = 'active'::public.membership_status;

-- LINE-first members do not have an email address by default. Even after an
-- optional backup email is added, email delivery remains an explicit opt-in.
alter table public.notification_email_preferences
alter column is_enabled set default false;

create function public.sync_my_line_auth_identity()
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
  current_user_id uuid := auth.uid();
  current_line_user_id text;
begin
  if current_user_id is null then
    raise exception 'authentication required'
      using errcode = '42501';
  end if;

  if not exists (
    select 1
    from public.profiles as profile
    where profile.id = current_user_id
      and profile.membership_status = 'active'::public.membership_status
  ) then
    raise exception 'active membership required'
      using errcode = '42501';
  end if;

  select identity.provider_id
    into current_line_user_id
  from auth.identities as identity
  where identity.user_id = current_user_id
    and identity.provider = 'custom:line'
  order by identity.created_at desc
  limit 1;

  if current_line_user_id is null then
    return 'not_line_identity';
  end if;

  if current_line_user_id !~ '^U[0-9A-Fa-f]{32}$' then
    raise exception 'LINE identity is invalid.'
      using errcode = '22023';
  end if;

  if exists (
    select 1
    from public.line_account_links as link
    where link.user_id = current_user_id
  ) then
    return 'already_present';
  end if;

  insert into public.line_account_links (
    user_id,
    line_user_id,
    status
  )
  values (
    current_user_id,
    current_line_user_id,
    'active'::public.line_account_link_status
  );

  return 'linked';
exception
  when unique_violation then
    return 'conflict';
end;
$$;

revoke all on function public.sync_my_line_auth_identity()
from public, anon, authenticated, service_role;

grant execute on function public.sync_my_line_auth_identity()
to authenticated;

comment on function public.sync_my_line_auth_identity() is
  'Creates the authenticated active member LINE notification link from the trusted custom:line Auth identity without exposing the LINE identifier.';
