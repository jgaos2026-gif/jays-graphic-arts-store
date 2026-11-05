"""Supabase store adapter tests — stdlib only, no network.

The HTTP layer is mocked (urllib.request.urlopen is patched), so these
tests prove the adapter's config detection, fail-safe behavior, RPC
shapes, and authority rules without touching any live project.
No real key is used anywhere here — the "keys" below are fake sentinels.
"""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from supabase_store import SupabaseStore, SupabaseError  # noqa: E402

FAKE_URL = "https://hnczbvhlhzfwscfpltoe.supabase.co"
FAKE_KEY = "test-sentinel-not-a-real-key"


class FakeResponse:
    def __init__(self, status=200, body=""):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(url, code, message):
    return urllib.error.HTTPError(url, code, "error", {}, io.BytesIO(
        json.dumps({"message": message}).encode()))


class TestConfig(unittest.TestCase):
    def test_unset_env_is_not_configured_and_never_calls_network(self):
        with patch.dict(os.environ, {}, clear=True):
            store = SupabaseStore.from_env()
            self.assertFalse(store.configured)
            self.assertFalse(store.enabled)
            with patch("supabase_store.urllib.request.urlopen") as urlopen:
                self.assertFalse(store.initialize())
                urlopen.assert_not_called()

    def test_url_without_key_is_not_configured(self):
        with patch.dict(os.environ, {"SUPABASE_URL": FAKE_URL}, clear=True):
            store = SupabaseStore.from_env()
            self.assertFalse(store.configured)
            self.assertFalse(store.initialize())

    def test_key_without_url_is_not_configured(self):
        with patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": FAKE_KEY}, clear=True):
            store = SupabaseStore.from_env()
            self.assertFalse(store.configured)
            self.assertFalse(store.initialize())

    def test_non_https_url_is_not_configured(self):
        with patch.dict(os.environ, {"SUPABASE_URL": "http://example.com",
                                     "SUPABASE_SERVICE_ROLE_KEY": FAKE_KEY}, clear=True):
            store = SupabaseStore.from_env()
            self.assertFalse(store.configured)

    def test_both_set_ping_ok_enables(self):
        store = SupabaseStore(FAKE_URL, FAKE_KEY)
        self.assertTrue(store.configured)
        with patch("supabase_store.urllib.request.urlopen",
                   return_value=FakeResponse(200, "[]")) as urlopen:
            self.assertTrue(store.initialize())
            self.assertTrue(store.enabled)
            req = urlopen.call_args[0][0]
            self.assertEqual(req.full_url, FAKE_URL + "/rest/v1/")
            # the service-role key is sent as a header, never in the URL
            self.assertNotIn("test-sentinel", req.full_url)

    def test_both_set_ping_unreachable_fails_safe(self):
        store = SupabaseStore(FAKE_URL, FAKE_KEY)
        with patch("supabase_store.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("no route")):
            self.assertFalse(store.initialize())
            self.assertFalse(store.enabled)

    def test_both_set_ping_401_fails_safe(self):
        store = SupabaseStore(FAKE_URL, "test-wrong-sentinel")
        with patch("supabase_store.urllib.request.urlopen",
                   side_effect=http_error(FAKE_URL, 401, "Invalid API key")):
            self.assertFalse(store.initialize())
            self.assertFalse(store.enabled)


class TestRpc(unittest.TestCase):
    def setUp(self):
        self.store = SupabaseStore(FAKE_URL, FAKE_KEY)
        self.store.enabled = True

    def test_rpc_posts_to_public_wrapper_with_service_key_headers(self):
        def fake_open(req, **kwargs):
            self.assertEqual(req.full_url, FAKE_URL + "/rest/v1/rpc/brick1_verify_evidence_chain")
            self.assertEqual(req.get_header("Apikey"), FAKE_KEY)
            self.assertEqual(req.get_header("Authorization"), "Bearer " + FAKE_KEY)
            return FakeResponse(200, "true")

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            self.assertTrue(self.store.verify_evidence_chain())

    def test_rpc_http_error_never_leaks_the_key(self):
        with patch("supabase_store.urllib.request.urlopen",
                   side_effect=http_error(FAKE_URL, 401, "Invalid API key")):
            with self.assertRaises(SupabaseError) as ctx:
                self.store.order_status("00000000-0000-0000-0000-000000000001")
            self.assertNotIn(FAKE_KEY, str(ctx.exception))
            self.assertIn("401", str(ctx.exception))

    def test_create_inquiry_sends_all_ten_params(self):
        def fake_open(req, **kwargs):
            body = json.loads(req.data.decode())
            self.assertEqual(body["p_email"], "c@example.com")
            self.assertEqual(body["p_first_name"], "Test")
            self.assertEqual(body["p_last_name"], "Customer")
            self.assertEqual(body["p_service"], "logo")
            self.assertEqual(body["p_budget"], "600-1200")
            self.assertEqual(body["p_timeline"], "1-week")
            self.assertEqual(body["p_brief"], "Bold logo")
            self.assertEqual(body["p_referral"], "")
            self.assertEqual(body["p_company"], "")
            self.assertEqual(body["p_correlation_id"], "00000000-0000-0000-0000-000000000001")
            return FakeResponse(200, json.dumps(
                {"order_id": "11111111-1111-1111-1111-111111111111",
                 "state": "INQUIRY"}))

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            out = self.store.create_inquiry(
                "c@example.com", "Test", "Customer", "logo", "600-1200",
                "1-week", "Bold logo",
                correlation_id="00000000-0000-0000-0000-000000000001")
            self.assertEqual(out["state"], "INQUIRY")

    def test_record_payment_uses_the_brick1_allocation_shape(self):
        def fake_open(req, **kwargs):
            body = json.loads(req.data.decode())
            self.assertEqual(body["p_provider"], "stripe")
            self.assertEqual(body["p_provider_event_id"], "pi_test123")
            self.assertEqual(body["p_amount_cents"], 12250)
            self.assertIs(body["p_synthetic"], False)
            return FakeResponse(200, json.dumps(
                {"payment_id": "22222222-2222-2222-2222-222222222222",
                 "state": "DEPOSIT_CONFIRMED", "paid_cents": 12250}))

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            out = self.store.record_payment(
                "11111111-1111-1111-1111-111111111111", "stripe", "pi_test123",
                12250, False, "owner")
            self.assertEqual(out["state"], "DEPOSIT_CONFIRMED")

    def test_set_qa_sends_sentinel_actor_and_confidence(self):
        def fake_open(req, **kwargs):
            body = json.loads(req.data.decode())
            self.assertEqual(body["p_actor"], "SENTINEL")
            self.assertEqual(body["p_confidence"], 0.99)
            self.assertTrue(body["p_reflection"])
            return FakeResponse(200, json.dumps({"passed": True, "state": "BALANCE_REQUIRED"}))

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            out = self.store.set_qa(
                "11111111-1111-1111-1111-111111111111", "SENTINEL",
                True, True, True, 0.99, {"note": "ok"})
            self.assertTrue(out["passed"])

    def test_set_vera_approval_sends_vera_actor(self):
        def fake_open(req, **kwargs):
            body = json.loads(req.data.decode())
            self.assertEqual(body["p_actor"], "VERA")
            self.assertIs(body["p_approved"], True)
            return FakeResponse(200, json.dumps({"vera_approved": True}))

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            out = self.store.set_vera_approval(
                "11111111-1111-1111-1111-111111111111", "VERA", True)
            self.assertTrue(out["vera_approved"])

    def test_advance_sends_target_state(self):
        def fake_open(req, **kwargs):
            body = json.loads(req.data.decode())
            self.assertEqual(body["p_to"], "PRODUCTION")
            return FakeResponse(200, json.dumps({"state": "PRODUCTION"}))

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            out = self.store.advance(
                "11111111-1111-1111-1111-111111111111", "PRODUCTION", "AVA")
            self.assertEqual(out["state"], "PRODUCTION")

    def test_vistaprint_final_cents_asks_the_database(self):
        # $50.00 cost + $8.00 delivery -> $71.40, same as the engine.
        def fake_open(req, **kwargs):
            self.assertTrue(req.full_url.endswith("/rpc/brick1_vistaprint_final_cents"))
            body = json.loads(req.data.decode())
            self.assertEqual(body["p_cost_cents"], 5000)
            self.assertEqual(body["p_delivery_cents"], 800)
            return FakeResponse(200, "7140")

        with patch("supabase_store.urllib.request.urlopen", side_effect=fake_open):
            self.assertEqual(self.store.vistaprint_final_cents(5000, 800), 7140)

    def test_order_status_returns_the_gate_snapshot(self):
        with patch("supabase_store.urllib.request.urlopen",
                   return_value=FakeResponse(200, json.dumps(
                       {"order_id": "11111111-1111-1111-1111-111111111111",
                        "state": "DEPOSIT_CONFIRMED", "qa_passed": False,
                        "vera_approved": False, "active_exceptions": 0}))):
            out = self.store.order_status("11111111-1111-1111-1111-111111111111")
            self.assertEqual(out["state"], "DEPOSIT_CONFIRMED")
            self.assertEqual(out["active_exceptions"], 0)


if __name__ == "__main__":
    unittest.main()
