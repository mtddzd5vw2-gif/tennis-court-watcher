-- Account deletion must lock the member profile before deleting the Auth user.
-- Grant only the column required by the service-role Edge Function.
grant update (membership_status)
on table public.profiles
to service_role;
