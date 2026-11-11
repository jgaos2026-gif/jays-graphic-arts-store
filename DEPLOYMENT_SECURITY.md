# Store security deployment requirements

## Owner enrollment

Set JGA_OWNER_SETUP_TOKEN to a high-entropy secret in the service environment. Use it once in the owner setup screen together with the owner password. Do not put it in frontend source, a committed file, or a public build artifact. If the token is not configured, owner bootstrap is disabled.

## Stripe test-mode receipt verification

Set STRIPE_SECRET_KEY to the Stripe test-mode secret key (sk_test_...). The server retrieves each PaymentIntent directly from Stripe and refuses receipts unless:

- the PaymentIntent ID has valid pi_ identifier syntax;
- Stripe reports status=succeeded;
- amount_received exactly equals the order amount for the payment kind;
- currency is USD and the object is in test mode;
- Stripe metadata contains order_id matching the order being paid;
- the PaymentIntent has not already been recorded against any order.

The app deliberately refuses manually typed IDs without live Stripe verification. A PaymentIntent must have its metadata[order_id] set to the exact order ID before it can be recorded. Payment Links that do not provide this order-specific metadata will not satisfy this verification.

## Vistaprint pricing

A client can submit a print-order request but cannot set its own supplier cost, shipping cost, final price, deposit, or balance. The order remains unpriced until the authenticated owner enters the Vistaprint supplier quote. Only then does the server calculate the formula and allow the workflow to advance.

## Persistence limitation

This security patch does not complete the engine's durable-state migration. Do not treat a configured Supabase evidence adapter as proof that every client, order, session, payment, and BCT head is transactionally persisted. Verify live migrations and complete engine-state persistence before multi-instance or production use.
