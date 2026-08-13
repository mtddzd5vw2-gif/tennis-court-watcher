begin;

create extension if not exists pgtap with schema extensions;

select extensions.plan(46);

do $$
begin
  perform pg_catalog.set_config(
    'app.update_availability_watchdog_test_now',
    '2026-08-13 03:00:00+00',
    true
  );
end;
$$;

update public.update_availability_watchdog_state
set
  last_snapshot_at = null,
  observation_token = null,
  snapshot_outcome = null,
  active_run_count = 0,
  latest_live_run_created_at = null,
  latest_live_success_at = null,
  latest_live_failure_at = null,
  consecutive_failure_count = 0,
  claim_token = null,
  claimed_at = null,
  claim_expires_at = null,
  dispatch_cooldown_until = null,
  dispatch_confirmed_at = null,
  last_dispatch_attempt_at = null,
  last_dispatch_accepted_at = null,
  last_dispatched_workflow_run_id = null,
  last_outcome = null,
  check_count = 0,
  github_api_error_count = 0,
  dispatch_attempt_count = 0,
  dispatch_accepted_count = 0
where watchdog_name = 'update-availability';

select extensions.is(
  (
    select recorded
    from public.record_update_availability_watchdog_snapshot(
      '10000000-0000-4000-8000-000000000001',
      'fresh',
      0,
      '2026-08-13 02:50:00+00',
      '2026-08-13 02:50:00+00',
      null,
      0
    )
  ),
  true,
  'fresh snapshot is recorded'
);

select extensions.is(
  (select snapshot_outcome from public.update_availability_watchdog_state),
  'fresh',
  'fresh snapshot outcome is retained'
);

select extensions.is(
  (select check_count from public.update_availability_watchdog_state),
  1::bigint,
  'one observation increments the check counter once'
);

select extensions.is(
  (
    select pg_catalog.count(*)
    from public.claim_update_availability_fallback(
      '10000000-0000-4000-8000-000000000001',
      '20000000-0000-4000-8000-000000000001'
    )
  ),
  0::bigint,
  'fresh state cannot be claimed'
);

update public.update_availability_watchdog_state
set
  observation_token = '10000000-0000-4000-8000-000000000100',
  snapshot_outcome = 'stale',
  active_run_count = 0,
  last_snapshot_at = public.update_availability_watchdog_now(),
  latest_live_run_created_at = null
where watchdog_name = 'update-availability';

select extensions.is(
  (
    select pg_catalog.count(*)
    from public.claim_update_availability_fallback(
      '10000000-0000-4000-8000-000000000100',
      '20000000-0000-4000-8000-000000000100'
    )
  ),
  0::bigint,
  'null latest live run cannot be claimed as stale'
);

do $$
begin
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000101',
    'stale', 0, '2026-08-13 02:15:00+00', null, null, 0
  );
  update public.update_availability_watchdog_state
  set last_snapshot_at = '2026-08-13 02:58:00+00'
  where watchdog_name = 'update-availability';
end;
$$;

select extensions.is(
  (
    select pg_catalog.count(*)
    from public.claim_update_availability_fallback(
      '10000000-0000-4000-8000-000000000101',
      '20000000-0000-4000-8000-000000000101'
    )
  ),
  1::bigint,
  'a snapshot exactly two minutes old can be claimed'
);

do $$
begin
  perform public.finish_update_availability_fallback(
    '20000000-0000-4000-8000-000000000101',
    'recheck_unknown'
  );
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000102',
    'stale', 0, '2026-08-13 02:15:00+00', null, null, 0
  );
  update public.update_availability_watchdog_state
  set last_snapshot_at = '2026-08-13 02:57:59+00'
  where watchdog_name = 'update-availability';
end;
$$;

select extensions.is(
  (
    select pg_catalog.count(*)
    from public.claim_update_availability_fallback(
      '10000000-0000-4000-8000-000000000102',
      '20000000-0000-4000-8000-000000000102'
    )
  ),
  0::bigint,
  'a snapshot older than two minutes cannot be claimed'
);

