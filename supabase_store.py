"""Jay's Graphic Arts — Supabase durable store adapter (stdlib only).

What this is: the PostgREST client that lets the Render server use the
Supabase project "jga-apps" as its durable store for the `brick1` schema
(see supabase/migrations/001_brick1_operational.sql,
002_intake_and_command_api.sql, 003_oasis_clip_governance.sql and
001_jga_core.sql). No new dependency — stdlib urllib only.

How it is used:
  * The server reads SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from
    the environment ONLY. Both must be set, and the URL must be https.
  * `SupabaseStore.from_env().initialize()` pings the project. On any
    failure (not configured, unreachable, wrong key) it returns False
    and the server keeps the existing in-memory store EXACTLY as today
    — it never crashes, never retries in a loop, never logs a key.
  * Money and state changes go through the security-definer functions,
    reached here as the public RPC wrappers from 002/003
    (brick1_create_inquiry, brick1_set_quote, brick1_record_payment,
    brick1_set_qa, brick1_set_vera_approval, brick1_advance,
    brick1_propose_action, brick1_decide_action, brick1_execute_action,
    brick1_open_exception, brick1_resolve_exception,
    brick1_verify_evidence_chain, brick1_order_status,
    brick1_public_intake_allowed) plus brick1_vistaprint_final_cents
    (from 001_jga_core.sql). The server holds the service-role key and
    is the only database client; per-person isolation stays enforced in
    the app layer (a client sees only its own orders; the owner sees
    all), exactly as the engine does today.
  * Authority, as 003 hardens it: QA results may only be set by
    SENTINEL, VERA approval only by VERA, action execution only by AVA
    after VERA authorizes, exceptions only by AVA (routine) or VERA /
    OWNER (high-risk). Self-certification is forbidden: an actor named
    AVA or ORION may never approve its own work.

What this is NOT (stated so it cannot drift):
  * This adapter is the durable-store CLIENT. The runtime engine
    (JGAStore) still holds the live state in memory in v1; a full
    migration of the engine to read from Postgres on every call is a
    later step, verified against the live database — it is NOT claimed
    here. In particular the engine's order states (REQUESTED, IN_DESIGN,
    PROOF_SENT, PROOF_APPROVED, DELIVERED, CANCELLED) are NOT the
    brick1 order states (INQUIRY … FULFILLED plus QUARANTINED): the
    adapter does not pretend one is the other. Mapping the two state
    machines is part of the live-DB step.
  * The 10-bucket brick1 allocation (TAX 30%, SYSTEM_B 25%, OPS 15%,
    DRAW 10%, CREDIT 10%, NODE 3%, EMERGENCY 3%, HERMES 2%, CHARITY 1%,
    LEGACY 1%) is the BRICK 1 allocation. It is NOT the Talent Agent
    Plus 11VT split — never mix the two.
  * Vistaprint pricing: the SQL function brick1.vistaprint_final_cents
    FLOORS at each step; the Python engine rounds half-up. Both agree
    on the verified example ($50.00 cost + $8.00 delivery = $71.40)
    but can differ by one cent on odd amounts. The adapter reports the
    database value; it never overrides the engine's price in v1.
    Reconcile the rounding rule in the live-DB step; neither side was
    changed here.

Secrets: the service-role key is read from the environment and is
NEVER written to a file, a log line, or an exception message. The
project URL host is not secret and may appear in logs.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_PING_TIMEOUT = 5.0
_RPC_TIMEOUT = 10.0


class SupabaseError(Exception):
    """A Supabase call failed. The message never contains a key."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


