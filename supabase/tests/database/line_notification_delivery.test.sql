begin;

create extension if not exists pgtap with schema extensions;

select extensions.plan(62);

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
select * from public.claim_line_messages(
  1,
  '10000000-0000-4000-8000-000000000011',
  false
);

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
    repeat('a', 64),
    '10000000-0000-4000-8000-000000000011',
    false
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

select extensions.ok(
  public.enqueue_line_canary_test(
    '10000000-0000-4000-8000-000000000011',
    '40000000-0000-4000-8000-000000000055'
  ),
  'fixed-text canary is queued for the selected active member'
);

select extensions.ok(
  public.enqueue_line_canary_test(
    '10000000-0000-4000-8000-000000000011',
    '40000000-0000-4000-8000-000000000055'
  ),
  'repeating the canary idempotency key is a safe no-op'
);

select extensions.is(
  (
    select count(*)::integer
    from public.notification_messages
    where id = '40000000-0000-4000-8000-000000000055'
  ),
  1,
  'canary replay creates no duplicate queue row'
);

select extensions.is(
  (
    select count(*)::integer
    from public.claim_email_messages(10)
  ),
  0,
  'email worker cannot claim the fixed-text LINE canary'
);

select extensions.is(
  (
    select count(*)::integer
    from public.claim_line_messages(
      1,
      '10000000-0000-4000-8000-000000000012',
      false
    )
  ),
  0,
  'the wrong server-side canary cannot claim the test message'
);

select extensions.is(
  (
    select status::text
    from public.notification_messages
    where id = '40000000-0000-4000-8000-000000000055'
  ),
  'pending',
  'wrong-canary claim leaves the selected recipients job pending'
);

create temporary table line_delivery_fixed_test_claim as
select * from public.claim_line_messages(
  1,
  '10000000-0000-4000-8000-000000000011',
  false
);

select extensions.is(
  (select count(*)::integer from line_delivery_fixed_test_claim),
  1,
  'the selected server-side canary claims exactly one test message'
);

select extensions.is(
  (select test_text from line_delivery_fixed_test_claim),
  '【テスト通知】鹿児島テニス空き情報 LINE通知の動作確認です。',
  'the canary carries only the fixed explicit test text'
);

select extensions.is(
  (select items from line_delivery_fixed_test_claim),
  '[]'::jsonb,
  'the canary does not fabricate an availability delivery item'
);

select extensions.is(
  public.authorize_line_message_send(
    (select message_id from line_delivery_fixed_test_claim),
    (select locked_until from line_delivery_fixed_test_claim),
    (select line_user_id from line_delivery_fixed_test_claim),
    repeat('b', 64),
    '10000000-0000-4000-8000-000000000011',
    false
  ),
  'authorized',
  'send authorization rechecks the selected canary for the test message'
);

select extensions.is(
  public.record_line_message_accepted(
    (select message_id from line_delivery_fixed_test_claim),
    (select locked_until from line_delivery_fixed_test_claim),
    'line:request:223e4567-e89b-42d3-a456-426614174000',
    'accepted'
  ),
  true,
  'the fixed-text canary records normal provider acceptance'
);

select extensions.is(
  (
    select status::text
    from public.notification_messages
    where id = '40000000-0000-4000-8000-000000000055'
  ),
  'accepted',
  'the accepted canary is terminal and cannot be resent'
);

insert into auth.users (id)
values ('10000000-0000-4000-8000-000000000013');

update public.profiles
set membership_status = 'active'::public.membership_status
where id = '10000000-0000-4000-8000-000000000013';

