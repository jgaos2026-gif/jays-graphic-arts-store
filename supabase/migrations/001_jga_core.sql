-- ============================================================================
-- Jay's Graphic Arts — Supabase additions (schema: brick1)
-- ============================================================================
-- Apply FIRST, in order:
--   1. Jays-Graphic-Arts/brick1/supabase/migrations/001_brick1_operational.sql
--      (customers, orders, payment_events, production_jobs, qa_results,
--      fulfillment_events, allocations, evidence_ledger, plus
--      brick1.transition_order(), brick1.record_payment() and
--      brick1.append_evidence() — the deposit/production/QA/balance/
--      release gates enforced in the database).
--   2. This file: what the hosted Jay's Graphic Arts app needs beyond it.
--
-- Notes:
--   * The 001 migration's 10-bucket allocation (TAX 30%, SYSTEM_B 25%,
--     OPS 15%, DRAW 10%, CREDIT 10%, NODE 3%, EMERGENCY 3%, HERMES 2%,
--     CHARITY 1%, LEGACY 1%) is the Brick 1 allocation. It is NOT the
--     Talent Agent Plus 11VT split — do not mix the two.
--   * Vistaprint pricing: final = (Vistaprint cost x 1.20 + delivery at
--     cost) x 1.05, delivery never marked up. brick1.vistaprint_final_cents
--     below computes it in integer cents, flooring at each step.
--     Check: $50.00 cost + $8.00 delivery -> 5000*120/100=6000,
--     +800=6800, *105/100=7140 cents = $71.40.
--   * No secrets in this file. SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY
--     go in Render env vars; the Python server holds the service key and
--     is the only database client (same revoke-all posture as 001).
--   * No OAuth tokens are stored here; see tap.social_accounts note in
--     the Talent Agent Plus migration — same rule applies.
-- Stripe stays TEST MODE until the owner finishes Dashboard verification.
-- ============================================================================

-- Vistaprint final price in cents. Integer math, floor at each step.
create or replace function brick1.vistaprint_final_cents(p_cost_cents bigint, p_delivery_cents bigint)
returns bigint
language sql immutable as $$
  select ((p_cost_cents * 120 / 100) + p_delivery_cents) * 105 / 100;
$$;

create table if not exists brick1.print_jobs (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references brick1.orders (id),
  provider text not null default 'vistaprint',
  provider_cost_cents bigint not null check (provider_cost_cents >= 0),
  delivery_cents bigint not null default 0 check (delivery_cents >= 0),
  final_cents bigint generated always as (
    brick1.vistaprint_final_cents(provider_cost_cents, delivery_cents)
  ) stored,
  status text not null default 'QUOTED'
    check (status in ('QUOTED', 'ORDERED', 'PRINTING', 'SHIPPED', 'DELIVERED', 'FAILED')),
  provider_ref text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Shop outreach leads (the 12 verified shops, worked 5 a day).
create table if not exists brick1.leads (
  id uuid primary key default gen_random_uuid(),
  company text not null check (length(company) between 1 and 200),
  contact_name text,
  phone text,
  pitch text,
  status text not null default 'NEW'
    check (status in ('NEW', 'CONTACTED', 'INTERESTED', 'FOLLOW_UP', 'CLOSED', 'NOT_INTERESTED', 'DO_NOT_CALL')),
  last_contacted_at timestamptz,
  follow_up_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists brick1.ava_threads (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid references brick1.customers (id),
  kind text not null default 'chat',
  created_at timestamptz not null default now()
);

create table if not exists brick1.ava_messages (
  id uuid primary key default gen_random_uuid(),
  thread_id uuid not null references brick1.ava_threads (id),
  role text not null check (role in ('client', 'ava', 'owner')),
  body text not null check (length(body) between 1 and 4000),
  created_at timestamptz not null default now()
);

-- AVA braided memory: hash-linked links, written only through the BCT gate.
create table if not exists brick1.ava_memories (
  id uuid primary key default gen_random_uuid(),
  customer_id uuid references brick1.customers (id),
  link_type text not null check (link_type in ('fact', 'correction', 'decision', 'preference')),
  body text not null,
  link_hash text not null,
  prev_hash text,
  created_at timestamptz not null default now()
);

-- Studio social accounts (owner's own logins). No tokens stored here.
create table if not exists brick1.social_accounts (
  id uuid primary key default gen_random_uuid(),
  provider text not null check (provider in ('facebook', 'instagram', 'threads')),
  external_account_id text not null,
  connected_at timestamptz not null default now(),
  unique (provider)
);

-- 10% off the next order for a client who brings a new paying client.
create table if not exists brick1.referrals (
  id uuid primary key default gen_random_uuid(),
  referrer_customer_id uuid not null references brick1.customers (id),
  referred_email text not null check (length(referred_email) between 3 and 320),
  referred_customer_id uuid references brick1.customers (id),
  discount_granted boolean not null default false,
  created_at timestamptz not null default now()
);

alter table brick1.print_jobs enable row level security;
alter table brick1.leads enable row level security;
alter table brick1.ava_threads enable row level security;
alter table brick1.ava_messages enable row level security;
alter table brick1.ava_memories enable row level security;
alter table brick1.social_accounts enable row level security;
alter table brick1.referrals enable row level security;

revoke all on all tables in schema brick1 from anon, authenticated;
revoke all on all functions in schema brick1 from anon, authenticated;

-- Public wrapper so the Python server (PostgREST, service role) can ask
-- the database for the Vistaprint final price. The print_jobs.final_cents
-- generated column uses brick1.vistaprint_final_cents directly; this
-- wrapper is the same function reached over the API. Revoked from
-- public/anon/authenticated, granted to service_role only.
create or replace function public.brick1_vistaprint_final_cents(p_cost_cents bigint, p_delivery_cents bigint)
returns bigint
language sql immutable
security definer
set search_path = brick1, public
as $$
  select brick1.vistaprint_final_cents(p_cost_cents, p_delivery_cents);
$$;

revoke all on function public.brick1_vistaprint_final_cents(bigint, bigint) from public, anon, authenticated;
grant execute on function public.brick1_vistaprint_final_cents(bigint, bigint) to service_role;
