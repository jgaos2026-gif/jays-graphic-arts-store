"""Jay's Graphic Arts — store engine (stdlib only, in-memory v1).

Black-and-gold storefront for Jay's Graphic Arts LLC, Peoria, IL.
Real rules, unchanged from the business:

- Packages: Quick Creative Help from $75, Logo Launch from $350,
  Business Brand from $750, Brand & Social Launch from $1,250,
  Social Design from $299/month, Design Subscription $499/month.
- Terms: 35% to start, balance before final clean files, proof
  approval before anything is final, clean files only after final
  payment + proof approval.
- Vistaprint print pricing: final = (Vistaprint cost x 1.20 +
  delivery at cost) x 1.05, full breakdown shown. Delivery is never
  marked up.
- Stripe is TEST MODE (account "Jays graphic arts sandbox",
  acct_1UY8LtRyup299Dc0, livemode false) until John finishes
  Dashboard verification. The payment links below are the real
  test-mode links from that account, copied verbatim — no new
  products were created. Social Design and Design Subscription have
  NO Stripe price yet: they are ordered in-app and billed by the
  owner from the Stripe Dashboard (invoice / payment link).
- Money is receipt-only: the app records cleared Stripe payments
  (a Stripe object id is required); it moves no bank funds.
- Privacy: a signed-in client sees ONLY their own orders. The owner
  sees all orders. Signed-out visitors see only the public catalog.
"""
import bct_verify
import hashlib
import secrets
import time

VERSION = "1.0.0-render"

BUSINESS = {
    "name": "Jay's Graphic Arts LLC",
    "display_name": "Jay's Graphic Arts",
    "city": "Peoria, Illinois",
    "phone": "(779) 396-6934",
    "email": "johnarenz@jaysgraphicarts.com",
    "terms": "35% to start, balance before final clean files, "
             "proof approval before anything is final.",
}

# Stripe test-mode catalog (acct_1UY8LtRyup299Dc0, livemode false).
# Full-payment and 35%-deposit payment links, copied verbatim from
# the account. Balance links are 65% of the package price.
PACKAGES = [
    {"id": "quick", "name": "Quick Creative Help", "price_cents": 7500,
     "billing": "one-time", "revisions": 2,
     "blurb": "One quick design job: a poster, menu refresh, social graphic, "
              "business card, or price list.",
     "price_id": "price_1UYMKhRyup299Dc0UUonmxx7",
     "product_id": "prod_VZVLBHaNiwOmd3",
     "full_link": "https://buy.stripe.com/test_aFa3cxgJN2285H586SdAk04",
     "deposit_price_id": "price_1UYO5yRyup299Dc0szI9Fghn",
     "deposit_link": "https://buy.stripe.com/test_fZu9AV2SX7msb1pcn8dAk0n",
     "balance_link": "https://buy.stripe.com/test_6oUbJ3515gX26L9af0dAk0d"},
    {"id": "logo", "name": "Logo Launch", "price_cents": 35000,
     "billing": "one-time", "revisions": 2,
     "blurb": "Logo package: concepts, revisions, and the full file set "
              "(print + web + social).",
     "price_id": "price_1UYMKjRyup299Dc0JpT37nMc",
     "product_id": "prod_VZVLJR1obeilpe",
     "full_link": "https://buy.stripe.com/test_8x28wReBF8qwb1p9aWdAk05",
     "deposit_price_id": "price_1UYO60Ryup299Dc0g3oMDd1C",
     "deposit_link": "https://buy.stripe.com/test_4gMfZjgJN0Y40mL0EqdAk0o",
     "balance_link": "https://buy.stripe.com/test_00w3cx659ayEedBgDodAk0e"},
    {"id": "brand", "name": "Business Brand", "price_cents": 75000,
     "billing": "one-time", "revisions": 3,
     "blurb": "Logo + colors + fonts + business card & letterhead + social kit.",
     "price_id": "price_1UYMKkRyup299Dc0yNCJ1ahG",
     "product_id": "prod_VZVLqc8fngrXjE",
     "full_link": "https://buy.stripe.com/test_28EfZj9hl4ag3yX5YKdAk06",
     "deposit_price_id": "price_1UYO61Ryup299Dc0q0PfvRXy",
     "deposit_link": "https://buy.stripe.com/test_bJeeVfeBF6ioc5tdrcdAk0p",
     "balance_link": "https://buy.stripe.com/test_5kQ4gBeBFeOUd9xevgdAk0f"},
    {"id": "launch", "name": "Brand & Social Launch", "price_cents": 125000,
     "billing": "one-time", "revisions": 3,
     "blurb": "Full brand plus social launch kit — the complete JGA package.",
     "price_id": "price_1UYMKmRyup299Dc0yfBJhBTC",
     "product_id": "prod_VZVLgrteHt1UXW",
     "full_link": "https://buy.stripe.com/test_bJe9AV659ayE8Th5YKdAk07",
     "deposit_price_id": "price_1UYO62Ryup299Dc05aVZKlA8",
     "deposit_link": "https://buy.stripe.com/test_6oU9AV8dh36c5H586SdAk0q",
     "balance_link": "https://buy.stripe.com/test_fZucN7bpt4ag4D10EqdAk0g"},
    {"id": "social", "name": "Social Design", "price_cents": 29900,
     "billing": "monthly", "revisions": None,
     "blurb": "Social design every month: posts, stories, and campaign "
              "graphics, done for you.",
     "price_id": None, "product_id": None,
     "full_link": None, "deposit_price_id": None,
     "deposit_link": None, "balance_link": None,
     "stripe_note": "No Stripe price yet — order in-app; the owner bills "
                    "this subscription from the Stripe Dashboard."},
    {"id": "design-subscription", "name": "Design Subscription",
     "price_cents": 49900, "billing": "monthly", "revisions": None,
     "blurb": "Ongoing design subscription: a standing design service for "
              "your business, month to month.",
     "price_id": None, "product_id": None,
     "full_link": None, "deposit_price_id": None,
     "deposit_link": None, "balance_link": None,
     "stripe_note": "No Stripe price yet — order in-app; the owner bills "
                    "this subscription from the Stripe Dashboard."},
]

