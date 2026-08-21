-- Phase 4 user-specific LINE webhook, queue, and delivery lifecycle.
-- All new delivery paths remain disabled until their independent feature flags
-- and production secrets are configured.

begin;

create table public.line_webhook_events (
  webhook_event_id text primary key,
  event_type text not null,
  occurred_at timestamptz not null,
  created_at timestamptz not null default pg_catalog.now(),
  constraint line_webhook_events_id_not_blank
    check (pg_catalog.btrim(webhook_event_id) <> ''),
  constraint line_webhook_events_id_length
    check (pg_catalog.char_length(webhook_event_id) <= 255),
  constraint line_webhook_events_type_check
    check (event_type in ('follow', 'unfollow'))
);

create index line_webhook_events_created_at_idx
  on public.line_webhook_events (created_at);

alter table public.line_webhook_events enable row level security;

revoke all privileges on table public.line_webhook_events
from public, anon, authenticated, service_role;

grant select, insert, delete on table public.line_webhook_events
to service_role;

create function public.record_line_webhook_events(
  p_events jsonb
)
returns table (
  relevant_event_count integer,
  inserted_event_count integer,
  updated_link_count integer
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_event pg_catalog.jsonb;
  v_relevant_event_count pg_catalog.int4;
  v_inserted_event_count pg_catalog.int4 := 0;
  v_updated_link_count pg_catalog.int4 := 0;
  v_inserted pg_catalog.bool;
  v_updated_count pg_catalog.int4;
  v_event_id pg_catalog.text;
  v_event_type pg_catalog.text;
  v_line_user_id pg_catalog.text;
  v_occurred_at timestamptz;
begin
  if p_events is null or pg_catalog.jsonb_typeof(p_events) <> 'array' then
    raise exception 'LINE webhook events must be a JSON array.'
      using errcode = '22023';
  end if;

  v_relevant_event_count := pg_catalog.jsonb_array_length(p_events);
  if v_relevant_event_count > 100 then
    raise exception 'LINE webhook event batch exceeds 100 items.'
      using errcode = '22023';
  end if;

  for v_event in
    select event_input.value
    from pg_catalog.jsonb_array_elements(p_events) as event_input(value)
  loop
    if (
      pg_catalog.jsonb_typeof(v_event) <> 'object'
      or not (
        v_event ?& array[
          'webhook_event_id',
          'event_type',
          'line_user_id',
          'occurred_at'
        ]::pg_catalog.text[]
      )
      or (
        select pg_catalog.count(*)
        from pg_catalog.jsonb_object_keys(v_event)
      ) <> 4
      or pg_catalog.jsonb_typeof(v_event -> 'webhook_event_id') <> 'string'
      or pg_catalog.jsonb_typeof(v_event -> 'event_type') <> 'string'
      or pg_catalog.jsonb_typeof(v_event -> 'line_user_id') <> 'string'
      or pg_catalog.jsonb_typeof(v_event -> 'occurred_at') <> 'string'
    ) then
      raise exception 'LINE webhook event shape is invalid.'
        using errcode = '22023';
    end if;

    v_event_id := v_event ->> 'webhook_event_id';
    v_event_type := v_event ->> 'event_type';
    v_line_user_id := v_event ->> 'line_user_id';

    if (
      v_event_id !~ '^[A-Za-z0-9_-]{1,255}$'
      or v_event_type <> all (array['follow', 'unfollow'])
      or v_line_user_id !~ '^U[0-9a-f]{32}$'
    ) then
      raise exception 'LINE webhook event values are invalid.'
        using errcode = '22023';
    end if;

    begin
      v_occurred_at := (v_event ->> 'occurred_at')::timestamptz;
    exception
      when invalid_datetime_format or datetime_field_overflow then
        raise exception 'LINE webhook event timestamp is invalid.'
          using errcode = '22023';
    end;

    insert into public.line_webhook_events (
      webhook_event_id,
      event_type,
      occurred_at
    )
    values (
      v_event_id,
      v_event_type,
      v_occurred_at
    )
    on conflict (webhook_event_id) do nothing
    returning true into v_inserted;

    if coalesce(v_inserted, false) then
      v_inserted_event_count := v_inserted_event_count + 1;

      update public.line_account_links as link
      set
        status = case v_event_type
          when 'follow' then 'active'::public.line_account_link_status
          else 'blocked'::public.line_account_link_status
        end,
        last_webhook_at = v_occurred_at
      where link.line_user_id = v_line_user_id
        and link.status <> 'unlinked'::public.line_account_link_status
        and (
          link.last_webhook_at is null
          or link.last_webhook_at <= v_occurred_at
        );

      get diagnostics v_updated_count = row_count;
      v_updated_link_count := v_updated_link_count + v_updated_count;
    end if;

    v_inserted := false;
  end loop;

  return query select
    v_relevant_event_count,
    v_inserted_event_count,
    v_updated_link_count;
end;
$$;

create function public.enqueue_line_notification_candidates(
  p_candidates jsonb,
  p_shadow_mode boolean,
  p_canary_user_id uuid,
  p_allow_all boolean
)
returns table (
  candidate_count integer,
  eligible_candidate_count integer,
  inserted_delivery_item_count integer,
  inserted_message_count integer,
  linked_item_count integer,
  shadow_mode boolean
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_candidate pg_catalog.jsonb;
  v_rule_id_value pg_catalog.jsonb;
  v_candidate_count pg_catalog.int4;
  v_available_date pg_catalog.date;
  v_start_time pg_catalog.time;
  v_end_time pg_catalog.time;
  v_max_candidates constant pg_catalog.int4 := 500;
  v_max_matched_rules constant pg_catalog.int4 := 5;
  v_max_payload_bytes constant pg_catalog.int4 := 16384;
begin
  if (
    p_candidates is null
    or pg_catalog.jsonb_typeof(p_candidates) <> 'array'
    or p_shadow_mode is null
    or p_allow_all is null
    or (p_allow_all and p_canary_user_id is not null)
    or (not p_shadow_mode and not p_allow_all and p_canary_user_id is null)
  ) then
    raise exception 'LINE notification enqueue controls are invalid.'
      using errcode = '22023';
  end if;

  v_candidate_count := pg_catalog.jsonb_array_length(p_candidates);
  if v_candidate_count > v_max_candidates then
    raise exception 'LINE notification candidate batch exceeds 500 items.'
      using errcode = '22023';
  end if;

  for v_candidate in
    select candidate.value
    from pg_catalog.jsonb_array_elements(p_candidates) as candidate(value)
  loop
    if (
      pg_catalog.jsonb_typeof(v_candidate) <> 'object'
      or not (
        v_candidate ?& array[
          'user_id',
          'channel',
          'slot_id',
          'facility_id',
          'facility_name',
          'available_date',
          'start_time',
          'end_time',
          'matched_rule_ids',
          'payload'
        ]::pg_catalog.text[]
      )
      or (
        select pg_catalog.count(*)
        from pg_catalog.jsonb_object_keys(v_candidate)
      ) <> 10
      or pg_catalog.jsonb_typeof(v_candidate -> 'user_id') <> 'string'
      or pg_catalog.jsonb_typeof(v_candidate -> 'channel') <> 'string'
      or pg_catalog.jsonb_typeof(v_candidate -> 'slot_id') <> 'string'
      or pg_catalog.jsonb_typeof(v_candidate -> 'facility_id') <> 'string'
      or pg_catalog.jsonb_typeof(v_candidate -> 'facility_name') <> 'string'
      or pg_catalog.jsonb_typeof(v_candidate -> 'available_date') <> 'string'
      or pg_catalog.jsonb_typeof(v_candidate -> 'start_time') <> 'string'
      or pg_catalog.jsonb_typeof(v_candidate -> 'end_time') <> 'string'
      or pg_catalog.jsonb_typeof(v_candidate -> 'matched_rule_ids') <> 'array'
      or pg_catalog.jsonb_typeof(v_candidate -> 'payload') <> 'object'
    ) then
      raise exception 'LINE notification candidate shape is invalid.'
        using errcode = '22023';
    end if;

    if (
      v_candidate ->> 'channel' <> 'line'
      or pg_catalog.btrim(v_candidate ->> 'slot_id') = ''
      or pg_catalog.char_length(v_candidate ->> 'slot_id') > 200
      or pg_catalog.btrim(v_candidate ->> 'facility_id') = ''
      or pg_catalog.char_length(v_candidate ->> 'facility_id') > 100
      or pg_catalog.btrim(v_candidate ->> 'facility_name') = ''
      or pg_catalog.char_length(v_candidate ->> 'facility_name') > 200
      or (v_candidate ->> 'available_date')
        !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
      or (v_candidate ->> 'start_time')
        !~ '^[0-2][0-9]:[0-5][0-9](:[0-5][0-9])?$'
      or (v_candidate ->> 'end_time')
        !~ '^[0-2][0-9]:[0-5][0-9](:[0-5][0-9])?$'
      or pg_catalog.jsonb_array_length(v_candidate -> 'matched_rule_ids') < 1
      or pg_catalog.jsonb_array_length(v_candidate -> 'matched_rule_ids')
        > v_max_matched_rules
      or pg_catalog.octet_length((v_candidate -> 'payload')::text)
        > v_max_payload_bytes
      or not public.notification_email_payload_is_valid(
        v_candidate -> 'payload'
      )
    ) then
      raise exception 'LINE notification candidate values are invalid.'
        using errcode = '22023';
    end if;

    begin
      perform (v_candidate ->> 'user_id')::uuid;
      v_available_date := (v_candidate ->> 'available_date')::date;
      v_start_time := (v_candidate ->> 'start_time')::time;
      v_end_time := (v_candidate ->> 'end_time')::time;
    exception
      when invalid_text_representation or datetime_field_overflow then
        raise exception 'LINE notification candidate types are invalid.'
          using errcode = '22023';
    end;

    if (
      v_available_date::text <> v_candidate ->> 'available_date'
      or v_start_time >= v_end_time
    ) then
      raise exception 'LINE notification candidate schedule is invalid.'
        using errcode = '22023';
    end if;

    for v_rule_id_value in
      select rule_id.value
      from pg_catalog.jsonb_array_elements(
        v_candidate -> 'matched_rule_ids'
      ) as rule_id(value)
    loop
      if pg_catalog.jsonb_typeof(v_rule_id_value) <> 'string' then
        raise exception 'Matched notification rule IDs must be UUID strings.'
          using errcode = '22023';
      end if;
      begin
        perform (v_rule_id_value #>> '{}')::uuid;
      exception
        when invalid_text_representation then
          raise exception 'Matched notification rule IDs are invalid.'
            using errcode = '22023';
      end;
    end loop;

    if not exists (
      select 1
      from public.facilities as facility
      where facility.id = v_candidate ->> 'facility_id'
        and facility.name = pg_catalog.btrim(v_candidate ->> 'facility_name')
        and facility.is_active = true
    ) then
      raise exception 'LINE notification candidate facility is invalid.'
        using errcode = '22023';
    end if;
  end loop;

  if exists (
    with normalized_candidates as (
      select
        (candidate_input.candidate ->> 'user_id')::uuid as user_id,
        pg_catalog.btrim(candidate_input.candidate ->> 'slot_id') as slot_id,
        pg_catalog.jsonb_build_object(
          'facility_id', candidate_input.candidate ->> 'facility_id',
          'facility_name', pg_catalog.btrim(
            candidate_input.candidate ->> 'facility_name'
          ),
          'available_date',
            (candidate_input.candidate ->> 'available_date')::date,
          'start_time', (candidate_input.candidate ->> 'start_time')::time,
          'end_time', (candidate_input.candidate ->> 'end_time')::time,
          'payload', candidate_input.candidate -> 'payload'
        ) as snapshot
      from pg_catalog.jsonb_array_elements(p_candidates)
        as candidate_input(candidate)
    )
    select 1
    from normalized_candidates as candidate
    group by candidate.user_id, candidate.slot_id
    having pg_catalog.count(distinct candidate.snapshot) > 1
  ) then
    raise exception
      'Duplicate LINE notification candidates have conflicting snapshots.'
      using errcode = '22023';
  end if;

  return query
  with parsed_candidates as materialized (
    select
      candidate_input.ordinality,
      (candidate_input.candidate ->> 'user_id')::uuid as user_id,
      'line'::public.notification_channel as channel,
      pg_catalog.btrim(candidate_input.candidate ->> 'slot_id') as slot_id,
      candidate_input.candidate ->> 'facility_id' as facility_id,
      pg_catalog.btrim(candidate_input.candidate ->> 'facility_name')
        as facility_name,
      (candidate_input.candidate ->> 'available_date')::date
        as available_date,
      (candidate_input.candidate ->> 'start_time')::time as start_time,
      (candidate_input.candidate ->> 'end_time')::time as end_time,
      array(
        select distinct (rule_id.value #>> '{}')::uuid
        from pg_catalog.jsonb_array_elements(
          candidate_input.candidate -> 'matched_rule_ids'
        ) as rule_id(value)
        order by (rule_id.value #>> '{}')::uuid
      ) as matched_rule_ids,
      candidate_input.candidate -> 'payload' as payload
    from pg_catalog.jsonb_array_elements(p_candidates)
      with ordinality as candidate_input(candidate, ordinality)
  ),
  candidate_snapshots as materialized (
    select distinct on (parsed.user_id, parsed.slot_id)
      parsed.*
    from parsed_candidates as parsed
    order by parsed.user_id, parsed.slot_id, parsed.ordinality
  ),
  aggregated_candidate_rules as materialized (
    select
      parsed.user_id,
      parsed.slot_id,
      pg_catalog.array_agg(
        distinct matched_rule.rule_id order by matched_rule.rule_id
      ) as matched_rule_ids
    from parsed_candidates as parsed
    cross join lateral pg_catalog.unnest(
      parsed.matched_rule_ids
    ) as matched_rule(rule_id)
    group by parsed.user_id, parsed.slot_id
  ),
  deduplicated_candidates as materialized (
    select
      snapshot.user_id,
      snapshot.channel,
      snapshot.slot_id,
      snapshot.facility_id,
      snapshot.facility_name,
      snapshot.available_date,
      snapshot.start_time,
      snapshot.end_time,
      matched_rules.matched_rule_ids,
      snapshot.payload
    from candidate_snapshots as snapshot
    inner join aggregated_candidate_rules as matched_rules
      on matched_rules.user_id = snapshot.user_id
      and matched_rules.slot_id = snapshot.slot_id
  ),
  eligible_candidates as materialized (
    select
      candidate.user_id,
      candidate.channel,
      candidate.slot_id,
      candidate.facility_id,
      candidate.facility_name,
      candidate.available_date,
      candidate.start_time,
      candidate.end_time,
      enabled_rules.matched_rule_ids,
      candidate.payload
    from deduplicated_candidates as candidate
    inner join public.profiles as profile
      on profile.id = candidate.user_id
      and profile.membership_status = 'active'::public.membership_status
    inner join public.line_account_links as link
      on link.user_id = candidate.user_id
      and link.status = 'active'::public.line_account_link_status
    cross join lateral (
      select pg_catalog.array_agg(
        notification_rule.id order by notification_rule.id
      ) as matched_rule_ids
      from public.notification_rules as notification_rule
      where notification_rule.user_id = candidate.user_id
        and notification_rule.is_enabled = true
        and notification_rule.id = any (candidate.matched_rule_ids)
    ) as enabled_rules
    where pg_catalog.cardinality(enabled_rules.matched_rule_ids) >= 1
      and (
        p_canary_user_id is null
        or candidate.user_id = p_canary_user_id
      )
  ),
  inserted_delivery_items as (
    insert into public.notification_delivery_items (
      user_id,
      channel,
      slot_id,
      facility_id,
      facility_name,
      available_date,
      start_time,
      end_time,
      matched_rule_ids,
      payload
    )
    select
      candidate.user_id,
      candidate.channel,
      candidate.slot_id,
      candidate.facility_id,
      candidate.facility_name,
      candidate.available_date,
      candidate.start_time,
      candidate.end_time,
      candidate.matched_rule_ids,
      candidate.payload
    from eligible_candidates as candidate
    where not p_shadow_mode
    on conflict (user_id, channel, slot_id) do nothing
    returning id, user_id, channel
  ),
  inserted_messages as (
    insert into public.notification_messages (
      user_id,
      channel,
      status,
      next_attempt_at
    )
    select
      delivery_item.user_id,
      delivery_item.channel,
      'pending'::public.notification_message_status,
      pg_catalog.now()
    from inserted_delivery_items as delivery_item
    group by delivery_item.user_id, delivery_item.channel
    returning id, user_id, channel
  ),
  inserted_links as (
    insert into public.notification_message_items (
      message_id,
      delivery_item_id,
      user_id,
      channel
    )
    select
      message.id,
      delivery_item.id,
      delivery_item.user_id,
      delivery_item.channel
    from inserted_delivery_items as delivery_item
    inner join inserted_messages as message
      on message.user_id = delivery_item.user_id
      and message.channel = delivery_item.channel
    returning message_id
  )
  select
    v_candidate_count,
    (select pg_catalog.count(*)::int from eligible_candidates),
    (select pg_catalog.count(*)::int from inserted_delivery_items),
    (select pg_catalog.count(*)::int from inserted_messages),
    (select pg_catalog.count(*)::int from inserted_links),
    p_shadow_mode;
end;
$$;

-- The Phase 3 implementation predated the LINE enum value. Constrain its
-- claim scan explicitly so a future LINE row can never reach the email worker.
create or replace function public.claim_email_messages(
  batch_size integer
)
returns table (
  message_id uuid,
  user_id uuid,
  channel public.notification_channel,
  attempt_count integer,
  locked_until timestamptz,
  items jsonb
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_max_batch_size constant pg_catalog.int4 := 100;
  v_max_attempts constant pg_catalog.int4 := 5;
  v_provider_safety_window constant interval := interval '23 hours';
begin
  if batch_size is null or batch_size < 1 or batch_size > v_max_batch_size then
    raise exception 'Email message claim batch size is invalid.'
      using errcode = '22023';
  end if;

  with ineligible_messages as materialized (
    select message.id
    from public.notification_messages as message
    where message.channel = 'email'::public.notification_channel
      and (
        message.status in ('pending', 'retry_wait')
        or (
          message.status = 'processing'::public.notification_message_status
          and message.locked_until <= pg_catalog.now()
        )
      )
      and not exists (
        select 1
        from public.profiles as profile
        inner join public.notification_email_preferences as preference
          on preference.user_id = profile.id
        where profile.id = message.user_id
          and profile.membership_status = 'active'::public.membership_status
          and preference.is_enabled = true
          and preference.disabled_reason is null
      )
    order by message.next_attempt_at, message.created_at, message.id
    for update of message skip locked
    limit batch_size
  )
  update public.notification_messages as message
  set
    status = 'cancelled'::public.notification_message_status,
    locked_at = null,
    locked_until = null,
    last_error_code = null,
    last_error_message = null
  from ineligible_messages as ineligible
  where message.id = ineligible.id;

  with exhausted_messages as materialized (
    select message.id
    from public.notification_messages as message
    inner join public.profiles as profile
      on profile.id = message.user_id
      and profile.membership_status = 'active'::public.membership_status
    inner join public.notification_email_preferences as preference
      on preference.user_id = message.user_id
      and preference.is_enabled = true
      and preference.disabled_reason is null
    where message.channel = 'email'::public.notification_channel
      and (
        message.status in ('pending', 'retry_wait')
        or (
          message.status = 'processing'::public.notification_message_status
          and message.locked_until <= pg_catalog.now()
        )
      )
      and (
        message.attempt_count >= v_max_attempts
        or (
          message.provider_first_attempt_at is not null
          and message.provider_first_attempt_at + v_provider_safety_window
            <= pg_catalog.now()
        )
      )
    order by message.next_attempt_at, message.created_at, message.id
    for update of message skip locked
    limit batch_size
  )
  update public.notification_messages as message
  set
    status = 'failed_permanent'::public.notification_message_status,
    locked_at = null,
    locked_until = null,
    failed_at = pg_catalog.now(),
    last_error_code = case
      when message.attempt_count >= v_max_attempts
        then 'attempt_limit_exceeded'
      else 'idempotency_window_expired'
    end,
    last_error_message = null
  from exhausted_messages as exhausted
  where message.id = exhausted.id;

  return query
  with claimable_messages as materialized (
    select message.id
    from public.notification_messages as message
    inner join public.profiles as profile
      on profile.id = message.user_id
      and profile.membership_status = 'active'::public.membership_status
    inner join public.notification_email_preferences as preference
      on preference.user_id = message.user_id
      and preference.is_enabled = true
      and preference.disabled_reason is null
    where message.channel = 'email'::public.notification_channel
      and (
        (
          message.status in ('pending', 'retry_wait')
          and message.next_attempt_at <= pg_catalog.now()
          and (
            message.locked_until is null
            or message.locked_until <= pg_catalog.now()
          )
        )
        or (
          message.status = 'processing'::public.notification_message_status
          and message.locked_until <= pg_catalog.now()
        )
      )
      and message.attempt_count < v_max_attempts
      and (
        message.provider_first_attempt_at is null
        or message.provider_first_attempt_at + v_provider_safety_window
          > pg_catalog.now()
      )
    order by message.next_attempt_at, message.created_at, message.id
    for update of message skip locked
    limit batch_size
  ),
  claimed_messages as (
    update public.notification_messages as message
    set
      status = 'processing'::public.notification_message_status,
      attempt_count = message.attempt_count + 1,
      locked_at = pg_catalog.now(),
      locked_until = pg_catalog.now() + interval '5 minutes'
    from claimable_messages as claimable
    where message.id = claimable.id
    returning
      message.id,
      message.user_id,
      message.channel,
      message.attempt_count,
      message.locked_until
  )
  select
    claimed.id,
    claimed.user_id,
    claimed.channel,
    claimed.attempt_count,
    claimed.locked_until,
    pg_catalog.jsonb_agg(
      pg_catalog.jsonb_build_object(
        'facility_name', delivery_item.facility_name,
        'available_date', delivery_item.available_date,
        'start_time', delivery_item.start_time,
        'end_time', delivery_item.end_time,
        'payload', delivery_item.payload
      )
      order by
        delivery_item.available_date,
        delivery_item.start_time,
        delivery_item.end_time,
        delivery_item.facility_name,
        delivery_item.payload::text,
        delivery_item.id
    )
  from claimed_messages as claimed
  inner join public.notification_message_items as message_item
    on message_item.message_id = claimed.id
  inner join public.notification_delivery_items as delivery_item
    on delivery_item.id = message_item.delivery_item_id
  group by
    claimed.id,
    claimed.user_id,
    claimed.channel,
    claimed.attempt_count,
    claimed.locked_until
  order by claimed.locked_until, claimed.id;
end;
$$;

create function public.claim_line_messages(
  batch_size integer
)
returns table (
  message_id uuid,
  user_id uuid,
  line_user_id text,
  channel public.notification_channel,
  attempt_count integer,
  locked_until timestamptz,
  items jsonb
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_max_batch_size constant pg_catalog.int4 := 10;
  v_max_attempts constant pg_catalog.int4 := 5;
  v_provider_safety_window constant interval := interval '23 hours';
begin
  if batch_size is null or batch_size < 1 or batch_size > v_max_batch_size then
    raise exception 'LINE message claim batch size is invalid.'
      using errcode = '22023';
  end if;

  with ineligible_messages as materialized (
    select message.id
    from public.notification_messages as message
    where message.channel = 'line'::public.notification_channel
      and (
        message.status in ('pending', 'retry_wait')
        or (
          message.status = 'processing'::public.notification_message_status
          and message.locked_until <= pg_catalog.now()
        )
      )
      and not exists (
        select 1
        from public.profiles as profile
        inner join public.line_account_links as link
          on link.user_id = profile.id
        where profile.id = message.user_id
          and profile.membership_status = 'active'::public.membership_status
          and link.status = 'active'::public.line_account_link_status
      )
    order by message.next_attempt_at, message.created_at, message.id
    for update of message skip locked
    limit batch_size
  )
  update public.notification_messages as message
  set
    status = 'cancelled'::public.notification_message_status,
    locked_at = null,
    locked_until = null,
    last_error_code = null,
    last_error_message = null
  from ineligible_messages as ineligible
  where message.id = ineligible.id;

  with exhausted_messages as materialized (
    select message.id
    from public.notification_messages as message
    inner join public.profiles as profile
      on profile.id = message.user_id
      and profile.membership_status = 'active'::public.membership_status
    inner join public.line_account_links as link
      on link.user_id = message.user_id
      and link.status = 'active'::public.line_account_link_status
    where message.channel = 'line'::public.notification_channel
      and (
        message.status in ('pending', 'retry_wait')
        or (
          message.status = 'processing'::public.notification_message_status
          and message.locked_until <= pg_catalog.now()
        )
      )
      and (
        message.attempt_count >= v_max_attempts
        or (
          message.provider_first_attempt_at is not null
          and message.provider_first_attempt_at + v_provider_safety_window
            <= pg_catalog.now()
        )
      )
    order by message.next_attempt_at, message.created_at, message.id
    for update of message skip locked
    limit batch_size
  )
  update public.notification_messages as message
  set
    status = 'failed_permanent'::public.notification_message_status,
    locked_at = null,
    locked_until = null,
    failed_at = pg_catalog.now(),
    last_error_code = case
      when message.attempt_count >= v_max_attempts
        then 'attempt_limit_exceeded'
      else 'idempotency_window_expired'
    end,
    last_error_message = null
  from exhausted_messages as exhausted
  where message.id = exhausted.id;

  return query
  with claimable_messages as materialized (
    select message.id
    from public.notification_messages as message
    inner join public.profiles as profile
      on profile.id = message.user_id
      and profile.membership_status = 'active'::public.membership_status
    inner join public.line_account_links as link
      on link.user_id = message.user_id
      and link.status = 'active'::public.line_account_link_status
    where message.channel = 'line'::public.notification_channel
      and (
        (
          message.status in ('pending', 'retry_wait')
          and message.next_attempt_at <= pg_catalog.now()
          and (
            message.locked_until is null
            or message.locked_until <= pg_catalog.now()
          )
        )
        or (
          message.status = 'processing'::public.notification_message_status
          and message.locked_until <= pg_catalog.now()
        )
      )
      and message.attempt_count < v_max_attempts
      and (
        message.provider_first_attempt_at is null
        or message.provider_first_attempt_at + v_provider_safety_window
          > pg_catalog.now()
      )
    order by message.next_attempt_at, message.created_at, message.id
    for update of message skip locked
    limit batch_size
  ),
  claimed_messages as (
    update public.notification_messages as message
    set
      status = 'processing'::public.notification_message_status,
      attempt_count = message.attempt_count + 1,
      locked_at = pg_catalog.now(),
      locked_until = pg_catalog.now() + interval '5 minutes'
    from claimable_messages as claimable
    where message.id = claimable.id
    returning
      message.id,
      message.user_id,
      message.channel,
      message.attempt_count,
      message.locked_until
  )
  select
    claimed.id,
    claimed.user_id,
    link.line_user_id,
    claimed.channel,
    claimed.attempt_count,
    claimed.locked_until,
    pg_catalog.jsonb_agg(
      pg_catalog.jsonb_build_object(
        'facility_name', delivery_item.facility_name,
        'available_date', delivery_item.available_date,
        'start_time', delivery_item.start_time,
        'end_time', delivery_item.end_time,
        'payload', delivery_item.payload
      )
      order by
        delivery_item.available_date,
        delivery_item.start_time,
        delivery_item.end_time,
        delivery_item.facility_name,
        delivery_item.payload::text,
        delivery_item.id
    )
  from claimed_messages as claimed
  inner join public.line_account_links as link
    on link.user_id = claimed.user_id
    and link.status = 'active'::public.line_account_link_status
  inner join public.notification_message_items as message_item
    on message_item.message_id = claimed.id
  inner join public.notification_delivery_items as delivery_item
    on delivery_item.id = message_item.delivery_item_id
  group by
    claimed.id,
    claimed.user_id,
    link.line_user_id,
    claimed.channel,
    claimed.attempt_count,
    claimed.locked_until
  order by claimed.locked_until, claimed.id;
end;
$$;

create function public.authorize_line_message_send(
  p_message_id uuid,
  p_locked_until timestamptz,
  p_line_user_id text,
  p_provider_payload_fingerprint text
)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_message public.notification_messages%rowtype;
  v_max_attempts constant pg_catalog.int4 := 5;
  v_provider_safety_window constant interval := interval '23 hours';
begin
  if (
    p_message_id is null
    or p_locked_until is null
    or p_line_user_id is null
    or p_line_user_id !~ '^U[0-9a-f]{32}$'
    or p_provider_payload_fingerprint is null
    or p_provider_payload_fingerprint !~ '^[0-9a-f]{64}$'
  ) then
    raise exception 'LINE send authorization input is invalid.'
      using errcode = '22023';
  end if;

  select message.*
  into v_message
  from public.notification_messages as message
  where message.id = p_message_id
    and message.channel = 'line'::public.notification_channel
    and message.status = 'processing'::public.notification_message_status
    and message.locked_until = p_locked_until
    and message.locked_until > pg_catalog.now()
  for update;

  if not found then
    return 'stale';
  end if;

  if not exists (
    select 1
    from public.profiles as profile
    inner join public.line_account_links as link
      on link.user_id = profile.id
    where profile.id = v_message.user_id
      and profile.membership_status = 'active'::public.membership_status
      and link.status = 'active'::public.line_account_link_status
      and link.line_user_id = p_line_user_id
  ) then
    update public.notification_messages as message
    set
      status = 'cancelled'::public.notification_message_status,
      locked_at = null,
      locked_until = null,
      last_error_code = null,
      last_error_message = null
    where message.id = v_message.id;
    return 'cancelled';
  end if;

  if v_message.attempt_count > v_max_attempts then
    update public.notification_messages as message
    set
      status = 'failed_permanent'::public.notification_message_status,
      locked_at = null,
      locked_until = null,
      failed_at = pg_catalog.now(),
      last_error_code = 'attempt_limit_exceeded',
      last_error_message = null
    where message.id = v_message.id;
    return 'failed_permanent';
  end if;

  if (
    v_message.provider_first_attempt_at is not null
    and v_message.provider_first_attempt_at + v_provider_safety_window
      <= pg_catalog.now()
  ) then
    update public.notification_messages as message
    set
      status = 'failed_permanent'::public.notification_message_status,
      locked_at = null,
      locked_until = null,
      failed_at = pg_catalog.now(),
      last_error_code = 'idempotency_window_expired',
      last_error_message = null
    where message.id = v_message.id;
    return 'failed_permanent';
  end if;

  if (
    v_message.provider_payload_fingerprint is not null
    and v_message.provider_payload_fingerprint
      <> p_provider_payload_fingerprint
  ) then
    update public.notification_messages as message
    set
      status = 'failed_permanent'::public.notification_message_status,
      locked_at = null,
      locked_until = null,
      failed_at = pg_catalog.now(),
      last_error_code = 'provider_payload_changed',
      last_error_message = null
    where message.id = v_message.id;
    return 'failed_permanent';
  end if;

  update public.notification_messages as message
  set
    provider_first_attempt_at = coalesce(
      message.provider_first_attempt_at,
      pg_catalog.now()
    ),
    provider_payload_fingerprint = coalesce(
      message.provider_payload_fingerprint,
      p_provider_payload_fingerprint
    )
  where message.id = v_message.id;

  return 'authorized';
end;
$$;

create function public.record_line_message_accepted(
  p_message_id uuid,
  p_locked_until timestamptz,
  p_provider_message_id text,
  p_provider_status text
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_updated_count pg_catalog.int4;
begin
  if (
    p_message_id is null
    or p_locked_until is null
    or p_provider_message_id is null
    or p_provider_message_id !~ '^line:(message|request):[A-Za-z0-9_-]{1,220}$'
    or p_provider_status is null
    or p_provider_status <> all (array['accepted', 'accepted_retry'])
  ) then
    raise exception 'Accepted LINE result input is invalid.'
      using errcode = '22023';
  end if;

  update public.notification_messages as message
  set
    status = 'accepted'::public.notification_message_status,
    provider_message_id = p_provider_message_id,
    provider_status = p_provider_status,
    accepted_at = pg_catalog.now(),
    failed_at = null,
    locked_at = null,
    locked_until = null,
    last_error_code = null,
    last_error_message = null
  where message.id = p_message_id
    and message.channel = 'line'::public.notification_channel
    and message.status = 'processing'::public.notification_message_status
    and message.locked_until = p_locked_until
    and message.locked_until > pg_catalog.now()
    and message.provider_first_attempt_at is not null
    and message.provider_payload_fingerprint is not null;

  get diagnostics v_updated_count = row_count;
  return v_updated_count = 1;
end;
$$;

create function public.record_line_message_failure(
  p_message_id uuid,
  p_locked_until timestamptz,
  p_error_code text
)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_message public.notification_messages%rowtype;
  v_retryable pg_catalog.bool;
  v_delay_seconds pg_catalog.int4;
  v_retry_at timestamptz;
  v_final_error_code pg_catalog.text;
  v_max_attempts constant pg_catalog.int4 := 5;
  v_provider_safety_window constant interval := interval '23 hours';
begin
  if (
    p_message_id is null
    or p_locked_until is null
    or p_error_code is null
    or p_error_code <> all (
      array[
        'worker_internal_error',
        'line_network_error',
        'line_server_error',
        'line_rate_limited',
        'line_unexpected_response',
        'line_invalid_access_token',
        'line_invalid_recipient_or_payload',
        'line_quota_exceeded',
        'line_client_error'
      ]::text[]
    )
  ) then
    raise exception 'LINE failure result input is invalid.'
      using errcode = '22023';
  end if;

  select message.*
  into v_message
  from public.notification_messages as message
  where message.id = p_message_id
    and message.channel = 'line'::public.notification_channel
    and message.status = 'processing'::public.notification_message_status
    and message.locked_until = p_locked_until
    and message.locked_until > pg_catalog.now()
  for update;

  if not found then
    return 'stale';
  end if;

  v_retryable := p_error_code = any (
    array[
      'line_network_error',
      'line_server_error'
    ]::text[]
  );

  if v_retryable and v_message.attempt_count < v_max_attempts then
    v_delay_seconds := case v_message.attempt_count
      when 1 then 60
      when 2 then 120
      when 3 then 300
      else 900
    end + pg_catalog.floor(pg_catalog.random() * 31)::int;
    v_retry_at := pg_catalog.now()
      + pg_catalog.make_interval(secs => v_delay_seconds);

    if (
      v_message.provider_first_attempt_at is null
      or v_retry_at < v_message.provider_first_attempt_at
        + v_provider_safety_window
    ) then
      update public.notification_messages as message
      set
        status = 'retry_wait'::public.notification_message_status,
        next_attempt_at = v_retry_at,
        locked_at = null,
        locked_until = null,
        failed_at = null,
        last_error_code = p_error_code,
        last_error_message = null
      where message.id = v_message.id;
      return 'retry_wait';
    end if;
  end if;

  v_final_error_code := case
    when v_retryable and v_message.attempt_count >= v_max_attempts
      then 'attempt_limit_exceeded'
    when v_retryable
      and v_message.provider_first_attempt_at is not null
      and v_retry_at >= v_message.provider_first_attempt_at
        + v_provider_safety_window
      then 'idempotency_window_expired'
    else p_error_code
  end;

  update public.notification_messages as message
  set
    status = 'failed_permanent'::public.notification_message_status,
    locked_at = null,
    locked_until = null,
    failed_at = pg_catalog.now(),
    last_error_code = v_final_error_code,
    last_error_message = null
  where message.id = v_message.id;

  return 'failed_permanent';
end;
$$;

create function public.cancel_line_notification_backlog()
returns table (
  cancelled_count integer,
  active_processing_count integer
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_cancelled_count pg_catalog.int4;
  v_active_processing_count pg_catalog.int4;
begin
  update public.notification_messages as message
  set
    status = 'cancelled'::public.notification_message_status,
    locked_at = null,
    locked_until = null,
    last_error_code = 'rollout_cancelled',
    last_error_message = null
  where message.channel = 'line'::public.notification_channel
    and (
      message.status in ('pending', 'retry_wait')
      or (
        message.status = 'processing'::public.notification_message_status
        and message.locked_until <= pg_catalog.now()
      )
    );

  get diagnostics v_cancelled_count = row_count;

  select pg_catalog.count(*)::int
  into v_active_processing_count
  from public.notification_messages as message
  where message.channel = 'line'::public.notification_channel
    and message.status = 'processing'::public.notification_message_status
    and message.locked_until > pg_catalog.now();

  return query select v_cancelled_count, v_active_processing_count;
end;
$$;

create function public.cleanup_line_webhook_events(
  batch_size integer default 1000
)
returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_deleted_count pg_catalog.int4;
begin
  if batch_size is null or batch_size < 1 or batch_size > 1000 then
    raise exception 'LINE webhook cleanup batch size is invalid.'
      using errcode = '22023';
  end if;

  with expired_events as materialized (
    select event.webhook_event_id
    from public.line_webhook_events as event
    where event.created_at < pg_catalog.statement_timestamp() - interval '90 days'
    order by event.created_at, event.webhook_event_id
    for update of event skip locked
    limit batch_size
  )
  delete from public.line_webhook_events as event
  using expired_events as expired
  where event.webhook_event_id = expired.webhook_event_id;

  get diagnostics v_deleted_count = row_count;
  return v_deleted_count;
end;
$$;

grant select, insert, update on table
  public.notification_delivery_items,
  public.notification_messages,
  public.notification_message_items
to service_role;

grant select on table
  public.profiles,
  public.notification_rules,
  public.line_account_links,
  public.facilities
to service_role;

grant execute on function public.notification_email_payload_is_valid(jsonb)
to service_role;

revoke all on function public.record_line_webhook_events(jsonb)
from public, anon, authenticated, service_role;
grant execute on function public.record_line_webhook_events(jsonb)
to service_role;

revoke all on function public.enqueue_line_notification_candidates(
  jsonb,
  boolean,
  uuid,
  boolean
)
from public, anon, authenticated, service_role;
grant execute on function public.enqueue_line_notification_candidates(
  jsonb,
  boolean,
  uuid,
  boolean
)
to service_role;

revoke all on function public.claim_line_messages(integer)
from public, anon, authenticated, service_role;
grant execute on function public.claim_line_messages(integer)
to service_role;

revoke all on function public.authorize_line_message_send(
  uuid,
  timestamptz,
  text,
  text
)
from public, anon, authenticated, service_role;
grant execute on function public.authorize_line_message_send(
  uuid,
  timestamptz,
  text,
  text
)
to service_role;

revoke all on function public.record_line_message_accepted(
  uuid,
  timestamptz,
  text,
  text
)
from public, anon, authenticated, service_role;
grant execute on function public.record_line_message_accepted(
  uuid,
  timestamptz,
  text,
  text
)
to service_role;

revoke all on function public.record_line_message_failure(
  uuid,
  timestamptz,
  text
)
from public, anon, authenticated, service_role;
grant execute on function public.record_line_message_failure(
  uuid,
  timestamptz,
  text
)
to service_role;

revoke all on function public.cancel_line_notification_backlog()
from public, anon, authenticated, service_role;
grant execute on function public.cancel_line_notification_backlog()
to service_role;

revoke all on function public.cleanup_line_webhook_events(integer)
from public, anon, authenticated, service_role;
grant execute on function public.cleanup_line_webhook_events(integer)
to service_role;

comment on table public.line_webhook_events is
  'Deduplicated LINE follow/unfollow event IDs without raw payloads or user IDs.';
comment on function public.record_line_webhook_events(jsonb) is
  'Idempotently applies signed LINE follow/unfollow events to active links.';
comment on function public.enqueue_line_notification_candidates(
  jsonb,
  boolean,
  uuid,
  boolean
) is
  'Validates LINE candidates and supports no-write shadow and canary rollout.';
comment on function public.claim_line_messages(integer) is
  'Claims bounded LINE batches for active linked members only.';
comment on function public.authorize_line_message_send(
  uuid,
  timestamptz,
  text,
  text
) is
  'Rechecks the exact LINE recipient, lease, and payload before push.';
comment on function public.record_line_message_accepted(
  uuid,
  timestamptz,
  text,
  text
) is
  'Records normalized LINE acceptance for the exact current lease.';
comment on function public.record_line_message_failure(
  uuid,
  timestamptz,
  text
) is
  'Records allowlisted LINE failure codes with bounded retries.';
comment on function public.cancel_line_notification_backlog() is
  'Cancels unsent LINE backlog while reporting any still-active leases.';
comment on function public.cleanup_line_webhook_events(integer) is
  'Deletes deduplication-only LINE webhook history older than 90 days.';
comment on function public.cleanup_email_notification_history(integer) is
  'Deletes old stable notification history for all channels in bounded batches.';

commit;
