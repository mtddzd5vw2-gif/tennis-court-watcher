begin;

create extension if not exists pgtap with schema extensions;

select extensions.plan(18);

insert into auth.users (id)
values ('61000000-0000-4000-8000-000000000001');

update public.profiles
set membership_status = 'active'::public.membership_status
where id = '61000000-0000-4000-8000-000000000001';

-- Old delivered history: eligible for message deletion and then orphan delivery cleanup.
insert into public.notification_delivery_items (
  id,
  user_id,
  channel,
  slot_id,
  facility_id,
  facility_name,
  available_date,
  start_time,
  end_time,
  matched_rule_ids,
  payload,
  created_at
)
values (
  '62000000-0000-4000-8000-000000000001',
  '61000000-0000-4000-8000-000000000001',
  'email',
  'retention-old-delivered',
  'kamoike-prefectural',
  '鴨池県営テニスコート',
  (current_date - 120),
  time '09:00',
  time '11:00',
  array['63000000-0000-4000-8000-000000000001'::uuid],
  '{}',
  pg_catalog.now() - interval '120 days'
);

insert into public.notification_messages (
  id,
  user_id,
  channel,
  status,
  provider_message_id,
  provider_status,
  accepted_at,
  delivered_at,
  created_at,
  updated_at
)
values (
  '64000000-0000-4000-8000-000000000001',
  '61000000-0000-4000-8000-000000000001',
  'email',
  'delivered',
  'resend_retention_old_1',
  'delivered',
  pg_catalog.now() - interval '119 days',
  pg_catalog.now() - interval '119 days',
  pg_catalog.now() - interval '120 days',
  pg_catalog.now() - interval '119 days'
);

insert into public.notification_message_items (
  message_id,
  delivery_item_id,
  user_id,
  channel,
  created_at
)
values (
  '64000000-0000-4000-8000-000000000001',
  '62000000-0000-4000-8000-000000000001',
  '61000000-0000-4000-8000-000000000001',
  'email',
  pg_catalog.now() - interval '120 days'
);

insert into public.notification_provider_events (
  id,
  message_id,
  provider,
  provider_event_id,
  provider_message_id,
  event_type,
  provider_status,
  occurred_at,
  created_at
)
values (
  '65000000-0000-4000-8000-000000000001',
  '64000000-0000-4000-8000-000000000001',
  'resend',
  'msg_retention_old_event_1',
  'resend_retention_old_1',
  'email.delivered',
  'delivered',
  pg_catalog.now() - interval '119 days',
  pg_catalog.now() - interval '119 days'
);

-- Old terminal message with a recently-arrived provider event must be retained.
insert into public.notification_delivery_items (
  id,
  user_id,
  channel,
  slot_id,
  facility_id,
  facility_name,
  available_date,
  start_time,
  end_time,
  matched_rule_ids,
  payload,
  created_at
)
values (
  '62000000-0000-4000-8000-000000000002',
  '61000000-0000-4000-8000-000000000001',
  'email',
  'retention-recent-event',
  'sumizei',
  'SuMIzeiテニスコート',
  (current_date - 120),
  time '10:00',
  time '12:00',
  array['63000000-0000-4000-8000-000000000002'::uuid],
  '{}',
  pg_catalog.now() - interval '120 days'
);

insert into public.notification_messages (
  id,
  user_id,
  channel,
  status,
  provider_message_id,
  provider_status,
  accepted_at,
  delivered_at,
  created_at,
  updated_at
)
values (
  '64000000-0000-4000-8000-000000000002',
  '61000000-0000-4000-8000-000000000001',
  'email',
  'delivered',
  'resend_retention_recent_event',
  'delivered',
  pg_catalog.now() - interval '120 days',
  pg_catalog.now() - interval '120 days',
  pg_catalog.now() - interval '120 days',
  pg_catalog.now() - interval '120 days'
);

insert into public.notification_message_items (
  message_id,
  delivery_item_id,
  user_id,
  channel,
  created_at
)
values (
  '64000000-0000-4000-8000-000000000002',
  '62000000-0000-4000-8000-000000000002',
  '61000000-0000-4000-8000-000000000001',
  'email',
  pg_catalog.now() - interval '120 days'
);

insert into public.notification_provider_events (
  id,
  message_id,
  provider,
  provider_event_id,
  provider_message_id,
  event_type,
  provider_status,
  occurred_at,
  created_at
)
values (
  '65000000-0000-4000-8000-000000000002',
  '64000000-0000-4000-8000-000000000002',
  'resend',
  'msg_retention_recent_event_2',
  'resend_retention_recent_event',
  'email.delivered',
  'delivered',
  pg_catalog.now() - interval '120 days',
  pg_catalog.now() - interval '1 day'
);

