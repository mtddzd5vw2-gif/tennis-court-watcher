-- PostgreSQL does not implicitly resolve least(bigint, integer). Keep the
-- original daily aggregate contract while making the cap explicitly bigint.
-- This applied rollout step is retained in history; the immediately following
-- migration removes the invalid pg_catalog qualification from LEAST.

create or replace function public.record_anonymous_funnel_event(
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
      1000000::pg_catalog.int8
    ),
    updated_at = pg_catalog.now();

  return true;
end;
$$;

revoke all on function public.record_anonymous_funnel_event(text)
from public, anon, authenticated, service_role;

grant execute on function public.record_anonymous_funnel_event(text)
to service_role;

notify pgrst, 'reload schema';
