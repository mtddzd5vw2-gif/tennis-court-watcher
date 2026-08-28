-- Phase 4 limited beta: server-side LINE rollout allowlist.
-- The allowlist is private, capped at 20 members, and is enforced at enqueue,
-- worker claim, and immediately before each provider request.

begin;

create schema if not exists private;

revoke all on schema private from public;
grant usage on schema private to service_role;

create table private.line_notification_beta_allowlist (
  user_id uuid primary key
    references public.profiles(id) on delete cascade,
  created_at timestamptz not null default pg_catalog.statement_timestamp()
);

alter table private.line_notification_beta_allowlist
  enable row level security;
alter table private.line_notification_beta_allowlist
  force row level security;

revoke all privileges on table private.line_notification_beta_allowlist
from public, anon, authenticated, service_role;
grant select on table private.line_notification_beta_allowlist
to service_role;

create function public.replace_line_notification_beta_allowlist(
  p_user_ids uuid[]
)
returns table (
  allowlisted_count integer,
  cancelled_message_count integer
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_allowlisted_count pg_catalog.int4;
  v_cancelled_message_count pg_catalog.int4;
  v_max_allowlisted_members constant pg_catalog.int4 := 20;
begin
  if (
    p_user_ids is null
    or pg_catalog.cardinality(p_user_ids) > v_max_allowlisted_members
    or pg_catalog.array_position(p_user_ids, null) is not null
    or pg_catalog.cardinality(p_user_ids) <> (
      select pg_catalog.count(distinct requested.user_id)::int
      from pg_catalog.unnest(p_user_ids) as requested(user_id)
    )
  ) then
    raise exception 'LINE beta allowlist input is invalid.'
      using errcode = '22023';
  end if;

  perform pg_catalog.pg_advisory_xact_lock(20260828001723);

  if exists (
    select 1
    from public.notification_messages as message
    where message.channel = 'line'::public.notification_channel
      and message.status = 'processing'::public.notification_message_status
      and message.locked_until > pg_catalog.now()
  ) then
    raise exception 'LINE beta allowlist cannot change during an active lease.'
      using errcode = '55006';
  end if;

  if exists (
    select 1
    from pg_catalog.unnest(p_user_ids) as requested(user_id)
    left join public.profiles as profile
      on profile.id = requested.user_id
      and profile.membership_status = 'active'::public.membership_status
    where profile.id is null
  ) then
    raise exception 'LINE beta allowlist contains an ineligible member.'
      using errcode = '42501';
  end if;

  delete from private.line_notification_beta_allowlist;

  insert into private.line_notification_beta_allowlist (user_id)
  select requested.user_id
  from pg_catalog.unnest(p_user_ids) as requested(user_id)
  order by requested.user_id;

  get diagnostics v_allowlisted_count = row_count;

  update public.notification_messages as message
  set
    status = 'cancelled'::public.notification_message_status,
    locked_at = null,
    locked_until = null,
    last_error_code = 'rollout_allowlist_removed',
    last_error_message = null
  where message.channel = 'line'::public.notification_channel
    and message.status in ('pending', 'retry_wait', 'processing')
    and (
      message.status <> 'processing'::public.notification_message_status
      or message.locked_until <= pg_catalog.now()
    )
    and not exists (
      select 1
      from private.line_notification_beta_allowlist as allowlisted
      where allowlisted.user_id = message.user_id
    );

  get diagnostics v_cancelled_message_count = row_count;

  return query select v_allowlisted_count, v_cancelled_message_count;
end;
$$;

create function public.enqueue_line_notification_candidates(
  p_candidates jsonb,
  p_shadow_mode boolean,
  p_canary_user_id uuid,
  p_use_allowlist boolean,
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
  v_rollout_mode_count pg_catalog.int4;
  v_allowlisted_count pg_catalog.int4;
  v_available_date pg_catalog.date;
  v_start_time pg_catalog.time;
  v_end_time pg_catalog.time;
  v_max_candidates constant pg_catalog.int4 := 500;
  v_max_matched_rules constant pg_catalog.int4 := 5;
  v_max_payload_bytes constant pg_catalog.int4 := 16384;
  v_max_allowlisted_members constant pg_catalog.int4 := 20;
begin
  perform pg_catalog.pg_advisory_xact_lock_shared(20260828001723);

  if (
    p_candidates is null
    or pg_catalog.jsonb_typeof(p_candidates) <> 'array'
    or p_shadow_mode is null
    or p_use_allowlist is null
    or p_allow_all is null
  ) then
    raise exception 'LINE notification enqueue controls are invalid.'
      using errcode = '22023';
  end if;

  v_rollout_mode_count :=
    case when p_canary_user_id is null then 0 else 1 end
    + case when p_use_allowlist then 1 else 0 end
    + case when p_allow_all then 1 else 0 end;

  if (
    v_rollout_mode_count > 1
    or (not p_shadow_mode and v_rollout_mode_count <> 1)
  ) then
    raise exception 'LINE notification enqueue controls are invalid.'
      using errcode = '22023';
  end if;

  if p_use_allowlist then
    select pg_catalog.count(*)::int
    into v_allowlisted_count
    from private.line_notification_beta_allowlist;

    if (
      v_allowlisted_count < 1
      or v_allowlisted_count > v_max_allowlisted_members
    ) then
      raise exception 'LINE beta allowlist is empty or exceeds 20 members.'
        using errcode = '22023';
    end if;
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
        v_rollout_mode_count = 0
        or p_allow_all
        or candidate.user_id = p_canary_user_id
        or (
          p_use_allowlist
          and exists (
            select 1
            from private.line_notification_beta_allowlist as allowlisted
            where allowlisted.user_id = candidate.user_id
          )
        )
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

create function public.claim_line_messages(
  batch_size integer,
  p_canary_user_id uuid,
  p_use_allowlist boolean,
  p_allow_all boolean
)
returns table (
  message_id uuid,
  user_id uuid,
  line_user_id text,
  channel public.notification_channel,
  attempt_count integer,
  locked_until timestamptz,
  test_text text,
  items jsonb
)
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_rollout_mode_count pg_catalog.int4;
  v_allowlisted_count pg_catalog.int4;
  v_max_batch_size constant pg_catalog.int4 := 10;
  v_max_attempts constant pg_catalog.int4 := 5;
  v_max_allowlisted_members constant pg_catalog.int4 := 20;
  v_provider_safety_window constant interval := interval '23 hours';
begin
  perform pg_catalog.pg_advisory_xact_lock_shared(20260828001723);

  if (
    batch_size is null
    or batch_size < 1
    or batch_size > v_max_batch_size
    or p_use_allowlist is null
    or p_allow_all is null
  ) then
    raise exception 'LINE message claim controls are invalid.'
      using errcode = '22023';
  end if;

  v_rollout_mode_count :=
    case when p_canary_user_id is null then 0 else 1 end
    + case when p_use_allowlist then 1 else 0 end
    + case when p_allow_all then 1 else 0 end;

  if v_rollout_mode_count <> 1 then
    raise exception 'LINE message claim controls are invalid.'
      using errcode = '22023';
  end if;

  if p_use_allowlist then
    select pg_catalog.count(*)::int
    into v_allowlisted_count
    from private.line_notification_beta_allowlist;

    if (
      v_allowlisted_count < 1
      or v_allowlisted_count > v_max_allowlisted_members
    ) then
      raise exception 'LINE beta allowlist is empty or exceeds 20 members.'
        using errcode = '22023';
    end if;
  end if;

  with ineligible_messages as materialized (
    select message.id
    from public.notification_messages as message
    where message.channel = 'line'::public.notification_channel
      and (
        p_allow_all
        or message.user_id = p_canary_user_id
        or (
          p_use_allowlist
          and exists (
            select 1
            from private.line_notification_beta_allowlist as allowlisted
            where allowlisted.user_id = message.user_id
          )
        )
      )
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
        p_allow_all
        or message.user_id = p_canary_user_id
        or (
          p_use_allowlist
          and exists (
            select 1
            from private.line_notification_beta_allowlist as allowlisted
            where allowlisted.user_id = message.user_id
          )
        )
      )
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
        p_allow_all
        or message.user_id = p_canary_user_id
        or (
          p_use_allowlist
          and exists (
            select 1
            from private.line_notification_beta_allowlist as allowlisted
            where allowlisted.user_id = message.user_id
          )
        )
      )
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
      message.locked_until,
      message.line_test_text
  )
  select
    claimed.id,
    claimed.user_id,
    link.line_user_id,
    claimed.channel,
    claimed.attempt_count,
    claimed.locked_until,
    claimed.line_test_text,
    case
      when claimed.line_test_text is not null then '[]'::pg_catalog.jsonb
      else pg_catalog.jsonb_agg(
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
    end
  from claimed_messages as claimed
  inner join public.line_account_links as link
    on link.user_id = claimed.user_id
    and link.status = 'active'::public.line_account_link_status
  left join public.notification_message_items as message_item
    on message_item.message_id = claimed.id
  left join public.notification_delivery_items as delivery_item
    on delivery_item.id = message_item.delivery_item_id
  group by
    claimed.id,
    claimed.user_id,
    link.line_user_id,
    claimed.channel,
    claimed.attempt_count,
    claimed.locked_until,
    claimed.line_test_text
  having
    claimed.line_test_text is not null
    or pg_catalog.count(delivery_item.id) > 0
  order by claimed.locked_until, claimed.id;
end;
$$;

create function public.authorize_line_message_send(
  p_message_id uuid,
  p_locked_until timestamptz,
  p_line_user_id text,
  p_provider_payload_fingerprint text,
  p_canary_user_id uuid,
  p_use_allowlist boolean,
  p_allow_all boolean
)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_message public.notification_messages%rowtype;
  v_rollout_mode_count pg_catalog.int4;
  v_allowlisted_count pg_catalog.int4;
  v_max_attempts constant pg_catalog.int4 := 5;
  v_max_allowlisted_members constant pg_catalog.int4 := 20;
  v_provider_safety_window constant interval := interval '23 hours';
begin
  perform pg_catalog.pg_advisory_xact_lock_shared(20260828001723);

  if (
    p_message_id is null
    or p_locked_until is null
    or p_line_user_id is null
    or p_line_user_id !~ '^U[0-9a-f]{32}$'
    or p_provider_payload_fingerprint is null
    or p_provider_payload_fingerprint !~ '^[0-9a-f]{64}$'
    or p_use_allowlist is null
    or p_allow_all is null
  ) then
    raise exception 'LINE send authorization input is invalid.'
      using errcode = '22023';
  end if;

  v_rollout_mode_count :=
    case when p_canary_user_id is null then 0 else 1 end
    + case when p_use_allowlist then 1 else 0 end
    + case when p_allow_all then 1 else 0 end;

  if v_rollout_mode_count <> 1 then
    raise exception 'LINE send authorization input is invalid.'
      using errcode = '22023';
  end if;

  if p_use_allowlist then
    select pg_catalog.count(*)::int
    into v_allowlisted_count
    from private.line_notification_beta_allowlist;

    if (
      v_allowlisted_count < 1
      or v_allowlisted_count > v_max_allowlisted_members
    ) then
      raise exception 'LINE beta allowlist is empty or exceeds 20 members.'
        using errcode = '22023';
    end if;
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

  if not coalesce(
    p_allow_all
      or v_message.user_id = p_canary_user_id
      or (
        p_use_allowlist
        and exists (
          select 1
          from private.line_notification_beta_allowlist as allowlisted
          where allowlisted.user_id = v_message.user_id
        )
      ),
    false
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

revoke all on function public.replace_line_notification_beta_allowlist(uuid[])
from public, anon, authenticated, service_role;
grant execute on function public.replace_line_notification_beta_allowlist(uuid[])
to service_role;

revoke all on function public.enqueue_line_notification_candidates(
  jsonb,
  boolean,
  uuid,
  boolean
)
from public, anon, authenticated, service_role;
revoke all on function public.enqueue_line_notification_candidates(
  jsonb,
  boolean,
  uuid,
  boolean,
  boolean
)
from public, anon, authenticated, service_role;
grant execute on function public.enqueue_line_notification_candidates(
  jsonb,
  boolean,
  uuid,
  boolean,
  boolean
)
to service_role;

revoke all on function public.claim_line_messages(integer, uuid, boolean)
from public, anon, authenticated, service_role;
revoke all on function public.claim_line_messages(
  integer,
  uuid,
  boolean,
  boolean
)
from public, anon, authenticated, service_role;
grant execute on function public.claim_line_messages(
  integer,
  uuid,
  boolean,
  boolean
)
to service_role;

revoke all on function public.authorize_line_message_send(
  uuid,
  timestamptz,
  text,
  text,
  uuid,
  boolean
)
from public, anon, authenticated, service_role;
revoke all on function public.authorize_line_message_send(
  uuid,
  timestamptz,
  text,
  text,
  uuid,
  boolean,
  boolean
)
from public, anon, authenticated, service_role;
grant execute on function public.authorize_line_message_send(
  uuid,
  timestamptz,
  text,
  text,
  uuid,
  boolean,
  boolean
)
to service_role;

comment on schema private is
  'Server-only objects that must not be exposed through the Data API.';
comment on table private.line_notification_beta_allowlist is
  'At most 20 Supabase Auth users approved for the limited LINE beta.';
comment on function public.replace_line_notification_beta_allowlist(uuid[]) is
  'Atomically replaces the capped LINE beta allowlist and cancels removed unsent work.';
comment on function public.enqueue_line_notification_candidates(
  jsonb,
  boolean,
  uuid,
  boolean,
  boolean
) is
  'Validates LINE candidates and enforces shadow, canary, beta allowlist, or allow-all scope.';
comment on function public.claim_line_messages(
  integer,
  uuid,
  boolean,
  boolean
) is
  'Claims bounded LINE batches inside one mutually exclusive rollout scope.';
comment on function public.authorize_line_message_send(
  uuid,
  timestamptz,
  text,
  text,
  uuid,
  boolean,
  boolean
) is
  'Rechecks rollout scope, recipient, lease, and payload immediately before LINE Push.';
comment on function public.enqueue_line_notification_candidates(
  jsonb,
  boolean,
  uuid,
  boolean
) is
  'Deprecated single-canary RPC signature; execution is revoked from service_role.';
comment on function public.claim_line_messages(integer, uuid, boolean) is
  'Deprecated single-canary RPC signature; execution is revoked from service_role.';
comment on function public.authorize_line_message_send(
  uuid,
  timestamptz,
  text,
  text,
  uuid,
  boolean
) is
  'Deprecated single-canary RPC signature; execution is revoked from service_role.';

commit;