# 12 orderable services across the five families from the portfolio.
SERVICES = [
    {"id": "logo-design", "family": "Brand identity",
     "name": "Logo design", "from_cents": 35000,
     "blurb": "A logo that gives a business a face."},
    {"id": "brand-identity", "family": "Brand identity",
     "name": "Brand identity", "from_cents": 75000,
     "blurb": "Logo, colors, fonts, and the full identity system."},
    {"id": "brand-guidelines", "family": "Brand identity",
     "name": "Brand guidelines", "from_cents": 35000,
     "blurb": "The rules that keep a brand recognizable everywhere."},
    {"id": "business-cards", "family": "Print + business essentials",
     "name": "Business cards", "from_cents": None,
     "blurb": "Printed by Vistaprint — priced from the Vistaprint quote.",
     "print": True},
    {"id": "flyers", "family": "Promotion + events",
     "name": "Flyers", "from_cents": None,
     "blurb": "Printed by Vistaprint — priced from the Vistaprint quote.",
     "print": True},
    {"id": "posters", "family": "Promotion + events",
     "name": "Posters", "from_cents": None,
     "blurb": "Printed by Vistaprint — priced from the Vistaprint quote.",
     "print": True},
    {"id": "event-tickets", "family": "Promotion + events",
     "name": "Event tickets", "from_cents": None,
     "blurb": "Printed by Vistaprint — priced from the Vistaprint quote.",
     "print": True},
    {"id": "social-graphics", "family": "Social + digital",
     "name": "Social media graphics", "from_cents": 7500,
     "blurb": "Posts, stories, and profile graphics."},
    {"id": "digital-ads", "family": "Social + digital",
     "name": "Digital ad creative", "from_cents": 7500,
     "blurb": "Ad creatives sized for the platforms you run on."},
    {"id": "brochures-menus", "family": "Print + business essentials",
     "name": "Brochures & menus", "from_cents": None,
     "blurb": "Printed by Vistaprint — priced from the Vistaprint quote.",
     "print": True},
    {"id": "banners-signage", "family": "Promotion + events",
     "name": "Banners & signage", "from_cents": None,
     "blurb": "Printed by Vistaprint — priced from the Vistaprint quote.",
     "print": True},
    {"id": "custom-creative", "family": "Custom creative",
     "name": "Custom creative", "from_cents": None,
     "blurb": "Anything else creative — quoted up front, before work starts."},
]

PRINT_PRODUCTS = [
    {"id": "print-business-cards", "name": "Business cards"},
    {"id": "print-flyers", "name": "Flyers"},
    {"id": "print-posters", "name": "Posters"},
    {"id": "print-event-tickets", "name": "Event tickets"},
    {"id": "print-postcards", "name": "Postcards"},
    {"id": "print-brochures", "name": "Brochures"},
]

ORDER_STATES = ["REQUESTED", "IN_DESIGN", "PROOF_SENT", "PROOF_APPROVED",
                "DELIVERED", "CANCELLED"]


class JGAError(Exception):
    pass


