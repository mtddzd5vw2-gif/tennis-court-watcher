begin;

create extension if not exists pgtap with schema extensions;

select extensions.plan(46);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_email_preferences as preference
    left join public.notification_email_unsubscribe_tokens as unsubscribe_token
      on unsubscribe_token.user_id = preference.user_id
    where unsubscribe_token.user_id is null
  ),
  0,
  'migration backfills a token for every existing email preference'
);

insert into auth.users (id)
values
  ('12000000-0000-4000-8000-000000000001'),
  ('12000000-0000-4000-8000-000000000002'),
  ('12000000-0000-4000-8000-000000000003'),
  ('12000000-0000-4000-8000-000000000004'),
  ('12000000-0000-4000-8000-000000000005'),
  ('12000000-0000-4000-8000-000000000006'),
  ('12000000-0000-4000-8000-000000000007');

update public.profiles
set membership_status = 'active'::public.membership_status
where id::text like '12000000-0000-4000-8000-%';

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_email_unsubscribe_tokens
    where user_id::text like '12000000-0000-4000-8000-%'
  ),
  7,
  'a new preference automatically creates one private token row'
);

select extensions.throws_ok(
  $$
    update public.notification_email_unsubscribe_tokens
    set token = (
      select token
      from public.notification_email_unsubscribe_tokens
      where user_id = '12000000-0000-4000-8000-000000000001'
    )
    where user_id = '12000000-0000-4000-8000-000000000002'
  $$,
  '23505',
  'duplicate key value violates unique constraint "notification_email_unsubscribe_tokens_token_key"',
  'unsubscribe capabilities are unique'
);

select extensions.ok(
  not exists (
    select 1
    from pg_catalog.pg_class as relation
    cross join lateral pg_catalog.aclexplode(
      coalesce(
        relation.relacl,
        pg_catalog.acldefault('r', relation.relowner)
      )
    ) as privilege
    where relation.oid =
      'public.notification_email_unsubscribe_tokens'::pg_catalog.regclass
      and privilege.grantee = 0
      and privilege.privilege_type = any (
        array['SELECT', 'INSERT', 'UPDATE', 'DELETE']::pg_catalog.text[]
      )
  ),
  'PUBLIC has no direct token table privileges'
);

select extensions.is(
  pg_catalog.has_table_privilege(
    'anon', 'public.notification_email_unsubscribe_tokens',
    'select,insert,update,delete'
  ),
  false,
  'anon has no direct token table privileges'
);

select extensions.is(
  pg_catalog.has_table_privilege(
    'authenticated', 'public.notification_email_unsubscribe_tokens',
    'select,insert,update,delete'
  ),
  false,
  'authenticated has no direct token table privileges'
);

select extensions.is(
  pg_catalog.has_table_privilege(
    'service_role', 'public.notification_email_unsubscribe_tokens',
    'select,insert,update,delete'
  ),
  false,
  'service_role has no direct token table privileges'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.get_email_unsubscribe_token_for_message(uuid)',
    'execute'
  ),
  true,
  'service_role can get the capability for a message'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.unsubscribe_email_notifications_by_token(uuid)',
    'execute'
  ),
  true,
  'service_role can execute unsubscribe'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.email_unsubscribe_token_is_valid(uuid)',
    'execute'
  ),
  true,
  'service_role can validate a confirmation-page capability'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'anon', 'public.get_email_unsubscribe_token_for_message(uuid)', 'execute'
  ),
  false,
  'anon cannot get message capabilities'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'authenticated',
    'public.get_email_unsubscribe_token_for_message(uuid)',
    'execute'
  ),
  false,
  'authenticated cannot get message capabilities'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'anon', 'public.unsubscribe_email_notifications_by_token(uuid)', 'execute'
  ),
  false,
  'anon cannot execute unsubscribe directly'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'authenticated',
    'public.unsubscribe_email_notifications_by_token(uuid)',
    'execute'
  ),
  false,
  'authenticated cannot execute unsubscribe directly'
);

set local role service_role;

select extensions.throws_ok(
  $$
    select token
    from public.notification_email_unsubscribe_tokens
    limit 1
  $$,
  '42501',
  'permission denied for table notification_email_unsubscribe_tokens',
  'service_role must use the security-definer RPC boundary'
);

set local role postgres;

update public.notification_email_preferences
set is_enabled = true
where user_id in (
  '12000000-0000-4000-8000-000000000001',
  '12000000-0000-4000-8000-000000000002',
  '12000000-0000-4000-8000-000000000003',
  '12000000-0000-4000-8000-000000000004',
  '12000000-0000-4000-8000-000000000005'
);

