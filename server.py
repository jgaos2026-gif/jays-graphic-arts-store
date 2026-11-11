"""Jay's Graphic Arts — Render app, v1.

A real, runnable storefront (not a mockup): a stdlib-only Python
HTTP server wrapping the jga.py engine, plus a phone-first PWA
frontend in frontend/. Open http://127.0.0.1:8080/ after starting it.

    python3 server.py [--port 8080] [--host 127.0.0.1]

Auth: owner setup once (8+ character password), then owner/client
login with passwords (PBKDF2 hashes, session tokens, 24h TTL).
Every mutating call needs a Bearer <redacted> Clients can act only for
themselves; the owner can act for any order.

Money rules, unchanged from the business:
- 35% to start, balance before final clean files, proof approval
  before anything is final. Clean files release ONLY after final
  payment is cleared AND the client approves the proof.
- Vistaprint print pricing: final = (Vistaprint cost x 1.20 +
  delivery at cost) x 1.05. Delivery is never marked up.
- Stripe is TEST MODE (account "Jays graphic arts sandbox",
  acct_1UY8LtRyup299Dc0, livemode false) until the owner finishes
  Dashboard verification. The app links to the real test-mode
  payment links; it moves no bank funds. Payments are recorded
  as receipts with a Stripe object id.
- Social Design ($299/mo) and Design Subscription ($499/mo) have no
  Stripe price yet: they are ordered in-app and billed by the owner
  from the Stripe Dashboard.

Limits, stated so they cannot drift:
- v1 is SINGLE-PROCESS and IN-MEMORY by default. Restarting the server
  clears all clients, orders, and sessions. A public multi-user release
  needs a real database with transactions first.
- Supabase mode: when SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set
  in the environment, the server uses the Supabase project as its durable
  store (supabase_store.py, stdlib PostgREST, service-role key held by
  the server only). Intake, quotes, payments, QA, VERA decisions, state
  advances, exceptions, and evidence go through the brick1
  security-definer functions; the 10-bucket brick1 allocation is the
  Brick 1 allocation, NOT the Talent Agent Plus 11VT. If Supabase is
  unset or unreachable at startup, the server runs in-memory exactly as
  before and says so on /api/health ("store": "in-memory"). The runtime
  engine still holds live state in memory in v1 — the adapter is the
  durable-store client, not a full engine-to-Postgres migration (that is
  verified against the live database, not claimed here).
- This is NOT an APK/AAB and NOT on the Play Store. It is the
  working app a Play Store wrapper (Capacitor/Android Studio)
  would package.
- Tokens are stored in the browser's localStorage in v1. A packaged
  phone app must move them to platform secure storage.
"""
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from jga import JGAStore, JGAError, vistaprint_quote, VERSION  # noqa: E402
from supabase_store import SupabaseStore, SupabaseError  # noqa: E402

APP = JGAStore()


