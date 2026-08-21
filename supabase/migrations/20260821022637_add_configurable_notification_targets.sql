-- Let members choose Saturdays, Sundays, and Japanese holidays independently.
-- Notification time ranges stay inside the current 08:00-13:00 monitoring
-- window and use whole-hour boundaries with a minimum two-hour span.

alter table public.notification_rules
add column include_holidays boolean not null default false;

comment on column public.notification_rules.include_holidays is
  'Whether Japanese holidays match independently of the dates ISO weekday.';

-- PR #55 represented the fixed weekends-and-holidays policy by saving all
-- seven ISO weekdays. Convert only that exact representation to the new model.
update public.notification_rules as rule
set include_holidays = true
where rule.start_time = '08:00'::time
  and rule.end_time = '13:00'::time
  and (
    select pg_catalog.array_agg(
      selected_weekday.weekday
      order by selected_weekday.weekday
    )
    from public.notification_rule_weekdays as selected_weekday
    where selected_weekday.rule_id = rule.id
      and selected_weekday.user_id = rule.user_id
  ) = array[1, 2, 3, 4, 5, 6, 7]::smallint[];

delete from public.notification_rule_weekdays as selected_weekday
using public.notification_rules as rule
where selected_weekday.rule_id = rule.id
  and selected_weekday.user_id = rule.user_id
  and rule.include_holidays = true
  and rule.start_time = '08:00'::time
  and rule.end_time = '13:00'::time
  and selected_weekday.weekday between 1 and 5;

do $$
begin
  if exists (
    select 1
    from public.notification_rules as rule
    where rule.start_time < '08:00'::time
      or rule.end_time > '13:00'::time
      or pg_catalog.date_part('minute', rule.start_time) <> 0
      or pg_catalog.date_part('second', rule.start_time) <> 0
      or pg_catalog.date_part('minute', rule.end_time) <> 0
      or pg_catalog.date_part('second', rule.end_time) <> 0
      or rule.end_time - rule.start_time < interval '2 hours'
      or rule.minimum_duration_minutes < 60
      or rule.minimum_duration_minutes > 300
      or rule.minimum_duration_minutes % 60 <> 0
      or rule.minimum_duration_minutes
        > pg_catalog.date_part('epoch', rule.end_time - rule.start_time) / 60
  ) then
    raise exception
      'Existing notification rules must be normalized before this migration.';
  end if;

  if exists (
    select 1
    from public.notification_rule_weekdays as selected_weekday
    where selected_weekday.weekday not in (6, 7)
  ) then
    raise exception
      'Existing notification weekdays must be normalized before this migration.';
  end if;
end;
$$;

alter table public.notification_rules
add constraint notification_rules_monitored_time_window check (
  start_time >= '08:00'::time
  and end_time <= '13:00'::time
  and pg_catalog.date_part('minute', start_time) = 0
  and pg_catalog.date_part('second', start_time) = 0
  and pg_catalog.date_part('minute', end_time) = 0
  and pg_catalog.date_part('second', end_time) = 0
  and end_time - start_time >= interval '2 hours'
),
add constraint notification_rules_supported_duration check (
  minimum_duration_minutes between 60 and 300
  and minimum_duration_minutes % 60 = 0
  and minimum_duration_minutes
    <= pg_catalog.date_part('epoch', end_time - start_time) / 60
);

alter table public.notification_rule_weekdays
drop constraint notification_rule_weekdays_iso_check,
add constraint notification_rule_weekdays_supported_check
  check (weekday in (6, 7));

drop function public.save_notification_rule(
  uuid,
  text,
  boolean,
  date,
  date,
  time without time zone,
  time without time zone,
  smallint,
  text[],
  smallint[]
);

create function public.save_notification_rule(
  p_rule_id uuid,
  p_name text,
  p_is_enabled boolean,
  p_include_holidays boolean,
  p_date_from date,
  p_date_to date,
  p_start_time time without time zone,
  p_end_time time without time zone,
  p_minimum_duration_minutes smallint,
  p_facility_ids text[],
  p_weekdays smallint[]
)
returns uuid
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_user_id uuid := auth.uid();
  v_rule_id uuid;
  v_facility_ids text[];
  v_weekdays smallint[];
