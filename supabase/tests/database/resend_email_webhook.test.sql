begin;

create extension if not exists pgtap with schema extensions;

select extensions.plan(50);

insert into auth.users (id)
values
  ('11000000-0000-4000-8000-000000000001'),
  ('11000000-0000-4000-8000-000000000002'),
  ('11000000-0000-4000-8000-000000000003'),
  ('11000000-0000-4000-8000-000000000004'),
  ('11000000-0000-4000-8000-000000000005'),
  ('11000000-0000-4000-8000-000000000006');

update public.profiles
set membership_status = 'active'::public.membership_status
where id::text like '11000000-0000-4000-8000-%';

update public.notification_email_preferences
set is_enabled = true
where user_id::text like '11000000-0000-4000-8000-%'
  and user_id <> '11000000-0000-4000-8000-000000000006';

update public.notification_email_preferences
set
  is_enabled = false,
  disabled_reason = 'manual_opt_out',
  disabled_at = pg_catalog.now() - interval '1 day'
where user_id = '11000000-0000-4000-8000-000000000006';

insert into public.notification_messages (
  id,
  user_id,
  channel,
  status,
  provider_message_id,
  provider_first_attempt_at,
  provider_payload_fingerprint,
  accepted_at
)
values
  (
    '51000000-0000-4000-8000-000000000001',
    '11000000-0000-4000-8000-000000000001',
    'email', 'accepted', 'resend_direct_1',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('a', 64),
    pg_catalog.now() - interval '19 minutes'
  ),
  (
    '51000000-0000-4000-8000-000000000002',
    '11000000-0000-4000-8000-000000000001',
    'email', 'accepted', 'resend_delayed_2',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('b', 64),
    pg_catalog.now() - interval '19 minutes'
  ),
  (
    '51000000-0000-4000-8000-000000000003',
    '11000000-0000-4000-8000-000000000001',
    'email', 'accepted', 'resend_failed_3',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('c', 64),
    pg_catalog.now() - interval '19 minutes'
  ),
  (
    '51000000-0000-4000-8000-000000000004',
    '11000000-0000-4000-8000-000000000002',
    'email', 'accepted', 'resend_bounced_4',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('d', 64),
    pg_catalog.now() - interval '19 minutes'
  ),
  (
    '51000000-0000-4000-8000-000000000005',
    '11000000-0000-4000-8000-000000000003',
    'email', 'accepted', 'resend_complained_5',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('e', 64),
    pg_catalog.now() - interval '19 minutes'
  ),
  (
    '51000000-0000-4000-8000-000000000006',
    '11000000-0000-4000-8000-000000000004',
    'email', 'accepted', 'resend_suppressed_6',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('f', 64),
    pg_catalog.now() - interval '19 minutes'
  ),
  (
    '51000000-0000-4000-8000-000000000007',
    '11000000-0000-4000-8000-000000000005',
    'email', 'accepted', 'resend_failed_7',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('1', 64),
    pg_catalog.now() - interval '19 minutes'
  ),
  (
    '51000000-0000-4000-8000-000000000008',
    '11000000-0000-4000-8000-000000000006',
    'email', 'accepted', 'resend_delivered_8',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('2', 64),
    pg_catalog.now() - interval '19 minutes'
  ),
  (
    '51000000-0000-4000-8000-000000000009',
    '11000000-0000-4000-8000-000000000001',
    'email', 'accepted', 'resend_order_9',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('3', 64),
    null
  ),
  (
    '51000000-0000-4000-8000-000000000011',
    '11000000-0000-4000-8000-000000000001',
    'email', 'accepted', 'resend_existing_11',
    pg_catalog.now() - interval '20 minutes',
    pg_catalog.repeat('5', 64),
    pg_catalog.now() - interval '19 minutes'
  );

insert into public.notification_messages (
  id,
  user_id,
  channel,
  status,
  attempt_count,
  locked_at,
  locked_until,
  provider_first_attempt_at,
  provider_payload_fingerprint
)
values (
  '51000000-0000-4000-8000-000000000010',
  '11000000-0000-4000-8000-000000000001',
  'email',
  'processing',
  1,
  pg_catalog.now(),
  pg_catalog.now() + interval '5 minutes',
  pg_catalog.now(),
  pg_catalog.repeat('4', 64)
);

