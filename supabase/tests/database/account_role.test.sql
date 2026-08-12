begin;

create extension if not exists pgtap with schema extensions;

select extensions.plan(30);

select extensions.set_eq(
  $$
    select enum_value::text
    from pg_catalog.unnest(
      pg_catalog.enum_range(null::public.account_role)
    ) as enum_value
  $$,
  array['member', 'admin']::text[],
  'account_role contains exactly member and admin'
);

select extensions.is(
  (
    select column_info.udt_schema || '.' || column_info.udt_name
    from information_schema.columns as column_info
    where column_info.table_schema = 'public'
      and column_info.table_name = 'profiles'
      and column_info.column_name = 'account_role'
  ),
  'public.account_role',
  'profiles.account_role uses the account_role enum'
);

select extensions.is(
  (
    select column_info.is_nullable
    from information_schema.columns as column_info
    where column_info.table_schema = 'public'
      and column_info.table_name = 'profiles'
      and column_info.column_name = 'account_role'
  ),
  'NO',
  'profiles.account_role is not nullable'
);

select extensions.matches(
  (
    select column_info.column_default::text
    from information_schema.columns as column_info
    where column_info.table_schema = 'public'
      and column_info.table_name = 'profiles'
      and column_info.column_name = 'account_role'
  ),
  'member',
  'profiles.account_role defaults to member'
);

insert into auth.users (id)
values ('50000000-0000-4000-8000-000000000001');

select extensions.is(
  (
    select profile.account_role::text
    from public.profiles as profile
    where profile.id = '50000000-0000-4000-8000-000000000001'
  ),
  'member',
  'a profile created without an explicit account role is a member'
);

insert into auth.users (id)
values ('50000000-0000-4000-8000-000000000002');

select extensions.is(
  (
    select profile.account_role::text
    from public.profiles as profile
    where profile.id = '50000000-0000-4000-8000-000000000002'
  ),
  'member',
  'the new Auth user trigger creates a member profile'
);

select extensions.ok(
  pg_catalog.has_table_privilege(
    'authenticated',
    'public.profiles',
    'SELECT'
  ),
  'authenticated retains profile SELECT privilege'
);

select extensions.ok(
  not pg_catalog.has_table_privilege(
    'authenticated',
    'public.profiles',
    'UPDATE'
  ),
  'authenticated has no profile UPDATE privilege'
);

select extensions.ok(
  not pg_catalog.has_table_privilege(
    'anon',
    'public.profiles',
    'SELECT'
  ),
  'anon has no profile SELECT privilege'
);

select extensions.ok(
  not pg_catalog.has_table_privilege(
    'anon',
    'public.profiles',
    'UPDATE'
  ),
  'anon has no profile UPDATE privilege'
);

select extensions.ok(
  not pg_catalog.has_function_privilege(
    'authenticated',
    'public.set_account_role(uuid, public.account_role)',
    'EXECUTE'
  ),
  'authenticated cannot execute set_account_role'
);

select extensions.ok(
  not pg_catalog.has_function_privilege(
    'anon',
    'public.set_account_role(uuid, public.account_role)',
    'EXECUTE'
  ),
  'anon cannot execute set_account_role'
);

select extensions.ok(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.set_account_role(uuid, public.account_role)',
    'EXECUTE'
  ),
  'service_role can execute set_account_role'
);

update public.profiles
set
  membership_status = 'active'::public.membership_status,
  latest_terms_version = '2026-08-04-draft',
  latest_terms_accepted_at = timestamptz '2026-08-12 01:00:00+00'
where id = '50000000-0000-4000-8000-000000000001';

insert into public.terms_acceptances (
  user_id,
  document_type,
  version,
  accepted_at
)
values (
  '50000000-0000-4000-8000-000000000001',
  'terms',
  '2026-08-04-draft',
  timestamptz '2026-08-12 01:00:00+00'
);

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '50000000-0000-4000-8000-000000000001',
  true
);

set local role authenticated;

select extensions.is(
  (
    select profile.account_role::text
    from public.profiles as profile
    where profile.id = '50000000-0000-4000-8000-000000000001'
  ),
  'member',
  'authenticated can select their own account role'
);