def _hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 salt.encode(), 100000).hex()
    return salt + ":" + digest


def _check_password(password, stored):
    try:
        salt, digest = stored.split(":")
    except ValueError:
        return False
    return secrets.compare_digest(
        hashlib.pbkdf2_hmac("sha256", password.encode(),
                            salt.encode(), 100000).hex(), digest)


def _money(cents):
    return cents  # all amounts are integer cents, never floats


def deposit_cents(price_cents):
    """35% of the price, rounded half-up to the cent."""
    return (price_cents * 35 + 50) // 100


def vistaprint_quote(vistaprint_cost_cents, delivery_cents):
    """final = (Vistaprint cost x 1.20 + delivery at cost) x 1.05.

    Delivery is never marked up. Every line of the breakdown is
    returned so the client sees the whole price.
    """
    if not isinstance(vistaprint_cost_cents, int) or not isinstance(delivery_cents, int):
        raise JGAError("Vistaprint cost and delivery must be whole cents")
    if vistaprint_cost_cents < 0 or delivery_cents < 0:
        raise JGAError("Vistaprint cost and delivery cannot be negative")
    product_cents = (vistaprint_cost_cents * 120 + 50) // 100  # +20%
    subtotal_cents = product_cents + delivery_cents
    markup_cents = (subtotal_cents * 5 + 50) // 100            # +5% final
    total_cents = subtotal_cents + markup_cents
    return {
        "vistaprint_cost_cents": vistaprint_cost_cents,
        "product_cents": product_cents,
        "product_markup_pct": 20,
        "delivery_cents": delivery_cents,
        "delivery_markup_pct": 0,
        "subtotal_cents": subtotal_cents,
        "final_markup_cents": markup_cents,
        "final_markup_pct": 5,
        "total_cents": total_cents,
    }


