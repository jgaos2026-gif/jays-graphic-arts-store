"""Jay's Graphic Arts server tests — stdlib only. Starts the real server in a thread."""
import json
import os
import sys
import threading
import unittest
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import ThreadingHTTPServer, Handler  # noqa: E402

LINKS = {
    "quick": ("https://buy.stripe.com/test_aFa3cxgJN2285H586SdAk04",
              "https://buy.stripe.com/test_fZu9AV2SX7msb1pcn8dAk0n",
              "https://buy.stripe.com/test_6oUbJ3515gX26L9af0dAk0d"),
    "logo": ("https://buy.stripe.com/test_8x28wReBF8qwb1p9aWdAk05",
             "https://buy.stripe.com/test_4gMfZjgJN0Y40mL0EqdAk0o",
             "https://buy.stripe.com/test_00w3cx659ayEedBgDodAk0e"),
    "brand": ("https://buy.stripe.com/test_28EfZj9hl4ag3yX5YKdAk06",
              "https://buy.stripe.com/test_bJeeVfeBF6ioc5tdrcdAk0p",
              "https://buy.stripe.com/test_5kQ4gBeBFeOUd9xevgdAk0f"),
    "launch": ("https://buy.stripe.com/test_bJe9AV659ayE8Th5YKdAk07",
               "https://buy.stripe.com/test_6oU9AV8dh36c5H586SdAk0q",
               "https://buy.stripe.com/test_fZucN7bpt4ag4D10EqdAk0g"),
}


