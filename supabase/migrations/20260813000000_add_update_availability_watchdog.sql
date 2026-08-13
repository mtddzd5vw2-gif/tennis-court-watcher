-- Phase 3.4.4b scheduler reliability watchdog state and atomic lifecycle.
-- The GitHub-native schedule remains primary. This state only coordinates a
-- fallback dispatch when no qualifying live run has been created recently.

begin;

create table public.update_availability_watchdog_state (
  watchdog_name text primary key,
  last_snapshot_at timestamptz,
  observation_token uuid,
  snapshot_outcome text,
  active_run_count integer not null default 0,
  latest_live_run_created_at timestamptz,
  latest_live_success_at timestamptz,
  latest_live_failure_at timestamptz,
  consecutive_failure_count integer not null default 0,
  claim_token uuid,
  claimed_at timestamptz,
  claim_expires_at timestamptz,
  dispatch_cooldown_until timestamptz,
  dispatch_confirmed_at timestamptz,
  last_dispatch_attempt_at timestamptz,
  last_dispatch_accepted_at timestamptz,
  last_dispatched_workflow_run_id bigint,
  last_outcome text,
  check_count bigint not null default 0,
  github_api_error_count bigint not null default 0,
  dispatch_attempt_count bigint not null default 0,
  dispatch_accepted_count bigint not null default 0,
  updated_at timestamptz not null default pg_catalog.now(),
  constraint update_availability_watchdog_singleton
    check (watchdog_name = 'update-availability'),
  constraint update_availability_watchdog_snapshot_outcome
    check (
      snapshot_outcome is null
      or snapshot_outcome = any (
        array['fresh', 'stale', 'active', 'unknown']::text[]
      )
    ),
  constraint update_availability_watchdog_active_count
    check (active_run_count >= 0),
  constraint update_availability_watchdog_failure_count
    check (consecutive_failure_count >= 0),
  constraint update_availability_watchdog_claim_triplet
    check (
      (
        claim_token is null
        and claimed_at is null
        and claim_expires_at is null
      )
      or (
        claim_token is not null
        and claimed_at is not null
        and claim_expires_at is not null
        and claim_expires_at > claimed_at
      )
    ),
  constraint update_availability_watchdog_nonnegative_counters
    check (
      check_count >= 0
      and github_api_error_count >= 0
      and dispatch_attempt_count >= 0
      and dispatch_accepted_count >= 0
      and dispatch_accepted_count <= dispatch_attempt_count
    ),
  constraint update_availability_watchdog_run_id
    check (
      last_dispatched_workflow_run_id is null
      or last_dispatched_workflow_run_id > 0
    )
);

comment on table public.update_availability_watchdog_state is
  'Singleton scheduler-liveness observations, short claim lease, and dispatch cooldown; contains no user data.';

alter table public.update_availability_watchdog_state
  enable row level security;

revoke all privileges on table
  public.update_availability_watchdog_state
from public, anon, authenticated, service_role;

insert into public.update_availability_watchdog_state (watchdog_name)
values ('update-availability');

create function public.update_availability_watchdog_in_service_window(
  p_at timestamptz
)
returns boolean
language sql
immutable
set search_path = ''
as $$
  select case
    when p_at is null then false
    else
      (p_at at time zone 'Asia/Tokyo')::time >= time '07:20'
      or (p_at at time zone 'Asia/Tokyo')::time < time '00:30'
  end;
$$;

create function public.update_availability_watchdog_now()
returns timestamptz
language plpgsql
stable
set search_path = ''
as $$
declare
  v_test_now text;
begin
  -- Deterministic pgTAP boundaries are available only to a direct trusted DB
  -- administrator session. PostgREST connects as authenticator, including for
  -- service_role JWTs, so production RPC callers cannot override the clock.
  v_test_now := pg_catalog.current_setting(
    'app.update_availability_watchdog_test_now',
    true
  );
  if (
    session_user = any (array['postgres', 'supabase_admin']::name[])
    and v_test_now is not null
    and v_test_now <> ''
  ) then
    return v_test_now::timestamptz;
  end if;
  return pg_catalog.now();
end;
$$;