insert into public.notification_messages (id, user_id, channel)
values (
  '52000000-0000-4000-8000-000000000001',
  '12000000-0000-4000-8000-000000000001',
  'email'
);

select extensions.is(
  public.get_email_unsubscribe_token_for_message(
    '52000000-0000-4000-8000-000000000001'
  ),
  (
    select token
    from public.notification_email_unsubscribe_tokens
    where user_id = '12000000-0000-4000-8000-000000000001'
  ),
  'message token lookup returns only the email message owner capability'
);

select extensions.is(
  public.get_email_unsubscribe_token_for_message(
    '52000000-0000-4000-8000-000000000099'
  ),
  null,
  'message token lookup returns null for an unknown message'
);

create temporary table unsubscribe_test_snapshot (
  label text primary key,
  token uuid,
  disabled_reason text,
  disabled_at timestamptz
);

insert into unsubscribe_test_snapshot (label, token)
select 'valid', token
from public.notification_email_unsubscribe_tokens
where user_id = '12000000-0000-4000-8000-000000000001';

select extensions.is(
  public.unsubscribe_email_notifications_by_token(
    (select token from unsubscribe_test_snapshot where label = 'valid')
  ),
  '{"outcome": "processed"}'::jsonb,
  'a valid capability returns the generic processed outcome'
);

select extensions.ok(
  (
    select
      is_enabled = false
      and disabled_reason is null
      and disabled_at is not null
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000001'
  ),
  'valid unsubscribe records a manual OFF state without a provider reason'
);

update unsubscribe_test_snapshot
set disabled_at = (
  select disabled_at
  from public.notification_email_preferences
  where user_id = '12000000-0000-4000-8000-000000000001'
)
where label = 'valid';

select extensions.is(
  public.unsubscribe_email_notifications_by_token(
    (select token from unsubscribe_test_snapshot where label = 'valid')
  ),
  '{"outcome": "processed"}'::jsonb,
  'a repeated unsubscribe returns the same generic outcome'
);

select extensions.is(
  (
    select disabled_at
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000001'
  ),
  (select disabled_at from unsubscribe_test_snapshot where label = 'valid'),
  'a repeated unsubscribe does not change the manual OFF timestamp'
);

select extensions.is(
  public.unsubscribe_email_notifications_by_token(
    'ffffffff-ffff-4fff-8fff-ffffffffffff'
  ),
  '{"outcome": "processed"}'::jsonb,
  'an unknown capability has the same generic no-op outcome'
);

update public.notification_email_preferences
set is_enabled = false
where user_id = '12000000-0000-4000-8000-000000000002';

insert into unsubscribe_test_snapshot (label, token, disabled_at)
select 'manual', unsubscribe_token.token, preference.disabled_at
from public.notification_email_unsubscribe_tokens as unsubscribe_token
inner join public.notification_email_preferences as preference
  on preference.user_id = unsubscribe_token.user_id
where preference.user_id = '12000000-0000-4000-8000-000000000002';

select extensions.is(
  public.unsubscribe_email_notifications_by_token(
    (select token from unsubscribe_test_snapshot where label = 'manual')
  ),
  '{"outcome": "processed"}'::jsonb,
  'an already manually disabled preference is safe to unsubscribe'
);

select extensions.is(
  (
    select disabled_at
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000002'
  ),
  (select disabled_at from unsubscribe_test_snapshot where label = 'manual'),
  'unsubscribe preserves an existing manual OFF timestamp'
);

update public.notification_email_preferences
set
  is_enabled = false,
  disabled_reason = case user_id
    when '12000000-0000-4000-8000-000000000003' then 'resend_bounced'
    when '12000000-0000-4000-8000-000000000004' then 'resend_complained'
    when '12000000-0000-4000-8000-000000000005' then 'resend_suppressed'
  end,
  disabled_at = case user_id
    when '12000000-0000-4000-8000-000000000003'
      then timestamptz '2026-08-14 01:00:00+00'
    when '12000000-0000-4000-8000-000000000004'
      then timestamptz '2026-08-14 02:00:00+00'
    when '12000000-0000-4000-8000-000000000005'
      then timestamptz '2026-08-14 03:00:00+00'
  end
where user_id in (
  '12000000-0000-4000-8000-000000000003',
  '12000000-0000-4000-8000-000000000004',
  '12000000-0000-4000-8000-000000000005'
);

insert into unsubscribe_test_snapshot (label, token, disabled_reason, disabled_at)
select user_id::text, unsubscribe_token.token,
  preference.disabled_reason, preference.disabled_at
from public.notification_email_unsubscribe_tokens as unsubscribe_token
inner join public.notification_email_preferences as preference
  using (user_id)
