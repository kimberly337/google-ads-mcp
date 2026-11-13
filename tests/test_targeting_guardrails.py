"""Offline safety checks for campaign targeting; no customer mutation requests."""

import unittest

from fastmcp.exceptions import ToolError

from ads_mcp import propose_and_confirm as pac
from ads_mcp.tools.targeting import (
    CENTRAL_OREGON_LOCATION_IDS,
    _digits,
    _money_micros,
    _name,
)


class TargetingGuardrailsTest(unittest.TestCase):
    def test_approved_central_oregon_locations(self):
        self.assertEqual(
            set(CENTRAL_OREGON_LOCATION_IDS),
            {"1024400", "1024548", "1024566", "1024545", "9196279"},
        )
        self.assertEqual(
            CENTRAL_OREGON_LOCATION_IDS["1024548"], "Redmond, Oregon"
        )

    def test_numeric_ids_reject_injection(self):
        for value in ("1/2", "1;DROP", "123abc", "", "  - "):
            with self.subTest(value=value):
                with self.assertRaises(ToolError):
                    _digits(value, "campaign_id")
        self.assertEqual(_digits("121-285-2768", "customer_id"), "1212852768")

    def test_money_limits(self):
        self.assertEqual(_money_micros("2.50"), 2500000)
        for invalid in ("0", "-1", "0.001", "NaN", "Infinity", "1001"):
            with self.subTest(value=invalid):
                with self.assertRaises(ToolError):
                    _money_micros(invalid)

    def test_text_validation(self):
        with self.assertRaises(ToolError):
            _name("", "keyword")
        with self.assertRaises(ToolError):
            _name("x" * 26, "sitelink", 25)

    def test_customer_and_payload_checksum_binding(self):
        payload = {"tool": "add_search_keywords", "keywords": ["hr consultant"]}
        checksum = pac.compute_checksum("1212852768", payload)
        self.assertIsNone(pac.verify_checksum("1212852768", payload, checksum))
        self.assertIsNotNone(pac.verify_checksum("1212852768", payload, None))
        self.assertIsNotNone(pac.verify_checksum("1212852768", payload, "bad"))
        self.assertIsNotNone(
            pac.verify_checksum("9876543210", payload, checksum)
        )
        self.assertIsNotNone(
            pac.verify_checksum(
                "1212852768", {**payload, "keywords": ["different"]}, checksum
            )
        )


if __name__ == "__main__":
    unittest.main()