create function public.record_update_availability_watchdog_snapshot(
  p_observation_token uuid,
  p_snapshot_outcome text,
  p_active_run_count integer,
  p_latest_live_run_created_at timestamptz,
  p_latest_live_success_at timestamptz,
  p_latest_live_failure_at timestamptz,
  p_consecutive_failure_count integer
)
returns table (
  recorded boolean,
  last_dispatched_workflow_run_id bigint
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_now timestamptz := public.update_availability_watchdog_now();
begin
  if (
    p_observation_token is null
    or p_snapshot_outcome is null
    or p_snapshot_outcome <> all (
      array['fresh', 'stale', 'active', 'unknown']::text[]
    )
    or p_active_run_count is null
    or p_active_run_count < 0
    or p_consecutive_failure_count is null
    or p_consecutive_failure_count < 0
    or (
      p_snapshot_outcome = 'active'
      and p_active_run_count = 0
    )
    or (
      p_snapshot_outcome <> 'active'
      and p_active_run_count <> 0
    )
    or (
      p_snapshot_outcome = 'unknown'
      and (
        p_latest_live_run_created_at is not null
        or p_latest_live_success_at is not null
        or p_latest_live_failure_at is not null
        or p_consecutive_failure_count <> 0
      )
    )
  ) then
    raise exception 'Watchdog snapshot input is invalid.'
      using errcode = '22023';
  end if;

  return query
  update public.update_availability_watchdog_state as state
  set
    last_snapshot_at = v_now,
    observation_token = p_observation_token,
    snapshot_outcome = p_snapshot_outcome,
    active_run_count = p_active_run_count,
    latest_live_run_created_at = p_latest_live_run_created_at,
    latest_live_success_at = p_latest_live_success_at,
    latest_live_failure_at = p_latest_live_failure_at,
    consecutive_failure_count = p_consecutive_failure_count,
    last_outcome = case p_snapshot_outcome
      when 'fresh' then 'live_run_fresh'
      when 'stale' then 'live_run_stale'
      when 'active' then 'active_run_present'
      else 'github_snapshot_unknown'
    end,
    check_count = state.check_count + case
      when state.observation_token is distinct from p_observation_token then 1
      else 0
    end,
    github_api_error_count = state.github_api_error_count + case
      when state.observation_token is distinct from p_observation_token
        and p_snapshot_outcome = 'unknown' then 1
      else 0
    end,
    updated_at = v_now
  where state.watchdog_name = 'update-availability'
  returning true, state.last_dispatched_workflow_run_id;
end;
$$;

create function public.claim_update_availability_fallback(
  p_observation_token uuid,
  p_claim_token uuid
)
returns table (
  claim_token uuid,
  claim_expires_at timestamptz,
  last_dispatched_workflow_run_id bigint
)
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_now timestamptz := public.update_availability_watchdog_now();
begin
  if p_observation_token is null or p_claim_token is null then
    raise exception 'Watchdog claim input is invalid.'
      using errcode = '22023';
  end if;

  return query
  update public.update_availability_watchdog_state as state
  set
    claim_token = p_claim_token,
    claimed_at = v_now,
    claim_expires_at = v_now + interval '5 minutes',
    last_outcome = 'fallback_claimed',
    updated_at = v_now
  where state.watchdog_name = 'update-availability'
    and state.observation_token = p_observation_token
    and state.snapshot_outcome = 'stale'
    and state.active_run_count = 0
    and state.last_snapshot_at is not null
    and state.last_snapshot_at >= v_now - interval '2 minutes'
    and state.last_snapshot_at <= v_now + interval '2 minutes'
    and state.latest_live_run_created_at is not null
    and state.latest_live_run_created_at
      <= v_now - interval '45 minutes'
    and public.update_availability_watchdog_in_service_window(
      v_now
    )
    and (
      state.claim_token is null
      or state.claim_expires_at <= v_now
    )
    and (
      state.dispatch_cooldown_until is null
      or state.dispatch_cooldown_until <= v_now
    )
  returning
    state.claim_token,
    state.claim_expires_at,
    state.last_dispatched_workflow_run_id;
end;
$$;

create function public.confirm_update_availability_fallback(
  p_observation_token uuid,
  p_claim_token uuid
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_updated_count integer;
  v_now timestamptz := public.update_availability_watchdog_now();
begin
  if p_observation_token is null or p_claim_token is null then
    raise exception 'Watchdog confirmation input is invalid.'
      using errcode = '22023';
  end if;

  update public.update_availability_watchdog_state as state
  set
    dispatch_cooldown_until = v_now + interval '30 minutes',
    dispatch_confirmed_at = v_now,
    last_dispatch_attempt_at = v_now,
    dispatch_attempt_count = state.dispatch_attempt_count + 1,
    last_outcome = 'dispatch_confirmed',
    updated_at = v_now
  where state.watchdog_name = 'update-availability'
    and state.observation_token = p_observation_token
    and state.snapshot_outcome = 'stale'
    and state.active_run_count = 0
    and state.claim_token = p_claim_token
    and state.claim_expires_at > v_now
    and state.last_snapshot_at is not null
    and state.last_snapshot_at >= v_now - interval '2 minutes'
    and state.last_snapshot_at <= v_now + interval '2 minutes'
    and state.latest_live_run_created_at is not null
    and state.latest_live_run_created_at
      <= v_now - interval '45 minutes'
    and public.update_availability_watchdog_in_service_window(
      v_now
    )
    and (
      state.dispatch_cooldown_until is null
      or state.dispatch_cooldown_until <= v_now
    );

  get diagnostics v_updated_count = row_count;
  return v_updated_count = 1;
end;
$$;

create function public.finish_update_availability_fallback(
  p_claim_token uuid,
  p_outcome text,
  p_workflow_run_id bigint default null
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_updated_count integer;
  v_is_dispatch_result boolean;
  v_now timestamptz := public.update_availability_watchdog_now();
begin
  if (
    p_claim_token is null
    or p_outcome is null
    or p_outcome <> all (
      array[
        'recheck_fresh',
        'recheck_active',
        'recheck_unknown',
        'dispatch_accepted',
        'dispatch_failed',
        'dispatch_unknown'
      ]::text[]
    )
    or (
      p_outcome = 'dispatch_accepted'
      and (
        p_workflow_run_id is null
        or p_workflow_run_id <= 0
      )
    )
    or (
      p_outcome <> 'dispatch_accepted'
      and p_workflow_run_id is not null
    )
  ) then
    raise exception 'Watchdog finish input is invalid.'
      using errcode = '22023';
  end if;

  v_is_dispatch_result := p_outcome = any (
    array[
      'dispatch_accepted',
      'dispatch_failed',
      'dispatch_unknown'
    ]::text[]
  );

  update public.update_availability_watchdog_state as state
  set
    claim_token = null,
    claimed_at = null,
    claim_expires_at = null,
    last_dispatch_accepted_at = case
      when p_outcome = 'dispatch_accepted' then v_now
      else state.last_dispatch_accepted_at
    end,
    last_dispatched_workflow_run_id = case
      when p_outcome = 'dispatch_accepted'
        then p_workflow_run_id
      else state.last_dispatched_workflow_run_id
    end,
    dispatch_accepted_count = state.dispatch_accepted_count + case
      when p_outcome = 'dispatch_accepted' then 1
      else 0
    end,
    last_outcome = p_outcome,
    updated_at = v_now
  where state.watchdog_name = 'update-availability'
    and state.claim_token = p_claim_token
    and (
      (
        v_is_dispatch_result
        and state.dispatch_confirmed_at is not null
        and state.dispatch_confirmed_at >= state.claimed_at
      )
      or (
        not v_is_dispatch_result
        and (
          state.dispatch_confirmed_at is null
          or state.dispatch_confirmed_at < state.claimed_at
        )
        and state.claim_expires_at > v_now
      )
    );

  get diagnostics v_updated_count = row_count;
  return v_updated_count = 1;
end;
$$;

revoke all on function
  public.update_availability_watchdog_in_service_window(timestamptz)
from public, anon, authenticated;

revoke all on function public.update_availability_watchdog_now()
from public, anon, authenticated;

revoke execute on function
  public.record_update_availability_watchdog_snapshot(
    uuid,
    text,
    integer,
    timestamptz,
    timestamptz,
    timestamptz,
    integer
  )
from public, anon, authenticated;

grant execute on function
  public.record_update_availability_watchdog_snapshot(
    uuid,
    text,
    integer,
    timestamptz,
    timestamptz,
    timestamptz,
    integer
  )
to service_role;

revoke execute on function
  public.claim_update_availability_fallback(uuid, uuid)
from public, anon, authenticated;

grant execute on function
  public.claim_update_availability_fallback(uuid, uuid)
to service_role;

revoke execute on function
  public.confirm_update_availability_fallback(uuid, uuid)
from public, anon, authenticated;

grant execute on function
  public.confirm_update_availability_fallback(uuid, uuid)
to service_role;

revoke execute on function
  public.finish_update_availability_fallback(uuid, text, bigint)
from public, anon, authenticated;

grant execute on function
  public.finish_update_availability_fallback(uuid, text, bigint)
to service_role;

comment on function
  public.record_update_availability_watchdog_snapshot(
    uuid,
    text,
    integer,
    timestamptz,
    timestamptz,
    timestamptz,
    integer
  ) is
  'Records one normalized GitHub API observation; repeated tokens correct a snapshot without double-counting.';

comment on function
  public.claim_update_availability_fallback(uuid, uuid) is
  'Atomically acquires a five-minute fallback lease without starting cooldown.';

comment on function
  public.confirm_update_availability_fallback(uuid, uuid) is
  'Rechecks the second observation and service window, then starts the thirty-minute cooldown before POST.';

comment on function
  public.finish_update_availability_fallback(uuid, text, bigint) is
  'Releases the exact lease and records a normalized pre-POST or post-POST result.';

commit;