do $$
begin
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000103',
    'stale', 0, '2026-08-13 02:15:00+00', null, null, 0
  );
  perform * from public.claim_update_availability_fallback(
    '10000000-0000-4000-8000-000000000103',
    '20000000-0000-4000-8000-000000000103'
  );
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000104',
    'stale', 0, '2026-08-13 02:15:00+00', null, null, 0
  );
  update public.update_availability_watchdog_state
  set last_snapshot_at = '2026-08-13 02:57:59+00'
  where watchdog_name = 'update-availability';
end;
$$;

select extensions.is(
  public.confirm_update_availability_fallback(
    '10000000-0000-4000-8000-000000000104',
    '20000000-0000-4000-8000-000000000103'
  ),
  false,
  'a matching second observation token cannot confirm an old snapshot'
);

do $$
begin
  perform public.finish_update_availability_fallback(
    '20000000-0000-4000-8000-000000000103',
    'recheck_unknown'
  );
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000105',
    'stale', 0, '2026-08-13 02:15:00+00', null, null, 0
  );
  perform * from public.claim_update_availability_fallback(
    '10000000-0000-4000-8000-000000000105',
    '20000000-0000-4000-8000-000000000105'
  );
  update public.update_availability_watchdog_state
  set
    observation_token = '10000000-0000-4000-8000-000000000106',
    last_snapshot_at = public.update_availability_watchdog_now(),
    latest_live_run_created_at = null
  where watchdog_name = 'update-availability';
end;
$$;

select extensions.is(
  public.confirm_update_availability_fallback(
    '10000000-0000-4000-8000-000000000106',
    '20000000-0000-4000-8000-000000000105'
  ),
  false,
  'a second snapshot with null latest live run cannot confirm'
);

do $$
begin
  perform public.finish_update_availability_fallback(
    '20000000-0000-4000-8000-000000000105',
    'recheck_unknown'
  );
end;
$$;

select extensions.is(
  (
    select recorded
    from public.record_update_availability_watchdog_snapshot(
      '10000000-0000-4000-8000-000000000002',
      'stale',
      0,
      '2026-08-13 02:15:00+00',
      '2026-08-13 02:15:00+00',
      null,
      0
    )
  ),
  true,
  'exactly forty-five minutes old is recorded as stale'
);

select extensions.is(
  (select snapshot_outcome from public.update_availability_watchdog_state),
  'stale',
  'stale snapshot outcome is retained'
);

select extensions.is(
  (
    select pg_catalog.count(*)
    from public.claim_update_availability_fallback(
      '10000000-0000-4000-8000-000000000002',
      '20000000-0000-4000-8000-000000000002'
    )
  ),
  1::bigint,
  'stale state acquires one atomic claim'
);

select extensions.is(
  (select claim_expires_at from public.update_availability_watchdog_state),
  '2026-08-13 03:05:00+00'::timestamptz,
  'claim lease expires after five minutes'
);

select extensions.is(
  (select dispatch_cooldown_until from public.update_availability_watchdog_state),
  null::timestamptz,
  'claim alone does not start dispatch cooldown'
);

select extensions.is(
  (
    select pg_catalog.count(*)
    from public.claim_update_availability_fallback(
      '10000000-0000-4000-8000-000000000002',
      '20000000-0000-4000-8000-000000000003'
    )
  ),
  0::bigint,
  'a competing claim cannot acquire the singleton lease'
);

select extensions.is(
  public.finish_update_availability_fallback(
    '20000000-0000-4000-8000-000000000002',
    'recheck_unknown'
  ),
  true,
  'pre-POST unknown result releases the exact claim'
);

select extensions.is(
  (select claim_token from public.update_availability_watchdog_state),
  null::uuid,
  'pre-POST finish clears the claim'
);

select extensions.is(
  (select dispatch_cooldown_until from public.update_availability_watchdog_state),
  null::timestamptz,
  'pre-POST failure still has no cooldown'
);