select extensions.is(
  public.record_resend_email_event(
    'msg_sent_1', 'resend_direct_1', 'email.sent',
    pg_catalog.now() - interval '10 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'accepted message records email.sent'
);

select extensions.ok(
  (
    select
      status = 'accepted'::public.notification_message_status
      and provider_status = 'sent'
      and accepted_at is not null
      and locked_at is null
      and locked_until is null
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000001'
  ),
  'email.sent maps to accepted and clears any worker lease'
);

select extensions.ok(
  (
    select
      message_id = '51000000-0000-4000-8000-000000000001'
      and provider_status = 'sent'
    from public.notification_provider_events
    where provider_event_id = 'msg_sent_1'
  ),
  'provider message ID directly correlates the normalized event'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_delivered_1', 'resend_direct_1', 'email.delivered',
    pg_catalog.now() - interval '9 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'sent message records email.delivered'
);

select extensions.ok(
  (
    select
      status = 'delivered'::public.notification_message_status
      and provider_status = 'delivered'
      and delivered_at = pg_catalog.now() - interval '9 minutes'
      and failed_at is null
      and last_error_code is null
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000001'
  ),
  'email.delivered maps all delivery fields'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_delayed_2', 'resend_delayed_2', 'email.delivery_delayed',
    pg_catalog.now() - interval '8 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'email.delivery_delayed is recorded'
);

select extensions.ok(
  (
    select
      status = 'accepted'::public.notification_message_status
      and provider_status = 'delivery_delayed'
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000002'
  ),
  'email.delivery_delayed remains accepted with a normalized status'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_failed_3', 'resend_failed_3', 'email.failed',
    pg_catalog.now() - interval '7 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'email.failed is recorded'
);

select extensions.ok(
  (
    select
      status = 'failed_permanent'::public.notification_message_status
      and provider_status = 'failed'
      and failed_at = pg_catalog.now() - interval '7 minutes'
      and last_error_code = 'resend_delivery_failed'
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000003'
  ),
  'email.failed maps to a normalized permanent failure'
);

select extensions.is(
  (
    public.record_resend_email_event(
      'msg_bounced_4', 'resend_bounced_4', 'email.bounced',
      pg_catalog.now() - interval '6 minutes', null, null
    ) ->> 'preference_disabled_count'
  )::integer,
  1,
  'email.bounced reports one disabled preference'
);

select extensions.ok(
  (
    select
      status = 'bounced'::public.notification_message_status
      and provider_status = 'bounced'
      and last_error_code = 'resend_bounced'
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000004'
  ),
  'email.bounced maps message state'
);

select extensions.ok(
  (
    select
      is_enabled = false
      and disabled_reason = 'resend_bounced'
      and disabled_at = pg_catalog.now() - interval '6 minutes'
    from public.notification_email_preferences
    where user_id = '11000000-0000-4000-8000-000000000002'
  ),
  'email.bounced disables the recipient preference'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_complained_5', 'resend_complained_5', 'email.complained',
    pg_catalog.now() - interval '5 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'email.complained is recorded'
);

select extensions.ok(
  (
    select
      status = 'complained'::public.notification_message_status
      and last_error_code = 'resend_complained'
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000005'
  ),
  'email.complained maps message state'
);

select extensions.ok(
  (
    select
      is_enabled = false
      and disabled_reason = 'resend_complained'
    from public.notification_email_preferences
    where user_id = '11000000-0000-4000-8000-000000000003'
  ),
  'email.complained disables the recipient preference'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_suppressed_6', 'resend_suppressed_6', 'email.suppressed',
    pg_catalog.now() - interval '4 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'email.suppressed is recorded'
);

select extensions.ok(
  (
    select
      status = 'suppressed'::public.notification_message_status
      and last_error_code = 'resend_suppressed'
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000006'
  ),
  'email.suppressed maps message state'
);

select extensions.ok(
  (
    select
      is_enabled = false
      and disabled_reason = 'resend_suppressed'
    from public.notification_email_preferences
    where user_id = '11000000-0000-4000-8000-000000000004'
  ),
  'email.suppressed disables the recipient preference'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_failed_7', 'resend_failed_7', 'email.failed',
    pg_catalog.now() - interval '3 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'a second email.failed event records normally'
);

select extensions.ok(
  (
    select is_enabled = true and disabled_reason is null
    from public.notification_email_preferences
    where user_id = '11000000-0000-4000-8000-000000000005'
  ),
  'email.failed does not disable email preferences'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_delivered_8', 'resend_delivered_8', 'email.delivered',
    pg_catalog.now() - interval '2 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'delivery for a manually disabled user is recorded'
);

select extensions.ok(
  (
    select
      is_enabled = false
      and disabled_reason = 'manual_opt_out'
    from public.notification_email_preferences
    where user_id = '11000000-0000-4000-8000-000000000006'
  ),
  'email.delivered does not automatically re-enable preferences'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_late_bounced_8', 'resend_delivered_8', 'email.bounced',
    pg_catalog.now() - interval '2 days', null, null
  ) ->> 'outcome',
  'recorded',
  'a late provider suppression event is recorded for a manually disabled user'
);

select extensions.ok(
  (
    select
      is_enabled = false
      and disabled_reason = 'resend_bounced'
      and disabled_at = pg_catalog.now() - interval '2 days'
    from public.notification_email_preferences
    where user_id = '11000000-0000-4000-8000-000000000006'
  ),
  'provider suppression replaces a newer manual opt-out reason'
);