class JGAStore:
    def __init__(self):
        self.owner_hash = None
        self.sessions = {}   # token -> (kind, principal, expires)
        self.clients = {}    # client_id -> dict
        self.clients_by_email = {}
        self.orders = {}     # order_id -> dict
        self._next_client = 1
        self._next_order = 1
        # BCT state chains (milestone 2): order_id -> hash of the order's
        # last certified state. The chain head is the root of trust for
        # transition verification. Rejections are recorded, never silent.
        # (In-memory only in v1, like the orders themselves.)
        self._bct_chain_head: dict[str, str] = {}
        self._bct_rejections: list[dict] = []

    # -- auth ---------------------------------------------------------
    def create_owner(self, password):
        if self.owner_hash is not None:
            raise JGAError("owner already set up")
        if not password or len(password) < 8:
            raise JGAError("owner password must be 8+ characters")
        self.owner_hash = _hash_password(password)

    def login_owner(self, password):
        if self.owner_hash is None:
            raise JGAError("owner not set up yet")
        if not _check_password(password, self.owner_hash):
            raise JGAError("wrong owner password")
        tok = secrets.token_hex(32)
        self.sessions[tok] = ("owner", "owner", time.time() + 24 * 3600)
        return tok

    def create_client(self, name, email, password):
        if not name or not email or "@" not in email:
            raise JGAError("a name and a real email are required")
        email = email.strip().lower()
        if email in self.clients_by_email:
            raise JGAError("an account with that email already exists")
        if not password or len(password) < 8:
            raise JGAError("client password must be 8+ characters")
        cid = "client-%04d" % self._next_client
        self._next_client += 1
        self.clients[cid] = {"client_id": cid, "name": name.strip(),
                             "email": email, "password_hash": _hash_password(password)}
        self.clients_by_email[email] = cid
        return {"client_id": cid, "name": name.strip(), "email": email}

    def login_client(self, email, password):
        cid = self.clients_by_email.get((email or "").strip().lower())
        if cid is None:
            raise JGAError("no account with that email")
        if not _check_password(password, self.clients[cid]["password_hash"]):
            raise JGAError("wrong password")
        tok = secrets.token_hex(32)
        self.sessions[tok] = ("client", cid, time.time() + 24 * 3600)
        return tok, cid

    def logout(self, token):
        self.sessions.pop(token, None)

    def _principal(self, token):
        sess = self.sessions.get(token or "")
        if not sess:
            raise JGAError("sign in first")
        kind, principal, expires = sess
        if time.time() > expires:
            self.sessions.pop(token, None)
            raise JGAError("session expired — sign in again")
        return kind, principal

    def _require_owner(self, token):
        kind, principal = self._principal(token)
        if kind != "owner":
            raise JGAError("owner only")
        return principal

    def _require_client(self, token):
        kind, principal = self._principal(token)
        if kind != "client":
            raise JGAError("client sign-in required")
        return principal

    # -- public catalog ------------------------------------------------
    def public_catalog(self):
        return {
            "business": BUSINESS,
            "services": SERVICES,
            "packages": [{k: v for k, v in p.items()} for p in PACKAGES],
            "print_products": PRINT_PRODUCTS,
            "process": ["Discover", "Direction", "Design", "Revise", "Deliver"],
            "design_directions": ["Street Bold", "Quiet Premium",
                                  "Community Warmth", "Digital Rhythm"],
            "terms": BUSINESS["terms"],
            "stripe": {
                "mode": "test",
                "notice": "Stripe is in TEST MODE until the owner finishes "
                          "verification in the Stripe Dashboard. No real "
                          "money moves.",
                "account": "Jays graphic arts sandbox",
            },
            "vistaprint": {
                "formula": "final = (Vistaprint cost x 1.20 + delivery at "
                           "cost) x 1.05",
                "note": "Delivery is charged at Vistaprint's actual shipping "
                        "cost and is never marked up.",
            },
            "data_notice": "In-memory v1: restarting the server clears all "
                           "orders and sessions. The free Render instance "
                           "sleeps after inactivity.",
        }

    def stripe_links(self):
        return {"mode": "test",
                "notice": "Test mode — no real money moves until the owner "
                          "finishes Stripe verification.",
                "packages": [{k: v for k, v in p.items()} for p in PACKAGES]}

    # -- orders ---------------------------------------------------------
    def create_order(self, token, package_id=None, service_id=None,
                     brief="", print_product_id=None,
                     vistaprint_cost_cents=None, delivery_cents=None):
        cid = self._require_client(token)
        if not (brief or "").strip() and not package_id and not service_id:
            raise JGAError("tell us what you need — a package, a service, or a brief")
        order = {"client_id": cid, "brief": (brief or "").strip(),
                 "state": "REQUESTED", "proof": "none",
                 "deposit_paid": False, "balance_paid": False,
                 "paid_in_full": False, "payments": [],
                 "vistaprint": None, "clean_files_released": False}
        if package_id:
            pkg = next((p for p in PACKAGES if p["id"] == package_id), None)
            if pkg is None:
                raise JGAError("unknown package")
            if pkg["billing"] == "monthly":
                raise JGAError("subscriptions are ordered in-app and billed "
                               "by the owner from the Stripe Dashboard")
            order.update({"kind": "package", "package_id": pkg["id"],
                          "title": pkg["name"], "price_cents": pkg["price_cents"]})
        elif print_product_id:
            prod = next((p for p in PRINT_PRODUCTS if p["id"] == print_product_id), None)
            if prod is None:
                raise JGAError("unknown print product")
            if vistaprint_cost_cents is None or delivery_cents is None:
                raise JGAError("a print order needs the Vistaprint cost and "
                               "the delivery cost from the Vistaprint quote")
            quote = vistaprint_quote(int(vistaprint_cost_cents), int(delivery_cents))
            order.update({"kind": "print", "print_product_id": prod["id"],
                          "title": prod["name"] + " (Vistaprint print)",
                          "price_cents": quote["total_cents"],
                          "vistaprint": quote})
        elif service_id:
            svc = next((s for s in SERVICES if s["id"] == service_id), None)
            if svc is None:
                raise JGAError("unknown service")
            price = svc.get("from_cents")
            if price is None:
                raise JGAError("this service is quoted up front — write your "
                               "brief and the owner will quote it")
            order.update({"kind": "service", "service_id": svc["id"],
                          "title": svc["name"], "price_cents": price})
        else:
            raise JGAError("pick a package, a service, or a print product")
        order["deposit_cents"] = deposit_cents(order["price_cents"])
        order["balance_cents"] = order["price_cents"] - order["deposit_cents"]
        oid = "order-%04d" % self._next_order
        self._next_order += 1
        order["order_id"] = oid
        self.orders[oid] = order
        return self._order_view(order, cid)

    def _order_view(self, order, viewer_client_id=None):
        view = {k: v for k, v in order.items()}
        return view

    def list_orders(self, token):
        kind, principal = self._principal(token)
        if kind == "owner":
            return [self._order_view(o) for o in self.orders.values()]
        return [self._order_view(o) for o in self.orders.values()
                if o["client_id"] == principal]

    def get_order(self, token, order_id):
        kind, principal = self._principal(token)
        order = self.orders.get(order_id)
        if order is None:
            raise JGAError("no such order")
        if kind == "client" and order["client_id"] != principal:
            raise JGAError("that order is not yours")
        return self._order_view(order)

    def _bct_certify_order_transition(self, order, to_state):
        """BCT gate (milestone 2): an order state change is certified only
        when the transition is chained to the order's recorded state hash
        and actually changes state — the fixed BctVerifier layer-5 rules
        (see bct_verify.py). Returns the new state hash and advances the
        chain head. Raises JGAError and records the rejection on any
        failure: the transition must not proceed."""
        prior = self._bct_chain_head.get(order["order_id"], bct_verify.GENESIS)
        new_state = {"order_id": order["order_id"], "state": to_state}
        declared = bct_verify.state_hash(new_state)
        transition = bct_verify.compute_transition_hash(declared, prior)
        ok, reason = bct_verify.verify_transition(prior, new_state, declared, transition)
        if not ok:
            self._bct_rejections.append({
                "order_id": order["order_id"], "from_state": order["state"],
                "to_state": to_state, "reason": reason})
            raise JGAError(
                "BCT verification failed for order %s: %s" % (order["order_id"], reason))
        self._bct_chain_head[order["order_id"]] = declared
        return declared

    def approve_proof(self, token, order_id):
        cid = self._require_client(token)
        order = self.orders.get(order_id)
        if order is None:
            raise JGAError("no such order")
        if order["client_id"] != cid:
            raise JGAError("that order is not yours")
        if order["proof"] != "sent":
            raise JGAError("there is no proof waiting for your approval yet")
        self._bct_certify_order_transition(order, "PROOF_APPROVED")
        order["proof"] = "approved"
        order["state"] = "PROOF_APPROVED"
        return self._order_view(order)

    def set_order_state(self, token, order_id, state):
        self._require_owner(token)
        order = self.orders.get(order_id)
        if order is None:
            raise JGAError("no such order")
        if state not in ORDER_STATES:
            raise JGAError("unknown order state")
        cur = order["state"]
        allowed = {
            "REQUESTED": ["IN_DESIGN", "CANCELLED"],
            "IN_DESIGN": ["PROOF_SENT", "CANCELLED"],
            "PROOF_SENT": ["CANCELLED"],  # client approves the proof
            "PROOF_APPROVED": ["DELIVERED", "IN_DESIGN", "CANCELLED"],
            "DELIVERED": [],
            "CANCELLED": [],
        }
        if state not in allowed.get(cur, []):
            raise JGAError("cannot move an order from %s to %s" % (cur, state))
        if state == "PROOF_SENT":
            order["proof"] = "sent"
        if state == "DELIVERED":
            if order["proof"] != "approved":
                raise JGAError("clean files release only after the client "
                               "approves the proof")
            if not order["paid_in_full"]:
                raise JGAError("clean files release only after final payment "
                               "is cleared")
            order["clean_files_released"] = True
        self._bct_certify_order_transition(order, state)
        order["state"] = state
        return self._order_view(order)

    def record_payment(self, token, order_id, stripe_object_id,
                       amount_cents, kind):
        """Record a CLEARED Stripe payment. Receipt-only: the app moves
        no bank funds. The amount must equal what is owed for that kind,
        and a Stripe object id (pi_ / cs_ / ch_ / in_) is required."""
        self._require_owner(token)
        order = self.orders.get(order_id)
        if order is None:
            raise JGAError("no such order")
        if not stripe_object_id or not any(
                stripe_object_id.startswith(p)
                for p in ("pi_", "cs_", "ch_", "in_")):
            raise JGAError("a real Stripe payment id is required "
                           "(pi_, cs_, ch_ or in_)")
        if kind not in ("deposit", "balance", "full"):
            raise JGAError("payment kind must be deposit, balance or full")
        expected = {"deposit": order["deposit_cents"],
                    "balance": order["balance_cents"],
                    "full": order["price_cents"]}[kind]
        if int(amount_cents) != expected:
            raise JGAError("that payment is %d cents but %s is %d cents"
                           % (int(amount_cents), kind, expected))
        if kind == "deposit" and order["deposit_paid"]:
            raise JGAError("deposit already recorded")
        if kind == "balance" and order["balance_paid"]:
            raise JGAError("balance already recorded")
        if kind == "full" and order["paid_in_full"]:
            raise JGAError("full payment already recorded")
        order["payments"].append({"stripe_object_id": stripe_object_id,
                                  "amount_cents": int(amount_cents),
                                  "kind": kind})
        if kind in ("deposit", "full"):
            order["deposit_paid"] = True
        if kind in ("balance", "full"):
            order["balance_paid"] = True
        order["paid_in_full"] = order["deposit_paid"] and order["balance_paid"]
        return self._order_view(order)