select extensions.is(
  (
    select pg_catalog.count(*)
    from public.claim_update_availability_fallback(
      '10000000-0000-4000-8000-000000000002',
      '20000000-0000-4000-8000-000000000004'
    )
  ),
  1::bigint,
  'a released claim can be acquired again'
);

update public.update_availability_watchdog_state
set
  claimed_at = '2026-08-13 02:54:59+00',
  claim_expires_at = '2026-08-13 02:59:59+00'
where watchdog_name = 'update-availability';

select extensions.is(
  (
    select pg_catalog.count(*)
    from public.claim_update_availability_fallback(
      '10000000-0000-4000-8000-000000000002',
      '20000000-0000-4000-8000-000000000005'
    )
  ),
  1::bigint,
  'an expired claim can be reacquired'
);

select extensions.is(
  (select claim_token from public.update_availability_watchdog_state),
  '20000000-0000-4000-8000-000000000005'::uuid,
  'expired claim is replaced atomically'
);

select extensions.is(
  public.finish_update_availability_fallback(
    '20000000-0000-4000-8000-000000000005',
    'recheck_fresh'
  ),
  true,
  'replacement claim can finish before POST'
);

do $$
begin
  perform * from public.claim_update_availability_fallback(
    '10000000-0000-4000-8000-000000000002',
    '20000000-0000-4000-8000-000000000006'
  );
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000003',
    'stale',
    0,
    '2026-08-13 02:15:00+00',
    '2026-08-13 02:15:00+00',
    null,
    0
  );
end;
$$;

select extensions.is(
  public.confirm_update_availability_fallback(
    '10000000-0000-4000-8000-000000000002',
    '20000000-0000-4000-8000-000000000006'
  ),
  false,
  'confirm rejects the first observation token'
);

select extensions.is(
  public.confirm_update_availability_fallback(
    '10000000-0000-4000-8000-000000000003',
    '20000000-0000-4000-8000-000000000006'
  ),
  true,
  'confirm accepts the second stale observation token'
);

select extensions.is(
  (select dispatch_cooldown_until from public.update_availability_watchdog_state),
  '2026-08-13 03:30:00+00'::timestamptz,
  'confirm starts a thirty-minute cooldown'
);

select extensions.is(
  (select dispatch_attempt_count from public.update_availability_watchdog_state),
  1::bigint,
  'confirm records the POST attempt before network I/O'
);

select extensions.is(
  public.finish_update_availability_fallback(
    '20000000-0000-4000-8000-000000000006',
    'dispatch_failed'
  ),
  true,
  'known POST rejection finishes the confirmed claim'
);

select extensions.is(
  (select last_outcome from public.update_availability_watchdog_state),
  'dispatch_failed',
  'known POST rejection has a distinct normalized outcome'
);

do $$
begin
  perform pg_catalog.set_config(
    'app.update_availability_watchdog_test_now',
    '2026-08-13 03:31:00+00',
    true
  );
end;
$$;

do $$
begin
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000004',
    'stale', 0, '2026-08-13 02:15:00+00', null, null, 0
  );
  perform * from public.claim_update_availability_fallback(
    '10000000-0000-4000-8000-000000000004',
    '20000000-0000-4000-8000-000000000007'
  );
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000005',
    'stale', 0, '2026-08-13 02:15:00+00', null, null, 0
  );
  perform public.confirm_update_availability_fallback(
    '10000000-0000-4000-8000-000000000005',
    '20000000-0000-4000-8000-000000000007'
  );
end;
$$;

select extensions.throws_ok(
  $$
    select public.finish_update_availability_fallback(
      '20000000-0000-4000-8000-000000000008',
      'dispatch_accepted',
      null
    )
  $$,
  '22023',
  'Watchdog finish input is invalid.',
  'accepted dispatch requires a workflow run id'
);

select extensions.is(
  public.finish_update_availability_fallback(
    '20000000-0000-4000-8000-000000000007',
    'dispatch_unknown'
  ),
  true,
  'ambiguous POST result finishes the confirmed claim'
);

select extensions.is(
  (select last_outcome from public.update_availability_watchdog_state),
  'dispatch_unknown',
  'ambiguous POST result differs from a pre-POST or known failure'
);