begin
  if v_user_id is null then
    raise exception 'Authentication is required to save a notification rule.'
      using errcode = '42501';
  end if;

  if (
    p_name is null
    or pg_catalog.btrim(p_name) = ''
    or pg_catalog.char_length(pg_catalog.btrim(p_name)) > 80
  ) then
    raise exception 'Notification rule name must be 1 to 80 characters.'
      using errcode = '22023';
  end if;

  if p_is_enabled is null or p_include_holidays is null then
    raise exception 'Notification rule states are required.'
      using errcode = '22023';
  end if;

  if (
    p_start_time is null
    or p_end_time is null
    or p_start_time < '08:00'::time
    or p_end_time > '13:00'::time
    or pg_catalog.date_part('minute', p_start_time) <> 0
    or pg_catalog.date_part('second', p_start_time) <> 0
    or pg_catalog.date_part('minute', p_end_time) <> 0
    or pg_catalog.date_part('second', p_end_time) <> 0
    or p_end_time - p_start_time < interval '2 hours'
  ) then
    raise exception
      'Notification time must be a whole-hour range of at least two hours between 08:00 and 13:00.'
      using errcode = '22023';
  end if;

  if (
    p_date_from is not null
    and p_date_to is not null
    and p_date_from > p_date_to
  ) then
    raise exception 'Notification rule start date must not be after end date.'
      using errcode = '22023';
  end if;

  if (
    p_minimum_duration_minutes is null
    or p_minimum_duration_minutes < 60
    or p_minimum_duration_minutes > 300
    or p_minimum_duration_minutes % 60 <> 0
    or p_minimum_duration_minutes
      > pg_catalog.date_part('epoch', p_end_time - p_start_time) / 60
  ) then
    raise exception
      'Minimum duration must be 60 to 300 minutes in 60 minute steps and fit the notification time range.'
      using errcode = '22023';
  end if;

  if (
    p_facility_ids is null
    or pg_catalog.cardinality(p_facility_ids) < 1
  ) then
    raise exception 'At least one facility is required.'
      using errcode = '22023';
  end if;

  if exists (
    select 1
    from pg_catalog.unnest(p_facility_ids) as facility_input(facility_id)
    where facility_input.facility_id is null
      or pg_catalog.btrim(facility_input.facility_id) = ''
  ) then
    raise exception 'Facility IDs must not be null or blank.'
      using errcode = '22023';
  end if;

  select pg_catalog.array_agg(
    distinct facility_input.facility_id
    order by facility_input.facility_id
  )
  into v_facility_ids
  from pg_catalog.unnest(p_facility_ids) as facility_input(facility_id);

  if (
    select pg_catalog.count(*)
    from public.facilities as facility
    where facility.id = any (v_facility_ids)
      and facility.is_active = true
  ) <> pg_catalog.cardinality(v_facility_ids) then
    raise exception 'Every facility must exist and be active.'
      using errcode = '22023';
  end if;

  if p_weekdays is null then
    raise exception 'Weekday selections are required.'
      using errcode = '22023';
  end if;

  if exists (
    select 1
    from pg_catalog.unnest(p_weekdays) as weekday_input(weekday)
    where weekday_input.weekday is null
      or weekday_input.weekday not in (6, 7)
  ) then
    raise exception 'Weekdays must contain only Saturday or Sunday.'
      using errcode = '22023';
  end if;

  select pg_catalog.coalesce(
    pg_catalog.array_agg(
      distinct weekday_input.weekday
      order by weekday_input.weekday
    ),
    array[]::smallint[]
  )
  into v_weekdays
  from pg_catalog.unnest(p_weekdays) as weekday_input(weekday);

  if pg_catalog.cardinality(v_weekdays) < 1 and not p_include_holidays then
    raise exception 'At least one notification day is required.'
      using errcode = '22023';
  end if;

  if p_rule_id is null then
    insert into public.notification_rules (
      user_id,
      name,
      is_enabled,
      include_holidays,
      date_from,
      date_to,
      start_time,
      end_time,
      minimum_duration_minutes
    )
    values (
      v_user_id,
      pg_catalog.btrim(p_name),
      p_is_enabled,
      p_include_holidays,
      p_date_from,
      p_date_to,
      p_start_time,
      p_end_time,
      p_minimum_duration_minutes
    )
    returning id into v_rule_id;
  else
    update public.notification_rules as rule
    set
      name = pg_catalog.btrim(p_name),
      is_enabled = p_is_enabled,
      include_holidays = p_include_holidays,
      date_from = p_date_from,
      date_to = p_date_to,
      start_time = p_start_time,
      end_time = p_end_time,
      minimum_duration_minutes = p_minimum_duration_minutes
    where rule.id = p_rule_id
      and rule.user_id = v_user_id
    returning rule.id into v_rule_id;

    if not found then
      raise exception 'Notification rule was not found for the current user.'
        using errcode = '42501';
    end if;
  end if;

  delete from public.notification_rule_facilities as selected_facility
  where selected_facility.rule_id = v_rule_id
    and selected_facility.user_id = v_user_id;

  delete from public.notification_rule_weekdays as selected_weekday
  where selected_weekday.rule_id = v_rule_id
    and selected_weekday.user_id = v_user_id;

  insert into public.notification_rule_facilities (
    rule_id,
    user_id,
    facility_id
  )
  select
    v_rule_id,
    v_user_id,
    facility_input.facility_id
  from pg_catalog.unnest(v_facility_ids) as facility_input(facility_id);

  insert into public.notification_rule_weekdays (
    rule_id,
    user_id,
    weekday
  )
  select
    v_rule_id,
    v_user_id,
    weekday_input.weekday
  from pg_catalog.unnest(v_weekdays) as weekday_input(weekday);

  return v_rule_id;