insert into public.notification_rules (
  id,
  user_id,
  name,
  is_enabled,
  start_time,
  end_time,
  minimum_duration_minutes
)
values (
  '20000000-0000-4000-8000-000000000013',
  '10000000-0000-4000-8000-000000000013',
  'LINE beta outsider rule',
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
values (
  '10000000-0000-4000-8000-000000000013',
  'U33333333333333333333333333333333',
  'active'
);

create temporary table line_beta_allowlist_replace as
select *
from private.replace_line_notification_beta_allowlist(array[
  '10000000-0000-4000-8000-000000000011'::uuid,
  '10000000-0000-4000-8000-000000000012'::uuid
]);

select extensions.is(
  (select allowlisted_count from line_beta_allowlist_replace),
  2,
  'the operator RPC installs two limited-beta members atomically'
);

select extensions.is(
  (select cancelled_message_count from line_beta_allowlist_replace),
  0,
  'initial beta installation has no stale backlog to cancel'
);

select extensions.is(
  (
    select count(*)::integer
    from private.line_notification_beta_allowlist
  ),
  2,
  'the private allowlist contains only the requested members'
);

select extensions.throws_ok(
  $$
    select *
    from private.replace_line_notification_beta_allowlist(
      array(
        select pg_catalog.gen_random_uuid()
        from pg_catalog.generate_series(1, 21)
      )
    )
  $$,
  '22023',
  'LINE beta allowlist input is invalid.',
  'the operator RPC rejects more than 20 members'
);

create temporary table line_beta_candidates (
  candidates jsonb not null
) on commit drop;

insert into line_beta_candidates (candidates)
values (
  jsonb_build_array(
    jsonb_build_object(
      'user_id', '10000000-0000-4000-8000-000000000011',
      'channel', 'line',
      'slot_id', 'line-beta-slot-one',
      'facility_id', 'kamoike-prefectural',
      'facility_name', '鴨池県営テニスコート',
      'available_date', '2026-08-23',
      'start_time', '09:00',
      'end_time', '11:00',
      'matched_rule_ids', jsonb_build_array(
        '20000000-0000-4000-8000-000000000011'
      ),
      'payload', jsonb_build_object(
        'court_name', 'Aコート',
        'reservation_url', 'https://example.invalid/beta-one'
      )
    ),
    jsonb_build_object(
      'user_id', '10000000-0000-4000-8000-000000000012',
      'channel', 'line',
      'slot_id', 'line-beta-slot-two',
      'facility_id', 'sumizei',
      'facility_name', 'SuMIzeiテニスコート',
      'available_date', '2026-08-23',
      'start_time', '10:00',
      'end_time', '12:00',
      'matched_rule_ids', jsonb_build_array(
        '20000000-0000-4000-8000-000000000012'
      ),
      'payload', jsonb_build_object(
        'court_name', 'Bコート',
        'reservation_url', 'https://example.invalid/beta-two'
      )
    ),
    jsonb_build_object(
      'user_id', '10000000-0000-4000-8000-000000000013',
      'channel', 'line',
      'slot_id', 'line-beta-slot-outsider',
      'facility_id', 'sumizei',
      'facility_name', 'SuMIzeiテニスコート',
      'available_date', '2026-08-23',
      'start_time', '09:00',
      'end_time', '11:00',
      'matched_rule_ids', jsonb_build_array(
        '20000000-0000-4000-8000-000000000013'
      ),
      'payload', jsonb_build_object(
        'court_name', 'Cコート',
        'reservation_url', 'https://example.invalid/beta-outsider'
      )
    )
  )
);

create temporary table line_beta_enqueue as
select *
from public.enqueue_line_notification_candidates(
  (select candidates from line_beta_candidates),
  false,
  null,
  true,
  false
);

select extensions.is(
  (select eligible_candidate_count from line_beta_enqueue),
  2,
  'limited-beta enqueue admits both allowlisted members'
);

select extensions.is(
  (select inserted_delivery_item_count from line_beta_enqueue),
  2,
  'limited-beta enqueue writes only the two allowed delivery items'
);

select extensions.is(
  (select inserted_message_count from line_beta_enqueue),
  2,
  'limited-beta enqueue creates one message per allowed member'
);

select extensions.is(
  (
    select count(*)::integer
    from public.notification_delivery_items
    where user_id = '10000000-0000-4000-8000-000000000013'
      and slot_id = 'line-beta-slot-outsider'
  ),
  0,
  'a linked active outsider receives no limited-beta delivery item'
);

select extensions.is(
  (
    select inserted_message_count
    from public.enqueue_line_notification_candidates(
      jsonb_build_array(
        (select candidates -> 2 from line_beta_candidates)
      ),
      false,
      null,
      false,
      true
    )
  ),
  1,
  'the explicit allow-all mode can independently queue the outsider test row'
);

create temporary table line_beta_claim as
select *
from public.claim_line_messages(
  10,
  null,
  true,
  false
);

select extensions.is(
  (select count(*)::integer from line_beta_claim),
  2,
  'limited-beta claim returns both and only allowlisted members'
);

select extensions.is(
  (
    select status::text
    from public.notification_messages
    where user_id = '10000000-0000-4000-8000-000000000013'
      and channel = 'line'::public.notification_channel
  ),
  'pending',
  'the allowlist worker leaves an outsider message unclaimed'
);

delete from private.line_notification_beta_allowlist
where user_id = '10000000-0000-4000-8000-000000000012';

select extensions.is(
  public.authorize_line_message_send(
    (
      select message_id from line_beta_claim
      where user_id = '10000000-0000-4000-8000-000000000012'
    ),
    (
      select locked_until from line_beta_claim
      where user_id = '10000000-0000-4000-8000-000000000012'
    ),
    (
      select line_user_id from line_beta_claim
      where user_id = '10000000-0000-4000-8000-000000000012'
    ),
    repeat('c', 64),
    null,
    true,
    false
  ),
  'cancelled',
  'send authorization cancels a member removed after claim'
);

select extensions.is(
  public.authorize_line_message_send(
    (
      select message_id from line_beta_claim
      where user_id = '10000000-0000-4000-8000-000000000011'
    ),
    (
      select locked_until from line_beta_claim
      where user_id = '10000000-0000-4000-8000-000000000011'
    ),
    (
      select line_user_id from line_beta_claim
      where user_id = '10000000-0000-4000-8000-000000000011'
    ),
    repeat('d', 64),
    null,
    true,
    false
  ),
  'authorized',
  'send authorization keeps a retained beta member eligible'
);

update public.notification_messages
set status = 'cancelled'::public.notification_message_status,
    locked_at = null,
    locked_until = null
where user_id = '10000000-0000-4000-8000-000000000013'
  and channel = 'line'::public.notification_channel;

select extensions.ok(
  not has_table_privilege(
    'authenticated',
    'private.line_notification_beta_allowlist',
    'SELECT'
  ),
  'authenticated members cannot inspect the private beta allowlist'
);

select extensions.ok(
  has_table_privilege(
    'service_role',
    'private.line_notification_beta_allowlist',
    'SELECT'
  ),
  'service role can read the allowlist for delivery enforcement'
);

select extensions.ok(
  not has_table_privilege(
    'service_role',
    'private.line_notification_beta_allowlist',
    'INSERT'
  ),
  'service role cannot bypass the trusted database-operator boundary'
);

select extensions.ok(
  not has_function_privilege(
    'service_role',
    'private.replace_line_notification_beta_allowlist(uuid[])',
    'EXECUTE'
  ),
  'service role cannot execute the private allowlist replacement function'
);

select extensions.ok(
  has_function_privilege(
    'service_role',
    'public.claim_line_messages(integer,uuid,boolean,boolean)',
    'EXECUTE'
  ),
  'service role can execute the allowlist-aware LINE claim'
);

select extensions.ok(
  not has_function_privilege(
    'service_role',
    'public.claim_line_messages(integer,uuid,boolean)',
    'EXECUTE'
  ),
  'service role cannot execute the deprecated claim boundary'
);

select extensions.ok(
  (
    select relforcerowsecurity
    from pg_catalog.pg_class
    where oid = 'private.line_notification_beta_allowlist'::regclass
  ),
  'the private beta allowlist forces RLS'
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
    'public.claim_line_messages(integer,uuid,boolean,boolean)',
    'EXECUTE'
  ),
  'authenticated members cannot claim LINE messages'
);

select extensions.ok(
  has_function_privilege(
    'service_role',
    'public.claim_line_messages(integer,uuid,boolean,boolean)',
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
