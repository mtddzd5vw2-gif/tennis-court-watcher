begin;

create extension if not exists pgtap with schema extensions;

select extensions.plan(32);

insert into auth.users (id)
values
  ('70000000-0000-4000-8000-000000000001'),
  ('70000000-0000-4000-8000-000000000002'),
  ('70000000-0000-4000-8000-000000000003'),
  ('70000000-0000-4000-8000-000000000004');

update public.profiles
set membership_status = 'active'::public.membership_status
where id in (
  '70000000-0000-4000-8000-000000000001',
  '70000000-0000-4000-8000-000000000002',
  '70000000-0000-4000-8000-000000000004'
);

select extensions.ok(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.create_line_link_session(uuid,text,text,timestamptz)',
    'EXECUTE'
  ),
  'service_role can create LINE link sessions'
);

select extensions.ok(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.complete_line_account_link(text,text,text,boolean)',
    'EXECUTE'
  ),
  'service_role can complete LINE account links'
);

select extensions.ok(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.unlink_line_account(uuid)',
    'EXECUTE'
  ),
  'service_role can unlink LINE accounts'
);

select extensions.ok(
  not pg_catalog.has_function_privilege(
    'authenticated',
    'public.create_line_link_session(uuid,text,text,timestamptz)',
    'EXECUTE'
  ),
  'authenticated cannot create LINE link sessions directly'
);

select extensions.ok(
  not pg_catalog.has_function_privilege(
    'authenticated',
    'public.complete_line_account_link(text,text,text,boolean)',
    'EXECUTE'
  ),
  'authenticated cannot complete LINE account links directly'
);

select extensions.ok(
  not pg_catalog.has_function_privilege(
    'authenticated',
    'public.unlink_line_account(uuid)',
    'EXECUTE'
  ),
  'authenticated cannot unlink LINE accounts directly'
);

select extensions.ok(
  not pg_catalog.has_function_privilege(
    'anon',
    'public.create_line_link_session(uuid,text,text,timestamptz)',
    'EXECUTE'
  ),
  'anon cannot create LINE link sessions'
);

select extensions.ok(
  not pg_catalog.has_function_privilege(
    'anon',
    'public.complete_line_account_link(text,text,text,boolean)',
    'EXECUTE'
  ),
  'anon cannot complete LINE account links'
);

select extensions.ok(
  not pg_catalog.has_function_privilege(
    'anon',
    'public.unlink_line_account(uuid)',
    'EXECUTE'
  ),
  'anon cannot unlink LINE accounts'
);

set local role service_role;

select extensions.is(
  public.create_line_link_session(
    '70000000-0000-4000-8000-000000000001',
    pg_catalog.repeat('a', 64),
    pg_catalog.repeat('1', 64),
    pg_catalog.clock_timestamp() + interval '5 minutes'
  ),
  'created',
  'active member can create a valid session'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.line_link_sessions
    where user_id = '70000000-0000-4000-8000-000000000001'
  ),
  1,
  'first valid session is stored'
);

select extensions.is(
  public.create_line_link_session(
    '70000000-0000-4000-8000-000000000001',
    pg_catalog.repeat('b', 64),
    pg_catalog.repeat('2', 64),
    pg_catalog.clock_timestamp() + interval '5 minutes'
  ),
  'created',
  'starting again replaces the previous session'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.line_link_sessions
    where user_id = '70000000-0000-4000-8000-000000000001'
  ),
  1,
  'a member has only one live session'
);

select extensions.is(
  (
    select pg_catalog.encode(state_hash, 'hex')
    from public.line_link_sessions
    where user_id = '70000000-0000-4000-8000-000000000001'
  ),
  pg_catalog.repeat('b', 64),
  'only the decoded SHA-256 hash is stored'
);

select extensions.is(
  public.create_line_link_session(
    '70000000-0000-4000-8000-000000000003',
    pg_catalog.repeat('c', 64),
    pg_catalog.repeat('3', 64),
    pg_catalog.clock_timestamp() + interval '5 minutes'
  ),
  'inactive_member',
  'non-active member cannot create a session'
);