select extensions.ok(
  (
    select dispatch_cooldown_until
    from public.update_availability_watchdog_state
  ) > public.update_availability_watchdog_now(),
  'ambiguous POST result preserves cooldown'
);

do $$
begin
  perform pg_catalog.set_config(
    'app.update_availability_watchdog_test_now',
    '2026-08-13 04:02:00+00',
    true
  );
end;
$$;

do $$
begin
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000006',
    'stale', 0, '2026-08-13 02:15:00+00', null, null, 0
  );
  perform * from public.claim_update_availability_fallback(
    '10000000-0000-4000-8000-000000000006',
    '20000000-0000-4000-8000-000000000008'
  );
  perform * from public.record_update_availability_watchdog_snapshot(
    '10000000-0000-4000-8000-000000000007',
    'stale', 0, '2026-08-13 02:15:00+00', null, null, 0
  );
  perform public.confirm_update_availability_fallback(
    '10000000-0000-4000-8000-000000000007',
    '20000000-0000-4000-8000-000000000008'
  );
end;
$$;

select extensions.is(
  public.finish_update_availability_fallback(
    '20000000-0000-4000-8000-000000000008',
    'dispatch_accepted',
    987654321
  ),
  true,
  'accepted POST result finishes the confirmed claim'
);

select extensions.is(
  (select dispatch_accepted_count from public.update_availability_watchdog_state),
  1::bigint,
  'accepted dispatch counter is incremented once'
);

select extensions.is(
  (
    select last_dispatched_workflow_run_id
    from public.update_availability_watchdog_state
  ),
  987654321::bigint,
  'available workflow run id is retained for later liveness checks'
);

select extensions.is(
  public.update_availability_watchdog_in_service_window(
    '2026-08-12 15:22:00+00'
  ),
  true,
  'JST 00:22 is inside the service window'
);

select extensions.is(
  public.update_availability_watchdog_in_service_window(
    '2026-08-12 15:30:00+00'
  ),
  false,
  'JST 00:30 is outside the service window'
);

select extensions.is(
  public.update_availability_watchdog_in_service_window(
    '2026-08-12 22:20:00+00'
  ),
  true,
  'JST 07:20 is inside the service window'
);

select extensions.is(
  public.update_availability_watchdog_in_service_window(
    '2026-08-12 22:19:59+00'
  ),
  false,
  'JST 07:19:59 is outside the service window'
);

select extensions.is(
  (
    select relrowsecurity
    from pg_catalog.pg_class
    where oid = 'public.update_availability_watchdog_state'::regclass
  ),
  true,
  'watchdog state has RLS enabled'
);

select extensions.is(
  (
    select pg_catalog.count(*)
    from pg_catalog.pg_policy
    where polrelid = 'public.update_availability_watchdog_state'::regclass
  ),
  0::bigint,
  'watchdog state intentionally has no regular RLS policy'
);

select extensions.is(
  pg_catalog.has_table_privilege(
    'anon',
    'public.update_availability_watchdog_state',
    'select'
  ),
  false,
  'anon has no direct state read privilege'
);

select extensions.is(
  pg_catalog.has_table_privilege(
    'authenticated',
    'public.update_availability_watchdog_state',
    'select'
  ),
  false,
  'authenticated has no direct state read privilege'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'anon',
    'public.record_update_availability_watchdog_snapshot(uuid,text,integer,timestamptz,timestamptz,timestamptz,integer)',
    'execute'
  ),
  false,
  'anon cannot execute snapshot RPC'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'authenticated',
    'public.record_update_availability_watchdog_snapshot(uuid,text,integer,timestamptz,timestamptz,timestamptz,integer)',
    'execute'
  ),
  false,
  'authenticated cannot execute snapshot RPC'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.record_update_availability_watchdog_snapshot(uuid,text,integer,timestamptz,timestamptz,timestamptz,integer)',
    'execute'
  ),
  true,
  'service role can execute snapshot RPC'
);

select extensions.finish();

rollback;