def verify_stripe_payment(payment_intent_id, expected_amount_cents, order_id):
    """Retrieve the PaymentIntent from Stripe; never trust a client-supplied ID."""
    if not isinstance(payment_intent_id, str) or not payment_intent_id.startswith("pi_"):
        raise JGAError("only a Stripe PaymentIntent ID (pi_) is accepted")
    secret = os.environ.get("STRIPE_SECRET_KEY", "")
    if not secret.startswith(("sk_test_", "sk_live_")):
        raise JGAError("Stripe verification is unavailable; configure STRIPE_SECRET_KEY")
    request = urllib.request.Request(
        "https://api.stripe.com/v1/payment_intents/" + payment_intent_id,
        headers={"Authorization": "Bearer " + secret},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payment = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise JGAError("Stripe could not verify that PaymentIntent") from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise JGAError("Stripe verification request failed") from exc
    if payment.get("id") != payment_intent_id:
        raise JGAError("Stripe returned a different PaymentIntent")
    if payment.get("status") != "succeeded":
        raise JGAError("Stripe PaymentIntent is not succeeded")
    if payment.get("amount_received") != expected_amount_cents:
        raise JGAError("Stripe amount received does not match the order amount")
    if payment.get("currency") != "usd":
        raise JGAError("Stripe PaymentIntent currency must be USD")
    metadata = payment.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("order_id") != order_id:
        raise JGAError("Stripe PaymentIntent metadata must bind it to this order")
    if payment.get("livemode") is not False:
        raise JGAError("this storefront is configured for Stripe test mode only")
    return payment

# Durable store (Supabase) if configured and reachable at startup;
# otherwise the existing in-memory store, exactly as before.
# Never crashes, never logs a key — see supabase_store.py.
STORE = SupabaseStore.from_env()
STORE_MODE = "supabase" if STORE.initialize() else "in-memory"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass  # keep the console clean; errors go in the JSON body

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            self._send(404, {"error": "not found"})
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode())
        except Exception:
            return {}

    def _token(self):
        auth = self.headers.get("Authorization") or ""
        if auth.startswith("Bearer "):
            return auth[7:]
        return ""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/":
                return self._send_file(os.path.join(HERE, "frontend", "index.html"), "text/html")
            if path.startswith("/frontend/"):
                name = os.path.basename(path)
                ctype = {"html": "text/html", "js": "text/javascript",
                         "css": "text/css", "json": "application/json",
                         "svg": "image/svg+xml"}.get(name.rsplit(".", 1)[-1], "text/plain")
                return self._send_file(os.path.join(HERE, "frontend", name), ctype)
            if path == "/api/health":
                return self._send(200, {"ok": True, "product": "Jay's Graphic Arts",
                                        "version": VERSION,
                                        "banking_execution": "NONE_LEDGER_RECEIPT_ONLY",
                                        "stripe_mode": "test",
                                        "store": STORE_MODE, "supabase_configured": STORE.configured})
            if path == "/api/store/verify":
                APP._require_owner(self._token())
                if STORE.enabled:
                    try:
                        valid = STORE.verify_evidence_chain()
                    except SupabaseError as e:
                        raise JGAError("Supabase verify failed: " + str(e))
                    return self._send(200, {"ok": True, "store": STORE_MODE,
                                            "evidence_chain_valid": valid})
                return self._send(200, {"ok": True, "store": STORE_MODE,
                                        "evidence_chain_valid": None,
                                        "note": "Supabase is not configured — running in-memory; nothing to verify"})
            if path == "/api/public":
                return self._send(200, APP.public_catalog())
            if path == "/api/stripe/links":
                return self._send(200, APP.stripe_links())
            if path == "/api/orders":
                return self._send(200, APP.list_orders(self._token()))
            if path.startswith("/api/orders/"):
                oid = path.split("/")[3]
                return self._send(200, APP.get_order(self._token(), oid))
            return self._send(404, {"error": "unknown route"})
        except JGAError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": type(e).__name__ + ": " + str(e)})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._body()
        tok = self._token()
        try:
            if path == "/api/setup-owner":
                bootstrap = os.environ.get("JGA_OWNER_SETUP_TOKEN", "")
                supplied = body.get("setup_token", "")
                if not bootstrap:
                    return self._send(503, {"error": "owner bootstrap is disabled; configure JGA_OWNER_SETUP_TOKEN"})
                if not isinstance(supplied, str) or not hmac.compare_digest(supplied, bootstrap):
                    return self._send(403, {"error": "invalid owner bootstrap credential"})
                APP.create_owner(body["password"])
                return self._send(200, {"ok": True})
            if path == "/api/login-owner":
                return self._send(200, {"token": APP.login_owner(body["password"]),
                                        "principal": "owner"})
            if path == "/api/logout":
                APP.logout(tok)
                return self._send(200, {"ok": True})
            if path == "/api/clients":
                return self._send(200, APP.create_client(body["name"], body["email"],
                                                         body["password"]))
            if path == "/api/login-client":
                token, cid = APP.login_client(body["email"], body["password"])
                return self._send(200, {"token": token, "principal": cid})
            if path == "/api/vistaprint/quote":
                return self._send(200, vistaprint_quote(
                    int(body["vistaprint_cost_cents"]), int(body["delivery_cents"])))
            if path == "/api/orders":
                return self._send(200, APP.create_order(
                    tok, package_id=body.get("package_id"),
                    service_id=body.get("service_id"),
                    brief=body.get("brief", ""),
                    print_product_id=body.get("print_product_id")))
            if path.startswith("/api/orders/") and path.endswith("/vistaprint-quote"):
                oid = path.split("/")[3]
                return self._send(200, APP.set_print_quote(
                    tok, oid, int(body["vistaprint_cost_cents"]),
                    int(body["delivery_cents"])))
            if path.startswith("/api/orders/") and path.endswith("/approve-proof"):
                oid = path.split("/")[3]
                return self._send(200, APP.approve_proof(tok, oid))
            if path.startswith("/api/orders/") and path.endswith("/state"):
                oid = path.split("/")[3]
                return self._send(200, APP.set_order_state(tok, oid, body["state"]))
            if path.startswith("/api/orders/") and path.endswith("/record-payment"):
                oid = path.split("/")[3]
                expected = APP.expected_payment_amount(tok, oid, body["kind"])
                verified = verify_stripe_payment(body["stripe_object_id"], expected, oid)
                return self._send(200, APP.record_payment(
                    tok, oid, body["stripe_object_id"],
                    int(body["amount_cents"]), body["kind"], verified))
            return self._send(404, {"error": "unknown route"})
        except (JGAError, KeyError, ValueError, TypeError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": type(e).__name__ + ": " + str(e)})


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    a = p.parse_args()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Jay's Graphic Arts v{VERSION} running at http://{a.host}:{a.port}/  (Ctrl-C stops it)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