select extensions.is(
  public.create_line_link_session(
    '70000000-0000-4000-8000-000000000001',
    'short',
    pg_catalog.repeat('4', 64),
    pg_catalog.clock_timestamp() + interval '5 minutes'
  ),
  'invalid_request',
  'malformed hash input fails closed'
);

select extensions.is(
  public.complete_line_account_link(
    pg_catalog.repeat('b', 64),
    pg_catalog.repeat('2', 64),
    'Ucccccccccccccccccccccccccccccccc',
    true
  ),
  'linked',
  'verified friend completes the one-time link'
);

select extensions.is(
  (
    select status::text
    from public.line_account_links
    where user_id = '70000000-0000-4000-8000-000000000001'
      and line_user_id = 'Ucccccccccccccccccccccccccccccccc'
  ),
  'active',
  'friend link is active'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.line_link_sessions
    where user_id = '70000000-0000-4000-8000-000000000001'
  ),
  0,
  'successful completion removes the session'
);

select extensions.is(
  public.complete_line_account_link(
    pg_catalog.repeat('b', 64),
    pg_catalog.repeat('2', 64),
    'Ucccccccccccccccccccccccccccccccc',
    true
  ),
  'invalid_session',
  'consumed state and nonce cannot be reused'
);

select extensions.is(
  public.create_line_link_session(
    '70000000-0000-4000-8000-000000000002',
    pg_catalog.repeat('d', 64),
    pg_catalog.repeat('4', 64),
    pg_catalog.clock_timestamp() + interval '5 minutes'
  ),
  'created',
  'second active member can start a separate session'
);

select extensions.is(
  public.complete_line_account_link(
    pg_catalog.repeat('d', 64),
    pg_catalog.repeat('4', 64),
    'Ucccccccccccccccccccccccccccccccc',
    true
  ),
  'line_conflict',
  'same LINE account cannot be claimed by another member'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.line_account_links
    where user_id = '70000000-0000-4000-8000-000000000002'
  ),
  0,
  'LINE conflict creates no second member link'
);

select extensions.is(
  public.create_line_link_session(
    '70000000-0000-4000-8000-000000000001',
    pg_catalog.repeat('e', 64),
    pg_catalog.repeat('5', 64),
    pg_catalog.clock_timestamp() + interval '5 minutes'
  ),
  'created',
  'linked member can start an explicit refresh flow'
);

select extensions.is(
  public.complete_line_account_link(
    pg_catalog.repeat('e', 64),
    pg_catalog.repeat('5', 64),
    'Udddddddddddddddddddddddddddddddd',
    true
  ),
  'member_conflict',
  'active member cannot silently replace their LINE account'
);

select extensions.is(
  (
    select line_user_id
    from public.line_account_links
    where user_id = '70000000-0000-4000-8000-000000000001'
  ),
  'Ucccccccccccccccccccccccccccccccc',
  'member conflict preserves the original LINE account'
);

select extensions.is(
  public.unlink_line_account(
    '70000000-0000-4000-8000-000000000001'
  ),
  'unlinked',
  'active member link can be unlinked'
);

select extensions.ok(
  (
    select status = 'unlinked'::public.line_account_link_status
      and unlinked_at is not null
    from public.line_account_links
    where user_id = '70000000-0000-4000-8000-000000000001'
  ),
  'unlink records status and timestamp'
);

select extensions.is(
  public.unlink_line_account(
    '70000000-0000-4000-8000-000000000001'
  ),
  'not_linked',
  'unlink is idempotent'
);

select extensions.is(
  public.create_line_link_session(
    '70000000-0000-4000-8000-000000000004',
    pg_catalog.repeat('f', 64),
    pg_catalog.repeat('6', 64),
    pg_catalog.clock_timestamp() + interval '5 minutes'
  ),
  'created',
  'another active member can start a session'
);

select extensions.is(
  public.complete_line_account_link(
    pg_catalog.repeat('f', 64),
    pg_catalog.repeat('6', 64),
    'Ueeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
    false
  ),
  'friend_required',
  'non-friend link is recorded with a safe action result'
);

select extensions.is(
  (
    select status::text
    from public.line_account_links
    where user_id = '70000000-0000-4000-8000-000000000004'
  ),
  'blocked',
  'non-friend link is not active for delivery'
);

set local role postgres;

select * from extensions.finish();

rollback;
