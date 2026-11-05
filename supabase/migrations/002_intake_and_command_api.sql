create table if not exists brick1.intake_rate_limits (
  ip_hash text not null,
  window_start timestamptz not null,
  request_count integer not null default 1 check (request_count > 0),
  primary key(ip_hash, window_start)
);
alter table brick1.intake_rate_limits enable row level security;
revoke all on brick1.intake_rate_limits from anon, authenticated;

alter table brick1.customers add constraint customers_email_unique unique(email);

create or replace function public.brick1_public_intake_allowed(p_ip_hash text)
returns boolean
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  bucket timestamptz := date_trunc('hour', now());
  n integer;
begin
  if p_ip_hash is null or length(p_ip_hash) <> 64 then return false; end if;
  insert into brick1.intake_rate_limits(ip_hash,window_start,request_count)
  values(p_ip_hash,bucket,1)
  on conflict(ip_hash,window_start)
  do update set request_count=brick1.intake_rate_limits.request_count+1
  returning request_count into n;
  return n <= 8;
end;
$$;

create or replace function public.brick1_create_inquiry(
  p_email text,
  p_first_name text,
  p_last_name text,
  p_company text,
  p_service text,
  p_budget text,
  p_timeline text,
  p_brief text,
  p_referral text,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  c_id uuid;
  o brick1.orders;
begin
  insert into brick1.customers(email,first_name,last_name,company)
  values(lower(p_email),p_first_name,p_last_name,p_company)
  on conflict(email) do update set first_name=excluded.first_name,last_name=excluded.last_name,company=excluded.company
  returning id into c_id;

  insert into brick1.orders(customer_id,service,budget,timeline,brief)
  values(c_id,p_service,p_budget,p_timeline,p_brief)
  returning * into o;

  perform brick1.append_evidence(p_correlation_id,o.id,'PUBLIC_INTAKE','INQUIRY_CREATED',jsonb_build_object('service',p_service,'budget',p_budget,'timeline',p_timeline,'referral',p_referral));
  return jsonb_build_object('order_id',o.id,'state',o.state,'created_at',o.created_at);
end;
$$;

create or replace function public.brick1_set_quote(
  p_order_id uuid,
  p_total_cents bigint,
  p_actor text,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare o brick1.orders;
begin
  select * into o from brick1.orders where id=p_order_id for update;
  if not found then raise exception 'order_not_found'; end if;
  if o.state <> 'INQUIRY' then raise exception 'quote_requires_inquiry'; end if;
  if p_total_cents <= 0 then raise exception 'invalid_quote'; end if;
  update brick1.orders set quote_total_cents=p_total_cents,updated_at=now() where id=p_order_id returning * into o;
  perform brick1.transition_order(p_order_id,'QUOTE',p_actor,p_correlation_id);
  perform brick1.transition_order(p_order_id,'DEPOSIT_REQUIRED',p_actor,p_correlation_id);
  return jsonb_build_object('order_id',p_order_id,'state','DEPOSIT_REQUIRED','quote_total_cents',p_total_cents,'deposit_required_cents',ceil(p_total_cents*0.35)::bigint);
end;
$$;

create or replace function public.brick1_record_payment(
  p_order_id uuid,
  p_provider text,
  p_provider_event_id text,
  p_amount_cents bigint,
  p_synthetic boolean,
  p_actor text,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  pay_id uuid;
  o brick1.orders;
begin
  pay_id := brick1.record_payment(p_order_id,p_provider,p_provider_event_id,p_amount_cents,p_synthetic,p_actor,p_correlation_id);
  select * into o from brick1.orders where id=p_order_id;
  if o.state='DEPOSIT_REQUIRED' and o.deposit_required_cents is not null and o.paid_cents >= o.deposit_required_cents then
    o := brick1.transition_order(p_order_id,'DEPOSIT_CONFIRMED',p_actor,p_correlation_id);
  elsif o.state='BALANCE_REQUIRED' and o.quote_total_cents is not null and o.paid_cents >= o.quote_total_cents then
    o := brick1.transition_order(p_order_id,'PAID',p_actor,p_correlation_id);
  end if;
  return jsonb_build_object('payment_id',pay_id,'order_id',p_order_id,'state',o.state,'paid_cents',o.paid_cents,'synthetic',p_synthetic);
end;
$$;

create or replace function public.brick1_set_qa(
  p_order_id uuid,
  p_actor text,
  p_reflection boolean,
  p_surface boolean,
  p_atmosphere boolean,
  p_confidence numeric,
  p_evidence jsonb,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare passed boolean;
begin
  if (select state from brick1.orders where id=p_order_id) <> 'QA' then raise exception 'qa_state_required'; end if;
  passed := p_reflection and p_surface and p_atmosphere and p_confidence >= 0.99;
  insert into brick1.qa_results(order_id,actor,reflection_pass,surface_pass,atmosphere_pass,confidence,evidence)
  values(p_order_id,p_actor,p_reflection,p_surface,p_atmosphere,p_confidence,coalesce(p_evidence,'{}'::jsonb));
  update brick1.orders set qa_passed=passed,updated_at=now() where id=p_order_id;
  perform brick1.append_evidence(p_correlation_id,p_order_id,p_actor,'QA_RESULT',jsonb_build_object('passed',passed,'confidence',p_confidence));
  if passed then perform brick1.transition_order(p_order_id,'BALANCE_REQUIRED',p_actor,p_correlation_id); end if;
  return jsonb_build_object('order_id',p_order_id,'passed',passed,'state',(select state from brick1.orders where id=p_order_id));
end;
$$;

create or replace function public.brick1_set_vera_approval(
  p_order_id uuid,
  p_actor text,
  p_approved boolean,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
begin
  if lower(p_actor) like '%ava%' or lower(p_actor) like '%orion%' then raise exception 'self_certification_forbidden'; end if;
  update brick1.orders set vera_approved=p_approved,updated_at=now() where id=p_order_id;
  if not found then raise exception 'order_not_found'; end if;
  perform brick1.append_evidence(p_correlation_id,p_order_id,p_actor,'VERA_DECISION',jsonb_build_object('approved',p_approved));
  return jsonb_build_object('order_id',p_order_id,'vera_approved',p_approved);
end;
$$;

create or replace function public.brick1_advance(
  p_order_id uuid,
  p_to brick1.order_state,
  p_actor text,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare o brick1.orders;
begin
  o := brick1.transition_order(p_order_id,p_to,p_actor,p_correlation_id);
  return jsonb_build_object('order_id',o.id,'state',o.state,'paid_cents',o.paid_cents,'qa_passed',o.qa_passed,'vera_approved',o.vera_approved);
end;
$$;

revoke all on function public.brick1_public_intake_allowed(text) from public, anon, authenticated;
revoke all on function public.brick1_create_inquiry(text,text,text,text,text,text,text,text,text,uuid) from public, anon, authenticated;
revoke all on function public.brick1_set_quote(uuid,bigint,text,uuid) from public, anon, authenticated;
revoke all on function public.brick1_record_payment(uuid,text,text,bigint,boolean,text,uuid) from public, anon, authenticated;
revoke all on function public.brick1_set_qa(uuid,text,boolean,boolean,boolean,numeric,jsonb,uuid) from public, anon, authenticated;
revoke all on function public.brick1_set_vera_approval(uuid,text,boolean,uuid) from public, anon, authenticated;
revoke all on function public.brick1_advance(uuid,brick1.order_state,text,uuid) from public, anon, authenticated;

grant execute on function public.brick1_public_intake_allowed(text) to service_role;
grant execute on function public.brick1_create_inquiry(text,text,text,text,text,text,text,text,text,uuid) to service_role;
grant execute on function public.brick1_set_quote(uuid,bigint,text,uuid) to service_role;
grant execute on function public.brick1_record_payment(uuid,text,text,bigint,boolean,text,uuid) to service_role;
grant execute on function public.brick1_set_qa(uuid,text,boolean,boolean,boolean,numeric,jsonb,uuid) to service_role;
grant execute on function public.brick1_set_vera_approval(uuid,text,boolean,uuid) to service_role;
grant execute on function public.brick1_advance(uuid,brick1.order_state,text,uuid) to service_role;
