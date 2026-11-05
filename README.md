# Jay's Graphic Arts — Render app, v1

A real, runnable storefront, not a mockup. Python stdlib only — no pip installs.
Same pattern as the Talent Agent Plus render package: free plan, Python 3,
`python3 server.py --host 0.0.0.0 --port $PORT`, health check `/`.

## Run it

```bash
python3 server.py --port 8080
# open http://127.0.0.1:8080/
```

First run: set an owner password (8+ characters) on the Owner tab, sign in
as owner. Clients create their own account on the Account tab. The app
starts empty — no demo clients or orders.

## What it does, with the real rules

Storefront (public, no sign-in): services, packages, print products,
process, terms. Ordering: package, service, or Vistaprint print order
with the client's brief. Order flow: REQUESTED → IN_DESIGN → PROOF_SENT
→ client approves → PROOF_APPROVED → DELIVERED. Clean files release
only after final payment is cleared AND the client approves the proof.

Packages: Quick Creative Help $75, Logo Launch $350, Business Brand $750,
Brand & Social Launch $1,250, Social Design $299/mo, Design Subscription
$499/mo. Terms: 35% to start, balance before final clean files.

Vistaprint print pricing: final = (Vistaprint cost × 1.20 + delivery at
cost) × 1.05, full breakdown shown on every print order. Delivery is
never marked up. Example the tests check: $50.00 + $8.00 delivery →
$60.00 + $8.00 = $68.00 × 1.05 = $71.40.

Stripe is TEST MODE (account "Jays graphic arts sandbox",
acct_1UY8LtRyup299Dc0, livemode false). The app links to the real
test-mode payment links for the four one-time packages: full payment,
35% deposit, and 65% balance. Payments are recorded as receipts with
a Stripe object id; the app moves no bank funds. Social Design and
Design Subscription have NO Stripe price yet — they are ordered in-app
and billed by the owner from the Stripe Dashboard (invoice / payment
link). The app says so plainly wherever they appear.

Privacy: a signed-in client sees only their own orders. The owner sees
all orders. Signed-out visitors see only the public catalog.

## Tests

```bash
python3 -m unittest tests.test_server -v
```

Covers: health + public catalog, the real Stripe links verbatim,
the Vistaprint $50/$8 = $71.40 example, the full order journey
(order → design → proof → approve → deposit → balance → deliver),
negatives (unauthenticated order refused, second client cannot see
the first client's orders, deliver before proof approval refused,
deliver before full payment refused, wrong-amount payment refused,
payment without a Stripe id refused), and a print order priced by
the Vistaprint formula.

## Limits — read before a real client uses it

- Single-process, in-memory v1. Restarting the server clears all
  clients, orders, and sessions. A public multi-user release needs a
  real database with transactions first.
- Tokens live in browser localStorage in v1. A packaged phone app
  must move them to platform secure storage.
- This is NOT an APK/AAB and is NOT on the Play Store. The Play Store
  step is packaging this web app (Capacitor/Android Studio) plus
  John's Google Play developer account.
- Stripe stays test mode until John finishes onboarding in the Stripe
  Dashboard (EIN, SSN, address, payout bank = his Chime account).
  No real money moves until then.
- No live Vistaprint API order is placed by the app: the owner enters
  the Vistaprint cost and delivery from the Vistaprint quote, the app
  prices it, and fulfillment runs through Vistaprint.
- Nothing in this package links out to any app-builder service.
