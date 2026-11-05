-- Brick 1 OASIS CLIP governance hardening.
-- Keeps raw customer payloads inside brick1 while separating ORION/AVA/SENTINEL/VERA authority.

create type brick1.exception_state as enum (
  'NORMAL','RETRYABLE_EXCEPTION','QUARANTINED','WAITING_CUSTOMER',
  'WAITING_PAYMENT','WAITING_QA','RECOVERY','OWNER_REVIEW'
);

alter table brick1.orders
  add column if not exists exception_state brick1.exception_state not null default 'NORMAL';

create table if not exists brick1.action_requests (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references brick1.orders(id),
  correlation_id uuid not null,
  requested_by text not null check (requested_by in ('ORION','AVA')),
  action text not null check (action in ('SET_QUOTE','START_PRODUCTION','REQUEST_QA','FULFILL','ARCHIVE','ESCALATE_EXCEPTION')),
  payload jsonb not null default '{}'::jsonb,
  payload_hash text not null,
  status text not null default 'PENDING' check (status in ('PENDING','AUTHORIZED','DENIED','EXECUTED','FAILED')),
  decided_by text,
  decision_reason text,
  failure_code text,
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  executed_at timestamptz
);

create table if not exists brick1.exceptions (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references brick1.orders(id),
  correlation_id uuid not null,
  state brick1.exception_state not null,
  code text not null check (length(code) between 1 and 120),
  opened_by text not null,
  details_hash text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  resolved_at timestamptz,
  resolved_by text
);

create index if not exists brick1_active_exceptions_idx on brick1.exceptions(order_id) where active;

alter table brick1.action_requests enable row level security;
alter table brick1.exceptions enable row level security;
revoke all on brick1.action_requests from anon, authenticated;
revoke all on brick1.exceptions from anon, authenticated;

-- Serialize evidence appends to prevent predecessor forks under concurrent writes.
create or replace function brick1.append_evidence(
  p_correlation_id uuid,
  p_order_id uuid,
  p_actor text,
  p_event_type text,
  p_payload jsonb
) returns text
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  prev text;
  payload_digest text;
  entry_digest text;
begin
  perform pg_advisory_xact_lock(hashtext('brick1.evidence_ledger'));
  select entry_hash into prev from brick1.evidence_ledger order by seq desc limit 1;
  payload_digest := encode(digest(convert_to(coalesce(p_payload,'{}'::jsonb)::text,'utf8'),'sha256'),'hex');
  entry_digest := encode(digest(convert_to(coalesce(prev,'GENESIS') || '|' || p_correlation_id::text || '|' || coalesce(p_order_id::text,'') || '|' || p_actor || '|' || p_event_type || '|' || payload_digest,'utf8'),'sha256'),'hex');
  insert into brick1.evidence_ledger(correlation_id,order_id,actor,event_type,payload_hash,predecessor_hash,entry_hash)
  values (p_correlation_id,p_order_id,p_actor,p_event_type,payload_digest,prev,entry_digest);
  return entry_digest;
end;
$$;

create or replace function brick1.block_evidence_mutation()
returns trigger language plpgsql as $$
begin
  raise exception 'evidence_ledger_append_only';
end;
$$;

drop trigger if exists brick1_evidence_append_only on brick1.evidence_ledger;
create trigger brick1_evidence_append_only
before update or delete on brick1.evidence_ledger
for each row execute function brick1.block_evidence_mutation();