-- Active lifecycle states are never retention candidates.
insert into public.notification_messages (
  id,
  user_id,
  channel,
  status,
  created_at,
  updated_at
)
values
  (
    '64000000-0000-4000-8000-000000000003',
    '61000000-0000-4000-8000-000000000001',
    'email',
    'pending',
    pg_catalog.now() - interval '120 days',
    pg_catalog.now() - interval '120 days'
  ),
  (
    '64000000-0000-4000-8000-000000000004',
    '61000000-0000-4000-8000-000000000001',
    'email',
    'processing',
    pg_catalog.now() - interval '120 days',
    pg_catalog.now() - interval '120 days'
  ),
  (
    '64000000-0000-4000-8000-000000000005',
    '61000000-0000-4000-8000-000000000001',
    'email',
    'retry_wait',
    pg_catalog.now() - interval '120 days',
    pg_catalog.now() - interval '120 days'
  );

-- An old orphan whose availability date is still in the future must stay.
insert into public.notification_delivery_items (
  id,
  user_id,
  channel,
  slot_id,
  facility_id,
  facility_name,
  available_date,
  start_time,
  end_time,
  matched_rule_ids,
  payload,
  created_at
)
values (
  '62000000-0000-4000-8000-000000000003',
  '61000000-0000-4000-8000-000000000001',
  'email',
  'retention-future-orphan',
  'toukai-tennis',
  '東開庭球場',
  (current_date + 30),
  time '08:00',
  time '10:00',
  array['63000000-0000-4000-8000-000000000003'::uuid],
  '{}',
  pg_catalog.now() - interval '120 days'
);

-- An old orphan with a past availability date is independently eligible.
insert into public.notification_delivery_items (
  id,
  user_id,
  channel,
  slot_id,
  facility_id,
  facility_name,
  available_date,
  start_time,
  end_time,
  matched_rule_ids,
  payload,
  created_at
)
values (
  '62000000-0000-4000-8000-000000000004',
  '61000000-0000-4000-8000-000000000001',
  'email',
  'retention-past-orphan',
  'kamoike-prefectural',
  '鴨池県営テニスコート',
  (current_date - 120),
  time '11:00',
  time '13:00',
  array['63000000-0000-4000-8000-000000000004'::uuid],
  '{}',
  pg_catalog.now() - interval '120 days'
);

select extensions.throws_ok(
  $$ select public.cleanup_email_notification_history(0) $$,
  '22023',
  'Email retention cleanup batch size is invalid.',
  'batch size zero is rejected'
);

select extensions.throws_ok(
  $$ select public.cleanup_email_notification_history(1001) $$,
  '22023',
  'Email retention cleanup batch size is invalid.',
  'batch sizes above the maximum are rejected'
);

select extensions.is(
  (
    public.cleanup_email_notification_history(1000)
      ->> 'deleted_message_count'
  )::integer,
  1,
  'one old stable terminal message is deleted'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_messages
    where id = '64000000-0000-4000-8000-000000000001'
  ),
  0,
  'eligible message is gone'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_message_items
    where message_id = '64000000-0000-4000-8000-000000000001'
  ),
  0,
  'message item cascades with the deleted message'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_provider_events
    where message_id = '64000000-0000-4000-8000-000000000001'
  ),
  0,
  'provider event cascades with the deleted message'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_delivery_items
    where id = '62000000-0000-4000-8000-000000000001'
  ),
  0,
  'old past delivery item is deleted after its message reference disappears'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_messages
    where id = '64000000-0000-4000-8000-000000000002'
  ),
  1,
  'recently-arrived provider event protects an old terminal message'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_delivery_items
    where id = '62000000-0000-4000-8000-000000000002'
  ),
  1,
  'delivery item referenced by a retained message stays'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_messages
    where id = any (
      array[
        '64000000-0000-4000-8000-000000000003'::uuid,
        '64000000-0000-4000-8000-000000000004'::uuid,
        '64000000-0000-4000-8000-000000000005'::uuid
      ]
    )
  ),
  3,
  'pending processing and retry_wait messages are retained'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_delivery_items
    where id = '62000000-0000-4000-8000-000000000003'
  ),
  1,
  'future availability delivery item is retained even when old and orphaned'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_delivery_items
    where id = '62000000-0000-4000-8000-000000000004'
  ),
  0,
  'old orphaned delivery item with a past availability date is deleted'
);

select extensions.is(
  (
    public.cleanup_email_notification_history(1000)
      ->> 'deleted_message_count'
  )::integer,
  0,
  'second cleanup is idempotent for messages'
);

select extensions.is(
  (
    public.cleanup_email_notification_history(1000)
      ->> 'deleted_delivery_item_count'
  )::integer,
  0,
  'second cleanup is idempotent for delivery items'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'anon',
    'public.cleanup_email_notification_history(integer)',
    'EXECUTE'
  ),
  false,
  'anon cannot execute retention cleanup'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'authenticated',
    'public.cleanup_email_notification_history(integer)',
    'EXECUTE'
  ),
  false,
  'authenticated cannot execute retention cleanup'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.cleanup_email_notification_history(integer)',
    'EXECUTE'
  ),
  true,
  'service_role can execute retention cleanup'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_messages
    where id = '64000000-0000-4000-8000-000000000002'
  ),
  1,
  'cleanup never removes the recent-event protected message on repeat runs'
);

select * from extensions.finish();

rollback;