where user_id in (
  '12000000-0000-4000-8000-000000000003',
  '12000000-0000-4000-8000-000000000004',
  '12000000-0000-4000-8000-000000000005'
);

select extensions.ok(
  (
    select pg_catalog.bool_and(
      public.unsubscribe_email_notifications_by_token(token)
        = '{"outcome": "processed"}'::jsonb
    )
    from unsubscribe_test_snapshot
    where label like '12000000-%'
  ),
  'all provider suppression capabilities return the generic outcome'
);

select extensions.ok(
  not exists (
    select 1
    from public.notification_email_preferences as preference
    inner join unsubscribe_test_snapshot as snapshot
      on snapshot.label = preference.user_id::text
    where preference.disabled_reason is distinct from snapshot.disabled_reason
      or preference.disabled_at is distinct from snapshot.disabled_at
      or preference.is_enabled <> false
  ),
  'unsubscribe never overwrites bounced, complained, or suppressed state'
);

insert into unsubscribe_test_snapshot (label, token)
select 'processing_reenable', token
from public.notification_email_unsubscribe_tokens
where user_id = '12000000-0000-4000-8000-000000000006';

insert into public.notification_messages (
  id, user_id, channel, status, attempt_count, locked_at, locked_until,
  provider_first_attempt_at, provider_payload_fingerprint
)
values (
  '52000000-0000-4000-8000-000000000002',
  '12000000-0000-4000-8000-000000000006',
  'email',
  'processing',
  1,
  timestamptz '2026-08-14 04:00:00+00',
  timestamptz '2026-08-14 04:05:00+00',
  timestamptz '2026-08-14 04:01:00+00',
  pg_catalog.repeat('a', 64)
);

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '12000000-0000-4000-8000-000000000006',
  true
);
set local role authenticated;

select extensions.throws_ok(
  $$
    update public.notification_email_preferences
    set is_enabled = true
    where user_id = '12000000-0000-4000-8000-000000000006'
  $$,
  '55000',
  'Email notification re-enable is temporarily unavailable.',
  'processing message blocks authenticated false-to-true re-enable'
);

set local role postgres;

select extensions.is(
  (
    select is_enabled
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000006'
  ),
  false,
  'processing-blocked re-enable leaves the preference OFF'
);

select extensions.is(
  (
    select token
    from public.notification_email_unsubscribe_tokens
    where user_id = '12000000-0000-4000-8000-000000000006'
  ),
  (
    select token
    from unsubscribe_test_snapshot
    where label = 'processing_reenable'
  ),
  'processing-blocked re-enable does not rotate the token'
);

select extensions.ok(
  (
    select
      status = 'processing'::public.notification_message_status
      and locked_at = timestamptz '2026-08-14 04:00:00+00'
      and locked_until = timestamptz '2026-08-14 04:05:00+00'
      and provider_first_attempt_at = timestamptz '2026-08-14 04:01:00+00'
      and provider_payload_fingerprint = pg_catalog.repeat('a', 64)
    from public.notification_messages
    where id = '52000000-0000-4000-8000-000000000002'
  ),
  'processing-blocked re-enable preserves status, lease, and fingerprint'
);

insert into unsubscribe_test_snapshot (label, token)
select 'retry_wait_reenable', token
from public.notification_email_unsubscribe_tokens
where user_id = '12000000-0000-4000-8000-000000000007';

insert into public.notification_messages (
  id, user_id, channel, status, attempt_count, next_attempt_at,
  provider_first_attempt_at, provider_payload_fingerprint
)
values (
  '52000000-0000-4000-8000-000000000003',
  '12000000-0000-4000-8000-000000000007',
  'email',
  'retry_wait',
  1,
  timestamptz '2026-08-14 05:00:00+00',
  timestamptz '2026-08-14 04:10:00+00',
  pg_catalog.repeat('b', 64)
);

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '12000000-0000-4000-8000-000000000007',
  true
);
set local role authenticated;

select extensions.throws_ok(
  $$
    update public.notification_email_preferences
    set is_enabled = true
    where user_id = '12000000-0000-4000-8000-000000000007'
  $$,
  '55000',
  'Email notification re-enable is temporarily unavailable.',
  'retry_wait message blocks authenticated false-to-true re-enable'
);

set local role postgres;

select extensions.is(
  (
    select is_enabled
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000007'
  ),
  false,
  'retry_wait-blocked re-enable leaves the preference OFF'
);

select extensions.is(
  (
    select token
    from public.notification_email_unsubscribe_tokens
    where user_id = '12000000-0000-4000-8000-000000000007'
  ),
  (
    select token
    from unsubscribe_test_snapshot
    where label = 'retry_wait_reenable'
  ),
  'retry_wait-blocked re-enable does not rotate the token'
);