select extensions.ok(
  (
    select
      event_type = 'email.bounced'
      and occurred_at = pg_catalog.now() - interval '2 days'
    from public.notification_provider_events
    where provider_event_id = 'msg_late_bounced_8'
  ),
  'the late provider suppression event is stored normally'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_sent_1', 'resend_direct_1', 'email.sent',
    pg_catalog.now() - interval '10 minutes', null, null
  ) ->> 'outcome',
  'duplicate',
  'duplicate svix-id is a no-op'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_provider_events
    where provider_event_id = 'msg_sent_1'
  ),
  1,
  'duplicate svix-id stores only one provider event'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_order_delivered_9', 'resend_order_9', 'email.delivered',
    pg_catalog.now() - interval '1 minute', null, null
  ) ->> 'outcome',
  'recorded',
  'newer delivered event can arrive first'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_order_sent_9', 'resend_order_9', 'email.sent',
    pg_catalog.now() - interval '2 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'older sent event is still recorded'
);

select extensions.is(
  (
    select status::text
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000009'
  ),
  'delivered',
  'older sent event does not regress a delivered message'
);

select extensions.is(
  (
    select provider_status
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000009'
  ),
  'delivered',
  'older sent event preserves delivered provider status'
);

select extensions.is(
  (
    select accepted_at
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000009'
  ),
  pg_catalog.now() - interval '2 minutes',
  'older sent event moves accepted_at to the earliest provider chronology'
);

select extensions.is(
  (
    select delivered_at
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000009'
  ),
  pg_catalog.now() - interval '1 minute',
  'older sent event preserves the newer delivered_at'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_order_delayed_9', 'resend_order_9', 'email.delivery_delayed',
    pg_catalog.now() - interval '3 minutes', null, null
  ) ->> 'outcome',
  'recorded',
  'older delivery delay is still recorded'
);

select extensions.is(
  (
    select status::text || ':' || provider_status
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000009'
  ),
  'delivered:delivered',
  'older delivery delay does not regress a delivered message'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_tag_race_10', 'resend_tag_bound_10', 'email.sent',
    pg_catalog.now() - interval '1 minute',
    'user_notification',
    '51000000-0000-4000-8000-000000000010'
  ) ->> 'outcome',
  'recorded',
  'an authorized tag-race event correlates successfully'
);

select extensions.is(
  (
    select provider_message_id
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000010'
  ),
  'resend_tag_bound_10',
  'tag-race correlation binds the provider message ID'
);

select extensions.ok(
  (
    select
      message_id = '51000000-0000-4000-8000-000000000010'
      and provider_message_id = 'resend_tag_bound_10'
    from public.notification_provider_events
    where provider_event_id = 'msg_tag_race_10'
  ),
  'tag-race provider event references the bound message pair'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_conflict_11', 'resend_different_11', 'email.sent',
    pg_catalog.now() - interval '1 minute',
    'user_notification',
    '51000000-0000-4000-8000-000000000011'
  ) ->> 'outcome',
  'correlation_conflict',
  'a mismatched provider ID returns a correlation conflict'
);

select extensions.is(
  (
    select provider_message_id
    from public.notification_messages
    where id = '51000000-0000-4000-8000-000000000011'
  ),
  'resend_existing_11',
  'a correlation conflict never overwrites the provider ID'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_provider_events
    where provider_event_id = 'msg_conflict_11'
  ),
  0,
  'a correlation conflict stores no provider event'
);

select extensions.is(
  public.record_resend_email_event(
    'msg_external_12', 'resend_external_12', 'email.delivered',
    pg_catalog.now() - interval '1 minute', null, null
  ) ->> 'outcome',
  'ignored_unmatched',
  'an external unmatched event is ignored'
);

select extensions.is(
  (
    select pg_catalog.count(*)::integer
    from public.notification_provider_events
    where provider_event_id = 'msg_external_12'
  ),
  0,
  'an external unmatched event stores nothing'
);

select extensions.is(
  pg_catalog.has_table_privilege(
    'authenticated',
    'public.notification_provider_events',
    'insert'
  ),
  false,
  'authenticated cannot insert provider events directly'
);

select extensions.is(
  pg_catalog.has_table_privilege(
    'anon',
    'public.notification_messages',
    'update'
  ),
  false,
  'anon cannot update message state directly'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'service_role',
    'public.record_resend_email_event(text,text,text,timestamptz,text,text)',
    'execute'
  ),
  true,
  'service role can execute the Resend event RPC'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'anon',
    'public.record_resend_email_event(text,text,text,timestamptz,text,text)',
    'execute'
  ),
  false,
  'anon cannot execute the Resend event RPC'
);

select extensions.is(
  pg_catalog.has_function_privilege(
    'authenticated',
    'public.record_resend_email_event(text,text,text,timestamptz,text,text)',
    'execute'
  ),
  false,
  'authenticated cannot execute the Resend event RPC'
);

select extensions.throws_ok(
  $$
    select public.record_resend_email_event(
      'msg_future_13', 'resend_future_13', 'email.sent',
      pg_catalog.now() + interval '6 minutes', null, null
    )
  $$,
  '22023',
  'Resend event input is invalid.',
  'provider event timestamps beyond the clock-skew allowance are rejected'
);

select extensions.throws_ok(
  $$
    select public.record_resend_email_event(
      'msg_unsupported_14', 'resend_unsupported_14', 'email.opened',
      pg_catalog.now(), null, null
    )
  $$,
  '22023',
  'Resend event input is invalid.',
  'the RPC rejects unsupported event types'
);

select extensions.finish();

rollback;