select extensions.throws_ok(
  $$
    update public.profiles
    set account_role = 'admin'::public.account_role
    where id = '50000000-0000-4000-8000-000000000001'
  $$,
  '42501',
  'permission denied for table profiles',
  'authenticated cannot promote themselves directly'
);

select extensions.throws_ok(
  $$
    update public.profiles
    set account_role = 'admin'::public.account_role
    where id = '50000000-0000-4000-8000-000000000002'
  $$,
  '42501',
  'permission denied for table profiles',
  'authenticated cannot change another profile role directly'
);

select extensions.throws_ok(
  $$
    select public.set_account_role(
      '50000000-0000-4000-8000-000000000001',
      'admin'::public.account_role
    )
  $$,
  '42501',
  'permission denied for function set_account_role',
  'authenticated cannot execute the role change RPC'
);

set local role postgres;
set local role anon;

select extensions.throws_ok(
  $$
    select account_role
    from public.profiles
    where id = '50000000-0000-4000-8000-000000000001'
  $$,
  '42501',
  'permission denied for table profiles',
  'anon cannot retrieve account roles'
);

select extensions.throws_ok(
  $$
    update public.profiles
    set account_role = 'admin'::public.account_role
    where id = '50000000-0000-4000-8000-000000000001'
  $$,
  '42501',
  'permission denied for table profiles',
  'anon cannot change account roles directly'
);

select extensions.throws_ok(
  $$
    select public.set_account_role(
      '50000000-0000-4000-8000-000000000001',
      'admin'::public.account_role
    )
  $$,
  '42501',
  'permission denied for function set_account_role',
  'anon cannot execute the role change RPC'
);

set local role postgres;
set local role service_role;

select extensions.lives_ok(
  $$
    select public.set_account_role(
      '50000000-0000-4000-8000-000000000001',
      'admin'::public.account_role
    )
  $$,
  'service_role can promote a member to admin'
);

set local role postgres;

select extensions.is(
  (
    select profile.account_role::text
    from public.profiles as profile
    where profile.id = '50000000-0000-4000-8000-000000000001'
  ),
  'admin',
  'service_role promotion persists the admin role'
);

select extensions.is(
  (
    select profile.membership_status::text
    from public.profiles as profile
    where profile.id = '50000000-0000-4000-8000-000000000001'
  ),
  'active',
  'role promotion does not change membership status'
);

select extensions.is(
  (
    select
      profile.latest_terms_version
      || '|' || profile.latest_terms_accepted_at::text
    from public.profiles as profile
    where profile.id = '50000000-0000-4000-8000-000000000001'
  ),
  '2026-08-04-draft|2026-08-12 01:00:00+00',
  'role promotion does not change profile terms acceptance'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.terms_acceptances as acceptance
    where acceptance.user_id = '50000000-0000-4000-8000-000000000001'
  ),
  1,
  'role promotion does not change terms acceptance history'
);

select extensions.is(
  (
    select profile.account_role::text
    from public.profiles as profile
    where profile.id = '50000000-0000-4000-8000-000000000002'
  ),
  'member',
  'role promotion does not affect another profile'
);

set local role service_role;

select extensions.lives_ok(
  $$
    select public.set_account_role(
      '50000000-0000-4000-8000-000000000001',
      'member'::public.account_role
    )
  $$,
  'service_role can demote an admin to member'
);

set local role postgres;

select extensions.is(
  (
    select profile.account_role::text
    from public.profiles as profile
    where profile.id = '50000000-0000-4000-8000-000000000001'
  ),
  'member',
  'service_role demotion persists the member role'
);

select extensions.is(
  (
    select profile.membership_status::text
    from public.profiles as profile
    where profile.id = '50000000-0000-4000-8000-000000000001'
  ),
  'active',
  'role demotion does not change membership status'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.terms_acceptances as acceptance
    where acceptance.user_id = '50000000-0000-4000-8000-000000000001'
      and acceptance.version = '2026-08-04-draft'
      and acceptance.accepted_at =
        timestamptz '2026-08-12 01:00:00+00'
  ),
  1,
  'role demotion leaves terms acceptance unchanged'
);

select extensions.finish();

rollback;
