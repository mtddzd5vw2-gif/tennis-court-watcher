begin;

create extension if not exists pgtap with schema extensions;

select extensions.plan(30);

insert into auth.users (id)
values
  ('10000000-0000-4000-8000-000000000011'),
  ('10000000-0000-4000-8000-000000000012');

update public.profiles
set membership_status = 'active'::public.membership_status
where id in (
  '10000000-0000-4000-8000-000000000011',
  '10000000-0000-4000-8000-000000000012'
);

insert into public.notification_rules (
  id,
  user_id,
  name,
  is_enabled,
  start_time,
  end_time,
  minimum_duration_minutes
)
values
  (
    '20000000-0000-4000-8000-000000000011',
    '10000000-0000-4000-8000-000000000011',
    'LINE canary rule',
    true,
    time '09:00',
    time '12:00',
    60
  ),
  (
    '20000000-0000-4000-8000-000000000012',
    '10000000-0000-4000-8000-000000000012',
    'LINE other member rule',
    true,
    time '09:00',
    time '12:00',
    60
  );

insert into public.line_account_links (
  user_id,
  line_user_id,
  status
)
values
  (
    '10000000-0000-4000-8000-000000000011',
    'U11111111111111111111111111111111',
    'active'
  ),
  (
    '10000000-0000-4000-8000-000000000012',
    'U22222222222222222222222222222222',
    'active'
  );

create temporary table line_delivery_test_candidates (
  candidates jsonb not null
) on commit drop;

insert into line_delivery_test_candidates (candidates)
values (
  jsonb_build_array(
    jsonb_build_object(
      'user_id', '10000000-0000-4000-8000-000000000011',
      'channel', 'line',
      'slot_id', 'line-canary-slot',
      'facility_id', 'kamoike-prefectural',
      'facility_name', '鴨池県営テニスコート',
      'available_date', '2026-08-22',
      'start_time', '09:00',
      'end_time', '11:00',
      'matched_rule_ids', jsonb_build_array(
        '20000000-0000-4000-8000-000000000011'
      ),
      'payload', jsonb_build_object(
        'court_name', 'Aコート',
        'reservation_url', 'https://example.invalid/canary'
      )
    ),
    jsonb_build_object(
      'user_id', '10000000-0000-4000-8000-000000000012',
      'channel', 'line',
      'slot_id', 'line-other-slot',
      'facility_id', 'sumizei',
      'facility_name', 'SuMIzeiテニスコート',
      'available_date', '2026-08-22',
      'start_time', '10:00',
      'end_time', '12:00',
      'matched_rule_ids', jsonb_build_array(
        '20000000-0000-4000-8000-000000000012'
      ),
      'payload', jsonb_build_object(
        'court_name', 'Bコート',
        'reservation_url', 'https://example.invalid/other'
      )
    )
  )
);

select extensions.is(
  (
    select result.candidate_count
    from public.enqueue_line_notification_candidates(
      (select candidates from line_delivery_test_candidates),
      true,
      null,
      false
    ) as result
  ),
  2,
  'shadow evaluates the complete matching batch'
);

select extensions.is(
  (
    select result.eligible_candidate_count
    from public.enqueue_line_notification_candidates(
      (select candidates from line_delivery_test_candidates),
      true,
      null,
      false
    ) as result
  ),
  2,
  'shadow finds both linked active members'
);

select extensions.is(
  (
    select result.inserted_delivery_item_count
    from public.enqueue_line_notification_candidates(
      (select candidates from line_delivery_test_candidates),
      true,
      null,
      false
    ) as result
  ),
  0,
  'shadow writes no delivery items'
);

select extensions.is(
  (
    select count(*)::integer
    from public.notification_delivery_items
    where channel = 'line'::public.notification_channel
  ),
  0,
  'shadow leaves the shared queue unchanged'
);

create temporary table line_delivery_test_enqueue as
select *
from public.enqueue_line_notification_candidates(
  (select candidates from line_delivery_test_candidates),
  false,
  '10000000-0000-4000-8000-000000000011',
  false
);

select extensions.is(
  (select eligible_candidate_count from line_delivery_test_enqueue),
  1,
  'live canary filters eligibility to one member'
);

select extensions.is(
  (select inserted_delivery_item_count from line_delivery_test_enqueue),
  1,
  'live canary inserts one delivery item'
);

select extensions.is(
  (select inserted_message_count from line_delivery_test_enqueue),
  1,
  'live canary creates one aggregated message'
);

select extensions.is(
  (
    select count(*)::integer
    from public.notification_delivery_items
    where channel = 'line'::public.notification_channel
      and user_id = '10000000-0000-4000-8000-000000000012'
  ),
  0,
  'the other member receives no canary delivery item'
);

select extensions.is(
  (
    select count(*)::integer
    from public.claim_email_messages(10)
  ),
  0,
  'the email worker never claims a LINE message'
);

select extensions.is(
  (
    select status::text
    from public.notification_messages
    where channel = 'line'::public.notification_channel
  ),
  'pending',
  'email claim leaves LINE queue state unchanged'
);

create temporary table line_delivery_test_claim as
select * from public.claim_line_messages(1);

select extensions.is(
  (select count(*)::integer from line_delivery_test_claim),
  1,
  'LINE worker claims the canary message'
);

select extensions.is(
  (select line_user_id from line_delivery_test_claim),
  'U11111111111111111111111111111111',
  'LINE claim resolves only the canary recipient server-side'
);