select extensions.ok(
  (
    select
      status = 'retry_wait'::public.notification_message_status
      and next_attempt_at = timestamptz '2026-08-14 05:00:00+00'
      and provider_first_attempt_at = timestamptz '2026-08-14 04:10:00+00'
      and provider_payload_fingerprint = pg_catalog.repeat('b', 64)
    from public.notification_messages
    where id = '52000000-0000-4000-8000-000000000003'
  ),
  'retry_wait-blocked re-enable leaves the message unchanged'
);

insert into unsubscribe_test_snapshot (label, token)
select 'no_inflight_reenable', token
from public.notification_email_unsubscribe_tokens
where user_id = '12000000-0000-4000-8000-000000000002';

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '12000000-0000-4000-8000-000000000002',
  true
);
set local role authenticated;

update public.notification_email_preferences
set is_enabled = true
where user_id = '12000000-0000-4000-8000-000000000002';

set local role postgres;

select extensions.isnt(
  (
    select token
    from public.notification_email_unsubscribe_tokens
    where user_id = '12000000-0000-4000-8000-000000000002'
  ),
  (
    select token
    from unsubscribe_test_snapshot
    where label = 'no_inflight_reenable'
  ),
  're-enable without processing or retry_wait rotates the capability'
);

select extensions.is(
  (
    select is_enabled
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000002'
  ),
  true,
  'authenticated user can re-enable when no in-flight message exists'
);

insert into unsubscribe_test_snapshot (label, token)
select 'pending_reenable', token
from public.notification_email_unsubscribe_tokens
where user_id = '12000000-0000-4000-8000-000000000001';

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '12000000-0000-4000-8000-000000000001',
  true
);
set local role authenticated;

update public.notification_email_preferences
set is_enabled = true
where user_id = '12000000-0000-4000-8000-000000000001';

set local role postgres;

select extensions.isnt(
  (
    select token
    from public.notification_email_unsubscribe_tokens
    where user_id = '12000000-0000-4000-8000-000000000001'
  ),
  (select token from unsubscribe_test_snapshot where label = 'pending_reenable'),
  'pending-only re-enable rotates the capability'
);

select extensions.is(
  (
    select is_enabled
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000001'
  ),
  true,
  'pending-only re-enable succeeds'
);

select extensions.is(
  (
    select status::text
    from public.notification_messages
    where id = '52000000-0000-4000-8000-000000000001'
  ),
  'pending',
  'pending-only re-enable leaves the message pending'
);

select extensions.is(
  public.unsubscribe_email_notifications_by_token(
    (select token from unsubscribe_test_snapshot where label = 'pending_reenable')
  ),
  '{"outcome": "processed"}'::jsonb,
  'the rotated old capability still has a generic outcome'
);

select extensions.is(
  (
    select is_enabled
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000001'
  ),
  true,
  'the rotated old capability can no longer disable notifications'
);

select extensions.is(
  public.unsubscribe_email_notifications_by_token(
    (
      select token
      from public.notification_email_unsubscribe_tokens
      where user_id = '12000000-0000-4000-8000-000000000001'
    )
  ),
  '{"outcome": "processed"}'::jsonb,
  'the rotated current capability has the same generic outcome'
);

select extensions.is(
  (
    select is_enabled
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000001'
  ),
  false,
  'the rotated current capability disables notifications'
);

select pg_catalog.set_config(
  'request.jwt.claim.sub',
  '12000000-0000-4000-8000-000000000003',
  true
);
set local role authenticated;

select extensions.throws_ok(
  $$
    update public.notification_email_preferences
    set is_enabled = true
    where user_id = '12000000-0000-4000-8000-000000000003'
  $$,
  '23514',
  'new row for relation "notification_email_preferences" violates check constraint "notification_email_preferences_enabled_not_suppressed"',
  'provider suppression cannot be re-enabled by the authenticated user'
);

set local role postgres;

select extensions.is(
  (
    select token
    from public.notification_email_unsubscribe_tokens
    where user_id = '12000000-0000-4000-8000-000000000003'
  ),
  (
    select token
    from unsubscribe_test_snapshot
    where label = '12000000-0000-4000-8000-000000000003'
  ),
  'a rejected provider-suppressed re-enable does not rotate the token'
);

select extensions.ok(
  (
    select
      is_enabled = false
      and disabled_reason = 'resend_bounced'
      and disabled_at = timestamptz '2026-08-14 01:00:00+00'
    from public.notification_email_preferences
    where user_id = '12000000-0000-4000-8000-000000000003'
  ),
  'rejected re-enable preserves the provider suppression boundary'
);

select extensions.finish();

rollback;
