create extension if not exists pgcrypto;

create schema if not exists brick1;

create type brick1.order_state as enum (
  'INQUIRY','QUOTE','DEPOSIT_REQUIRED','DEPOSIT_CONFIRMED','PRODUCTION',
  'QA','BALANCE_REQUIRED','PAID','FULFILLED','ARCHIVED','QUARANTINED','CANCELLED'
);

create table if not exists brick1.customers (
  id uuid primary key default gen_random_uuid(),
  email text not null check (length(email) between 3 and 320),
  first_name text not null check (length(first_name) between 1 and 120),
  last_name text not null check (length(last_name) between 1 and 120),
  company text,
  created_at timestamptz not null default now()
);
create unique index if not exists customers_email_ci on brick1.customers (lower(email));

create table if not exists brick1.orders (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid not null references brick1.customers(id),
  service text not null check (length(service) between 1 and 120),
  budget text not null check (length(budget) between 1 and 120),
  timeline text not null check (length(timeline) between 1 and 120),
  brief text not null check (length(brief) between 1 and 12000),
  state brick1.order_state not null default 'INQUIRY',
  quote_total_cents bigint check (quote_total_cents is null or quote_total_cents > 0),
  deposit_required_cents bigint generated always as (
    case when quote_total_cents is null then null else ceil(quote_total_cents * 0.35)::bigint end
  ) stored,
  paid_cents bigint not null default 0 check (paid_cents >= 0),
  qa_passed boolean not null default false,
  vera_approved boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists brick1.payment_events (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references brick1.orders(id),
  provider text not null,
  provider_event_id text not null,
  amount_cents bigint not null check (amount_cents > 0),
  synthetic boolean not null default true,
  received_at timestamptz not null default now(),
  unique(provider, provider_event_id)
);

create table if not exists brick1.production_jobs (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references brick1.orders(id),
  status text not null default 'PENDING' check (status in ('PENDING','RUNNING','COMPLETE','FAILED','QUARANTINED')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists brick1.qa_results (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references brick1.orders(id),
  actor text not null,
  reflection_pass boolean not null,
  surface_pass boolean not null,
  atmosphere_pass boolean not null,
  confidence numeric(5,4) not null check (confidence between 0 and 1),
  evidence jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists brick1.fulfillment_events (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references brick1.orders(id),
  provider text not null,
  provider_ref text,
  status text not null check (status in ('REQUESTED','ACCEPTED','SHIPPED','DELIVERED','FAILED')),
  synthetic boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists brick1.allocations (
  id uuid primary key default gen_random_uuid(),
  payment_event_id uuid not null references brick1.payment_events(id),
  bucket text not null check (bucket in ('TAX','SYSTEM_B','OPS','DRAW','CREDIT','NODE','EMERGENCY','HERMES','CHARITY','LEGACY')),
  basis_points integer not null check (basis_points between 0 and 10000),
  amount_cents bigint not null check (amount_cents >= 0),
  unique(payment_event_id,bucket)
);

create table if not exists brick1.evidence_ledger (
  seq bigint generated always as identity primary key,
  correlation_id uuid not null,
  order_id uuid,
  actor text not null,
  event_type text not null,
  payload_hash text not null,
  predecessor_hash text,
  entry_hash text not null unique,
  created_at timestamptz not null default now()
);

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
  digest text;
begin
  select entry_hash into prev from brick1.evidence_ledger order by seq desc limit 1 for update;
  payload_digest := encode(digest(convert_to(coalesce(p_payload,'{}'::jsonb)::text,'utf8'),'sha256'),'hex');
  digest := encode(digest(convert_to(coalesce(prev,'GENESIS') || '|' || p_correlation_id::text || '|' || coalesce(p_order_id::text,'') || '|' || p_actor || '|' || p_event_type || '|' || payload_digest,'utf8'),'sha256'),'hex');
  insert into brick1.evidence_ledger(correlation_id,order_id,actor,event_type,payload_hash,predecessor_hash,entry_hash)
  values (p_correlation_id,p_order_id,p_actor,p_event_type,payload_digest,prev,digest);
  return digest;
end;
$$;

create or replace function brick1.valid_transition(p_from brick1.order_state, p_to brick1.order_state)
returns boolean language sql immutable as $$
  select (p_from,p_to) in (
    ('INQUIRY','QUOTE'),
    ('QUOTE','DEPOSIT_REQUIRED'),
    ('DEPOSIT_REQUIRED','DEPOSIT_CONFIRMED'),
    ('DEPOSIT_CONFIRMED','PRODUCTION'),
    ('PRODUCTION','QA'),
    ('QA','BALANCE_REQUIRED'),
    ('BALANCE_REQUIRED','PAID'),
    ('PAID','FULFILLED'),
    ('FULFILLED','ARCHIVED')
  ) or p_to in ('QUARANTINED','CANCELLED');
$$;

create or replace function brick1.transition_order(
  p_order_id uuid,
  p_to brick1.order_state,
  p_actor text,
  p_correlation_id uuid default gen_random_uuid()
) returns brick1.orders
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  o brick1.orders;
begin
  select * into o from brick1.orders where id=p_order_id for update;
  if not found then raise exception 'order_not_found'; end if;
  if not brick1.valid_transition(o.state,p_to) then raise exception 'invalid_transition:%->%',o.state,p_to; end if;
  if p_to='DEPOSIT_CONFIRMED' and (o.deposit_required_cents is null or o.paid_cents < o.deposit_required_cents) then raise exception 'deposit_not_satisfied'; end if;
  if p_to='PRODUCTION' and o.state <> 'DEPOSIT_CONFIRMED' then raise exception 'production_requires_deposit'; end if;
  if p_to='BALANCE_REQUIRED' and not o.qa_passed then raise exception 'qa_required'; end if;
  if p_to='PAID' and (o.quote_total_cents is null or o.paid_cents < o.quote_total_cents) then raise exception 'balance_not_satisfied'; end if;
  if p_to='FULFILLED' and (o.state <> 'PAID' or not o.qa_passed or not o.vera_approved) then raise exception 'release_gate_failed'; end if;
  update brick1.orders set state=p_to, updated_at=now() where id=p_order_id returning * into o;
  perform brick1.append_evidence(p_correlation_id,p_order_id,p_actor,'STATE_TRANSITION',jsonb_build_object('to',p_to));
  return o;
end;
$$;

create or replace function brick1.record_payment(
  p_order_id uuid,
  p_provider text,
  p_provider_event_id text,
  p_amount_cents bigint,
  p_synthetic boolean,
  p_actor text,
  p_correlation_id uuid default gen_random_uuid()
) returns uuid
language plpgsql
security definer
set search_path = brick1, public
as $$
declare
  payment_id uuid;
  pct integer[] := array[3000,2500,1500,1000,1000,300,300,200,100,100];
  buckets text[] := array['TAX','SYSTEM_B','OPS','DRAW','CREDIT','NODE','EMERGENCY','HERMES','CHARITY','LEGACY'];
  i integer;
  allocated bigint := 0;
  amt bigint;
begin
  insert into brick1.payment_events(order_id,provider,provider_event_id,amount_cents,synthetic)
  values(p_order_id,p_provider,p_provider_event_id,p_amount_cents,p_synthetic)
  returning id into payment_id;
  update brick1.orders set paid_cents=paid_cents+p_amount_cents,updated_at=now() where id=p_order_id;
  for i in 1..10 loop
    if i < 10 then amt := floor(p_amount_cents * pct[i] / 10000.0)::bigint; allocated := allocated + amt;
    else amt := p_amount_cents - allocated; end if;
    insert into brick1.allocations(payment_event_id,bucket,basis_points,amount_cents) values(payment_id,buckets[i],pct[i],amt);
  end loop;
  perform brick1.append_evidence(p_correlation_id,p_order_id,p_actor,'PAYMENT_RECORDED',jsonb_build_object('provider',p_provider,'event',p_provider_event_id,'amount_cents',p_amount_cents,'synthetic',p_synthetic));
  return payment_id;
end;
$$;

alter table brick1.customers enable row level security;
alter table brick1.orders enable row level security;
alter table brick1.payment_events enable row level security;
alter table brick1.production_jobs enable row level security;
alter table brick1.qa_results enable row level security;
alter table brick1.fulfillment_events enable row level security;
alter table brick1.allocations enable row level security;
alter table brick1.evidence_ledger enable row level security;

revoke all on schema brick1 from anon, authenticated;
revoke all on all tables in schema brick1 from anon, authenticated;
revoke all on all functions in schema brick1 from anon, authenticated;