select extensions.is(
  public.authorize_line_message_send(
    (select message_id from line_delivery_test_claim),
    (select locked_until from line_delivery_test_claim),
    (select line_user_id from line_delivery_test_claim),
    repeat('a', 64)
  ),
  'authorized',
  'LINE send authorization accepts the exact recipient and lease'
);

select extensions.is(
  public.record_line_message_accepted(
    (select message_id from line_delivery_test_claim),
    (select locked_until from line_delivery_test_claim),
    'line:request:123e4567-e89b-42d3-a456-426614174000',
    'accepted'
  ),
  true,
  'LINE acceptance records the current lease'
);

select extensions.is(
  (
    select status::text
    from public.notification_messages
    where id = (select message_id from line_delivery_test_claim)
  ),
  'accepted',
  'accepted LINE message is terminal'
);

select extensions.is(
  (
    select inserted_event_count
    from public.record_line_webhook_events(
      jsonb_build_array(jsonb_build_object(
        'webhook_event_id', '01LINEUNFOLLOW00000000000001',
        'event_type', 'unfollow',
        'line_user_id', 'U11111111111111111111111111111111',
        'occurred_at', '2026-08-21T10:00:00Z'
      ))
    )
  ),
  1,
  'first unfollow webhook is inserted'
);

select extensions.is(
  (
    select status::text
    from public.line_account_links
    where user_id = '10000000-0000-4000-8000-000000000011'
  ),
  'blocked',
  'unfollow blocks LINE delivery'
);

select extensions.is(
  (
    select inserted_event_count
    from public.record_line_webhook_events(
      jsonb_build_array(jsonb_build_object(
        'webhook_event_id', '01LINEUNFOLLOW00000000000001',
        'event_type', 'unfollow',
        'line_user_id', 'U11111111111111111111111111111111',
        'occurred_at', '2026-08-21T10:00:00Z'
      ))
    )
  ),
  0,
  'redelivered webhook is idempotent'
);

select extensions.is(
  (
    select updated_link_count
    from public.record_line_webhook_events(
      jsonb_build_array(jsonb_build_object(
        'webhook_event_id', '01LINEOLDERFOLLOW000000000001',
        'event_type', 'follow',
        'line_user_id', 'U11111111111111111111111111111111',
        'occurred_at', '2026-08-21T09:59:59Z'
      ))
    )
  ),
  0,
  'an older redelivery cannot overwrite newer block state'
);

select extensions.is(
  (
    select status::text
    from public.line_account_links
    where user_id = '10000000-0000-4000-8000-000000000011'
  ),
  'blocked',
  'older follow leaves the member blocked'
);

select extensions.is(
  (
    select updated_link_count
    from public.record_line_webhook_events(
      jsonb_build_array(jsonb_build_object(
        'webhook_event_id', '01LINENEWERFOLLOW000000000001',
        'event_type', 'follow',
        'line_user_id', 'U11111111111111111111111111111111',
        'occurred_at', '2026-08-21T10:00:01Z'
      ))
    )
  ),
  1,
  'a newer follow reactivates the link'
);

select extensions.is(
  (
    select status::text
    from public.line_account_links
    where user_id = '10000000-0000-4000-8000-000000000012'
  ),
  'active',
  'the other member link is isolated from canary events'
);

select extensions.ok(
  not has_function_privilege(
    'authenticated',
    'public.claim_line_messages(integer)',
    'EXECUTE'
  ),
  'authenticated members cannot claim LINE messages'
);

select extensions.ok(
  has_function_privilege(
    'service_role',
    'public.claim_line_messages(integer)',
    'EXECUTE'
  ),
  'service role can claim LINE messages'
);

select extensions.ok(
  not has_table_privilege(
    'authenticated',
    'public.line_webhook_events',
    'SELECT'
  ),
  'authenticated members cannot read the webhook ledger'
);

select extensions.ok(
  (
    select relrowsecurity
    from pg_catalog.pg_class
    where oid = 'public.line_webhook_events'::regclass
  ),
  'the webhook ledger has RLS enabled'
);

insert into public.notification_messages (
  id,
  user_id,
  channel,
  status
)
values (
  '40000000-0000-4000-8000-000000000099',
  '10000000-0000-4000-8000-000000000012',
  'line',
  'pending'
);

select extensions.is(
  (
    select cancelled_count
    from public.cancel_line_notification_backlog()
  ),
  1,
  'rollback cancellation removes unsent LINE backlog'
);

select extensions.is(
  (
    select status::text
    from public.notification_messages
    where id = '40000000-0000-4000-8000-000000000099'
  ),
  'cancelled',
  'rollback cancellation is persisted'
);

insert into public.line_webhook_events (
  webhook_event_id,
  event_type,
  occurred_at,
  created_at
)
values (
  '01LINEEXPIREDWEBHOOK0000000001',
  'follow',
  pg_catalog.now() - interval '91 days',
  pg_catalog.now() - interval '91 days'
);

select extensions.is(
  public.cleanup_line_webhook_events(1),
  1,
  'LINE webhook retention deletes one bounded expired event'
);

select extensions.ok(
  not has_function_privilege(
    'authenticated',
    'public.cleanup_line_webhook_events(integer)',
    'EXECUTE'
  ),
  'authenticated members cannot run webhook retention cleanup'
);

select extensions.finish();

rollback;
