-- Phase 3.5a Resend webhook delivery feedback.
-- Raw webhook payloads and recipient data are intentionally not persisted.

begin;

create function public.record_resend_email_event(
  p_provider_event_id text,
  p_provider_message_id text,
  p_event_type text,
  p_occurred_at timestamptz,
  p_source_tag text default null,
  p_message_id_tag text default null
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_message public.notification_messages%rowtype;
  v_latest_event_type pg_catalog.text;
  v_latest_provider_status pg_catalog.text;
  v_latest_occurred_at timestamptz;
  v_latest_delivered_at timestamptz;
  v_earliest_accepted_at timestamptz;
  v_preference_disabled_count pg_catalog.int4 := 0;
  v_disabled_reason pg_catalog.text;
  v_incoming_preference_priority pg_catalog.int4;
begin
  if (
    p_provider_event_id is null
    or p_provider_event_id !~ '^msg_[A-Za-z0-9_-]{1,251}$'
    or p_provider_message_id is null
    or p_provider_message_id !~ '^[A-Za-z0-9_-]{1,255}$'
    or p_event_type is null
    or p_event_type <> all (
      array[
        'email.sent',
        'email.delivery_delayed',
        'email.delivered',
        'email.failed',
        'email.bounced',
        'email.complained',
        'email.suppressed'
      ]::pg_catalog.text[]
    )
    or p_occurred_at is null
    or not pg_catalog.isfinite(p_occurred_at)
    or p_occurred_at > pg_catalog.statement_timestamp() + interval '5 minutes'
  ) then
    raise exception 'Resend event input is invalid.'
      using errcode = '22023';
  end if;

  if (
    p_source_tag is not null
    and (
      p_source_tag !~ '^[A-Za-z0-9_-]+$'
      or pg_catalog.char_length(p_source_tag) > 256
    )
  ) or (
    p_message_id_tag is not null
    and (
      p_message_id_tag !~ '^[A-Za-z0-9_-]+$'
      or pg_catalog.char_length(p_message_id_tag) > 256
    )
  ) then
    raise exception 'Resend event tag input is invalid.'
      using errcode = '22023';
  end if;

  -- Serialize correlation and provider-ID binding for one Resend message.
  perform pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'resend:' || p_provider_message_id,
      0
    )
  );

  if exists (
    select 1
    from public.notification_provider_events as provider_event
    where provider_event.provider = 'resend'
      and provider_event.provider_event_id = p_provider_event_id
  ) then
    return pg_catalog.jsonb_build_object(
      'outcome', 'duplicate',
      'stored_event_count', 0,
      'preference_disabled_count', 0
    );
  end if;

  -- A provider ID already recorded by the worker is the strongest correlation.
  select message.*
  into v_message
  from public.notification_messages as message
  where message.provider_message_id = p_provider_message_id
  for update;

  if not found
    and p_source_tag = 'user_notification'
    and p_message_id_tag is not null
    and p_message_id_tag ~
      '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$'
  then
    select message.*
    into v_message
    from public.notification_messages as message
    where message.id = p_message_id_tag::pg_catalog.uuid
    for update;

    if found then
      if v_message.provider_message_id is null then
        if (
          v_message.provider_first_attempt_at is null
          or v_message.provider_payload_fingerprint is null
        ) then
          return pg_catalog.jsonb_build_object(
            'outcome', 'correlation_conflict',
            'stored_event_count', 0,
            'preference_disabled_count', 0
          );
        end if;

        update public.notification_messages as message
        set provider_message_id = p_provider_message_id
        where message.id = v_message.id;

        v_message.provider_message_id := p_provider_message_id;
      elsif v_message.provider_message_id <> p_provider_message_id then
        return pg_catalog.jsonb_build_object(
          'outcome', 'correlation_conflict',
          'stored_event_count', 0,
          'preference_disabled_count', 0
        );
      end if;
    end if;
  end if;

  if v_message.id is null then
    return pg_catalog.jsonb_build_object(
      'outcome', 'ignored_unmatched',
      'stored_event_count', 0,
      'preference_disabled_count', 0
    );
  end if;

  if v_message.provider_message_id <> p_provider_message_id then
    return pg_catalog.jsonb_build_object(
      'outcome', 'correlation_conflict',
      'stored_event_count', 0,
      'preference_disabled_count', 0
    );
  end if;

  insert into public.notification_provider_events (
    message_id,
    provider,
    provider_event_id,
    provider_message_id,
    event_type,
    provider_status,
    occurred_at
  )
  values (
    v_message.id,
    'resend',
    p_provider_event_id,
    p_provider_message_id,
    p_event_type,
    case p_event_type
      when 'email.sent' then 'sent'
      when 'email.delivery_delayed' then 'delivery_delayed'
      when 'email.delivered' then 'delivered'
      when 'email.failed' then 'failed'
      when 'email.bounced' then 'bounced'
      when 'email.complained' then 'complained'
      when 'email.suppressed' then 'suppressed'
    end,
    p_occurred_at
  )
  on conflict (provider, provider_event_id) do nothing;

  if not found then
    return pg_catalog.jsonb_build_object(
      'outcome', 'duplicate',
      'stored_event_count', 0,
      'preference_disabled_count', 0
    );
  end if;

  -- Delivery order is not authoritative. Re-evaluate the message from the
  -- newest provider timestamp, then from this fixed priority for equal times:
  -- complained > suppressed > bounced > failed > delivered > delayed > sent.
  select
    provider_event.event_type,
    provider_event.provider_status,
    provider_event.occurred_at
  into
    v_latest_event_type,
    v_latest_provider_status,
    v_latest_occurred_at
  from public.notification_provider_events as provider_event
  where provider_event.message_id = v_message.id
    and provider_event.provider = 'resend'
  order by
    provider_event.occurred_at desc,
    case provider_event.event_type
      when 'email.complained' then 70
      when 'email.suppressed' then 60
      when 'email.bounced' then 50
      when 'email.failed' then 40
      when 'email.delivered' then 30
      when 'email.delivery_delayed' then 20
      when 'email.sent' then 10
      else 0
    end desc,
    provider_event.provider_event_id desc
  limit 1;

  select pg_catalog.max(provider_event.occurred_at)
  into v_latest_delivered_at
  from public.notification_provider_events as provider_event
  where provider_event.message_id = v_message.id
    and provider_event.provider = 'resend'
    and provider_event.event_type = 'email.delivered';

  select pg_catalog.min(provider_event.occurred_at)
  into v_earliest_accepted_at
  from public.notification_provider_events as provider_event
  where provider_event.message_id = v_message.id
    and provider_event.provider = 'resend'
    and provider_event.event_type = any (
      array[
        'email.sent',
        'email.delivery_delayed',
        'email.delivered'
      ]::pg_catalog.text[]
    );

  update public.notification_messages as message
  set
    status = case v_latest_event_type
      when 'email.sent'
        then 'accepted'::public.notification_message_status
      when 'email.delivery_delayed'
        then 'accepted'::public.notification_message_status
      when 'email.delivered'
        then 'delivered'::public.notification_message_status
      when 'email.failed'
        then 'failed_permanent'::public.notification_message_status
      when 'email.bounced'
        then 'bounced'::public.notification_message_status
      when 'email.complained'
        then 'complained'::public.notification_message_status
      when 'email.suppressed'
        then 'suppressed'::public.notification_message_status
    end,
    provider_status = v_latest_provider_status,
    accepted_at = case
      when v_earliest_accepted_at is null then message.accepted_at
      when message.accepted_at is null then v_earliest_accepted_at
      else least(
        message.accepted_at,
        v_earliest_accepted_at
      )
    end,
    delivered_at = coalesce(v_latest_delivered_at, message.delivered_at),
    failed_at = case
      when v_latest_event_type = any (
        array[
          'email.sent',
          'email.delivery_delayed',
          'email.delivered'
        ]::pg_catalog.text[]
      ) then null
      else v_latest_occurred_at
    end,
    locked_at = null,
    locked_until = null,
    last_error_code = case v_latest_event_type
      when 'email.failed' then 'resend_delivery_failed'
      when 'email.bounced' then 'resend_bounced'
      when 'email.complained' then 'resend_complained'
      when 'email.suppressed' then 'resend_suppressed'
      else null
    end,
    last_error_message = null
  where message.id = v_message.id;

  if p_event_type = any (
    array[
      'email.bounced',
      'email.complained',
      'email.suppressed'
    ]::pg_catalog.text[]
  ) then
    v_disabled_reason := case p_event_type
      when 'email.bounced' then 'resend_bounced'
      when 'email.complained' then 'resend_complained'
      when 'email.suppressed' then 'resend_suppressed'
    end;
    v_incoming_preference_priority := case p_event_type
      when 'email.bounced' then 10
      when 'email.suppressed' then 20
      when 'email.complained' then 30
    end;

    update public.notification_email_preferences as preference
    set
      is_enabled = false,
      disabled_reason = v_disabled_reason,
      disabled_at = p_occurred_at
    where preference.user_id = v_message.user_id
      and (
        preference.disabled_reason is null
        or preference.disabled_reason <> all (
          array[
            'resend_bounced',
            'resend_suppressed',
            'resend_complained'
          ]::pg_catalog.text[]
        )
        or (
          preference.disabled_reason = any (
            array[
              'resend_bounced',
              'resend_suppressed',
              'resend_complained'
            ]::pg_catalog.text[]
          )
          and (
            preference.disabled_at is null
            or preference.disabled_at < p_occurred_at
            or (
              preference.disabled_at = p_occurred_at
              and case preference.disabled_reason
                when 'resend_bounced' then 10
                when 'resend_suppressed' then 20
                when 'resend_complained' then 30
              end < v_incoming_preference_priority
            )
          )
        )
      );

    get diagnostics v_preference_disabled_count = row_count;
  end if;

  return pg_catalog.jsonb_build_object(
    'outcome', 'recorded',
    'stored_event_count', 1,
    'preference_disabled_count', v_preference_disabled_count
  );
end;
$$;

revoke execute on function public.record_resend_email_event(
  text,
  text,
  text,
  timestamptz,
  text,
  text
)
from public, anon, authenticated;

grant execute on function public.record_resend_email_event(
  text,
  text,
  text,
  timestamptz,
  text,
  text
)
to service_role;

comment on function public.record_resend_email_event(
  text,
  text,
  text,
  timestamptz,
  text,
  text
) is
  'Idempotently records normalized Resend delivery events and aggregate outcomes.';

commit;