end;
$$;

revoke all on function public.save_notification_rule(
  uuid,
  text,
  boolean,
  boolean,
  date,
  date,
  time without time zone,
  time without time zone,
  smallint,
  text[],
  smallint[]
)
from public, anon, authenticated;

grant execute on function public.save_notification_rule(
  uuid,
  text,
  boolean,
  boolean,
  date,
  date,
  time without time zone,
  time without time zone,
  smallint,
  text[],
  smallint[]
)
to authenticated;

comment on function public.save_notification_rule(
  uuid,
  text,
  boolean,
  boolean,
  date,
  date,
  time without time zone,
  time without time zone,
  smallint,
  text[],
  smallint[]
) is
  'Atomically creates or updates one complete configurable notification rule for auth.uid().';

drop function public.list_notification_rules_for_matching();

create function public.list_notification_rules_for_matching()
returns table (
  rule_id uuid,
  user_id uuid,
  date_from date,
  date_to date,
  start_time time without time zone,
  end_time time without time zone,
  minimum_duration_minutes smallint,
  include_holidays boolean,
  facility_ids text[],
  weekdays smallint[]
)
language sql
security invoker
stable
set search_path = ''
as $$
  select
    rule.id as rule_id,
    rule.user_id,
    rule.date_from,
    rule.date_to,
    rule.start_time,
    rule.end_time,
    rule.minimum_duration_minutes,
    rule.include_holidays,
    pg_catalog.array_agg(
      distinct selected_facility.facility_id
      order by selected_facility.facility_id
    ) as facility_ids,
    pg_catalog.coalesce(
      pg_catalog.array_agg(
        distinct selected_weekday.weekday
        order by selected_weekday.weekday
      ) filter (where selected_weekday.weekday is not null),
      array[]::smallint[]
    ) as weekdays
  from public.notification_rules as rule
  inner join public.profiles as profile
    on profile.id = rule.user_id
    and profile.membership_status = 'active'::public.membership_status
  inner join public.notification_rule_facilities as selected_facility
    on selected_facility.rule_id = rule.id
    and selected_facility.user_id = rule.user_id
  left join public.notification_rule_weekdays as selected_weekday
    on selected_weekday.rule_id = rule.id
    and selected_weekday.user_id = rule.user_id
  where rule.is_enabled = true
  group by
    rule.id,
    rule.user_id,
    rule.date_from,
    rule.date_to,
    rule.start_time,
    rule.end_time,
    rule.minimum_duration_minutes,
    rule.include_holidays
  having
    pg_catalog.count(distinct selected_facility.facility_id) >= 1
    and (
      pg_catalog.count(distinct selected_weekday.weekday) >= 1
      or rule.include_holidays = true
    )
  order by rule.user_id, rule.id;
$$;

revoke execute on function public.list_notification_rules_for_matching()
from public, anon, authenticated;

grant execute on function public.list_notification_rules_for_matching()
to service_role;

comment on function public.list_notification_rules_for_matching() is
  'Returns active members enabled weekend and holiday rules to trusted matching jobs.';