create or replace function public.brick1_propose_action(
  p_order_id uuid,
  p_actor text,
  p_action text,
  p_payload jsonb,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  r brick1.action_requests;
  payload_digest text;
begin
  if p_actor not in ('ORION','AVA') then raise exception 'proposal_authority_denied'; end if;
  if p_action not in ('SET_QUOTE','START_PRODUCTION','REQUEST_QA','FULFILL','ARCHIVE','ESCALATE_EXCEPTION') then raise exception 'unsupported_action'; end if;
  if not exists(select 1 from brick1.orders where id=p_order_id) then raise exception 'order_not_found'; end if;
  payload_digest := encode(digest(convert_to(coalesce(p_payload,'{}'::jsonb)::text,'utf8'),'sha256'),'hex');
  insert into brick1.action_requests(order_id,correlation_id,requested_by,action,payload,payload_hash)
  values(p_order_id,p_correlation_id,p_actor,p_action,coalesce(p_payload,'{}'::jsonb),payload_digest)
  returning * into r;
  perform brick1.append_evidence(p_correlation_id,p_order_id,p_actor,'ACTION_PROPOSED',jsonb_build_object('request_id',r.id,'action',p_action,'payload_hash',payload_digest));
  return jsonb_build_object('request_id',r.id,'status',r.status,'action',r.action);
end;
$$;

create or replace function public.brick1_decide_action(
  p_request_id uuid,
  p_actor text,
  p_approved boolean,
  p_reason text,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  r brick1.action_requests;
begin
  if p_actor <> 'VERA' then raise exception 'vera_authority_required'; end if;
  select * into r from brick1.action_requests where id=p_request_id for update;
  if not found then raise exception 'request_not_found'; end if;
  if r.status <> 'PENDING' then raise exception 'request_not_pending'; end if;
  update brick1.action_requests
     set status=case when p_approved then 'AUTHORIZED' else 'DENIED' end,
         decided_by=p_actor,
         decision_reason=left(coalesce(p_reason,''),500),
         decided_at=now()
   where id=p_request_id returning * into r;
  perform brick1.append_evidence(p_correlation_id,r.order_id,p_actor,'ACTION_DECIDED',jsonb_build_object('request_id',r.id,'action',r.action,'approved',p_approved));
  return jsonb_build_object('request_id',r.id,'status',r.status,'action',r.action);
end;
$$;

create or replace function public.brick1_execute_action(
  p_request_id uuid,
  p_actor text,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  r brick1.action_requests;
  result jsonb;
  total_cents bigint;
  requested_state brick1.exception_state;
  exception_code text;
  details_digest text;
  e brick1.exceptions;
  failure text;
begin
  if p_actor <> 'AVA' then raise exception 'ava_execution_authority_required'; end if;
  select * into r from brick1.action_requests where id=p_request_id for update;
  if not found then raise exception 'request_not_found'; end if;
  if r.status <> 'AUTHORIZED' then raise exception 'action_not_authorized'; end if;

  begin
    if r.action='SET_QUOTE' then
      total_cents := nullif(r.payload->>'total_cents','')::bigint;
      result := public.brick1_set_quote(r.order_id,total_cents,p_actor,p_correlation_id);
    elsif r.action='START_PRODUCTION' then
      result := public.brick1_advance(r.order_id,'PRODUCTION',p_actor,p_correlation_id);
    elsif r.action='REQUEST_QA' then
      result := public.brick1_advance(r.order_id,'QA',p_actor,p_correlation_id);
    elsif r.action='FULFILL' then
      result := public.brick1_advance(r.order_id,'FULFILLED',p_actor,p_correlation_id);
    elsif r.action='ARCHIVE' then
      result := public.brick1_advance(r.order_id,'ARCHIVED',p_actor,p_correlation_id);
    elsif r.action='ESCALATE_EXCEPTION' then
      requested_state := nullif(r.payload->>'requested_state','')::brick1.exception_state;
      exception_code := left(coalesce(nullif(r.payload->>'code',''),'UNSPECIFIED_EXCEPTION'),120);
      if requested_state not in ('QUARANTINED','RECOVERY','OWNER_REVIEW') then
        raise exception 'high_risk_exception_state_required';
      end if;
      details_digest := encode(digest(convert_to(coalesce(r.payload,'{}'::jsonb)::text,'utf8'),'sha256'),'hex');
      insert into brick1.exceptions(order_id,correlation_id,state,code,opened_by,details_hash)
      values(r.order_id,p_correlation_id,requested_state,exception_code,'AVA_VERA_AUTHORIZED',details_digest)
      returning * into e;
      update brick1.orders set exception_state=requested_state,updated_at=now() where id=r.order_id;
      perform brick1.transition_order(r.order_id,'QUARANTINED','AVA_VERA_AUTHORIZED',p_correlation_id);
      perform brick1.append_evidence(p_correlation_id,r.order_id,'AVA_VERA_AUTHORIZED','EXCEPTION_ESCALATED',jsonb_build_object('exception_id',e.id,'requested_state',requested_state,'code',exception_code,'request_id',r.id));
      result := jsonb_build_object('order_id',r.order_id,'state','QUARANTINED','exception_state',requested_state,'exception_id',e.id);
    else
      raise exception 'unsupported_action';
    end if;
  exception when others then
    failure := left(SQLSTATE || ':' || SQLERRM,500);
    update brick1.action_requests set status='FAILED',failure_code=failure where id=p_request_id;
    perform brick1.append_evidence(p_correlation_id,r.order_id,p_actor,'ACTION_FAILED',jsonb_build_object('request_id',r.id,'action',r.action,'failure',failure));
    return jsonb_build_object('request_id',r.id,'request_status','FAILED','error',failure);
  end;

  update brick1.action_requests set status='EXECUTED', executed_at=now(), failure_code=null where id=p_request_id;
  perform brick1.append_evidence(p_correlation_id,r.order_id,p_actor,'ACTION_EXECUTED',jsonb_build_object('request_id',r.id,'action',r.action));
  return result || jsonb_build_object('request_id',r.id,'request_status','EXECUTED');
end;
$$;

create or replace function public.brick1_open_exception(
  p_order_id uuid,
  p_state brick1.exception_state,
  p_code text,
  p_actor text,
  p_details jsonb,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  e brick1.exceptions;
  details_digest text;
begin
  if p_actor <> 'AVA' then raise exception 'ava_exception_supervisor_required'; end if;
  if p_state not in ('RETRYABLE_EXCEPTION','WAITING_CUSTOMER','WAITING_PAYMENT','WAITING_QA') then
    raise exception 'routine_exception_only';
  end if;
  if not exists(select 1 from brick1.orders where id=p_order_id) then raise exception 'order_not_found'; end if;
  details_digest := encode(digest(convert_to(coalesce(p_details,'{}'::jsonb)::text,'utf8'),'sha256'),'hex');
  insert into brick1.exceptions(order_id,correlation_id,state,code,opened_by,details_hash)
  values(p_order_id,p_correlation_id,p_state,left(p_code,120),p_actor,details_digest)
  returning * into e;
  update brick1.orders set exception_state=p_state,updated_at=now() where id=p_order_id;
  perform brick1.append_evidence(p_correlation_id,p_order_id,p_actor,'EXCEPTION_OPENED',jsonb_build_object('exception_id',e.id,'state',p_state,'code',p_code,'details_hash',details_digest));
  return jsonb_build_object('exception_id',e.id,'state',e.state,'active',e.active);
end;
$$;

create or replace function public.brick1_resolve_exception(
  p_exception_id uuid,
  p_actor text,
  p_correlation_id uuid
) returns jsonb
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  e brick1.exceptions;
  remaining integer;
begin
  select * into e from brick1.exceptions where id=p_exception_id for update;
  if not found then raise exception 'exception_not_found'; end if;
  if not e.active then raise exception 'exception_already_resolved'; end if;
  if e.state in ('QUARANTINED','RECOVERY','OWNER_REVIEW') and p_actor not in ('VERA','OWNER') then
    raise exception 'high_risk_exception_requires_vera_or_owner';
  end if;
  if e.state not in ('QUARANTINED','RECOVERY','OWNER_REVIEW') and p_actor not in ('AVA','VERA','OWNER') then
    raise exception 'exception_resolution_authority_denied';
  end if;
  update brick1.exceptions set active=false,resolved_at=now(),resolved_by=p_actor where id=p_exception_id;
  select count(*) into remaining from brick1.exceptions where order_id=e.order_id and active;
  if remaining=0 then update brick1.orders set exception_state='NORMAL',updated_at=now() where id=e.order_id; end if;
  perform brick1.append_evidence(p_correlation_id,e.order_id,p_actor,'EXCEPTION_RESOLVED',jsonb_build_object('exception_id',e.id,'state',e.state));
  return jsonb_build_object('exception_id',e.id,'resolved',true,'remaining_active',remaining);
end;
$$;

-- SENTINEL and VERA are independent gates; AVA/ORION cannot impersonate them.
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
  if p_actor <> 'SENTINEL' then raise exception 'sentinel_authority_required'; end if;
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
  if p_actor <> 'VERA' then raise exception 'vera_authority_required'; end if;
  update brick1.orders set vera_approved=p_approved,updated_at=now() where id=p_order_id;
  if not found then raise exception 'order_not_found'; end if;
  perform brick1.append_evidence(p_correlation_id,p_order_id,p_actor,'VERA_DECISION',jsonb_build_object('approved',p_approved));
  return jsonb_build_object('order_id',p_order_id,'vera_approved',p_approved);
end;
$$;

create or replace function public.brick1_verify_evidence_chain()
returns boolean
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  r record;
  expected_prev text := null;
  expected_hash text;
begin
  for r in select * from brick1.evidence_ledger order by seq loop
    if r.predecessor_hash is distinct from expected_prev then return false; end if;
    expected_hash := encode(digest(convert_to(coalesce(expected_prev,'GENESIS') || '|' || r.correlation_id::text || '|' || coalesce(r.order_id::text,'') || '|' || r.actor || '|' || r.event_type || '|' || r.payload_hash,'utf8'),'sha256'),'hex');
    if expected_hash <> r.entry_hash then return false; end if;
    expected_prev := r.entry_hash;
  end loop;
  return true;
end;
$$;

create or replace function public.brick1_order_status(p_order_id uuid)
returns jsonb
language sql
security definer
set search_path = brick1, public
as $$
  select jsonb_build_object(
    'order_id',o.id,
    'state',o.state,
    'exception_state',o.exception_state,
    'quote_total_cents',o.quote_total_cents,
    'deposit_required_cents',o.deposit_required_cents,
    'paid_cents',o.paid_cents,
    'qa_passed',o.qa_passed,
    'vera_approved',o.vera_approved,
    'active_exceptions',(select count(*) from brick1.exceptions e where e.order_id=o.id and e.active)
  ) from brick1.orders o where o.id=p_order_id;
$$;

revoke all on function public.brick1_propose_action(uuid,text,text,jsonb,uuid) from public, anon, authenticated;
revoke all on function public.brick1_decide_action(uuid,text,boolean,text,uuid) from public, anon, authenticated;
revoke all on function public.brick1_execute_action(uuid,text,uuid) from public, anon, authenticated;
revoke all on function public.brick1_open_exception(uuid,brick1.exception_state,text,text,jsonb,uuid) from public, anon, authenticated;
revoke all on function public.brick1_resolve_exception(uuid,text,uuid) from public, anon, authenticated;
revoke all on function public.brick1_verify_evidence_chain() from public, anon, authenticated;
revoke all on function public.brick1_order_status(uuid) from public, anon, authenticated;

grant execute on function public.brick1_propose_action(uuid,text,text,jsonb,uuid) to service_role;
grant execute on function public.brick1_decide_action(uuid,text,boolean,text,uuid) to service_role;
grant execute on function public.brick1_execute_action(uuid,text,uuid) to service_role;
grant execute on function public.brick1_open_exception(uuid,brick1.exception_state,text,text,jsonb,uuid) to service_role;
grant execute on function public.brick1_resolve_exception(uuid,text,uuid) to service_role;
grant execute on function public.brick1_verify_evidence_chain() to service_role;
grant execute on function public.brick1_order_status(uuid) to service_role;
