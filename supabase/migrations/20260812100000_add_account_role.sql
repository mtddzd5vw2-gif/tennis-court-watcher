-- Database-authoritative account roles for members and administrators.
-- Account roles are independent from membership lifecycle and billing state.

begin;

create type public.account_role as enum (
  'member',
  'admin'
);

alter table public.profiles
  add column account_role public.account_role
    not null
    default 'member'::public.account_role;

comment on column public.profiles.account_role is
  'Authoritative account authorization role; independent from membership and billing.';

create function public.set_account_role(
  p_user_id uuid,
  p_account_role public.account_role
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.profiles
  set account_role = p_account_role
  where id = p_user_id;

  if not found then
    raise exception 'Member profile is unavailable.'
      using errcode = '22023';
  end if;
end;
$$;

comment on function public.set_account_role(uuid, public.account_role) is
  'Trusted-server-only account role change by explicit Auth user UUID.';

revoke execute on function public.set_account_role(uuid, public.account_role)
from public, anon, authenticated;

grant execute on function public.set_account_role(uuid, public.account_role)
to service_role;

commit;
