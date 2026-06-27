"""
Unit tests for redaction.py — privacy redaction for external-LLM-safe output.

Covers: mask_value (PII patterns + survival of safe values),
classify_field_sensitivity, scan_secrets (detection + preview truncation),
redact_payload (external and local modes).  Import-safe; no Odoo dependency.
"""
import sys
import json
import unittest
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "odoo-introspect" / "scripts"

_spec = importlib.util.spec_from_file_location("redaction", SCRIPTS_DIR / "redaction.py")
redaction = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(redaction)


# ---------------------------------------------------------------------------
# mask_value: PII patterns are replaced
# ---------------------------------------------------------------------------

class MaskValuePIITests(unittest.TestCase):
    def test_email_masked(self):
        self.assertEqual(
            redaction.mask_value("Contact info@example.com for details"),
            "Contact <email> for details",
        )

    def test_phone_international_masked(self):
        self.assertIn("<phone>", redaction.mask_value("+1-800-555-1234"))

    def test_phone_local_grouped_masked(self):
        self.assertIn("<phone>", redaction.mask_value("Call 0912 345 678 now"))

    def test_jwt_masked(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9"
            ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
            ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        self.assertEqual(redaction.mask_value(jwt), "<jwt>")

    def test_hex_token_32_chars_masked(self):
        # 32-char MD5-style hex hash
        self.assertEqual(
            redaction.mask_value("d8e8fca2dc0f896fd7cb4cb0031ba249"),
            "<token>",
        )

    def test_hex_token_40_chars_masked(self):
        # 40-char SHA-1 style
        self.assertEqual(
            redaction.mask_value("da39a3ee5e6b4b0d3255bfef95601890afd80709"),
            "<token>",
        )


# ---------------------------------------------------------------------------
# mask_value: safe values survive unmasked
# ---------------------------------------------------------------------------

class MaskValueSurvivalTests(unittest.TestCase):
    def test_plain_text_unchanged(self):
        plain = "Hello world this is ordinary text"
        self.assertEqual(redaction.mask_value(plain), plain)

    def test_small_integer_unchanged(self):
        # Non-str values returned unchanged (not "unmasked" — identity)
        self.assertEqual(redaction.mask_value(42), 42)

    def test_none_unchanged(self):
        self.assertIsNone(redaction.mask_value(None))

    def test_date_string_unchanged(self):
        # YYYY-MM-DD has only 8 digits — must not trigger phone/card/token masks
        self.assertEqual(redaction.mask_value("2024-01-15"), "2024-01-15")

    def test_short_number_string_unchanged(self):
        self.assertEqual(redaction.mask_value("42"), "42")

    def test_model_name_unchanged(self):
        self.assertEqual(redaction.mask_value("sale.order"), "sale.order")


# ---------------------------------------------------------------------------
# classify_field_sensitivity
# ---------------------------------------------------------------------------

class ClassifyFieldSensitivityTests(unittest.TestCase):
    def test_high_for_sensitive_model(self):
        self.assertEqual(
            redaction.classify_field_sensitivity("res.partner", "name"), "high")
        self.assertEqual(
            redaction.classify_field_sensitivity("hr.employee", "department_id"), "high")
        self.assertEqual(
            redaction.classify_field_sensitivity("account.move", "amount_total"), "high")
        self.assertEqual(
            redaction.classify_field_sensitivity("payment.transaction", "reference"), "high")

    def test_high_for_sensitive_key_field(self):
        self.assertEqual(
            redaction.classify_field_sensitivity("sale.order", "password"), "high")
        self.assertEqual(
            redaction.classify_field_sensitivity("sale.order", "api_key"), "high")
        self.assertEqual(
            redaction.classify_field_sensitivity("product.template", "token"), "high")

    def test_high_for_fixed_pii_field_names(self):
        for field in ("email", "phone", "mobile", "vat", "iban", "acc_number", "login"):
            with self.subTest(field=field):
                self.assertEqual(
                    redaction.classify_field_sensitivity("sale.order", field), "high")

    def test_normal_for_generic_model_and_field(self):
        self.assertEqual(
            redaction.classify_field_sensitivity("sale.order", "name"), "normal")
        self.assertEqual(
            redaction.classify_field_sensitivity("stock.picking", "state"), "normal")
        self.assertEqual(
            redaction.classify_field_sensitivity("product.template", "description"), "normal")


# ---------------------------------------------------------------------------
# scan_secrets: detects patterns, previews truncated to 6+ellipsis
# ---------------------------------------------------------------------------

class ScanSecretsTests(unittest.TestCase):
    def test_finds_aws_key(self):
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE configured"
        hits = redaction.scan_secrets(text)
        kinds = [h["kind"] for h in hits]
        self.assertIn("aws_key", kinds)

    def test_aws_key_preview_correct(self):
        hits = redaction.scan_secrets("key=AKIAIOSFODNN7EXAMPLE")
        aws = next(h for h in hits if h["kind"] == "aws_key")
        self.assertEqual(aws["match_preview"], "AKIAIO…")  # first 6 + ellipsis

    def test_finds_private_key_header_rsa(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAK..."
        hits = redaction.scan_secrets(text)
        kinds = [h["kind"] for h in hits]
        self.assertIn("private_key_header", kinds)

    def test_finds_private_key_header_bare(self):
        text = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADA..."
        hits = redaction.scan_secrets(text)
        kinds = [h["kind"] for h in hits]
        self.assertIn("private_key_header", kinds)

    def test_private_key_preview_truncated(self):
        text = "-----BEGIN EC PRIVATE KEY-----\ndata..."
        hits = redaction.scan_secrets(text)
        pk = next(h for h in hits if h["kind"] == "private_key_header")
        self.assertTrue(pk["match_preview"].endswith("…"))
        self.assertEqual(len(pk["match_preview"]), 7)  # 6 chars + "…"

    def test_all_previews_exactly_seven_chars(self):
        text = "AKIAIOSFODNN7EXAMPLE -----BEGIN RSA PRIVATE KEY-----"
        hits = redaction.scan_secrets(text)
        for h in hits:
            self.assertEqual(len(h["match_preview"]), 7, msg=f"bad preview: {h}")

    def test_no_false_positive_on_plain_text(self):
        text = "The sale.order model has fields name, state, and amount_total."
        self.assertEqual(redaction.scan_secrets(text), [])


# ---------------------------------------------------------------------------
# redact_payload: external mode
# ---------------------------------------------------------------------------

class RedactExternalModeTests(unittest.TestCase):
    def test_strips_source_key(self):
        obj = {"source": "def action_confirm(self):\n    return True", "name": "SO"}
        result = redaction.redact_payload(obj, mode="external")
        self.assertEqual(result["source"], "<stripped:external-mode>")
        self.assertEqual(result["name"], "SO")

    def test_strips_locals_key(self):
        obj = {"locals": {"self": "<sale.order(1,)>", "vals": {}}, "model": "sale.order"}
        result = redaction.redact_payload(obj, mode="external")
        self.assertEqual(result["locals"], "<stripped:external-mode>")

    def test_strips_code_key(self):
        obj = {"code": "env['res.partner'].write({'active': False})", "active": True}
        result = redaction.redact_payload(obj, mode="external")
        self.assertEqual(result["code"], "<stripped:external-mode>")

    def test_redacts_password_key(self):
        obj = {"username": "admin", "password": "hunter2"}
        result = redaction.redact_payload(obj, mode="external")
        self.assertEqual(result["password"], "<redacted>")
        self.assertEqual(result["username"], "admin")

    def test_redacts_token_key(self):
        obj = {"api_token": "abc123xyz", "model": "sale.order"}
        result = redaction.redact_payload(obj, mode="external")
        self.assertEqual(result["api_token"], "<redacted>")

    def test_masks_email_in_string_value(self):
        obj = {"contact": "Reach owner@example.com for billing"}
        result = redaction.redact_payload(obj, mode="external")
        self.assertIn("<email>", result["contact"])
        self.assertNotIn("owner@example.com", result["contact"])

    def test_nested_dict_comprehensive(self):
        obj = {
            "source": "...",
            "locals": {"x": 1},
            "code": "pass",
            "password": "s3cret",
            "partner": {"email": "jane@acme.com", "name": "Jane Doe"},
        }
        result = redaction.redact_payload(obj, mode="external")
        self.assertEqual(result["source"], "<stripped:external-mode>")
        self.assertEqual(result["locals"], "<stripped:external-mode>")
        self.assertEqual(result["code"], "<stripped:external-mode>")
        self.assertEqual(result["password"], "<redacted>")
        self.assertEqual(result["partner"]["email"], "<email>")
        self.assertEqual(result["partner"]["name"], "Jane Doe")

    def test_list_values_processed(self):
        obj = {"contacts": ["alice@example.com", "plain text", 42]}
        result = redaction.redact_payload(obj, mode="external")
        self.assertEqual(result["contacts"][0], "<email>")
        self.assertEqual(result["contacts"][1], "plain text")
        self.assertEqual(result["contacts"][2], 42)  # non-str unchanged

    def test_default_mode_equals_external(self):
        obj = {"source": "x = 1", "password": "pw"}
        self.assertEqual(
            redaction.redact_payload(obj),
            redaction.redact_payload(obj, mode="external"),
        )


# ---------------------------------------------------------------------------
# redact_payload: local mode
# ---------------------------------------------------------------------------

class RedactLocalModeTests(unittest.TestCase):
    def test_keeps_source(self):
        src = "def action_confirm(self):\n    return super().action_confirm()"
        obj = {"source": src, "password": "hunter2"}
        result = redaction.redact_payload(obj, mode="local")
        self.assertEqual(result["source"], src)          # source kept
        self.assertEqual(result["password"], "<redacted>")  # key still redacted

    def test_keeps_locals(self):
        obj = {"locals": {"x": 42, "name": "John"}, "token": "abc"}
        result = redaction.redact_payload(obj, mode="local")
        self.assertEqual(result["locals"], {"x": 42, "name": "John"})
        self.assertEqual(result["token"], "<redacted>")

    def test_no_pii_masking_on_values(self):
        # Emails in values are NOT masked in local mode
        obj = {"contact": "owner@example.com", "secret": "shhh"}
        result = redaction.redact_payload(obj, mode="local")
        self.assertEqual(result["contact"], "owner@example.com")  # untouched
        self.assertEqual(result["secret"], "<redacted>")


# ---------------------------------------------------------------------------
# Module-level constants sanity
# ---------------------------------------------------------------------------

class ConstantsTests(unittest.TestCase):
    def test_sensitive_models_contains_all_required(self):
        required = {
            "res.partner", "res.users", "account.move", "account.payment",
            "hr.employee", "hr.payslip", "payment.transaction",
            "mail.message", "ir.attachment",
        }
        self.assertTrue(required <= redaction.SENSITIVE_MODELS)

    def test_sensitive_key_re_matches_expected(self):
        p = redaction.SENSITIVE_KEY_RE
        for kw in ("password", "token", "secret", "api_key", "apikey",
                   "authorization", "session", "private_key", "credential",
                   "passwd", "pwd"):
            self.assertIsNotNone(p.search(kw), msg=f"should match: {kw}")

    def test_sensitive_key_re_no_false_positives(self):
        p = redaction.SENSITIVE_KEY_RE
        for kw in ("name", "state", "model", "amount_total", "description"):
            self.assertIsNone(p.search(kw), msg=f"should NOT match: {kw}")

    def test_sensitive_key_re_matches_compound_keys(self):
        # Substring match: api_token, auth_token, my_password_field, etc.
        p = redaction.SENSITIVE_KEY_RE
        self.assertIsNotNone(p.search("api_token"))
        self.assertIsNotNone(p.search("auth_token"))
        self.assertIsNotNone(p.search("my_password_field"))


class HardeningV091Tests(unittest.TestCase):
    """v0.9.1 redaction hardening (oracle review fixes)."""

    def test_provider_secrets_masked(self):
        self.assertEqual(redaction.mask_value("AKIAIOSFODNN7EXAMPLE"), "<aws_key>")
        self.assertEqual(redaction.mask_value("ghp_" + "a" * 36), "<github_token>")
        self.assertIn("<stripe_key>", redaction.mask_value("k sk_live_" + "a" * 20))
        self.assertIn("<private_key>", redaction.mask_value("-----BEGIN RSA PRIVATE KEY-----"))

    def test_benign_secret_keys_redacted(self):
        for k in ("aws_access_key_id", "access_key", "client_secret",
                  "webhook_url", "signing_key", "AUTH_TOKEN"):
            self.assertEqual(redaction.redact_payload({k: "x"}, "external")[k], "<redacted>", k)

    def test_strip_key_case_and_variants(self):
        for k in ("Source", "LOCALS", "local_vars", "frame_locals", "self", "args", "kwargs"):
            self.assertEqual(redaction.redact_payload({k: "anything"}, "external")[k],
                             "<stripped:external-mode>", k)

    def test_record_shape_value_redacted_by_sensitivity(self):
        r = redaction.redact_payload(
            {"model": "res.partner", "field": "name", "value": "Jane Doe"}, "external")
        self.assertTrue(r["value"].startswith("<redacted"), r["value"])
        self.assertNotIn("Jane Doe", json.dumps(r))
        # non-sensitive model/field keeps the value (still PII-masked, but 'draft' is clean)
        r2 = redaction.redact_payload(
            {"model": "sale.order", "field": "state", "value": "draft"}, "external")
        self.assertEqual(r2["value"], "draft")

    def test_sensitive_model_record_dump_redacts_plain_name(self):
        # full record dump (not a triple): a plain name/city must not leak (v0.9.1 r2 #5)
        r = redaction.redact_payload(
            {"model": "res.partner", "name": "Jane Doe", "city": "Hanoi"}, "external")
        self.assertNotIn("Jane Doe", json.dumps(r))
        self.assertNotIn("Hanoi", json.dumps(r))

    def test_sensitive_record_nested_shapes_redacted(self):
        # round-3 #3: display_name, many2one display tuple, nested child records
        for payload in (
            {"model": "res.partner", "display_name": "Jane Doe"},
            {"model": "res.partner", "parent_id": [1, "Jane Doe"]},
            {"model": "res.partner", "child_ids": [{"name": "Kid Name", "city": "Hanoi"}]},
        ):
            out = json.dumps(redaction.redact_payload(payload, "external"))
            self.assertNotIn("Jane Doe", out, payload)
            self.assertNotIn("Kid Name", out, payload)
            self.assertNotIn("Hanoi", out, payload)

    def test_scan_secrets_jwt_threshold_matches_mask(self):
        # round-3 #4: a 10+/segment JWT that mask_value masks must also be scanned
        short_jwt = "aaaaaaaaaa.bbbbbbbbbb.cccccccccc"
        self.assertEqual(redaction.mask_value(short_jwt), "<jwt>")
        self.assertTrue(any(h["kind"] == "jwt" for h in redaction.scan_secrets(short_jwt)))

    def test_aws_key_under_benign_key_does_not_leak(self):
        # the exact oracle finding: AWS key under aws_access_key_id
        out = redaction.redact_payload({"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE"}, "external")
        self.assertNotIn("AKIAIOSFODNN7EXAMPLE", json.dumps(out))

    def test_scan_secrets_covers_same_providers_as_mask(self):
        # round-2 #3: the scanner must catch what the redactor masks
        text = "ghp_" + "a" * 36 + " sk_live_" + "b" * 20 + " ASIA" + "C" * 16
        kinds = {h["kind"] for h in redaction.scan_secrets(text)}
        self.assertTrue({"github_token", "stripe_key", "aws_key"} <= kinds, kinds)


if __name__ == "__main__":
    unittest.main()
