-- Promote the public legal documents from the development draft to the
-- 2026-08-21 formal terms. Preserve every historical acceptance and require
-- active members to explicitly accept the new current version.

do $$
begin
  if not exists (
    select 1
    from public.legal_document_versions as document
    where document.document_type = 'terms'
      and document.version = '2026-08-04-draft'
      and document.is_current = true
  ) then
    raise exception
      'Expected the 2026-08-04 draft to be the current terms version.';
  end if;

  if exists (
    select 1
    from public.legal_document_versions as document
    where document.document_type = 'terms'
      and document.version = '2026-08-21'
  ) then
    raise exception 'The 2026-08-21 terms version already exists.';
  end if;
end;
$$;

update public.legal_document_versions as document
set is_current = false
where document.document_type = 'terms'
  and document.is_current = true;

insert into public.legal_document_versions (
  document_type,
  version,
  effective_at,
  is_current
)
values (
  'terms',
  '2026-08-21',
  timestamptz '2026-08-21 00:00:00+09',
  true
);

update public.profiles as profile
set membership_status = 'pending_terms'::public.membership_status
where profile.membership_status = 'active'::public.membership_status
  and not exists (
    select 1
    from public.terms_acceptances as acceptance
    where acceptance.user_id = profile.id
      and acceptance.document_type = 'terms'
      and acceptance.version = '2026-08-21'
  );
