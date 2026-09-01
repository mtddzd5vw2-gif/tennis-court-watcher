-- Privacy-preserving daily funnel counts for the public LINE-first signup flow.
-- The application database stores no visitor identifier, IP address, user agent,
-- referrer, URL, or raw event row.

create table private.anonymous_funnel_daily_counts (
  event_date date not null,
  event_name text not null,
  event_count bigint not null default 1,
  created_at timestamptz not null default pg_catalog.now(),
  updated_at timestamptz not null default pg_catalog.now(),
  constraint anonymous_funnel_daily_counts_pkey
    primary key (event_date, event_name),
  constraint anonymous_funnel_daily_counts_event_name_check
    check (
      event_name in (
        'login_page_view',
        'line_start_click',
        'terms_prompt_view'
      )
    ),
  constraint anonymous_funnel_daily_counts_event_count_check
    check (event_count between 1 and 1000000)
);

alter table private.anonymous_funnel_daily_counts
  enable row level security;

revoke all privileges on table private.anonymous_funnel_daily_counts
from public, anon, authenticated, service_role;

grant select on table private.anonymous_funnel_daily_counts
to service_role;

create function public.record_anonymous_funnel_event(
  p_event_name text
)
returns boolean
language plpgsql
security definer
set search_path = ''
set statement_timeout = '2s'
as $$
declare
  current_event_date pg_catalog.date :=
    pg_catalog.timezone('Asia/Tokyo', pg_catalog.now())::pg_catalog.date;
begin
  if p_event_name is null or p_event_name not in (
    'login_page_view',
    'line_start_click',
    'terms_prompt_view'
  ) then
    raise exception 'anonymous funnel event is invalid'
      using errcode = '22023';
  end if;

  delete from private.anonymous_funnel_daily_counts as daily_count
  where daily_count.event_date < current_event_date - 400;

  insert into private.anonymous_funnel_daily_counts (
    event_date,
    event_name,
    event_count
  )
  values (
    current_event_date,
    p_event_name,
    1
  )
  on conflict (event_date, event_name) do update
  set
    event_count = pg_catalog.least(
      private.anonymous_funnel_daily_counts.event_count + 1,
      1000000
    ),
    updated_at = pg_catalog.now();

  return true;
end;
$$;

revoke all on function public.record_anonymous_funnel_event(text)
from public, anon, authenticated, service_role;

grant execute on function public.record_anonymous_funnel_event(text)
to service_role;

comment on table private.anonymous_funnel_daily_counts is
  'Daily aggregate public signup funnel counts without visitor identifiers or raw events.';

comment on function public.record_anonymous_funnel_event(text) is
  'Service-role-only atomic increment for an allowlisted anonymous funnel event with 400-day retention.';