class SupabaseStore:
    """PostgREST client for the `brick1` schema. Fail-safe by design."""

    def __init__(self, url: str | None = None, service_role_key: str | None = None):
        self.url = (url if url is not None else _env("SUPABASE_URL")).rstrip("/")
        self._key = service_role_key if service_role_key is not None else _env("SUPABASE_SERVICE_ROLE_KEY")
        self.configured = bool(self.url and self._key and self.url.startswith("https://"))
        self.enabled = False  # True only after a successful ping

    @classmethod
    def from_env(cls) -> "SupabaseStore":
        return cls()

    # -- lifecycle ------------------------------------------------------
    def initialize(self) -> bool:
        """Ping the project once. True = durable store live. False = keep
        the in-memory store. Never raises, never logs a key."""
        if not self.configured:
            return False
        try:
            self.enabled = self.ping()
        except Exception:
            self.enabled = False
        return self.enabled

    def ping(self) -> bool:
        """A plain GET on the PostgREST root. True on HTTP 200 only."""
        try:
            status, _ = self._request("GET", "/rest/v1/", timeout=_PING_TIMEOUT)
            return status == 200
        except SupabaseError:
            return False

    # -- low-level HTTP -------------------------------------------------
    def _headers(self, profile: str | None = None) -> dict:
        headers = {
            "apikey": self._key,
            "Authorization": "Bearer " + self._key,
            "Content-Type": "application/json",
        }
        if profile:
            headers["Accept-Profile"] = profile
            headers["Content-Profile"] = profile
        return headers

    def _request(self, method: str, path: str, body=None, profile: str | None = None,
                 timeout: float = _RPC_TIMEOUT):
        """One HTTP call. Returns (status, parsed JSON or None).
        Raises SupabaseError with the status and the short PostgREST
        message only — never the key, never a raw body."""
        if not self.configured:
            raise SupabaseError("Supabase is not configured (SUPABASE_URL and "
                                "SUPABASE_SERVICE_ROLE_KEY must both be set)")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data,
                                     headers=self._headers(profile), method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                return resp.status, (json.loads(raw) if raw.strip() else None)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                parsed = json.loads(e.read().decode())
                if isinstance(parsed, dict) and isinstance(parsed.get("message"), str):
                    detail = ": " + parsed["message"][:200]
            except Exception:
                detail = ""
            raise SupabaseError("Supabase request failed with HTTP %d%s" % (e.code, detail))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SupabaseError("Supabase request failed: %s" % type(e).__name__)

    # -- PostgREST primitives -------------------------------------------
    def rpc(self, name: str, params: dict):
        """Call a public RPC wrapper. Void functions return None (204)."""
        status, payload = self._request("POST", "/rest/v1/rpc/" + name, body=params or {})
        if status not in (200, 201, 204):
            raise SupabaseError("RPC %s failed with HTTP %d" % (name, status))
        return payload

    def table_select(self, table: str, filters: dict, profile: str = "brick1"):
        """Select rows from a schema table. `filters` are exact matches
        (eq). Returns a list (possibly empty). Needs the schema exposed
        in the project's API settings."""
        query = "&".join("%s=eq.%s" % (k, urllib.request.quote(str(v), safe=""))
                         for k, v in filters.items())
        path = "/rest/v1/%s?%s" % (table, query) if query else "/rest/v1/%s" % table
        status, payload = self._request("GET", path, profile=profile)
        if status != 200 or not isinstance(payload, list):
            raise SupabaseError("select on %s failed with HTTP %d" % (table, status))
        return payload

    # -- brick1: intake / quote / payment / QA / VERA / advance ---------
    def intake_allowed(self, ip_hash: str) -> bool:
        """Rate gate for public intake: 8 requests per IP hash per hour."""
        return bool(self.rpc("brick1_public_intake_allowed", {"p_ip_hash": ip_hash}))

    def create_inquiry(self, email: str, first_name: str, last_name: str,
                       service: str, budget: str, timeline: str, brief: str,
                       company: str = "", referral: str = "",
                       correlation_id: str | None = None):
        """Create a customer + an INQUIRY order. Returns the order id,
        state, and creation time. Synthetic data only in tests."""
        params = {
            "p_email": email, "p_first_name": first_name, "p_last_name": last_name,
            "p_company": company, "p_service": service, "p_budget": budget,
            "p_timeline": timeline, "p_brief": brief, "p_referral": referral,
        }
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_create_inquiry", params)

    def set_quote(self, order_id: str, total_cents: int, actor: str,
                  correlation_id: str | None = None):
        """Set the quote; moves INQUIRY -> QUOTE -> DEPOSIT_REQUIRED.
        The 35% deposit is a generated column in the database."""
        params = {"p_order_id": order_id, "p_total_cents": int(total_cents),
                  "p_actor": actor}
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_set_quote", params)

    def record_payment(self, order_id: str, provider: str, provider_event_id: str,
                       amount_cents: int, synthetic: bool, actor: str,
                       correlation_id: str | None = None):
        """Record a payment once (idempotent on provider + event id).
        Splits the 10-bucket BRICK 1 allocation (not the 11VT) and
        advances DEPOSIT_REQUIRED -> DEPOSIT_CONFIRMED or
        BALANCE_REQUIRED -> PAID when the paid total covers it."""
        params = {
            "p_order_id": order_id, "p_provider": provider,
            "p_provider_event_id": provider_event_id,
            "p_amount_cents": int(amount_cents), "p_synthetic": bool(synthetic),
            "p_actor": actor,
        }
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_record_payment", params)

    def set_qa(self, order_id: str, actor: str, reflection: bool, surface: bool,
               atmosphere: bool, confidence: float, evidence: dict | None = None,
               correlation_id: str | None = None):
        """Record a QA result. Per 003, the actor MUST be SENTINEL.
        Passes only when all three checks pass and confidence >= 0.99;
        a pass moves QA -> BALANCE_REQUIRED."""
        params = {
            "p_order_id": order_id, "p_actor": actor,
            "p_reflection": bool(reflection), "p_surface": bool(surface),
            "p_atmosphere": bool(atmosphere), "p_confidence": float(confidence),
            "p_evidence": evidence or {},
        }
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_set_qa", params)

    def set_vera_approval(self, order_id: str, actor: str, approved: bool,
                          correlation_id: str | None = None):
        """Record the VERA gate decision. Per 003, the actor MUST be VERA.
        AVA/ORION may never approve their own work (self-certification
        is forbidden)."""
        params = {"p_order_id": order_id, "p_actor": actor, "p_approved": bool(approved)}
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_set_vera_approval", params)

    def advance(self, order_id: str, to_state: str, actor: str,
                correlation_id: str | None = None):
        """Move an order through the brick1 state machine in the database.
        Deposit, production, QA, balance, and release gates are enforced
        there; an invalid move is refused with the gate name."""
        params = {"p_order_id": order_id, "p_to": to_state, "p_actor": actor}
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_advance", params)

    # -- brick1: governance (003) ---------------------------------------
    def propose_action(self, order_id: str, actor: str, action: str,
                       payload: dict | None = None, correlation_id: str | None = None):
        """ORION/AVA propose; only VERA may authorize (decide_action)."""
        params = {"p_order_id": order_id, "p_actor": actor, "p_action": action,
                  "p_payload": payload or {}}
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_propose_action", params)

    def decide_action(self, request_id: str, actor: str, approved: bool,
                      reason: str = "", correlation_id: str | None = None):
        """VERA authorizes or denies a proposed action."""
        params = {"p_request_id": request_id, "p_actor": actor,
                  "p_approved": bool(approved), "p_reason": reason}
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_decide_action", params)

    def execute_action(self, request_id: str, actor: str,
                       correlation_id: str | None = None):
        """AVA executes an AUTHORIZED action. Failures are recorded on
        the request and in the evidence ledger, never silent."""
        params = {"p_request_id": request_id, "p_actor": actor}
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_execute_action", params)

    def open_exception(self, order_id: str, state: str, code: str, actor: str,
                       details: dict | None = None, correlation_id: str | None = None):
        """Open a routine exception. Per 003 the actor MUST be AVA and
        the state must be a routine one (not QUARANTINED/RECOVERY/
        OWNER_REVIEW — those go through propose/decide/execute)."""
        params = {"p_order_id": order_id, "p_state": state, "p_code": code,
                  "p_actor": actor, "p_details": details or {}}
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_open_exception", params)

    def resolve_exception(self, exception_id: str, actor: str,
                          correlation_id: str | None = None):
        """Resolve an exception. High-risk states need VERA or OWNER."""
        params = {"p_exception_id": exception_id, "p_actor": actor}
        if correlation_id:
            params["p_correlation_id"] = correlation_id
        return self.rpc("brick1_resolve_exception", params)

    # -- brick1: verification / pricing ---------------------------------
    def order_status(self, order_id: str):
        """One read of an order's state, gates, and active exceptions."""
        return self.rpc("brick1_order_status", {"p_order_id": order_id})

    def verify_evidence_chain(self) -> bool:
        """Re-verify the whole brick1 evidence chain in the database."""
        return bool(self.rpc("brick1_verify_evidence_chain", {}))

    def vistaprint_final_cents(self, cost_cents: int, delivery_cents: int) -> int:
        """Ask the DATABASE for the Vistaprint final price, using
        brick1.vistaprint_final_cents(). The database FLOORS at each
        step; the Python engine rounds half-up — both agree on the
        verified example ($50.00 + $8.00 = $71.40) but can differ by
        one cent on odd amounts. This returns the database value; the
        engine's price is not overridden in v1."""
        return int(self.rpc("brick1_vistaprint_final_cents", {
            "p_cost_cents": int(cost_cents), "p_delivery_cents": int(delivery_cents)}))
