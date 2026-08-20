begin;

create extension if not exists pgtap with schema extensions;

select extensions.plan(9);

insert into auth.users (id)
values
  ('60000000-0000-4000-8000-000000000001'),
  ('60000000-0000-4000-8000-000000000002'),
  ('60000000-0000-4000-8000-000000000003'),
  ('60000000-0000-4000-8000-000000000004');

update public.profiles
set membership_status = 'active'::public.membership_status
where id = '60000000-0000-4000-8000-000000000001';

update public.profiles
set membership_status = 'withdrawal_pending'::public.membership_status
where id in (
  '60000000-0000-4000-8000-000000000002',
  '60000000-0000-4000-8000-000000000003'
);

update public.profiles
set membership_status = 'suspended'::public.membership_status
where id = '60000000-0000-4000-8000-000000000004';

insert into public.terms_acceptances (
  user_id,
  document_type,
  version,
  accepted_at
)
values
  (
    '60000000-0000-4000-8000-000000000001',
    'terms',
    '2026-08-04-draft',
    timestamptz '2026-08-20 00:00:00+00'
  ),
  (
    '60000000-0000-4000-8000-000000000002',
    'terms',
    '2026-08-04-draft',
    timestamptz '2026-08-20 00:00:00+00'
  );

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '60000000-0000-4000-8000-000000000001',
  true
);

set local role authenticated;

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.profiles
    where id = '60000000-0000-4000-8000-000000000001'
  ),
  1,
  'active member can still read their own profile'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.terms_acceptances
    where user_id = '60000000-0000-4000-8000-000000000001'
  ),
  1,
  'active member can still read their own terms acceptance'
);

set local role postgres;

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '60000000-0000-4000-8000-000000000002',
  true
);

set local role authenticated;

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.profiles
    where id = '60000000-0000-4000-8000-000000000002'
  ),
  0,
  'withdrawal_pending member cannot read their own profile'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.terms_acceptances
    where user_id = '60000000-0000-4000-8000-000000000002'
  ),
  0,
  'withdrawal_pending member cannot read their own terms acceptance'
);

set local role postgres;

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '60000000-0000-4000-8000-000000000003',
  true
);

set local role authenticated;

select extensions.throws_ok(
  $$
    select *
    from public.accept_current_terms()
  $$,
  '42501',
  'account withdrawal is pending',
  'withdrawal_pending member cannot accept terms'
);

set local role postgres;

select extensions.is(
  (
    select membership_status::text
    from public.profiles
    where id = '60000000-0000-4000-8000-000000000003'
  ),
  'withdrawal_pending',
  'rejected terms acceptance keeps withdrawal_pending status'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.terms_acceptances
    where user_id = '60000000-0000-4000-8000-000000000003'
  ),
  0,
  'rejected terms acceptance writes no acceptance history'
);

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '60000000-0000-4000-8000-000000000004',
  true
);

set local role authenticated;

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.profiles
    where id = '60000000-0000-4000-8000-000000000004'
  ),
  1,
  'suspended member retains existing self-profile visibility'
);

set local role postgres;

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '60000000-0000-4000-8000-000000000003',
  true
);

update public.profiles
set membership_status = 'pending_terms'::public.membership_status
where id = '60000000-0000-4000-8000-000000000003';

set local role authenticated;

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.profiles
    where id = '60000000-0000-4000-8000-000000000003'
  ),
  1,
  'pending_terms member retains self-profile visibility'
);

set local role postgres;

select * from extensions.finish();

rollback;
