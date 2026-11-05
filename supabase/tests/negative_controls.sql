\set ON_ERROR_STOP on

DO $$
DECLARE
  inquiry jsonb;
  order_id uuid;
  req jsonb;
  req_id uuid;
  result jsonb;
  payment_id uuid;
  exception_result jsonb;
  exception_id uuid;
  allocation_total bigint;
  allocation_count integer;
  evidence_ok boolean;
BEGIN
  -- Synthetic customer/order. No real payment rail is touched.
  inquiry := public.brick1_create_inquiry(
    'synthetic-brick1@example.invalid',
    'Synthetic',
    'Customer',
    'JGA CI',
    'logo',
    '600-1200',
    '1-week',
    'SYNTHETIC TEST BRIEF - MUST REMAIN INSIDE BRICK1',
    'ci',
    gen_random_uuid()
  );
  order_id := (inquiry->>'order_id')::uuid;

  -- NEGATIVE: skipped lifecycle must fail.
  BEGIN
    PERFORM public.brick1_advance(order_id,'PRODUCTION','AVA',gen_random_uuid());
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: skipped deposit reached production';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM LIKE 'NEGATIVE_CONTROL_FAILED:%' THEN RAISE; END IF;
  END;

  -- ORION/AVA can propose, but only VERA can authorize.
  req := public.brick1_propose_action(order_id,'ORION','SET_QUOTE',jsonb_build_object('total_cents',10000),gen_random_uuid());
  req_id := (req->>'request_id')::uuid;
  BEGIN
    PERFORM public.brick1_decide_action(req_id,'AVA',true,'spoofed approval',gen_random_uuid());
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: AVA authorized its own action';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM LIKE 'NEGATIVE_CONTROL_FAILED:%' THEN RAISE; END IF;
  END;
  PERFORM public.brick1_decide_action(req_id,'VERA',true,'quote within synthetic policy',gen_random_uuid());
  BEGIN
    PERFORM public.brick1_execute_action(req_id,'ORION',gen_random_uuid());
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: ORION executed an operational action';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM LIKE 'NEGATIVE_CONTROL_FAILED:%' THEN RAISE; END IF;
  END;
  result := public.brick1_execute_action(req_id,'AVA',gen_random_uuid());
  IF result->>'request_status' <> 'EXECUTED' OR result->>'state' <> 'DEPOSIT_REQUIRED' THEN
    RAISE EXCEPTION 'QUOTE_FLOW_FAILED:%', result;
  END IF;

  -- NEGATIVE: even a VERA-authorized request cannot defeat the deposit gate.
  req := public.brick1_propose_action(order_id,'AVA','START_PRODUCTION','{}'::jsonb,gen_random_uuid());
  req_id := (req->>'request_id')::uuid;
  PERFORM public.brick1_decide_action(req_id,'VERA',true,'attempt before deposit to prove gate',gen_random_uuid());
  result := public.brick1_execute_action(req_id,'AVA',gen_random_uuid());
  IF result->>'request_status' <> 'FAILED' THEN
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: production before deposit did not remain FAILED';
  END IF;
  IF (SELECT state FROM brick1.orders WHERE id=order_id) <> 'DEPOSIT_REQUIRED' THEN
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: failed action changed order state';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM brick1.action_requests WHERE id=req_id AND status='FAILED' AND failure_code IS NOT NULL) THEN
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: failed action evidence disappeared';
  END IF;

  -- Synthetic 35% deposit.
  result := public.brick1_record_payment(order_id,'SYNTHETIC','deposit-001',3500,true,'PAYMENT_TEST',gen_random_uuid());
  IF result->>'state' <> 'DEPOSIT_CONFIRMED' THEN RAISE EXCEPTION 'DEPOSIT_GATE_FAILED:%',result; END IF;
  payment_id := (result->>'payment_id')::uuid;
  SELECT sum(amount_cents),count(*) INTO allocation_total,allocation_count FROM brick1.allocations WHERE payment_event_id=payment_id;
  IF allocation_total <> 3500 OR allocation_count <> 10 THEN
    RAISE EXCEPTION 'ALLOCATION_FAILED total=% count=%',allocation_total,allocation_count;
  END IF;

  -- NEGATIVE: provider event idempotency.
  BEGIN
    PERFORM public.brick1_record_payment(order_id,'SYNTHETIC','deposit-001',3500,true,'PAYMENT_TEST',gen_random_uuid());
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: duplicate payment event accepted';
  EXCEPTION WHEN unique_violation THEN
    NULL;
  WHEN OTHERS THEN
    IF SQLERRM LIKE 'NEGATIVE_CONTROL_FAILED:%' THEN RAISE; END IF;
    RAISE;
  END;

  -- Governed production start.
  req := public.brick1_propose_action(order_id,'AVA','START_PRODUCTION','{}'::jsonb,gen_random_uuid());
  req_id := (req->>'request_id')::uuid;
  PERFORM public.brick1_decide_action(req_id,'VERA',true,'deposit satisfied',gen_random_uuid());
  result := public.brick1_execute_action(req_id,'AVA',gen_random_uuid());
  IF result->>'state' <> 'PRODUCTION' THEN RAISE EXCEPTION 'PRODUCTION_START_FAILED:%',result; END IF;

  -- Move to independent QA lane.
  req := public.brick1_propose_action(order_id,'AVA','REQUEST_QA','{}'::jsonb,gen_random_uuid());
  req_id := (req->>'request_id')::uuid;
  PERFORM public.brick1_decide_action(req_id,'VERA',true,'production handed to QA',gen_random_uuid());
  result := public.brick1_execute_action(req_id,'AVA',gen_random_uuid());
  IF result->>'state' <> 'QA' THEN RAISE EXCEPTION 'QA_REQUEST_FAILED:%',result; END IF;

  -- NEGATIVE: AVA cannot impersonate SENTINEL.
  BEGIN
    PERFORM public.brick1_set_qa(order_id,'AVA',true,true,true,0.999,'{}'::jsonb,gen_random_uuid());
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: AVA passed its own QA';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM LIKE 'NEGATIVE_CONTROL_FAILED:%' THEN RAISE; END IF;
  END;

  result := public.brick1_set_qa(order_id,'SENTINEL',true,true,true,0.999,'{"synthetic":true}'::jsonb,gen_random_uuid());
  IF result->>'state' <> 'BALANCE_REQUIRED' OR result->>'passed' <> 'true' THEN
    RAISE EXCEPTION 'SENTINEL_QA_FAILED:%',result;
  END IF;

  -- NEGATIVE: AVA cannot impersonate VERA.
  BEGIN
    PERFORM public.brick1_set_vera_approval(order_id,'AVA',true,gen_random_uuid());
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: AVA granted VERA approval';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM LIKE 'NEGATIVE_CONTROL_FAILED:%' THEN RAISE; END IF;
  END;
  PERFORM public.brick1_set_vera_approval(order_id,'VERA',true,gen_random_uuid());

  -- NEGATIVE: release before full payment must fail and remain visible.
  req := public.brick1_propose_action(order_id,'AVA','FULFILL','{}'::jsonb,gen_random_uuid());
  req_id := (req->>'request_id')::uuid;
  PERFORM public.brick1_decide_action(req_id,'VERA',true,'prove release gate',gen_random_uuid());
  result := public.brick1_execute_action(req_id,'AVA',gen_random_uuid());
  IF result->>'request_status' <> 'FAILED' THEN
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: fulfillment before balance was not FAILED';
  END IF;

  -- Synthetic remaining 65% balance.
  result := public.brick1_record_payment(order_id,'SYNTHETIC','balance-001',6500,true,'PAYMENT_TEST',gen_random_uuid());
  IF result->>'state' <> 'PAID' THEN RAISE EXCEPTION 'BALANCE_GATE_FAILED:%',result; END IF;

  -- Fulfillment now permitted.
  req := public.brick1_propose_action(order_id,'AVA','FULFILL','{}'::jsonb,gen_random_uuid());
  req_id := (req->>'request_id')::uuid;
  PERFORM public.brick1_decide_action(req_id,'VERA',true,'paid plus QA plus VERA gate satisfied',gen_random_uuid());
  result := public.brick1_execute_action(req_id,'AVA',gen_random_uuid());
  IF result->>'state' <> 'FULFILLED' THEN RAISE EXCEPTION 'FULFILLMENT_FAILED:%',result; END IF;

  req := public.brick1_propose_action(order_id,'AVA','ARCHIVE','{}'::jsonb,gen_random_uuid());
  req_id := (req->>'request_id')::uuid;
  PERFORM public.brick1_decide_action(req_id,'VERA',true,'close synthetic cycle',gen_random_uuid());
  result := public.brick1_execute_action(req_id,'AVA',gen_random_uuid());
  IF result->>'state' <> 'ARCHIVED' THEN RAISE EXCEPTION 'ARCHIVE_FAILED:%',result; END IF;

  -- Routine exception can be supervised by AVA.
  inquiry := public.brick1_create_inquiry(
    'synthetic-exception@example.invalid','Synthetic','Exception','JGA CI','logo','under-300','flexible',
    'SECOND SYNTHETIC TEST BRIEF','ci',gen_random_uuid());
  order_id := (inquiry->>'order_id')::uuid;
  exception_result := public.brick1_open_exception(order_id,'WAITING_PAYMENT','DEPOSIT_PENDING','AVA','{"synthetic":true}'::jsonb,gen_random_uuid());
  exception_id := (exception_result->>'exception_id')::uuid;
  PERFORM public.brick1_resolve_exception(exception_id,'AVA',gen_random_uuid());

  -- NEGATIVE: AVA cannot directly open high-risk states.
  BEGIN
    PERFORM public.brick1_open_exception(order_id,'OWNER_REVIEW','HIGH_RISK','AVA','{}'::jsonb,gen_random_uuid());
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: AVA directly opened OWNER_REVIEW';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM LIKE 'NEGATIVE_CONTROL_FAILED:%' THEN RAISE; END IF;
  END;

  -- High-risk escalation: AVA requests, VERA authorizes, AVA executes.
  req := public.brick1_propose_action(order_id,'AVA','ESCALATE_EXCEPTION',jsonb_build_object('requested_state','OWNER_REVIEW','code','UNKNOWN_FINANCIAL_EXCEPTION'),gen_random_uuid());
  req_id := (req->>'request_id')::uuid;
  PERFORM public.brick1_decide_action(req_id,'VERA',true,'high-risk escalation approved',gen_random_uuid());
  result := public.brick1_execute_action(req_id,'AVA',gen_random_uuid());
  IF result->>'state' <> 'QUARANTINED' OR result->>'exception_state' <> 'OWNER_REVIEW' THEN
    RAISE EXCEPTION 'HIGH_RISK_ESCALATION_FAILED:%',result;
  END IF;
  exception_id := (result->>'exception_id')::uuid;

  BEGIN
    PERFORM public.brick1_resolve_exception(exception_id,'AVA',gen_random_uuid());
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: AVA resolved high-risk exception without VERA/OWNER';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM LIKE 'NEGATIVE_CONTROL_FAILED:%' THEN RAISE; END IF;
  END;
  PERFORM public.brick1_resolve_exception(exception_id,'VERA',gen_random_uuid());

  -- Evidence must be hash-linked and append-only.
  SELECT public.brick1_verify_evidence_chain() INTO evidence_ok;
  IF NOT evidence_ok THEN RAISE EXCEPTION 'EVIDENCE_CHAIN_FAILED'; END IF;

  BEGIN
    UPDATE brick1.evidence_ledger SET actor='TAMPER' WHERE seq=(SELECT min(seq) FROM brick1.evidence_ledger);
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: evidence ledger mutation succeeded';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM LIKE 'NEGATIVE_CONTROL_FAILED:%' THEN RAISE; END IF;
  END;

  IF EXISTS (SELECT 1 FROM brick1.evidence_ledger WHERE entry_hash LIKE '%SYNTHETIC TEST BRIEF%') THEN
    RAISE EXCEPTION 'NEGATIVE_CONTROL_FAILED: raw customer brief entered evidence ledger';
  END IF;
END;
$$;

SELECT 'BRICK1_SYNTHETIC_CYCLE_PASS' AS result;
SELECT public.brick1_verify_evidence_chain() AS evidence_chain_valid;