def call(base, path, body=None, token=None):
    req = urllib.request.Request(base + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": "Bearer " + token} if token else {})},
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class TestJGA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.base = "http://127.0.0.1:%d" % cls.port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        os.environ["JGA_OWNER_SETUP_TOKEN"] = "test-only-bootstrap-token"
        _, r = call(cls.base, "/api/setup-owner", {
            "password": "owner-pass-123",
            "setup_token": os.environ["JGA_OWNER_SETUP_TOKEN"]
        })
        assert r.get("ok")
        _, r = call(cls.base, "/api/login-owner", {"password": "owner-pass-123"})
        cls.owner = r["token"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_health_and_public(self):
        s, h = call(self.base, "/api/health")
        self.assertEqual(s, 200)
        self.assertEqual(h["stripe_mode"], "test")
        s, cat = call(self.base, "/api/public")
        self.assertEqual(s, 200)
        self.assertEqual(len(cat["packages"]), 6)
        self.assertTrue(len(cat["services"]) >= 11)
        self.assertEqual(cat["business"]["phone"], "(779) 396-6934")
        # Stripe links are the real test-mode links, verbatim
        s, links = call(self.base, "/api/stripe/links")
        pkgs = {p["id"]: p for p in links["packages"]}
        for pid, (full, dep, bal) in LINKS.items():
            self.assertEqual(pkgs[pid]["full_link"], full, pid)
            self.assertEqual(pkgs[pid]["deposit_link"], dep, pid)
            self.assertEqual(pkgs[pid]["balance_link"], bal, pid)
        # Social Design / Design Subscription have no Stripe price yet
        self.assertIsNone(pkgs["social"]["price_id"])
        self.assertIsNone(pkgs["design-subscription"]["price_id"])

    def test_vistaprint_quote_example(self):
        # $50.00 cost + $8.00 delivery -> $60.00 + $8.00 = $68.00 x 1.05 = $71.40
        s, q = call(self.base, "/api/vistaprint/quote",
                    {"vistaprint_cost_cents": 5000, "delivery_cents": 800})
        self.assertEqual(s, 200)
        self.assertEqual(q["product_cents"], 6000)
        self.assertEqual(q["delivery_cents"], 800)
        self.assertEqual(q["subtotal_cents"], 6800)
        self.assertEqual(q["final_markup_cents"], 340)
        self.assertEqual(q["total_cents"], 7140)
        s, _ = call(self.base, "/api/vistaprint/quote",
                    {"vistaprint_cost_cents": -100, "delivery_cents": 0})
        self.assertEqual(s, 400)

    def test_order_flow_end_to_end(self):
        b, owner = self.base, self.owner
        # two clients; second must never see the first's orders
        s, c1 = call(b, "/api/clients", {"name": "Alice", "email": "alice@example.com",
                                         "password": "alice-pass-123"})
        self.assertEqual(s, 200, c1)
        s, l1 = call(b, "/api/login-client", {"email": "alice@example.com",
                                              "password": "alice-pass-123"})
        atok = l1["token"]
        s, c2 = call(b, "/api/clients", {"name": "Bob", "email": "bob@example.com",
                                         "password": "bob-pass-123"})
        s, l2 = call(b, "/api/login-client", {"email": "bob@example.com",
                                              "password": "bob-pass-123"})
        btok = l2["token"]
        # unauthenticated order must be refused
        s, _ = call(b, "/api/orders", {"package_id": "logo", "brief": "logo please"})
        self.assertEqual(s, 400)
        # Alice orders Logo Launch: $350, deposit $122.50, balance $227.50
        s, o = call(b, "/api/orders", {"package_id": "logo", "brief": "Bold logo"},
                    atok)
        self.assertEqual(s, 200, o)
        self.assertEqual(o["price_cents"], 35000)
        self.assertEqual(o["deposit_cents"], 12250)
        self.assertEqual(o["balance_cents"], 22750)
        oid = o["order_id"]
        # Bob cannot see Alice's order
        s, _ = call(b, "/api/orders/" + oid, None, btok)
        self.assertEqual(s, 400)
        s, mine = call(b, "/api/orders", None, btok)
        self.assertEqual(mine, [])
        # owner cannot deliver before proof approval + full payment
        call(b, "/api/orders/%s/state" % oid, {"state": "IN_DESIGN"}, owner)
        call(b, "/api/orders/%s/state" % oid, {"state": "PROOF_SENT"}, owner)
        s, _ = call(b, "/api/orders/%s/state" % oid, {"state": "DELIVERED"}, owner)
        self.assertEqual(s, 400)
        # wrong-amount payment refused; missing Stripe id refused
        s, _ = call(b, "/api/orders/%s/record-payment" % oid,
                    {"stripe_object_id": "pi_test123", "amount_cents": 9999,
                     "kind": "deposit"}, owner)
        self.assertEqual(s, 400)
        s, _ = call(b, "/api/orders/%s/record-payment" % oid,
                    {"stripe_object_id": "not-a-stripe-id", "amount_cents": 12250,
                     "kind": "deposit"}, owner)
        self.assertEqual(s, 400)
        # deposit recorded, then client approves the proof
        s, o = call(b, "/api/orders/%s/record-payment" % oid,
                    {"stripe_object_id": "pi_test_deposit", "amount_cents": 12250,
                     "kind": "deposit"}, owner)
        self.assertEqual(s, 200)
        self.assertTrue(o["deposit_paid"])
        # A full payment cannot be stacked on top of an already-recorded deposit.
        s, _ = call(b, "/api/orders/%s/record-payment" % oid,
                    {"stripe_object_id": "pi_test_full_after_deposit",
                     "amount_cents": 35000, "kind": "full"}, owner)
        self.assertEqual(s, 400)
        # A Stripe object cannot be reused for another payment on this order.
        s, _ = call(b, "/api/orders/%s/record-payment" % oid,
                    {"stripe_object_id": "pi_test_deposit",
                     "amount_cents": 22750, "kind": "balance"}, owner)
        self.assertEqual(s, 400)
        s, o = call(b, "/api/orders/%s/approve-proof" % oid, {}, atok)
        self.assertEqual(s, 200)
        self.assertEqual(o["state"], "PROOF_APPROVED")
        # still cannot deliver: balance unpaid
        s, _ = call(b, "/api/orders/%s/state" % oid, {"state": "DELIVERED"}, owner)
        self.assertEqual(s, 400)
        s, o = call(b, "/api/orders/%s/record-payment" % oid,
                    {"stripe_object_id": "pi_test_balance", "amount_cents": 22750,
                     "kind": "balance"}, owner)
        self.assertEqual(s, 200)
        self.assertTrue(o["paid_in_full"])
        s, o = call(b, "/api/orders/%s/state" % oid, {"state": "DELIVERED"}, owner)
        self.assertEqual(s, 200)
        self.assertTrue(o["clean_files_released"])

    def test_print_order_uses_vistaprint_formula(self):
        b, owner = self.base, self.owner
        s, l1 = call(b, "/api/login-client", {"email": "alice@example.com",
                                              "password": "alice-pass-123"})
        atok = l1["token"]
        s, o = call(b, "/api/orders", {"print_product_id": "print-flyers",
                                       "brief": "100 flyers",
                                       "vistaprint_cost_cents": 5000,
                                       "delivery_cents": 800}, atok)
        self.assertEqual(s, 200, o)
        self.assertEqual(o["price_cents"], 7140)
        self.assertEqual(o["deposit_cents"], 2499)  # 35% of 7140
        self.assertEqual(o["vistaprint"]["total_cents"], 7140)


if __name__ == "__main__":
    unittest.main()
