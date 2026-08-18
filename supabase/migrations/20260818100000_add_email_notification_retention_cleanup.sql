-- Phase 3.5c email notification retention cleanup.
-- Deletes only old, stable notification history in bounded batches.
-- The function returns aggregate counts only and never exposes identifiers or PII.

begin;

create function public.cleanup_email_notification_history(
  batch_size integer default 1000
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_cutoff timestamptz;
  v_today pg_catalog.date;
  v_message_ids pg_catalog.uuid[];
  v_delivery_item_ids pg_catalog.uuid[];
  v_deleted_message_count pg_catalog.int4 := 0;
  v_deleted_message_item_count pg_catalog.int4 := 0;
  v_deleted_provider_event_count pg_catalog.int4 := 0;
  v_deleted_delivery_item_count pg_catalog.int4 := 0;
begin
  if (
    batch_size is null
    or batch_size < 1
    or batch_size > 1000
  ) then
    raise exception 'Email retention cleanup batch size is invalid.'
      using errcode = '22023';
  end if;

  -- Do not let two manual/cron cleanup runs compete for the same history.
  if not pg_catalog.pg_try_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'email-notification-retention-cleanup',
      0
    )
  ) then
    return pg_catalog.jsonb_build_object(
      'outcome', 'busy',
      'deleted_message_count', 0,
      'deleted_message_item_count', 0,
      'deleted_provider_event_count', 0,
      'deleted_delivery_item_count', 0
    );
  end if;

  v_cutoff := pg_catalog.statement_timestamp() - interval '90 days';
  v_today := (
    pg_catalog.statement_timestamp() at time zone 'Asia/Tokyo'
  )::pg_catalog.date;

  select coalesce(
    pg_catalog.array_agg(candidate.id order by candidate.created_at, candidate.id),
    array[]::pg_catalog.uuid[]
  )
  into v_message_ids
  from (
    select
      message.id,
      message.created_at
    from public.notification_messages as message
    where message.created_at < v_cutoff
      and message.updated_at < v_cutoff
      and message.status = any (
        array[
          'accepted',
          'delivered',
          'failed_permanent',
          'bounced',
          'complained',
          'suppressed',
          'cancelled'
        ]::public.notification_message_status[]
      )
      and not exists (
        select 1
        from public.notification_provider_events as provider_event
        where provider_event.message_id = message.id
          and provider_event.created_at >= v_cutoff
      )
    order by message.created_at, message.id
    for update of message skip locked
    limit batch_size
  ) as candidate;

  if pg_catalog.cardinality(v_message_ids) > 0 then
    select pg_catalog.count(*)::pg_catalog.int4
    into v_deleted_message_item_count
    from public.notification_message_items as message_item
    where message_item.message_id = any (v_message_ids);

    select pg_catalog.count(*)::pg_catalog.int4
    into v_deleted_provider_event_count
    from public.notification_provider_events as provider_event
    where provider_event.message_id = any (v_message_ids);

    delete from public.notification_messages as message
    where message.id = any (v_message_ids);

    get diagnostics v_deleted_message_count = row_count;
  end if;

  -- Delivery items are the dedupe authority. Delete them only after all
  -- message references are gone, the snapshot itself is older than 90 days,
  -- and the availability date is already in the past in the service timezone.
  select coalesce(
    pg_catalog.array_agg(candidate.id order by candidate.created_at, candidate.id),
    array[]::pg_catalog.uuid[]
  )
  into v_delivery_item_ids
  from (
    select
      delivery_item.id,
      delivery_item.created_at
    from public.notification_delivery_items as delivery_item
    where delivery_item.created_at < v_cutoff
      and delivery_item.available_date < v_today
      and not exists (
        select 1
        from public.notification_message_items as message_item
        where message_item.delivery_item_id = delivery_item.id
      )
    order by delivery_item.created_at, delivery_item.id
    for update of delivery_item skip locked
    limit batch_size
  ) as candidate;

  if pg_catalog.cardinality(v_delivery_item_ids) > 0 then
    delete from public.notification_delivery_items as delivery_item
    where delivery_item.id = any (v_delivery_item_ids);

    get diagnostics v_deleted_delivery_item_count = row_count;
  end if;

  return pg_catalog.jsonb_build_object(
    'outcome', 'cleaned',
    'deleted_message_count', v_deleted_message_count,
    'deleted_message_item_count', v_deleted_message_item_count,
    'deleted_provider_event_count', v_deleted_provider_event_count,
    'deleted_delivery_item_count', v_deleted_delivery_item_count
  );
end;
$$;

revoke execute on function public.cleanup_email_notification_history(integer)
from public, anon, authenticated;

grant execute on function public.cleanup_email_notification_history(integer)
to service_role;

comment on function public.cleanup_email_notification_history(integer) is
  'Deletes old stable email notification history in bounded batches and returns aggregate counts only.';

commit;
